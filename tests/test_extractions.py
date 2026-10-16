"""
Phase 4 — AI Legal-Rights Extraction Engine tests.

The mock AI service is used; no real request is sent to the Anthropic API.
The mock returns 4 known + 3 unknown fields.
"""

import pytest
from httpx import AsyncClient

from app.enums import LegalFieldName
from app.services.ai import UNVERIFIED_DISCLAIMER


# ── Success scenarios ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_extraction_returns_201(
    issuer_client: AsyncClient, created_document: dict
):
    """Extraction must be triggerable for a valid document and return 201."""
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")

    assert resp.status_code == 201
    data = resp.json()
    assert data["document_id"] == doc_id
    assert "id" in data
    assert "claims" in data


@pytest.mark.asyncio
async def test_extraction_contains_all_seven_fields(
    issuer_client: AsyncClient, created_document: dict
):
    """An extraction must always contain all seven legal-rights fields."""
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    assert len(claims) == 7
    extracted_fields = {c["field_name"] for c in claims}
    expected_fields = {f.value for f in LegalFieldName}
    assert extracted_fields == expected_fields


@pytest.mark.asyncio
async def test_extraction_status_is_always_draft(
    issuer_client: AsyncClient, created_document: dict
):
    """
    The status can only be 'draft'.
    Automatic approval is never applied.
    """
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")

    assert resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_extraction_disclaimer_is_present(
    issuer_client: AsyncClient, created_document: dict
):
    """
    Every extraction response must contain the disclaimer text.
    The AI cannot confirm legal validity.
    """
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    data = resp.json()

    assert "disclaimer" in data
    assert len(data["disclaimer"]) > 0
    # Verify that the disclaimer text is passed through unchanged
    assert data["disclaimer"] == UNVERIFIED_DISCLAIMER


@pytest.mark.asyncio
async def test_known_claims_have_source_references(
    issuer_client: AsyncClient, created_document: dict
):
    """
    Claims with is_unknown=False must include a source page and a quote.
    This satisfies the requirement that 'every claim must show its source'.
    """
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    known = [c for c in claims if not c["is_unknown"]]
    assert len(known) > 0, "The mock must return at least one known claim"

    for claim in known:
        assert claim["supporting_passage"] is not None, (
            f"{claim['field_name']} has is_unknown=False but no source quote"
        )
        assert len(claim["supporting_passage"]) > 0


@pytest.mark.asyncio
async def test_unknown_claims_are_flagged_correctly(
    issuer_client: AsyncClient, created_document: dict
):
    """
    Fields with insufficient evidence must have is_unknown=True and value='unknown'.
    The AI does not fabricate missing information.
    """
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    unknown = [c for c in claims if c["is_unknown"]]
    assert len(unknown) > 0, "The mock must return at least one unknown field"

    for claim in unknown:
        assert claim["value"] == "unknown", (
            f"{claim['field_name']} has is_unknown=True but value is not 'unknown'"
        )
        assert claim["supporting_passage"] is None


@pytest.mark.asyncio
async def test_ai_model_is_recorded(
    issuer_client: AsyncClient, created_document: dict
):
    """The model that was used must be stored in the extraction record."""
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    data = resp.json()

    assert "ai_model" in data
    assert len(data["ai_model"]) > 0


@pytest.mark.asyncio
async def test_get_extraction_by_id(
    issuer_client: AsyncClient, created_document: dict
):
    """A created extraction must be retrievable by ID."""
    doc_id = created_document["id"]
    create_resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    extraction_id = create_resp.json()["id"]

    get_resp = await issuer_client.get(f"/extractions/{extraction_id}")

    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == extraction_id
    assert len(get_resp.json()["claims"]) == 7


@pytest.mark.asyncio
async def test_list_extractions_for_document(
    issuer_client: AsyncClient, created_document: dict
):
    """After two extractions are triggered, the list must contain both."""
    doc_id = created_document["id"]
    await issuer_client.post(f"/documents/{doc_id}/extractions")
    await issuer_client.post(f"/documents/{doc_id}/extractions")

    resp = await issuer_client.get(f"/documents/{doc_id}/extractions")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_extractions_stored_separately_from_assets(
    issuer_client: AsyncClient, created_document: dict
):
    """
    Extraction results must be kept separate from the asset/document records.
    The document metadata must not change after an extraction is triggered.
    """
    doc_id = created_document["id"]

    # Document state before the extraction
    before = await issuer_client.get(f"/documents/{doc_id}")
    before_status = before.json()["status"]

    # Trigger the extraction
    await issuer_client.post(f"/documents/{doc_id}/extractions")

    # Document state after the extraction — must not change
    after = await issuer_client.get(f"/documents/{doc_id}")
    assert after.json()["status"] == before_status
    assert after.json()["current_version_number"] == before.json()["current_version_number"]


# ── Error scenarios ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_extraction_nonexistent_document_returns_404(
    issuer_client: AsyncClient,
):
    """Triggering an extraction for a non-existent document must return 404."""
    resp = await issuer_client.post("/documents/nonexistent-doc-id-000/extractions")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_extraction_not_found_returns_404(issuer_client: AsyncClient):
    """A non-existent extraction ID must return 404."""
    resp = await issuer_client.get("/extractions/nonexistent-extraction-id")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_extractions_nonexistent_document_returns_404(issuer_client: AsyncClient):
    """Listing extractions for a non-existent document must return 404."""
    resp = await issuer_client.get("/documents/nonexistent-doc-id-000/extractions")

    assert resp.status_code == 404
