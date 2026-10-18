"""
Stage 10B-3 — manual legal claims (app/manual_claims.py, POST /documents/{id}/manual-extractions).

Coverage:
 1. Creation: an unapproved draft labelled manual, exactly as entered, with no AI service involved
 2. Source verification before anything is stored: fabricated, wrong-page, out-of-range, too
    short and image-only quotations are refused; one bad claim refuses the whole request
 3. Request validation: duplicate fields, "unknown" values, malformed pages and unknown keys
 4. References: asset, document version, superseded versions, inactive asset, other tenants
 5. Authorization: roles, scopes and credentials
 6. Review: no approval on creation, the existing approval gates and separation of duties apply,
    corrections fill in further fields, and the passport labels manual values
 7. No AI result can carry the manual marker
"""

import logging
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EvidenceStatus, LegalFieldName
from app.main import app
from app.manual_claims import MANUAL_DISCLAIMER, MANUAL_ENTRY_MODEL
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval
from app.services.ai import UNVERIFIED_DISCLAIMER, get_ai_service
from tests.conftest import _HARBOR_ASSET, _MERIDIAN_ASSET, INST_KEY_ALPHA, bearer
from tests.mock_ai import MockAIService
from tests.pdf_fixtures import make_source_pdf
from tests.test_evidence_binding import _stored_file
from tests.test_approval_evidence_gate import (
    FABRICATED,
    FEE_SIMPLE_QUOTE,
    INCOME_QUOTE,
    JURISDICTION_QUOTE,
    OWNERSHIP_QUOTE,
    ScriptedAI,
    _new_version,
    _passport_url,
    _upload,
    as_read,
    known,
)

F = LegalFieldName
FIXTURES = Path(__file__).parent / "fixtures" / "real_pdfs"
URL = "/documents/{}/manual-extractions"


def claim(field: LegalFieldName, page, passage: str, value: str = "Stated in the user's words.") -> dict:
    return {"field_name": field.value, "value": value, "source_page": page, "supporting_passage": passage}


def body(document: dict, *claims: dict, asset_id: str | None = None, version_id: str | None = None) -> dict:
    return {
        "asset_id": asset_id or document["asset_id"],
        "version_id": version_id or document["current_version"]["id"],
        "claims": list(claims),
    }


async def _create(client: AsyncClient, document: dict, headers: dict, *claims: dict, **refs):
    return await client.post(URL.format(document["id"]), headers=headers, json=body(document, *claims, **refs))


async def _counts(db: AsyncSession) -> tuple[int, int, int]:
    return tuple([
        (await db.execute(select(func.count()).select_from(model))).scalar_one()
        for model in (LegalExtraction, LegalClaim, ReviewApproval)
    ])


@pytest.fixture
def no_ai_service():
    """AI_PROVIDER=disabled, and any use of the AI service fails the test."""
    def _forbidden():
        raise AssertionError("the manual claim endpoint must never resolve an AI service")
    app.dependency_overrides[get_ai_service] = _forbidden
    yield
    app.dependency_overrides[get_ai_service] = lambda: MockAIService()


# ── 1. Creation ───────────────────────────────────────────────────────────────


