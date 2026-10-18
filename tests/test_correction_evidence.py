"""
Stage 9B-4 step 7 — reviewer correction evidence integrity.

A correction decides the effective value AND the effective evidence of a claim. A changed value
never inherits the previous quotation or page; omitted evidence is kept only for an unchanged
value; null clears; a claim corrected to unknown carries no evidence; the resulting quotation of a
known claim must be verified on its cited page of the extraction's own document version. A refused
correction changes nothing. The audit trail keeps the evidence before and after every correction.
A verified quotation proves source-text presence only; whether it supports the value is the
reviewer's attestation.

Coverage (the numbers of the step 7 test plan):
 1. Valid correction with explicit quotation and correct page
 2. Changed value without fresh evidence
 3. Changed value with an old unrelated quotation
 4. Invalid source page          5. Out-of-range page number
 6. Explicit null source page    7. Explicit null supporting passage
 8. Omitted versus explicitly null fields
 9. Known claim changed to unknown          10. Unknown claim changed to known
11. Correction with a fabricated quotation  12. Quotation of another document version
13. Another tenant's document               14. Approval after an invalid correction
15. Re-approval after a valid correction    16. Original AI evidence: historical, not authoritative
17. Old approval stale after an evidence change
18. Snapshot format 2 records the corrected evidence
19. No partial persistence                  20. Concurrent corrections, uploads and approvals
21. Legacy correction records               22. Passport representation
(23, migration and rollback: tests/test_migration_9b4.py)
"""

import asyncio
import hashlib
from datetime import datetime, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.correction_rules import CorrectedClaim, RefusedCorrection, resolve_correction
from app.enums import CorrectionRejection, EvidenceStatus, LegalFieldName
from app.evidence import SourceDocumentText
from app.evidence_records import ExtractionSourceText
from app.models.extraction import LegalClaim
from app.models.review import ReviewCorrection
from app.schemas.review import ClaimCorrectionRequest
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET
from tests.mock_ai import MOCK_SOURCE_PAGES
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    FABRICATED,
    FEE_SIMPLE_QUOTE,
    JURISDICTION_QUOTE,
    OWNERSHIP_QUOTE,
    _approval_rows,
    _approve,
    _extract,
    _hold_lock,
    _new_version,
    _passport_url,
    _run_before_lock,
    _snapshot,
    _status,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
)
from tests.test_evidence_binding import _exec, _history, _oracle_check, _reasons

F = LegalFieldName

NEW_VALUE = "Fee simple title held by the Trustee."
INCOME_PASSAGE = MOCK_SOURCE_PAGES[0][2]
DISTRIBUTIONS_QUOTE = "Distributions are paid within thirty days after the end of each calendar quarter"
EVIDENCE_REQUIRED = "Correction rejected — a changed value needs its own source evidence"


def _claim(extraction: dict, field: LegalFieldName) -> dict:
    return next(c for c in extraction["claims"] if c["field_name"] == field.value)


def _as_read(claim) -> dict:
    """The mandatory precondition (issue A): the claim exactly as the caller read it."""
    get = claim.get if isinstance(claim, dict) else lambda name: getattr(claim, name)
    return {"value": get("value"), "is_unknown": bool(get("is_unknown")), "source_page": get("source_page"),
            "supporting_passage": get("supporting_passage")}


async def _patch(client: AsyncClient, extraction: dict, field: LegalFieldName, headers: dict, **body):
    """Corrects a claim as read in `extraction`; a test that corrects it again must read it again."""
    claim = _claim(extraction, field)
    body.setdefault("expected_claim", _as_read(claim))
    return await client.patch(f"/extractions/{extraction['id']}/claims/{claim['id']}", headers=headers, json=body)


