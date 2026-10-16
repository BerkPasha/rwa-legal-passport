"""
Stage 9B-4 step 4 — the source-evidence gate of new approvals.

POST /extractions/{id}/approvals succeeds only if every known claim of the extraction, as stored
at approval time (reviewer corrections included), is `verified` against the stored page text of
exactly the document version being approved. Evidence states from the client, the AI or earlier
API responses are never trusted. A verified quotation proves source-text presence only, never
legal correctness. Existing approvals and the passport are not changed by this step.

Coverage:
 1. Authentic quotation on the correct page — the approval succeeds
 2. Fabricated quotations (including near misses)
 3. Wrong page reference
 4. Page 999 and other impossible pages on a two-page PDF; no page cited
 5. Single-character and too-short quotations
 6. Missing quotations
 7. Blank, missing or invalid page text
 8. Unknown fields are never promoted to verified
 9. Mixed valid and invalid claims
10. Reviewer corrections are verified as corrected
11. Document version mismatch (superseded version, no fallback to another version's text)
12. Cross-tenant attempts
13. A rejected approval changes nothing (database, history, response contract, log)
14. Concurrency: corrections, page-text changes and uploads racing with the approval
15. Valid approval workflows keep working
16. Every approval path goes through the same gate
"""

import ast
import asyncio
import inspect
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.routers.review as review_router
from app.db.base import Base
from app.db.session import get_db
from app.enums import EvidenceStatus, LegalFieldName
from app.evidence import EvidenceClaim, SourceDocumentText, verify_evidence
from app.evidence_records import ApprovalEvidenceFailure, ExtractionSourceText, approval_evidence_failures
from app.main import app
from app.models.extraction import LegalClaim
from app.models.review import ReviewApproval
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim
from app.services.ai import UNVERIFIED_DISCLAIMER, get_ai_service
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET
from tests.mock_ai import MOCK_SOURCE_PAGES, MockAIService
from tests.pdf_fixtures import make_source_pdf, make_text_pdf

F = LegalFieldName

# Authentic quotations of the standard two-page fixture document (tests/mock_ai.py)
OWNERSHIP_QUOTE = MOCK_SOURCE_PAGES[0][1]
INCOME_QUOTE = "Net rental income shall be distributed quarterly"
JURISDICTION_QUOTE = "governed by the laws of the State of New York"
FEE_SIMPLE_QUOTE = "The Trustee holds fee simple title to the Property"  # page 2
FABRICATED = "Investors may redeem their interests at any time at net asset value."

REJECTION_PREFIX = "Approval rejected — missing source evidence for: "
REJECTION_SUFFIX = (
    ". Every known claim needs a supporting quotation that appears on its cited page of this "
    "document version."
)


class ScriptedAI:
    """Returns exactly the given claims; makes no API call."""

    def __init__(self, *claims: RawClaim):
        self._claims = list(claims)

    async def extract_legal_rights(self, document_id: str, version_id: str, pages: list[dict]) -> AIExtractionResult:
        return AIExtractionResult(
            claims=self._claims,
            ai_model="scripted-test",
            extraction_timestamp=datetime.now(timezone.utc),
            disclaimer=UNVERIFIED_DISCLAIMER,
        )


