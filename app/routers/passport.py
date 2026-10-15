"""
Kurumsal Yasal Şeffaflık Pasaportu endpoint'i.

GET /passport/{network}/{asset_code}/{issuer_key}

Yalnızca onaylanmış bilgi döner — taslak çıkarımlar asla gösterilmez.
API anahtarı kimlik doğrulaması ve istek hız sınırlaması uygulanır.
Her istek yapılandırılmış günlük kaydıyla belgelenir.

Güvenlik kısıtlamaları:
- X-API-Key başlığı zorunlu.
- Yalnızca status='approved' olan çıkarımlar döndürülür.
- Kaynak PDF dosyası bu endpoint üzerinden indirilemez.
- Hız sınırı: varsayılan 60 istek/dakika/istemci.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api_keys import InstitutionalClient, get_institutional_client
from app.db.session import get_db
from app.enums import BlockchainNetwork, ExtractionStatus
from app.models.asset import Asset
from app.models.document import Document, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval
from app.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.schemas.passport import (
    ApprovalEntry,
    AssetSummary,
    DocumentReference,
    ExtractionMetadata,
    LegalPassportResponse,
    LegalRightEntry,
)

logger = logging.getLogger("rwa_passport")

PASSPORT_DISCLAIMER = (
    "INSTITUTIONAL USE ONLY — This legal passport reflects approved internal assessments "
    "based on issuer-provided documentation. It does NOT constitute legal advice, regulatory "
    "certification, investment recommendation, or a binding legal opinion under any jurisdiction. "
    "All information must be independently verified by qualified legal counsel before use."
)

router = APIRouter(tags=["Legal Passport"])


@router.get(
    "/passport/{network}/{asset_code}/{issuer_key}",
    response_model=LegalPassportResponse,
    status_code=status.HTTP_200_OK,
    summary="Onaylanmış yasal pasaportu getir",
    description=(
        "Blockchain tanımlayıcıları ile tokenize edilmiş bir varlığın onaylanmış yasal "
        "haklarını ve kısıtlamalarını döndürür. "
        "**Yalnızca onaylanmış bilgi gösterilir — taslak veya incelemede olan bilgi asla gösterilmez.** "
        "X-API-Key başlığı ile kimlik doğrulama gereklidir."
    ),
    responses={
        401: {"description": "Geçersiz API anahtarı."},
        403: {"description": "X-API-Key başlığı eksik."},
        404: {"description": "Varlık bulunamadı veya onaylanmış pasaport mevcut değil."},
        429: {"description": "İstek hız sınırı aşıldı."},
    },
)
async def get_legal_passport(
    network: str,
    asset_code: str,
    issuer_key: str,
    db: AsyncSession = Depends(get_db),
    client: InstitutionalClient = Depends(get_institutional_client),
    limiter: InMemoryRateLimiter = Depends(get_rate_limiter),
) -> LegalPassportResponse:
    # ── 1. Hız sınırı kontrolü ────────────────────────────────────────────────
    if not limiter.is_allowed(client.client_id):
        logger.warning(
            "passport.rate_limited client_id=%s org=%s asset=%s/%s",
            client.client_id,
            client.organization,
            network,
            asset_code,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="İstek hız sınırı aşıldı. Lütfen bir dakika sonra tekrar deneyin.",
        )

    # ── 2. Blockchain ağ değerini doğrula ─────────────────────────────────────
    try:
        blockchain_network = BlockchainNetwork(network)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Geçersiz blockchain ağı: {network!r}.",
        )

    # ── 3. Varlığı blockchain tanımlayıcılarıyla bul ──────────────────────────
    asset_result = await db.execute(
        select(Asset).where(
            Asset.blockchain_network == blockchain_network.value,
            Asset.stellar_asset_code == asset_code,
            Asset.stellar_issuer_public_key == issuer_key,
        )
    )
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        logger.info(
            "passport.not_found client_id=%s asset=%s/%s/%s",
            client.client_id, network, asset_code, issuer_key[:8],
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Varlık bulunamadı.",
        )

    # ── 4. Onaylı çıkarımları bul (taslak asla gösterilmez) ───────────────────
    doc_ids_subq = select(Document.id).where(Document.asset_id == asset.id)
    extractions_result = await db.execute(
        select(LegalExtraction)
        .where(
            LegalExtraction.document_id.in_(doc_ids_subq),
            LegalExtraction.status == ExtractionStatus.approved.value,
        )
        .order_by(LegalExtraction.created_at.asc())
    )
    approved_extractions = extractions_result.scalars().all()

    if not approved_extractions:
        logger.info(
            "passport.no_approved_extraction client_id=%s asset=%s/%s",
            client.client_id, network, asset_code,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bu varlık için onaylanmış yasal pasaport henüz mevcut değil.",
        )

    passport_version = len(approved_extractions)
    extraction = approved_extractions[-1]  # en güncel onaylı çıkarım

    # ── 5. İddialar, onaylar, belge ───────────────────────────────────────────
    claims_result = await db.execute(
        select(LegalClaim)
        .where(LegalClaim.extraction_id == extraction.id)
        .order_by(LegalClaim.field_name)
    )
    claims = claims_result.scalars().all()

    approvals_result = await db.execute(
        select(ReviewApproval)
        .where(ReviewApproval.extraction_id == extraction.id)
        .order_by(ReviewApproval.approved_at)
    )
    approvals = approvals_result.scalars().all()

    doc_result = await db.execute(
        select(Document).where(Document.id == extraction.document_id)
    )
    document = doc_result.scalar_one()

    version_result = await db.execute(
        select(DocumentVersion).where(DocumentVersion.id == extraction.version_id)
    )
    doc_version = version_result.scalar_one()

    # ── 6. Yanıt oluştur ──────────────────────────────────────────────────────
    effective_date = (
        max(a.approved_at for a in approvals) if approvals else extraction.created_at
    )

    legal_rights = [
        LegalRightEntry(
            field_name=c.field_name,
            value=c.value,
            is_unknown=c.is_unknown,
            verified=(
                not c.is_unknown
                and bool(c.supporting_passage and c.supporting_passage.strip())
            ),
            source_page=c.source_page if not c.is_unknown else None,
            supporting_passage=c.supporting_passage if not c.is_unknown else None,
        )
        for c in claims
    ]

    approval_entries = [
        ApprovalEntry(
            approval_type=a.approval_type,
            reviewer_role=a.reviewer_role,
            approved_at=a.approved_at,
            notes=a.notes,
        )
        for a in approvals
    ]

    retrieved_at = datetime.now(timezone.utc)

    logger.info(
        "passport.served client_id=%s org=%s asset=%s/%s version=%d rights=%d",
        client.client_id,
        client.organization,
        network,
        asset_code,
        passport_version,
        len(legal_rights),
    )

    return LegalPassportResponse(
        asset=AssetSummary(
            asset_id=asset.id,
            asset_code=asset.stellar_asset_code,
            issuer_key=asset.stellar_issuer_public_key,
            network=blockchain_network,
            issuer_organization=asset.issuer_organization_name,
            asset_type=asset.asset_type,
            jurisdiction=asset.jurisdiction,
        ),
        legal_rights=legal_rights,
        approvals=approval_entries,
        source_document=DocumentReference(
            document_id=document.id,
            title=document.title,
            document_type=document.document_type,
            version_number=doc_version.version_number,
        ),
        extraction_metadata=ExtractionMetadata(
            extraction_id=extraction.id,
            ai_model=extraction.ai_model,
            extraction_timestamp=extraction.extraction_timestamp,
            passport_version=passport_version,
            effective_date=effective_date,
        ),
        disclaimer=PASSPORT_DISCLAIMER,
        retrieved_at=retrieved_at,
    )
