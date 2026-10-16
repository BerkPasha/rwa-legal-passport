"""
Institutional Legal Passport response schemas.

Every field carries a description for the OpenAPI documentation.

Meaning of the verification indicators (Stage 9B-4, step 6). Four things are kept apart:
- source quotation verified: the deterministic check found the quotation on its cited page of
  the approved document version (evidence_status = verified; `verified`);
- reviewer attestation recorded: a current approval exists (every served source has one);
- independent reviewer attestation recorded: a current approval by an independent legal
  reviewer (approval type legal_verification);
- legal interpretation: never independently established by this system.
Source-text verification proves only that the quotation exists in the source document. It does
not establish legal enforceability, ownership authenticity, investor entitlement, regulatory
compliance, issuer solvency or an independent legal opinion.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.enums import (
    ApprovalStaleReason,
    ApprovalType,
    AssetStatus,
    BlockchainNetwork,
    EvidenceStatus,
    ReviewerAttestationStatus,
    ValueOrigin,
    VerificationLevel,
)

SOURCE_VERIFICATION_MEANING = (
    "Source-text verification establishes only that the quotation appears on the cited page of "
    "the approved document version. It does not establish legal enforceability, ownership "
    "authenticity, investor entitlement, regulatory compliance, issuer solvency or an independent "
    "legal opinion. Reviewer approvals are internal attestations, not legal opinions."
)
LEGAL_INTERPRETATION_NOT_ESTABLISHED = "not_independently_established"


class AssetSummary(BaseModel):
    """Identifying information of the tokenized asset."""
    asset_id: str = Field(description="Internal database identifier.")
    asset_code: str = Field(description="Stellar asset code (e.g. MRPROP).")
    issuer_key: str = Field(description="Issuer Stellar public key.")
    network: BlockchainNetwork = Field(description="Linked Stellar network.")
    issuer_organization: str = Field(description="Name of the token-issuing organization.")
    asset_type: str = Field(description="Asset category (real_estate, bond, etc.).")
    jurisdiction: str = Field(description="Legal jurisdiction.")
    status: AssetStatus = Field(
        description="Asset status. A passport is served only for active assets."
    )


class RightSource(BaseModel):
    """The value of one legal field in one currently approved source document."""
    document_id: str = Field(description="Internal document identifier.")
    document_title: str = Field(description="Document title.")
    version_number: int = Field(description="Approved document version.")
    value: str = Field(description="Approved value in this document; 'unknown' if not known.")
    is_unknown: bool = Field(description="If True, this document has insufficient evidence for the field.")
    source_page: Optional[int] = Field(default=None, description="Source page number in this document.")
    supporting_passage: Optional[str] = Field(default=None, description="Supporting quote from this document.")
    independently_verified: bool = Field(
        description=(
            "True only if this document's extraction has a current approval by an independent legal "
            "reviewer AND this value is known and its quotation is source-verified. Not a legal opinion."
        )
    )
    # ── Stage 9B-4, step 6 ────────────────────────────────────────────────────
    evidence_status: EvidenceStatus = Field(
        description=(
            "Deterministic check of this quotation on its cited page of this approved version, "
            "re-computed on every read. Only 'verified' is positive; unknown values are 'not_applicable'."
        )
    )
    value_origin: ValueOrigin = Field(
        description=(
            "ai_extraction: approved as extracted by the AI; reviewer_correction: a reviewer corrected "
            "the claim before approval (the value shown is the reviewer's, not the AI's)."
        )
    )
    reviewer_attestation_status: ReviewerAttestationStatus = Field(
        description="Which current approvals of this document stand behind this value; not_applicable if unknown."
    )


class LegalRightEntry(BaseModel):
    """
    A single legal right or restriction record.

    verified=True: the value is known, not conflicting, and its quotation was found on the cited
    page of the approved document version in every approved source (source-text presence only).
    It does NOT mean legal correctness or independent legal verification.
    verified=False: the field is unknown, sources conflict, or the evidence is not verified.
    """
    field_name: str = Field(description="Legal field name (e.g. ownership_nature).")
    value: str = Field(
        description="Approved value; 'unknown' if not known; 'conflicting' if approved sources disagree."
    )
    is_unknown: bool = Field(description="If True, insufficient evidence was found in the documents.")
    verified: bool = Field(
        description=(
            "True only if the value is known, not conflicting and its quotation is source-verified "
            "(evidence_status = verified) in every approved source. Source-text presence only: not "
            "legal correctness and not independent legal verification."
        )
    )
    independently_verified: bool = Field(
        description=(
            "True only if verified is True and every source of the value has a current approval by an "
            "independent legal reviewer. An internal attestation on top of a source-verified quotation; "
            "not an independent legal opinion. Issuer attestation alone never sets this."
        )
    )
    conflict: bool = Field(
        description="True if currently approved documents state different values; no value is chosen."
    )
    source_page: Optional[int] = Field(
        default=None, description="Source page number of the verified quotation; populated only if verified=True."
    )
    supporting_passage: Optional[str] = Field(
        default=None,
        description="Quote from the document supporting the claim; populated only if verified=True.",
    )
    sources: list[RightSource] = Field(
        default_factory=list, description="The field's value in every currently approved source document."
    )
    # ── Stage 9B-4, step 6 ────────────────────────────────────────────────────
    evidence_status: EvidenceStatus = Field(
        description=(
            "verified if the quotation is source-verified in every approved source of the value; "
            "otherwise the first failing status; not_applicable for unknown and conflicting fields."
        )
    )
    verification_method: str = Field(description="Deterministic matching method of evidence_status.")
    document_id: Optional[str] = Field(
        default=None, description="Document of the verified quotation; populated only if verified=True."
    )
    document_version_number: Optional[int] = Field(
        default=None, description="Approved document version of the verified quotation; only if verified=True."
    )
    value_origin: Optional[ValueOrigin] = Field(
        default=None,
        description=(
            "Origin of the value shown (see RightSource.value_origin); reviewer_correction if any "
            "source's claim was corrected by a reviewer. Null for unknown and conflicting fields."
        ),
    )
    reviewer_attestation_status: ReviewerAttestationStatus = Field(
        description=(
            "independent_reviewer_attested if every source of the value has a current independent "
            "reviewer approval, issuer_attested otherwise; not_applicable for unknown and conflicting fields."
        )
    )


class ApprovalEntry(BaseModel):
    """A reviewer's current approval record."""
    approval_type: ApprovalType = Field(
        description=(
            "issuer_attestation (issuer) or legal_verification (independent legal reviewer). Both are "
            "internal attestations; legal_verification is not an independent legal opinion."
        )
    )
    reviewer_role: str = Field(description="Role of the reviewer.")
    approved_at: datetime = Field(description="Approval timestamp (UTC).")
    notes: Optional[str] = Field(default=None, description="Optional notes of the reviewer.")
    document_id: str = Field(description="Document the approval is bound to.")
    document_version_number: int = Field(description="Exact document version that was approved.")
    binding_digest: str = Field(
        description=(
            "SHA-256 over the approved binding (document version, evidence, asset information). An "
            "unkeyed digest identifies content; it is not a signature and does not prove authenticity."
        )
    )