async def _reread(client: AsyncClient, extraction: dict, headers: dict) -> dict:
    """A reviewer reloading the extraction before correcting again (expected_claim is mandatory)."""
    r = await client.get(f"/extractions/{extraction['id']}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def _row(db: AsyncSession, claim_id: str) -> tuple:
    return tuple((await db.execute(
        text("SELECT value, is_unknown, source_page, supporting_passage FROM legal_claims WHERE id = :c"),
        {"c": claim_id})).one())


async def _corrections(client: AsyncClient, extraction_id: str, headers: dict) -> list[dict]:
    r = await client.get(f"/extractions/{extraction_id}/corrections", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _refused(r, rule: str, evidence_status: str | None = None) -> None:
    assert r.status_code == 422, r.text
    body = r.json()
    assert set(body) == {"detail", "correction_rule", "evidence_status"}
    assert body["correction_rule"] == rule and body["evidence_status"] == evidence_status
    assert body["detail"].startswith("Correction rejected — ")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ── Unit level: the resolution rules ─────────────────────────────────────────

_SOURCE = ExtractionSourceText(SourceDocumentText.from_page_records("v1", [
    {"page_number": 1, "text_content": "\n".join(MOCK_SOURCE_PAGES[0])},
    {"page_number": 2, "text_content": "\n".join(MOCK_SOURCE_PAGES[1])},
]), 1)


def _ai_claim(is_unknown: bool | str = False) -> LegalClaim:
    if is_unknown == "legacy":  # an unknown claim with leftover text, as corrections stored it before step 7
        return LegalClaim(id="c", extraction_id="e", field_name="ownership_nature", value="AI value", is_unknown=True,
                          source_page=1, supporting_passage=OWNERSHIP_QUOTE)
    if is_unknown:
        return LegalClaim(id="c", extraction_id="e", field_name="voting_rights", value="unknown", is_unknown=True,
                          source_page=None, supporting_passage=None)
    return LegalClaim(id="c", extraction_id="e", field_name="ownership_nature", value="AI value", is_unknown=False,
                      source_page=1, supporting_passage=OWNERSHIP_QUOTE)


@pytest.mark.parametrize("ai_unknown,body,expected", [
    # unchanged value: omitted evidence is kept
    (False, {"corrected_value": "AI value", "is_unknown": False}, CorrectedClaim("AI value", False, 1, OWNERSHIP_QUOTE)),
    # unchanged value, new authentic quotation on the inherited page
    (False, {"corrected_value": "AI value", "is_unknown": False, "corrected_passage": INCOME_PASSAGE},
     CorrectedClaim("AI value", False, 1, INCOME_PASSAGE)),
    # changed value: evidence must be sent, both fields
    (False, {"corrected_value": "New", "is_unknown": False}, RefusedCorrection(CorrectionRejection.evidence_required)),
    (False, {"corrected_value": "New", "is_unknown": False, "corrected_source_page": 1},
     RefusedCorrection(CorrectionRejection.evidence_required)),
    (False, {"corrected_value": "New", "is_unknown": False, "corrected_passage": OWNERSHIP_QUOTE},
     RefusedCorrection(CorrectionRejection.evidence_required)),
    (False, {"corrected_value": "New", "is_unknown": False, "corrected_source_page": 2,
             "corrected_passage": FEE_SIMPLE_QUOTE}, CorrectedClaim("New", False, 2, FEE_SIMPLE_QUOTE)),
    # explicit null clears; a known claim without evidence is refused with the evidence status
    (False, {"corrected_value": "AI value", "is_unknown": False, "corrected_source_page": None},
     RefusedCorrection(CorrectionRejection.evidence_not_verified, EvidenceStatus.page_missing)),
    (False, {"corrected_value": "AI value", "is_unknown": False, "corrected_passage": None},
     RefusedCorrection(CorrectionRejection.evidence_not_verified, EvidenceStatus.quotation_missing)),
    # corrected to unknown: no evidence, canonical value; explicit nulls are fine, evidence is not
    (False, {"corrected_value": "typed text", "is_unknown": True}, CorrectedClaim("unknown", True, None, None)),
    (False, {"corrected_value": "x", "is_unknown": True, "corrected_source_page": None, "corrected_passage": None},
     CorrectedClaim("unknown", True, None, None)),
    (False, {"corrected_value": "x", "is_unknown": True, "corrected_passage": OWNERSHIP_QUOTE},
     RefusedCorrection(CorrectionRejection.unknown_with_evidence)),
    (False, {"corrected_value": "x", "is_unknown": True, "corrected_source_page": 1},
     RefusedCorrection(CorrectionRejection.unknown_with_evidence)),
    # a known claim stating "unknown" is contradictory
    (False, {"corrected_value": " Unknown ", "is_unknown": False, "corrected_source_page": 1,
             "corrected_passage": OWNERSHIP_QUOTE}, RefusedCorrection(CorrectionRejection.unknown_value_for_known_claim)),
    # unknown made known: a change, so evidence is required
    (True, {"corrected_value": "unknown-free value", "is_unknown": False},
     RefusedCorrection(CorrectionRejection.evidence_required)),
    # ...even when a legacy unknown claim still carries its old value and quotation (written before
    # step 7): making it known again never inherits them
    ("legacy", {"corrected_value": "AI value", "is_unknown": False},
     RefusedCorrection(CorrectionRejection.evidence_required)),
    (True, {"corrected_value": "Paid in thirty days.", "is_unknown": False, "corrected_source_page": 2,
            "corrected_passage": DISTRIBUTIONS_QUOTE}, CorrectedClaim("Paid in thirty days.", False, 2, DISTRIBUTIONS_QUOTE)),
])
def test_correction_resolution_rules(ai_unknown, body: dict, expected):
    claim = _ai_claim(ai_unknown)
    request = ClaimCorrectionRequest.model_validate({**body, "expected_claim": _as_read(claim)})
    assert resolve_correction(claim, request, "v1", _SOURCE) == expected


def test_resolution_is_bound_to_the_extractions_version():
    request = ClaimCorrectionRequest.model_validate(
        {"corrected_value": "AI value", "is_unknown": False, "expected_claim": _as_read(_ai_claim())})
    assert resolve_correction(_ai_claim(), request, "v2", _SOURCE) == RefusedCorrection(
        CorrectionRejection.evidence_not_verified, EvidenceStatus.document_version_mismatch)


# ── 1. Valid correction ──────────────────────────────────────────────────────


async def test_valid_correction_with_explicit_quotation_and_page(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict, async_db: AsyncSession
):
    ai = _claim(created_extraction, F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE,
                     correction_note="Title is held by the Trustee.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["original_value"], body["original_is_unknown"], body["original_passage"],
            body["original_source_page"]) == (ai["value"], False, ai["supporting_passage"], 1)
    assert (body["corrected_value"], body["corrected_is_unknown"], body["corrected_passage"],
            body["corrected_source_page"], body["page_history_recorded"]) == (NEW_VALUE, False, FEE_SIMPLE_QUOTE, 2, True)
    assert await _row(async_db, ai["id"]) == (NEW_VALUE, False, 2, FEE_SIMPLE_QUOTE)
    ex = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    evidence = _claim(ex, F.ownership_nature)["evidence"]
    assert (evidence["status"], evidence["cited_page"]) == ("verified", 2)


# ── 2, 3. Changed values and old quotations ──────────────────────────────────


@pytest.mark.parametrize("evidence", [
    {}, {"corrected_source_page": 1}, {"corrected_passage": OWNERSHIP_QUOTE},
], ids=["nothing_sent", "page_only", "passage_only"])
async def test_changed_value_without_fresh_evidence_is_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, evidence: dict
):
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="Leasehold interest only.", is_unknown=False, **evidence)
    _refused(r, "evidence_required")
    assert r.json()["detail"].startswith(EVIDENCE_REQUIRED)
    assert await _snapshot(async_db) == before


