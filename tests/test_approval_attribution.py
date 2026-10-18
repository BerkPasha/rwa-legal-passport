"""
Stage 9B-5C C1 — approval attribution hardening.

An approval is current only for the exact extraction, document, document version and claims it
was bound to. evaluate_approval compares the approval's own identity columns with the live
context loaded from the database (extraction → document → version, claims by extraction), never
with values the approval states about itself. A mismatch, or missing identity context, is
binding_integrity_failed; nothing is assumed to match.

The binding digest is an unkeyed SHA-256: someone with database write access can recompute it.
These tests therefore also forge identity columns together with a recomputed digest (the threat
model of the known limitation C); every such forgery that changes an identity is refuted by the
live records. A complete forgery that rewrites the evidence snapshot for another extraction as
well remains undetectable without a keyed signature (limitation C, not addressed here).

Coverage:
 1. Valid same-extraction issuer and legal approvals stay current
 2. Forged document identity (R7b), with and without a recomputed digest
 3. Forged document version number (format 1 and format 2)
 4. Cross-extraction substitution (moved record, or a context of another extraction)
 5. Mismatched source binding (pre-existing protection)
 6. Missing identity context
"""

import dataclasses
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.approval_integrity import (
    binding_payload,
    canonical_json,
    evaluate_approval,
    load_extraction_context,
    sha256_digest,
)
from app.enums import ApprovalStaleReason
from app.models.document import Document, DocumentVersion
from app.models.review import ReviewApproval
from tests.test_approval_evidence_gate import _approve, _passport_url, _upload
from tests.test_evidence_binding import _bind_format_1, _exec, _history
from tests.test_passport_verification import _approved, _passport

INTEGRITY = ApprovalStaleReason.binding_integrity_failed


async def _row(db: AsyncSession, approval_id: str) -> ReviewApproval:
    return (await db.execute(select(ReviewApproval).where(ReviewApproval.id == approval_id)
                             .execution_options(populate_existing=True))).scalar_one()


async def _rebind(db: AsyncSession, approval_id: str, rewrite_snapshot=None, **columns) -> None:
    """Rewrites binding columns AND recomputes every digest, as someone with database write access could."""
    row = await _row(db, approval_id)
    for name, value in columns.items():
        setattr(row, name, value)
    if rewrite_snapshot is not None:
        record = rewrite_snapshot(json.loads(row.evidence_snapshot))
        row.evidence_snapshot = canonical_json(record)
        row.evidence_digest = sha256_digest(record)
    row.binding_digest = sha256_digest(binding_payload(row))
    await db.commit()


async def _approval_of(client: AsyncClient, extraction_id: str, headers: dict, approval_type: str) -> dict:
    return next(a for a in await _history(client, extraction_id, headers) if a["approval_type"] == approval_type)


async def _currency(db: AsyncSession, approval_id: str, ctx=None):
    row = await _row(db, approval_id)
    return evaluate_approval(row, ctx or await load_extraction_context(db, row.extraction_id))


# ── 1. Valid same-extraction approvals ───────────────────────────────────────