class DocumentReference(BaseModel):
    """Metadata of the source document the extraction is based on (no content, no download link)."""
    document_id: str = Field(description="Internal document identifier.")
    title: str = Field(description="Document title.")
    document_type: str = Field(description="Document category (prospectus, legal_opinion, etc.).")
    version_number: int = Field(description="Document version the extraction was performed on.")


class ApprovedSourceDocument(DocumentReference):
    """A currently approved source document and the strength of its current approvals."""
    approval_types: list[ApprovalType] = Field(
        default_factory=list, description="Current approval types of this document's extraction."
    )
    independently_verified: bool = Field(
        description=(
            "True only if a current independent legal reviewer approval exists AND the document "
            "contributes at least one known, source-verified value. Not a legal opinion."
        )
    )
    # ── Stage 9B-4, step 6 ────────────────────────────────────────────────────
    source_verified_claim_count: int = Field(
        description="Known claims of this document whose quotation is source-verified."
    )
    unknown_claim_count: int = Field(description="Claims of this document that are unknown.")


class ExcludedDocument(BaseModel):
    """A document whose earlier approval is no longer current and is therefore not used."""
    document_id: str = Field(description="Internal document identifier.")
    title: str = Field(description="Document title.")
    document_type: str = Field(description="Document category.")
    reasons: list[ApprovalStaleReason] = Field(description="Why its approval is not current.")