def known(field: LegalFieldName, page, passage, value: str = "Stated value.") -> RawClaim:
    source = None if passage is None else ClaimSource(page_number=page, supporting_passage=passage)
    return RawClaim(field=field, value=value, is_unknown=False, source=source)


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, headers: dict, title: str = "Prospectus") -> dict:
    r = await client.post(
        f"/assets/{asset_id}/documents",
        headers=headers,
        data={"document_type": "prospectus", "title": title},
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _new_version(client: AsyncClient, document_id: str, pdf: bytes, headers: dict) -> dict:
    r = await client.post(
        f"/documents/{document_id}/versions", headers=headers,
        files={"file": ("v.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _extract(client: AsyncClient, document_id: str, headers: dict, *claims: RawClaim) -> dict:
    """Runs an extraction; with claims, the AI returns exactly those (missing fields → unknown)."""
    if claims:
        app.dependency_overrides[get_ai_service] = lambda: ScriptedAI(*claims)
    try:
        r = await client.post(f"/documents/{document_id}/extractions", headers=headers)
    finally:
        app.dependency_overrides[get_ai_service] = lambda: MockAIService()
    assert r.status_code == 201, r.text
    return r.json()


async def _approve(client: AsyncClient, extraction_id: str, headers: dict, body: dict | None = None):
    return await client.post(f"/extractions/{extraction_id}/approvals", headers=headers, json=body or {})


def as_read(extraction: dict, field: LegalFieldName) -> dict:
    """The mandatory correction precondition (issue A): the claim exactly as read in `extraction`."""
    claim = next(c for c in extraction["claims"] if c["field_name"] == field.value)
    return {key: claim[key] for key in ("value", "is_unknown", "source_page", "supporting_passage")}


async def _correct(client: AsyncClient, extraction: dict, field: LegalFieldName, headers: dict, **changes):
    claim = next(c for c in extraction["claims"] if c["field_name"] == field.value)
    body = {"corrected_value": claim["value"], "is_unknown": claim["is_unknown"],
            "expected_claim": as_read(extraction, field), **changes}
    r = await client.patch(f"/extractions/{extraction['id']}/claims/{claim['id']}", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _failures(r) -> dict[str, str]:
    assert r.status_code == 422, r.text
    return {f["field_name"]: f["status"] for f in r.json()["evidence_failures"]}


def _claim_id(extraction: dict, field: LegalFieldName) -> str:
    return next(c["id"] for c in extraction["claims"] if c["field_name"] == field.value)


async def _approval_count(db: AsyncSession) -> int:
    return (await db.execute(select(func.count()).select_from(ReviewApproval))).scalar_one()


async def _status(db: AsyncSession, extraction_id: str) -> str:
    return (await db.execute(
        text("SELECT status FROM legal_extractions WHERE id = :id"), {"id": extraction_id}
    )).scalar_one()


_TABLES = ("assets", "documents", "document_versions", "document_pages", "legal_extractions",
           "legal_claims", "review_corrections", "review_approvals")


async def _snapshot(db: AsyncSession) -> dict:
    """Every row of every table involved in an approval."""
    return {
        table: [tuple(row) for row in (await db.execute(text(f"SELECT * FROM {table} ORDER BY id"))).all()]
        for table in _TABLES
    }


async def _replace_page_text(db: AsyncSession, version_id: str, new_text: str) -> None:
    await db.execute(
        text("UPDATE document_pages SET text_content = :t WHERE version_id = :v"), {"t": new_text, "v": version_id}
    )
    await db.commit()


def _passport_url(asset: dict) -> str:
    return f"/passport/{asset['blockchain_network']}/{asset['stellar_asset_code']}/{asset['stellar_issuer_public_key']}"


# ── 1. Authentic quotation on the correct page ───────────────────────────────


async def test_authentic_quotations_on_the_cited_pages_are_approved(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    for claim in created_extraction["claims"]:
        assert claim["evidence"]["status"] == ("not_applicable" if claim["is_unknown"] else "verified")
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 201, r.text
    body = r.json()
    assert "evidence_failures" not in body
    assert body["currency"] == {"is_current": True, "reasons": []}
    # The approval binds the verified claims exactly as stored
    approved = {c["claim_id"]: (c["source_page"], c["supporting_passage"]) for c in body["evidence_snapshot"]}
    assert approved == {c["id"]: (c["source_page"], c["supporting_passage"]) for c in created_extraction["claims"]}
    assert await _approval_count(async_db) == 1
    assert await _status(async_db, created_extraction["id"]) == "approved"


async def test_authentic_quotation_on_page_two_is_approved(
    client: AsyncClient, created_document: dict, legal_headers: dict, issuer_headers: dict
):
    ex = await _extract(client, created_document["id"], issuer_headers,
                        known(F.ownership_nature, 2, FEE_SIMPLE_QUOTE, value="Fee simple title held by the Trustee."))
    r = await _approve(client, ex["id"], legal_headers)
    assert r.status_code == 201, r.text
    assert r.json()["approval_type"] == "legal_verification"


# ── 2. Fabricated quotations ─────────────────────────────────────────────────


@pytest.mark.parametrize("quotation", [
    FABRICATED,
    # one word changed
    "Investors shall hold a fractional beneficial ownership interest in the Meridian Property Fund.",
    # case is preserved: a lower-cased copy is not the source text
    OWNERSHIP_QUOTE.lower(),
    # starts or ends inside a word (token boundaries)
    "nvestors shall hold a fractional beneficial ownership interest",
    "Distributions are paid within thirty days after the end of each calendar quart",
    # two sentences that are not adjacent in the source, stitched together
    "Investors shall hold a fractional beneficial ownership interest. Net rental income",
], ids=["invented", "word_changed", "lower_cased", "starts_mid_word", "ends_mid_word", "stitched"])
async def test_fabricated_quotation_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, quotation: str
):
    page = 2 if quotation.startswith("Distributions") else 1
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.ownership_nature, page, quotation))
    r = await _approve(client, ex["id"], issuer_headers)
    assert _failures(r) == {"ownership_nature": "not_found"}
    assert await _approval_count(async_db) == 0
    assert await _status(async_db, ex["id"]) == "draft"


async def test_quotation_spanning_a_page_break_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict
):
    spanning = "State of New York, United States of America. The Trustee holds fee simple title"
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.jurisdiction, 1, spanning))
    assert _failures(await _approve(client, ex["id"], issuer_headers)) == {"jurisdiction": "not_found"}


# ── 3. Wrong page reference ──────────────────────────────────────────────────


@pytest.mark.parametrize("page,quotation", [(2, JURISDICTION_QUOTE), (1, FEE_SIMPLE_QUOTE)])
async def test_quotation_on_another_page_than_cited_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, page, quotation
):
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.jurisdiction, page, quotation))
    assert _failures(await _approve(client, ex["id"], issuer_headers)) == {"jurisdiction": "found_on_other_page"}
    assert await _approval_count(async_db) == 0


# ── 4. Impossible pages ──────────────────────────────────────────────────────


@pytest.mark.parametrize("page,expected", [
    (999, "page_out_of_range"), (3, "page_out_of_range"), (0, "page_out_of_range"),
    (-3, "page_out_of_range"), (None, "page_missing"),
])
async def test_impossible_or_missing_page_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, legal_headers: dict,
    async_db: AsyncSession, page, expected: str,
):
    pages = (await client.get(f"/documents/{created_document['id']}/text", headers=issuer_headers)).json()["pages"]
    assert len(pages) == 2  # a two-page PDF
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.jurisdiction, page, JURISDICTION_QUOTE))
    for headers in (issuer_headers, legal_headers):
        assert _failures(await _approve(client, ex["id"], headers)) == {"jurisdiction": expected}
    assert await _approval_count(async_db) == 0


# ── 5. Too-short quotations ──────────────────────────────────────────────────


@pytest.mark.parametrize("quotation", [
    "x",                     # a single character
    "State of New York",     # authentic, but 17 characters
    "accredited investors",  # authentic, 20 characters but 2 words
])
async def test_too_short_quotation_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, quotation: str
):
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.transfer_restrictions, 1, quotation))
    assert _failures(await _approve(client, ex["id"], issuer_headers)) == {"transfer_restrictions": "quotation_too_short"}


