"""
Phase 7 — Institutional Legal Passport integration and security tests (11 tests).

Coverage:
 1. Authentication — API key missing or invalid
 2. Asset not found — 404
 3. No approved extraction — 404 (the draft stays hidden)
 4. Draft information is never shown
 5. All 7 legal fields are returned with the correct verification status
 6. Unknown fields are labeled explicitly
 7. Approval metadata is present in the response
 8. Disclaimer mandatory in every response
 9. 429 is returned when the rate limit is exceeded
10. The passport request is written to the log
11. The source document PDF cannot be downloaded through this endpoint (no link)
"""

import logging

import pytest
from httpx import AsyncClient

from app.main import app
from app.rate_limit import InMemoryRateLimiter, get_rate_limiter


def _passport_url(asset: dict) -> str:
    """Builds the passport URL from the asset data."""
    return (
        f"/passport/{asset['blockchain_network']}"
        f"/{asset['stellar_asset_code']}"
        f"/{asset['stellar_issuer_public_key']}"
    )


# ── 1–2. Authentication ───────────────────────────────────────────────────────


async def test_passport_no_api_key_returns_403(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """Without the X-API-Key header, 403 Forbidden is returned."""
    r = await client.get(_passport_url(created_asset))
    assert r.status_code == 403


async def test_passport_invalid_api_key_returns_401(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """With an invalid API key, 401 Unauthorized is returned."""
    r = await client.get(
        _passport_url(created_asset), headers={"X-API-Key": "yanlis-anahtar"}
    )
    assert r.status_code == 401


# ── 3. Asset not found ────────────────────────────────────────────────────────


async def test_passport_unknown_asset_returns_404(
    client: AsyncClient, inst_headers: dict
):
    """An asset that is not defined on the Stellar network → 404 Not Found."""
    r = await client.get(
        "/passport/stellar-testnet/GHOST/GDQOE23CFSUMSVQK4Y5JHPPYK73VYCNHZHA7ENKCV37P6SUEO6XQBKPP",
        headers=inst_headers,
    )
    assert r.status_code == 404


# ── 4. Draft information stays hidden ─────────────────────────────────────────


async def test_passport_draft_extraction_returns_404(
    client: AsyncClient, created_extraction: dict, created_asset: dict, inst_headers: dict
):
    """
    Before the extraction is approved, the passport request returns 404.
    Draft information is never shown through the external API.
    """
    # created_extraction exists (draft) but there is no approved_extraction
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 404
    assert "approved" in r.json()["detail"].lower()


# ── 5. All legal fields are returned ──────────────────────────────────────────


async def test_passport_returns_all_seven_legal_rights(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    The approved passport contains all 7 legal fields.
    The mock AI extracts 4 known + 3 unknown fields.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    assert body["asset"]["asset_code"] == "MRPROP"
    assert body["asset"]["network"] == "stellar-mainnet"

    rights = body["legal_rights"]
    assert len(rights) == 7

    field_names = {right["field_name"] for right in rights}
    assert field_names == {
        "income_rights",
        "insolvency_provisions",
        "jurisdiction",
        "ownership_nature",
        "redemption_rights",
        "transfer_restrictions",
        "voting_rights",
    }


# ── 6. Unknown fields are labeled explicitly ─────────────────────────────────


async def test_passport_unknown_fields_clearly_labeled(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    For fields with is_unknown=True:
    - value == "unknown"
    - verified == False
    - supporting_passage == None
    For fields with is_unknown=False, verified==True (source evidence present).
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    rights = r.json()["legal_rights"]

    unknown_rights = [r for r in rights if r["is_unknown"]]
    known_rights = [r for r in rights if not r["is_unknown"]]

    assert len(unknown_rights) == 3  # voting_rights, redemption_rights, insolvency_provisions
    assert len(known_rights) == 4

    for u in unknown_rights:
        assert u["verified"] is False
        assert u["supporting_passage"] is None

    for k in known_rights:
        assert k["verified"] is True
        assert k["supporting_passage"] is not None


# ── 7. Approval metadata is present in the response ───────────────────────────


async def test_passport_includes_approval_metadata(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    The passport response:
    - approvals list populated (at least 1 approval)
    - extraction_metadata: passport_version, effective_date, ai_model
    - source_document: document title and version number
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    approvals = body["approvals"]
    assert len(approvals) >= 1
    assert approvals[0]["approval_type"] == "issuer_attestation"
    assert approvals[0]["approved_at"] is not None

    meta = body["extraction_metadata"]
    assert meta["passport_version"] == 1
    assert meta["ai_model"] == "mock-v1"
    assert meta["effective_date"] is not None

    doc_ref = body["source_document"]
    assert doc_ref["title"] == "Q4 2024 Prospectus"
    assert doc_ref["version_number"] == 1


# ── 8. Disclaimer mandatory in every response ─────────────────────────────────


async def test_passport_disclaimer_always_present(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    The disclaimer field is present in every passport response and
    states explicitly that it is not legally binding.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()
    disclaimer = body["disclaimer"]
    assert disclaimer
    # Key phrase stating that this is not legal advice
    assert "legal advice" in disclaimer.lower() or "legal counsel" in disclaimer.lower()


# ── 9. 429 is returned when the rate limit is exceeded ───────────────────────


async def test_passport_rate_limit_exceeded_returns_429(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    With a low-limit rate limiter, 429 Too Many Requests is returned once the limit is exceeded.
    """
    low_limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    app.dependency_overrides[get_rate_limiter] = lambda: low_limiter

    url = _passport_url(created_asset)
    r1 = await client.get(url, headers=inst_headers)
    assert r1.status_code == 200
    r2 = await client.get(url, headers=inst_headers)
    assert r2.status_code == 200
    # The third request exceeds the limit
    r3 = await client.get(url, headers=inst_headers)
    assert r3.status_code == 429


# ── 10. The passport request is written to the log ────────────────────────────


async def test_passport_request_is_logged(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
    caplog,
):
    """
    A successful passport request leaves an INFO record in the 'rwa_passport' logger.
    The record contains the client ID and the asset code.
    """
    with caplog.at_level(logging.INFO, logger="rwa_passport"):
        r = await client.get(_passport_url(created_asset), headers=inst_headers)

    assert r.status_code == 200
    log_messages = [record.message for record in caplog.records]
    # client_id must appear in at least one record
    assert any("meridian-capital-001" in msg for msg in log_messages)


# ── 11. PDF content cannot be downloaded through this endpoint ───────────────


async def test_passport_does_not_expose_document_download_url(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    The passport response contains only document metadata (title, type, version).
    Raw PDF content, a download link or the document text are not shown.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    doc_ref = body["source_document"]
    # Metadata fields only — no download link or content
    assert set(doc_ref.keys()) == {"document_id", "title", "document_type", "version_number"}
    assert "download_url" not in doc_ref
    assert "content" not in doc_ref
    assert "text" not in doc_ref