async def test_a_verified_manual_claim_is_stored_as_an_unapproved_manual_draft(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, no_ai_service
):
    r = await _create(client, created_document, issuer_headers,
                      claim(F.jurisdiction, 1, JURISDICTION_QUOTE, "New York law governs the Trust Agreement."))
    assert r.status_code == 201, r.text
    record = r.json()
    assert record["status"] == "draft"
    assert record["source"] == "manual"
    assert record["ai_model"] == MANUAL_ENTRY_MODEL
    assert record["disclaimer"] == MANUAL_DISCLAIMER
    assert record["approval_state"] == {"state": "not_approved", "version_is_current": True,
                                        "current_approval_types": [], "reasons": []}
    assert record["document_id"] == created_document["id"]
    assert record["version_id"] == created_document["current_version"]["id"]
    by_field = {c["field_name"]: c for c in record["claims"]}
    assert set(by_field) == {f.value for f in LegalFieldName}
    entered = by_field["jurisdiction"]
    assert [entered["value"], entered["is_unknown"], entered["source_page"], entered["supporting_passage"]] == [
        "New York law governs the Trust Agreement.", False, 1, JURISDICTION_QUOTE]
    assert entered["evidence"]["status"] == "verified"
    assert entered["evidence"]["version_number"] == 1
    for field, c in by_field.items():
        if field != "jurisdiction":
            assert [c["value"], c["is_unknown"], c["source_page"], c["supporting_passage"]] == ["unknown", True, None, None]
            assert c["evidence"]["status"] == "not_applicable"
    # Nothing is approved, and nothing was attested on anyone's behalf
    assert await _counts(async_db) == (1, 7, 0)
    approvals = await client.get(f"/extractions/{record['id']}/approvals", headers=issuer_headers)
    assert approvals.json() == []


async def test_several_claims_are_created_together_and_kept_exactly_as_entered(
    client: AsyncClient, created_document: dict, issuer_headers: dict, no_ai_service
):
    spaced = "Net rental  income shall be\ndistributed quarterly"   # verifies after normalization
    r = await _create(client, created_document, issuer_headers,
                      claim(F.ownership_nature, 1, OWNERSHIP_QUOTE),
                      claim(F.income_rights, 1, spaced, "  Quarterly distributions.  "),
                      claim(F.insolvency_provisions, 2, FEE_SIMPLE_QUOTE))
    assert r.status_code == 201, r.text
    by_field = {c["field_name"]: c for c in r.json()["claims"]}
    assert by_field["income_rights"]["supporting_passage"] == spaced          # never rewritten
    assert by_field["income_rights"]["value"] == "  Quarterly distributions.  "
    assert by_field["insolvency_provisions"]["source_page"] == 2
    assert [by_field[f]["evidence"]["status"] for f in ("ownership_nature", "income_rights", "insolvency_provisions")] \
        == ["verified"] * 3
    assert sum(1 for c in by_field.values() if c["is_unknown"]) == 4


async def test_a_real_pdf_quotation_with_typographic_text_is_verified(
    client: AsyncClient, created_asset: dict, issuer_headers: dict, no_ai_service
):
    document = await _upload(client, created_asset["id"], (FIXTURES / "trust_prospectus.pdf").read_bytes(), issuer_headers)
    r = await _create(client, document, issuer_headers,
                      claim(F.insolvency_provisions, 4, "the Trust property shall not form part of the Trustee’s estate"),
                      claim(F.jurisdiction, 4, "governed by the laws of England and Wales"))
    assert r.status_code == 201, r.text
    assert {c["evidence"]["status"] for c in r.json()["claims"] if not c["is_unknown"]} == {"verified"}


async def test_the_author_is_recorded_in_the_security_log(
    client: AsyncClient, created_document: dict, issuer_headers: dict, caplog, no_ai_service
):
    with caplog.at_level(logging.INFO, logger="rwa_security"):
        r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))
    assert r.status_code == 201
    created = [rec.getMessage() for rec in caplog.records if "extraction.manual_created" in rec.getMessage()]
    assert len(created) == 1
    assert f"extraction={r.json()['id']}" in created[0] and "role=issuer_reviewer" in created[0]
    assert "fields=jurisdiction" in created[0]
    assert JURISDICTION_QUOTE not in created[0]    # no document text in the log


# ── 2. Source verification before storage ────────────────────────────────────