async def test_old_ai_quotation_is_never_reused_implicitly_for_a_new_value(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    ai = _claim(created_extraction, F.ownership_nature)
    # Before step 7 this stored the new value with the AI's quotation and page as its "evidence"
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="Full legal title.", is_unknown=False)
    _refused(r, "evidence_required")
    assert await _row(async_db, ai["id"]) == (ai["value"], False, 1, ai["supporting_passage"])


async def test_re_citing_a_quotation_for_a_new_value_is_recorded_as_the_reviewers_own_act(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict
):
    ai = _claim(created_extraction, F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="Fractional beneficial interest in the Meridian Property Trust.",
                     is_unknown=False, corrected_source_page=1, corrected_passage=ai["supporting_passage"])
    assert r.status_code == 200, r.text
    (record,) = await _corrections(client, created_extraction["id"], issuer_headers)
    assert (record["reviewer_id"], record["corrected_passage"], record["corrected_source_page"]) == (
        "bob-martinez-01", ai["supporting_passage"], 1)


async def test_an_unrelated_but_present_quotation_is_presence_not_support(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict
):
    """
    The check proves only that the quotation is on the cited page. Whether it supports the value
    (here: an income passage cited for the ownership field) is the reviewer's attestation; the
    evidence result says so in its scope.
    """
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="Quarterly distributions.", is_unknown=False,
                     corrected_source_page=1, corrected_passage=INCOME_PASSAGE)
    assert r.status_code == 200, r.text
    ex = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    evidence = _claim(ex, F.ownership_nature)["evidence"]
    assert (evidence["status"], evidence["scope"]) == ("verified", "source_text_presence_only")
    assert "not legal correctness" in evidence["explanation"]


# ── 4, 5. Invalid and impossible pages ───────────────────────────────────────


@pytest.mark.parametrize("page,expected", [
    (2, "found_on_other_page"), (3, "page_out_of_range"), (999, "page_out_of_range"),
    (0, None), (-1, None), (True, None), ("1", None), (1.5, None),
], ids=["wrong_page", "page_3_of_2", "page_999", "zero", "negative", "boolean", "string", "float"])
async def test_invalid_or_impossible_source_pages_are_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, page, expected
):
    ai = _claim(created_extraction, F.jurisdiction)
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.jurisdiction, legal_headers,
                     corrected_value=ai["value"], is_unknown=False, corrected_source_page=page)
    assert r.status_code == 422, r.text
    if expected is None:
        assert isinstance(r.json()["detail"], list)  # the request validation convention (FastAPI)
    else:
        _refused(r, "evidence_not_verified", expected)
    assert await _snapshot(async_db) == before


