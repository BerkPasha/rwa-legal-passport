"""
Stage 9B-4 step 2 — evidence verification results in the extraction API.

Every claim of an extraction response carries `evidence`: the deterministic check of its
quotation against the stored page text of the extraction's own document version. Approval
and passport behaviour are not changed in this step. Coverage:
 1. Valid quotation on the cited page
 2. Fabricated quotation
 3. Incorrect page numbers
 4. Quotation only on another page
 5. Empty, missing or invalid page text (fail closed)
 6. Unknown claims
 7. Outdated document version
 8. Mismatched extraction version
 9. Tenant isolation
10. Serialization and OpenAPI schema
11. Existing response compatibility
12. Evidence is not approval or independent verification
"""

import json
import uuid
from datetime import datetime, timezone

from httpx import AsyncClient
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EvidenceStatus, LegalFieldName
from app.main import app
from app.models.document import DocumentPage
from app.models.extraction import LegalClaim, LegalExtraction
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim
from app.services.ai import UNVERIFIED_DISCLAIMER, get_ai_service
from tests.conftest import _HARBOR_ASSET, foreign_keys_unenforced
from tests.pdf_fixtures import make_text_pdf

F = LegalFieldName

PAGE_1 = ["This Trust Agreement is governed by the laws", "of the State of New York, United States of America."]
PAGE_2 = ["Net rental income shall be distributed quarterly,", "pro-rata to token holdings."]
TWO_PAGE_PDF = make_text_pdf([PAGE_1, PAGE_2])
QUOTE_1 = "governed by the laws of the State of New York"
QUOTE_2 = "Net rental income shall be distributed quarterly"
FABRICATED = "Investors hold a fractional beneficial ownership interest"

EVIDENCE_KEYS = {
    "status", "cited_page", "found_on_pages", "page_count", "version_number",
    "verification_method", "normalization_version", "scope", "explanation",
}


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


def known(field: LegalFieldName, page, passage: str, value: str = "Stated value.") -> RawClaim:
    return RawClaim(field=field, value=value, is_unknown=False,
                    source=ClaimSource(page_number=page, supporting_passage=passage))


