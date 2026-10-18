"""
Stage 9B-4 step 6 — institutional passport verification semantics.

The passport keeps four things apart: source quotation verified (deterministic evidence check,
re-computed on every read), reviewer attestation recorded, independent reviewer attestation
recorded, and legal interpretation (never established by this system). An unknown field, a
stale approval or an approval of unknown fields only never produces a positive indicator.

Coverage:
 1. Valid known claim with verified source evidence
 2. Approved extraction containing only unknown fields
 3. Mixed verified and unknown fields
 4. Fabricated evidence in a historical approval
 5. Wrong source page
 6. Stale approval
 7. Modified source-page text
 8. Invalid document version binding and altered stored source files
 9. Legacy format 1 approval with unverifiable evidence
10. Format 2 approval with valid evidence
11. Reviewer-corrected value and value_origin
12. Original AI value versus approved corrected value
13. Independent reviewer approval without verified source evidence
14. No false promotion to independently_verified
15. Response serialization and OpenAPI metadata
16. Backward-compatible response fields
17. Cross-tenant isolation
18. Repeated deterministic evaluation
19. Approval integrity and concurrency behaviour
20. Aggregate verification status
"""

import asyncio
import dataclasses
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.passport as passport_router
from app.approval_integrity import load_extraction_context, summarize_extraction
from app.enums import LegalFieldName
from app.main import app
from app.models.extraction import LegalClaim
from app.services.ai import get_ai_service
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET
from tests.mock_ai import MOCK_SOURCE_PAGES, MockAIService
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    FABRICATED,
    FEE_SIMPLE_QUOTE,
    JURISDICTION_QUOTE,
    ScriptedAI,
    _approve,
    _correct,
    _extract,
    _new_version,
    _passport_url,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
    known,
)
from tests.test_evidence_binding import _bind_format_1, _exec, _stored_file

F = LegalFieldName

LEGACY_TOP_KEYS = {"asset", "verification_level", "legal_rights", "approvals", "source_document",
                   "source_documents", "excluded_documents", "extraction_metadata", "disclaimer", "retrieved_at"}
LEGACY_ENTRY_KEYS = {"field_name", "value", "is_unknown", "verified", "independently_verified", "conflict",
                     "source_page", "supporting_passage", "sources"}
LEGACY_SOURCE_KEYS = {"document_id", "document_title", "version_number", "value", "is_unknown", "source_page",
                      "supporting_passage", "independently_verified"}
LEGACY_DOCUMENT_KEYS = {"document_id", "title", "document_type", "version_number", "approval_types",
                        "independently_verified"}
NEW_ENTRY_KEYS = {"evidence_status", "verification_method", "document_id", "document_version_number",
                  "value_origin", "reviewer_attestation_status"}
NEW_SOURCE_KEYS = {"evidence_status", "value_origin", "reviewer_attestation_status"}
NEW_DOCUMENT_KEYS = {"source_verified_claim_count", "unknown_claim_count"}
SUMMARY_KEYS = {"source_verified_field_count", "independently_attested_field_count", "unverified_field_count",
                "unknown_field_count", "conflicting_field_count", "reviewer_attestation_recorded",
                "independent_reviewer_attestation_recorded", "evidence_scope", "verification_method",
                "normalization_version", "legal_interpretation_status", "meaning"}
# Stage 9B-5B, step 1: additive fields (tests/test_passport_verification_status.py)
STEP_9B5B1_TOP_KEYS = {"verification_status"}
STEP_9B5B1_SUMMARY_KEYS = {"issuer_attestation_recorded", "legal_reviewer_approval_recorded"}
LEGACY_LEVELS ={"issuer_attested", "partially_independently_verified", "independently_verified"}
MOCK_KNOWN = {"income_rights", "jurisdiction", "ownership_nature", "transfer_restrictions"}
MOCK_UNKNOWN = {"insolvency_provisions", "redemption_rights", "voting_rights"}
DISTRIBUTIONS_QUOTE = "Distributions are paid within thirty days after the end of each calendar quarter"


