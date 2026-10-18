"""
Review and approval workflow endpoints.

POST /extractions/{id}/review          — Start the review (under_review), or a re-review of a stale approval
PATCH /extractions/{id}/claims/{cid}   — Correct a claim (immutable audit record; evidence rules of
                                         Stage 9B-4 step 7 in app/correction_rules.py)
POST /extractions/{id}/approvals       — Create an approval (source evidence required)
GET  /extractions/{id}/corrections     — Correction history
GET  /extractions/{id}/approvals       — Approval history with the approved snapshots

Security constraints:
- Every endpoint requires authentication and a role permission (Bearer token):
  review:start, claim:correct, approval:create, audit:read.
- A reviewer can only access extractions of the issuers within their scope (otherwise 404).
- The AI cannot approve its own findings (403 if user_id starts with "ai-").
- Approval does not guarantee legal validity — the disclaimer is mandatory.
- Before approval, every known claim needs verified source evidence (Stage 9B-4, step 4): its
  quotation is checked on the backend against the stored page text of exactly the version
  being approved (app/evidence_records.py). Evidence states from clients or the AI are never
  trusted. A verified quotation proves source-text presence only, not legal correctness.
- That page text must still be what the stored PDF contains (Stage 9B-4, step 5,
  app/source_integrity.py): the file's SHA-256 and its re-extracted page text must match the
  version record and the page records, otherwise the approval is refused (409).
- The evidence is bound to the approval in snapshot format 2: per claim its evidence status and
  the SHA-256 of its cited page text and quotation (app/approval_integrity.py).

Approval version integrity (Stage 9B-3, see app/approval_integrity.py):
- An approval is bound to the exact document version, evidence and material asset information.
- Only an extraction of the document's current version can be approved; approvals are never
  transferred to a newer version.
- A stale approval (asset information changed, evidence changed, ...) is restored only by a new
  approval after an explicit re-review (POST /review). Approval records are never modified.
- State-changing endpoints take the write lock before checking the state (no check/write race).
"""

import json
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal, security_logger
from app.approval_integrity import (
    ExtractionContext,
    bind_approval,
    competing_current_extractions,
    evaluate_approval,
    load_extraction_context,
    lock_extraction_for_update,
    stored_evidence,
    summarize_extraction,
)
from app.auth import require_permission
from app.authorization import get_extraction_for_principal
from app.db.session import get_db
from app.enums import (
    ApprovalRefusal,
    ApprovalType,
    AssetStatus,
    DocumentStatus,
    ExtractionApprovalState,
    ExtractionStatus,
    CorrectionRejection,
    ReviewerRole,
    SourceIntegrityFailure,
)
from app.correction_rules import RefusedCorrection, changed_since_read, resolve_correction
from app.evidence_records import ApprovalEvidenceFailure, approval_evidence_failures
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval, ReviewCorrection
from app.permissions import Permission
from app.schemas.review import (
    ApprovalCurrencyResponse,
    ApprovalRequest,
    ClaimCorrectionRequest,
    EvidenceSnapshotClaim,
    ReviewApprovalResponse,
    ReviewCorrectionResponse,
    ReviewStatusResponse,
)
from app.services.storage import StorageService, get_storage
from app.source_integrity import read_stored_pdf_text, source_integrity_failure

router = APIRouter(tags=["Legal Review"])

APPROVAL_DISCLAIMER = (
    "INTERNAL REVIEW ONLY — This approval reflects a reviewer's internal assessment "
    "and does NOT constitute legal certification, guarantee of legal validity, "
    "regulatory compliance confirmation, or a binding legal opinion under any jurisdiction. "
    "Independent qualified legal counsel must be consulted for legally binding conclusions."
)

_SUPERSEDED_APPROVAL = (
    "The extraction belongs to a superseded document version; approvals are accepted only "
    "for the current version."
)
_SUPERSEDED_REVIEW = (
    "The extraction belongs to a superseded document version; run a new extraction on the "
    "current version."
)


_UNVERIFIED_EVIDENCE = (
    "Approval rejected — missing source evidence for: {fields}. Every known claim needs a "
    "supporting quotation that appears on its cited page of this document version."
)


