"""
AI legal-rights extraction endpoints.

POST /documents/{document_id}/extractions          — Trigger extraction with the AI (draft)
POST /documents/{document_id}/manual-extractions   — Create claims manually, without AI (draft; Stage 10B-3)
GET  /documents/{document_id}/extractions          — Extraction history of a document
GET  /extractions/{extraction_id}                  — Get a single extraction

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
- A real AI service (requires_text_layer) is not called for a document version without any
  extractable text: 422 no_extractable_text, no request, nothing stored (Stage 10A-1). Mock
  services keep their behaviour, so evidence on pages without text stays testable.
- External AI processing (Stage 10A-2, AI_EXTRACTION.md). Nothing is sent unless every gate
  passes, in this order:
  1. the provider is enabled and configured (503 ai_provider_disabled / _not_configured);
  2. the document's issuer authorized external AI processing (403);
  3. the version has text (422 no_extractable_text) and the input is within its bounds
     (422 too_many_pages / input_too_large);
  4. the caller confirmed this request: confirm_external_processing true, and version_id, if
     given, names the current version (428 with what would be sent / 409);
  5. the per-user rate limit (429), counted only for requests that reach the AI service.
  Refusals and failures store nothing and log no document text. Mock services have no gates.
- Manual claims (Stage 10B-3, app/manual_claims.py) call no AI service. Every quotation must be
  verified on its cited page of the named, current version before anything is stored; the record
  is a draft like any other, marked by MANUAL_ENTRY_MODEL, which no AI result may carry.
"""

import uuid
from datetime import datetime, timezone

from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import AccessRegistry, Principal, get_access_registry, security_logger
from app.approval_integrity import load_extraction_context, summarize_extraction
from app.auth import require_permission
from app.authorization import get_document_for_principal, get_extraction_for_principal
from app.db.session import get_db
from app.evidence import normalize_text
from app.enums import AssetStatus, DocumentStatus, ManualClaimRefusal
from app.evidence_records import ExtractionSourceText, load_version_source_text, verify_claim_evidence
from app.manual_claims import (
    MANUAL_DISCLAIMER,
    MANUAL_ENTRY_MODEL,
    ManualClaimProblem,
    complete_claim_set,
    extraction_source,
    manual_claim_problem,
)
from app.models.asset import Asset
from app.models.document import DocumentPage, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.permissions import Permission
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter
from app.schemas.extraction import (
    ClaimEvidenceResponse,
    ExtractionApprovalStateResponse,
    ExtractionRequest,
    LegalClaimResponse,
    LegalExtractionResponse,
    ManualExtractionRequest,
)
from app.services.storage import StorageService, get_storage
from app.source_integrity import read_stored_pdf_text, source_integrity_failure
from app.services.ai import (
    NO_EXTRACTABLE_TEXT,
    NO_EXTRACTABLE_TEXT_REFUSAL,
    AIOutputValidationError,
    AnthropicAIService,
    get_ai_service,
    has_extractable_text,
    validate_ai_claims,
)

router = APIRouter(tags=["Legal Extractions"])

_DOC_NOT_FOUND = "Document not found."
_EXTRACTION_NOT_FOUND = "Extraction not found."
_NOT_AUTHORIZED = (
    "The issuer of this document has not authorized external AI processing, so no document text was "
    "sent. An operator can enable it per issuer in access_control.json (external_ai_processing)."
)
_CONFIRMATION_REQUIRED = (
    "Confirmation required: this request sends the extracted text of version {version_number} of this "
    "document ({page_count} pages, {input_chars} characters including the instructions) to {processor}, "
    "an external AI service (model {model}). Nothing has been sent. To proceed, repeat the request "
    "with confirm_external_processing true and this version_id."
)
_CONFIRMATION_STALE = (
    "The confirmed version is not the current version of this document; nothing was sent. Review "
    "the current version and confirm again."
)


