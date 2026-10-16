"""
Stage 9B-4 step 5 — cryptographic evidence binding and approval snapshot integrity.

Every new approval binds its claims in evidence snapshot format 2: per claim the evidence
status, the SHA-256 of the raw stored text of its cited page and of its normalized quotation,
its document version and its latest correction. Before binding, the stored PDF must match its
recorded SHA-256 and its re-extracted text must match the page records. Currency is re-checked
on every read against the live records; nothing a snapshot says about itself is trusted.
Hashes identify content: they prove neither legal correctness nor issuer authenticity.

Coverage:
 1. Format 2 evidence snapshot of a new approval
 2. Deterministic serialization and hashing
 3. Document version binding
 4. Page-text hash
 5. Quotation hash
 6. Source text modified after approval
 7. Quotation modified after approval
 8. Approved value (and correction identity) modified after approval
 9. Evidence snapshot tampering and forgery
10. Manipulated evidence status
11. Document version mismatch
12. Stored PDF bytes inconsistent with the recorded hash
13. PDF text inconsistent with the stored page records
14. Legacy format 1 snapshots
15. Legacy approvals with unverifiable evidence fail closed
16. Historical approvals are never modified
17. No partial approval persistence
18. Valid approval workflow
19. Concurrent uploads and approvals
20. Concurrent corrections and approvals
21. Cross-tenant isolation
22. Stage 9B-3 protections remain effective
"""

import asyncio
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import event, select, text
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.documents as documents_router
import app.routers.review as review_router
from app.approval_integrity import (
    EvidenceBindingError,
    asset_snapshot,
    binding_payload,
    bind_approval,
    canonical_json,
    evidence_snapshot,
    evidence_snapshot_v2,
    load_extraction_context,
    sha256_digest,
)
from app.enums import EvidenceStatus, LegalFieldName
from app.evidence import EvidenceClaim, SourceDocumentText, normalize_text, verify_evidence
from app.models.review import ReviewApproval
from app.source_integrity import _pdf_limits, read_stored_pdf_text, source_integrity_failure
from app.services.storage import StorageService
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET
from tests.mock_ai import MOCK_SOURCE_PAGES
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (  # shared helpers and the file-based database fixture
    FABRICATED,
    FEE_SIMPLE_QUOTE,
    INCOME_QUOTE,
    _approval_rows,
    _approve,
    _correct,
    as_read,
    _extract,
    _hold_lock,
    _new_version,
    _passport_url,
    _run_before_lock,
    _snapshot,
    _status,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
    known,
)

F = LegalFieldName

SNAPSHOT_FIELDS = ("claim_id", "field_name", "value", "is_unknown", "source_page", "supporting_passage")
CLAIM_KEYS = set(SNAPSHOT_FIELDS) | {"correction_id", "document_version_id", "evidence_status",
                                     "page_text_sha256", "quotation_sha256"}
EXPECTED_METHOD = {
    "scope": "source_text_presence_only",
    "verification_method": "exact_substring_token_boundary",
    "normalization_version": 1,
    "min_quotation_chars": 20,
    "min_quotation_words": 3,
    "hash_algorithm": "sha256",
    "page_text_hash": "utf8_raw_stored_page_text",
    "quotation_hash": "utf8_normalized_quotation",
    "source_text_hash": "utf8_json_page_number_and_raw_text_list",
}
NOT_CURRENT = {"detail": "The legal approval for this asset is no longer current; a re-review is required."}


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _source_hash(pages: dict[int, str]) -> str:
    """Independent re-computation of the format 2 source_text_sha256."""
    return _sha(json.dumps([[n, pages[n]] for n in sorted(pages)], ensure_ascii=False, separators=(",", ":")))


async def _pages(db: AsyncSession, version_id: str) -> dict[int, str]:
    rows = (await db.execute(text("SELECT page_number, text_content FROM document_pages WHERE version_id = :v"),
                             {"v": version_id})).all()
    return {n: t for n, t in rows}