def _evidence_rejection(failures: list[ApprovalEvidenceFailure]) -> JSONResponse:
    """422 in the usual {"detail": str} form, plus the failed claims and their evidence status."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": _UNVERIFIED_EVIDENCE.format(
                fields=", ".join(sorted({f.field_name for f in failures}))
            ),
            "evidence_failures": [
                {"claim_id": f.claim_id, "field_name": f.field_name, "status": f.status.value}
                for f in failures
            ],
        },
    )


_SOURCE_INTEGRITY = (
    "Approval rejected — the stored source document of this version failed its integrity check "
    "({reason}). Approvals are not accepted for this document version."
)


def _source_integrity_rejection(reason: SourceIntegrityFailure) -> JSONResponse:
    """409: the stored file, the version record and the page records do not agree."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": _SOURCE_INTEGRITY.format(reason=reason.value),
            "source_integrity_failure": reason.value,
        },
    )


# Version of the correction audit record: 1 = source pages recorded (Stage 9B-4, step 7)
CORRECTION_EVIDENCE_AUDIT_VERSION = 1

_CORRECTION_REJECTED = {
    CorrectionRejection.evidence_required: (
        "Correction rejected — a changed value needs its own source evidence: send corrected_source_page "
        "and corrected_passage for the corrected value. The previous quotation and page are never reused "
        "for a new value."
    ),
    CorrectionRejection.unknown_with_evidence: (
        "Correction rejected — an unknown claim carries no source evidence: omit corrected_source_page and "
        "corrected_passage, or send them as null."
    ),
    CorrectionRejection.unknown_value_for_known_claim: (
        "Correction rejected — the value states that the field is unknown: send is_unknown: true instead."
    ),
    CorrectionRejection.evidence_not_verified: (
        "Correction rejected — the supporting quotation is not verified on its cited page of this document "
        "version ({status}). Verification proves source-text presence only, not legal correctness."
    ),
}


CLAIM_CHANGED = "claim_changed"
_CLAIM_CHANGED_DETAIL = (
    "Correction rejected — the claim changed after it was read ({fields}). Nothing was changed: "
    "reload the claim and review its current value and evidence before correcting it."
)


def _correction_conflict(fields: list[str]) -> JSONResponse:
    """409: the stored claim is no longer the state the reviewer read (expected_claim)."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={
            "detail": _CLAIM_CHANGED_DETAIL.format(fields=", ".join(fields)),
            "correction_conflict": CLAIM_CHANGED,
            "changed_fields": fields,
        },
    )


def _correction_rejection(refused: RefusedCorrection) -> JSONResponse:
    """422 in the usual {"detail": str} form, plus the rule and the evidence status."""
    status_value = refused.evidence_status.value if refused.evidence_status else None
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={
            "detail": _CORRECTION_REJECTED[refused.rule].format(status=status_value),
            "correction_rule": refused.rule.value,
            "evidence_status": status_value,
        },
    )


_APPROVAL_REFUSED = {
    ApprovalRefusal.duplicate_approval: (
        "Approval rejected — you already have a current approval of this extraction. A repeated "
        "approval by the same reviewer is never recorded as a further approval."
    ),
    ApprovalRefusal.self_review: (
        "Approval rejected — you made the most recent correction of at least one claim of this "
        "extraction. A legal approval of one's own correction is not an independent review: another "
        "legal reviewer must approve it."
    ),
    ApprovalRefusal.competing_extraction: (
        "Approval rejected — another extraction of this document version already has a current "
        "approval. Only one extraction per document version can be approved; a different "
        "extraction can be approved once that approval is no longer current (e.g. a new document "
        "version)."
    ),
}


def _approval_refusal(refusal: ApprovalRefusal, **details) -> JSONResponse:
    """409 in the usual {"detail": str} form, plus the machine-readable refusal (Stage 9B-5C)."""
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"detail": _APPROVAL_REFUSED[refusal], "approval_refusal": refusal.value, **details},
    )


def _approval_type_for_role(role: ReviewerRole) -> str:
    return {
        ReviewerRole.issuer_reviewer: ApprovalType.issuer_attestation.value,
        ReviewerRole.independent_legal_reviewer: ApprovalType.legal_verification.value,
    }[role]


async def _get_extraction_or_404(
    db: AsyncSession, extraction_id: str, reviewer: Principal
) -> LegalExtraction:
    """Loads the extraction; 404 if it does not exist or is outside the reviewer's issuer scope."""
    return await get_extraction_for_principal(
        db, extraction_id, reviewer, "Extraction not found."
    )


