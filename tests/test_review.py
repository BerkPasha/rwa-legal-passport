"""
Phase 5 — Review and approval workflow tests (15 tests).

Coverage:
 1. Authentication — unauthorized and invalid token
 2. start_review — successful transition, already-approved conflict, not found
 3. correct_claim — audit record, automatic under_review, approved-extraction block, not found
 4. approve_extraction — issuer and legal approval types, missing source evidence, AI ban, not found
 5. list_corrections / list_approvals — list verification after records are created
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction import LegalClaim
from sqlalchemy import select


def _same_evidence(claim: dict) -> dict:
    """A changed value needs explicit evidence (Stage 9B-4 step 7); here the claim's own, re-cited."""
    return {"corrected_source_page": claim["source_page"], "corrected_passage": claim["supporting_passage"]}


def _as_read(claim: dict) -> dict:
    """The mandatory correction precondition (Stage 9B-4 issue A): the claim exactly as read."""
    return {key: claim[key] for key in ("value", "is_unknown", "source_page", "supporting_passage")}


async def _patch_as_read(client: AsyncClient, claim: dict, url: str, **kwargs):
    """A correction stating `claim` as read; the request-body lines of the tests stay as they are."""
    kwargs["json"] = {**kwargs["json"], "expected_claim": _as_read(claim)}
    return await client.patch(url, **kwargs)


# ── 1. Authentication ─────────────────────────────────────────────────────────


async def test_start_review_unauthenticated(client: AsyncClient, created_extraction: dict):
    """
    When no Authorization header is sent at all, HTTPBearer returns 403.
    (FastAPI default behaviour: 403 if the header is missing, 401 if the token is invalid.)
    """
    eid = created_extraction["id"]
    r = await client.post(f"/extractions/{eid}/review")
    assert r.status_code == 403


async def test_start_review_invalid_token(client: AsyncClient, created_extraction: dict):
    """Invalid token → 401."""
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/review",
        headers={"Authorization": "Bearer gecersiz-token"},
    )
    assert r.status_code == 401


# ── 2. start_review ───────────────────────────────────────────────────────────