# ── 6, 7, 8. Explicit null versus omitted ────────────────────────────────────


@pytest.mark.parametrize("cleared,expected", [
    ({"corrected_source_page": None}, "page_missing"),
    ({"corrected_passage": None}, "quotation_missing"),
    ({"corrected_source_page": None, "corrected_passage": None}, "quotation_missing"),
], ids=["null_page", "null_passage", "both_null"])
async def test_explicit_null_clears_evidence_so_a_known_claim_is_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, cleared, expected
):
    ai = _claim(created_extraction, F.jurisdiction)
    r = await _patch(client, created_extraction, F.jurisdiction, legal_headers,
                     corrected_value=ai["value"], is_unknown=False, **cleared)
    _refused(r, "evidence_not_verified", expected)
    # The clear is never stored half-way, and the old evidence is not presented as new evidence
    assert await _row(async_db, ai["id"]) == (ai["value"], False, 1, ai["supporting_passage"])
    assert (await async_db.execute(text("SELECT COUNT(*) FROM review_corrections"))).scalar_one() == 0


async def test_omitted_evidence_is_kept_only_for_an_unchanged_value(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict, async_db: AsyncSession
):
    ai = _claim(created_extraction, F.jurisdiction)
    # A note-only correction keeps the evidence; the audit records it as unchanged
    r = await _patch(client, created_extraction, F.jurisdiction, legal_headers,
                     corrected_value=ai["value"], is_unknown=False, correction_note="Checked.")
    assert r.status_code == 200, r.text
    assert (r.json()["corrected_passage"], r.json()["corrected_source_page"]) == (ai["supporting_passage"], 1)
    # A new quotation with the page omitted: the page is kept and the quotation verified on it
    r = await _patch(client, await _reread(client, created_extraction, legal_headers), F.jurisdiction, legal_headers,
                     corrected_value=ai["value"], is_unknown=False, corrected_passage=JURISDICTION_QUOTE)
    assert r.status_code == 200, r.text
    assert await _row(async_db, ai["id"]) == (ai["value"], False, 1, JURISDICTION_QUOTE)
    # The same correction with the page sent as null is refused: null is not omission
    r = await _patch(client, await _reread(client, created_extraction, legal_headers), F.jurisdiction, legal_headers,
                     corrected_value=ai["value"], is_unknown=False, corrected_passage=JURISDICTION_QUOTE,
                     corrected_source_page=None)
    _refused(r, "evidence_not_verified", "page_missing")
    assert len(await _corrections(client, created_extraction["id"], issuer_headers)) == 2


async def test_misspelled_evidence_fields_are_refused_not_ignored(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    before = await _snapshot(async_db)
    ai = _claim(created_extraction, F.jurisdiction)
    for extra in ({"source_page": 2}, {"supporting_passage": FABRICATED}, {"evidence_status": "verified"}):
        r = await _patch(client, created_extraction, F.jurisdiction, legal_headers,
                         corrected_value=ai["value"], is_unknown=False, **extra)
        assert r.status_code == 422 and isinstance(r.json()["detail"], list), r.text
    assert await _snapshot(async_db) == before


# ── 9, 10. Unknown claims ────────────────────────────────────────────────────


async def test_known_claim_corrected_to_unknown_loses_all_active_evidence(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, issuer_headers: dict,
    second_legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    ai = _claim(created_extraction, F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="typed by the reviewer", is_unknown=True)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["corrected_value"], body["corrected_is_unknown"], body["corrected_passage"],
            body["corrected_source_page"]) == ("unknown", True, None, None)
    assert (body["original_value"], body["original_passage"], body["original_source_page"]) == (
        ai["value"], ai["supporting_passage"], 1)
    assert await _row(async_db, ai["id"]) == ("unknown", True, None, None)
    ex = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    assert _claim(ex, F.ownership_nature)["evidence"]["status"] == "not_applicable"

    # Another legal reviewer approves the correction (no one approves their own, Stage 9B-5C)
    approval = await _approve(client, created_extraction["id"], second_legal_headers)
    assert approval.status_code == 201, approval.text
    record = next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert (record["value"], record["evidence_status"], record["page_text_sha256"], record["quotation_sha256"]) == (
        "unknown", "not_applicable", None, None)
    entry = next(e for e in (await client.get(_passport_url(created_asset), headers=inst_headers)).json()["legal_rights"]
                 if e["field_name"] == "ownership_nature")
    assert (entry["is_unknown"], entry["verified"], entry["independently_verified"], entry["supporting_passage"]) == (
        True, False, False, None)


@pytest.mark.parametrize("evidence", [
    {"corrected_source_page": 1}, {"corrected_passage": OWNERSHIP_QUOTE},
    {"corrected_source_page": 1, "corrected_passage": OWNERSHIP_QUOTE},
], ids=["page", "passage", "both"])
async def test_evidence_sent_with_an_unknown_correction_is_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, evidence: dict
):
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="unknown", is_unknown=True, **evidence)
    _refused(r, "unknown_with_evidence")
    assert await _snapshot(async_db) == before


