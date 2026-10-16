import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import AccessRegistry, Principal, get_access_registry, security_logger
from app.approval_integrity import MATERIAL_ASSET_FIELDS
from app.auth import require_permission
from app.authorization import get_asset_for_principal, scope_assets
from app.db.session import get_db
from app.models.asset import Asset
from app.permissions import Permission
from app.schemas.asset import AssetCreate, AssetResponse, AssetUpdate

router = APIRouter(prefix="/assets", tags=["Assets"])

_NOT_FOUND = "Asset not found."


@router.post(
    "",
    response_model=AssetResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tokenized asset",
)
async def create_asset(
    payload: AssetCreate,
    principal: Principal = Depends(require_permission(Permission.asset_create)),
    registry: AccessRegistry = Depends(get_access_registry),
    db: AsyncSession = Depends(get_db),
) -> AssetResponse:
    # An asset can only be bound to an issuer defined in the access registry
    if payload.issuer_id not in registry.issuers:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="issuer_id does not match an issuer registered in the access configuration.",
        )
    if not principal.can_access_issuer(payload.issuer_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to register assets for this issuer.",
        )

    asset = Asset(
        id=str(uuid.uuid4()),
        issuer_id=payload.issuer_id,
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
    summary="List the registered assets within scope",
)
async def list_assets(
    principal: Principal = Depends(require_permission(Permission.asset_read)),
    db: AsyncSession = Depends(get_db),
) -> list[AssetResponse]:
    stmt = scope_assets(select(Asset), principal).order_by(Asset.created_at.desc())
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Get asset details by ID",
)
async def get_asset(
    asset_id: str,
    principal: Principal = Depends(require_permission(Permission.asset_read)),
    db: AsyncSession = Depends(get_db),
) -> AssetResponse:
    return await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)


@router.patch(
    "/{asset_id}",
    response_model=AssetResponse,
    summary="Update the legal/organizational information of an asset",
    description=(
        "Every change of material information (organization name, asset type, jurisdiction, "
        "status) increments material_revision. Approvals bound to an earlier revision stop "
        "being current and require a re-review; restoring the previous values does not "
        "restore them."
    ),
)
async def update_asset(
    asset_id: str,
    payload: AssetUpdate,
    principal: Principal = Depends(require_permission(Permission.asset_update)),
    db: AsyncSession = Depends(get_db),
) -> AssetResponse:
    asset = await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)

    changes = payload.model_dump(exclude_none=True)
    changed_material = sorted(
        field
        for field, value in changes.items()
        if field in MATERIAL_ASSET_FIELDS and getattr(asset, field) != value
    )
    for field, value in changes.items():
        setattr(asset, field, value)
    asset.updated_at = datetime.now(timezone.utc)
    if changed_material:
        # Atomic increment in SQL: concurrent changes can never reuse a revision number
        asset.material_revision = Asset.material_revision + 1

    await db.commit()
    await db.refresh(asset)
    if changed_material:
        security_logger.info(
            "asset.material_change asset=%s user=%s fields=%s revision=%d",
            asset.id, principal.user_id, ",".join(changed_material), asset.material_revision,
        )
    return asset