# ── 6. Missing quotations ────────────────────────────────────────────────────


@pytest.mark.parametrize("passage", [None, "", "   \n\t ", "​­"],
                         ids=["no_source", "empty", "whitespace", "invisible_only"])
async def test_missing_quotation_is_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, passage
):
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.income_rights, 1, passage))
    r = await _approve(client, ex["id"], issuer_headers)
    assert _failures(r) == {"income_rights": "quotation_missing"}
    assert r.json()["detail"] == REJECTION_PREFIX + "income_rights" + REJECTION_SUFFIX


# ── 7. Blank, missing or invalid page text ───────────────────────────────────


async def test_blank_pdf_is_never_approved(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession,
):
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)  # the default mock AI quotations
    for headers in (issuer_headers, legal_headers):
        assert _failures(await _approve(client, ex["id"], headers)) == {
            "income_rights": "no_text_layer", "jurisdiction": "no_text_layer",
            "ownership_nature": "no_text_layer", "transfer_restrictions": "no_text_layer",
        }
    assert await _approval_count(async_db) == 0


async def test_cited_page_without_text_is_rejected(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], make_text_pdf([list(MOCK_SOURCE_PAGES[0]), []]), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers,
                        known(F.jurisdiction, 1, JURISDICTION_QUOTE), known(F.ownership_nature, 2, FABRICATED))
    assert _failures(await _approve(client, ex["id"], issuer_headers)) == {"ownership_nature": "no_text_layer"}


async def test_missing_page_records_are_never_approved(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession,
):
    await async_db.execute(text("DELETE FROM document_pages WHERE version_id = :v"),
                           {"v": created_document["current_version"]["id"]})
    await async_db.commit()
    r = await _approve(client, created_extraction["id"], issuer_headers)
    # Since step 5 the missing records are caught first, by the check against the stored PDF
    # (the gate itself fails closed as well: test_gate_function_fails_closed_without_page_text)
    assert r.status_code == 409 and r.json()["source_integrity_failure"] == "page_count_mismatch"
    assert await _approval_count(async_db) == 0


async def test_invalid_page_records_are_never_approved(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession,
):
    await async_db.execute(text("UPDATE document_pages SET page_number = 1 WHERE version_id = :v"),
                           {"v": created_document["current_version"]["id"]})
    await async_db.commit()
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 409 and r.json()["source_integrity_failure"] == "page_numbering_mismatch"
    assert await _approval_count(async_db) == 0


# ── 8. Unknown fields ────────────────────────────────────────────────────────


async def test_unknown_fields_are_approved_as_unknown_and_never_promoted(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict,
):
    unknown_fields = {c["field_name"] for c in created_extraction["claims"] if c["is_unknown"]}
    assert unknown_fields == {"insolvency_provisions", "redemption_rights", "voting_rights"}
    for headers in (issuer_headers, legal_headers):
        r = await _approve(client, created_extraction["id"], headers)
        assert r.status_code == 201, r.text
    snapshot = {c["field_name"]: c for c in r.json()["evidence_snapshot"]}
    for name in unknown_fields:
        assert snapshot[name]["is_unknown"] is True and snapshot[name]["value"] == "unknown"
        assert snapshot[name]["source_page"] is None and snapshot[name]["supporting_passage"] is None

    ex = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    assert {c["field_name"] for c in ex["claims"] if c["evidence"]["status"] == "not_applicable"} == unknown_fields
    rights = {e["field_name"]: e for e in (await client.get(_passport_url(created_asset), headers=inst_headers)).json()["legal_rights"]}
    for name in unknown_fields:
        entry = rights[name]
        assert entry["is_unknown"] is True and entry["value"] == "unknown"
        assert entry["verified"] is False and entry["independently_verified"] is False
        assert entry["source_page"] is None and entry["supporting_passage"] is None


