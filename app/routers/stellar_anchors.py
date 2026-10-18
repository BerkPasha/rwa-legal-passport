"""
Stellar Testnet evidence anchoring endpoints (internal API; STELLAR_ANCHORING.md).

POST /assets/{asset_id}/stellar-anchors                       anchor the current evidence manifest
GET  /assets/{asset_id}/stellar-anchors                       anchoring history of the asset
POST /assets/{asset_id}/stellar-anchors/{anchor_id}/refresh   re-check a pending anchor on the network
GET  /assets/{asset_id}/stellar-anchors/verification          live verification (same as the passport's)

Institutional clients verify through GET /passport/{network}/{asset_code}/{issuer_key}/stellar-anchor.
Every route is scoped to the asset's issuer (out of scope: the same 404 as a missing asset).
An anchor never changes approvals, evidence or the passport; it is not a legal approval.
"""

from fastapi import APIRouter, Depends, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal
from app.anchoring import (
    AnchorRefused,
    anchor_current_manifest,
    anchor_response,
    refresh_anchor,
    verify_passport_anchor,
)
from app.auth import require_permission
from app.authorization import get_asset_for_principal
from app.db.session import get_db
from app.enums import AnchorStatus
from app.models.stellar_anchor import StellarAnchor
from app.permissions import Permission
from app.schemas.stellar_anchor import AnchorVerificationResponse, StellarAnchorResponse
from app.services.stellar_anchor import (
    AnchorAccountNotFound,
    AnchorConfigError,
    AnchorNetworkError,
    StellarAnchorService,
    WrongNetworkError,
    get_anchor_service,
)
from app.services.storage import StorageService, get_storage

router = APIRouter(tags=["Stellar Evidence Anchoring"])

_NOT_FOUND = "Asset not found."


def _refused(status_code: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail, "anchor_refusal": code})


@router.post(
    "/assets/{asset_id}/stellar-anchors",
    response_model=StellarAnchorResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Anchor the passport's current evidence manifest on Stellar Testnet",
    description=(
        "Builds the deterministic evidence manifest of what the passport currently serves and "
        "commits its SHA-256 digest in one Stellar Testnet transaction (memo hash + manage_data "
        "`lp:<asset id>`), signed by the configured Testnet anchoring account.\n\n"
        "Idempotent: a digest that is already confirmed or pending returns the existing anchor (200) "
        "and submits nothing; a failed attempt is retried. 201 = confirmed by the network, "
        "202 = submitted but not yet confirmed (use /refresh), 502 = rejected by the network "
        "(`failure_reason`). Refused with `anchor_refusal`: 409 `mainnet_not_supported` / "
        "`no_current_passport`, 422 `invalid_asset_identity`, 503 `anchoring_not_configured` / "
        "`anchor_account_not_found` / `wrong_network` / `network_unavailable` (nothing stored).\n\n"
        "An anchor is not a legal approval and changes nothing in the passport."
    ),
)
async def create_stellar_anchor(
    asset_id: str,
    response: Response,
    principal: Principal = Depends(require_permission(Permission.anchor_create)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
    service: StellarAnchorService = Depends(get_anchor_service),
):
    asset = await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)
    try:
        anchor, submitted = await anchor_current_manifest(db, asset, storage, service, principal.user_id)
    except AnchorRefused as exc:
        return _refused(exc.status_code, exc.code, exc.detail)
    except AnchorConfigError as exc:
        return _refused(503, "anchoring_not_configured", str(exc))
    except AnchorAccountNotFound as exc:
        return _refused(503, "anchor_account_not_found", str(exc))
    except WrongNetworkError as exc:
        return _refused(503, "wrong_network", str(exc))
    except AnchorNetworkError as exc:
        return _refused(503, "network_unavailable", str(exc))
    if not submitted:
        response.status_code = status.HTTP_200_OK
    else:
        response.status_code = {
            AnchorStatus.confirmed.value: status.HTTP_201_CREATED,
            AnchorStatus.pending.value: status.HTTP_202_ACCEPTED,
            AnchorStatus.failed.value: status.HTTP_502_BAD_GATEWAY,
        }[anchor.status]
    return anchor_response(anchor, service)


@router.get(
    "/assets/{asset_id}/stellar-anchors",
    response_model=list[StellarAnchorResponse],
    summary="Anchoring history of an asset (stored states, newest first)",
)
async def list_stellar_anchors(
    asset_id: str,
    principal: Principal = Depends(require_permission(Permission.asset_read)),
    db: AsyncSession = Depends(get_db),
    service: StellarAnchorService = Depends(get_anchor_service),
) -> list[StellarAnchorResponse]:
    await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)
    anchors = (await db.execute(
        select(StellarAnchor).where(StellarAnchor.asset_id == asset_id)
        .order_by(StellarAnchor.created_at.desc(), StellarAnchor.id)
    )).scalars().all()
    return [anchor_response(a, service) for a in anchors]


@router.post(
    "/assets/{asset_id}/stellar-anchors/{anchor_id}/refresh",
    response_model=StellarAnchorResponse,
    summary="Re-check a pending anchor on the network",
    description=(
        "A pending anchor becomes confirmed if the network reports the matching successful "
        "transaction, and failed if it failed or its time bounds expired without inclusion. A "
        "network error changes nothing. Confirmed and failed anchors are returned unchanged."
    ),
)
async def refresh_stellar_anchor(
    asset_id: str,
    anchor_id: str,
    principal: Principal = Depends(require_permission(Permission.anchor_create)),
    db: AsyncSession = Depends(get_db),
    service: StellarAnchorService = Depends(get_anchor_service),
):
    await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)
    anchor = (await db.execute(
        select(StellarAnchor).where(StellarAnchor.id == anchor_id, StellarAnchor.asset_id == asset_id)
    )).scalar_one_or_none()
    if anchor is None:
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "Anchor not found."})
    anchor = await refresh_anchor(db, anchor, service)
    return anchor_response(anchor, service)


@router.get(
    "/assets/{asset_id}/stellar-anchors/verification",
    response_model=AnchorVerificationResponse,
    summary="Verify the asset's evidence commitment against live Stellar network data",
    description="The same read-only verification as GET /passport/{network}/{asset_code}/{issuer_key}/stellar-anchor.",
)
async def verify_stellar_anchor(
    asset_id: str,
    principal: Principal = Depends(require_permission(Permission.asset_read)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
    service: StellarAnchorService = Depends(get_anchor_service),
) -> AnchorVerificationResponse:
    asset = await get_asset_for_principal(db, asset_id, principal, _NOT_FOUND)
    return await verify_passport_anchor(db, asset, storage, service)
