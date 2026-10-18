"""
AI extraction schemas.

Two layers:
  - Internal schemas (AIExtractionResult, RawClaim): for communication with the AI service.
    Never returned directly as an API response.
  - External schemas (LegalExtractionResponse, LegalClaimResponse): API responses.
    Always include the disclaimer.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt

from app.enums import (
    ApprovalStaleReason,
    ApprovalType,
    EvidenceStatus,
    ExtractionApprovalState,
    ExtractionSource,
    ExtractionStatus,
    LegalFieldName,
)


# ── Internal AI service schemas (not written to the database) ────────────────


class ClaimSource(BaseModel):
    """Source reference of a claim within the document."""
    # Strict: lax coercion would turn the JSON value true into page 1
    page_number: Optional[StrictInt] = None
    supporting_passage: str  # verbatim quote from the document


class RawClaim(BaseModel):
    """
    A single legal-rights claim returned by the AI.
    Validated and canonicalized by app.services.ai.validate_ai_claims before it is stored.
    """
    field: LegalFieldName
    value: str               # the string "unknown" if there is no evidence
    is_unknown: StrictBool   # strict: "yes" or 1 is not a valid flag
    source: Optional[ClaimSource] = None


class AIExtractionResult(BaseModel):
    """
    Raw result returned by the AI service.
    Processed by the router before it is written to the database.
    """
    claims: list[RawClaim]
    ai_model: str
    extraction_timestamp: datetime
    disclaimer: str          # disclaimer — always populated


# ── API request schema ───────────────────────────────────────────────────────


class ExtractionRequest(BaseModel):
    """
    Optional body of POST /documents/{id}/extractions (Stage 10A-2). With an external AI
    provider, the caller must confirm, for this request, that the text of the current document
    version is sent to that provider; the 428 response says exactly what would be sent.
    """
    confirm_external_processing: StrictBool = False
    # The version the caller confirmed (the 428 response's version_id); another current version
    # gives 409, so a confirmation never covers text the caller was not shown
    version_id: Optional[str] = Field(default=None, min_length=1, max_length=64)

    model_config = ConfigDict(extra="forbid")


class ManualClaimInput(BaseModel):
    """
    One legal claim typed by a user (Stage 10B-3): its legal field (the claim category), the
    claim itself, and the exact quotation from the cited page that supports it. The quotation is
    checked on the server against the stored page text; nothing in the request is trusted.
    """
    field_name: LegalFieldName
    value: str = Field(..., min_length=1, max_length=2000, description="The claim, in the user's words.")
    # Strict: lax coercion would read true as page 1
    source_page: StrictInt = Field(..., ge=1, description="Page of the document version that holds the quotation.")
    supporting_passage: str = Field(
        ..., min_length=1, max_length=2000,
        description="Exact quotation from that page; never generated or completed by the server.",
    )

    model_config = ConfigDict(extra="forbid")


class ManualExtractionRequest(BaseModel):
    """
    Body of POST /documents/{document_id}/manual-extractions (Stage 10B-3). asset_id and
    version_id name exactly what the user looked at: the document must belong to that asset, and
    the version must be the document's current version.
    """
    asset_id: str = Field(..., min_length=1, max_length=64)
    version_id: str = Field(..., min_length=1, max_length=64)
    claims: list[ManualClaimInput] = Field(..., min_length=1, max_length=len(LegalFieldName))

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{
                "asset_id": "5f0c7d1e-0000-4000-8000-000000000001",
                "version_id": "5f0c7d1e-0000-4000-8000-000000000002",
                "claims": [{
                    "field_name": "jurisdiction",
                    "value": "Governed by the laws of the State of New York.",
                    "source_page": 1,
                    "supporting_passage": "This Trust Agreement is governed by the laws of the State of New York",
                }],
            }]
        },
    )


# ── API response schemas (returned to the client) ────────────────────────────


class ClaimEvidenceResponse(BaseModel):
    """
    Deterministic source-text check of the claim's quotation (Stage 9B-4, app/evidence.py),
    evaluated on every read against the extracted page text of the extraction's own document
    version. It proves source-text presence only: it is not legal correctness, reviewer
    approval or independent legal verification.
    """
    status: EvidenceStatus = Field(
        description="'verified' only if the quotation appears on the cited page; every other value is a failure."
    )
    cited_page: Optional[int] = Field(
        default=None, description="Cited page number if it is an integer, even when it is out of range."
    )
    found_on_pages: list[int] = Field(
        default_factory=list, description="Pages of this document version on which the quotation appears."
    )
    page_count: int = Field(description="Number of pages of the checked document version.")
    version_number: Optional[int] = Field(
        default=None,
        description="Checked document version: the extraction's own version, never a newer one. "
                    "Null if that version's text is unavailable.",
    )
    verification_method: str = Field(description="Matching method (exact substring on token boundaries).")
    normalization_version: int = Field(description="Version of the text normalization rules.")
    scope: str = Field(description="Always 'source_text_presence_only': the check proves nothing more.")
    explanation: str = Field(description="Human-readable reason for the status.")


class LegalClaimResponse(BaseModel):
    id: str
    extraction_id: str
    field_name: str
    value: str
    is_unknown: bool
    source_page: Optional[int] = None
    supporting_passage: Optional[str] = None
    created_at: datetime
    evidence: ClaimEvidenceResponse

    model_config = ConfigDict(from_attributes=True)


class ExtractionApprovalStateResponse(BaseModel):
    """Approval currency of an extraction, derived from its approval records on every read."""
    state: ExtractionApprovalState = Field(
        description="not_approved, current, stale (re-review possible) or superseded (newer document version)."
    )
    version_is_current: bool = Field(
        description="False if the extraction belongs to a superseded document version; it can no longer be approved."
    )
    current_approval_types: list[ApprovalType] = Field(
        default_factory=list, description="Approval types that are currently valid."
    )
    reasons: list[ApprovalStaleReason] = Field(
        default_factory=list, description="Why the existing approvals are not current."
    )


class LegalExtractionResponse(BaseModel):
    id: str
    document_id: str
    version_id: str
    # "draft" only — automatic approval is never applied
    status: ExtractionStatus
    ai_model: str
    source: ExtractionSource = Field(
        description="'manual' if the claims were typed by a user (no AI was involved), 'ai_service' otherwise."
    )
    # Disclaimer shown in every response
    disclaimer: str
    extraction_timestamp: datetime
    created_at: datetime
    claims: list[LegalClaimResponse] = []
    approval_state: ExtractionApprovalStateResponse

    model_config = ConfigDict(from_attributes=True)