async def test_unknown_claim_with_leftover_evidence_does_not_block_and_is_not_promoted(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    """An unknown claim states nothing: whatever page or quotation it carries is not evidence."""
    await async_db.execute(
        text("UPDATE legal_claims SET source_page = 999, supporting_passage = :p "
             "WHERE extraction_id = :e AND field_name = 'voting_rights'"),
        {"p": FABRICATED, "e": created_extraction["id"]},
    )
    await async_db.commit()
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 201, r.text
    entry = next(e for e in (await client.get(_passport_url(created_asset), headers=inst_headers)).json()["legal_rights"]
                 if e["field_name"] == "voting_rights")
    assert entry["is_unknown"] is True and entry["verified"] is False and entry["supporting_passage"] is None
    assert all(s["supporting_passage"] is None and s["source_page"] is None for s in entry["sources"])


async def test_promoting_an_unknown_claim_without_evidence_is_rejected(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession
):
    # Since step 7 the correction itself is refused (a value change needs its own evidence)
    r = await client.patch(
        f"/extractions/{created_extraction['id']}/claims/{_claim_id(created_extraction, F.voting_rights)}",
        headers=issuer_headers, json={"corrected_value": "One vote per token.", "is_unknown": False,
                                      "expected_claim": as_read(created_extraction, F.voting_rights)})
    assert r.status_code == 422 and r.json()["correction_rule"] == "evidence_required"
    # The gate still refuses such a claim if it reaches the database another way
    await async_db.execute(
        text("UPDATE legal_claims SET is_unknown = 0, value = 'One vote per token.' "
             "WHERE extraction_id = :e AND field_name = 'voting_rights'"), {"e": created_extraction["id"]})
    await async_db.commit()
    assert _failures(await _approve(client, created_extraction["id"], issuer_headers)) == {
        "voting_rights": "quotation_missing"
    }


# ── 9. Mixed valid and invalid claims ────────────────────────────────────────


async def test_one_invalid_claim_rejects_the_whole_approval(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    ex = await _extract(
        client, created_document["id"], issuer_headers,
        known(F.jurisdiction, 1, JURISDICTION_QUOTE),     # valid
        known(F.ownership_nature, 1, FABRICATED),         # fabricated
        known(F.income_rights, 2, INCOME_QUOTE),          # wrong page
        known(F.transfer_restrictions, 1, "x"),           # too short
    )
    before = await _snapshot(async_db)
    r = await _approve(client, ex["id"], issuer_headers)
    assert r.json()["evidence_failures"] == [
        {"claim_id": _claim_id(ex, F.income_rights), "field_name": "income_rights", "status": "found_on_other_page"},
        {"claim_id": _claim_id(ex, F.ownership_nature), "field_name": "ownership_nature", "status": "not_found"},
        {"claim_id": _claim_id(ex, F.transfer_restrictions), "field_name": "transfer_restrictions",
         "status": "quotation_too_short"},
    ]
    assert r.json()["detail"] == (
        REJECTION_PREFIX + "income_rights, ownership_nature, transfer_restrictions" + REJECTION_SUFFIX
    )
    assert await _snapshot(async_db) == before


# ── 10. Reviewer corrections ─────────────────────────────────────────────────


@pytest.mark.parametrize("changes,expected", [
    ({"corrected_passage": FABRICATED}, "not_found"),
    ({"corrected_source_page": 2}, "found_on_other_page"),
    ({"corrected_source_page": 999}, "page_out_of_range"),
    ({"corrected_passage": "x"}, "quotation_too_short"),
    # an authentic page-2 quotation cited on page 1
    ({"corrected_value": "Laws of Delaware.", "corrected_source_page": 1,
      "corrected_passage": "Distributions are paid within thirty days"}, "found_on_other_page"),
], ids=["fabricated_passage", "wrong_page", "page_999", "too_short", "page_and_passage_mismatch"])
async def test_reviewer_correction_with_invalid_evidence_is_rejected(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    async_db: AsyncSession, changes: dict, expected: str,
):
    claim = next(c for c in created_extraction["claims"] if c["field_name"] == "jurisdiction")
    # Since step 7 the correction itself is refused and changes nothing
    r = await client.patch(f"/extractions/{created_extraction['id']}/claims/{claim['id']}", headers=legal_headers,
                           json={"corrected_value": claim["value"], "is_unknown": False,
                                 "expected_claim": as_read(created_extraction, F.jurisdiction), **changes})
    assert r.status_code == 422 and r.json()["evidence_status"] == expected
    assert await _status(async_db, created_extraction["id"]) == "draft"
    # The gate still refuses the same evidence if it reaches the claim another way
    columns = {"corrected_passage": "supporting_passage", "corrected_source_page": "source_page",
               "corrected_value": "value"}
    for key, value in changes.items():
        await async_db.execute(text(f"UPDATE legal_claims SET {columns[key]} = :v WHERE id = :c"),
                               {"v": value, "c": claim["id"]})
    await async_db.commit()
    for headers in (issuer_headers, legal_headers):
        assert _failures(await _approve(client, created_extraction["id"], headers)) == {"jurisdiction": expected}
    assert await _approval_count(async_db) == 0


async def test_reviewer_correction_with_authentic_evidence_is_approved(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict
):
    await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                   corrected_value="Fee simple title held by the Trustee.",
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    r = await _approve(client, created_extraction["id"], legal_headers)
    assert r.status_code == 201, r.text
    approved = next(c for c in r.json()["evidence_snapshot"] if c["field_name"] == "ownership_nature")
    assert (approved["value"], approved["source_page"], approved["supporting_passage"]) == (
        "Fee simple title held by the Trustee.", 2, FEE_SIMPLE_QUOTE)


async def test_a_valid_correction_after_a_rejection_allows_exactly_one_approval(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.ownership_nature, 1, FABRICATED))
    assert _failures(await _approve(client, ex["id"], issuer_headers)) == {"ownership_nature": "not_found"}
    await _correct(client, ex, F.ownership_nature, issuer_headers, corrected_passage=OWNERSHIP_QUOTE)
    assert (await _approve(client, ex["id"], issuer_headers)).status_code == 201
    assert await _approval_count(async_db) == 1


# ── 11. Document version mismatch ────────────────────────────────────────────


async def test_extraction_of_a_superseded_version_is_never_approved(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession,
):
    await _new_version(client, created_document["id"], make_source_pdf("Second version."), issuer_headers)
    for headers in (issuer_headers, legal_headers):
        r = await _approve(client, created_extraction["id"], headers)
        assert r.status_code == 409 and "superseded document version" in r.json()["detail"]
        assert "evidence_failures" not in r.json()
    assert await _approval_count(async_db) == 0


async def test_text_of_another_version_never_verifies_a_claim(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, source_pdf: bytes,
    issuer_headers: dict, async_db: AsyncSession,
):
    # Version 1 contains the quotations, version 2 is blank: the v2 extraction cannot borrow v1's text
    doc = await _upload(client, created_asset["id"], source_pdf, issuer_headers)
    await _new_version(client, doc["id"], minimal_pdf, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    assert set(_failures(await _approve(client, ex["id"], issuer_headers)).values()) == {"no_text_layer"}

    # Version 3 contains them again: an extraction of version 3 is approved, bound to version 3
    v3 = await _new_version(client, doc["id"], make_source_pdf("Third version."), issuer_headers)
    ex3 = await _extract(client, doc["id"], issuer_headers)
    r = await _approve(client, ex3["id"], issuer_headers)
    assert r.status_code == 201 and r.json()["document_version_id"] == v3["id"]
    # The rejected v2 extraction stays unapprovable: v3 is not used for it either
    r = await _approve(client, ex["id"], issuer_headers)
    assert r.status_code == 409
    assert await _approval_count(async_db) == 1


async def test_extraction_pointing_to_another_documents_version_is_never_approved(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, async_db: AsyncSession,
):
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Other")
    await async_db.execute(text("UPDATE legal_extractions SET version_id = :v WHERE id = :e"),
                           {"v": other["current_version"]["id"], "e": created_extraction["id"]})
    await async_db.commit()
    r = await _approve(client, created_extraction["id"], issuer_headers)
    assert r.status_code == 409
    assert await _approval_count(async_db) == 0


def _claim(claim_id: str, field: str, page, passage, is_unknown: bool = False) -> LegalClaim:
    return LegalClaim(id=claim_id, extraction_id="e", field_name=field, value="v", is_unknown=is_unknown,
                      source_page=page, supporting_passage=passage)


_SOURCE = ExtractionSourceText(
    SourceDocumentText.from_page_records("version-1", [
        {"page_number": 1, "text_content": " ".join(MOCK_SOURCE_PAGES[0])},
        {"page_number": 2, "text_content": " ".join(MOCK_SOURCE_PAGES[1])},
    ]),
    1,
)


def test_gate_function_binds_to_the_approved_version_and_orders_failures():
    claims = [
        _claim("c3", "ownership_nature", 1, FABRICATED),
        _claim("c1", "jurisdiction", 1, JURISDICTION_QUOTE),
        _claim("c2", "income_rights", 1, INCOME_QUOTE),
        _claim("c4", "voting_rights", 999, "x", is_unknown=True),
    ]
    assert approval_evidence_failures(claims, "version-1", _SOURCE) == [
        ApprovalEvidenceFailure("c3", "ownership_nature", EvidenceStatus.not_found),
    ]
    # Page text of any other version than the approved one never verifies anything
    failures = approval_evidence_failures(claims, "version-2", _SOURCE)
    assert [(f.claim_id, f.status) for f in failures] == [
        ("c2", EvidenceStatus.document_version_mismatch),
        ("c1", EvidenceStatus.document_version_mismatch),
        ("c3", EvidenceStatus.document_version_mismatch),
    ]
    assert approval_evidence_failures([], "version-2", _SOURCE) == []


def test_gate_function_fails_closed_without_page_text():
    """Missing or invalid page records verify nothing (the approval endpoint catches them even earlier)."""
    claims = [_claim("c1", "jurisdiction", 1, JURISDICTION_QUOTE), _claim("c2", "voting_rights", None, None, True)]
    empty = ExtractionSourceText(SourceDocumentText("version-1", ()), 1)
    assert [(f.claim_id, f.status) for f in approval_evidence_failures(claims, "version-1", empty)] == [
        ("c1", EvidenceStatus.source_text_unavailable)]
    duplicate = ExtractionSourceText(SourceDocumentText.from_page_records("version-1", [
        {"page_number": 1, "text_content": " ".join(MOCK_SOURCE_PAGES[0])},
        {"page_number": 1, "text_content": " ".join(MOCK_SOURCE_PAGES[1])},
    ]), 1)
    assert [(f.claim_id, f.status) for f in approval_evidence_failures(claims, "version-1", duplicate)] == [
        ("c1", EvidenceStatus.source_text_invalid)]


# ── 12. Cross-tenant attempts ────────────────────────────────────────────────


async def test_cross_tenant_approval_is_denied_without_revealing_evidence(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    other_issuer_headers: dict, other_legal_headers: dict, inst_headers: dict,
    async_db: AsyncSession, caplog,
):
    invalid = await _extract(client, created_document["id"], issuer_headers, known(F.ownership_nature, 1, FABRICATED))
    with caplog.at_level(logging.WARNING, logger="rwa_security"):
        for extraction in (created_extraction, invalid):
            for headers in (other_issuer_headers, other_legal_headers):
                r = await _approve(client, extraction["id"], headers)
                assert r.status_code == 404 and r.json() == {"detail": "Extraction not found."}
            assert (await _approve(client, extraction["id"], inst_headers)).status_code == 403
    assert not any("rejected_unverified_evidence" in rec.getMessage() for rec in caplog.records)
    assert await _approval_count(async_db) == 0


async def test_other_tenant_reviewer_gate_applies_in_their_own_scope(
    client: AsyncClient, admin_client: AsyncClient, other_issuer_headers: dict, other_legal_headers: dict,
    minimal_pdf: bytes, source_pdf: bytes,
):
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    blank = await _upload(client, harbor["id"], minimal_pdf, other_issuer_headers)
    ex = await _extract(client, blank["id"], other_issuer_headers)
    assert set(_failures(await _approve(client, ex["id"], other_legal_headers)).values()) == {"no_text_layer"}
    doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Bond terms")
    ex = await _extract(client, doc["id"], other_issuer_headers)
    assert (await _approve(client, ex["id"], other_legal_headers)).status_code == 201


# ── 13. A rejected approval changes nothing ──────────────────────────────────


async def test_rejected_approval_leaves_database_and_history_unchanged(
    client: AsyncClient, created_document: dict, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession,
):
    # A fabricated AI quotation (since step 7 a correction can no longer store one)
    extraction = await _extract(client, created_document["id"], issuer_headers,
                                known(F.income_rights, 1, FABRICATED), known(F.jurisdiction, 1, JURISDICTION_QUOTE))
    before = await _snapshot(async_db)
    history = (await client.get(f"/extractions/{extraction['id']}/approvals", headers=issuer_headers)).json()
    for headers in (issuer_headers, legal_headers):
        assert _failures(await _approve(client, extraction["id"], headers)) == {"income_rights": "not_found"}
    assert await _snapshot(async_db) == before
    assert (await client.get(f"/extractions/{extraction['id']}/approvals", headers=issuer_headers)).json() == history == []
    state = (await client.get(f"/extractions/{extraction['id']}", headers=issuer_headers)).json()
    assert state["status"] == "draft" and state["approval_state"]["state"] == "not_approved"
    document = (await client.get(f"/documents/{created_document['id']}", headers=issuer_headers)).json()
    assert document["current_version_number"] == 1


async def test_rejected_second_approval_keeps_the_existing_approval_untouched(
    client: AsyncClient, created_document: dict, approved_extraction: dict, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession,
):
    eid = approved_extraction["id"]
    history = (await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)).json()
    await _replace_page_text(async_db, created_document["current_version"]["id"], "Replaced text without the quotations.")
    before = await _snapshot(async_db)
    # Since step 5 the changed page text makes the existing approval stale, so the second
    # approval is refused before the gate: a re-review is required
    r = await _approve(client, eid, legal_headers)
    assert r.status_code == 409 and "start a re-review" in r.json()["detail"]
    assert await _snapshot(async_db) == before
    after = (await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)).json()
    assert [{k: v for k, v in h.items() if k != "currency"} for h in after] == [
        {k: v for k, v in h.items() if k != "currency"} for h in history
    ]
    assert await _status(async_db, eid) == "approved"