async def _history(client: AsyncClient, extraction_id: str, headers: dict) -> list[dict]:
    r = await client.get(f"/extractions/{extraction_id}/approvals", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _reasons(client: AsyncClient, extraction_id: str, headers: dict, index: int = 0) -> list[str]:
    return (await _history(client, extraction_id, headers))[index]["currency"]["reasons"]


async def _state(client: AsyncClient, extraction_id: str, headers: dict) -> dict:
    return (await client.get(f"/extractions/{extraction_id}", headers=headers)).json()["approval_state"]


async def _version_id(db: AsyncSession, extraction_id: str) -> str:
    return (await db.execute(text("SELECT version_id FROM legal_extractions WHERE id = :e"),
                             {"e": extraction_id})).scalar_one()


async def _exec(db: AsyncSession, sql: str, **params) -> None:
    await db.execute(text(sql), params)
    await db.commit()


async def _forge(db: AsyncSession, approval_id: str, rewrite) -> None:
    """
    Rewrites a stored snapshot AND recomputes every digest of the approval, as an attacker with
    database write access could: the digest chain alone cannot reveal such a forgery.
    """
    row = (await db.execute(select(ReviewApproval).where(ReviewApproval.id == approval_id)
                            .execution_options(populate_existing=True))).scalar_one()
    record = rewrite(json.loads(row.evidence_snapshot))
    row.evidence_snapshot = canonical_json(record)
    row.evidence_digest = sha256_digest(record)
    row.binding_digest = sha256_digest(binding_payload(row))
    await db.commit()


def _edit_claim(field: str, **changes):
    def rewrite(record):
        for claim in record["claims"]:
            if claim["field_name"] == field:
                claim.update(changes)
        return record
    return rewrite


async def _bind_format_1(db: AsyncSession, extraction_id: str) -> str:
    """An approval bound the way Stage 9B-3 and step 4 did: format 1, no evidence binding."""
    ctx = await load_extraction_context(db, extraction_id)
    version = ctx.extraction_version
    evidence, asset = evidence_snapshot(ctx.claims), asset_snapshot(ctx.asset)
    now = datetime.now(timezone.utc)
    approval = ReviewApproval(
        id=str(uuid.uuid4()), extraction_id=extraction_id, reviewer_id="bob-martinez-01",
        reviewer_role="independent_legal_reviewer", approval_type="legal_verification",
        disclaimer="Approval bound in format 1.", approved_at=now, created_at=now,
        document_id=ctx.document.id, document_version_id=version.id, document_version_number=version.version_number,
        document_sha256=version.sha256_hash, evidence_snapshot=canonical_json(evidence),
        evidence_digest=sha256_digest(evidence), asset_material_revision=ctx.asset.material_revision,
        asset_snapshot=canonical_json(asset), asset_snapshot_digest=sha256_digest(asset),
    )
    approval.binding_digest = sha256_digest(binding_payload(approval))
    db.add(approval)
    await db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :e"), {"e": extraction_id})
    await db.commit()
    return approval.id


async def _stored_file(db: AsyncSession, tmp_path: Path, version_id: str) -> Path:
    stored_path = (await db.execute(text("SELECT stored_path FROM document_versions WHERE id = :v"),
                                    {"v": version_id})).scalar_one()
    return tmp_path / "storage" / Path(stored_path)  # conftest: StorageService(tmp_path / "storage")


async def _oracle_check(db_or_factory, approval_id: str) -> None:
    """Independent re-verification of a stored format 2 record against the database."""
    async def run(s):
        row = (await s.execute(text("SELECT * FROM review_approvals WHERE id = :id"), {"id": approval_id})).mappings().one()
        pages = await _pages(s, row["document_version_id"])
        return row, pages
    if isinstance(db_or_factory, AsyncSession):
        row, pages = await run(db_or_factory)
    else:
        async with db_or_factory() as s:
            row, pages = await run(s)
    record = json.loads(row["evidence_snapshot"])
    assert row["evidence_snapshot"] == canonical_json(record)
    assert row["evidence_digest"] == sha256_digest(record)
    assert record["format"] == 2 and record["method"] == EXPECTED_METHOD
    assert record["source"]["document_version_id"] == row["document_version_id"]
    assert record["source"]["source_text_sha256"] == _source_hash(pages)
    assert record["source"]["page_count"] == len(pages)
    source = SourceDocumentText.from_page_records(row["document_version_id"], [
        {"page_number": n, "text_content": t} for n, t in pages.items()])
    for c in record["claims"]:
        check = verify_evidence(EvidenceClaim(row["document_version_id"], c["source_page"], c["supporting_passage"],
                                              c["is_unknown"]), source)
        if c["is_unknown"]:
            assert check.status is EvidenceStatus.not_applicable and c["page_text_sha256"] is None
        else:
            assert check.status is EvidenceStatus.verified, c
            assert c["page_text_sha256"] == _sha(pages[c["source_page"]])
            assert c["quotation_sha256"] == _sha(normalize_text(c["supporting_passage"]))


# ── 1. Format 2 evidence snapshot ────────────────────────────────────────────


async def test_new_approval_is_bound_in_evidence_snapshot_format_2(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession,
):
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 201, r.text
    a = r.json()
    record, version = a["evidence_record"], created_document["current_version"]
    assert a["evidence_snapshot_format"] == 2
    assert set(record) == {"format", "method", "source", "claims"} and record["format"] == 2
    assert record["method"] == EXPECTED_METHOD
    pages = await _pages(async_db, version["id"])
    assert record["source"] == {
        "document_version_id": version["id"], "version_number": 1, "document_sha256": version["sha256_hash"],
        "page_count": 2, "source_text_sha256": _source_hash(pages),
    }
    assert [c["field_name"] for c in record["claims"]] == sorted(c["field_name"] for c in created_extraction["claims"])
    claims = {c["field_name"]: c for c in record["claims"]}
    for original in created_extraction["claims"]:
        c = claims[original["field_name"]]
        assert set(c) == CLAIM_KEYS
        assert {k: c[k] for k in SNAPSHOT_FIELDS} == {**{k: original[k] for k in SNAPSHOT_FIELDS[1:]}, "claim_id": original["id"]}
        assert c["document_version_id"] == version["id"] and c["correction_id"] is None
        if original["is_unknown"]:
            assert (c["evidence_status"], c["page_text_sha256"], c["quotation_sha256"]) == ("not_applicable", None, None)
        else:
            assert c["evidence_status"] == "verified"
            assert len(c["page_text_sha256"]) == len(c["quotation_sha256"]) == 64
    # evidence_snapshot keeps the 9B-3 shape: the record's claims in the format 1 fields
    assert a["evidence_snapshot"] == [{k: c[k] for k in SNAPSHOT_FIELDS} for c in record["claims"]]
    stored = (await async_db.execute(text("SELECT evidence_snapshot FROM review_approvals"))).scalar_one()
    assert stored == canonical_json(record)
    await _oracle_check(async_db, a["id"])


# ── 2. Deterministic serialization and hashing ───────────────────────────────


async def test_snapshot_serialization_and_hashing_are_deterministic(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession
):
    first = (await _approve(client, created_extraction["id"], issuer_headers)).json()
    second = (await _approve(client, created_extraction["id"], legal_headers)).json()
    # The same evidence gives the same record and digest; the approvals themselves differ
    assert first["evidence_record"] == second["evidence_record"]
    assert first["evidence_digest"] == second["evidence_digest"] == sha256_digest(first["evidence_record"])
    assert first["binding_digest"] != second["binding_digest"]
    ctx = await load_extraction_context(async_db, created_extraction["id"])
    assert evidence_snapshot_v2(ctx) == evidence_snapshot_v2(ctx) == first["evidence_record"]
    # Key order and whitespace never change the canonical form or the digest
    reordered = json.loads(json.dumps(first["evidence_record"], indent=3), object_pairs_hook=lambda p: dict(reversed(p)))
    assert list(reordered) != list(first["evidence_record"])
    assert canonical_json(reordered) == canonical_json(first["evidence_record"])
    assert sha256_digest(reordered) == first["evidence_digest"]


async def test_non_ascii_text_is_hashed_as_utf8_without_escaping(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    value = "Copropriété fractionnée — año, 信托"
    ex = await _extract(client, created_document["id"], issuer_headers,
                        known(F.ownership_nature, 1, MOCK_SOURCE_PAGES[0][1], value=value))
    a = (await _approve(client, ex["id"], issuer_headers)).json()
    stored = (await async_db.execute(text("SELECT evidence_snapshot FROM review_approvals"))).scalar_one()
    assert value in stored and "\\u" not in stored
    assert a["evidence_digest"] == hashlib.sha256(stored.encode("utf-8")).hexdigest()


# ── 3. Document version binding ──────────────────────────────────────────────


async def test_evidence_is_bound_to_the_approved_document_version(
    client: AsyncClient, created_document: dict, approved_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession,
):
    (v1_approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    v1_record = v1_approval["evidence_record"]
    v2 = await _new_version(client, created_document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    ex2 = await _extract(client, created_document["id"], issuer_headers)
    a2 = (await _approve(client, ex2["id"], issuer_headers)).json()
    record = a2["evidence_record"]
    assert a2["document_version_id"] == record["source"]["document_version_id"] == v2["id"]
    assert (record["source"]["version_number"], record["source"]["document_sha256"]) == (2, v2["sha256_hash"])
    assert {c["document_version_id"] for c in record["claims"]} == {v2["id"]}
    assert record["source"]["source_text_sha256"] == _source_hash(await _pages(async_db, v2["id"]))
    assert record["source"]["source_text_sha256"] != v1_record["source"]["source_text_sha256"]
    # The version 1 approval keeps its own record, bound to version 1
    (after,) = await _history(client, approved_extraction["id"], issuer_headers)
    assert after["evidence_record"] == v1_record
    assert {c["document_version_id"] for c in v1_record["claims"]} == {created_document["current_version"]["id"]}


# ── 4–5. Page-text and quotation hashes ──────────────────────────────────────


async def test_page_text_hash_is_the_sha256_of_the_cited_pages_stored_text(
    client: AsyncClient, created_document: dict, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                   corrected_value="Fee simple title held by the Trustee.",
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    record = (await _approve(client, created_extraction["id"], legal_headers)).json()["evidence_record"]
    pages = await _pages(async_db, created_document["current_version"]["id"])
    for c in record["claims"]:
        if not c["is_unknown"]:
            assert c["page_text_sha256"] == _sha(pages[c["source_page"]])
    ownership = next(c for c in record["claims"] if c["field_name"] == "ownership_nature")
    assert ownership["source_page"] == 2 and ownership["page_text_sha256"] == _sha(pages[2]) != _sha(pages[1])


async def test_quotation_hash_is_the_sha256_of_the_normalized_quotation(
    client: AsyncClient, created_document: dict, issuer_headers: dict
):
    irregular = "Net rental income\n   shall be distributed quarterly"
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.income_rights, 1, irregular))
    record = (await _approve(client, ex["id"], issuer_headers)).json()["evidence_record"]
    income = next(c for c in record["claims"] if c["field_name"] == "income_rights")
    assert income["supporting_passage"] == irregular  # the quotation itself is kept as stored
    assert income["quotation_sha256"] == _sha("Net rental income shall be distributed quarterly")
    assert income["quotation_sha256"] != _sha(irregular)


# ── 6. Source text modified after approval ───────────────────────────────────


@pytest.mark.parametrize("sql,expected", [
    ("UPDATE document_pages SET text_content = 'Replaced text without the quotations.' "
     "WHERE version_id = :v AND page_number = 1", ["source_text_changed", "evidence_not_verified"]),
    ("UPDATE document_pages SET text_content = text_content || ' Appended later.' "
     "WHERE version_id = :v AND page_number = 2", ["source_text_changed"]),
    ("UPDATE document_pages SET text_content = replace(text_content, 'Investors shall', 'Investors  shall') "
     "WHERE version_id = :v AND page_number = 1", ["source_text_changed"]),
    ("INSERT INTO document_pages (id, version_id, page_number, text_content, created_at) "
     "VALUES ('extra-page', :v, 3, 'An extra page.', '2026-10-16 00:00:00.000000')", ["source_text_changed"]),
], ids=["cited_page_replaced", "uncited_page_changed", "whitespace_only", "page_added"])
async def test_source_text_change_after_approval_makes_the_approval_stale(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict,
    issuer_headers: dict, inst_headers: dict, async_db: AsyncSession, sql: str, expected: list,
):
    eid, url = approved_extraction["id"], _passport_url(created_asset)
    assert (await client.get(url, headers=inst_headers)).status_code == 200
    await _exec(async_db, sql, v=created_document["current_version"]["id"])
    assert await _reasons(client, eid, issuer_headers) == expected
    assert await _state(client, eid, issuer_headers) == {
        "state": "stale", "version_is_current": True, "current_approval_types": [], "reasons": expected}
    r = await client.get(url, headers=inst_headers)
    assert r.status_code == 409 and r.json() == NOT_CURRENT


async def test_tampered_document_is_excluded_from_a_multi_document_passport(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict,
    source_pdf: bytes, issuer_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Legal Opinion")
    ex = await _extract(client, other["id"], issuer_headers)
    assert (await _approve(client, ex["id"], issuer_headers)).status_code == 201
    await _exec(async_db, "UPDATE document_pages SET text_content = text_content || ' x' WHERE version_id = :v",
                v=created_document["current_version"]["id"])
    body = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    assert [d["title"] for d in body["source_documents"]] == ["Legal Opinion"]
    assert body["excluded_documents"] == [{
        "document_id": created_document["id"], "title": "Q4 2024 Prospectus", "document_type": "prospectus",
        "reasons": ["source_text_changed"],
    }]


# ── 7–8. Approved claims modified after approval ─────────────────────────────


@pytest.mark.parametrize("column,value,expected", [
    ("supporting_passage", INCOME_QUOTE, ["evidence_changed"]),             # another authentic quotation
    ("supporting_passage", FABRICATED, ["evidence_changed", "evidence_not_verified"]),
    ("source_page", 2, ["evidence_changed", "evidence_not_verified"]),
    ("value", "Leasehold interest only.", ["evidence_changed"]),
    ("is_unknown", 1, ["evidence_changed"]),
], ids=["passage_still_authentic", "passage_fabricated", "page_changed", "value_changed", "made_unknown"])
async def test_approved_claim_modified_after_approval_makes_the_approval_stale(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession, column: str, value, expected: list,
):
    await _exec(async_db, f"UPDATE legal_claims SET {column} = :x WHERE extraction_id = :e AND field_name = 'ownership_nature'",
                x=value, e=approved_extraction["id"])
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == expected
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409


async def test_correction_identity_is_bound(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession
):
    correction = await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                                corrected_value="Fee simple title held by the Trustee.",
                                corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    a = (await _approve(client, created_extraction["id"], legal_headers)).json()
    ownership = next(c for c in a["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert ownership["correction_id"] == correction["id"]
    assert a["currency"]["is_current"] is True

    # Removing a correction record rewrites the audit trail behind the approved value
    await _exec(async_db, "DELETE FROM review_corrections WHERE id = :c", c=correction["id"])
    assert await _reasons(client, created_extraction["id"], issuer_headers) == ["evidence_changed"]


async def test_inserted_correction_record_is_detected(
    client: AsyncClient, created_extraction: dict, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    claim_id = next(c["id"] for c in created_extraction["claims"] if c["field_name"] == "ownership_nature")
    await _exec(
        async_db,
        "INSERT INTO review_corrections (id, extraction_id, claim_id, reviewer_id, reviewer_role, original_value, "
        "original_is_unknown, corrected_value, corrected_is_unknown, created_at) "
        "VALUES ('forged-correction', :e, :c, 'alice-chen-01', 'issuer_reviewer', 'x', 0, 'x', 0, '2099-01-01 00:00:00.000000')",
        e=approved_extraction["id"], c=claim_id,
    )
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == ["evidence_changed"]


# ── 9. Snapshot tampering and forgery ────────────────────────────────────────


async def test_snapshot_edited_without_recomputing_digests_breaks_the_binding(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    await _exec(async_db, "UPDATE review_approvals SET evidence_snapshot = "
                          "replace(evidence_snapshot, 'Fractional beneficial', 'Full legal')")
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == [
        "binding_integrity_failed", "evidence_changed"]


@pytest.mark.parametrize("rewrite", [
    lambda r: {**r, "format": 3},
    lambda r: {k: v for k, v in r.items() if k != "source"},
    lambda r: {**r, "claims": list(reversed(r["claims"]))},
    lambda r: {**r, "claims": r["claims"] + r["claims"][:1]},
    lambda r: {**r, "claims": [{**c, "source_page": str(c["source_page"])} for c in r["claims"]]},
    lambda r: {**r, "claims": [{**c, "extra": True} for c in r["claims"]]},
    lambda r: "not an object",
    lambda r: {**r, "format": True},
], ids=["unknown_format", "missing_source", "unordered", "duplicate_claim", "page_as_string", "extra_key",
        "not_an_object", "format_as_bool"])
async def test_malformed_snapshot_fails_closed_even_with_recomputed_digests(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession, rewrite,
):
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    await _forge(async_db, approval["id"], rewrite)
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)  # never a 500
    assert approval["currency"] == {"is_current": False, "reasons": ["binding_integrity_failed"]}
    assert approval["evidence_snapshot_format"] is None
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409


async def test_unreadable_snapshot_fails_closed(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    await _exec(async_db, "UPDATE review_approvals SET evidence_snapshot = 'not json'")
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    assert approval["currency"]["reasons"] == ["binding_integrity_failed"]
    assert approval["evidence_record"] is None and approval["evidence_snapshot"] is None


@pytest.mark.parametrize("rewrite,expected", [
    (_edit_claim("ownership_nature", page_text_sha256="0" * 64), ["source_text_changed"]),
    (lambda r: {**r, "source": {**r["source"], "source_text_sha256": "0" * 64}}, ["source_text_changed"]),
    (lambda r: {**r, "source": {**r["source"], "page_count": 3}}, ["source_text_changed"]),
    (_edit_claim("ownership_nature", quotation_sha256="0" * 64), ["evidence_not_verified"]),
    (lambda r: {**r, "method": {**r["method"], "normalization_version": 0}}, ["evidence_not_verified"]),
    (lambda r: {**r, "method": {}}, ["evidence_not_verified"]),
], ids=["page_hash", "source_text_hash", "page_count", "quotation_hash", "normalization_version", "method_removed"])
async def test_forged_hashes_or_method_are_detected_against_the_live_records(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession, rewrite, expected
):
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    await _forge(async_db, approval["id"], rewrite)
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == expected


async def test_forged_snapshot_for_fabricated_evidence_is_refuted_by_live_verification(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    """Claim and snapshot rewritten consistently, digests recomputed: the page text still refutes it."""
    eid = approved_extraction["id"]
    (approval,) = await _history(client, eid, issuer_headers)
    await _exec(async_db, "UPDATE legal_claims SET supporting_passage = :p WHERE extraction_id = :e "
                          "AND field_name = 'ownership_nature'", p=FABRICATED, e=eid)
    await _forge(async_db, approval["id"], _edit_claim(
        "ownership_nature", supporting_passage=FABRICATED, quotation_sha256=_sha(normalize_text(FABRICATED)),
        evidence_status="verified"))
    assert await _reasons(client, eid, issuer_headers) == ["evidence_not_verified"]
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409


# ── 10. Manipulated evidence status ──────────────────────────────────────────


@pytest.mark.parametrize("rewrite", [
    _edit_claim("ownership_nature", evidence_status="not_found"),
    _edit_claim("ownership_nature", evidence_status="not_applicable"),
    _edit_claim("voting_rights", evidence_status="verified"),
    _edit_claim("voting_rights", page_text_sha256="0" * 64),
], ids=["known_not_found", "known_not_applicable", "unknown_verified", "unknown_with_hash"])
async def test_manipulated_evidence_status_is_detected(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession, rewrite
):
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    await _forge(async_db, approval["id"], rewrite)
    assert "evidence_not_verified" in await _reasons(client, approved_extraction["id"], issuer_headers)
    assert (await _history(client, approved_extraction["id"], issuer_headers))[0]["currency"]["is_current"] is False


async def test_status_edited_without_recomputing_digests_is_detected(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    await _exec(async_db, "UPDATE review_approvals SET evidence_snapshot = "
                          "replace(evidence_snapshot, '\"evidence_status\":\"verified\"', '\"evidence_status\":\"not_found\"')")
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == [
        "binding_integrity_failed", "evidence_not_verified"]


# ── 11. Document version mismatch ────────────────────────────────────────────


@pytest.mark.parametrize("rewrite,expected", [
    (lambda r: {**r, "source": {**r["source"], "document_version_id": "another-version"}}, ["binding_integrity_failed"]),
    (lambda r: {**r, "source": {**r["source"], "version_number": 7}}, ["binding_integrity_failed"]),
    (lambda r: {**r, "source": {**r["source"], "document_sha256": "0" * 64}}, ["binding_integrity_failed"]),
    (_edit_claim("jurisdiction", document_version_id="another-version"), ["binding_integrity_failed", "evidence_changed"]),
], ids=["source_version", "version_number", "document_sha256", "claim_version"])
async def test_snapshot_version_identity_must_match_the_binding(
    client: AsyncClient, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession, rewrite, expected
):
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    await _forge(async_db, approval["id"], rewrite)
    assert await _reasons(client, approved_extraction["id"], issuer_headers) == expected


async def test_extraction_moved_to_another_version_is_detected(
    client: AsyncClient, created_document: dict, approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    v2 = await _new_version(client, created_document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    await _exec(async_db, "UPDATE legal_extractions SET version_id = :v WHERE id = :e", v=v2["id"], e=approved_extraction["id"])
    reasons = await _reasons(client, approved_extraction["id"], issuer_headers)
    assert {"binding_integrity_failed", "document_hash_mismatch", "evidence_changed",
            "source_text_changed", "evidence_not_verified"} <= set(reasons)


# ── 12–13. Source PDF consistency at approval time ───────────────────────────


async def _tamper_file(path: Path, db: AsyncSession, version_id: str, case: str) -> None:
    if case == "bytes_changed":
        path.write_bytes(make_source_pdf("Altered on disk."))
    elif case == "file_deleted":
        path.unlink()
    elif case == "recorded_hash_changed":
        await _exec(db, "UPDATE document_versions SET sha256_hash = :h WHERE id = :v", h="0" * 64, v=version_id)
    elif case == "stored_path_changed":
        await _exec(db, "UPDATE document_versions SET stored_path = '../../outside.pdf' WHERE id = :v", v=version_id)
    elif case in ("replaced_consistently", "unparsable"):
        data = make_source_pdf("A different document.") if case == "replaced_consistently" else b"%PDF-1.4 broken %%EOF"
        path.write_bytes(data)
        await _exec(db, "UPDATE document_versions SET sha256_hash = :h WHERE id = :v",
                    h=hashlib.sha256(data).hexdigest(), v=version_id)


@pytest.mark.parametrize("case,expected", [
    ("bytes_changed", "file_hash_mismatch"),
    ("file_deleted", "file_unavailable"),
    ("recorded_hash_changed", "file_hash_mismatch"),
    ("stored_path_changed", "file_unavailable"),
    ("replaced_consistently", "page_text_mismatch"),
    ("unparsable", "file_unreadable"),
])
async def test_stored_pdf_inconsistent_with_its_record_refuses_the_approval(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    async_db: AsyncSession, tmp_path: Path, caplog, case: str, expected: str,
):
    version_id = created_document["current_version"]["id"]
    await _tamper_file(await _stored_file(async_db, tmp_path, version_id), async_db, version_id, case)
    before = await _snapshot(async_db)
    with caplog.at_level(logging.ERROR, logger="rwa_security"):
        for headers in (issuer_headers, legal_headers):
            r = await _approve(client, created_extraction["id"], headers)
            assert r.status_code == 409, r.text
            assert r.json() == {
                "detail": "Approval rejected — the stored source document of this version failed its integrity "
                          f"check ({expected}). Approvals are not accepted for this document version.",
                "source_integrity_failure": expected,
            }
    assert await _snapshot(async_db) == before
    assert await _status(async_db, created_extraction["id"]) == "draft"
    logged = [rec.getMessage() for rec in caplog.records if "rejected_source_integrity" in rec.getMessage()]
    assert len(logged) == 2 and f"reason={expected}" in logged[0] and str(tmp_path) not in logged[0]


@pytest.mark.parametrize("sql,expected", [
    ("UPDATE document_pages SET text_content = text_content || ' Appended.' WHERE version_id = :v AND page_number = 1",
     "page_text_mismatch"),
    ("UPDATE document_pages SET page_number = CASE page_number WHEN 1 THEN 2 ELSE 1 END WHERE version_id = :v",
     "page_text_mismatch"),
    ("DELETE FROM document_pages WHERE version_id = :v AND page_number = 2", "page_count_mismatch"),
    ("INSERT INTO document_pages (id, version_id, page_number, text_content, created_at) "
     "VALUES ('extra-page', :v, 3, '', '2026-10-16 00:00:00.000000')", "page_count_mismatch"),
    ("UPDATE document_pages SET page_number = 5 WHERE version_id = :v AND page_number = 2", "page_numbering_mismatch"),
], ids=["text_changed_quotations_kept", "pages_swapped", "page_deleted", "page_added", "renumbered"])
async def test_page_records_inconsistent_with_the_stored_pdf_refuse_the_approval(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession, sql: str, expected: str,
):
    await _exec(async_db, sql, v=created_document["current_version"]["id"])
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 409 and r.json()["source_integrity_failure"] == expected
    assert (await async_db.execute(text("SELECT COUNT(*) FROM review_approvals"))).scalar_one() == 0


async def test_source_check_uses_the_upload_method_and_limits(
    created_document: dict, async_db: AsyncSession, tmp_path: Path
):
    assert _pdf_limits() == documents_router._pdf_limits()
    version_id = created_document["current_version"]["id"]
    stored = await read_stored_pdf_text(async_db, StorageService(str(tmp_path / "storage")), version_id)
    assert stored.failure is None
    assert dict(stored.pages) == await _pages(async_db, version_id)  # identical to the upload's page records


async def test_version_record_changed_after_the_file_check_is_refused(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, monkeypatch,
):
    """The file is checked before the lock; a changed version record under the lock is refused."""
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)

    async def change_version_record():
        async with file_db() as s:
            await _exec(s, "UPDATE document_versions SET sha256_hash = :h WHERE id = :v",
                        h="f" * 64, v=doc["current_version"]["id"])
        return "changed"

    results = _run_before_lock(monkeypatch, change_version_record)
    r = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert results == ["changed"]
    assert r.status_code == 409 and r.json()["source_integrity_failure"] == "version_changed"
    assert await _approval_rows(file_db, ex["id"]) == []


# ── 14–15. Legacy format 1 approvals ─────────────────────────────────────────


async def test_legacy_format_1_approval_is_read_and_revalidated(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    await _bind_format_1(async_db, created_extraction["id"])
    (h,) = await _history(client, created_extraction["id"], issuer_headers)
    assert h["evidence_snapshot_format"] == 1 and isinstance(h["evidence_record"], list)
    assert h["evidence_snapshot"] == h["evidence_record"]
    assert h["evidence_digest"] == sha256_digest(h["evidence_record"])
    # Its claims verify against the stored page text of the approved version: it may stay current
    assert h["currency"] == {"is_current": True, "reasons": []}
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200
    # The 9B-3 rule still applies to it
    await _exec(async_db, "UPDATE legal_claims SET value = 'Changed' WHERE field_name = 'jurisdiction'")
    assert await _reasons(client, created_extraction["id"], issuer_headers) == ["evidence_changed"]


async def test_legacy_format_1_approval_without_verifiable_evidence_fails_closed(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    """A format 1 approval of quotations that are not in the document (the pre-step-4 hole)."""
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    approval_id = await _bind_format_1(async_db, ex["id"])
    row_before = (await async_db.execute(text("SELECT * FROM review_approvals"))).all()

    (h,) = await _history(client, ex["id"], issuer_headers)
    assert h["currency"] == {"is_current": False, "reasons": ["evidence_not_verified"]}
    assert (await _state(client, ex["id"], issuer_headers))["state"] == "stale"
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409 and r.json() == NOT_CURRENT

    # It cannot be renewed without verified evidence: the re-review is possible, the approval is not
    assert (await client.post(f"/extractions/{ex['id']}/review", headers=legal_headers)).status_code == 200
    r = await _approve(client, ex["id"], legal_headers)
    assert r.status_code == 422 and {f["status"] for f in r.json()["evidence_failures"]} == {"no_text_layer"}
    assert [x["id"] for x in await _history(client, ex["id"], issuer_headers)] == [approval_id]
    assert (await async_db.execute(text("SELECT * FROM review_approvals"))).all() == row_before


async def test_legacy_format_1_approval_is_stale_once_its_quotations_leave_the_page_text(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    await _bind_format_1(async_db, created_extraction["id"])
    await _exec(async_db, "UPDATE document_pages SET text_content = 'Replaced.' WHERE version_id = :v",
                v=created_document["current_version"]["id"])
    assert await _reasons(client, created_extraction["id"], issuer_headers) == ["evidence_not_verified"]


async def test_renewal_of_a_stale_legacy_approval_is_bound_in_format_2(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
):
    legacy_id = await _bind_format_1(async_db, created_extraction["id"])
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{created_extraction['id']}/review", headers=legal_headers)).status_code == 200
    renewed = await _approve(client, created_extraction["id"], legal_headers)
    assert renewed.status_code == 201 and renewed.json()["evidence_snapshot_format"] == 2
    legacy, new = await _history(client, created_extraction["id"], issuer_headers)
    assert legacy["id"] == legacy_id and legacy["evidence_snapshot_format"] == 1  # never upgraded
    assert legacy["currency"] == {"is_current": False, "reasons": ["asset_information_changed"]}
    assert new["currency"] == {"is_current": True, "reasons": []}


# ── 16. Historical approvals are never modified ──────────────────────────────


async def test_reads_reviews_and_renewals_never_rewrite_historical_approvals(
    client: AsyncClient, created_asset: dict, created_extraction: dict, minimal_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    blank = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers, title="Scanned")
    blank_ex = await _extract(client, blank["id"], issuer_headers)
    now = datetime.now(timezone.utc)
    async_db.add(ReviewApproval(id="unbound-legacy", extraction_id=blank_ex["id"], reviewer_id="alice-chen-01",
                                reviewer_role="issuer_reviewer", approval_type="issuer_attestation",
                                disclaimer="Legacy approval.", approved_at=now, created_at=now))
    await async_db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :e"), {"e": blank_ex["id"]})
    await async_db.commit()
    await _bind_format_1(async_db, created_extraction["id"])
    before = (await async_db.execute(text("SELECT * FROM review_approvals ORDER BY id"))).all()

    statements: list[str] = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    engine = async_db.bind.sync_engine
    event.listen(engine, "before_cursor_execute", capture)
    try:
        for eid in (created_extraction["id"], blank_ex["id"]):
            await _history(client, eid, issuer_headers)
            await client.get(f"/extractions/{eid}", headers=issuer_headers)
        await client.get(_passport_url(created_asset), headers=inst_headers)
        assert (await client.post(f"/extractions/{blank_ex['id']}/review", headers=legal_headers)).status_code == 200
        assert (await _approve(client, blank_ex["id"], legal_headers)).status_code == 422
        other = await _extract(client, created_extraction["document_id"], issuer_headers)
        assert (await _approve(client, other["id"], legal_headers)).status_code == 201
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    writes = [s for s in statements if s.lstrip().upper().startswith(("UPDATE REVIEW_APPROVALS", "DELETE FROM REVIEW_APPROVALS"))]
    assert writes == []
    after = (await async_db.execute(text("SELECT * FROM review_approvals ORDER BY id"))).all()
    assert [row for row in after if row in before] == before  # every historical row byte-identical
    assert len(after) == len(before) + 1


# ── 17. No partial approval persistence ──────────────────────────────────────


async def test_failure_while_binding_leaves_nothing_behind(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession, monkeypatch
):
    before = await _snapshot(async_db)

    def failing_bind(approval, ctx):
        raise EvidenceBindingError("simulated failure while binding")

    monkeypatch.setattr(review_router, "bind_approval", failing_bind)
    with pytest.raises(EvidenceBindingError):
        await _approve(client, created_extraction["id"], issuer_headers)
    monkeypatch.setattr(review_router, "bind_approval", bind_approval)
    assert await _snapshot(async_db) == before
    assert (await _approve(client, created_extraction["id"], issuer_headers)).status_code == 201
    assert len(await _history(client, created_extraction["id"], issuer_headers)) == 1


async def test_failure_while_storing_the_approval_leaves_nothing_behind(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    before = await _snapshot(async_db)

    def fail_on_approval(session, flush_context, instances):
        if any(isinstance(obj, ReviewApproval) for obj in session.new):
            raise RuntimeError("simulated failure while storing the approval")

    event.listen(async_db.sync_session, "before_flush", fail_on_approval)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await _approve(client, created_extraction["id"], issuer_headers)
    finally:
        event.remove(async_db.sync_session, "before_flush", fail_on_approval)
    assert await _snapshot(async_db) == before  # neither the record nor the status change remains
    # Nothing of the failed attempt is left pending in the session either: a later commit on the
    # same session must not store it (exactly one approval after the retry)
    assert (await _approve(client, created_extraction["id"], issuer_headers)).status_code == 201
    assert len(await _history(client, created_extraction["id"], issuer_headers)) == 1


async def test_binding_never_accepts_unverified_evidence(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    """Defence in depth: bind_approval re-verifies, even if the gate were bypassed."""
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.ownership_nature, 1, FABRICATED))
    ctx = await load_extraction_context(async_db, ex["id"])
    with pytest.raises(EvidenceBindingError, match="ownership_nature"):
        bind_approval(ReviewApproval(id="x", extraction_id=ex["id"], reviewer_id="r", reviewer_role="issuer_reviewer",
                                     approval_type="issuer_attestation", disclaimer="d",
                                     approved_at=datetime.now(timezone.utc)), ctx)


# ── 18. Valid approval workflow ──────────────────────────────────────────────


async def test_valid_workflow_binds_every_approval_in_format_2(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    eid = created_extraction["id"]
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    correction = await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                                corrected_value="Fee simple title held by the Trustee.",
                                corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    legal = (await _approve(client, eid, legal_headers)).json()
    issuer = (await _approve(client, eid, issuer_headers)).json()
    assert legal["evidence_record"] == issuer["evidence_record"]
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200

    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    renewed = (await _approve(client, eid, issuer_headers)).json()
    history = await _history(client, eid, issuer_headers)
    assert [h["evidence_snapshot_format"] for h in history] == [2, 2, 2]
    assert [h["currency"]["reasons"] for h in history] == [["asset_information_changed"]] * 2 + [[]]
    ownership = next(c for c in renewed["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert ownership["correction_id"] == correction["id"]
    for h in history:
        await _oracle_check(async_db, h["id"])
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200


# ── 19–20. Concurrency ───────────────────────────────────────────────────────


async def test_concurrent_uploads_and_approvals_bind_only_verified_source_text(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    outcomes = []
    for round_number, delay in enumerate((0.0, 0.3, 0.05, 0.3, 0.15, 0.0)):
        ex = await _extract(client, doc["id"], issuer_headers)

        async def upload_after():
            await asyncio.sleep(delay)
            return await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                                     files={"file": ("v.pdf", make_source_pdf(f"Race {round_number}."), "application/pdf")})

        upload, first, second = await asyncio.wait_for(asyncio.gather(
            upload_after(), _approve(client, ex["id"], issuer_headers), _approve(client, ex["id"], legal_headers)), 60)
        assert upload.status_code in (201, 409, 503), upload.text
        created = [r for r in (first, second) if r.status_code == 201]
        for r in (first, second):
            assert r.status_code in (201, 409, 503), r.text
            if r.status_code == 409:
                assert "superseded document version" in r.json()["detail"], r.text
        rows = await _approval_rows(file_db, ex["id"])
        assert sorted(rows) == sorted(r.json()["id"] for r in created)
        for r in created:
            assert r.json()["evidence_record"]["source"]["document_version_id"] == ex["version_id"]
            await _oracle_check(file_db, r.json()["id"])
        outcomes.append((upload.status_code, first.status_code, second.status_code))
    print(f"binding race outcomes: {outcomes}")
    assert any(code == 201 for o in outcomes for code in o[1:])  # some approvals won their race


async def test_correction_committed_before_the_lock_is_what_gets_bound(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    claim_id = next(c["id"] for c in ex["claims"] if c["field_name"] == "ownership_nature")
    results = _run_before_lock(monkeypatch, lambda: client.patch(
        f"/extractions/{ex['id']}/claims/{claim_id}", headers=legal_headers,
        json={"corrected_value": "Fee simple title held by the Trustee.", "is_unknown": False,
              "corrected_source_page": 2, "corrected_passage": FEE_SIMPLE_QUOTE,
              "expected_claim": as_read(ex, F.ownership_nature)}))
    r = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert [x.status_code for x in results] == [200]
    assert r.status_code == 201, r.text
    ownership = next(c for c in r.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert (ownership["source_page"], ownership["supporting_passage"]) == (2, FEE_SIMPLE_QUOTE)
    assert ownership["correction_id"] == results[0].json()["id"]
    assert ownership["quotation_sha256"] == _sha(FEE_SIMPLE_QUOTE)
    await _oracle_check(file_db, r.json()["id"])


async def test_correction_waiting_for_the_lock_cannot_alter_the_bound_evidence(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    claim = next(c for c in ex["claims"] if c["field_name"] == "ownership_nature")
    held = _hold_lock(monkeypatch)

    async def correct_when_locked():
        await held.wait()
        return await client.patch(
            f"/extractions/{ex['id']}/claims/{claim['id']}", headers=legal_headers,
            json={"corrected_value": "Changed", "is_unknown": False, "corrected_passage": FEE_SIMPLE_QUOTE,
                  "corrected_source_page": 2, "expected_claim": as_read(ex, F.ownership_nature)})

    approval, correction = await asyncio.wait_for(
        asyncio.gather(_approve(client, ex["id"], issuer_headers), correct_when_locked()), 30)
    assert approval.status_code == 201 and correction.status_code == 409
    ownership = next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert (ownership["supporting_passage"], ownership["correction_id"]) == (claim["supporting_passage"], None)
    history = await _history(client, ex["id"], issuer_headers)
    assert history[0]["currency"] == {"is_current": True, "reasons": []}


# ── 21. Cross-tenant isolation ───────────────────────────────────────────────


async def test_other_tenants_learn_nothing_about_evidence_or_source_integrity(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict,
    issuer_headers: dict, other_issuer_headers: dict, other_legal_headers: dict, inst_beta_headers: dict,
    async_db: AsyncSession, tmp_path: Path,
):
    eid = approved_extraction["id"]
    for headers in (other_issuer_headers, other_legal_headers):
        r = await client.get(f"/extractions/{eid}/approvals", headers=headers)
        assert r.status_code == 404 and r.json() == {"detail": "Extraction not found."}
    # A Meridian extraction whose stored source is inconsistent: Meridian gets 409, others 404
    ex = await _extract(client, created_document["id"], issuer_headers)
    (await _stored_file(async_db, tmp_path, created_document["current_version"]["id"])).write_bytes(b"%PDF-1.4 x %%EOF")
    assert (await _approve(client, ex["id"], issuer_headers)).status_code == 409
    for headers in (other_issuer_headers, other_legal_headers):
        r = await _approve(client, ex["id"], headers)
        missing = await _approve(client, "00000000-0000-0000-0000-000000000000", headers)
        assert r.status_code == missing.status_code == 404 and r.json() == missing.json()
    assert (await client.get(_passport_url(created_asset), headers=inst_beta_headers)).status_code == 404


async def test_harbor_approvals_are_bound_to_harbor_source_text(
    client: AsyncClient, admin_client: AsyncClient, source_pdf: bytes, other_issuer_headers: dict,
    other_legal_headers: dict, async_db: AsyncSession,
):
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Bond terms")
    ex = await _extract(client, doc["id"], other_issuer_headers)
    r = await _approve(client, ex["id"], other_legal_headers)
    assert r.status_code == 201
    assert r.json()["evidence_record"]["source"]["document_version_id"] == doc["current_version"]["id"]
    await _oracle_check(async_db, r.json()["id"])


# ── 22. Stage 9B-3 protections ───────────────────────────────────────────────


async def test_9b3_staleness_reasons_are_unchanged_for_format_2_approvals(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_document: dict,
    approved_extraction: dict, issuer_headers: dict, async_db: AsyncSession,
):
    eid = approved_extraction["id"]
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert await _reasons(client, eid, issuer_headers) == ["asset_information_changed"]
    await _new_version(client, created_document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    assert await _reasons(client, eid, issuer_headers) == ["document_version_superseded", "asset_information_changed"]
    await _exec(async_db, "UPDATE review_approvals SET binding_digest = :d", d="0" * 64)
    assert await _reasons(client, eid, issuer_headers) == [
        "binding_integrity_failed", "document_version_superseded", "asset_information_changed"]


async def test_unbound_legacy_approval_is_still_never_current(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    now = datetime.now(timezone.utc)
    async_db.add(ReviewApproval(id="legacy", extraction_id=created_extraction["id"], reviewer_id="alice-chen-01",
                                reviewer_role="issuer_reviewer", approval_type="issuer_attestation",
                                disclaimer="Legacy approval.", approved_at=now, created_at=now))
    await async_db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :e"),
                           {"e": created_extraction["id"]})
    await async_db.commit()
    (h,) = await _history(client, created_extraction["id"], issuer_headers)
    assert h["currency"] == {"is_current": False, "reasons": ["unbound_legacy_approval"]}
    assert (h["evidence_snapshot_format"], h["evidence_record"], h["evidence_snapshot"]) == (None, None, None)


async def test_source_integrity_check_agrees_with_a_normal_upload(
    created_document: dict, created_extraction: dict, async_db: AsyncSession, tmp_path: Path
):
    ctx = await load_extraction_context(async_db, created_extraction["id"])
    stored = await read_stored_pdf_text(async_db, StorageService(str(tmp_path / "storage")), ctx.extraction_version.id)
    assert source_integrity_failure(stored, ctx.extraction_version, ctx.source) is None