async def test_unknown_claim_made_known_needs_its_own_verified_evidence(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    voting = _claim(created_extraction, F.voting_rights)
    for body, rule, status in [
        ({}, "evidence_required", None),
        ({"corrected_source_page": 2}, "evidence_required", None),
        ({"corrected_source_page": 2, "corrected_passage": FABRICATED}, "evidence_not_verified", "not_found"),
        ({"corrected_source_page": 1, "corrected_passage": DISTRIBUTIONS_QUOTE}, "evidence_not_verified",
         "found_on_other_page"),
    ]:
        r = await _patch(client, created_extraction, F.voting_rights, legal_headers,
                         corrected_value="Distributions within thirty days.", is_unknown=False, **body)
        _refused(r, rule, status)
    assert await _row(async_db, voting["id"]) == ("unknown", True, None, None)
    r = await _patch(client, created_extraction, F.voting_rights, legal_headers,
                     corrected_value="Distributions within thirty days.", is_unknown=False,
                     corrected_source_page=2, corrected_passage=DISTRIBUTIONS_QUOTE)
    assert r.status_code == 200, r.text
    assert await _row(async_db, voting["id"]) == ("Distributions within thirty days.", False, 2, DISTRIBUTIONS_QUOTE)


async def test_legacy_unknown_claim_with_leftover_text_never_lends_it_to_a_known_value(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    ai = _claim(created_extraction, F.ownership_nature)
    # Before step 7, marking a claim unknown kept its value, page and quotation
    await _exec(async_db, "UPDATE legal_claims SET is_unknown = 1 WHERE id = :c", c=ai["id"])
    # The reviewer reads the legacy record as it is stored now
    r = await _patch(client, await _reread(client, created_extraction, legal_headers), F.ownership_nature, legal_headers,
                     corrected_value=ai["value"], is_unknown=False)
    _refused(r, "evidence_required")
    assert await _row(async_db, ai["id"]) == (ai["value"], True, 1, ai["supporting_passage"])


@pytest.mark.parametrize("flag", ["yes", "true", 1, 0, None])
async def test_unknown_flag_must_be_a_boolean(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, flag
):
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers,
                     corrected_value="unknown", is_unknown=flag)
    assert r.status_code == 422 and isinstance(r.json()["detail"], list), r.text
    assert await _snapshot(async_db) == before


async def test_known_claim_stating_unknown_is_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict
):
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value="UNKNOWN",
                     is_unknown=False, corrected_source_page=1, corrected_passage=OWNERSHIP_QUOTE)
    _refused(r, "unknown_value_for_known_claim")


# ── 11, 12, 13. Fabricated, other-version and other-tenant quotations ────────


@pytest.mark.parametrize("passage,expected", [
    (FABRICATED, "not_found"),
    (OWNERSHIP_QUOTE.lower(), "not_found"),
    ("Investors shall hold a fractional beneficial ownership interest in the Meridian Property Fund.", "not_found"),
    ("x", "quotation_too_short"),
    ("   ", "quotation_missing"),
], ids=["invented", "lower_cased", "word_changed", "single_character", "whitespace"])
async def test_fabricated_or_unusable_quotation_is_refused(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, passage, expected
):
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=1, corrected_passage=passage)
    _refused(r, "evidence_not_verified", expected)
    assert await _snapshot(async_db) == before


async def test_quotation_of_another_document_version_is_never_accepted(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict
):
    only_in_v2 = "Version two adds a sentence that exists only in the amended prospectus."
    await _new_version(client, created_document["id"], make_source_pdf(only_in_v2), issuer_headers)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=only_in_v2)
    _refused(r, "evidence_not_verified", "not_found")  # checked against version 1 only
    v2_extraction = await _extract(client, created_document["id"], issuer_headers)
    r = await _patch(client, v2_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=only_in_v2)
    assert r.status_code == 200, r.text