@pytest.mark.parametrize("page, passage, expected", [
    (1, FABRICATED, EvidenceStatus.not_found),                      # fabricated
    (2, JURISDICTION_QUOTE, EvidenceStatus.found_on_other_page),    # real, but on page 1
    (3, JURISDICTION_QUOTE, EvidenceStatus.page_out_of_range),      # the document has 2 pages
    (1, "laws of New York", EvidenceStatus.quotation_too_short),
    (1, "   ", EvidenceStatus.quotation_missing),
])
async def test_an_unverified_quotation_is_refused_and_nothing_is_stored(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, no_ai_service,
    page, passage, expected,
):
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, page, passage))
    assert r.status_code == 422, r.text
    payload = r.json()
    assert payload["manual_claim_refusal"] == "evidence_not_verified"
    assert payload["fields"] == ["jurisdiction"]
    assert [(f["field_name"], f["status"]) for f in payload["evidence_failures"]] == [("jurisdiction", expected.value)]
    assert "Nothing was stored" in payload["detail"]
    if expected is EvidenceStatus.found_on_other_page:
        assert "it appears on page 1" in payload["evidence_failures"][0]["explanation"]
    assert await _counts(async_db) == (0, 0, 0)


async def test_one_unverified_claim_refuses_the_whole_request(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, no_ai_service
):
    r = await _create(client, created_document, issuer_headers,
                      claim(F.ownership_nature, 1, OWNERSHIP_QUOTE),
                      claim(F.redemption_rights, 1, FABRICATED))
    assert r.status_code == 422
    assert r.json()["fields"] == ["redemption_rights"]
    assert await _counts(async_db) == (0, 0, 0)


async def test_an_image_only_page_cannot_be_quoted(
    client: AsyncClient, created_asset: dict, issuer_headers: dict, async_db: AsyncSession, no_ai_service
):
    mixed = await _upload(client, created_asset["id"], (FIXTURES / "mixed_scanned.pdf").read_bytes(), issuer_headers)
    assert mixed["current_version"]["text_layer"]["pages_without_text"] == [2]
    r = await _create(client, mixed, issuer_headers, claim(F.ownership_nature, 2, "a clause read on the scanned page"))
    assert r.json()["evidence_failures"][0]["status"] == "no_text_layer"

    scanned = await _upload(client, created_asset["id"], (FIXTURES / "scanned_only.pdf").read_bytes(), issuer_headers,
                            title="Scanned only")
    r = await _create(client, scanned, issuer_headers, claim(F.ownership_nature, 1, "a clause read on the scanned page"))
    assert r.status_code == 422
    assert r.json()["evidence_failures"][0]["status"] == "no_text_layer"
    assert await _counts(async_db) == (0, 0, 0)


@pytest.mark.parametrize("damage, reason", [("removed", "file_unavailable"), ("altered", "file_hash_mismatch")])
async def test_a_version_whose_stored_pdf_fails_its_check_cannot_be_quoted(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, tmp_path: Path,
    damage, reason,
):
    path = await _stored_file(async_db, tmp_path, created_document["current_version"]["id"])
    if damage == "removed":
        path.unlink()
    else:
        path.write_bytes(path.read_bytes() + b"\n% altered")
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))
    assert r.status_code == 409
    assert [r.json()["manual_claim_refusal"], r.json()["source_integrity_failure"]] == ["source_integrity_failed", reason]
    assert str(tmp_path) not in r.text
    assert await _counts(async_db) == (0, 0, 0)


async def test_a_version_without_stored_page_text_cannot_be_quoted(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    await async_db.execute(text("DELETE FROM document_pages WHERE version_id = :v"),
                           {"v": created_document["current_version"]["id"]})
    await async_db.commit()
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))
    assert r.status_code == 409
    assert r.json()["manual_claim_refusal"] == "source_integrity_failed"
    assert await _counts(async_db) == (0, 0, 0)


# ── 3. Request validation ─────────────────────────────────────────────────────


async def test_a_legal_field_can_be_given_once(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    r = await _create(client, created_document, issuer_headers,
                      claim(F.jurisdiction, 1, JURISDICTION_QUOTE), claim(F.jurisdiction, 1, JURISDICTION_QUOTE, "Other."))
    assert r.status_code == 422
    assert [r.json()["manual_claim_refusal"], r.json()["fields"]] == ["duplicate_field", ["jurisdiction"]]
    assert await _counts(async_db) == (0, 0, 0)


@pytest.mark.parametrize("value", ["unknown", "  Unknown ", " "])
async def test_a_manual_claim_must_state_a_value(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, value
):
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE, value))
    assert r.status_code == 422
    assert r.json()["manual_claim_refusal"] == "unknown_value"
    assert await _counts(async_db) == (0, 0, 0)


