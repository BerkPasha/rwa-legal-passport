from datetime import datetime
from typing import Any, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from app.enums import ApprovalStaleReason, ApprovalType, ExtractionStatus, ReviewerRole


# ── Request schemas ───────────────────────────────────────────────────────────


class ExpectedClaimState(BaseModel):
    """
    The claim exactly as the reviewer read it (optimistic concurrency, Stage 9B-4 issue A). All four
    fields are required, so a precondition always covers the whole evidence state; null means none.
    """
    model_config = ConfigDict(extra="forbid")

    value: StrictStr
    is_unknown: StrictBool
    source_page: Optional[StrictInt] = Field(..., description="Cited page as read; null if none.")
    supporting_passage: Optional[StrictStr] = Field(..., description="Quotation as read; null if none.")


class ClaimCorrectionRequest(BaseModel):
    """
    A claim correction request (evidence rules: Stage 9B-4, step 7, app/correction_rules.py).

    corrected_source_page / corrected_passage: an omitted field keeps the current evidence only if
    the value and the unknown flag are unchanged; a field sent as null clears it. A changed value
    (or an unknown claim made known) needs both fields for the new value. The resulting quotation
    must be verified on its cited page of the extraction's document version. A claim corrected to
    unknown carries no evidence and its value becomes "unknown". Unknown keys are rejected.

    expected_claim (required, Stage 9B-4 issue A): the claim as the reviewer read it. If the stored
    claim differs when the correction is processed, it is refused with 409 and nothing changes, so
    an omitted page or quotation can never keep evidence saved by another reviewer after this one
    read the claim. A request without it (or with null) is invalid (422).
    """
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [
                {
                    "corrected_value": "Fractional beneficial ownership interest in the Trust.",
                    "is_unknown": False,
                    "correction_note": "Value confirmed; page and quotation kept.",
                    "expected_claim": {
                        "value": "Fractional beneficial ownership interest in the Trust.",
                        "is_unknown": False,
                        "source_page": 1,
                        "supporting_passage": "Investors shall hold a fractional beneficial ownership interest "
                                              "in the Meridian Property Trust.",
                    },
                },
                {
                    "corrected_value": "unknown",
                    "is_unknown": True,
                    "corrected_source_page": None,
                    "corrected_passage": None,
                    "expected_claim": {
                        "value": "Fractional beneficial ownership interest in the Trust.",
                        "is_unknown": False,
                        "source_page": 1,
                        "supporting_passage": "Investors shall hold a fractional beneficial ownership interest "
                                              "in the Meridian Property Trust.",
                    },
                },
            ]
        },
    )

    corrected_value: str = Field(..., min_length=1, max_length=2000)
    # Strict: lax coercion would read true as page 1 and "yes" as unknown
    is_unknown: StrictBool
    corrected_source_page: Optional[StrictInt] = Field(
        None, ge=1, description="Cited page of the quotation; null clears it; omitted keeps it (unchanged value only)."
    )
    corrected_passage: Optional[str] = Field(
        None, min_length=1, max_length=2000,
        description="Supporting quotation; null clears it; omitted keeps it (unchanged value only).",
    )
    correction_note: Optional[str] = Field(None, max_length=1000)
    expected_claim: ExpectedClaimState = Field(
        ...,
        description=(
            "Required optimistic concurrency precondition: value, is_unknown, source_page and "
            "supporting_passage exactly as read. Compared with the stored claim under the write lock; any "
            "difference gives 409 (correction_conflict: claim_changed) and changes nothing. Missing or "
            "null: 422."
        ),
    )


class ApprovalRequest(BaseModel):
    """
    Approval request — the approval type is derived automatically from the reviewer's role.
    Unknown fields (e.g. reviewer_id or approval_type) are rejected with 422 rather than ignored:
    the reviewer identity always comes from the authenticated token (Stage 9B-5C).
    """
    model_config = ConfigDict(extra="forbid")
    notes: Optional[str] = Field(None, max_length=1000)