def _refusal(status_code: int, code: str, detail: str, **extra) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "extraction_refusal": code, **extra})


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
        source=extraction_source(extraction.ai_model),
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
    responses={
        403: {"description": "The document's issuer has not authorized external AI processing; nothing sent."},
        409: {"description": "The confirmed version_id is not the current version; nothing sent."},
        422: {"description": "No page of the current version has extractable text (a real AI service "
                             "only): nothing is sent to the AI service or stored (no_extractable_text). "
                             "Or the model input exceeds AI_MAX_PAGES / AI_MAX_INPUT_CHARS."},
        428: {"description": "External processing must be confirmed for this request; the body states "
                             "what would be sent (no document text). Nothing sent."},
        503: {"description": "No AI provider is enabled or configured, or the AI request failed or its "
                             "output was rejected (`ai_error`). Nothing stored."},
    },
)
async def trigger_extraction(
    document_id: str,
    confirmation: Optional[ExtractionRequest] = Body(default=None),
    principal: Principal = Depends(require_permission(Permission.extraction_run)),
    limiter: InMemoryRateLimiter = Depends(get_extraction_rate_limiter),
    db: AsyncSession = Depends(get_db),
    ai_service: AnthropicAIService = Depends(get_ai_service),
    registry: AccessRegistry = Depends(get_access_registry),
) -> LegalExtractionResponse:
    # ── 1. Document (issuer scope) and current version ───────────────────────
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    # A disabled or unconfigured provider sends nothing (Stage 10A-2)
    if not getattr(ai_service, "available", True):
        security_logger.info(
            "extraction.refused_provider_unavailable code=%s document=%s user=%s",
            ai_service.code, document_id, principal.user_id,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": ai_service.message, "ai_error": ai_service.code},
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
    processor = getattr(ai_service, "external_processor", None)
    if processor:
        # The text leaves this server: the document's issuer must have authorized it
        issuer_id = (
            await db.execute(select(Asset.issuer_id).where(Asset.id == document.asset_id))
        ).scalar_one_or_none()
        if not registry.allows_external_ai(issuer_id):
            security_logger.warning(
                "extraction.refused_external_not_authorized document=%s user=%s", document_id, principal.user_id
            )
            return _refusal(status.HTTP_403_FORBIDDEN, "external_processing_not_authorized", _NOT_AUTHORIZED)

    if getattr(ai_service, "requires_text_layer", False) and not has_extractable_text(pages):
        # A scanned PDF gives the model nothing to quote: no request is sent, nothing is stored
        security_logger.info("extraction.refused_no_text document=%s user=%s", document_id, principal.user_id)
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            content={"detail": NO_EXTRACTABLE_TEXT, "extraction_refusal": NO_EXTRACTABLE_TEXT_REFUSAL},
        )

    if processor:
        # Exactly what would be sent, measured before anything is sent; the summary has no text
        prepared = ai_service.prepare(document_id, pages)
        summary = {
            "processor": processor,
            "model": ai_service.model,
            "document_id": document_id,
            "version_id": current_version.id,
            "version_number": current_version.version_number,
            "page_count": prepared.page_count,
            "pages_without_text": [
                p["page_number"] for p in pages if not normalize_text(p["text_content"] or "")
            ],
            "input_chars": prepared.input_chars,
            "max_input_chars": ai_service.max_input_chars,
            "max_pages": ai_service.max_pages,
            "max_output_tokens": ai_service.max_output_tokens,
        }
        problem = ai_service.input_limit_problem(prepared)
        if problem is not None:
            security_logger.info(
                "extraction.refused_input_limit code=%s document=%s pages=%d input_chars=%d",
                problem[0], document_id, prepared.page_count, prepared.input_chars,
            )
            return _refusal(status.HTTP_422_UNPROCESSABLE_CONTENT, problem[0], problem[1], external_processing=summary)
        if confirmation is None or not confirmation.confirm_external_processing:
            return _refusal(
                status.HTTP_428_PRECONDITION_REQUIRED, "external_processing_confirmation_required",
                _CONFIRMATION_REQUIRED.format(**summary), external_processing=summary,
            )
        if confirmation.version_id is not None and confirmation.version_id != current_version.id:
            return _refusal(
                status.HTTP_409_CONFLICT, "confirmation_version_mismatch", _CONFIRMATION_STALE,
                external_processing=summary,
            )
        security_logger.info(
            "extraction.external_processing_confirmed document=%s version=%s user=%s processor=%s "
            "model=%s pages=%d input_chars=%d",
            document_id, current_version.id, principal.user_id, processor, ai_service.model,
            prepared.page_count, prepared.input_chars,
        )

    # Per-user rate limit, counted only for requests that reach the (paid) AI service
    if not limiter.is_allowed(principal.user_id):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI extraction rate limit exceeded. Please try again later.",
        )

    # ── 3. AI call and output validation (the same gate for every AI service) ─
    try:
        ai_result = await ai_service.extract_legal_rights(
            document_id=document_id,
            version_id=current_version.id,
            pages=pages,
        )
        claims = validate_ai_claims(ai_result.claims)
    except RuntimeError as exc:
        code = getattr(exc, "code", "provider_error")
        if isinstance(exc, AIOutputValidationError):
            security_logger.warning(
                "extraction.ai_output_rejected code=%s document=%s user=%s", code, document_id, principal.user_id
            )
        else:
            security_logger.warning(
                "extraction.ai_failed code=%s document=%s user=%s", code, document_id, principal.user_id
            )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc), "ai_error": code},
        )
    if ai_result.ai_model == MANUAL_ENTRY_MODEL:
        # Only the manual endpoint may mark a record as typed by a user (Stage 10B-3)
        security_logger.warning(
            "extraction.ai_output_rejected code=manual_marker document=%s user=%s", document_id, principal.user_id
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "AI service: the result claims to be a manual entry. Nothing was stored.",
                     "ai_error": "invalid_output"},
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


