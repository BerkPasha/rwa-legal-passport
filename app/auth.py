"""
Bearer token authentication and role-based authorization.

Credentials are read from the access-configuration file (see app/access_registry.py);
the file contains only SHA-256 digests of tokens. There are no tokens in the source code.

Response rules:
- No Authorization header               → 403 (FastAPI HTTPBearer default)
- Invalid / disabled token              → 401
- The role lacks the permission         → 403
- The resource belongs to another issuer → 404 (app/authorization.py; does not reveal that it exists)

PRODUCTION NOTE:
In production this should be replaced with a JWT/OIDC-based identity provider; the
Principal model and the require_permission() dependency can stay the same.

AI APPROVAL BAN:
The AI service has no credentials. Additional check in the code layer: if the user
ID starts with "ai-", the approval is rejected (app/routers/review.py).
"""

from typing import Callable

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.access_registry import AccessRegistry, Principal, get_access_registry, security_logger
from app.permissions import Permission

_bearer = HTTPBearer(
    description="User token (platform_admin, issuer_reviewer, independent_legal_reviewer).",
)


def get_current_principal(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
    registry: AccessRegistry = Depends(get_access_registry),
) -> Principal:
    """
    FastAPI dependency: validates the Bearer token and returns the user profile.
    Invalid token → 401 Unauthorized
    """
    principal = registry.authenticate_user(credentials.credentials)
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing authentication token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return principal


def require_permission(permission: Permission) -> Callable[..., Principal]:
    """
    Builds a dependency that checks a role permission.
    Usage: principal: Principal = Depends(require_permission(Permission.asset_read))
    """

    def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if not principal.has_permission(permission):
            security_logger.warning(
                "authz.permission_denied user=%s role=%s permission=%s",
                principal.user_id,
                principal.role.value,
                permission.value,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You are not authorized to perform this operation.",
            )
        return principal

    _dependency.__name__ = f"require_{permission.name}"
    return _dependency