async def test_evidence_is_recomputed_at_approval_time(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession,
):
    """An earlier 'verified' API response is never trusted: the stored claims are checked again."""
    shown = (await client.get(f"/extractions/{created_extraction['id']}", headers=issuer_headers)).json()
    assert {c["evidence"]["status"] for c in shown["claims"]} == {"verified", "not_applicable"}
    # The stored quotation changes after the response was shown (page-text changes are refused
    # even earlier since step 5, by the check against the stored PDF)
    await async_db.execute(
        text("UPDATE legal_claims SET supporting_passage = :p WHERE extraction_id = :e AND field_name = 'ownership_nature'"),
        {"p": FABRICATED, "e": created_extraction["id"]},
    )
    await async_db.commit()
    r = await _approve(client, created_extraction["id"], issuer_headers,
                       body={"evidence": "verified", "status": "verified", "verified": True, "claims": shown["claims"]})
    assert _failures(r) == {"ownership_nature": "not_found"}
    assert await _approval_count(async_db) == 0


async def test_rejection_response_is_deterministic_and_reveals_no_content(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, caplog
):
    ex = await _extract(client, created_document["id"], issuer_headers,
                        known(F.ownership_nature, 1, FABRICATED), known(F.jurisdiction, 999, JURISDICTION_QUOTE))
    with caplog.at_level(logging.WARNING, logger="rwa_security"):
        first, second = await _approve(client, ex["id"], issuer_headers), await _approve(client, ex["id"], issuer_headers)
    assert first.status_code == second.status_code == 422
    assert first.json() == second.json()
    body = first.json()
    assert set(body) == {"detail", "evidence_failures"}
    assert all(set(f) == {"claim_id", "field_name", "status"} for f in body["evidence_failures"])
    assert {f["status"] for f in body["evidence_failures"]} <= {s.value for s in EvidenceStatus}
    # Neither the quotations nor the page text, versions or verifier explanations are disclosed
    for secret in (FABRICATED, JURISDICTION_QUOTE, MOCK_SOURCE_PAGES[0][0], ex["version_id"],
                   created_document["id"], "does not appear", "page_count", "explanation"):
        assert secret not in first.text
    messages = [rec.getMessage() for rec in caplog.records if "rejected_unverified_evidence" in rec.getMessage()]
    assert len(messages) == 2
    assert "jurisdiction:page_out_of_range,ownership_nature:not_found" in messages[0]
    assert "user=alice-chen-01" in messages[0]
    assert FABRICATED not in messages[0] and JURISDICTION_QUOTE not in messages[0]