@pytest.mark.parametrize("change", [
    {"source_page": 0},
    {"source_page": True},
    {"source_page": "1"},
    {"source_page": None},
    {"field_name": "dividend_rights"},
    {"supporting_passage": ""},
    {"value": ""},
    {"value": "x" * 2001},
    {"evidence_status": "verified"},       # a client cannot state its own evidence result
])
async def test_malformed_claims_are_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession, change
):
    r = await _create(client, created_document, issuer_headers, {**claim(F.jurisdiction, 1, JURISDICTION_QUOTE), **change})
    assert r.status_code == 422
    assert await _counts(async_db) == (0, 0, 0)


async def test_malformed_requests_are_rejected(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    url = URL.format(created_document["id"])
    good = claim(F.jurisdiction, 1, JURISDICTION_QUOTE)
    for payload in (
        body(created_document),                                                     # no claim
        body(created_document, *[claim(f, 1, JURISDICTION_QUOTE) for f in LegalFieldName], good),  # 8 claims
        {**body(created_document, good), "status": "approved"},                     # unknown key
        {"version_id": created_document["current_version"]["id"], "claims": [good]},  # no asset
        {"asset_id": created_document["asset_id"], "claims": [good]},                 # no version
    ):
        assert (await client.post(url, headers=issuer_headers, json=payload)).status_code == 422
    assert await _counts(async_db) == (0, 0, 0)


# ── 4. References ─────────────────────────────────────────────────────────────


async def test_the_document_must_belong_to_the_named_asset(
    client: AsyncClient, admin_client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    second = (await admin_client.post("/assets", json={**_MERIDIAN_ASSET, "stellar_asset_code": "MRPRO2"})).json()
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    for asset_id in (second["id"], harbor["id"], "00000000-0000-4000-8000-000000000000"):
        r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE),
                          asset_id=asset_id)
        assert r.status_code == 422
        assert r.json() == {"detail": "The document does not belong to this asset. Nothing was stored.",
                            "manual_claim_refusal": "document_not_in_asset"}
    assert await _counts(async_db) == (0, 0, 0)


async def test_the_version_must_be_the_current_version_of_this_document(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    other = await _upload(client, created_asset["id"], make_source_pdf("Another document."), issuer_headers, title="Other")
    for version_id in (other["current_version"]["id"], "00000000-0000-4000-8000-000000000000"):
        r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE),
                          version_id=version_id)
        assert [r.status_code, r.json()["manual_claim_refusal"]] == [422, "version_not_in_document"]

    old_version = created_document["current_version"]["id"]
    await _new_version(client, created_document["id"], make_source_pdf("Version two."), issuer_headers)
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE),
                      version_id=old_version)
    assert [r.status_code, r.json()["manual_claim_refusal"]] == [409, "version_not_current"]
    assert await _counts(async_db) == (0, 0, 0)


async def test_no_claim_is_created_while_the_asset_is_not_active(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_document: dict,
    issuer_headers: dict, async_db: AsyncSession,
):
    assert (await admin_client.patch(f"/assets/{created_asset['id']}", json={"status": "suspended"})).status_code == 200
    r = await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))
    assert [r.status_code, r.json()["manual_claim_refusal"]] == [409, "asset_not_active"]
    assert await _counts(async_db) == (0, 0, 0)


# ── 5. Authorization ──────────────────────────────────────────────────────────