def _reviewer_role(reviewer: Principal) -> ReviewerRole:
    """Role for review records; review permissions exist only for reviewer roles."""
    role = reviewer.reviewer_role
    if role is None:  # ROLE_PERMISSIONS prevents this case; defensive check
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="You are not authorized to perform this operation."
        )
    return role


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


def _database_busy() -> HTTPException:
    security_logger.warning("review.database_busy")
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="Database is busy; please retry.",
    )


async def _locked_context(db: AsyncSession, extraction_id: str) -> ExtractionContext:
    """Ends the read transaction, takes the write lock, then loads the live state."""
    await db.commit()
    await lock_extraction_for_update(db, extraction_id)
    return await load_extraction_context(db, extraction_id)


_SNAPSHOT_CLAIM_FIELDS = tuple(EvidenceSnapshotClaim.model_fields)


def _evidence_views(approval: ReviewApproval) -> tuple[list[EvidenceSnapshotClaim] | None, int | None, object]:
    """
    (claims in the format 1 fields, snapshot format, stored snapshot) of an approval. A damaged
    snapshot is shown as stored, never repaired; its currency reports binding_integrity_failed.
    """
    fmt, stored = stored_evidence(approval)
    record = stored if isinstance(stored, (dict, list)) else None
    if fmt is None:
        return None, None, record
    claims = stored if fmt == 1 else stored["claims"]
    try:
        projected = [
            EvidenceSnapshotClaim.model_validate({k: c.get(k) for k in _SNAPSHOT_CLAIM_FIELDS}) for c in claims
        ]
    except (AttributeError, ValidationError):
        projected = None
    return projected, fmt, record


def build_approval_response(approval: ReviewApproval, ctx: ExtractionContext) -> ReviewApprovalResponse:
    currency = evaluate_approval(approval, ctx)
    evidence_claims, evidence_format, evidence_record = _evidence_views(approval)
    return ReviewApprovalResponse(
        id=approval.id,
        extraction_id=approval.extraction_id,
        reviewer_id=approval.reviewer_id,
        reviewer_role=approval.reviewer_role,
        approval_type=approval.approval_type,
        disclaimer=approval.disclaimer,
        notes=approval.notes,
        approved_at=approval.approved_at,
        created_at=approval.created_at,
        document_id=approval.document_id,
        document_version_id=approval.document_version_id,
        document_version_number=approval.document_version_number,
        document_sha256=approval.document_sha256,
        evidence_digest=approval.evidence_digest,
        evidence_snapshot=evidence_claims,
        evidence_snapshot_format=evidence_format,
        evidence_record=evidence_record,
        asset_material_revision=approval.asset_material_revision,
        asset_snapshot_digest=approval.asset_snapshot_digest,
        asset_snapshot=json.loads(approval.asset_snapshot) if approval.asset_snapshot else None,
        binding_digest=approval.binding_digest,
        currency=ApprovalCurrencyResponse(is_current=currency.is_current, reasons=currency.reasons),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/extractions/{extraction_id}/review",
    response_model=ReviewStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Start the review — the status is updated to 'under_review'",
    description=(
        "Starts the review of a draft. For an approved extraction whose approvals are all stale "
        "(for example after a material asset change), this starts the re-review that is required "
        "before a new, current approval can be given. Approval records are never modified."
    ),
)
async def start_review(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: Principal = Depends(require_permission(Permission.review_start)),
) -> ReviewStatusResponse:
    reviewer_role = _reviewer_role(reviewer)
    await _get_extraction_or_404(db, extraction_id, reviewer)

    try:
        ctx = await _locked_context(db, extraction_id)
        extraction = ctx.extraction
        if extraction.status == ExtractionStatus.approved.value:
            summary = summarize_extraction(ctx)
            if not ctx.version_is_current:
                await db.rollback()
                raise _conflict(_SUPERSEDED_REVIEW)
            if summary.state == ExtractionApprovalState.current:
                await db.rollback()
                raise _conflict("An approved extraction cannot be returned to review.")
            security_logger.info(
                "review.reopened extraction=%s user=%s reasons=%s",
                extraction.id, reviewer.user_id, ",".join(r.value for r in summary.reasons),
            )
        extraction.status = ExtractionStatus.under_review.value
        await db.commit()
    except OperationalError:
        await db.rollback()
        raise _database_busy()

    return ReviewStatusResponse(
        extraction_id=extraction.id,
        status=ExtractionStatus(extraction.status),
        reviewer_id=reviewer.user_id,
        reviewer_role=reviewer_role,
    )


