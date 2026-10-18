"""
GET /ai/status — whether AI extraction can run on this server (Stage 10B-2).

Read-only and content-free: it reports the AI service that POST /documents/{id}/extractions would
use (the same dependency), whether it can send a request, the external processor and model when
one is configured, and the models the server accepts. It never returns the API key. The interface
uses it to say, before anything is tried, that AI extraction is disabled, and to label stored
extractions: only a supported model name marks an AI result.

Guard: extraction:read, the roles that see extractions.
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.access_registry import Principal
from app.auth import require_permission
from app.config import SUPPORTED_AI_MODELS
from app.permissions import Permission
from app.services.ai import get_ai_service

router = APIRouter(prefix="/ai", tags=["Legal Extractions"])

_AVAILABLE_EXTERNAL = (
    "AI extraction is available. The text of a document version is sent to {processor} (model {model}) "
    "only for issuers that authorized external AI processing, and only after an explicit confirmation. "
    "The result is an unverified draft."
)
_AVAILABLE_LOCAL = "AI extraction is available on this server without an external processor."


class AIStatusResponse(BaseModel):
    available: bool = Field(description="Whether an extraction request can reach an AI service.")
    status: str = Field(description="available, ai_provider_disabled or ai_provider_not_configured.")
    external_processor: Optional[str] = Field(description="The organization that would receive document text, if any.")
    model: Optional[str] = Field(description="The configured model, when an external provider is available.")
    supported_models: list[str] = Field(description="Model names that identify a genuine AI extraction.")
    message: str


@router.get("/status", response_model=AIStatusResponse, summary="Whether AI extraction is available on this server")
async def ai_status(
    principal: Principal = Depends(require_permission(Permission.extraction_read)),
    ai_service=Depends(get_ai_service),
) -> AIStatusResponse:
    supported = sorted(SUPPORTED_AI_MODELS)
    if not getattr(ai_service, "available", True):
        return AIStatusResponse(available=False, status=ai_service.code, external_processor=None, model=None,
                                supported_models=supported, message=ai_service.message)
    processor = getattr(ai_service, "external_processor", None)
    model = getattr(ai_service, "model", None) if processor else None
    message = _AVAILABLE_EXTERNAL.format(processor=processor, model=model) if processor else _AVAILABLE_LOCAL
    return AIStatusResponse(available=True, status="available", external_processor=processor, model=model,
                            supported_models=supported, message=message)