class ExtractionMetadata(BaseModel):
    """Technical metadata of the AI extraction."""
    extraction_id: str = Field(description="Internal extraction identifier.")
    ai_model: str = Field(description="AI model that performed the extraction.")
    extraction_timestamp: datetime = Field(description="AI extraction timestamp (UTC).")
    passport_version: int = Field(
        description=(
            "Number of approval records ever created for this asset's extractions; increases "
            "with every approval and never decreases."
        )
    )
    effective_date: datetime = Field(
        description="Latest current approval timestamp — used as the effective date."
    )


class VerificationSummary(BaseModel):
    """
    What the passport's verification indicators establish, counted over its legal fields
    (Stage 9B-4, step 6). Source verification, reviewer attestation, independent reviewer
    attestation and legal interpretation are reported separately.
    """
    source_verified_field_count: int = Field(
        description="Known, non-conflicting fields whose quotation is source-verified (verified=True)."
    )
    independently_attested_field_count: int = Field(
        description="Of these, fields whose every source has a current independent reviewer approval."
    )
    unverified_field_count: int = Field(
        description="Known, non-conflicting fields whose quotation is not source-verified (shown, not verified)."
    )
    unknown_field_count: int = Field(description="Fields without sufficient evidence in any approved source.")
    conflicting_field_count: int = Field(description="Fields on which approved sources disagree.")
    reviewer_attestation_recorded: bool = Field(
        description="A current reviewer approval stands behind this passport (always true when served)."
    )
    independent_reviewer_attestation_recorded: bool = Field(
        description=(
            "At least one current approval is by an independent legal reviewer. Says nothing about "
            "whether any field is verified; see independently_attested_field_count."
        )
    )
    evidence_scope: str = Field(description="What a verified quotation proves: source_text_presence_only.")
    verification_method: str = Field(description="Deterministic quotation matching method.")
    normalization_version: int = Field(description="Version of the text normalization rules.")
    legal_interpretation_status: str = Field(
        description="Always not_independently_established: this system never establishes legal correctness."
    )
    meaning: str = Field(description="Plain-language statement of what the indicators do and do not establish.")


class LegalPassportResponse(BaseModel):
    """
    Institutional Legal Passport — summary of approved legal rights.

    WARNING: This passport reflects the internal review process only.
    It is not legal advice, regulatory approval or investment advice.
    All information must be verified by independent legal counsel.
    """
    asset: AssetSummary
    verification_level: VerificationLevel = Field(
        description=(
            "Reviewer attestation behind the source-verified fields: independently_verified (every "
            "verified field backed by a current independent reviewer approval), "
            "partially_independently_verified, or issuer_attested (no verified field independently "
            "backed — also when no field is verified at all; see verification_summary). Derived from "
            "verified fields only; never a legal opinion."
        )
    )
    legal_rights: list[LegalRightEntry]
    approvals: list[ApprovalEntry] = Field(description="Current approvals only.")
    source_document: DocumentReference = Field(
        description="Most recently approved current source document (see source_documents)."
    )
    source_documents: list[ApprovedSourceDocument] = Field(
        description="Every document with a current approval that contributes to this passport."
    )
    excluded_documents: list[ExcludedDocument] = Field(
        default_factory=list,
        description=(
            "Documents whose approval is no longer current (e.g. a newer version awaits review, or "
            "the stored source file no longer matches the approved SHA-256)."
        ),
    )
    extraction_metadata: ExtractionMetadata
    verification_summary: VerificationSummary = Field(
        description="What the verification indicators of this passport establish (Stage 9B-4, step 6)."
    )
    # Mandatory in every response — passport ≠ legal certificate
    disclaimer: str = Field(description="Legal disclaimer — mandatory in every response.")
    retrieved_at: datetime = Field(description="Timestamp when this passport was retrieved (UTC).")