@router.patch(
    "/extractions/{extraction_id}/claims/{claim_id}",
    response_model=ReviewCorrectionResponse,
    status_code=status.HTTP_200_OK,
    summary="Correct a claim — the state before the change is written to an immutable audit record",
    description=(
        "Evidence rules (Stage 9B-4, step 7): an omitted `corrected_source_page` / "
        "`corrected_passage` keeps the current evidence only while the value and the unknown flag "
        "are unchanged; null clears it. A changed value (or an unknown claim made known) needs both "
        "for the new value: the previous quotation and page are never reused. The resulting "
        "quotation of a known claim must be verified on its cited page of the extraction's document "
        "version. A claim corrected to unknown carries no evidence and its value becomes "
        "'unknown'. Otherwise 422 with `correction_rule` and, if applicable, `evidence_status`; "
        "nothing is changed. The audit record keeps the value, quotation and page before and after.\n\n"
        "Optimistic concurrency (Stage 9B-4, issue A): `expected_claim` is **required**: the claim's "
        "value, is_unknown, source_page and supporting_passage exactly as read. If the stored claim "
        "differs, the correction is refused with 409 (`correction_conflict: claim_changed`, "
        "`changed_fields`) and nothing changes. A request without it, or with null or an incomplete "
        "state, is invalid (422). Reload the claim after a 409; never retry with a newly fetched state "
        "without reviewing it."
    ),
    responses={
        409: {"description": "The extraction is approved, or the claim changed after it was read (expected_claim)."},
    },
)
async def correct_claim(
    extraction_id: str,
    claim_id: str,
    body: ClaimCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    reviewer: Principal = Depends(require_permission(Permission.claim_correct)),
) -> ReviewCorrectionResponse:
    reviewer_role = _reviewer_role(reviewer)
    await _get_extraction_or_404(db, extraction_id, reviewer)

    try:
        ctx = await _locked_context(db, extraction_id)
        extraction = ctx.extraction
        if extraction.status == ExtractionStatus.approved.value:
            await db.rollback()
            raise _conflict("An approved extraction cannot be corrected.")

        claim = next((c for c in ctx.claims if c.id == claim_id), None)
        if claim is None:
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Claim not found."
            )

        # Optimistic concurrency (issue A, mandatory): the claim must still be what the reviewer read,
        # compared under the write lock, so an omitted page or quotation never keeps unseen evidence
        stale = changed_since_read(claim, body.expected_claim)
        if stale:
            await db.rollback()
            security_logger.warning(
                "correction.conflict extraction=%s claim=%s user=%s fields=%s",
                extraction_id, claim_id, reviewer.user_id, ",".join(stale),
            )
            return _correction_conflict(stale)

        # Evidence rules: the effective value and evidence, checked against the page text of the
        # extraction's own version as loaded under the write lock (app/correction_rules.py)
        outcome = resolve_correction(claim, body, extraction.version_id, ctx.source)
        if isinstance(outcome, RefusedCorrection):
            await db.rollback()
            security_logger.warning(
                "correction.rejected extraction=%s claim=%s user=%s rule=%s status=%s",
                extraction_id, claim_id, reviewer.user_id, outcome.rule.value,
                outcome.evidence_status.value if outcome.evidence_status else "-",
            )
            return _correction_rejection(outcome)

        # Audit record — the state before and after the change is stored immutably
        correction = ReviewCorrection(
            id=str(uuid.uuid4()),
            extraction_id=extraction_id,
            claim_id=claim_id,
            reviewer_id=reviewer.user_id,
            reviewer_role=reviewer_role.value,
            original_value=claim.value,
            original_is_unknown=claim.is_unknown,
            original_passage=claim.supporting_passage,
            original_source_page=claim.source_page,
            corrected_value=outcome.value,
            corrected_is_unknown=outcome.is_unknown,
            corrected_passage=outcome.supporting_passage,
            corrected_source_page=outcome.source_page,
            evidence_audit_version=CORRECTION_EVIDENCE_AUDIT_VERSION,
            correction_note=body.correction_note,
        )
        db.add(correction)

        # The claim takes the effective value and evidence; nothing of the previous evidence is kept
        claim.value = outcome.value
        claim.is_unknown = outcome.is_unknown
        claim.source_page = outcome.source_page
        claim.supporting_passage = outcome.supporting_passage

        # If still a draft, automatically move it under review
        if extraction.status == ExtractionStatus.draft.value:
            extraction.status = ExtractionStatus.under_review.value

        await db.commit()
    except OperationalError:
        await db.rollback()
        raise _database_busy()
    except BaseException:
        # Nothing of a failed correction may remain: neither the audit record nor the claim change
        await db.rollback()
        raise
    await db.refresh(correction)

    return ReviewCorrectionResponse.model_validate(correction)