# ── 14. Concurrency ──────────────────────────────────────────────────────────


@pytest.fixture
async def file_db(client: AsyncClient, tmp_path: Path):
    """File-based SQLite with a session per request (real concurrency), set after the client's overrides."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'gate.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    yield factory
    await engine.dispose()


def _run_before_lock(monkeypatch, other):
    """
    `other` completes after the approval request started, just before it takes the write lock.
    Runs once: a correction inside `other` takes the same lock without re-entering the hook.
    """
    original = review_router.lock_extraction_for_update
    results: list = []

    async def delayed_lock(db, extraction_id):
        if not results:
            results.append(None)
            results[0] = await other()
        await original(db, extraction_id)

    monkeypatch.setattr(review_router, "lock_extraction_for_update", delayed_lock)
    return results


def _hold_lock(monkeypatch) -> asyncio.Event:
    """The approval keeps the write lock for a moment after loading the state."""
    original = review_router.load_extraction_context
    held = asyncio.Event()

    async def slow_load(db, extraction_id):
        ctx = await original(db, extraction_id)
        if not held.is_set():
            held.set()
            await asyncio.sleep(0.5)
        return ctx

    monkeypatch.setattr(review_router, "load_extraction_context", slow_load)
    return held


async def _bound_statuses(factory, approval_id: str) -> list[EvidenceStatus]:
    """Independent oracle: re-verifies an approval's snapshot against the page text of its bound version."""
    async with factory() as s:
        row = (await s.execute(text("SELECT document_version_id, evidence_snapshot FROM review_approvals WHERE id = :id"),
                               {"id": approval_id})).one()
        pages = (await s.execute(text("SELECT page_number, text_content FROM document_pages WHERE version_id = :v"),
                                 {"v": row.document_version_id})).mappings().all()
    source = SourceDocumentText.from_page_records(row.document_version_id, [dict(p) for p in pages])
    record = json.loads(row.evidence_snapshot)
    claims = record["claims"] if isinstance(record, dict) else record  # format 2 (step 5) or format 1
    return [
        verify_evidence(EvidenceClaim(row.document_version_id, c["source_page"], c["supporting_passage"], c["is_unknown"]), source).status
        for c in claims
    ]


