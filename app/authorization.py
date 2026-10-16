"""
Resource-level authorization — issuer (tenant) isolation.

Every asset belongs to an issuer (Asset.issuer_id). Documents, versions,
extractions, corrections and approvals inherit this ownership through the asset.

Rule: when a user accesses a resource outside their scope, the response is a 404,
exactly as if the resource did not exist; this way the resource IDs of other
issuers cannot be confirmed. Denied accesses are written to the 'rwa_security' log.
"""

from fastapi import HTTPException, status
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal, safe_log_id, security_logger
from app.models.asset import Asset
from app.models.document import Document
from app.models.extraction import LegalExtraction


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _log_out_of_scope(principal: Principal, kind: str, resource_id: str) -> None:
    security_logger.warning(
        "authz.out_of_scope user=%s role=%s resource=%s id=%s",
        principal.user_id,
        principal.role.value,
        kind,
        safe_log_id(resource_id),
    )


def scope_assets(stmt: Select, principal: Principal) -> Select:
    """Restricts an asset query to the user's issuer scope."""
    if principal.has_global_scope:
        return stmt
    return stmt.where(Asset.issuer_id.in_(sorted(principal.issuer_ids)))


async def get_asset_for_principal(
    db: AsyncSession, asset_id: str, principal: Principal, detail: str
) -> Asset:
    result = await db.execute(select(Asset).where(Asset.id == asset_id))
    asset = result.scalar_one_or_none()
    if asset is None:
        raise _not_found(detail)
    if not principal.can_access_issuer(asset.issuer_id):
        _log_out_of_scope(principal, "asset", asset_id)
        raise _not_found(detail)
    return asset


async def get_document_for_principal(
    db: AsyncSession, document_id: str, principal: Principal, detail: str
) -> Document:
    result = await db.execute(
        select(Document, Asset.issuer_id)
        .join(Asset, Asset.id == Document.asset_id)
        .where(Document.id == document_id)
    )
    row = result.one_or_none()
    if row is None:
        raise _not_found(detail)
    document, issuer_id = row
    if not principal.can_access_issuer(issuer_id):
        _log_out_of_scope(principal, "document", document_id)
        raise _not_found(detail)
    return document


async def get_extraction_for_principal(
    db: AsyncSession, extraction_id: str, principal: Principal, detail: str
) -> LegalExtraction:
    result = await db.execute(
        select(LegalExtraction, Asset.issuer_id)
        .join(Document, Document.id == LegalExtraction.document_id)
        .join(Asset, Asset.id == Document.asset_id)
        .where(LegalExtraction.id == extraction_id)
    )
    row = result.one_or_none()
    if row is None:
        raise _not_found(detail)
    extraction, issuer_id = row
    if not principal.can_access_issuer(issuer_id):
        _log_out_of_scope(principal, "extraction", extraction_id)
        raise _not_found(detail)
    return extraction