@router.post(
    "/extractions/{extraction_id}/approvals",
    response_model=ReviewApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Approve an extraction — source evidence is mandatory for every known claim",
    description=(
        "The approval is bound to the exact document version (SHA-256), the evidence snapshot and "
        "the material asset information. Only an extraction of the document's current version of an "
        "active asset can be approved. An approved extraction whose approvals are all stale needs a "
        "re-review (POST /review) before it can be approved again.\n\n"
        "Every known claim must have verified source evidence: its supporting quotation must appear "
        "on its cited page of the stored text of this document version (checked on the server, "
        "never taken from the client or the AI). Otherwise the approval is rejected with 422: "
        "`detail` names the fields and `evidence_failures` lists `claim_id`, `field_name` and the "
        "evidence `status` of each failing claim. Verification proves source-text presence only, "
        "not legal correctness.\n\n"
        "The stored PDF of the version must match its recorded SHA-256 and its re-extracted page "
        "text must match the stored page text; otherwise the approval is refused with 409 and "
        "`source_integrity_failure` names the reason. The evidence is bound in snapshot format 2 "
        "(`evidence_record`): per claim the evidence status and the SHA-256 of the cited page "
        "text and of the quotation.\n\n"
        "Refused with 409 and `approval_refusal` (Stage 9B-5C), storing nothing: "
        "`duplicate_approval` (the reviewer already has a current approval of this extraction), "
        "`self_review` (a legal reviewer made the most recent correction of a claim; another legal "
        "reviewer must approve), `competing_extraction` (another extraction of this document version "
        "already has a current approval; `competing_extraction_ids` names it). The request body "
        "accepts `notes` only; the reviewer identity and approval type always come from the "
        "authenticated token."
    ),
)
async def approve_extraction(
    extraction_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    reviewer: Principal = Depends(require_permission(Permission.approval_create)),
    storage: StorageService = Depends(get_storage),
) -> ReviewApprovalResponse:
    # Code layer: the AI cannot approve its own findings
    if reviewer.user_id.startswith("ai-"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The AI service cannot approve extractions.",
        )
    # The approval type is derived from the authenticated role; the client cannot choose it
    reviewer_role = _reviewer_role(reviewer)

    scoped = await _get_extraction_or_404(db, extraction_id, reviewer)
    # The stored PDF of the extraction's version is read, hash-checked and re-extracted before
    # the write lock (it can be large); it is compared with the live records under the lock.
    stored_pdf = await read_stored_pdf_text(db, storage, scoped.version_id)

    try:
        # Every check below runs on the live state, under the write lock
        ctx = await _locked_context(db, extraction_id)
        extraction = ctx.extraction

        if not ctx.version_is_current or ctx.extraction_version is None:
            await db.rollback()
            security_logger.warning(
                "approval.rejected_superseded extraction=%s user=%s", extraction_id, reviewer.user_id
            )
            raise _conflict(_SUPERSEDED_APPROVAL)
        if ctx.document.status != DocumentStatus.active.value:
            await db.rollback()
            raise _conflict("Approvals are not accepted for an inactive document.")
        if ctx.asset.status != AssetStatus.active.value:
            await db.rollback()
            raise _conflict("Approvals are not accepted while the asset is not active.")
        summary = summarize_extraction(ctx)
        if (
            extraction.status == ExtractionStatus.approved.value
            and summary.state != ExtractionApprovalState.current
        ):
            await db.rollback()
            raise _conflict(
                "The existing approval is no longer current; start a re-review before approving again."
            )

        # Stage 9B-5C: one current approval per reviewer, no legal approval of one's own
        # correction, and one approved extraction per document version (issue B5)
        refusal, details = None, {}
        if any(a.reviewer_id == reviewer.user_id for a in summary.current_approvals):
            refusal = ApprovalRefusal.duplicate_approval
        elif reviewer_role is ReviewerRole.independent_legal_reviewer and ctx.authored_latest_correction(
            reviewer.user_id
        ):
            refusal = ApprovalRefusal.self_review
        else:
            competing = await competing_current_extractions(db, extraction)
            if competing:
                refusal, details = ApprovalRefusal.competing_extraction, {"competing_extraction_ids": competing}
        if refusal is not None:
            await db.rollback()
            security_logger.warning(
                "approval.rejected_%s extraction=%s user=%s%s", refusal.value, extraction_id, reviewer.user_id,
                f" competing={','.join(details['competing_extraction_ids'])}" if details else "",
            )
            return _approval_refusal(refusal, **details)

        # The page text loaded under the lock must be what the stored PDF contains (step 5)
        integrity = source_integrity_failure(stored_pdf, ctx.extraction_version, ctx.source)
        if integrity is not None:
            version_id = ctx.extraction_version.id  # read before the rollback expires the objects
            await db.rollback()
            security_logger.error(
                "approval.rejected_source_integrity extraction=%s version=%s user=%s reason=%s",
                extraction_id, version_id, reviewer.user_id, integrity.value,
            )
            return _source_integrity_rejection(integrity)

        # Mandatory source-evidence gate: every known claim, as corrected, must be verified
        # against the page text of exactly the version being approved. Loaded under the write
        # lock, so no upload, correction or concurrent approval can change it before the commit.
        failures = approval_evidence_failures(ctx.claims, ctx.extraction_version.id, ctx.source)
        if failures:
            await db.rollback()
            security_logger.warning(
                "approval.rejected_unverified_evidence extraction=%s user=%s claims=%s",
                extraction_id, reviewer.user_id,
                ",".join(f"{f.field_name}:{f.status.value}" for f in failures),
            )
            return _evidence_rejection(failures)

        now = datetime.now(timezone.utc)
        approval = ReviewApproval(
            id=str(uuid.uuid4()),
            extraction_id=extraction_id,
            reviewer_id=reviewer.user_id,
            reviewer_role=reviewer_role.value,
            approval_type=_approval_type_for_role(reviewer_role),
            disclaimer=APPROVAL_DISCLAIMER,
            notes=body.notes,
            approved_at=now,
            created_at=now,
        )
        bind_approval(approval, ctx)
        db.add(approval)

        extraction.status = ExtractionStatus.approved.value
        await db.commit()
    except OperationalError:
        await db.rollback()
        raise _database_busy()
    except BaseException:
        # Nothing of a failed approval may remain: neither the record nor the status change
        await db.rollback()
        raise

    security_logger.info(
        "approval.created extraction=%s approval=%s type=%s version=%d asset_revision=%d binding=%s",
        extraction_id, approval.id, approval.approval_type, approval.document_version_number,
        approval.asset_material_revision, approval.binding_digest[:12],
    )
    ctx = await load_extraction_context(db, extraction_id)
    created = next(a for a in ctx.approvals if a.id == approval.id)
    return build_approval_response(created, ctx)


@router.get(
    "/extractions/{extraction_id}/corrections",
    response_model=list[ReviewCorrectionResponse],
    summary="List the correction history of an extraction",
)
async def list_corrections(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: Principal = Depends(require_permission(Permission.audit_read)),
) -> list[ReviewCorrectionResponse]:
    await _get_extraction_or_404(db, extraction_id, reviewer)

    result = await db.execute(
        select(ReviewCorrection)
        .where(ReviewCorrection.extraction_id == extraction_id)
        .order_by(ReviewCorrection.created_at)
    )
    return [ReviewCorrectionResponse.model_validate(c) for c in result.scalars().all()]


@router.get(
    "/extractions/{extraction_id}/approvals",
    response_model=list[ReviewApprovalResponse],
    summary="List the approval history of an extraction",
    description=(
        "Every approval ever given, including stale and superseded ones, with the exact document "
        "version, evidence and asset information it was bound to, and whether it is still current."
    ),
)
async def list_approvals(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: Principal = Depends(require_permission(Permission.audit_read)),
) -> list[ReviewApprovalResponse]:
    await _get_extraction_or_404(db, extraction_id, reviewer)

    ctx = await load_extraction_context(db, extraction_id)
    return [build_approval_response(a, ctx) for a in ctx.approvals]