async def _approval_rows(factory, extraction_id: str) -> list:
    async with factory() as s:
        return (await s.execute(text("SELECT id FROM review_approvals WHERE extraction_id = :e"),
                                {"e": extraction_id})).scalars().all()


async def test_correction_committed_just_before_the_lock_is_what_the_gate_checks(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
    monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    claim = _claim_id(ex, F.ownership_nature)

    # A claim change committed just before the lock. Since step 7 a correction cannot store a
    # fabricated quotation, so another writer stands in for it; the gate sees the committed state.
    async def change_claim():
        async with file_db() as s:
            await s.execute(text("UPDATE legal_claims SET supporting_passage = :p WHERE id = :c"),
                            {"p": FABRICATED, "c": claim})
            await s.commit()
        return "changed"

    results = _run_before_lock(monkeypatch, change_claim)
    r = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert results == ["changed"]
    assert _failures(r) == {"ownership_nature": "not_found"}
    assert await _approval_rows(file_db, ex["id"]) == []


async def test_correction_waiting_for_the_lock_cannot_change_what_was_approved(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    held = _hold_lock(monkeypatch)

    async def correct_when_locked():
        await held.wait()
        return await client.patch(
            f"/extractions/{ex['id']}/claims/{_claim_id(ex, F.ownership_nature)}", headers=legal_headers,
            json={"corrected_value": "Changed", "is_unknown": False, "corrected_passage": FABRICATED,
                  "expected_claim": as_read(ex, F.ownership_nature)})

    approval, correction = await asyncio.wait_for(
        asyncio.gather(_approve(client, ex["id"], issuer_headers), correct_when_locked()), 30)
    assert approval.status_code == 201, approval.text
    assert correction.status_code == 409
    assert set(await _bound_statuses(file_db, approval.json()["id"])) == {
        EvidenceStatus.verified, EvidenceStatus.not_applicable}
    claims = (await client.get(f"/extractions/{ex['id']}", headers=issuer_headers)).json()["claims"]
    assert FABRICATED not in {c["supporting_passage"] for c in claims}


@pytest.mark.parametrize("new_text", [
    "Replaced text without the quotations.",
    # keeps every quotation, so the evidence gate alone would accept it
    " ".join(MOCK_SOURCE_PAGES[0]) + " Inserted after the upload.",
], ids=["quotations_removed", "quotations_kept"])
async def test_page_text_change_committed_just_before_the_lock_is_seen_under_the_lock(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, monkeypatch, new_text: str,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)

    async def replace_page_text():
        async with file_db() as s:
            await s.execute(text("UPDATE document_pages SET text_content = :t WHERE version_id = :v AND page_number = 1"),
                            {"t": new_text, "v": doc["current_version"]["id"]})
            await s.commit()
        return "replaced"

    results = _run_before_lock(monkeypatch, replace_page_text)
    r = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert results == ["replaced"]
    # The stored PDF was read before the lock; the page records are compared with it under the lock
    assert r.status_code == 409 and r.json()["source_integrity_failure"] == "page_text_mismatch"
    assert await _approval_rows(file_db, ex["id"]) == []


async def test_upload_committed_just_before_the_lock_still_wins_over_the_gate(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, monkeypatch,
):
    """9B-3 ordering is kept: a superseded version is refused (409) before evidence is considered."""
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.ownership_nature, 1, FABRICATED))
    results = _run_before_lock(monkeypatch, lambda: client.post(
        f"/documents/{doc['id']}/versions", headers=issuer_headers,
        files={"file": ("v2.pdf", make_source_pdf("v2"), "application/pdf")}))
    r = await asyncio.wait_for(_approve(client, ex["id"], issuer_headers), 30)
    assert [x.status_code for x in results] == [201]
    assert r.status_code == 409 and "superseded document version" in r.json()["detail"]
    assert await _approval_rows(file_db, ex["id"]) == []


async def test_random_races_never_create_an_approval_without_verified_bound_evidence(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    outcomes = []
    for round_number in range(6):
        valid = round_number % 2 == 0
        claims = () if valid else (known(F.ownership_nature, 1, FABRICATED), known(F.jurisdiction, 1, JURISDICTION_QUOTE))
        ex = await _extract(client, doc["id"], issuer_headers, *claims)

        async def upload_after(delay: float, label: str):
            # Staggered starts, so that both the upload-first and the approval-first orders occur
            await asyncio.sleep(delay)
            return await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                                     files={"file": ("v.pdf", make_source_pdf(label), "application/pdf")})

        upload, issuer_approval, legal_approval = await asyncio.wait_for(asyncio.gather(
            upload_after((0.0, 0.0, 0.3, 0.3, 0.05, 0.15)[round_number], f"race {round_number}"),
            _approve(client, ex["id"], issuer_headers),
            _approve(client, ex["id"], legal_headers),
        ), 60)
        codes = (upload.status_code, issuer_approval.status_code, legal_approval.status_code)
        assert upload.status_code in (201, 409, 503), upload.text
        rows = await _approval_rows(file_db, ex["id"])
        created = [r for r in (issuer_approval, legal_approval) if r.status_code == 201]
        assert sorted(rows) == sorted(r.json()["id"] for r in created)
        for r in (issuer_approval, legal_approval):
            if valid:
                assert r.status_code in (201, 409, 503), r.text
            else:
                assert r.status_code in (422, 409, 503), r.text
                if r.status_code == 422:
                    assert _failures(r) == {"ownership_nature": "not_found"}
            if r.status_code == 409:
                # Only the upload that committed first can refuse it; the source itself is intact
                assert "superseded document version" in r.json()["detail"], r.text
        for approval_id in rows:
            statuses = await _bound_statuses(file_db, approval_id)
            assert set(statuses) <= {EvidenceStatus.verified, EvidenceStatus.not_applicable}
        outcomes.append(codes)
    print(f"gate race outcomes: {outcomes}")