def unknown(field: LegalFieldName, page=None, passage: str | None = None) -> RawClaim:
    source = ClaimSource(page_number=page, supporting_passage=passage) if passage is not None else None
    return RawClaim(field=field, value="unknown", is_unknown=True, source=source)


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, headers: dict, title: str = "Prospectus") -> dict:
    r = await client.post(
        f"/assets/{asset_id}/documents",
        headers=headers,
        data={"document_type": "prospectus", "title": title},
        files={"file": ("doc.pdf", pdf, "application/pdf")},
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _extract(client: AsyncClient, document_id: str, headers: dict, *claims: RawClaim) -> dict:
    app.dependency_overrides[get_ai_service] = lambda: ScriptedAI(*claims)
    r = await client.post(f"/documents/{document_id}/extractions", headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _get(client: AsyncClient, extraction_id: str, headers: dict) -> dict:
    r = await client.get(f"/extractions/{extraction_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _evidence(extraction: dict, field: LegalFieldName) -> dict:
    return next(c for c in extraction["claims"] if c["field_name"] == field.value)["evidence"]


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {k for v in value.values() for k in _all_keys(v)}
    if isinstance(value, list):
        return {k for v in value for k in _all_keys(v)}
    return set()


# ── 1. Valid quotation on the cited page ─────────────────────────────────────


async def test_exact_quotation_on_cited_page_is_verified(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    evidence = _evidence(ex, F.jurisdiction)
    assert evidence["status"] == "verified"
    assert evidence["cited_page"] == 1
    assert evidence["found_on_pages"] == [1]
    assert evidence["page_count"] == 2
    assert evidence["version_number"] == 1
    assert evidence["verification_method"] == "exact_substring_token_boundary"
    assert evidence["normalization_version"] == 1
    assert evidence["scope"] == "source_text_presence_only"
    assert "not legal correctness" in evidence["explanation"]


async def test_quotation_spanning_a_line_break_is_verified(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers,
                        known(F.income_rights, 2, "distributed quarterly, pro-rata to token holdings"))
    assert _evidence(ex, F.income_rights)["status"] == "verified"


# ── 2. Fabricated quotation ──────────────────────────────────────────────────


async def test_fabricated_quotation_is_not_found(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.ownership_nature, 1, FABRICATED))
    evidence = _evidence(ex, F.ownership_nature)
    assert evidence["status"] == "not_found"
    assert evidence["found_on_pages"] == []


async def test_default_mock_quotations_on_a_blank_pdf_are_not_verified(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict
):
    # A blank (scanned-like) PDF: none of the mock AI quotations can be verified
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    r = await client.post(f"/documents/{doc['id']}/extractions", headers=issuer_headers)
    assert r.status_code == 201, r.text
    for claim in r.json()["claims"]:
        expected = "not_applicable" if claim["is_unknown"] else "no_text_layer"
        assert claim["evidence"]["status"] == expected, claim["field_name"]


async def test_default_mock_quotations_on_the_standard_fixture_are_verified(created_extraction: dict):
    # The standard fixture contains every mock AI quotation on the cited page 1
    for claim in created_extraction["claims"]:
        expected = "not_applicable" if claim["is_unknown"] else "verified"
        assert claim["evidence"]["status"] == expected, claim["field_name"]


# ── 3. Incorrect page numbers ────────────────────────────────────────────────


async def test_incorrect_page_numbers_fail(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    pages = {F.ownership_nature: 999, F.income_rights: 3, F.voting_rights: 0, F.jurisdiction: -3}
    ex = await _extract(client, doc["id"], issuer_headers,
                        *(known(field, page, QUOTE_1) for field, page in pages.items()))
    for field, page in pages.items():
        evidence = _evidence(ex, field)
        assert evidence["status"] == "page_out_of_range", field
        assert evidence["cited_page"] == page
        assert evidence["page_count"] == 2
        # The quotation exists on page 1, so the reviewer is told where it is
        assert evidence["found_on_pages"] == [1]


async def test_missing_page_reference_fails(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, None, QUOTE_1))
    evidence = _evidence(ex, F.jurisdiction)
    assert evidence["status"] == "page_missing"
    assert evidence["cited_page"] is None


async def test_correcting_the_page_is_reflected_on_the_next_read(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 999, QUOTE_1))
    claim = next(c for c in ex["claims"] if c["field_name"] == F.jurisdiction.value)
    r = await client.patch(
        f"/extractions/{ex['id']}/claims/{claim['id']}", headers=issuer_headers,
        json={"corrected_value": "Stated value.", "is_unknown": False, "corrected_source_page": 1,
              # The mandatory precondition (Stage 9B-4 issue A): the claim as read
              "expected_claim": {key: claim[key] for key in ("value", "is_unknown", "source_page", "supporting_passage")}},
    )
    assert r.status_code == 200
    assert "evidence" not in r.json()  # the correction response is unchanged
    assert _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)["status"] == "verified"


# ── 4. Quotation only on another page ────────────────────────────────────────


async def test_quotation_only_on_another_page_is_not_verified(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.income_rights, 1, QUOTE_2))
    evidence = _evidence(ex, F.income_rights)
    assert evidence["status"] == "found_on_other_page"
    assert evidence["cited_page"] == 1
    assert evidence["found_on_pages"] == [2]


# ── 5. Empty, missing or invalid page text (fail closed) ─────────────────────


async def test_blank_pdf_fails_closed(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], minimal_pdf, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    evidence = _evidence(ex, F.jurisdiction)
    assert evidence["status"] == "no_text_layer"
    assert evidence["page_count"] == 1
    assert evidence["version_number"] == 1