async def test_only_authorized_users_of_the_issuer_create_manual_claims(
    client: AsyncClient, created_document: dict, legal_headers: dict, other_issuer_headers: dict,
    other_legal_headers: dict, admin_headers: dict, async_db: AsyncSession,
):
    good = claim(F.jurisdiction, 1, JURISDICTION_QUOTE)
    cases = [
        ({}, 403),                                    # no credential
        (bearer("not-a-token"), 401),                 # invalid credential
        ({"X-API-Key": INST_KEY_ALPHA}, 403),         # institutional key: passport reads only
        (legal_headers, 403),                         # independent legal reviewer: no extraction:run
        (other_issuer_headers, 404),                  # another tenant: as if the document did not exist
        (other_legal_headers, 403),
    ]
    for headers, expected in cases:
        r = await _create(client, created_document, headers, good)
        assert r.status_code == expected, (headers, r.text)
        if expected == 404:
            assert r.json() == {"detail": "Document not found."}
    assert await _counts(async_db) == (0, 0, 0)
    # The platform operator holds extraction:run for every issuer, as for AI extractions
    assert (await _create(client, created_document, admin_headers, good)).status_code == 201


# ── 6. Review, approval and the passport ─────────────────────────────────────


async def test_the_manual_draft_follows_the_existing_review_and_reaches_the_passport_labelled_manual(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, no_ai_service,
):
    record = (await _create(client, created_document, issuer_headers,
                            claim(F.jurisdiction, 1, JURISDICTION_QUOTE, "New York law."))).json()
    # A draft has no passport
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code != 200

    assert (await client.post(f"/extractions/{record['id']}/review", headers=issuer_headers)).status_code == 200
    first = await client.post(f"/extractions/{record['id']}/approvals", headers=issuer_headers, json={})
    assert first.status_code == 201, first.text
    assert first.json()["approval_type"] == "issuer_attestation"
    # The same reviewer cannot approve twice
    again = await client.post(f"/extractions/{record['id']}/approvals", headers=issuer_headers, json={})
    assert [again.status_code, again.json()["approval_refusal"]] == [409, "duplicate_approval"]

    issuer_only = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    right = next(r for r in issuer_only["legal_rights"] if r["field_name"] == "jurisdiction")
    assert [right["value_origin"], right["reviewer_attestation_status"], right["independently_verified"]] == [
        "manual_entry", "issuer_attested", False]
    assert issuer_only["extraction_metadata"]["extraction_source"] == "manual"
    assert issuer_only["extraction_metadata"]["ai_model"] == MANUAL_ENTRY_MODEL

    second = await client.post(f"/extractions/{record['id']}/approvals", headers=legal_headers, json={})
    assert [second.status_code, second.json()["approval_type"]] == [201, "legal_verification"]
    passport = (await client.get(_passport_url(created_asset), headers=inst_headers)).json()
    right = next(r for r in passport["legal_rights"] if r["field_name"] == "jurisdiction")
    assert [right["value"], right["source_page"], right["supporting_passage"], right["document_version_number"]] == [
        "New York law.", 1, JURISDICTION_QUOTE, 1]
    assert [right["value_origin"], right["reviewer_attestation_status"], right["independently_verified"]] == [
        "manual_entry", "independent_reviewer_attested", True]
    assert {r["field_name"]: r["is_unknown"] for r in passport["legal_rights"]}["voting_rights"] is True
    assert sorted(a["approval_type"] for a in passport["approvals"]) == ["issuer_attestation", "legal_verification"]


async def test_further_fields_are_added_through_a_verified_correction(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, inst_headers: dict,
):
    record = (await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))).json()
    income = next(c for c in record["claims"] if c["field_name"] == "income_rights")
    url = f"/extractions/{record['id']}/claims/{income['id']}"
    base = {"corrected_value": "Quarterly income.", "is_unknown": False, "expected_claim": as_read(record, F.income_rights)}
    wrong = await client.patch(url, headers=issuer_headers, json={**base, "corrected_source_page": 2,
                                                                   "corrected_passage": INCOME_QUOTE})
    assert [wrong.status_code, wrong.json()["evidence_status"]] == [422, "found_on_other_page"]
    ok = await client.patch(url, headers=issuer_headers, json={**base, "corrected_source_page": 1,
                                                                "corrected_passage": INCOME_QUOTE})
    assert ok.status_code == 200, ok.text
    assert (await client.post(f"/extractions/{record['id']}/approvals", headers=issuer_headers, json={})).status_code == 201
    origins = {r["field_name"]: r["value_origin"]
               for r in (await client.get(_passport_url(created_asset), headers=inst_headers)).json()["legal_rights"]}
    assert [origins["jurisdiction"], origins["income_rights"]] == ["manual_entry", "reviewer_correction"]