async def _passport(client: AsyncClient, asset: dict, headers: dict, expected: int = 200):
    r = await client.get(_passport_url(asset), headers=headers)
    assert r.status_code == expected, r.text
    return r.json()


def _rights(body: dict) -> dict[str, dict]:
    return {e["field_name"]: e for e in body["legal_rights"]}


async def _extract_exactly(client: AsyncClient, document_id: str, headers: dict, *claims) -> dict:
    """The AI returns exactly these claims (none at all: every field is unknown)."""
    app.dependency_overrides[get_ai_service] = lambda: ScriptedAI(*claims)
    try:
        r = await client.post(f"/documents/{document_id}/extractions", headers=headers)
    finally:
        app.dependency_overrides[get_ai_service] = lambda: MockAIService()
    assert r.status_code == 201, r.text
    return r.json()


async def _approved(client: AsyncClient, extraction: dict, *headers: dict) -> None:
    for h in headers:
        r = await _approve(client, extraction["id"], h)
        assert r.status_code == 201, r.text


def _no_positive_indicator(entry: dict) -> None:
    assert entry["verified"] is False and entry["independently_verified"] is False
    assert entry["evidence_status"] in ("not_applicable", "not_found", "found_on_other_page", "page_out_of_range",
                                        "page_missing", "no_text_layer", "quotation_missing", "quotation_too_short",
                                        "document_version_mismatch", "source_text_unavailable", "source_text_invalid")
    assert entry["supporting_passage"] is None and entry["source_page"] is None
    assert entry["document_id"] is None and entry["document_version_number"] is None
    assert all(s["independently_verified"] is False for s in entry["sources"])


# ── 1, 10. Verified known claims (format 2) ──────────────────────────────────


async def test_verified_known_claim_is_derived_from_live_source_evidence(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict, inst_headers: dict
):
    body = await _passport(client, created_asset, inst_headers)
    jurisdiction = _rights(body)["jurisdiction"]
    assert jurisdiction["verified"] is True and jurisdiction["evidence_status"] == "verified"
    assert jurisdiction["verification_method"] == "exact_substring_token_boundary"
    assert (jurisdiction["source_page"], jurisdiction["document_id"], jurisdiction["document_version_number"]) == (
        1, created_document["id"], 1)
    assert jurisdiction["supporting_passage"] == MOCK_SOURCE_PAGES[0][4]
    assert jurisdiction["value_origin"] == "ai_extraction"
    # Issuer attestation only: source-verified, but never independently verified
    assert jurisdiction["reviewer_attestation_status"] == "issuer_attested"
    assert jurisdiction["independently_verified"] is False
    (source,) = jurisdiction["sources"]
    assert (source["evidence_status"], source["reviewer_attestation_status"], source["independently_verified"]) == (
        "verified", "issuer_attested", False)
    assert body["verification_level"] == "issuer_attested"


async def test_format_2_approval_with_valid_evidence_by_an_independent_reviewer(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, inst_headers: dict
):
    await _approved(client, created_extraction, legal_headers)
    approval = (await client.get(f"/extractions/{created_extraction['id']}/approvals", headers=legal_headers)).json()[0]
    assert approval["evidence_snapshot_format"] == 2
    body = await _passport(client, created_asset, inst_headers)
    for name in MOCK_KNOWN:
        entry = _rights(body)[name]
        assert (entry["verified"], entry["independently_verified"], entry["reviewer_attestation_status"]) == (
            True, True, "independent_reviewer_attested")
    assert body["verification_level"] == "independently_verified"


# ── 2, 13, 14. Unknown fields only ───────────────────────────────────────────