async def test_missing_page_records_fail_closed(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    assert _evidence(ex, F.jurisdiction)["status"] == "verified"

    await async_db.execute(delete(DocumentPage).where(DocumentPage.version_id == ex["version_id"]))
    await async_db.commit()

    evidence = _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)
    assert evidence["status"] == "source_text_unavailable"
    assert evidence["page_count"] == 0
    assert evidence["found_on_pages"] == []


async def test_inconsistent_page_records_fail_closed(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    # A second record for page 1 (the table has no unique index on page numbers)
    async_db.add(DocumentPage(id=str(uuid.uuid4()), version_id=ex["version_id"], page_number=1,
                              text_content="Injected duplicate page."))
    await async_db.commit()

    evidence = _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)
    assert evidence["status"] == "source_text_invalid"


async def test_evidence_follows_the_stored_page_text(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    await async_db.execute(
        update(DocumentPage)
        .where(DocumentPage.version_id == ex["version_id"], DocumentPage.page_number == 1)
        .values(text_content="This Trust Agreement is governed by the laws of Delaware.")
    )
    await async_db.commit()
    assert _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)["status"] == "not_found"


# ── 6. Unknown claims ────────────────────────────────────────────────────────


async def test_unknown_claims_are_never_verified(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(
        client, doc["id"], issuer_headers,
        unknown(F.voting_rights),
        unknown(F.redemption_rights, page=1, passage=QUOTE_1),  # unknown, although a real quotation is cited
    )
    for field in (F.voting_rights, F.redemption_rights):
        evidence = _evidence(ex, field)
        assert evidence["status"] == "not_applicable"
        assert evidence["found_on_pages"] == []


# ── 7. Outdated document version ─────────────────────────────────────────────


async def test_outdated_version_is_checked_against_its_own_text(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    v1_pdf = make_text_pdf([["Version one: the investor holds fee simple ownership of the property."]])
    v2_pdf = make_text_pdf([["Version two: the investor holds a leasehold interest only."]])
    claim = known(F.ownership_nature, 1, "the investor holds fee simple ownership")

    doc = await _upload(client, created_asset["id"], v1_pdf, issuer_headers)
    ex_v1 = await _extract(client, doc["id"], issuer_headers, claim)
    r = await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                          files={"file": ("v2.pdf", v2_pdf, "application/pdf")})
    assert r.status_code == 201

    # The v1 extraction stays bound to the v1 text; the newer version is never substituted
    v1_again = await _get(client, ex_v1["id"], issuer_headers)
    assert v1_again["approval_state"]["version_is_current"] is False
    assert _evidence(v1_again, F.ownership_nature) == _evidence(ex_v1, F.ownership_nature)
    assert _evidence(v1_again, F.ownership_nature)["status"] == "verified"
    assert _evidence(v1_again, F.ownership_nature)["version_number"] == 1

    # The same quotation does not occur in version 2
    ex_v2 = await _extract(client, doc["id"], issuer_headers, claim)
    assert _evidence(ex_v2, F.ownership_nature)["status"] == "not_found"
    assert _evidence(ex_v2, F.ownership_nature)["version_number"] == 2

    listed = (await client.get(f"/documents/{doc['id']}/extractions", headers=issuer_headers)).json()
    by_id = {e["id"]: _evidence(e, F.ownership_nature) for e in listed}
    assert by_id[ex_v1["id"]]["version_number"] == 1 and by_id[ex_v1["id"]]["status"] == "verified"
    assert by_id[ex_v2["id"]]["version_number"] == 2 and by_id[ex_v2["id"]]["status"] == "not_found"


# ── 8. Mismatched extraction version ─────────────────────────────────────────


async def test_version_of_another_document_is_never_used(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc_a = await _upload(client, created_asset["id"], make_text_pdf([["Unrelated text of document A."]]),
                          issuer_headers, title="Document A")
    doc_b = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers, title="Document B")
    ex = await _extract(client, doc_a["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    assert _evidence(ex, F.jurisdiction)["status"] == "not_found"

    # Corrupt the extraction so that it points to document B's version, whose text contains the quotation
    await async_db.execute(
        update(LegalExtraction).where(LegalExtraction.id == ex["id"])
        .values(version_id=doc_b["current_version"]["id"])
    )
    await async_db.commit()

    evidence = _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)
    assert evidence["status"] == "source_text_unavailable"
    assert evidence["version_number"] is None
    assert evidence["page_count"] == 0 and evidence["found_on_pages"] == []


async def test_nonexistent_extraction_version_fails_closed(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    async with foreign_keys_unenforced(async_db):           # a version that exists nowhere
        await async_db.execute(
            update(LegalExtraction).where(LegalExtraction.id == ex["id"]).values(version_id=str(uuid.uuid4()))
        )

    evidence = _evidence(await _get(client, ex["id"], issuer_headers), F.jurisdiction)
    assert evidence["status"] == "source_text_unavailable"
    assert evidence["version_number"] is None


# ── 9. Tenant isolation ──────────────────────────────────────────────────────


async def test_other_tenant_cannot_read_evidence(
    client: AsyncClient, created_asset: dict, issuer_headers: dict, other_issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))
    assert (await client.get(f"/extractions/{ex['id']}", headers=other_issuer_headers)).status_code == 404
    r = await client.get(f"/documents/{doc['id']}/extractions", headers=other_issuer_headers)
    assert r.status_code == 404
    assert QUOTE_1 not in r.text


async def test_version_of_another_tenant_is_never_used(
    client: AsyncClient,
    admin_client: AsyncClient,
    async_db: AsyncSession,
    created_asset: dict,
    issuer_headers: dict,
    other_issuer_headers: dict,
):
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    harbor_doc = await _upload(client, harbor["id"], TWO_PAGE_PDF, other_issuer_headers, title="Harbor Prospectus")
    doc = await _upload(client, created_asset["id"], make_text_pdf([["Meridian text only."]]), issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers, known(F.jurisdiction, 1, QUOTE_1))

    await async_db.execute(
        update(LegalExtraction).where(LegalExtraction.id == ex["id"])
        .values(version_id=harbor_doc["current_version"]["id"])
    )
    await async_db.commit()

    body = await _get(client, ex["id"], issuer_headers)
    evidence = _evidence(body, F.jurisdiction)
    assert evidence["status"] == "source_text_unavailable"
    assert evidence["version_number"] is None
    assert evidence["page_count"] == 0 and evidence["found_on_pages"] == []
    assert "Harbor" not in json.dumps(body)


# ── 10. Serialization and OpenAPI schema ─────────────────────────────────────


async def test_evidence_object_serialization(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers,
                        known(F.jurisdiction, 1, QUOTE_1), known(F.ownership_nature, 999, FABRICATED),
                        unknown(F.voting_rights))
    statuses = {e.value for e in EvidenceStatus}
    for claim in ex["claims"]:
        evidence = claim["evidence"]
        assert set(evidence) == EVIDENCE_KEYS
        assert evidence["status"] in statuses
        assert evidence["cited_page"] is None or isinstance(evidence["cited_page"], int)
        assert all(isinstance(p, int) for p in evidence["found_on_pages"])
        assert isinstance(evidence["page_count"], int) and isinstance(evidence["version_number"], int)
        assert isinstance(evidence["explanation"], str) and evidence["explanation"]


async def test_openapi_documents_the_evidence_object(client: AsyncClient):
    schemas = (await client.get("/openapi.json")).json()["components"]["schemas"]
    evidence = schemas["ClaimEvidenceResponse"]
    assert set(evidence["properties"]) == EVIDENCE_KEYS
    assert "evidence" in schemas["LegalClaimResponse"]["required"]
    assert set(schemas["EvidenceStatus"]["enum"]) == {s.value for s in EvidenceStatus}


async def test_repeated_reads_return_identical_evidence(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers,
                        known(F.jurisdiction, 1, QUOTE_1), known(F.income_rights, 1, QUOTE_2))
    first = await _get(client, ex["id"], issuer_headers)
    for _ in range(3):
        assert (await _get(client, ex["id"], issuer_headers))["claims"] == first["claims"]


# ── 11. Existing response compatibility ──────────────────────────────────────


async def test_existing_extraction_fields_are_unchanged(
    client: AsyncClient, async_db: AsyncSession, created_extraction: dict, issuer_headers: dict
):
    extraction_keys = {"id", "document_id", "version_id", "status", "ai_model", "disclaimer",
                       "extraction_timestamp", "created_at", "claims", "approval_state",
                       "source"}  # Stage 10B-3: one additive field; the existing ones are unchanged
    claim_keys = {"id", "extraction_id", "field_name", "value", "is_unknown", "source_page",
                  "supporting_passage", "created_at"}
    eid = created_extraction["id"]
    listed = (await client.get(f"/documents/{created_extraction['document_id']}/extractions",
                               headers=issuer_headers)).json()
    for body in (created_extraction, await _get(client, eid, issuer_headers), listed[0]):
        assert set(body) == extraction_keys
        assert body["status"] == "draft"
        assert body["disclaimer"] == UNVERIFIED_DISCLAIMER
        assert body["approval_state"]["state"] == "not_approved"
        for claim in body["claims"]:
            assert set(claim) == claim_keys | {"evidence"}

    # Claim values are reported exactly as stored; evidence never rewrites them
    rows = {c.id: c for c in (await async_db.execute(
        select(LegalClaim).where(LegalClaim.extraction_id == eid))).scalars()}
    for claim in created_extraction["claims"]:
        row = rows[claim["id"]]
        assert (claim["value"], claim["is_unknown"], claim["source_page"], claim["supporting_passage"]) == (
            row.value, row.is_unknown, row.source_page, row.supporting_passage)
    # Claims stay ordered by field name
    assert [c["field_name"] for c in created_extraction["claims"]] == sorted(f.value for f in F)


# ── 12. Evidence is not approval or independent verification ─────────────────


async def test_evidence_is_independent_of_approval(
    client: AsyncClient,
    created_asset: dict,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
):
    doc = await _upload(client, created_asset["id"], TWO_PAGE_PDF, issuer_headers)
    ex = await _extract(client, doc["id"], issuer_headers,
                        known(F.jurisdiction, 1, QUOTE_1), unknown(F.voting_rights))
    before = {c["field_name"]: c["evidence"] for c in ex["claims"]}

    r = await client.post(f"/extractions/{ex['id']}/approvals", headers=legal_headers, json={})
    assert r.status_code == 201
    after = await _get(client, ex["id"], issuer_headers)

    # An independent legal approval changes the approval state, never the evidence result
    assert after["approval_state"]["current_approval_types"] == ["legal_verification"]
    assert {c["field_name"]: c["evidence"] for c in after["claims"]} == before
    assert _evidence(after, F.voting_rights)["status"] == "not_applicable"
    for claim in after["claims"]:
        assert not _all_keys(claim["evidence"]) & {"verified", "independently_verified", "approval_type",
                                                    "approved", "verification_level"}
    assert not any("independent" in s.value or "approv" in s.value for s in EvidenceStatus)

    # The passport is unchanged in this step: it carries no evidence object
    passport = await client.get(
        f"/passport/{created_asset['blockchain_network']}/{created_asset['stellar_asset_code']}"
        f"/{created_asset['stellar_issuer_public_key']}",
        headers=inst_headers,
    )
    assert passport.status_code == 200
    assert "evidence" not in _all_keys(passport.json())
