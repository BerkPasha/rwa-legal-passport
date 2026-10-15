"""
Stellar blockchain doğrulama endpoint'i.

GET /assets/{asset_id}/blockchain-status
  → Varlığın Stellar ağında kayıtlı olup olmadığını sorgular.
  → Yalnızca Horizon REST API'den okunan ağ verisi döner.
  → Hiçbir zaman yasal mülkiyet iddiasında bulunmaz.

Tasarım kararları:
- Salt okunur: cüzdan, özel anahtar veya imzalama yoktur.
- Kimlik doğrulama gerekmez: blockchain verisi herkese açık.
- Ağ hatası → 503 Service Unavailable (geçici arıza).
- Varlık ağda bulunmasa da 200 döner; found_on_network=False ile belirtilir.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.enums import BlockchainNetwork
from app.models.asset import Asset
from app.schemas.blockchain import (
    AssetFlagsResponse,
    BlockchainStatusResponse,
    HorizonRecordResponse,
)
from app.services.stellar import (
    BLOCKCHAIN_DISCLAIMER,
    StellarNetworkError,
    StellarService,
    get_stellar_service,
)

router = APIRouter(tags=["Blockchain Verification"])


@router.get(
    "/assets/{asset_id}/blockchain-status",
    response_model=BlockchainStatusResponse,
    status_code=status.HTTP_200_OK,
    summary="Stellar ağında varlık kaydını sorgula — salt okunur",
    description=(
        "Varlığın Stellar Horizon ağında kayıtlı olup olmadığını döndürür. "
        "Bu endpoint hiçbir zaman yasal mülkiyet iddiasında bulunmaz. "
        "Kimlik doğrulama gerekmez; blockchain verisi herkese açıktır."
    ),
)
async def get_blockchain_status(
    asset_id: str,
    db: AsyncSession = Depends(get_db),
    stellar: StellarService = Depends(get_stellar_service),
) -> BlockchainStatusResponse:
    # ── 1. Varlığı veritabanından yükle ────────────────────────────────────────
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Varlık bulunamadı."
        )

    # ── 2. Stellar Horizon'dan varlık bilgisi al ───────────────────────────────
    network = BlockchainNetwork(asset.blockchain_network)
    try:
        info = await stellar.get_asset_info(
            asset_code=asset.stellar_asset_code,
            issuer_key=asset.stellar_issuer_public_key,
            network=network,
        )
    except StellarNetworkError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        )

    # ── 3. Yanıt oluştur ───────────────────────────────────────────────────────
    record_resp = None
    if info.record is not None:
        record_resp = HorizonRecordResponse(
            asset_type=info.record.asset_type,
            num_accounts=info.record.num_accounts,
            total_supply=info.record.total_supply,
            flags=AssetFlagsResponse(**info.record.flags.model_dump()),
        )

    return BlockchainStatusResponse(
        asset_id=asset.id,
        asset_code=asset.stellar_asset_code,
        issuer_key=asset.stellar_issuer_public_key,
        network=network,
        horizon_url=info.horizon_url,
        found_on_network=info.found_on_network,
        record=record_resp,
        retrieved_at=info.retrieved_at,
        disclaimer=BLOCKCHAIN_DISCLAIMER,
    )
