"""
AI yasal hak çıkarım endpoint'leri.

POST /documents/{document_id}/extractions   — AI ile çıkarımı tetikle (taslak)
GET  /documents/{document_id}/extractions   — Belgeye ait çıkarım geçmişi
GET  /extractions/{extraction_id}           — Tek çıkarımı getir

Önemli kısıtlamalar:
- Hiçbir endpoint otomatik onay yapmaz; status her zaman "draft" kalır.
- AI, yasal geçerliliği onaylayamaz.
- Tüm yanıtlar sorumluluk reddi içerir.
"""

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.document import Document, DocumentPage, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.schemas.extraction import LegalClaimResponse, LegalExtractionResponse
from app.services.ai import AnthropicAIService, get_ai_service

router = APIRouter(tags=["Legal Extractions"])


# ── Yardımcı ─────────────────────────────────────────────────────────────────


async def _load_extraction_response(
    db: AsyncSession, extraction: LegalExtraction
) -> LegalExtractionResponse:
    """Tüm iddiaları DB'den yükler ve LegalExtractionResponse oluşturur."""
    claims_result = await db.execute(
        select(LegalClaim)
        .where(LegalClaim.extraction_id == extraction.id)
        .order_by(LegalClaim.field_name)
    )
    claims = [LegalClaimResponse.model_validate(c) for c in claims_result.scalars().all()]

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
    )


# ── Endpointler ───────────────────────────────────────────────────────────────


@router.post(
    "/documents/{document_id}/extractions",
    response_model=LegalExtractionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="AI ile yasal hakları çıkar — dönen sonuç her zaman taslaktır",
)
async def trigger_extraction(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    ai_service: AnthropicAIService = Depends(get_ai_service),
) -> LegalExtractionResponse:
    # ── 1. Belge ve güncel versiyon ──────────────────────────────────────────
    doc_result = await db.execute(select(Document).where(Document.id == document_id))
    document = doc_result.scalar_one_or_none()
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Belge versiyonu bulunamadı."
        )

    # ── 2. Sayfa metinlerini çek ─────────────────────────────────────────────
    pages_result = await db.execute(
        select(DocumentPage)
        .where(DocumentPage.version_id == current_version.id)
        .order_by(DocumentPage.page_number)
    )
    pages = [
        {"page_number": p.page_number, "text_content": p.text_content}
        for p in pages_result.scalars().all()
    ]

    # ── 3. AI çağrısı ────────────────────────────────────────────────────────
    try:
        ai_result = await ai_service.extract_legal_rights(
            document_id=document_id,
            version_id=current_version.id,
            pages=pages,
        )
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # ── 4. Sonuçları kaydet — her zaman "draft" ──────────────────────────────
    extraction_id = str(uuid.uuid4())
    extraction = LegalExtraction(
        id=extraction_id,
        document_id=document_id,
        version_id=current_version.id,
        status="draft",                         # otomatik onay asla yapılmaz
        ai_model=ai_result.ai_model,
        disclaimer=ai_result.disclaimer,
        extraction_timestamp=ai_result.extraction_timestamp,
    )
    db.add(extraction)
    await db.flush()

    for claim in ai_result.claims:
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
    await db.refresh(extraction)

    return await _load_extraction_response(db, extraction)


@router.get(
    "/documents/{document_id}/extractions",
    response_model=list[LegalExtractionResponse],
    summary="Belgeye ait AI çıkarım geçmişini listele",
)
async def list_extractions(
    document_id: str,
    db: AsyncSession = Depends(get_db),
) -> list[LegalExtractionResponse]:
    doc_result = await db.execute(select(Document).where(Document.id == document_id))
    if doc_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Belge bulunamadı.")

    result = await db.execute(
        select(LegalExtraction)
        .where(LegalExtraction.document_id == document_id)
        .order_by(LegalExtraction.created_at.desc())
    )
    return [await _load_extraction_response(db, e) for e in result.scalars().all()]


@router.get(
    "/extractions/{extraction_id}",
    response_model=LegalExtractionResponse,
    summary="Çıkarım detayını tüm iddiaları ile getir",
)
async def get_extraction(
    extraction_id: str,
    db: AsyncSession = Depends(get_db),
) -> LegalExtractionResponse:
    result = await db.execute(
        select(LegalExtraction).where(LegalExtraction.id == extraction_id)
    )
    extraction = result.scalar_one_or_none()
    if extraction is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Çıkarım bulunamadı."
        )
    return await _load_extraction_response(db, extraction)
