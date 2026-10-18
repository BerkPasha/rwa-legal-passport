"""
Stage 9B-3 — legal approval version integrity.

An approval is bound to the exact document version, evidence and material asset information.
It is current only while all of them are unchanged; stale or superseded approvals are kept as
history but are never presented as currently approved.

Coverage:
 1. Binding of an approval (version, SHA-256, evidence and asset snapshots, digests)
 2. Approved V1 followed by a V2 upload (superseded, nothing transferred, re-approval on V2)
 3. Approval of the wrong (superseded) document version
 4. Material asset metadata changes (stale, re-review required, revert does not restore)
 5. Non-material PATCH keeps the approval current
 6. Suspended asset
 7. Historical approval snapshots and their access control
 8. Unauthorized approval attempts and cross-issuer access
 9. Issuer attestation is not independent legal verification
10. Several documents: conflicting evidence is flagged; a superseded document is excluded
11. Concurrent uploads, approvals and asset changes
12. Institutional API stale-approval rejection
13. Tampered bindings / evidence and legacy (unbound) approvals are never current
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.approval_integrity import sha256_digest
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.models.review import ReviewApproval
from tests.pdf_fixtures import make_source_pdf


def _passport_url(asset: dict) -> str:
    return (
        f"/passport/{asset['blockchain_network']}"
        f"/{asset['stellar_asset_code']}"
        f"/{asset['stellar_issuer_public_key']}"
    )


async def _approve(client: AsyncClient, extraction_id: str, headers: dict, notes: str | None = None):
    return await client.post(
        f"/extractions/{extraction_id}/approvals", headers=headers, json={"notes": notes} if notes else {}
    )


# Documents and versions contain the mock AI quotations (approvals require verified evidence,
# Stage 9B-4); the extra line gives every file its own content.
async def _new_version(issuer_client: AsyncClient, document_id: str, label: str):
    pdf = make_source_pdf(f"Amended terms: {label}.")
    return await issuer_client.post(
        f"/documents/{document_id}/versions", files={"file": (f"{label}.pdf", pdf, "application/pdf")}
    )


async def _extract(issuer_client: AsyncClient, document_id: str) -> dict:
    r = await issuer_client.post(f"/documents/{document_id}/extractions")
    assert r.status_code == 201, r.text
    return r.json()


async def _state(client: AsyncClient, extraction_id: str, headers: dict) -> dict:
    r = await client.get(f"/extractions/{extraction_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["approval_state"]


async def _history(client: AsyncClient, extraction_id: str, headers: dict) -> list[dict]:
    r = await client.get(f"/extractions/{extraction_id}/approvals", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _upload_document(
    issuer_client: AsyncClient, asset_id: str, title: str, document_type: str, *extra_lines: str
):
    pdf = make_source_pdf(f"{title}: governing terms.", *extra_lines)
    r = await issuer_client.post(
        f"/assets/{asset_id}/documents",
        data={"document_type": document_type, "title": title},
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _claim_id(extraction: dict, field_name: str) -> str:
    return next(c["id"] for c in extraction["claims"] if c["field_name"] == field_name)


def _as_read(extraction: dict, field_name: str) -> dict:
    """The mandatory correction precondition (Stage 9B-4 issue A): the claim exactly as read."""
    claim = next(c for c in extraction["claims"] if c["field_name"] == field_name)
    return {key: claim[key] for key in ("value", "is_unknown", "source_page", "supporting_passage")}


async def _approval_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(ReviewApproval))).scalar_one()


# ── 1. Binding ────────────────────────────────────────────────────────────────


async def test_approval_is_bound_to_exact_version_evidence_and_asset(
    client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    created_extraction: dict,
    issuer_headers: dict,
    async_db: AsyncSession,
):
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 201, r.text
    a = r.json()
    version = created_document["current_version"]

    assert a["document_id"] == created_document["id"]
    assert a["document_version_id"] == created_extraction["version_id"] == version["id"]
    assert a["document_version_number"] == 1
    assert a["document_sha256"] == version["sha256_hash"]
    assert a["asset_material_revision"] == created_asset["material_revision"] == 1
    assert a["asset_snapshot"]["jurisdiction"] == "US-NY"
    assert a["asset_snapshot"]["issuer_id"] == created_asset["issuer_id"]
    assert a["asset_snapshot"]["status"] == "active"
    approved_values = {c["field_name"]: c["value"] for c in a["evidence_snapshot"]}
    assert approved_values == {c["field_name"]: c["value"] for c in created_extraction["claims"]}
    # The digests are reproducible from the stored snapshots. Since Stage 9B-4 step 5 the evidence
    # is stored in format 2 (evidence_record); evidence_snapshot lists its claims in the 9B-3 fields
    assert a["evidence_snapshot_format"] == 2
    assert a["evidence_digest"] == sha256_digest(a["evidence_record"])
    assert a["evidence_snapshot"] == [
        {k: c[k] for k in ("claim_id", "field_name", "value", "is_unknown", "source_page", "supporting_passage")}
        for c in a["evidence_record"]["claims"]
    ]
    assert a["asset_snapshot_digest"] == sha256_digest(a["asset_snapshot"])
    assert len(a["binding_digest"]) == 64
    assert a["currency"] == {"is_current": True, "reasons": []}

    stored = (await async_db.execute(select(ReviewApproval))).scalar_one()
    assert stored.binding_digest == a["binding_digest"]
    assert stored.document_sha256 == version["sha256_hash"]

    state = await _state(client, created_extraction["id"], issuer_headers)
    assert state == {
        "state": "current",
        "version_is_current": True,
        "current_approval_types": ["issuer_attestation"],
        "reasons": [],
    }


# ── 2. Approved V1 followed by V2 upload ──────────────────────────────────────


async def test_v2_upload_supersedes_v1_approval_and_nothing_is_transferred(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    inst_headers: dict,
):
    url = _passport_url(created_asset)
    eid, doc_id = approved_extraction["id"], created_document["id"]
    assert (await client.get(url, headers=inst_headers)).status_code == 200
    before = await _history(client, eid, issuer_headers)

    v2 = await _new_version(issuer_client, doc_id, "v2")
    assert v2.status_code == 201 and v2.json()["version_number"] == 2

    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 409
    assert r.json() == {
        "detail": "The legal approval for this asset is no longer current; a re-review is required."
    }
    assert await _state(client, eid, issuer_headers) == {
        "state": "superseded",
        "version_is_current": False,
        "current_approval_types": [],
        "reasons": ["document_version_superseded"],
    }
    after = await _history(client, eid, issuer_headers)
    assert len(after) == 1
    # The historical record is unchanged; only its evaluated currency differs
    assert {k: v for k, v in after[0].items() if k != "currency"} == {
        k: v for k, v in before[0].items() if k != "currency"
    }
    assert after[0]["currency"] == {"is_current": False, "reasons": ["document_version_superseded"]}

    # Nothing is transferred to the new version
    new = await _extract(issuer_client, doc_id)
    assert new["version_id"] == v2.json()["id"]
    assert new["approval_state"]["state"] == "not_approved"
    assert new["approval_state"]["version_is_current"] is True
    assert await _history(client, new["id"], issuer_headers) == []
    assert (await client.get(url, headers=inst_headers)).status_code == 409

    # A new approval of V2 restores a current passport bound to version 2
    assert (await _approve(client, new["id"], issuer_headers)).status_code == 201
    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["source_document"]["version_number"] == 2
    assert body["extraction_metadata"]["extraction_id"] == new["id"]
    assert [a["document_version_number"] for a in body["approvals"]] == [2]
    assert body["extraction_metadata"]["passport_version"] == 2
    assert body["excluded_documents"] == []


# ── 3. Approval of the wrong document version ────────────────────────────────


async def test_approval_of_superseded_document_version_is_rejected(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_document: dict,
    created_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
    async_db: AsyncSession,
):
    eid = created_extraction["id"]
    assert (await _new_version(issuer_client, created_document["id"], "v2")).status_code == 201

    for headers in (issuer_headers, legal_headers):
        r = await _approve(client, eid, headers)
        assert r.status_code == 409
        assert r.json()["detail"] == (
            "The extraction belongs to a superseded document version; approvals are accepted only "
            "for the current version."
        )
    assert await _approval_count(async_db) == 0
    state = await _state(client, eid, issuer_headers)
    assert state["state"] == "not_approved" and state["version_is_current"] is False
    r = await client.get(f"/extractions/{eid}", headers=issuer_headers)
    assert r.json()["status"] == "draft"


async def test_superseded_approved_extraction_cannot_be_re_reviewed(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_document: dict,
    approved_extraction: dict,
    issuer_headers: dict,
):
    eid = approved_extraction["id"]
    # While the approval is current, a re-review is refused (unchanged behaviour)
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "An approved extraction cannot be returned to review."

    assert (await _new_version(issuer_client, created_document["id"], "v2")).status_code == 201
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 409
    assert "superseded document version" in r.json()["detail"]
    assert (await client.get(f"/extractions/{eid}", headers=issuer_headers)).json()["status"] == "approved"


# ── 4–6. Material asset metadata changes ─────────────────────────────────────


async def test_material_asset_change_makes_approval_stale_until_re_review(
    client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
):
    url, eid, aid = _passport_url(created_asset), approved_extraction["id"], created_asset["id"]
    assert (await client.get(url, headers=inst_headers)).json()["asset"]["jurisdiction"] == "US-NY"

    r = await admin_client.patch(f"/assets/{aid}", json={"jurisdiction": "KY-CAYMAN"})
    assert r.status_code == 200 and r.json()["material_revision"] == 2

    assert (await client.get(url, headers=inst_headers)).status_code == 409
    assert await _state(client, eid, issuer_headers) == {
        "state": "stale",
        "version_is_current": True,
        "current_approval_types": [],
        "reasons": ["asset_information_changed"],
    }

    # A stale approval is not restored by approving again without a re-review
    for headers in (legal_headers, issuer_headers):
        r = await _approve(client, eid, headers)
        assert r.status_code == 409
        assert "start a re-review" in r.json()["detail"]

    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 200 and r.json()["status"] == "under_review"
    assert (await client.get(url, headers=inst_headers)).status_code == 409

    r = await _approve(client, eid, issuer_headers)
    assert r.status_code == 201
    assert r.json()["asset_material_revision"] == 2
    assert r.json()["asset_snapshot"]["jurisdiction"] == "KY-CAYMAN"
    body = (await client.get(url, headers=inst_headers)).json()
    assert body["asset"]["jurisdiction"] == "KY-CAYMAN"
    assert len(body["approvals"]) == 1

    # Restoring the previous value is a new change: it does not restore the old approval
    r = await admin_client.patch(f"/assets/{aid}", json={"jurisdiction": "US-NY"})
    assert r.json()["material_revision"] == 3
    assert (await client.get(url, headers=inst_headers)).status_code == 409
    history = await _history(client, eid, issuer_headers)
    assert [h["currency"]["is_current"] for h in history] == [False, False]
    assert all("asset_information_changed" in h["currency"]["reasons"] for h in history)


async def test_non_material_patch_keeps_approval_current(
    client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    inst_headers: dict,
):
    r = await admin_client.patch(
        f"/assets/{created_asset['id']}",
        json={"jurisdiction": "US-NY", "issuer_organization_name": "Meridian Property Holdings LLC"},
    )
    assert r.status_code == 200 and r.json()["material_revision"] == 1
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200


async def test_suspended_asset_has_no_current_passport_and_accepts_no_approvals(
    client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
):
    url, eid, aid = _passport_url(created_asset), approved_extraction["id"], created_asset["id"]
    assert (await admin_client.patch(f"/assets/{aid}", json={"status": "suspended"})).status_code == 200

    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "The asset is not active; no current legal passport is available."}
    state = await _state(client, eid, issuer_headers)
    assert state["state"] == "stale"
    assert state["reasons"] == ["asset_information_changed", "asset_not_active"]

    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    r = await _approve(client, eid, legal_headers)
    assert r.status_code == 409
    assert r.json()["detail"] == "Approvals are not accepted while the asset is not active."

    # Reactivation is a material change: a new approval is required
    assert (await admin_client.patch(f"/assets/{aid}", json={"status": "active"})).json()["material_revision"] == 3
    assert (await client.get(url, headers=inst_headers)).status_code == 409
    assert (await _approve(client, eid, legal_headers)).status_code == 201
    assert (await client.get(url, headers=inst_headers)).status_code == 200


# ── 7. Historical approval snapshots ─────────────────────────────────────────


async def test_historical_approval_snapshots_are_preserved(
    client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    created_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
    second_legal_headers: dict,
    admin_headers: dict,
):
    eid = created_extraction["id"]
    first = (await _approve(client, eid, issuer_headers, notes="Original approval")).json()
    original_value = next(
        c["value"] for c in created_extraction["claims"] if c["field_name"] == "ownership_nature"
    )

    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{eid}/review", headers=legal_headers)).status_code == 200
    r = await client.patch(
        f"/extractions/{eid}/claims/{await _claim_id(created_extraction, 'ownership_nature')}",
        headers=legal_headers,
        json={"corrected_value": "Fee simple, amended", "is_unknown": False,
              "corrected_source_page": 2,
              "corrected_passage": "The Trustee holds fee simple title to the Property",
              "expected_claim": _as_read(created_extraction, "ownership_nature")},
    )
    assert r.status_code == 200
    # Another legal reviewer approves the correction (no one approves their own, Stage 9B-5C)
    r = await _approve(client, eid, second_legal_headers)
    assert r.status_code == 201, r.text
    second = r.json()

    for headers in (issuer_headers, legal_headers, admin_headers):  # every audit:read role
        history = await _history(client, eid, headers)
        assert [h["id"] for h in history] == [first["id"], second["id"]]
        old, new = history
        assert old["binding_digest"] == first["binding_digest"]
        assert old["notes"] == "Original approval"
        assert old["asset_snapshot"]["jurisdiction"] == "US-NY"
        assert {c["field_name"]: c["value"] for c in old["evidence_snapshot"]}["ownership_nature"] == original_value
        assert old["currency"] == {
            "is_current": False,
            "reasons": ["evidence_changed", "asset_information_changed"],
        }
        assert new["currency"] == {"is_current": True, "reasons": []}
        assert new["asset_snapshot"]["jurisdiction"] == "KY-CAYMAN"
        assert {c["field_name"]: c["value"] for c in new["evidence_snapshot"]}["ownership_nature"] == (
            "Fee simple, amended"
        )


async def test_historical_approvals_require_audit_permission_and_issuer_scope(
    client: AsyncClient,
    approved_extraction: dict,
    inst_headers: dict,
    other_issuer_headers: dict,
    other_legal_headers: dict,
):
    url = f"/extractions/{approved_extraction['id']}/approvals"
    assert (await client.get(url)).status_code == 403
    assert (await client.get(url, headers=inst_headers)).status_code == 403
    for headers in (other_issuer_headers, other_legal_headers):
        r = await client.get(url, headers=headers)
        assert r.status_code == 404 and r.json() == {"detail": "Extraction not found."}


# ── 8. Unauthorized approval attempts and cross-issuer access ────────────────


async def test_unauthorized_approval_attempts_create_nothing(
    client: AsyncClient,
    created_extraction: dict,
    admin_headers: dict,
    ai_headers: dict,
    other_issuer_headers: dict,
    other_legal_headers: dict,
    inst_headers: dict,
    async_db: AsyncSession,
):
    eid = created_extraction["id"]
    attempts = [
        ({}, 403),
        ({"Authorization": "Bearer not-a-token"}, 401),
        (inst_headers, 403),
        (admin_headers, 403),
        (ai_headers, 403),
        (other_issuer_headers, 404),
        (other_legal_headers, 404),
    ]
    for headers, expected in attempts:
        r = await _approve(client, eid, headers)
        assert r.status_code == expected, (headers, r.status_code, r.text)
    assert await _approval_count(async_db) == 0
    status = (await async_db.execute(text("SELECT status FROM legal_extractions WHERE id = :id"), {"id": eid})).scalar_one()
    assert status == "draft"


async def test_cross_issuer_cannot_re_review_approve_or_observe_staleness(
    client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    other_issuer_headers: dict,
    other_legal_headers: dict,
    inst_beta_headers: dict,
):
    eid = approved_extraction["id"]
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})

    for headers in (other_issuer_headers, other_legal_headers):
        assert (await client.post(f"/extractions/{eid}/review", headers=headers)).status_code == 404
        assert (await _approve(client, eid, headers)).status_code == 404
    assert (await client.get(f"/extractions/{eid}", headers=issuer_headers)).json()["status"] == "approved"

    # An out-of-scope key gets the same 404 as for an unknown asset: staleness is not revealed
    stale = await client.get(_passport_url(created_asset), headers=inst_beta_headers)
    unknown = await client.get(
        _passport_url({**created_asset, "stellar_asset_code": "NOPE"}), headers=inst_beta_headers
    )
    assert stale.status_code == unknown.status_code == 404
    assert stale.json() == unknown.json() == {"detail": "Asset not found."}


# ── 9. Issuer attestation is not independent legal verification ─────────────


async def test_issuer_attestation_is_never_reported_as_independent_verification(
    client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    legal_headers: dict,
    inst_headers: dict,
):
    url = _passport_url(created_asset)
    body = (await client.get(url, headers=inst_headers)).json()
    assert body["verification_level"] == "issuer_attested"
    assert body["source_documents"][0]["approval_types"] == ["issuer_attestation"]
    assert body["source_documents"][0]["independently_verified"] is False
    known = [e for e in body["legal_rights"] if not e["is_unknown"]]
    assert known and all(e["verified"] is True for e in known)
    assert all(e["independently_verified"] is False for e in body["legal_rights"])

    assert (await _approve(client, approved_extraction["id"], legal_headers)).status_code == 201
    body = (await client.get(url, headers=inst_headers)).json()
    assert body["verification_level"] == "independently_verified"
    assert body["source_documents"][0]["approval_types"] == ["issuer_attestation", "legal_verification"]
    for entry in body["legal_rights"]:
        assert entry["independently_verified"] is (not entry["is_unknown"])
    assert [a["approval_type"] for a in body["approvals"]] == ["issuer_attestation", "legal_verification"]


# ── 10. Several documents ────────────────────────────────────────────────────


async def test_conflicting_evidence_across_documents_is_never_silently_accepted(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    inst_headers: dict,
):
    leasehold = "In our opinion the Investors acquire a leasehold interest only."
    opinion = await _upload_document(
        issuer_client, created_asset["id"], "Legal Opinion", "legal_opinion", leasehold
    )
    extraction = await _extract(issuer_client, opinion["id"])
    original = {c["field_name"]: c["value"] for c in extraction["claims"]}
    original_passage = {c["field_name"]: c["supporting_passage"] for c in extraction["claims"]}
    corrections = {
        # A different value: a real conflict
        "ownership_nature": ("Leasehold interest only", 2, leasehold),
        # The same value with different spacing and case: not a conflict
        "income_rights": ("  " + original["income_rights"].upper() + " ", 1, original_passage["income_rights"]),
    }
    for field_name, (value, page, passage) in corrections.items():
        r = await client.patch(
            f"/extractions/{extraction['id']}/claims/{await _claim_id(extraction, field_name)}",
            headers=issuer_headers,
            json={"corrected_value": value, "is_unknown": False,
                  "corrected_source_page": page, "corrected_passage": passage,
                  "expected_claim": _as_read(extraction, field_name)},
        )
        assert r.status_code == 200
    assert (await _approve(client, extraction["id"], issuer_headers)).status_code == 201

    body = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    assert len(body["source_documents"]) == 2
    rights = {e["field_name"]: e for e in body["legal_rights"]}

    conflict = rights["ownership_nature"]
    assert conflict["conflict"] is True
    assert conflict["value"] == "conflicting"
    assert conflict["verified"] is False and conflict["independently_verified"] is False
    assert conflict["supporting_passage"] is None and conflict["source_page"] is None
    assert {s["value"] for s in conflict["sources"]} == {original["ownership_nature"], "Leasehold interest only"}
    assert {s["document_title"] for s in conflict["sources"]} == {"Q4 2024 Prospectus", "Legal Opinion"}

    agreed = rights["income_rights"]
    assert agreed["conflict"] is False and agreed["verified"] is True
    assert len(agreed["sources"]) == 2
    assert all(not e["conflict"] for name, e in rights.items() if name != "ownership_nature")


async def test_document_with_superseded_approval_is_excluded_from_passport(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    inst_headers: dict,
):
    opinion = await _upload_document(issuer_client, created_asset["id"], "Legal Opinion", "legal_opinion")
    extraction = await _extract(issuer_client, opinion["id"])
    assert (await _approve(client, extraction["id"], issuer_headers)).status_code == 201
    assert len((await client.get(_passport_url(created_asset), headers=inst_headers)).json()["source_documents"]) == 2

    assert (await _new_version(issuer_client, opinion["id"], "opinion-v2")).status_code == 201
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()
    assert [d["document_id"] for d in body["source_documents"]] == [created_document["id"]]
    assert body["excluded_documents"] == [{
        "document_id": opinion["id"],
        "title": "Legal Opinion",
        "document_type": "legal_opinion",
        "reasons": ["document_version_superseded"],
    }]
    assert all(
        s["document_id"] == created_document["id"] for e in body["legal_rights"] for s in e["sources"]
    )


# ── 11. Concurrency ──────────────────────────────────────────────────────────


_ORACLE_SQL = text("""
    SELECT ra.id, ra.extraction_id, ra.binding_digest, ra.approved_at, ra.document_version_id,
           ra.asset_material_revision, le.status AS extraction_status,
           (SELECT dv.id FROM document_versions dv
             WHERE dv.document_id = d.id AND dv.version_number = d.current_version_number) AS current_version_id,
           a.material_revision, a.status AS asset_status
      FROM review_approvals ra
      JOIN legal_extractions le ON le.id = ra.extraction_id
      JOIN documents d ON d.id = le.document_id
      JOIN assets a ON a.id = d.asset_id
     WHERE a.id = :asset_id