async def test_valid_issuer_and_legal_approvals_of_the_same_extraction_stay_current(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    history = await _history(client, created_extraction["id"], issuer_headers)
    assert sorted(a["approval_type"] for a in history) == ["issuer_attestation", "legal_verification"]
    assert all(a["currency"] == {"is_current": True, "reasons": []} for a in history)
    for approval in history:
        currency = await _currency(async_db, approval["id"])
        assert (currency.is_current, currency.reasons) == (True, [])
    summary = (await _passport(client, created_asset, inst_headers))["verification_summary"]
    assert (summary["issuer_attestation_recorded"], summary["legal_reviewer_approval_recorded"]) == (True, True)


async def test_valid_format_1_legacy_approval_stays_current(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, async_db: AsyncSession,
):
    approval_id = await _bind_format_1(async_db, created_extraction["id"])
    currency = await _currency(async_db, approval_id)
    assert (currency.is_current, currency.reasons) == (True, [])


# ── 2. Forged document identity (R7b) ────────────────────────────────────────


@pytest.mark.parametrize("recompute", [True, False], ids=["digest_recomputed", "digest_unchanged"])
@pytest.mark.parametrize("target", ["other_document", "missing_document"])
async def test_an_approval_bound_to_another_document_is_never_current(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
    target: str, recompute: bool,
):
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    other = (await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated"))["id"]
    forged = other if target == "other_document" else "missing-document"
    legal = await _approval_of(client, created_extraction["id"], legal_headers, "legal_verification")
    if recompute:
        await _rebind(async_db, legal["id"], document_id=forged)
    else:
        await _exec(async_db, "UPDATE review_approvals SET document_id = :d WHERE id = :a", d=forged, a=legal["id"])

    legal = await _approval_of(client, created_extraction["id"], legal_headers, "legal_verification")
    assert legal["currency"] == {"is_current": False, "reasons": ["binding_integrity_failed"]}
    body = await _passport(client, created_asset, inst_headers)
    # Only the untouched issuer attestation counts; the forged document id is never shown
    assert [(a["approval_type"], a["document_id"]) for a in body["approvals"]] == [
        ("issuer_attestation", created_document["id"])]
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is False
    assert forged not in str(body)


async def test_the_only_approval_bound_to_another_document_serves_no_passport(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approved(client, created_extraction, legal_headers)
    other = (await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated"))["id"]
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    await _rebind(async_db, legal["id"], document_id=other)
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}


# ── 3. Forged document version number ────────────────────────────────────────


async def test_a_consistently_forged_version_number_is_refuted_by_the_live_version(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, async_db: AsyncSession,
):
    """Format 2: the approval AND its snapshot claim version 7; the live version is 1."""
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    await _rebind(async_db, approval["id"], document_version_number=7,
                  rewrite_snapshot=lambda r: {**r, "source": {**r["source"], "version_number": 7}})
    (approval,) = await _history(client, approved_extraction["id"], issuer_headers)
    assert approval["currency"] == {"is_current": False, "reasons": ["binding_integrity_failed"]}
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409


async def test_a_forged_version_number_of_a_format_1_approval_is_refuted(
    client: AsyncClient, created_extraction: dict, async_db: AsyncSession,
):
    """Format 1 records no source block: only the live version can refute the approval's number."""
    approval_id = await _bind_format_1(async_db, created_extraction["id"])
    await _rebind(async_db, approval_id, document_version_number=7)
    currency = await _currency(async_db, approval_id)
    assert (currency.is_current, currency.reasons) == (False, [INTEGRITY])


# ── 4. Cross-extraction substitution ─────────────────────────────────────────


async def test_an_approval_is_never_current_in_the_context_of_another_extraction(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession,
):
    """X's approval evaluated with Y's live context (same version), whatever the caller passes."""
    await _approved(client, created_extraction, legal_headers)
    y = (await client.post(f"/documents/{created_document['id']}/extractions", headers=issuer_headers)).json()
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    x_ctx = await load_extraction_context(async_db, created_extraction["id"])
    y_ctx = await load_extraction_context(async_db, y["id"])

    # Y's whole context, or only Y's extraction record with X's own claims: both are refused
    for ctx in (y_ctx, dataclasses.replace(x_ctx, extraction=y_ctx.extraction)):
        currency = await _currency(async_db, legal["id"], ctx)
        assert currency.is_current is False and INTEGRITY in currency.reasons


async def test_a_moved_approval_record_never_becomes_current_for_another_extraction(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approved(client, created_extraction, legal_headers)
    # Y: a second extraction of the same version (approving it is refused since Stage 9B-5C)
    y = (await client.post(f"/documents/{created_document['id']}/extractions", headers=issuer_headers)).json()
    assert (await _approve(client, y["id"], issuer_headers)).status_code == 409
    (legal,) = await _history(client, created_extraction["id"], legal_headers)

    # Moved, then moved with a recomputed binding digest: X's bound claims refute it in Y
    await _exec(async_db, "UPDATE review_approvals SET extraction_id = :y WHERE id = :a", y=y["id"], a=legal["id"])
    await _exec(async_db, "UPDATE legal_extractions SET status = 'approved' WHERE id = :y", y=y["id"])
    moved = await _approval_of(client, y["id"], legal_headers, "legal_verification")
    assert moved["currency"]["is_current"] is False and "binding_integrity_failed" in moved["currency"]["reasons"]
    await _rebind(async_db, legal["id"])
    moved = await _approval_of(client, y["id"], legal_headers, "legal_verification")
    assert moved["currency"] == {"is_current": False, "reasons": ["evidence_changed"]}
    # Neither X (no approval left) nor Y (only the refuted one) is served
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}


# ── 5. Mismatched source binding (pre-existing protection) ───────────────────


@pytest.mark.parametrize("column", ["document_version_id", "document_sha256"])
async def test_an_approval_bound_to_another_version_or_file_is_never_current(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession, column: str,
):
    await _approved(client, created_extraction, legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated")
    forged = other["current_version"]["id"] if column == "document_version_id" else "0" * 64
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    await _rebind(async_db, legal["id"], **{column: forged})
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    assert legal["currency"]["is_current"] is False and "binding_integrity_failed" in legal["currency"]["reasons"]


# ── 6. Missing identity context ──────────────────────────────────────────────


@pytest.mark.parametrize("missing", ["extraction_version", "version_of_another_document", "document",
                                     "document_named_by_a_forged_binding"])
async def test_missing_or_foreign_identity_context_is_never_assumed_to_match(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, async_db: AsyncSession, missing: str,
):
    await _approved(client, created_extraction, legal_headers)
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    ctx = await load_extraction_context(async_db, created_extraction["id"])
    if missing == "extraction_version":
        # e.g. the extraction's version row is missing or belongs to another document
        ctx = dataclasses.replace(ctx, extraction_version=None)
    elif missing == "version_of_another_document":
        # Same bytes, same version number, another document: only its id tells it apart
        other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated")
        foreign = (await async_db.execute(
            select(DocumentVersion).where(DocumentVersion.id == other["current_version"]["id"]))).scalar_one()
        assert (foreign.version_number, foreign.sha256_hash) == (
            ctx.extraction_version.version_number, ctx.extraction_version.sha256_hash)
        ctx = dataclasses.replace(ctx, extraction_version=foreign)
    else:
        other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated")
        foreign = (await async_db.execute(select(Document).where(Document.id == other["id"]))).scalar_one()
        ctx = dataclasses.replace(ctx, document=foreign)
        if missing == "document_named_by_a_forged_binding":
            # The approval and the supplied document agree; the extraction's own document does not
            await _rebind(async_db, legal["id"], document_id=other["id"])
    currency = await _currency(async_db, legal["id"], ctx)
    assert currency.is_current is False and INTEGRITY in currency.reasons


async def test_an_extraction_pointing_at_another_documents_version_is_detected(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    await _approved(client, created_extraction, legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Unrelated")
    await _exec(async_db, "UPDATE legal_extractions SET version_id = :v WHERE id = :e",
                v=other["current_version"]["id"], e=created_extraction["id"])
    (legal,) = await _history(client, created_extraction["id"], legal_headers)
    assert legal["currency"]["is_current"] is False and "binding_integrity_failed" in legal["currency"]["reasons"]
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 409