async def test_start_review_success(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """A draft extraction is moved under review; the response reflects the under_review status."""
    eid = created_extraction["id"]
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["status"] == "under_review"
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"


async def test_start_review_already_approved_conflict(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """An approved extraction cannot be moved back under review — 409 Conflict."""
    eid = created_extraction["id"]
    # Approve first
    approve_r = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert approve_r.status_code == 201
    # Then try to move it under review
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 409


async def test_start_review_not_found(client: AsyncClient, issuer_headers: dict):
    """Non-existent extraction → 404."""
    r = await client.post(
        "/extractions/00000000-0000-0000-0000-000000000000/review",
        headers=issuer_headers,
    )
    assert r.status_code == 404


# ── 3. correct_claim ──────────────────────────────────────────────────────────


async def test_correct_claim_success_audit_record(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    When a correction is made:
    - The audit record contains the original and the corrected values.
    - The response returns all fields correctly.
    """
    eid = created_extraction["id"]
    # In alphabetical order the first claim is 'income_rights' (is_unknown=False, has an original value)
    claim = created_extraction["claims"][0]
    cid = claim["id"]
    original_value = claim["value"]

    # A changed value needs its own evidence (Stage 9B-4 step 7): an authentic quotation from page 2
    payload = {
        "corrected_value": "Revised: distributions paid within thirty days after each quarter.",
        "is_unknown": False,
        "corrected_source_page": 2,
        "corrected_passage": "Distributions are paid within thirty days after the end of each calendar quarter.",
        "correction_note": "Prospektüs güncellemesine göre sabit getiri oranı belirlendi.",
    }
    r = await _patch_as_read(
        client, claim, f"/extractions/{eid}/claims/{cid}", headers=issuer_headers, json=payload
    )
    assert r.status_code == 200
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["claim_id"] == cid
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"
    assert body["original_value"] == original_value
    assert body["corrected_value"] == payload["corrected_value"]
    assert body["corrected_passage"] == payload["corrected_passage"]
    assert body["correction_note"] == payload["correction_note"]
    assert (body["original_source_page"], body["corrected_source_page"], body["page_history_recorded"]) == (1, 2, True)


async def test_correct_claim_auto_sets_under_review(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    When a draft extraction is corrected, its status automatically becomes 'under_review'.
    The get-extraction endpoint returns the updated status.
    """
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]
    assert created_extraction["status"] == "draft"

    r = await _patch_as_read(
        client, claim, f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Updated value.", "is_unknown": False, **_same_evidence(claim)},
    )
    assert r.status_code == 200, r.text

    get_r = await client.get(f"/extractions/{eid}", headers=issuer_headers)
    assert get_r.status_code == 200
    assert get_r.json()["status"] == "under_review"


async def test_correct_claim_approved_rejected(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """A claim of an approved extraction cannot be corrected — 409 Conflict."""
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]
    # Approve first
    await client.post(f"/extractions/{eid}/approvals", headers=issuer_headers, json={})

    r = await _patch_as_read(
        client, claim, f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Değişiklik denemesi.", "is_unknown": False},
    )
    assert r.status_code == 409


async def test_correct_claim_not_found(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Non-existent claim ID → 404."""
    eid = created_extraction["id"]
    r = await _patch_as_read(
        client, created_extraction["claims"][0], f"/extractions/{eid}/claims/00000000-0000-0000-0000-000000000000",
        headers=issuer_headers,
        json={"corrected_value": "Deneme.", "is_unknown": False},
    )
    assert r.status_code == 404


# ── 4. approve_extraction ─────────────────────────────────────────────────────


async def test_approve_issuer_success(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    Approval by the issuer reviewer:
    - approval_type = issuer_attestation
    - status = approved
    - disclaimer field populated
    """
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals",
        headers=issuer_headers,
        json={"notes": "Tüm alanlar ihraçcı belgesiyle uyumlu."},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["approval_type"] == "issuer_attestation"
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"
    assert "INTERNAL REVIEW ONLY" in body["disclaimer"]
    assert body["notes"] == "Tüm alanlar ihraçcı belgesiyle uyumlu."
    assert body["approved_at"] is not None

    # The extraction status must be approved
    get_r = await client.get(f"/extractions/{eid}", headers=issuer_headers)
    assert get_r.json()["status"] == "approved"


async def test_approve_legal_reviewer_success(
    client: AsyncClient,
    created_extraction: dict,
    legal_headers: dict,
):
    """
    Approval by the independent legal expert:
    - approval_type = legal_verification
    """
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals",
        headers=legal_headers,
        json={},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["approval_type"] == "legal_verification"
    assert body["reviewer_id"] == "bob-martinez-01"
    assert body["reviewer_role"] == "independent_legal_reviewer"


async def test_approve_missing_source_evidence(
    client: AsyncClient,
    async_db: AsyncSession,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    When the supporting_passage of a known claim is cleared,
    the approval returns 422 Unprocessable Entity.
    """
    eid = created_extraction["id"]

    # Delete the evidence field of a known claim directly in the DB
    result = await async_db.execute(
        select(LegalClaim).where(
            LegalClaim.extraction_id == eid,
            LegalClaim.is_unknown == False,  # noqa: E712
        ).limit(1)
    )
    claim = result.scalar_one()
    claim.supporting_passage = None
    await async_db.commit()

    r = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert r.status_code == 422
    assert "missing source evidence" in r.json()["detail"]


async def test_approve_ai_forbidden(
    client: AsyncClient,
    created_extraction: dict,
    ai_headers: dict,
):
    """A user whose user_id starts with 'ai-' cannot approve — 403 Forbidden."""
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals", headers=ai_headers, json={}
    )
    assert r.status_code == 403


async def test_approve_not_found(client: AsyncClient, issuer_headers: dict):
    """Non-existent extraction → 404."""
    r = await client.post(
        "/extractions/00000000-0000-0000-0000-000000000000/approvals",
        headers=issuer_headers,
        json={},
    )
    assert r.status_code == 404


# ── 5. list_corrections / list_approvals ─────────────────────────────────────


async def test_list_corrections_after_correction(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """After a correction is made, the list endpoint returns the records."""
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]

    # Correct
    r = await _patch_as_read(
        client, claim, f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Güncellenmiş değer.", "is_unknown": False, **_same_evidence(claim)},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/extractions/{eid}/corrections", headers=issuer_headers)
    assert r.status_code == 200
    corrections = r.json()
    assert len(corrections) == 1
    assert corrections[0]["claim_id"] == claim["id"]
    assert corrections[0]["corrected_value"] == "Güncellenmiş değer."


async def test_list_approvals_after_approval(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
):
    """
    After approvals by two different reviewers, the list endpoint returns both records.
    The approval types are distinct from each other.
    """
    eid = created_extraction["id"]

    await client.post(f"/extractions/{eid}/approvals", headers=issuer_headers, json={})
    await client.post(f"/extractions/{eid}/approvals", headers=legal_headers, json={})

    r = await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)
    assert r.status_code == 200
    approvals = r.json()
    assert len(approvals) == 2
    types = {a["approval_type"] for a in approvals}
    assert types == {"issuer_attestation", "legal_verification"}