_MANUAL_REFUSAL_DETAILS = {
    ManualClaimRefusal.document_not_in_asset: "The document does not belong to this asset. Nothing was stored.",
    ManualClaimRefusal.version_not_in_document: "This document version does not belong to this document. Nothing was stored.",
    ManualClaimRefusal.version_not_current: (
        "This is not the current version of the document: claims can only be created for the current "
        "version. Reload the document and enter the claims again. Nothing was stored."
    ),
    ManualClaimRefusal.document_not_active: "Claims cannot be created for an inactive document. Nothing was stored.",
    ManualClaimRefusal.asset_not_active: "Claims cannot be created while the asset is not active. Nothing was stored.",
}


def _manual_refusal(status_code: int, refusal: ManualClaimRefusal, detail: str, **extra) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"detail": detail, "manual_claim_refusal": refusal.value, **extra}
    )


def _manual_problem_response(problem: ManualClaimProblem) -> JSONResponse:
    fields = ", ".join(problem.fields)
    if problem.refusal is ManualClaimRefusal.duplicate_field:
        detail = f"Each legal field can be given once; repeated: {fields}. Nothing was stored."
    elif problem.refusal is ManualClaimRefusal.unknown_value:
        detail = (f"A manual claim must state a value; leave the field out instead of entering 'unknown' "
                  f"({fields}). Nothing was stored.")
    else:
        detail = (f"The quotation is not verified on its cited page of this document version ({fields}). "
                  "Copy it exactly from the cited page; it is never completed or corrected by the server. "
                  "Nothing was stored.")
    return _manual_refusal(
        status.HTTP_422_UNPROCESSABLE_CONTENT, problem.refusal, detail,
        fields=problem.fields,
        evidence_failures=[
            {"field_name": f.field_name, "status": f.status.value, "explanation": f.explanation}
            for f in problem.evidence_failures
        ],
    )