# ── 15. Valid approval workflows ─────────────────────────────────────────────


async def test_valid_workflows_keep_working_with_the_gate(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_document: dict,
    created_extraction: dict, issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    eid = created_extraction["id"]
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                   corrected_value="Fee simple title held by the Trustee.",
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    assert (await _approve(client, eid, legal_headers)).status_code == 201
    assert (await _approve(client, eid, issuer_headers)).status_code == 201
    body = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    assert body["source_documents"][0]["approval_types"] == ["issuer_attestation", "legal_verification"]

    # Material asset change → stale → re-review → a new approval passes the gate again
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    assert (await _approve(client, eid, issuer_headers)).status_code == 201
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200

    # A new version with source text: its own extraction is approved and governs the passport
    v2 = await _new_version(client, created_document["id"], make_source_pdf("Amendment 1."), issuer_headers)
    ex2 = await _extract(client, created_document["id"], issuer_headers)
    r = await _approve(client, ex2["id"], legal_headers)
    assert r.status_code == 201 and r.json()["document_version_id"] == v2["id"]
    body = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    assert body["source_document"]["version_number"] == 2


# ── 16. Every approval path goes through the same gate ───────────────────────


async def test_re_review_path_is_gated(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, approved_extraction: dict,
    issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
):
    eid = approved_extraction["id"]
    await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
    assert (await client.post(f"/extractions/{eid}/review", headers=legal_headers)).status_code == 200
    # Since step 7 a correction with a fabricated quotation is refused outright...
    claim_id = _claim_id(approved_extraction, F.transfer_restrictions)
    r = await client.patch(f"/extractions/{eid}/claims/{claim_id}", headers=legal_headers,
                           json={"corrected_value": next(c["value"] for c in approved_extraction["claims"]
                                                         if c["id"] == claim_id),
                                 "is_unknown": False, "corrected_passage": FABRICATED,
                                 "expected_claim": as_read(approved_extraction, F.transfer_restrictions)})
    assert r.status_code == 422 and r.json()["evidence_status"] == "not_found"
    # ...and if such a quotation reaches the claim another way, the re-review path is still gated
    await async_db.execute(text("UPDATE legal_claims SET supporting_passage = :p WHERE id = :c"),
                           {"p": FABRICATED, "c": claim_id})
    await async_db.commit()
    for headers in (issuer_headers, legal_headers):
        assert _failures(await _approve(client, eid, headers)) == {"transfer_restrictions": "not_found"}
    assert await _approval_count(async_db) == 1  # only the original, now stale, approval


async def test_legacy_unbound_approval_cannot_be_renewed_without_verified_evidence(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers)
    now = datetime.now(timezone.utc)
    async_db.add(ReviewApproval(
        id="legacy-approval", extraction_id=ex["id"], reviewer_id="alice-chen-01", reviewer_role="issuer_reviewer",
        approval_type="issuer_attestation", disclaimer="Legacy approval.", approved_at=now, created_at=now,
    ))
    await async_db.execute(text("UPDATE legal_extractions SET status = 'approved' WHERE id = :id"), {"id": ex["id"]})
    await async_db.commit()

    assert (await client.post(f"/extractions/{ex['id']}/review", headers=legal_headers)).status_code == 200
    assert set(_failures(await _approve(client, ex["id"], legal_headers)).values()) == {"no_text_layer"}
    history = (await client.get(f"/extractions/{ex['id']}/approvals", headers=issuer_headers)).json()
    assert [(h["id"], h["currency"]["is_current"]) for h in history] == [("legacy-approval", False)]
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409


def test_only_the_approval_endpoint_creates_approvals_and_it_runs_the_gate_first():
    """Static inventory: no other code path can create an approval or mark an extraction approved."""
    app_dir = Path(__file__).resolve().parents[1] / "app"
    creators, approved_writes = set(), set()
    for path in app_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in (n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))):
            for node in ast.walk(fn):
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) in ("ReviewApproval", "bind_approval"):
                    creators.add((path.name, fn.name, node.func.id))
                if (isinstance(node, ast.Assign)
                        and any(isinstance(t, ast.Attribute) and t.attr == "status" for t in node.targets)
                        and "approved" in ast.unparse(node.value)):
                    approved_writes.add((path.name, fn.name))
        assert "INSERT INTO review_approvals" not in path.read_text(encoding="utf-8")
    assert creators == {("review.py", "approve_extraction", "ReviewApproval"),
                        ("review.py", "approve_extraction", "bind_approval")}
    assert approved_writes == {("review.py", "approve_extraction")}

    source = inspect.getsource(review_router.approve_extraction)
    assert source.index("approval_evidence_failures(") < source.index("ReviewApproval(")
    writes = {(m, r.path) for r in app.routes if isinstance(r, APIRoute)
              for m in r.methods if m != "GET" and "approv" in r.path}
    assert writes == {("POST", "/extractions/{extraction_id}/approvals")}
