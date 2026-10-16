"""
AI legal-rights extraction endpoints.

POST /documents/{document_id}/extractions   — Trigger extraction with the AI (draft)
GET  /documents/{document_id}/extractions   — Extraction history of a document
GET  /extractions/{extraction_id}           — Get a single extraction

Important constraints:
- No endpoint performs automatic approval; status always remains "draft".
- Every response carries approval_state: whether the extraction's approvals are current,
  stale or superseded by a newer document version (Stage 9B-3).
- Every claim carries `evidence`: the deterministic check of its quotation against the page
  text of the extraction's own document version (Stage 9B-4, app/evidence_records.py). It
  proves source-text presence only and does not affect approval or the passport.
- The output of every AI service, real or mocked, passes validate_ai_claims before anything is
  stored (Stage 9B-4, step 3). An invalid response (e.g. duplicate legal fields) is rejected
  with 503 and stores nothing; the extraction and its claims are written in one transaction.
- The AI cannot confirm legal validity.
- Every response includes the disclaimer.
- Triggering requires the extraction:run permission and is rate-limited per user
  (paid AI API cost control); reading requires the extraction:read permission.
- All access is limited to the issuer (tenant) scope.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal, security_logger
from app.approval_integrity import load_extraction_context, summarize_extraction
from app.auth import require_permission
from app.authorization import get_document_for_principal, get_extraction_for_principal
from app.db.session import get_db
from app.evidence_records import ExtractionSourceText, verify_claim_evidence
from app.models.document import DocumentPage, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.permissions import Permission
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter
from app.schemas.extraction import (
    ClaimEvidenceResponse,
    ExtractionApprovalStateResponse,
    LegalClaimResponse,
    LegalExtractionResponse,
)
from app.services.ai import AIOutputValidationError, AnthropicAIService, get_ai_service, validate_ai_claims

router = APIRouter(tags=["Legal Extractions"])

_DOC_NOT_FOUND = "Document not found."
_EXTRACTION_NOT_FOUND = "Extraction not found."


# ── Helper ───────────────────────────────────────────────────────────────────


def _claim_response(
    claim: LegalClaim, extraction: LegalExtraction, source: ExtractionSourceText
) -> LegalClaimResponse:
    check = verify_claim_evidence(claim, extraction, source)
    return LegalClaimResponse(
        id=claim.id,
        extraction_id=claim.extraction_id,
        field_name=claim.field_name,
        value=claim.value,
        is_unknown=claim.is_unknown,
        source_page=claim.source_page,
        supporting_passage=claim.supporting_passage,
        created_at=claim.created_at,
        evidence=ClaimEvidenceResponse(
            status=check.status,
            cited_page=check.cited_page,
            found_on_pages=list(check.found_on_pages),
            page_count=check.page_count,
            version_number=source.version_number,
            verification_method=check.method,
            normalization_version=check.normalization_version,
            scope=check.scope,
            explanation=check.explanation,
        ),
    )


async def _load_extraction_response(
    db: AsyncSession, extraction: LegalExtraction
) -> LegalExtractionResponse:
    """
    Loads all claims from the DB and builds a LegalExtractionResponse with its approval state
    and the evidence check of every claim against the extraction's own document version.
    """
    ctx = await load_extraction_context(db, extraction.id)
    summary = summarize_extraction(ctx)
    source = ctx.source  # the page text of the extraction's own version only
    claims_result = await db.execute(
        select(LegalClaim)
        .where(LegalClaim.extraction_id == extraction.id)
        .order_by(LegalClaim.field_name)
    )
    claims = [_claim_response(c, extraction, source) for c in claims_result.scalars().all()]

    return LegalExtractionResponse(
        id=extraction.id,
        document_id=extraction.document_id,
        version_id=extraction.version_id,
        status=extraction.status,
        ai_model=extraction.ai_model,
        disclaimer=extraction.disclaimer,
        extraction_timestamp=extraction.extraction_timestamp,
        created_at=extraction.created_at,
        claims=claims,
        approval_state=ExtractionApprovalStateResponse(
            state=summary.state,
            version_is_current=summary.version_is_current,
            current_approval_types=summary.current_approval_types,
            reasons=summary.reasons,
        ),
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/extractions",
    response_model=LegalExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Extract legal rights with the AI — the result is always a draft",
)
async def trigger_extraction(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.extraction_run)),
    limiter: InMemoryRateLimiter = Depends(get_extraction_rate_limiter),
    db: AsyncSession = Depends(get_db),
    ai_service: AnthropicAIService = Depends(get_ai_service),
) -> LegalExtractionResponse:
    # ── 1. Document (issuer scope) and current version ───────────────────────
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    # Per-user rate limit before the paid AI call
    if not limiter.is_allowed(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI extraction rate limit exceeded. Please try again later.",
        )

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found."
        )

    # ── 2. Fetch the page texts ──────────────────────────────────────────────
    pages_result = await db.execute(
        select(DocumentPage)
        .where(DocumentPage.version_id == current_version.id)
        .order_by(DocumentPage.page_number)
    )
    pages = [
        {"page_number": p.page_number, "text_content": p.text_content}
        for p in pages_result.scalars().all()
    ]

    # ── 3. AI call and output validation (the same gate for every AI service) ─
    try:
        ai_result = await ai_service.extract_legal_rights(
            document_id=document_id,
            version_id=current_version.id,
            pages=pages,
        )
        claims = validate_ai_claims(ai_result.claims)
    except RuntimeError as exc:
        if isinstance(exc, AIOutputValidationError):
            security_logger.warning(
                "extraction.ai_output_rejected document=%s user=%s", document_id, principal.user_id
            )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # ── 4. Save the results — always "draft", in one transaction ─────────────
    extraction_id = str(uuid.uuid4())
    extraction = LegalExtraction(
        id=extraction_id,
        document_id=document_id,
        version_id=current_version.id,
        status="draft",                         # automatic approval is never performed
        ai_model=ai_result.ai_model,
        disclaimer=ai_result.disclaimer,
        extraction_timestamp=ai_result.extraction_timestamp,
    )
    try:
        db.add(extraction)
        await db.flush()

        for claim in claims:
            db.add(
                LegalClaim(
                    id=str(uuid.uuid4()),
                    extraction_id=extraction_id,
                    field_name=claim.field.value,
                    value=claim.value,
                    is_unknown=claim.is_unknown,
                    source_page=claim.source.page_number if claim.source else None,
                    supporting_passage=claim.source.supporting_passage if claim.source else None,
                )
            )

        await db.commit()
    except BaseException:
        # Nothing of a failed save may remain: neither the extraction nor some of its claims
        await db.rollback()
        raise
    await db.refresh(extraction)

    return await _load_extraction_response(db, extraction)


@router.get(
    "/documents/{document_id}/extractions",
    response_model=list[LegalExtractionResponse],
    summary="List the AI extraction history of a document",
)
async def list_extractions(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.extraction_read)),
    db: AsyncSession = Depends(get_db),
) -> list[LegalExtractionResponse]:
    await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    result = await db.execute(
        select(LegalExtraction)
        .where(LegalExtraction.document_id == document_id)
        .order_by(LegalExtraction.created_at.desc())
    )
    return [await _load_extraction_response(db, e) for e in result.scalars().all()]


@router.get(
    "/extractions/{extraction_id}",
    response_model=LegalExtractionResponse,
    summary="Get extraction details with all claims",
)
async def get_extraction(
    extraction_id: str,
    principal: Principal = Depends(require_permission(Permission.extraction_read)),
    db: AsyncSession = Depends(get_db),
) -> LegalExtractionResponse:
    extraction = await get_extraction_for_principal(
        db, extraction_id, principal, _EXTRACTION_NOT_FOUND
    )
    return await _load_extraction_response(db, extraction)