async def test_other_tenants_can_neither_correct_nor_lend_their_text(
    client: AsyncClient, admin_client: AsyncClient, created_extraction: dict, legal_headers: dict,
    other_issuer_headers: dict, other_legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    before = await _snapshot(async_db)
    for headers in (other_issuer_headers, other_legal_headers):
        r = await _patch(client, created_extraction, F.ownership_nature, headers, corrected_value=NEW_VALUE,
                         is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
        assert r.status_code == 404 and r.json() == {"detail": "Extraction not found."}
    assert (await _patch(client, created_extraction, F.ownership_nature, inst_headers, corrected_value=NEW_VALUE,
                         is_unknown=False)).status_code == 403
    assert await _snapshot(async_db) == before

    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    harbor_text = "Harbor bond covenants appear only in the Harbor issuer's own document."
    await _upload(client, harbor["id"], make_source_pdf(harbor_text), other_issuer_headers, title="Bond terms")
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=harbor_text)
    _refused(r, "evidence_not_verified", "not_found")


# ── 14, 15. Approvals after corrections ──────────────────────────────────────


async def test_approval_after_a_refused_correction_binds_only_verified_evidence(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict
):
    ai = _claim(created_extraction, F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=1, corrected_passage=FABRICATED)
    _refused(r, "evidence_not_verified", "not_found")
    approval = await _approve(client, created_extraction["id"], legal_headers)
    assert approval.status_code == 201, approval.text
    record = next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert (record["value"], record["supporting_passage"], record["correction_id"]) == (
        ai["value"], ai["supporting_passage"], None)
    assert FABRICATED not in approval.text and NEW_VALUE not in approval.text
    assert await _corrections(client, created_extraction["id"], issuer_headers) == []


async def test_reapproval_after_a_valid_correction(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, approved_extraction: dict,
    issuer_headers: dict, legal_headers: dict, second_legal_headers: dict, inst_headers: dict,
):
    eid = approved_extraction["id"]
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{eid}/review", headers=legal_headers)).status_code == 200
    correction = await _patch(client, approved_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                              is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    assert correction.status_code == 200, correction.text
    r = await _approve(client, eid, second_legal_headers)  # not the reviewer who corrected it
    assert r.status_code == 201, r.text
    record = next(c for c in r.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert (record["value"], record["source_page"], record["supporting_passage"], record["correction_id"]) == (
        NEW_VALUE, 2, FEE_SIMPLE_QUOTE, correction.json()["id"])
    old, new = await _history(client, eid, issuer_headers)
    assert old["currency"]["reasons"] == ["evidence_changed", "asset_information_changed"]
    assert new["currency"] == {"is_current": True, "reasons": []}
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200


# ── 16. Original AI evidence: historical, not authoritative ──────────────────


async def test_original_ai_evidence_stays_in_the_audit_trail_but_is_never_current(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, issuer_headers: dict,
    second_legal_headers: dict, inst_headers: dict,
):
    ai = _claim(created_extraction, F.ownership_nature)
    assert (await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                         is_unknown=False, corrected_source_page=2,
                         corrected_passage=FEE_SIMPLE_QUOTE)).status_code == 200
    (record,) = await _corrections(client, created_extraction["id"], issuer_headers)
    assert (record["original_value"], record["original_passage"], record["original_source_page"]) == (
        ai["value"], ai["supporting_passage"], 1)
    current = _claim((await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json(),
                     F.ownership_nature)
    assert (current["value"], current["source_page"], current["supporting_passage"]) == (NEW_VALUE, 2, FEE_SIMPLE_QUOTE)
    approval = await _approve(client, created_extraction["id"], second_legal_headers)  # not the corrector
    assert approval.status_code == 201, approval.text
    assert ai["supporting_passage"] not in str(
        next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature"))
    passport = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert ai["value"] not in passport.text and ai["supporting_passage"] not in passport.text


# ── 17, 18. Approval binding of corrected evidence ───────────────────────────


async def test_old_approval_is_stale_when_the_effective_evidence_changes(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, approved_extraction: dict,
    issuer_headers: dict, legal_headers: dict,
):
    eid = approved_extraction["id"]
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{eid}/review", headers=legal_headers)).status_code == 200
    # Only the quotation changes (a shorter authentic part of the same sentence); the value is unchanged
    ai = _claim(approved_extraction, F.jurisdiction)
    r = await _patch(client, approved_extraction, F.jurisdiction, legal_headers, corrected_value=ai["value"],
                     is_unknown=False, corrected_passage=JURISDICTION_QUOTE)
    assert r.status_code == 200, r.text
    assert await _reasons(client, eid, issuer_headers) == [
        "re_review_in_progress", "evidence_changed", "asset_information_changed"]
    assert (await _approve(client, eid, issuer_headers)).status_code == 201
    old, new = await _history(client, eid, issuer_headers)
    assert old["currency"]["reasons"] == ["evidence_changed", "asset_information_changed"]
    assert new["currency"]["is_current"] is True


async def test_snapshot_format_2_records_the_corrected_evidence(
    client: AsyncClient, created_document: dict, created_extraction: dict, legal_headers: dict,
    second_legal_headers: dict, async_db: AsyncSession,
):
    correction = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                              is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    approval = (await _approve(client, created_extraction["id"], second_legal_headers)).json()
    record = next(c for c in approval["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    page_2 = (await async_db.execute(text("SELECT text_content FROM document_pages WHERE version_id = :v AND "
                                          "page_number = 2"), {"v": created_document["current_version"]["id"]})).scalar_one()
    assert record == {
        "claim_id": _claim(created_extraction, F.ownership_nature)["id"], "field_name": "ownership_nature",
        "value": NEW_VALUE, "is_unknown": False, "source_page": 2, "supporting_passage": FEE_SIMPLE_QUOTE,
        "correction_id": correction.json()["id"], "document_version_id": created_document["current_version"]["id"],
        "evidence_status": "verified", "page_text_sha256": _sha(page_2), "quotation_sha256": _sha(FEE_SIMPLE_QUOTE),
    }
    await _oracle_check(async_db, approval["id"])


# ── 19. No partial persistence ───────────────────────────────────────────────


async def test_failure_while_storing_a_correction_leaves_nothing_behind(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, issuer_headers: dict, async_db: AsyncSession
):
    before = await _snapshot(async_db)

    def fail_on_correction(session, flush_context, instances):
        if any(isinstance(obj, ReviewCorrection) for obj in session.new):
            raise RuntimeError("simulated failure while storing the correction")

    event.listen(async_db.sync_session, "before_flush", fail_on_correction)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                         is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    finally:
        event.remove(async_db.sync_session, "before_flush", fail_on_correction)
    assert await _snapshot(async_db) == before  # neither the audit record nor the claim change remains
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    assert r.status_code == 200, r.text
    assert len(await _corrections(client, created_extraction["id"], issuer_headers)) == 1


async def test_refused_corrections_leave_the_extraction_a_draft(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    for body in ({"corrected_value": NEW_VALUE, "is_unknown": False},
                 {"corrected_value": "x", "is_unknown": True, "corrected_passage": OWNERSHIP_QUOTE},
                 {"corrected_value": NEW_VALUE, "is_unknown": False, "corrected_source_page": 1,
                  "corrected_passage": FABRICATED}):
        assert (await _patch(client, created_extraction, F.ownership_nature, legal_headers, **body)).status_code == 422
    assert await _status(async_db, created_extraction["id"]) == "draft"


# ── 20. Concurrency ──────────────────────────────────────────────────────────


async def test_refused_correction_racing_with_an_approval_never_reaches_it(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    # The fabricated correction runs just before the approval takes its lock: it is refused
    results = _run_before_lock(monkeypatch, lambda: _patch(
        client, ex, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE, is_unknown=False,
        corrected_source_page=1, corrected_passage=FABRICATED))
    approval = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert [r.status_code for r in results] == [422]
    assert approval.status_code == 201, approval.text
    await _oracle_check(file_db, approval.json()["id"])
    assert FABRICATED not in approval.text


async def test_valid_correction_committed_before_the_lock_is_bound_and_a_waiting_one_is_refused(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    held = _hold_lock(monkeypatch)

    async def correct_when_locked():
        await held.wait()
        return await _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                            is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)

    approval, correction = await asyncio.wait_for(
        asyncio.gather(_approve(client, ex["id"], issuer_headers), correct_when_locked()), 30)
    assert approval.status_code == 201 and correction.status_code == 409  # approved first: never corrected
    record = next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert record["correction_id"] is None and record["supporting_passage"] == OWNERSHIP_QUOTE


async def test_correction_racing_with_an_upload_is_checked_against_its_own_version(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    outcomes = []
    for round_number, delay in enumerate((0.0, 0.2, 0.05)):
        ex = await _extract(client, doc["id"], issuer_headers)
        only_in_new = f"Round {round_number} amendment text that exists only in the newer version."

        async def upload_after():
            await asyncio.sleep(delay)
            return await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                                     files={"file": ("v.pdf", make_source_pdf(only_in_new), "application/pdf")})

        valid, borrowed, upload = await asyncio.wait_for(asyncio.gather(
            _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE, is_unknown=False,
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE),
            _patch(client, ex, F.jurisdiction, legal_headers, corrected_value="New York law.", is_unknown=False,
                   corrected_source_page=2, corrected_passage=only_in_new),
            upload_after(),
        ), 60)
        assert upload.status_code in (201, 503), upload.text
        assert valid.status_code in (200, 503), valid.text
        # A sentence of the concurrently uploaded version never verifies a claim of the older one
        assert borrowed.status_code in (422, 503), borrowed.text
        if borrowed.status_code == 422:
            assert borrowed.json()["evidence_status"] == "not_found"
        async with file_db() as s:
            rows = (await s.execute(text("SELECT supporting_passage FROM legal_claims WHERE extraction_id = :e"),
                                    {"e": ex["id"]})).scalars().all()
        assert only_in_new not in rows
        outcomes.append((valid.status_code, borrowed.status_code, upload.status_code))
    print(f"correction race outcomes: {outcomes}")


async def test_concurrent_corrections_and_approvals_keep_bound_evidence_verified(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    outcomes = []
    for round_number, delay in enumerate((0.0, 0.15, 0.05, 0.3)):
        # A document per round: a second approved extraction of one version would be refused
        # (Stage 9B-5C, competing_extraction), which is not what this race is about
        doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers, title=f"Prospectus {round_number}")
        ex = await _extract(client, doc["id"], issuer_headers)

        async def correct_after():
            await asyncio.sleep(delay)
            return await _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                                is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)

        correction, approval = await asyncio.wait_for(
            asyncio.gather(correct_after(), _approve(client, ex["id"], issuer_headers)), 60)
        assert correction.status_code in (200, 409, 503), correction.text
        assert approval.status_code in (201, 503), approval.text
        if approval.status_code == 201:
            await _oracle_check(file_db, approval.json()["id"])
            record = next(c for c in approval.json()["evidence_record"]["claims"]
                          if c["field_name"] == "ownership_nature")
            # Exactly one order happened: the correction before the approval, or not at all
            if correction.status_code == 200:
                assert (record["value"], record["correction_id"]) == (NEW_VALUE, correction.json()["id"])
            else:
                assert correction.status_code == 409 and record["correction_id"] is None
        assert await _approval_rows(file_db, ex["id"]) == ([approval.json()["id"]] if approval.status_code == 201 else [])
        outcomes.append((correction.status_code, approval.status_code))
    print(f"correction/approval race outcomes: {outcomes}")


# ── 21. Legacy correction records ────────────────────────────────────────────


async def test_legacy_correction_records_without_page_history_are_read_unchanged(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, issuer_headers: dict,
    second_legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    ai = _claim(created_extraction, F.income_rights)
    # A record written before step 7: no page history columns set
    await _exec(async_db,
                "INSERT INTO review_corrections (id, extraction_id, claim_id, reviewer_id, reviewer_role, original_value, "
                "original_is_unknown, original_passage, corrected_value, corrected_is_unknown, corrected_passage, "
                "correction_note, created_at) VALUES ('legacy-correction', :e, :c, 'alice-chen-01', 'issuer_reviewer', "
                ":v, 0, :p, :v, 0, NULL, 'Recorded before step 7.', :t)",
                e=created_extraction["id"], c=ai["id"], v=ai["value"], p=ai["supporting_passage"],
                t=datetime(2026, 10, 1, tzinfo=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f"))
    (legacy,) = await _corrections(client, created_extraction["id"], issuer_headers)
    assert (legacy["page_history_recorded"], legacy["original_source_page"], legacy["corrected_source_page"]) == (
        False, None, None)
    assert legacy["correction_note"] == "Recorded before step 7."
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                     is_unknown=False, corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    assert r.status_code == 200
    records = await _corrections(client, created_extraction["id"], issuer_headers)
    assert [x["page_history_recorded"] for x in records] == [False, True]
    assert (await _approve(client, created_extraction["id"], second_legal_headers)).status_code == 201
    rights = {e["field_name"]: e for e in (await client.get(_passport_url(created_asset), headers=inst_headers)).json()["legal_rights"]}
    assert rights["income_rights"]["value_origin"] == "reviewer_correction"  # a correction is on record
    assert rights["income_rights"]["verified"] is True


# ── 22. Passport representation ──────────────────────────────────────────────


async def test_passport_shows_the_corrected_value_with_its_own_verified_evidence(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict,
    second_legal_headers: dict, inst_headers: dict,
):
    assert (await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=NEW_VALUE,
                         is_unknown=False, corrected_source_page=2,
                         corrected_passage=FEE_SIMPLE_QUOTE)).status_code == 200
    assert (await _patch(client, created_extraction, F.jurisdiction, legal_headers,
                         corrected_value="unknown", is_unknown=True)).status_code == 200
    # Another legal reviewer approves Bob's corrections (independent credit needs another reviewer)
    assert (await _approve(client, created_extraction["id"], second_legal_headers)).status_code == 201
    body = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    rights = {e["field_name"]: e for e in body["legal_rights"]}
    ownership = rights["ownership_nature"]
    assert (ownership["value"], ownership["value_origin"], ownership["verified"], ownership["independently_verified"],
            ownership["source_page"], ownership["supporting_passage"], ownership["evidence_status"]) == (
        NEW_VALUE, "reviewer_correction", True, True, 2, FEE_SIMPLE_QUOTE, "verified")
    jurisdiction = rights["jurisdiction"]
    assert (jurisdiction["is_unknown"], jurisdiction["verified"], jurisdiction["evidence_status"]) == (
        True, False, "not_applicable")
    assert rights["income_rights"]["value_origin"] == "ai_extraction"
    assert body["verification_summary"]["source_verified_field_count"] == 3
