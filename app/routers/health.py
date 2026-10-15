from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse, summary="Sistem sağlık kontrolü")
async def health_check() -> HealthResponse:
    """
    Sunucunun ayakta olup olmadığını ve temel bilgilerini döner.
    """
    from app.config import settings

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
    )
