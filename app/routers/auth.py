"""
Credential verification endpoint.

GET /auth/me — returns which user, role and issuer scope the Bearer token is
bound to. Never shows the token or its digest in the response.
"""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.access_registry import Principal
from app.auth import get_current_principal
from app.enums import UserRole

router = APIRouter(prefix="/auth", tags=["Auth"])


class CurrentUserResponse(BaseModel):
    user_id: str
    display_name: str
    role: UserRole
    # "global" (platform_admin) or "issuers" (limited to issuer_ids)
    scope: str
    issuer_ids: list[str]
    permissions: list[str]


@router.get(
    "/me",
    response_model=CurrentUserResponse,
    summary="Get the current user's role and access scope",
)
async def get_me(principal: Principal = Depends(get_current_principal)) -> CurrentUserResponse:
    return CurrentUserResponse(
        user_id=principal.user_id,
        display_name=principal.display_name,
        role=principal.role,
        scope="global" if principal.has_global_scope else "issuers",
        issuer_ids=sorted(principal.issuer_ids),
        permissions=sorted(p.value for p in principal.permissions),
    )
