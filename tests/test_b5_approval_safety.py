"""
Stage 9B-5C — B5 approval safety: competing extractions, self-review and repeated approvals.

Rules:
- One approved extraction per document version: approving another extraction of a version that
  already has a current approval is refused (409 competing_extraction), whatever the role. The
  passport never chooses between several current approved extractions of one document: such a
  (historical or forged) state excludes the document (competing_current_extractions).
- A legal reviewer cannot approve an extraction whose most recent correction of any claim they
  made themselves (409 self_review). A historical approval of that kind is kept as recorded, but
  the passport never counts it (self_reviewed_approval if it is the only one).
- One current approval per reviewer and extraction (409 duplicate_approval); the passport shows
  one approval per reviewer and extraction even for historical duplicates.
- The approval request accepts `notes` only; identity always comes from the token.
Every refusal stores nothing. A reviewer role is recorded as configured; it does not prove
organizational independence.

Coverage:
 1. Issuer-only competing extraction
 2. Previously legally approved extraction (any role, symmetric)
 3. Conflicting current extractions (historical or forged state)
 4. Approval attribution integrity (other documents, stale approvals)
 5. Self-review
 6. Duplicate reviewer approval
 7. Stale or mismatched evidence
 8. Tenant isolation
 9. Valid existing workflows
10. Passport verification correctness
11. Concurrency
"""

import asyncio
import uuid
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval_integrity import bind_approval, load_extraction_context, summarize_extraction
from app.enums import LegalFieldName
from app.models.review import ReviewApproval
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET, foreign_keys_unenforced
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    FEE_SIMPLE_QUOTE,
    _approval_rows,
    _approve,
    _correct,
    _extract,
    _new_version,
    _passport_url,
    _snapshot,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
)
from tests.test_passport_verification import _passport, _rights

F = LegalFieldName
CORRECTED = "Fee simple title held by the Trustee."


def _refusal(r, expected: str) -> dict:
    assert r.status_code == 409, r.text
    body = r.json()
    assert body["approval_refusal"] == expected and isinstance(body["detail"], str)
    return body


async def _bind_directly(db: AsyncSession, extraction_id: str, reviewer_id: str, role: str, approval_type: str) -> str:
    """An approval bound without the API's gate, as a record from before Stage 9B-5C could be."""
    ctx = await load_extraction_context(db, extraction_id)
    now = datetime.now(timezone.utc)
    approval = ReviewApproval(
        id=str(uuid.uuid4()), extraction_id=extraction_id, reviewer_id=reviewer_id, reviewer_role=role,
        approval_type=approval_type, disclaimer="Historical approval.", approved_at=now, created_at=now,
    )
    bind_approval(approval, ctx)
    db.add(approval)
    await db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :e"), {"e": extraction_id})
    await db.commit()
    return approval.id


def _without_time(body: dict) -> dict:
    return {k: v for k, v in body.items() if k != "retrieved_at"}


async def _correct_ownership(client: AsyncClient, extraction: dict, headers: dict) -> dict:
    return await _correct(client, extraction, F.ownership_nature, headers, corrected_value=CORRECTED,
                          corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)


# ── 1, 2. Competing extractions ──────────────────────────────────────────────


@pytest.mark.parametrize("competitor", ["issuer", "legal", "second_legal"])
async def test_a_competing_extraction_cannot_replace_a_legally_approved_one(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, second_legal_headers: dict, inst_headers: dict,
    async_db: AsyncSession, competitor: str,
):
    x = created_extraction
    await _approve(client, x["id"], legal_headers)
    before = await _passport(client, created_asset, inst_headers)
    # The issuer runs a new extraction of the SAME version and changes a value in it
    y = await _extract(client, created_document["id"], issuer_headers)
    await _correct(client, y, F.jurisdiction, issuer_headers, corrected_value="Cayman Islands law governs the trust.",
                   corrected_source_page=1, corrected_passage="governed by the laws of the State of New York")
    rows = await _snapshot(async_db)

    headers = {"issuer": issuer_headers, "legal": legal_headers, "second_legal": second_legal_headers}[competitor]
    body = _refusal(await _approve(client, y["id"], headers), "competing_extraction")
    assert body["competing_extraction_ids"] == [x["id"]]
    # Nothing was stored, and the passport is exactly what it was
    assert await _snapshot(async_db) == rows
    after = await _passport(client, created_asset, inst_headers)
    assert _without_time(after) == _without_time(before)
    assert after["extraction_metadata"]["extraction_id"] == x["id"]
    assert after["verification_summary"]["legal_reviewer_approval_recorded"] is True