@pytest.mark.parametrize("approvers", [("legal",), ("issuer", "legal"), ("issuer",)])
async def test_approval_of_unknown_fields_only_verifies_nothing(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, approvers: tuple,
):
    ex = await _extract_exactly(client, created_document["id"], issuer_headers)
    assert all(c["is_unknown"] for c in ex["claims"])
    await _approved(client, ex, *({"issuer": issuer_headers, "legal": legal_headers}[a] for a in approvers))

    body = await _passport(client, created_asset, inst_headers)
    assert len(body["legal_rights"]) == 7
    for entry in body["legal_rights"]:
        assert entry["is_unknown"] is True and entry["value"] == "unknown"
        _no_positive_indicator(entry)
        assert entry["evidence_status"] == "not_applicable"
        assert entry["reviewer_attestation_status"] == "not_applicable" and entry["value_origin"] is None
        assert all(s["evidence_status"] == "not_applicable" and s["reviewer_attestation_status"] == "not_applicable"
                   for s in entry["sources"])
    # Even an independent reviewer's approval does not lift a passport without verified fields
    assert body["verification_level"] == "issuer_attested"
    (document,) = body["source_documents"]
    assert document["independently_verified"] is False
    assert (document["source_verified_claim_count"], document["unknown_claim_count"]) == (0, 7)
    summary = body["verification_summary"]
    assert (summary["source_verified_field_count"], summary["independently_attested_field_count"],
            summary["unknown_field_count"]) == (0, 0, 7)
    assert summary["independent_reviewer_attestation_recorded"] is ("legal" in approvers)
    assert summary["legal_interpretation_status"] == "not_independently_established"


# ── 3. Mixed verified and unknown fields ─────────────────────────────────────


async def test_mixed_verified_and_unknown_fields(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, inst_headers: dict
):
    await _approved(client, created_extraction, legal_headers)
    body = await _passport(client, created_asset, inst_headers)
    rights = _rights(body)
    for name in MOCK_KNOWN:
        assert rights[name]["verified"] is True and rights[name]["independently_verified"] is True
    for name in MOCK_UNKNOWN:
        _no_positive_indicator(rights[name])
        assert rights[name]["reviewer_attestation_status"] == "not_applicable"
    (document,) = body["source_documents"]
    assert (document["independently_verified"], document["source_verified_claim_count"],
            document["unknown_claim_count"]) == (True, 4, 3)
    summary = body["verification_summary"]
    assert {k: summary[k] for k in ("source_verified_field_count", "independently_attested_field_count",
                                    "unverified_field_count", "unknown_field_count", "conflicting_field_count")} == {
        "source_verified_field_count": 4, "independently_attested_field_count": 4, "unverified_field_count": 0,
        "unknown_field_count": 3, "conflicting_field_count": 0}
    assert body["verification_level"] == "independently_verified"


# ── 4, 5, 9. Historical approvals of unverifiable evidence ───────────────────


