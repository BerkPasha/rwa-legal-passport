import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.models.asset import Asset
from app.schemas.asset import AssetCreate, AssetResponse, AssetUpdate

router = APIRouter(prefix="/assets", tags=["Assets"])


@router.post(
    "",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Yeni tokenize edilmiş varlık kaydı oluştur",
)
async def create_asset(
    payload: AssetCreate, db: AsyncSession = Depends(get_db)
) -> AssetResponse:
    asset = Asset(
        id=str(uuid.uuid4()),
        blockchain_network=payload.blockchain_network,
        stellar_asset_code=payload.stellar_asset_code,
        stellar_issuer_public_key=payload.stellar_issuer_public_key,
        issuer_organization_name=payload.issuer_organization_name,
        asset_type=payload.asset_type,
        jurisdiction=payload.jurisdiction,
        status="active",
    )
    db.add(asset)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An asset with the same Stellar asset code and issuer public key already exists.",
        )
    await db.refresh(asset)
    return asset


@router.get(
    "",
    response_model=list[AssetResponse],
    summary="Tüm kayıtlı varlıkları listele",
)
async def list_assets(db: AsyncSession = Depends(get_db)) -> list[AssetResponse]:
    result = await db.execute(select(Asset).order_by(Asset.created_at.desc()))
    return result.scalars().all()


@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Varlık detayını ID ile getir",
)
async def get_asset(asset_id: str, db: AsyncSession = Depends(get_db)) -> AssetResponse:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found."
        )
    return asset


@router.patch(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Varlığın yasal/organizasyonel bilgilerini güncelle",
)
async def update_asset(
    asset_id: str, payload: AssetUpdate, db: AsyncSession = Depends(get_db)
) -> AssetResponse:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found."
        )

    changes = payload.model_dump(exclude_none=True)
    for field, value in changes.items():
        setattr(asset, field, value)
    asset.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(asset)
    return asset