async def test_evidence_that_no_longer_verifies_blocks_the_approval(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    record = (await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))).json()
    # The stored quotation is altered outside the API: the approval gate checks it again
    await async_db.execute(text("UPDATE legal_claims SET supporting_passage = :p WHERE extraction_id = :e AND "
                                "field_name = 'jurisdiction'"), {"p": FABRICATED, "e": record["id"]})
    await async_db.commit()
    r = await client.post(f"/extractions/{record['id']}/approvals", headers=issuer_headers, json={})
    assert r.status_code == 422
    assert {f["field_name"]: f["status"] for f in r.json()["evidence_failures"]} == {"jurisdiction": "not_found"}
    assert (await _counts(async_db))[2] == 0


async def test_separation_of_duties_applies_to_manual_records(
    client: AsyncClient, created_document: dict, issuer_headers: dict, legal_headers: dict, second_legal_headers: dict,
    other_legal_headers: dict,
):
    record = (await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))).json()
    # A legal reviewer who corrected a claim cannot give the legal approval of it
    corrected = await client.patch(
        f"/extractions/{record['id']}/claims/{next(c['id'] for c in record['claims'] if c['field_name'] == 'jurisdiction')}",
        headers=legal_headers, json={"corrected_value": "Governed by New York law.", "is_unknown": False,
                                     "corrected_source_page": 1, "corrected_passage": JURISDICTION_QUOTE,
                                     "expected_claim": as_read(record, F.jurisdiction)})
    assert corrected.status_code == 200, corrected.text
    own = await client.post(f"/extractions/{record['id']}/approvals", headers=legal_headers, json={})
    assert [own.status_code, own.json()["approval_refusal"]] == [409, "self_review"]
    # A legal reviewer of another issuer cannot see it at all
    assert (await client.post(f"/extractions/{record['id']}/approvals", headers=other_legal_headers, json={})).status_code == 404
    # Another legal reviewer of the issuer can
    assert (await client.post(f"/extractions/{record['id']}/approvals", headers=second_legal_headers, json={})).status_code == 201


async def test_a_manual_draft_of_a_superseded_version_cannot_be_approved(
    client: AsyncClient, created_document: dict, issuer_headers: dict
):
    record = (await _create(client, created_document, issuer_headers, claim(F.jurisdiction, 1, JURISDICTION_QUOTE))).json()
    await _new_version(client, created_document["id"], make_source_pdf("Version two."), issuer_headers)
    r = await client.post(f"/extractions/{record['id']}/approvals", headers=issuer_headers, json={})
    assert r.status_code == 409


# ── 7. The manual marker belongs to manual records only ──────────────────────


async def test_an_ai_result_carrying_the_manual_marker_is_refused(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession
):
    class Impersonating(ScriptedAI):
        async def extract_legal_rights(self, document_id, version_id, pages):
            result = await super().extract_legal_rights(document_id, version_id, pages)
            return result.model_copy(update={"ai_model": MANUAL_ENTRY_MODEL})

    app.dependency_overrides[get_ai_service] = lambda: Impersonating(known(F.jurisdiction, 1, JURISDICTION_QUOTE))
    try:
        r = await client.post(f"/documents/{created_document['id']}/extractions", headers=issuer_headers)
    finally:
        app.dependency_overrides[get_ai_service] = lambda: MockAIService()
    assert [r.status_code, r.json()["ai_error"]] == [503, "invalid_output"]
    assert await _counts(async_db) == (0, 0, 0)


async def test_ai_extractions_are_reported_as_produced_by_the_extraction_service(
    created_extraction: dict
):
    assert created_extraction["source"] == "ai_service"
    assert created_extraction["disclaimer"] == UNVERIFIED_DISCLAIMER