# ── Response schemas ──────────────────────────────────────────────────────────


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
    # ── Source page history (Stage 9B-4, step 7) ─────────────────────────────
    original_source_page: Optional[int] = Field(default=None, description="Cited page before the correction.")
    corrected_source_page: Optional[int] = Field(default=None, description="Cited page after the correction.")
    page_history_recorded: bool = Field(
        description="False for records created before Stage 9B-4 step 7, which did not record source pages."
    )

    model_config = ConfigDict(from_attributes=True)


class ApprovalCurrencyResponse(BaseModel):
    """Whether an approval still applies to the live document, evidence and asset information."""
    is_current: bool = Field(description="True only if no reason applies.")
    reasons: list[ApprovalStaleReason] = Field(
        default_factory=list, description="Why the approval is not current (empty when current)."
    )


class AssetSnapshotResponse(BaseModel):
    """Asset identifiers and material information at approval time."""
    asset_id: str
    issuer_id: str
    blockchain_network: str
    stellar_asset_code: str
    stellar_issuer_public_key: str
    issuer_organization_name: str
    asset_type: str
    jurisdiction: str
    status: str


class EvidenceSnapshotClaim(BaseModel):
    """One claim exactly as it was approved."""
    claim_id: str
    field_name: str
    value: str
    is_unknown: bool
    source_page: Optional[int] = None
    supporting_passage: Optional[str] = None


class ReviewApprovalResponse(BaseModel):
    id: str
    extraction_id: str
    reviewer_id: str
    reviewer_role: ReviewerRole
    approval_type: ApprovalType
    # Present in every approval response — does not guarantee legal validity
    disclaimer: str
    notes: Optional[str] = None
    approved_at: datetime
    created_at: datetime

    # ── Binding (Stage 9B-3): what exactly was approved — null only for legacy approvals ──
    document_id: Optional[str] = None
    document_version_id: Optional[str] = None
    document_version_number: Optional[int] = None
    document_sha256: Optional[str] = Field(
        default=None, description="SHA-256 of the approved document version."
    )
    evidence_digest: Optional[str] = Field(
        default=None, description="SHA-256 of the canonical evidence snapshot."
    )
    evidence_snapshot: Optional[list[EvidenceSnapshotClaim]] = Field(
        default=None,
        description="Historical snapshot of the approved claims (the claims of evidence_record, in these fields).",
    )
    # ── Evidence binding (Stage 9B-4, step 5) ────────────────────────────────
    evidence_snapshot_format: Optional[int] = Field(
        default=None,
        description=(
            "1: legacy claim list without evidence verification; 2: claims bound to their verified "
            "evidence. Null for an unbound legacy approval or an unreadable snapshot."
        ),
    )
    evidence_record: Optional[Union[dict[str, Any], list[Any]]] = Field(
        default=None,
        description=(
            "The evidence snapshot exactly as stored; evidence_digest is the SHA-256 of its canonical "
            "JSON (sorted keys, no whitespace, UTF-8). Format 2 adds, per claim, the evidence status, "
            "the SHA-256 of the cited page's stored text and of the normalized quotation. Hashes "
            "identify content; they do not prove legal correctness."
        ),
    )
    asset_material_revision: Optional[int] = None
    asset_snapshot_digest: Optional[str] = None
    asset_snapshot: Optional[AssetSnapshotResponse] = Field(
        default=None, description="Historical snapshot of the approved asset information."
    )
    binding_digest: Optional[str] = Field(
        default=None, description="SHA-256 over the whole binding; identifies the approved content."
    )
    currency: ApprovalCurrencyResponse = Field(
        description="Evaluated against the live state on every read; approval records never change."
    )


class ReviewStatusResponse(BaseModel):
    """Result of the start-review operation."""
    extraction_id: str
    status: ExtractionStatus
    reviewer_id: str
    reviewer_role: ReviewerRole