""")

_NOT_CURRENT = "The legal approval for this asset is no longer current; a re-review is required."
_NEVER_APPROVED = "No approved legal passport is available for this asset yet."


async def test_concurrent_uploads_approvals_and_asset_changes_stay_consistent(
    client: AsyncClient,
    issuer_client: AsyncClient,
    admin_client: AsyncClient,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
    tmp_path: Path,
    monkeypatch,
):
    """
    Real concurrency: file-based SQLite and a separate session per request.

    Before every passport check, the approval history and the live state are read directly from
    the database, and the one expected outcome is derived from them (independently of the
    application's own currency code; this test performs no corrections or tampering):
      200 — a current approval exists (exact version, current asset revision, active asset);
            the passport carries exactly the current approvals of the governing extraction
      409 — approval history exists, but no approval is current
      404 — no approval has ever been recorded for the asset
    The upload-first and approval-first orders are forced deterministically; the random races
    that follow must also leave the database in the state that each status code implies.
    """
    import app.routers.review as review_router

    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'approvals.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    original_lock = review_router.lock_extraction_for_update
    original_load = review_router.load_extraction_context

    def restore():
        monkeypatch.setattr(review_router, "lock_extraction_for_update", original_lock)
        monkeypatch.setattr(review_router, "load_extraction_context", original_load)

    try:
        from tests.conftest import _MERIDIAN_ASSET
        asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
        document = await _upload_document(issuer_client, asset["id"], "Prospectus", "prospectus")

        async def db_state() -> tuple[list, dict]:
            async with factory() as session:
                rows = (await session.execute(_ORACLE_SQL, {"asset_id": asset["id"]})).mappings().all()
            current = {
                r["id"]: r for r in rows
                if r["extraction_status"] == "approved"
                and r["document_version_id"] == r["current_version_id"]
                and r["asset_material_revision"] == r["material_revision"]
                and r["asset_status"] == "active"
            }
            return list(rows), current

        async def current_version_number() -> int:
            return (await issuer_client.get(f"/documents/{document['id']}")).json()["current_version_number"]

        async def approvals_of(extraction_id: str) -> list:
            return [r for r in (await db_state())[0] if r["extraction_id"] == extraction_id]

        async def check_passport_matches_database() -> int:
            rows, current = await db_state()
            # The API's history agrees with the database, approval by approval
            for extraction_id in {r["extraction_id"] for r in rows}:
                history = await _history(client, extraction_id, issuer_headers)
                assert sorted(h["id"] for h in history) == sorted(
                    r["id"] for r in rows if r["extraction_id"] == extraction_id
                )
                for h in history:
                    assert h["currency"]["is_current"] == (h["id"] in current), h
            passport = await client.get(_passport_url(asset), headers=inst_headers)
            if current:
                governing = max(current.values(), key=lambda r: r["approved_at"])["extraction_id"]
                expected = {r["binding_digest"] for r in current.values() if r["extraction_id"] == governing}
                assert passport.status_code == 200, passport.text
                body = passport.json()
                assert {a["binding_digest"] for a in body["approvals"]} == expected
                assert body["source_document"]["version_number"] == await current_version_number()
            elif rows:
                assert passport.status_code == 409, passport.text
                assert passport.json() == {"detail": _NOT_CURRENT}
            else:
                assert passport.status_code == 404, passport.text
                assert passport.json() == {"detail": _NEVER_APPROVED}
            return passport.status_code

        def run_before_lock(other_request, results: list):
            """The other request completes after the approval started, just before its write lock."""
            async def delayed_lock(db, extraction_id):
                results.append(await other_request())
                await original_lock(db, extraction_id)
            monkeypatch.setattr(review_router, "lock_extraction_for_update", delayed_lock)

        def hold_lock(lock_held: asyncio.Event):
            """The approval keeps the write lock for a moment after loading the state."""
            async def slow_load(db, extraction_id):
                ctx = await original_load(db, extraction_id)
                if not lock_held.is_set():
                    lock_held.set()
                    await asyncio.sleep(0.5)
                return ctx
            monkeypatch.setattr(review_router, "load_extraction_context", slow_load)

        # ── (1) Upload first (deterministic), on an asset that was never approved → 404 ──
        assert await check_passport_matches_database() == 404
        extraction = await _extract(issuer_client, document["id"])
        version_before = await current_version_number()
        uploads: list = []
        run_before_lock(lambda: _new_version(issuer_client, document["id"], "upload-first"), uploads)
        approval = await _approve(client, extraction["id"], issuer_headers)
        restore()
        assert [u.status_code for u in uploads] == [201]
        assert await current_version_number() == version_before + 1
        assert approval.status_code == 409 and "superseded document version" in approval.json()["detail"]
        assert await approvals_of(extraction["id"]) == []
        assert await check_passport_matches_database() == 404

        # ── (2) Approval first (deterministic): the upload waits for the lock → 409 ──
        extraction = await _extract(issuer_client, document["id"])
        version_before = await current_version_number()
        lock_held = asyncio.Event()
        hold_lock(lock_held)

        async def upload_when_locked():
            await lock_held.wait()
            return await _new_version(issuer_client, document["id"], "approval-first")

        approval, upload = await asyncio.gather(
            _approve(client, extraction["id"], issuer_headers), upload_when_locked()
        )
        restore()
        assert approval.status_code == 201 and upload.status_code == 201
        (row,) = await approvals_of(extraction["id"])
        assert row["binding_digest"] == approval.json()["binding_digest"]
        assert approval.json()["document_version_number"] == version_before
        assert await current_version_number() == version_before + 1
        assert await check_passport_matches_database() == 409

        # ── (3) A plain approval of the current version → 200 ──
        extraction = await _extract(issuer_client, document["id"])
        assert (await _approve(client, extraction["id"], issuer_headers)).status_code == 201
        assert await check_passport_matches_database() == 200

        # ── (4) Random races: every status code must match the resulting database state ──
        # A new version first: (3)'s approval of the current version would otherwise make the
        # first round's approval a competing extraction (Stage 9B-5C), which is not this race
        assert (await _new_version(issuer_client, document["id"], "before-races")).status_code == 201
        outcomes = []
        for round_number in range(4):
            extraction = await _extract(issuer_client, document["id"])
            version_before = await current_version_number()
            upload, approval = await asyncio.gather(
                _new_version(issuer_client, document["id"], f"race-{round_number}"),
                _approve(client, extraction["id"], issuer_headers),
            )
            version_after = await current_version_number()
            rows = await approvals_of(extraction["id"])
            if upload.status_code == 201:
                assert version_after == version_before + 1
            else:
                assert upload.status_code in (409, 503), upload.text
                assert version_after == version_before
            if approval.status_code == 201:
                (row,) = rows
                assert row["binding_digest"] == approval.json()["binding_digest"]
                # The approval binds the version that was current when it took the lock
                assert approval.json()["document_version_number"] == version_before
            elif approval.status_code == 409:
                # Only possible when the upload committed first
                assert rows == [] and upload.status_code == 201
                assert "superseded document version" in approval.json()["detail"]
            else:
                assert approval.status_code == 503 and rows == [], approval.text
            outcomes.append((upload.status_code, approval.status_code, await check_passport_matches_database()))

        # ── (5) Material asset change racing with two reviewers approving at once ──
        earlier_rounds: list[str] = []
        for round_number in range(3):
            extraction = await _extract(issuer_client, document["id"])
            revision_before = (await admin_client.get(f"/assets/{asset['id']}")).json()["material_revision"]
            patch, issuer_approval, legal_approval = await asyncio.gather(
                admin_client.patch(f"/assets/{asset['id']}", json={"jurisdiction": f"US-{round_number}"}),
                _approve(client, extraction["id"], issuer_headers),
                _approve(client, extraction["id"], legal_headers),
            )
            revision_after = (await admin_client.get(f"/assets/{asset['id']}")).json()["material_revision"]
            if patch.status_code == 200:
                assert revision_after == revision_before + 1 == patch.json()["material_revision"]
            else:
                assert patch.status_code == 503 and revision_after == revision_before, patch.text
            rows = {r["binding_digest"]: r for r in await approvals_of(extraction["id"])}
            created = [r for r in (issuer_approval, legal_approval) if r.status_code == 201]
            assert sorted(rows) == sorted(r.json()["binding_digest"] for r in created)
            for r in created:
                assert r.json()["asset_material_revision"] in (revision_before, revision_after)
            for r in (issuer_approval, legal_approval):
                if r.status_code == 409 and r.json().get("approval_refusal") == "competing_extraction":
                    # Rule B5 (Stage 9B-5C): an earlier round's extraction of this same version still
                    # holds a current approval (given after that round's asset change) and this round's
                    # approval reached the lock before this round's asset change made it stale
                    competing = r.json()["competing_extraction_ids"]
                    assert competing and set(competing) <= set(earlier_rounds), r.text
                elif r.status_code == 409:
                    # The other approval came first and a committed asset change made it stale
                    assert "start a re-review" in r.json()["detail"] and revision_after > revision_before
                else:
                    assert r.status_code in (201, 503), r.text
            earlier_rounds.append(extraction["id"])
            outcomes.append((patch.status_code, issuer_approval.status_code, legal_approval.status_code,
                             await check_passport_matches_database()))
        print(f"concurrency outcomes: {outcomes}")
    finally:
        restore()
        await engine.dispose()


# ── 12. Institutional API stale-approval rejection ───────────────────────────


async def test_institutional_api_never_serves_stale_approvals(
    client: AsyncClient,
    issuer_client: AsyncClient,
    admin_client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    approved_extraction: dict,
    inst_headers: dict,
):
    url = _passport_url(created_asset)
    assert (await client.get(url, headers=inst_headers)).status_code == 200

    await admin_client.patch(f"/assets/{created_asset['id']}", json={"issuer_organization_name": "Unrelated Corp"})
    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}

    await _new_version(issuer_client, created_document["id"], "v2")
    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}
    assert "Unrelated Corp" not in r.text


# ── 13. Tampering and legacy approvals ───────────────────────────────────────


async def test_tampered_binding_or_evidence_is_never_current(
    client: AsyncClient,
    created_asset: dict,
    approved_extraction: dict,
    issuer_headers: dict,
    inst_headers: dict,
    async_db: AsyncSession,
):
    url, eid = _passport_url(created_asset), approved_extraction["id"]

    await async_db.execute(text("UPDATE legal_claims SET value = 'Tampered' WHERE field_name = 'ownership_nature'"))
    await async_db.commit()
    assert (await client.get(url, headers=inst_headers)).status_code == 409
    assert (await _history(client, eid, issuer_headers))[0]["currency"]["reasons"] == ["evidence_changed"]

    await async_db.execute(text("UPDATE review_approvals SET document_sha256 = :h"), {"h": "f" * 64})
    await async_db.commit()
    reasons = (await _history(client, eid, issuer_headers))[0]["currency"]["reasons"]
    assert reasons == ["binding_integrity_failed", "document_hash_mismatch", "evidence_changed"]
    assert (await client.get(url, headers=inst_headers)).status_code == 409


async def test_legacy_unbound_approval_is_never_current_until_re_reviewed(
    client: AsyncClient,
    created_asset: dict,
    created_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
    async_db: AsyncSession,
):
    """An approval created before Stage 9B-3 has no binding: it is history, never current."""
    eid = created_extraction["id"]
    now = datetime.now(timezone.utc)
    async_db.add(ReviewApproval(
        id="legacy-approval", extraction_id=eid, reviewer_id="alice-chen-01", reviewer_role="issuer_reviewer",
        approval_type="issuer_attestation", disclaimer="Legacy approval.", approved_at=now, created_at=now,
    ))
    await async_db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :id"), {"id": eid})
    await async_db.commit()

    url = _passport_url(created_asset)
    assert (await client.get(url, headers=inst_headers)).status_code == 409
    state = await _state(client, eid, issuer_headers)
    assert state["state"] == "stale" and state["reasons"] == ["unbound_legacy_approval"]
    legacy = (await _history(client, eid, issuer_headers))[0]
    assert legacy["binding_digest"] is None
    assert legacy["currency"] == {"is_current": False, "reasons": ["unbound_legacy_approval"]}

    assert (await _approve(client, eid, legal_headers)).status_code == 409
    assert (await client.post(f"/extractions/{eid}/review", headers=legal_headers)).status_code == 200
    assert (await _approve(client, eid, legal_headers)).status_code == 201
    assert (await client.get(url, headers=inst_headers)).status_code == 200
    history = await _history(client, eid, issuer_headers)
    assert [h["id"] for h in history][0] == "legacy-approval"
    assert [h["currency"]["is_current"] for h in history] == [False, True]


async def _file_db(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'interleaving.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    return engine


async def test_forced_interleavings_of_approval_with_upload_and_asset_change(
    client: AsyncClient,
    issuer_client: AsyncClient,
    admin_client: AsyncClient,
    issuer_headers: dict,
    inst_headers: dict,
    tmp_path: Path,
    monkeypatch,
):
    """
    Deterministic versions of the races above (file-based SQLite, a session per request):
    (a) the upload / asset change commits after the approval request started but before it takes
        the write lock — the approval must see the new state;
    (b) the approval holds the write lock while the upload / asset change runs — the other request
        waits, and the approval is no longer current afterwards.
    """
    import app.routers.review as review_router

    engine = await _file_db(tmp_path)
    original_lock = review_router.lock_extraction_for_update
    original_load = review_router.load_extraction_context
    try:
        from tests.conftest import _MERIDIAN_ASSET
        asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
        document = await _upload_document(issuer_client, asset["id"], "Prospectus", "prospectus")
        url = _passport_url(asset)

        async def before_lock(other_request):
            """Runs other_request to completion inside the approval, just before the lock."""
            async def delayed_lock(db, extraction_id):
                outcome.append((await other_request()).status_code)
                await original_lock(db, extraction_id)
            monkeypatch.setattr(review_router, "lock_extraction_for_update", delayed_lock)

        async def while_locked(lock_held: asyncio.Event):
            async def slow_load(db, extraction_id):
                ctx = await original_load(db, extraction_id)
                if not lock_held.is_set():
                    lock_held.set()
                    await asyncio.sleep(0.5)  # keep holding the write lock
                return ctx
            monkeypatch.setattr(review_router, "load_extraction_context", slow_load)

        def restore():
            monkeypatch.setattr(review_router, "lock_extraction_for_update", original_lock)
            monkeypatch.setattr(review_router, "load_extraction_context", original_load)

        # (a) upload commits inside the check/write window → approval refused
        extraction = await _extract(issuer_client, document["id"])
        outcome: list[int] = []
        await before_lock(lambda: _new_version(issuer_client, document["id"], "a-upload"))
        r = await _approve(client, extraction["id"], issuer_headers)
        restore()
        assert outcome == [201]
        assert r.status_code == 409 and "superseded document version" in r.json()["detail"]
        assert await _history(client, extraction["id"], issuer_headers) == []

        # (b) approval holds the lock; the concurrent upload waits and supersedes it afterwards
        extraction = await _extract(issuer_client, document["id"])
        lock_held = asyncio.Event()
        await while_locked(lock_held)

        async def upload_when_locked():
            await lock_held.wait()
            return await _new_version(issuer_client, document["id"], "b-upload")

        approval, upload = await asyncio.gather(_approve(client, extraction["id"], issuer_headers), upload_when_locked())
        restore()
        assert approval.status_code == 201 and upload.status_code == 201
        assert approval.json()["document_version_number"] == upload.json()["version_number"] - 1
        assert (await _state(client, extraction["id"], issuer_headers))["state"] == "superseded"
        assert (await client.get(url, headers=inst_headers)).status_code == 409

        # (a') asset change commits inside the window → the approval binds the new revision
        extraction = await _extract(issuer_client, document["id"])
        outcome = []
        await before_lock(lambda: admin_client.patch(f"/assets/{asset['id']}", json={"jurisdiction": "KY-CAYMAN"}))
        r = await _approve(client, extraction["id"], issuer_headers)
        restore()
        assert outcome == [200]
        assert r.status_code == 201
        assert r.json()["asset_snapshot"]["jurisdiction"] == "KY-CAYMAN"
        assert (await client.get(url, headers=inst_headers)).status_code == 200

        # (b') approval holds the lock; the asset change waits and makes it stale afterwards. A new
        #      version first: (a')'s approval of this version is still current, and a second
        #      approved extraction of one version is refused (Stage 9B-5C, competing_extraction)
        assert (await _new_version(issuer_client, document["id"], "b'-version")).status_code == 201
        extraction = await _extract(issuer_client, document["id"])
        lock_held = asyncio.Event()
        await while_locked(lock_held)

        async def patch_when_locked():
            await lock_held.wait()
            return await admin_client.patch(f"/assets/{asset['id']}", json={"jurisdiction": "GB"})

        approval, patch = await asyncio.gather(_approve(client, extraction["id"], issuer_headers), patch_when_locked())
        restore()
        assert approval.status_code == 201 and patch.status_code == 200
        assert approval.json()["asset_snapshot"]["jurisdiction"] == "KY-CAYMAN"
        assert approval.json()["asset_material_revision"] == patch.json()["material_revision"] - 1
        assert (await _state(client, extraction["id"], issuer_headers))["state"] == "stale"
        assert (await client.get(url, headers=inst_headers)).status_code == 409

        # (c) approval holds the lock; a concurrent correction waits and then finds the extraction
        #     approved — an approved extraction is never corrected, even in a race
        extraction = await _extract(issuer_client, document["id"])
        lock_held = asyncio.Event()
        await while_locked(lock_held)

        async def correct_when_locked():
            await lock_held.wait()
            return await client.patch(
                f"/extractions/{extraction['id']}/claims/{await _claim_id(extraction, 'ownership_nature')}",
                headers=issuer_headers,
                json={"corrected_value": "Changed during approval", "is_unknown": False,
                      "expected_claim": _as_read(extraction, "ownership_nature")},
            )

        approval, correction = await asyncio.gather(
            _approve(client, extraction["id"], issuer_headers), correct_when_locked()
        )
        restore()
        assert approval.status_code == 201
        assert correction.status_code == 409
        assert correction.json()["detail"] == "An approved extraction cannot be corrected."
        assert (await _state(client, extraction["id"], issuer_headers))["state"] == "current"
        claims = (await client.get(f"/extractions/{extraction['id']}", headers=issuer_headers)).json()["claims"]
        assert "Changed during approval" not in {c["value"] for c in claims}
    finally:
        await engine.dispose()