async def test_historical_approval_of_fabricated_evidence_is_never_served_as_verified(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, source_pdf: bytes, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    blank = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers, title="Scanned")
    fabricated = await _extract(client, blank["id"], issuer_headers)  # the mock AI quotations are not in it
    await _bind_format_1(async_db, fabricated["id"])                   # approved the pre-step-4 way
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409  # the only approval is not current: nothing is served

    valid = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Prospectus")
    await _approved(client, await _extract(client, valid["id"], issuer_headers), issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert [d["title"] for d in body["source_documents"]] == ["Prospectus"]
    assert body["excluded_documents"] == [{"document_id": blank["id"], "title": "Scanned",
                                           "document_type": "prospectus", "reasons": ["evidence_not_verified"]}]
    assert all(s["document_id"] == valid["id"] for e in body["legal_rights"] for s in e["sources"])


async def test_historical_approval_with_a_wrong_source_page_is_never_served(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    ex = await _extract(client, created_document["id"], issuer_headers, known(F.jurisdiction, 2, JURISDICTION_QUOTE))
    await _bind_format_1(async_db, ex["id"])  # the step 4 gate would have refused it
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409
    history = (await client.get(f"/extractions/{ex['id']}/approvals", headers=issuer_headers)).json()
    assert history[0]["currency"]["reasons"] == ["evidence_not_verified"]


# ── Defence in depth: the passport re-verifies even a current approval ───────


async def _sources_with(db: AsyncSession, extraction_id: str, **changes_by_field) -> list:
    """The real current approval, with claims altered in memory only (a state the API cannot produce)."""
    ctx = await load_extraction_context(db, extraction_id)
    summary = summarize_extraction(ctx)
    assert summary.current_approvals
    claims = []
    for c in ctx.claims:
        changes = changes_by_field.get(c.field_name, {})
        claims.append(LegalClaim(
            id=c.id, extraction_id=c.extraction_id, field_name=c.field_name,
            value=changes.get("value", c.value), is_unknown=changes.get("is_unknown", c.is_unknown),
            source_page=changes.get("source_page", c.source_page),
            supporting_passage=changes.get("supporting_passage", c.supporting_passage),
        ))
    return [passport_router._Source(dataclasses.replace(ctx, claims=claims), summary)]


@pytest.mark.parametrize("changes,expected", [
    ({"supporting_passage": FABRICATED}, "not_found"),
    ({"source_page": 2}, "found_on_other_page"),
    ({"source_page": 999}, "page_out_of_range"),
    ({"supporting_passage": "x"}, "quotation_too_short"),
    ({"supporting_passage": None}, "quotation_missing"),
], ids=["fabricated", "wrong_page", "page_999", "too_short", "missing"])
async def test_unverified_evidence_is_never_reported_as_verified_even_under_a_current_approval(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, changes, expected
):
    await _approved(client, created_extraction, legal_headers)
    sources = await _sources_with(async_db, created_extraction["id"], ownership_nature=changes)
    entries = {e.field_name: e for e in passport_router._legal_rights(sources)}
    ownership = entries["ownership_nature"].model_dump(mode="json")
    _no_positive_indicator(ownership)
    assert ownership["evidence_status"] == expected
    assert ownership["value"] != "unknown"  # shown, but never as verified
    # The independent reviewer's approval is recorded as an attestation, not as verification
    assert ownership["reviewer_attestation_status"] == "independent_reviewer_attested"
    assert ownership["sources"][0]["evidence_status"] == expected
    assert passport_router._approved_source(sources[0]).source_verified_claim_count == 3
    summary = passport_router._verification_summary(list(entries.values()), sources)
    assert (summary.source_verified_field_count, summary.unverified_field_count) == (3, 1)


async def test_an_independent_approval_of_only_unverified_known_fields_is_not_independent_verification(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession
):
    await _approved(client, created_extraction, legal_headers)
    changes = {name: {"supporting_passage": FABRICATED} for name in MOCK_KNOWN}
    sources = await _sources_with(async_db, created_extraction["id"], **changes)
    entries = passport_router._legal_rights(sources)
    assert not any(e.verified or e.independently_verified for e in entries)
    assert passport_router._verification_level(entries).value == "issuer_attested"
    assert passport_router._approved_source(sources[0]).independently_verified is False


# ── 6, 7, 8. Stale approvals, modified text, version binding, source files ───


async def _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers, legal_headers):
    """The standard document (legal approval) and a second one stating a redemption right (issuer)."""
    await _approved(client, created_extraction, legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Legal Opinion")
    ex = await _extract_exactly(client, other["id"], issuer_headers,
                                known(F.redemption_rights, 2, DISTRIBUTIONS_QUOTE, value="Quarterly redemption window."))
    await _approved(client, ex, issuer_headers)
    return other


async def test_stale_or_superseded_document_contributes_nothing(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers, legal_headers)
    assert _rights(await _passport(client, created_asset, inst_headers))["redemption_rights"]["verified"] is True
    await _new_version(client, other["id"], make_source_pdf("Opinion v2."), issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [created_document["id"]]
    assert body["excluded_documents"][0]["reasons"] == ["document_version_superseded"]
    redemption = _rights(body)["redemption_rights"]
    assert redemption["is_unknown"] is True
    _no_positive_indicator(redemption)
    assert body["verification_level"] == "independently_verified"  # every remaining verified field


async def test_modified_source_page_text_removes_the_document_from_the_passport(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers, legal_headers)
    await _exec(async_db, "UPDATE document_pages SET text_content = text_content || ' x' WHERE version_id = :v",
                v=created_document["current_version"]["id"])
    body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [other["id"]]
    assert body["excluded_documents"][0]["reasons"] == ["source_text_changed"]
    rights = _rights(body)
    for name in MOCK_KNOWN:
        assert rights[name]["is_unknown"] is True  # the tampered document no longer supports them
        _no_positive_indicator(rights[name])
    assert rights["redemption_rights"]["verified"] is True
    assert body["verification_level"] == "issuer_attested"


async def test_invalid_document_version_binding_is_never_served(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    v2 = await _new_version(client, created_document["id"], make_source_pdf("Amendment."), issuer_headers)
    await _exec(async_db, "UPDATE legal_extractions SET version_id = :v WHERE id = :e",
                v=v2["id"], e=approved_extraction["id"])
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409


@pytest.mark.parametrize("case", ["bytes_changed", "file_deleted"])
async def test_altered_stored_source_file_is_detected_on_passport_reads(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession, tmp_path: Path, caplog,
    case: str,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers, legal_headers)
    path = await _stored_file(async_db, tmp_path, created_document["current_version"]["id"])
    original = path.read_bytes()
    if case == "bytes_changed":
        path.write_bytes(make_source_pdf("Altered on disk after approval."))
    else:
        path.unlink()
    with caplog.at_level("ERROR", logger="rwa_security"):
        body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [other["id"]]
    assert body["excluded_documents"] == [{"document_id": created_document["id"], "title": "Q4 2024 Prospectus",
                                           "document_type": "prospectus", "reasons": ["source_file_integrity_failed"]}]
    assert any("passport.source_file_integrity_failed" in rec.getMessage() for rec in caplog.records)
    assert str(tmp_path) not in caplog.text
    # The approval record itself is untouched; the restored file restores the document
    path.write_bytes(original)
    body = await _passport(client, created_asset, inst_headers)
    assert {d["document_id"] for d in body["source_documents"]} == {created_document["id"], other["id"]}


async def test_altered_source_file_of_the_only_document_gives_409(
    client: AsyncClient, created_asset: dict, created_document: dict, approved_extraction: dict, inst_headers: dict,
    async_db: AsyncSession, tmp_path: Path,
):
    (await _stored_file(async_db, tmp_path, created_document["current_version"]["id"])).write_bytes(b"%PDF-1.4 x %%EOF")
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409
    assert r.json() == {"detail": "The legal approval for this asset is no longer current; a re-review is required."}


# ── 11, 12. Reviewer corrections and value origin ────────────────────────────


async def test_reviewer_corrected_value_is_marked_and_the_ai_original_is_never_shown(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    second_legal_headers: dict, inst_headers: dict,
):
    ai_value = next(c["value"] for c in created_extraction["claims"] if c["field_name"] == "ownership_nature")
    await _correct(client, created_extraction, F.ownership_nature, legal_headers,
                   corrected_value="Fee simple title held by the Trustee.",
                   corrected_source_page=2, corrected_passage=FEE_SIMPLE_QUOTE)
    await _approved(client, created_extraction, second_legal_headers)  # not the corrector (Stage 9B-5C)

    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    body = r.json()
    ownership = _rights(body)["ownership_nature"]
    assert ownership["value"] == "Fee simple title held by the Trustee."
    assert (ownership["value_origin"], ownership["sources"][0]["value_origin"]) == ("reviewer_correction",) * 2
    assert (ownership["verified"], ownership["source_page"], ownership["supporting_passage"]) == (True, 2, FEE_SIMPLE_QUOTE)
    for name in MOCK_KNOWN - {"ownership_nature"}:
        assert _rights(body)[name]["value_origin"] == "ai_extraction"
    # The AI's superseded value is not part of the passport; it stays in the internal audit trail
    assert ai_value not in r.text
    corrections = (await client.get(f"/extractions/{created_extraction['id']}/corrections", headers=issuer_headers)).json()
    assert corrections[0]["original_value"] == ai_value
    assert (await client.get(f"/extractions/{created_extraction['id']}/corrections", headers=inst_headers)).status_code == 403


async def test_reviewer_marked_unknown_field_shows_no_leftover_value(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict,
    second_legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    ai = next(c for c in created_extraction["claims"] if c["field_name"] == "jurisdiction")
    ai_value = ai["value"]
    await _correct(client, created_extraction, F.jurisdiction, legal_headers, corrected_value=ai_value, is_unknown=True)
    # Since step 7 the correction clears the claim itself: value "unknown", no page, no quotation
    row = (await async_db.execute(text("SELECT value, source_page, supporting_passage FROM legal_claims WHERE id = :c"),
                                  {"c": ai["id"]})).one()
    assert tuple(row) == ("unknown", None, None)
    # Records written before step 7 kept the old text of a claim marked unknown: the passport never
    # presents any of it
    await _exec(async_db, "UPDATE legal_claims SET value = :v, source_page = 1, supporting_passage = :p WHERE id = :c",
                v=ai_value, p=ai["supporting_passage"], c=ai["id"])
    await _approved(client, created_extraction, second_legal_headers)  # not the corrector (Stage 9B-5C)
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    jurisdiction = _rights(r.json())["jurisdiction"]
    assert jurisdiction["is_unknown"] is True and jurisdiction["value"] == "unknown"
    _no_positive_indicator(jurisdiction)
    (source,) = jurisdiction["sources"]
    assert (source["value"], source["supporting_passage"], source["source_page"]) == ("unknown", None, None)
    assert (source["value_origin"], source["evidence_status"]) == ("reviewer_correction", "not_applicable")
    assert ai_value not in r.text


# ── 14, 20. Aggregate verification status ────────────────────────────────────


@pytest.mark.parametrize("scenario,expected_level,expected_counts", [
    ("issuer_only", "issuer_attested", (4, 0)),
    ("legal_only", "independently_verified", (4, 4)),
    ("issuer_and_legal", "independently_verified", (4, 4)),
    ("legal_on_unknown_only_doc_plus_issuer_doc", "issuer_attested", (4, 0)),
    ("legal_doc_plus_issuer_doc_other_field", "partially_independently_verified", (5, 4)),
    ("legal_doc_plus_issuer_doc_same_values", "issuer_attested", (4, 0)),
])
async def test_aggregate_status_follows_the_verified_fields(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, scenario: str, expected_level: str, expected_counts,
):
    if scenario == "issuer_only":
        await _approved(client, created_extraction, issuer_headers)
    elif scenario == "legal_only":
        await _approved(client, created_extraction, legal_headers)
    elif scenario == "issuer_and_legal":
        await _approved(client, created_extraction, issuer_headers, legal_headers)
    elif scenario == "legal_on_unknown_only_doc_plus_issuer_doc":
        await _approved(client, created_extraction, issuer_headers)
        other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
        await _approved(client, await _extract_exactly(client, other["id"], issuer_headers), legal_headers)
    elif scenario == "legal_doc_plus_issuer_doc_other_field":
        await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers, legal_headers)
    else:
        await _approved(client, created_extraction, legal_headers)
        other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
        await _approved(client, await _extract(client, other["id"], issuer_headers), issuer_headers)

    body = await _passport(client, created_asset, inst_headers)
    summary = body["verification_summary"]
    assert body["verification_level"] == expected_level
    assert (summary["source_verified_field_count"], summary["independently_attested_field_count"]) == expected_counts
    # Every positive indicator has a defensible source: verified fields only, each source-verified
    for entry in body["legal_rights"]:
        if entry["independently_verified"]:
            assert entry["verified"] and entry["reviewer_attestation_status"] == "independent_reviewer_attested"
        if entry["verified"]:
            assert entry["evidence_status"] == "verified" and not entry["is_unknown"] and not entry["conflict"]
            assert all(s["evidence_status"] == "verified" for s in entry["sources"] if not s["is_unknown"])
    for document in body["source_documents"]:
        if document["independently_verified"]:
            assert "legal_verification" in document["approval_types"] and document["source_verified_claim_count"] > 0
    if scenario == "legal_on_unknown_only_doc_plus_issuer_doc":
        opinion = next(d for d in body["source_documents"] if d["title"] == "Opinion")
        assert opinion["approval_types"] == ["legal_verification"] and opinion["independently_verified"] is False
        assert summary["independent_reviewer_attestation_recorded"] is True


async def test_conflicting_fields_carry_no_positive_indicator(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict,
):
    await _approved(client, created_extraction, legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
    ex = await _extract_exactly(client, other["id"], issuer_headers,
                                known(F.ownership_nature, 2, FEE_SIMPLE_QUOTE, value="Fee simple title."))
    await _approved(client, ex, legal_headers)
    body = await _passport(client, created_asset, inst_headers)
    conflict = _rights(body)["ownership_nature"]
    assert conflict["conflict"] is True and conflict["value"] == "conflicting"
    # The field asserts no value: no positive indicator at field level
    assert (conflict["verified"], conflict["independently_verified"]) == (False, False)
    assert (conflict["supporting_passage"], conflict["source_page"], conflict["document_id"]) == (None, None, None)
    assert (conflict["evidence_status"], conflict["reviewer_attestation_status"], conflict["value_origin"]) == (
        "not_applicable", "not_applicable", None)
    # Each source describes only its own document's statement (both independently approved here)
    assert {s["value"] for s in conflict["sources"]} == {"Fee simple title.", next(
        c["value"] for c in created_extraction["claims"] if c["field_name"] == "ownership_nature")}
    assert all(s["evidence_status"] == "verified" and s["independently_verified"] for s in conflict["sources"])
    assert body["verification_summary"]["conflicting_field_count"] == 1
    assert "ownership_nature" not in {e["field_name"] for e in body["legal_rights"] if e["verified"]}


# ── 15, 16. Serialization and compatibility ──────────────────────────────────


async def test_response_serialization_and_backward_compatible_fields(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, inst_headers: dict
):
    body = await _passport(client, created_asset, inst_headers)
    assert set(body) == LEGACY_TOP_KEYS | {"verification_summary"} | STEP_9B5B1_TOP_KEYS
    assert body["verification_level"] in LEGACY_LEVELS
    for entry in body["legal_rights"]:
        assert set(entry) == LEGACY_ENTRY_KEYS | NEW_ENTRY_KEYS
        assert isinstance(entry["verified"], bool) and isinstance(entry["independently_verified"], bool)
        for source in entry["sources"]:
            assert set(source) == LEGACY_SOURCE_KEYS | NEW_SOURCE_KEYS
    for document in body["source_documents"]:
        assert set(document) == LEGACY_DOCUMENT_KEYS | NEW_DOCUMENT_KEYS
    assert set(body["verification_summary"]) == SUMMARY_KEYS | STEP_9B5B1_SUMMARY_KEYS
    assert body["verification_summary"]["evidence_scope"] == "source_text_presence_only"
    meaning = body["verification_summary"]["meaning"]
    for limit in ("enforceability", "ownership", "entitlement", "compliance", "solvency", "legal opinion"):
        assert limit in meaning


def test_openapi_documents_the_verification_semantics():
    schemas = app.openapi()["components"]["schemas"]
    entry = schemas["LegalRightEntry"]["properties"]
    for name in NEW_ENTRY_KEYS | {"verified", "independently_verified"}:
        assert entry[name].get("description"), name
    assert "not legal correctness" in entry["verified"]["description"]
    assert "not an independent legal opinion" in entry["independently_verified"]["description"]
    assert set(schemas["VerificationSummary"]["properties"]) == SUMMARY_KEYS | STEP_9B5B1_SUMMARY_KEYS
    # Stage 10B-3 added manual_entry (claims typed by a user); the existing values are unchanged
    assert set(schemas["ValueOrigin"]["enum"]) == {"ai_extraction", "manual_entry", "reviewer_correction"}
    assert set(schemas["VerificationLevel"]["enum"]) == LEGACY_LEVELS  # no new level values
    passport_path = app.openapi()["paths"]["/passport/{network}/{asset_code}/{issuer_key}"]["get"]
    assert "does not establish enforceability" in passport_path["description"]


# ── 17. Cross-tenant isolation ───────────────────────────────────────────────


async def test_passport_evidence_never_crosses_tenants(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, approved_extraction: dict, source_pdf: bytes,
    other_issuer_headers: dict, other_legal_headers: dict, inst_headers: dict, inst_beta_headers: dict,
):
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Bond terms")
    await _approved(client, await _extract(client, doc["id"], other_issuer_headers), other_legal_headers)

    meridian = await client.get(_passport_url(created_asset), headers=inst_beta_headers)
    unknown = await client.get(_passport_url({**created_asset, "stellar_asset_code": "NOPE"}), headers=inst_beta_headers)
    assert meridian.status_code == unknown.status_code == 404 and meridian.json() == unknown.json()
    assert (await client.get(_passport_url(harbor), headers=inst_headers)).status_code == 404

    body = await _passport(client, harbor, inst_beta_headers)
    assert {s["document_id"] for e in body["legal_rights"] for s in e["sources"]} == {doc["id"]}
    assert approved_extraction["document_id"] not in str(body)
    assert body["verification_level"] == "independently_verified"


# ── 18. Deterministic evaluation ─────────────────────────────────────────────


async def test_repeated_reads_are_identical(
    client: AsyncClient, created_asset: dict, created_extraction: dict, legal_headers: dict, inst_headers: dict
):
    await _approved(client, created_extraction, legal_headers)
    bodies = [await _passport(client, created_asset, inst_headers) for _ in range(3)]
    for body in bodies:
        body.pop("retrieved_at")
    assert bodies[0] == bodies[1] == bodies[2]


# ── 19. Approval integrity and concurrency ───────────────────────────────────


async def test_passport_reads_racing_with_an_upload_never_mix_versions(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    await _approved(client, await _extract(client, doc["id"], issuer_headers), legal_headers)

    async def read():
        return await client.get(_passport_url(asset), headers=inst_headers)

    results = await asyncio.wait_for(asyncio.gather(
        read(), read(),
        client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                    files={"file": ("v2.pdf", make_source_pdf("Version 2."), "application/pdf")}),
        read(), read(),
    ), 60)
    upload = results[2]
    assert upload.status_code == 201, upload.text
    for r in results[:2] + results[3:]:
        assert r.status_code in (200, 409), r.text
        if r.status_code == 200:
            body = r.json()
            # A served passport is entirely the approved version 1: never verified values of version 2
            assert body["source_document"]["version_number"] == 1
            assert {e["document_version_number"] for e in body["legal_rights"] if e["verified"]} == {1}
        else:
            assert r.json() == {"detail": "The legal approval for this asset is no longer current; a re-review is required."}
    assert (await read()).status_code == 409  # after the upload: superseded, nothing is served
