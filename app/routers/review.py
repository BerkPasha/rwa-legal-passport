"""
İnceleme ve onay iş akışı endpoint'leri.

POST /extractions/{id}/review          — İncelemeyi başlat (under_review)
PATCH /extractions/{id}/claims/{cid}   — İddia düzeltme (değişmez denetim kaydı)
POST /extractions/{id}/approvals       — Onay oluştur (kaynak kanıt zorunlu)
GET  /extractions/{id}/corrections     — Düzeltme geçmişi
GET  /extractions/{id}/approvals       — Onay geçmişi

Güvenlik kısıtlamaları:
- Her endpoint kimlik doğrulaması gerektirir (Bearer token).
- AI kendi bulgularını onaylayamaz (user_id "ai-" ile başlıyorsa 403).
- Onay, yasal geçerliliği garanti etmez — disclaimer zorunlu.
- Onay öncesinde tüm bilinen iddialarda kaynak kanıt (supporting_passage) zorunludur.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import ReviewerInfo, get_current_reviewer
from app.db.session import get_db
from app.enums import ApprovalType, ExtractionStatus, ReviewerRole
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval, ReviewCorrection
from app.schemas.review import (
    ApprovalRequest,
    ClaimCorrectionRequest,
    ReviewApprovalResponse,
    ReviewCorrectionResponse,
    ReviewStatusResponse,
)

router = APIRouter(tags=["Legal Review"])

APPROVAL_DISCLAIMER = (
    "INTERNAL REVIEW ONLY — This approval reflects a reviewer's internal assessment "
    "and does NOT constitute legal certification, guarantee of legal validity, "
    "regulatory compliance confirmation, or a binding legal opinion under any jurisdiction. "
    "Independent qualified legal counsel must be consulted for legally binding conclusions."
)


def _approval_type_for_role(role: ReviewerRole) -> str:
    return {
        ReviewerRole.issuer_reviewer: ApprovalType.issuer_attestation.value,
        ReviewerRole.independent_legal_reviewer: ApprovalType.legal_verification.value,
    }[role]


async def _get_extraction_or_404(db: AsyncSession, extraction_id: str) -> LegalExtraction:
    result = await db.execute(
        select(LegalExtraction).where(LegalExtraction.id == extraction_id)
    )
    extraction = result.scalar_one_or_none()
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Çıkarım bulunamadı."
        )
    return extraction


# ── Endpoint'ler ──────────────────────────────────────────────────────────────


@router.post(
    "/extractions/{extraction_id}/review",
    response_model=ReviewStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="İnceleme sürecini başlat — durum 'under_review' olarak güncellenir",
)
async def start_review(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: ReviewerInfo = Depends(get_current_reviewer),
) -> ReviewStatusResponse:
    extraction = await _get_extraction_or_404(db, extraction_id)

    if extraction.status == ExtractionStatus.approved.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onaylanmış çıkarım yeniden incelemeye alınamaz.",
        )

    extraction.status = ExtractionStatus.under_review.value
    await db.commit()
    await db.refresh(extraction)

    return ReviewStatusResponse(
        extraction_id=extraction.id,
        status=ExtractionStatus(extraction.status),
        reviewer_id=reviewer.user_id,
        reviewer_role=reviewer.role,
    )


@router.patch(
    "/extractions/{extraction_id}/claims/{claim_id}",
    response_model=ReviewCorrectionResponse,
    status_code=status.HTTP_200_OK,
    summary="İddiayı düzelt — değişiklik öncesi durum değişmez denetim kaydına işlenir",
)
async def correct_claim(
    extraction_id: str,
    claim_id: str,
    body: ClaimCorrectionRequest,
    db: AsyncSession = Depends(get_db),
    reviewer: ReviewerInfo = Depends(get_current_reviewer),
) -> ReviewCorrectionResponse:
    extraction = await _get_extraction_or_404(db, extraction_id)

    if extraction.status == ExtractionStatus.approved.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Onaylanmış çıkarım düzeltilemez.",
        )

    claim_result = await db.execute(
        select(LegalClaim).where(
            LegalClaim.id == claim_id,
            LegalClaim.extraction_id == extraction_id,
        )
    )
    claim = claim_result.scalar_one_or_none()
    if claim is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="İddia bulunamadı."
        )

    # Denetim kaydı — değişiklik öncesi durum değişmez biçimde saklanır
    correction = ReviewCorrection(
        id=str(uuid.uuid4()),
        extraction_id=extraction_id,
        claim_id=claim_id,
        reviewer_id=reviewer.user_id,
        reviewer_role=reviewer.role.value,
        original_value=claim.value,
        original_is_unknown=claim.is_unknown,
        original_passage=claim.supporting_passage,
        corrected_value=body.corrected_value,
        corrected_is_unknown=body.is_unknown,
        corrected_passage=body.corrected_passage,
        correction_note=body.correction_note,
    )
    db.add(correction)

    # İddiadaki değerleri güncelle
    claim.value = body.corrected_value
    claim.is_unknown = body.is_unknown
    if body.corrected_source_page is not None:
        claim.source_page = body.corrected_source_page
    if body.corrected_passage is not None:
        claim.supporting_passage = body.corrected_passage

    # Taslak hâlindeyse otomatik olarak inceleme altına al
    if extraction.status == ExtractionStatus.draft.value:
        extraction.status = ExtractionStatus.under_review.value

    await db.commit()
    await db.refresh(correction)

    return ReviewCorrectionResponse.model_validate(correction)


@router.post(
    "/extractions/{extraction_id}/approvals",
    response_model=ReviewApprovalResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Çıkarımı onayla — bilinen her iddiada kaynak kanıt zorunludur",
)
async def approve_extraction(
    extraction_id: str,
    body: ApprovalRequest,
    db: AsyncSession = Depends(get_db),
    reviewer: ReviewerInfo = Depends(get_current_reviewer),
) -> ReviewApprovalResponse:
    # Kod katmanı: AI kendi bulgularını onaylayamaz
    if reviewer.user_id.startswith("ai-"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="AI servisi onay işlemi yapamaz.",
        )

    extraction = await _get_extraction_or_404(db, extraction_id)

    # Zorunlu kaynak kanıt denetimi — bilinen her iddia için supporting_passage gerekli
    claims_result = await db.execute(
        select(LegalClaim).where(LegalClaim.extraction_id == extraction_id)
    )
    claims = claims_result.scalars().all()

    missing_evidence = [
        c.field_name
        for c in claims
        if not c.is_unknown and not (c.supporting_passage and c.supporting_passage.strip())
    ]
    if missing_evidence:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Onay reddedildi — şu alanlarda kaynak kanıt eksik: "
                f"{', '.join(sorted(missing_evidence))}"
            ),
        )

    now = datetime.now(timezone.utc)
    approval = ReviewApproval(
        id=str(uuid.uuid4()),
        extraction_id=extraction_id,
        reviewer_id=reviewer.user_id,
        reviewer_role=reviewer.role.value,
        approval_type=_approval_type_for_role(reviewer.role),
        disclaimer=APPROVAL_DISCLAIMER,
        notes=body.notes,
        approved_at=now,
    )
    db.add(approval)

    extraction.status = ExtractionStatus.approved.value
    await db.commit()
    await db.refresh(approval)

    return ReviewApprovalResponse.model_validate(approval)


@router.get(
    "/extractions/{extraction_id}/corrections",
    response_model=list[ReviewCorrectionResponse],
    summary="Çıkarıma ait düzeltme geçmişini listele",
)
async def list_corrections(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: ReviewerInfo = Depends(get_current_reviewer),
) -> list[ReviewCorrectionResponse]:
    await _get_extraction_or_404(db, extraction_id)

    result = await db.execute(
        select(ReviewCorrection)
        .where(ReviewCorrection.extraction_id == extraction_id)
        .order_by(ReviewCorrection.created_at)
    )
    return [ReviewCorrectionResponse.model_validate(c) for c in result.scalars().all()]


@router.get(
    "/extractions/{extraction_id}/approvals",
    response_model=list[ReviewApprovalResponse],
    summary="Çıkarıma ait onay geçmişini listele",
)
async def list_approvals(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
    reviewer: ReviewerInfo = Depends(get_current_reviewer),
) -> list[ReviewApprovalResponse]:
    await _get_extraction_or_404(db, extraction_id)

    result = await db.execute(
        select(ReviewApproval)
        .where(ReviewApproval.extraction_id == extraction_id)
        .order_by(ReviewApproval.created_at)
    )
    return [ReviewApprovalResponse.model_validate(a) for a in result.scalars().all()]
