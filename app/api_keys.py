"""
Institutional client API key authentication.

HTTP header: X-API-Key
Each key is bound to an institutional client, to the permitted issuers / assets and
to the permitted operations (e.g. passport:read). Keys are stored in the access
configuration file only as SHA-256 digests.

An out-of-scope asset receives the same 404 response as a non-existent asset.
API keys cannot access any internal (Bearer) endpoint.

PRIVACY:
The API key is never logged.
"""

from typing import Callable, Optional

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.access_registry import AccessRegistry, InstitutionalClient, get_access_registry, security_logger
from app.permissions import InstitutionalOperation

_api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    auto_error=False,
    description="Institutional client API key (scoped to issuers/assets and operations).",
)


def get_institutional_client(
    api_key: Optional[str] = Security(_api_key_scheme),
    registry: AccessRegistry = Depends(get_access_registry),
) -> InstitutionalClient:
    """
    FastAPI dependency: validates the X-API-Key header.
    Header missing  → 403 Forbidden
    Invalid key     → 401 Unauthorized
    """
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The X-API-Key header is required.",
        )
    client = registry.authenticate_client(api_key)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key.",
        )
    return client


def require_operation(operation: InstitutionalOperation) -> Callable[..., InstitutionalClient]:
    """Builds a dependency that checks the API key is authorized for a specific operation."""

    def _dependency(
        client: InstitutionalClient = Depends(get_institutional_client),
    ) -> InstitutionalClient:
        if not client.allows(operation):
            security_logger.warning(
                "authz.operation_denied client_id=%s operation=%s",
                client.client_id,
                operation.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This API key is not authorized for this operation.",
            )
        return client

    _dependency.__name__ = f"require_{operation.name}"
    return _dependency
