from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


@router.get("/health", response_model=HealthResponse, summary="System health check")
async def health_check() -> HealthResponse:
    """
    Returns whether the server is up, together with basic information.
    """
    from app.config import settings

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
    )