async def test_the_approved_extraction_itself_can_still_receive_another_reviewers_approval(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    await _extract(client, created_document["id"], issuer_headers)  # an unapproved draft does not compete
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201
    assert (await _approve(client, created_extraction["id"], issuer_headers)).status_code == 201
    summary = (await _passport(client, created_asset, inst_headers))["verification_summary"]
    assert (summary["issuer_attestation_recorded"], summary["legal_reviewer_approval_recorded"]) == (True, True)


async def test_a_competing_extraction_can_be_approved_once_the_approved_one_is_no_longer_current(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_document: dict,
    created_extraction: dict, issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    await _approve(client, created_extraction["id"], legal_headers)
    y = await _extract(client, created_document["id"], issuer_headers)
    _refusal(await _approve(client, y["id"], issuer_headers), "competing_extraction")
    # A material asset change makes X's approval stale: it no longer competes
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await _approve(client, y["id"], issuer_headers)).status_code == 201
    body = await _passport(client, created_asset, inst_headers)
    assert body["extraction_metadata"]["extraction_id"] == y["id"]
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False  # X's is stale
    # ...and X, now re-reviewed, would compete with Y: refused in turn
    assert (await client.post(f"/extractions/{created_extraction['id']}/review", headers=legal_headers)).status_code == 200
    _refusal(await _approve(client, created_extraction["id"], legal_headers), "competing_extraction")


async def test_a_new_document_version_is_never_blocked_by_the_previous_versions_approval(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict,
    issuer_headers: dict, inst_headers: dict,
):
    v2 = await _new_version(client, created_document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    ex2 = await _extract(client, created_document["id"], issuer_headers)
    r = await _approve(client, ex2["id"], issuer_headers)
    assert r.status_code == 201 and r.json()["document_version_id"] == v2["id"]
    assert (await _passport(client, created_asset, inst_headers))["source_document"]["version_number"] == 2


# ── 3. Conflicting current extractions (historical or forged state) ──────────


async def test_conflicting_current_extractions_are_never_resolved_by_the_latest_approval(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approve(client, created_extraction["id"], legal_headers)
    y = await _extract(client, created_document["id"], issuer_headers)
    # A state the API now refuses: Y bound directly, later than X
    await _bind_directly(async_db, y["id"], "alice-chen-01", "issuer_reviewer", "issuer_attestation")
    assert summarize_extraction(await load_extraction_context(async_db, y["id"])).current_approvals

    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}  # the only document is excluded


async def test_a_document_with_conflicting_extractions_contributes_nothing(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approve(client, created_extraction["id"], legal_headers)
    y = await _extract(client, created_document["id"], issuer_headers)
    await _bind_directly(async_db, y["id"], "alice-chen-01", "issuer_reviewer", "issuer_attestation")
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
    await _approve(client, (await _extract(client, other["id"], issuer_headers))["id"], issuer_headers)

    body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [other["id"]]
    assert body["excluded_documents"] == [{
        "document_id": created_document["id"], "title": created_document["title"],
        "document_type": created_document["document_type"], "reasons": ["competing_current_extractions"],
    }]
    # Neither X's legal approval nor Y's attestation is used
    assert {a["document_id"] for a in body["approvals"]} == {other["id"]}
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False
    assert not any(e["independently_verified"] for e in body["legal_rights"])
    assert body["verification_level"] == "issuer_attested"


# ── 4. Approval attribution integrity ────────────────────────────────────────


async def test_extractions_of_other_documents_never_compete(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    await _approve(client, created_extraction["id"], legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
    ex = await _extract(client, other["id"], issuer_headers)
    assert (await _approve(client, ex["id"], issuer_headers)).status_code == 201
    body = await _passport(client, created_asset, inst_headers)
    assert len(body["source_documents"]) == 2 and body["excluded_documents"] == []


@pytest.mark.parametrize("body", [
    {"reviewer_id": "bob-martinez-01"},
    {"approval_type": "legal_verification"},
    {"reviewer_role": "independent_legal_reviewer"},
    {"notes": "ok", "verified": True},
], ids=["reviewer_id", "approval_type", "reviewer_role", "extra_with_notes"])
async def test_the_approval_request_never_carries_identity_or_verification(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession, body: dict,
):
    rows = await _snapshot(async_db)
    r = await _approve(client, created_extraction["id"], issuer_headers, body=body)
    assert r.status_code == 422 and {e["type"] for e in r.json()["detail"]} == {"extra_forbidden"}
    assert await _snapshot(async_db) == rows
    r = await _approve(client, created_extraction["id"], issuer_headers, body={"notes": "Reviewed."})
    assert r.status_code == 201
    assert (r.json()["reviewer_id"], r.json()["approval_type"], r.json()["notes"]) == (
        "alice-chen-01", "issuer_attestation", "Reviewed.")


# ── 5. Self-review ───────────────────────────────────────────────────────────


async def test_a_legal_reviewer_cannot_approve_their_own_correction(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict,
    second_legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _correct_ownership(client, created_extraction, legal_headers)
    rows = await _snapshot(async_db)
    _refusal(await _approve(client, created_extraction["id"], legal_headers), "self_review")
    assert await _snapshot(async_db) == rows
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 404

    r = await _approve(client, created_extraction["id"], second_legal_headers)
    assert r.status_code == 201 and r.json()["reviewer_id"] == "grace-osei-01"
    ownership = _rights(await _passport(client, created_asset, inst_headers))["ownership_nature"]
    assert (ownership["value"], ownership["independently_verified"]) == (CORRECTED, True)


async def test_the_issuer_may_attest_its_own_correction(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, inst_headers: dict,
):
    await _correct_ownership(client, created_extraction, issuer_headers)
    assert (await _approve(client, created_extraction["id"], issuer_headers)).status_code == 201
    ownership = _rights(await _passport(client, created_asset, inst_headers))["ownership_nature"]
    # The issuer's own statement: never independent verification
    assert (ownership["verified"], ownership["independently_verified"]) == (True, False)


async def test_only_the_latest_correction_decides_who_reviewed_it(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
):
    await _correct_ownership(client, created_extraction, legal_headers)
    # The issuer corrects the same claim again: the latest correction is no longer Bob's
    current = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    await _correct(client, current, F.ownership_nature, issuer_headers, corrected_value="Fee simple title.",
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201


async def test_a_historical_self_reviewed_approval_is_kept_but_never_counted(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict,
    issuer_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _correct_ownership(client, created_extraction, legal_headers)
    approval_id = await _bind_directly(async_db, created_extraction["id"], "bob-martinez-01",
                                       "independent_legal_reviewer", "legal_verification")
    # The record is preserved and its binding is current; only the passport does not count it
    (history,) = (await client.get(f"/extractions/{created_extraction['id']}/approvals", headers=issuer_headers)).json()
    assert (history["id"], history["currency"]["is_current"]) == (approval_id, True)
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409  # the only document is excluded (self_reviewed_approval)

    # With the issuer's attestation as well, the passport is served on that attestation alone
    assert (await _approve(client, created_extraction["id"], issuer_headers)).status_code == 201
    body = await _passport(client, created_asset, inst_headers)
    assert [a["approval_type"] for a in body["approvals"]] == ["issuer_attestation"]
    assert body["source_documents"][0]["approval_types"] == ["issuer_attestation"]
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False
    assert not any(e["independently_verified"] for e in body["legal_rights"])
    assert body["verification_level"] == "issuer_attested"


async def test_a_self_reviewed_document_is_reported_as_excluded(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _correct_ownership(client, created_extraction, legal_headers)
    await _bind_directly(async_db, created_extraction["id"], "bob-martinez-01",
                         "independent_legal_reviewer", "legal_verification")
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
    await _approve(client, (await _extract(client, other["id"], issuer_headers))["id"], issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert [(d["document_id"], d["reasons"]) for d in body["excluded_documents"]] == [
        (created_document["id"], ["self_reviewed_approval"])]
    assert CORRECTED not in str(body)


# ── 6. Duplicate reviewer approval ───────────────────────────────────────────


@pytest.mark.parametrize("reviewer", ["issuer", "legal"])
async def test_a_reviewer_cannot_approve_the_same_extraction_twice(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, async_db: AsyncSession, reviewer: str,
):
    headers = {"issuer": issuer_headers, "legal": legal_headers}[reviewer]
    assert (await _approve(client, created_extraction["id"], headers)).status_code == 201
    before = await _passport(client, created_asset, inst_headers)
    rows = await _snapshot(async_db)
    _refusal(await _approve(client, created_extraction["id"], headers), "duplicate_approval")
    assert await _snapshot(async_db) == rows
    after = await _passport(client, created_asset, inst_headers)
    assert _without_time(after) == _without_time(before) and len(after["approvals"]) == 1


async def test_re_approval_after_a_re_review_is_not_a_duplicate(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_extraction: dict,
    legal_headers: dict, inst_headers: dict,
):
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{created_extraction['id']}/review", headers=legal_headers)).status_code == 200
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201
    assert len((await _passport(client, created_asset, inst_headers))["approvals"]) == 1


async def test_historical_duplicates_count_once(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, inst_headers: dict,
    async_db: AsyncSession,
):
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201
    single = await _passport(client, created_asset, inst_headers)
    await _bind_directly(async_db, created_extraction["id"], "bob-martinez-01",
                         "independent_legal_reviewer", "legal_verification")
    body = await _passport(client, created_asset, inst_headers)
    assert len(body["approvals"]) == 1  # one reviewer, one approval
    for key in ("verification_level", "verification_status", "verification_summary", "legal_rights",
                "source_documents"):
        assert body[key] == single[key], key


# ── 7. Stale or mismatched evidence ──────────────────────────────────────────


async def test_an_approval_failing_its_integrity_check_neither_competes_nor_counts(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approve(client, created_extraction["id"], legal_headers)
    async with foreign_keys_unenforced(async_db):           # 'another-document' exists nowhere
        await async_db.execute(text("UPDATE review_approvals SET document_id = 'another-document'"))
    y = await _extract(client, created_document["id"], issuer_headers)
    assert (await _approve(client, y["id"], issuer_headers)).status_code == 201
    body = await _passport(client, created_asset, inst_headers)
    assert body["extraction_metadata"]["extraction_id"] == y["id"]
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False


# ── 8. Tenant isolation ──────────────────────────────────────────────────────


async def test_another_tenant_learns_nothing_about_competing_state(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    legal_headers: dict, other_issuer_headers: dict, other_legal_headers: dict, async_db: AsyncSession,
):
    await _approve(client, created_extraction["id"], legal_headers)
    y = await _extract(client, created_document["id"], issuer_headers)
    missing = await _approve(client, "00000000-0000-0000-0000-000000000000", other_issuer_headers)
    rows = await _snapshot(async_db)
    for headers in (other_issuer_headers, other_legal_headers):
        for target in (created_extraction["id"], y["id"]):
            r = await _approve(client, target, headers)
            assert r.status_code == 404 and r.json() == missing.json()
    assert await _snapshot(async_db) == rows


async def test_a_harbor_passport_is_unaffected_by_meridian_extractions(
    client: AsyncClient, admin_client: AsyncClient, created_extraction: dict, source_pdf: bytes,
    legal_headers: dict, other_issuer_headers: dict, other_legal_headers: dict, inst_beta_headers: dict,
):
    await _approve(client, created_extraction["id"], legal_headers)
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Bond terms")
    assert (await _approve(client, (await _extract(client, doc["id"], other_issuer_headers))["id"],
                           other_legal_headers)).status_code == 201
    body = await _passport(client, harbor, inst_beta_headers)
    assert body["excluded_documents"] == [] and [d["document_id"] for d in body["source_documents"]] == [doc["id"]]


# ── 9. Valid existing workflows ──────────────────────────────────────────────


async def test_the_standard_issuer_and_legal_workflow_still_works(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    second_legal_headers: dict, inst_headers: dict,
):
    eid = created_extraction["id"]
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    await _correct_ownership(client, created_extraction, issuer_headers)
    for headers in (issuer_headers, legal_headers, second_legal_headers):
        assert (await _approve(client, eid, headers)).status_code == 201
    body = await _passport(client, created_asset, inst_headers)
    assert len(body["approvals"]) == 3
    assert body["verification_level"] == "independently_verified"
    assert (body["verification_summary"]["issuer_attestation_recorded"],
            body["verification_summary"]["legal_reviewer_approval_recorded"]) == (True, True)


# ── 10. Passport verification correctness ────────────────────────────────────


async def test_issuer_attestation_alone_is_never_independent_verification(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, inst_headers: dict,
):
    await _approve(client, created_extraction["id"], issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert not any(e["independently_verified"] for e in body["legal_rights"])
    assert all(d["independently_verified"] is False for d in body["source_documents"])
    assert body["verification_level"] == "issuer_attested"
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False


# ── 11. Concurrency ──────────────────────────────────────────────────────────


async def test_simultaneous_approvals_by_one_reviewer_create_one_record(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    created = 0
    for round_number in range(3):
        doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers, title=f"Prospectus {round_number}")
        ex = await _extract(client, doc["id"], issuer_headers)
        results = await asyncio.wait_for(asyncio.gather(
            *(_approve(client, ex["id"], legal_headers) for _ in range(4))), 60)
        codes = sorted(r.status_code for r in results)
        assert codes.count(201) <= 1 and set(codes) <= {201, 409, 503}, codes
        for r in results:
            if r.status_code == 409:
                assert r.json()["approval_refusal"] == "duplicate_approval"
        assert len(await _approval_rows(file_db, ex["id"])) == codes.count(201)
        created += codes.count(201)
    assert created >= 1  # the races were real: approvals did succeed


async def test_simultaneous_approvals_of_competing_extractions_approve_one(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    created = 0
    for round_number in range(3):
        doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers, title=f"Prospectus {round_number}")
        x = await _extract(client, doc["id"], issuer_headers)
        y = await _extract(client, doc["id"], issuer_headers)
        rx, ry = await asyncio.wait_for(asyncio.gather(
            _approve(client, x["id"], legal_headers), _approve(client, y["id"], issuer_headers)), 60)
        assert {rx.status_code, ry.status_code} <= {201, 409, 503}
        assert [rx.status_code, ry.status_code].count(201) <= 1, (rx.text, ry.text)
        for r in (rx, ry):
            if r.status_code == 409:
                assert r.json()["approval_refusal"] == "competing_extraction"
        approved = [e for e in (x, y) if await _approval_rows(file_db, e["id"])]
        assert len(approved) == [rx.status_code, ry.status_code].count(201)
        created += len(approved)
    assert created >= 1
