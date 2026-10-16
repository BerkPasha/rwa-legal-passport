"""
Stellar blockchain verification endpoint.

GET /assets/{asset_id}/blockchain-status
  → Queries whether the asset is registered on the Stellar network.
  → Returns only network data read from the Horizon REST API.
  → Never makes any claim of legal ownership.

Design decisions:
- Read-only: no wallet, private key or signing.
- Authentication and the asset:read permission are required: although Horizon data
  is public, this endpoint reveals the internal asset registry and the issuer scope.
- Network error → 503 Service Unavailable (temporary failure).
- Returns 200 even if the asset is not found on the network; indicated by found_on_network=False.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal
from app.auth import require_permission
from app.authorization import get_asset_for_principal
from app.db.session import get_db
from app.enums import BlockchainNetwork
from app.permissions import Permission
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
    summary="Query the asset record on the Stellar network — read-only",
    description=(
        "Returns whether the asset is registered on the Stellar Horizon network. "
        "This endpoint never makes any claim of legal ownership. "
        "Requires a Bearer token and the asset:read permission; only assets within scope can be queried."
    ),
)
async def get_blockchain_status(
    asset_id: str,
    principal: Principal = Depends(require_permission(Permission.asset_read)),
    db: AsyncSession = Depends(get_db),
    stellar: StellarService = Depends(get_stellar_service),
) -> BlockchainStatusResponse:
    # ── 1. Load the asset from the database (issuer scope) ─────────────────────
    asset = await get_asset_for_principal(db, asset_id, principal, "Asset not found.")

    # ── 2. Fetch asset information from Stellar Horizon ────────────────────────
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

    # ── 3. Build the response ──────────────────────────────────────────────────
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
