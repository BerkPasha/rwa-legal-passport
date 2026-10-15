"""
AI çıkarım şemaları.

İki katman:
  - İç şemalar (AIExtractionResult, RawClaim): AI servisi ile iletişim için.
    Asla doğrudan API yanıtı olarak döndürülmez.
  - Dış şemalar (LegalExtractionResponse, LegalClaimResponse): API yanıtları.
    Her zaman sorumluluk reddi içerir.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.enums import ExtractionStatus, LegalFieldName


# ── AI servis iç şemaları (veritabanına yazılmaz) ────────────────────────────


class ClaimSource(BaseModel):
    """Bir iddianın belgedeki kaynak referansı."""
    page_number: Optional[int] = None
    supporting_passage: str  # belgeden birebir alıntı


class RawClaim(BaseModel):
    """AI'ın döndürdüğü tek bir yasal hak iddiası."""
    field: LegalFieldName
    value: str               # kanıt yoksa "unknown" string'i
    is_unknown: bool
    source: Optional[ClaimSource] = None


class AIExtractionResult(BaseModel):
    """
    AI servisinden dönen ham sonuç.
    Router tarafından veritabanına aktarılmadan önce işlenir.
    """
    claims: list[RawClaim]
    ai_model: str
    extraction_timestamp: datetime
    disclaimer: str          # sorumluluk reddi — her zaman dolu


# ── API yanıt şemaları (istemciye döndürülür) ────────────────────────────────


class LegalClaimResponse(BaseModel):
    id: str
    extraction_id: str
    field_name: str
    value: str
    is_unknown: bool
    source_page: Optional[int] = None
    supporting_passage: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class LegalExtractionResponse(BaseModel):
    id: str
    document_id: str
    version_id: str
    # Yalnızca "draft" — otomatik onay uygulanmaz
    status: ExtractionStatus
    ai_model: str
    # Her yanıtta görünen sorumluluk reddi
    disclaimer: str
    extraction_timestamp: datetime
    created_at: datetime
    claims: list[LegalClaimResponse] = []

    model_config = ConfigDict(from_attributes=True)