@router.post(
    "/documents/{document_id}/manual-extractions",
    response_model=LegalExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create legal claims manually from a document version — the result is always a draft",
    description=(
        "For an authorized user of the document's issuer (`extraction:run`); no AI service is called, "
        "whatever AI_PROVIDER says. Each claim names its legal field, the claim, the cited page and the "
        "exact quotation from that page of the named version. Every quotation must be verified on "
        "its cited page of the stored text of that version (the same check as approvals): otherwise "
        "422 `evidence_not_verified`, with the evidence `status` of each failing claim, and nothing is "
        "stored. The server never generates, completes or alters a quotation.\n\n"
        "`asset_id` must be the document's asset and `version_id` the document's current version "
        "(422 `document_not_in_asset` / `version_not_in_document`, 409 `version_not_current`), and its "
        "stored PDF must still match the version's records (409 `source_integrity_failed`, the approval's "
        "own check). A legal "
        "field may be given once (`duplicate_field`) and must state a value (`unknown_value`). Fields "
        "not given are stored as unknown claims; they can be filled in later through a correction.\n\n"
        "The record is a draft labelled `source: manual`; nothing is approved. Approvals follow the "
        "existing review rules. A verified quotation proves source-text presence only, not that the "
        "claim is legally correct."
    ),
    responses={
        404: {"description": "The document does not exist or is outside the caller's issuers."},
        409: {"description": "Not the current version, or the document or asset is inactive. Nothing stored."},
        422: {"description": "`manual_claim_refusal`: invalid reference, duplicate field, unknown value or "
                             "unverified quotation (`evidence_failures`). Nothing stored."},
    },
)
async def create_manual_extraction(
    document_id: str,
    body: ManualExtractionRequest,
    principal: Principal = Depends(require_permission(Permission.extraction_run)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> LegalExtractionResponse:
    # ── 1. The references: document (issuer scope), asset, current version ───
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)
    refusal: tuple[int, ManualClaimRefusal] | None = None
    version = (
        await db.execute(select(DocumentVersion).where(DocumentVersion.id == body.version_id))
    ).scalar_one_or_none()
    asset_status = (
        await db.execute(select(Asset.status).where(Asset.id == document.asset_id))
    ).scalar_one_or_none()
    if document.asset_id != body.asset_id:
        refusal = (status.HTTP_422_UNPROCESSABLE_CONTENT, ManualClaimRefusal.document_not_in_asset)
    elif version is None or version.document_id != document.id:
        refusal = (status.HTTP_422_UNPROCESSABLE_CONTENT, ManualClaimRefusal.version_not_in_document)
    elif version.version_number != document.current_version_number:
        refusal = (status.HTTP_409_CONFLICT, ManualClaimRefusal.version_not_current)
    elif document.status != DocumentStatus.active.value:
        refusal = (status.HTTP_409_CONFLICT, ManualClaimRefusal.document_not_active)
    elif asset_status != AssetStatus.active.value:
        refusal = (status.HTTP_409_CONFLICT, ManualClaimRefusal.asset_not_active)
    if refusal is not None:
        security_logger.warning(
            "extraction.manual_refused code=%s document=%s user=%s", refusal[1].value, document_id, principal.user_id
        )
        return _manual_refusal(refusal[0], refusal[1], _MANUAL_REFUSAL_DETAILS[refusal[1]])

    # ── 2. The page text is what the stored PDF contains (the approval's own check) ─
    stored_pdf = await read_stored_pdf_text(db, storage, version.id)
    source = await load_version_source_text(db, document.id, version.id)
    integrity = source_integrity_failure(stored_pdf, version, source)
    if integrity is not None:
        security_logger.error(
            "extraction.manual_refused code=source_integrity_failed reason=%s document=%s version=%s user=%s",
            integrity.value, document_id, version.id, principal.user_id,
        )
        return _manual_refusal(
            status.HTTP_409_CONFLICT, ManualClaimRefusal.source_integrity_failed,
            f"The stored PDF of this version does not match its records ({integrity.value}), so its text cannot "
            "be quoted. Nothing was stored.",
            source_integrity_failure=integrity.value,
        )

    # ── 3. Every quotation against the stored page text of exactly this version ─
    problem = manual_claim_problem(body.claims, version.id, source)
    if problem is not None:
        security_logger.warning(
            "extraction.manual_refused code=%s document=%s user=%s fields=%s",
            problem.refusal.value, document_id, principal.user_id,
            ",".join(f"{f.field_name}:{f.status.value}" for f in problem.evidence_failures) or ",".join(problem.fields),
        )
        return _manual_problem_response(problem)

    # ── 4. Save — always "draft", in one transaction ──────────────────────────
    extraction = LegalExtraction(
        id=str(uuid.uuid4()),
        document_id=document.id,
        version_id=version.id,
        status="draft",                         # automatic approval is never performed
        ai_model=MANUAL_ENTRY_MODEL,
        disclaimer=MANUAL_DISCLAIMER,
        extraction_timestamp=datetime.now(timezone.utc),
    )
    try:
        db.add(extraction)
        await db.flush()
        for claim in complete_claim_set(body.claims):
            db.add(
                LegalClaim(
                    id=str(uuid.uuid4()),
                    extraction_id=extraction.id,
                    field_name=claim.field.value,
                    value=claim.value,
                    is_unknown=claim.is_unknown,
                    source_page=claim.source_page,
                    supporting_passage=claim.supporting_passage,
                )
            )
        await db.commit()
    except BaseException:
        # Nothing of a failed save may remain: neither the record nor some of its claims
        await db.rollback()
        raise
    await db.refresh(extraction)

    # The author of a manual record (the extraction record itself has no author column)
    security_logger.info(
        "extraction.manual_created extraction=%s document=%s version=%s user=%s role=%s fields=%s",
        extraction.id, document.id, version.id, principal.user_id, principal.role.value,
        ",".join(sorted(c.field_name.value for c in body.claims)),
    )
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
