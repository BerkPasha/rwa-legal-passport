from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.enums import ApprovalType, ExtractionStatus, ReviewerRole


# ── İstek şemaları ────────────────────────────────────────────────────────────


class ClaimCorrectionRequest(BaseModel):
    """Bir iddia düzeltme talebi."""
    corrected_value: str = Field(..., min_length=1, max_length=2000)
    is_unknown: bool
    # Sağlanırsa mevcut kaynak alıntıyı değiştirir; sağlanmazsa mevcut korunur
    corrected_source_page: Optional[int] = Field(None, ge=1)
    corrected_passage: Optional[str] = Field(None, min_length=1, max_length=2000)
    correction_note: Optional[str] = Field(None, max_length=1000)


class ApprovalRequest(BaseModel):
    """Onay talebi — onay türü inceleyicinin rolünden otomatik türetilir."""
    notes: Optional[str] = Field(None, max_length=1000)


# ── Yanıt şemaları ────────────────────────────────────────────────────────────


class ReviewCorrectionResponse(BaseModel):
    id: str
    extraction_id: str
    claim_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    original_value: str
    original_is_unknown: bool
    original_passage: Optional[str] = None
    corrected_value: str
    corrected_is_unknown: bool
    corrected_passage: Optional[str] = None
    correction_note: Optional[str] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewApprovalResponse(BaseModel):
    id: str
    extraction_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    approval_type: ApprovalType
    # Her onay yanıtında bulunur — yasal geçerliliği garanti etmez
    disclaimer: str
    notes: Optional[str] = None
    approved_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ReviewStatusResponse(BaseModel):
    """İnceleme başlatma işleminin sonucu."""
    extraction_id: str
    status: ExtractionStatus
    reviewer_id: str
    reviewer_role: ReviewerRole
