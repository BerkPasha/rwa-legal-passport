from fastapi import APIRouter
from pydantic import BaseModel, Field

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    # Showcase deployment policies (Stage 10C-1), for the interface's labels; no configuration value
    read_only: bool = Field(description="Every write request is refused (READ_ONLY_MODE).")
    synthetic_data: bool = Field(description="The server declares all its data synthetic (SYNTHETIC_DATA_NOTICE).")
    api_docs: bool = Field(description="/docs, /redoc and /openapi.json are served (API_DOCS_ENABLED).")


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
        read_only=settings.read_only_mode,
        synthetic_data=settings.synthetic_data_notice,
        api_docs=settings.api_docs_enabled,
    )
