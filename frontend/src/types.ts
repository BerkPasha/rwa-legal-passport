// ── Enums ────────────────────────────────────────────────────────────────────

export type BlockchainNetwork = 'stellar-mainnet' | 'stellar-testnet'
export type AssetType = 'real_estate' | 'commodity' | 'equity' | 'bond' | 'fund' | 'other'
export type AssetStatus = 'active' | 'suspended' | 'retired'
export type DocumentType =
  | 'prospectus'
  | 'legal_opinion'
  | 'title_deed'
  | 'regulatory_filing'
  | 'audit_report'
  | 'other'
export type ExtractionStatus = 'draft' | 'under_review' | 'approved'
export type ReviewerRole = 'issuer_reviewer' | 'independent_legal_reviewer'
export type ApprovalType = 'issuer_attestation' | 'legal_verification'

// Stage 9B-3: approvals are bound to an exact document version, evidence and asset information,
// and their currency is evaluated by the backend on every read.
export type ExtractionApprovalStateValue = 'not_approved' | 'current' | 'stale' | 'superseded'
export type ApprovalStaleReason =
  | 'unbound_legacy_approval'
  | 'binding_integrity_failed'
  | 're_review_in_progress'
  | 'document_not_active'
  | 'document_version_superseded'
  | 'document_hash_mismatch'
  | 'evidence_changed'
  | 'asset_information_changed'
  | 'asset_not_active'
  // Stage 9B-4
  | 'source_text_changed'
  | 'evidence_not_verified'
  | 'source_file_integrity_failed'
export type VerificationLevel =
  | 'issuer_attested'
  | 'partially_independently_verified'
  | 'independently_verified'
export type DocumentStatus = 'active' | 'archived'

// Stage 9B-4: result of the backend's deterministic quotation check. Only 'verified' is positive,
// and it proves source-text presence on the cited page only — never legal correctness.
export type EvidenceStatus =
  | 'verified'
  | 'not_found'
  | 'found_on_other_page'
  | 'page_missing'
  | 'page_out_of_range'
  | 'no_text_layer'
  | 'quotation_missing'
  | 'quotation_too_short'
  | 'document_version_mismatch'
  | 'source_text_unavailable'
  | 'source_text_invalid'
  | 'not_applicable'
export type CorrectionRejection =
  | 'evidence_required'
  | 'unknown_with_evidence'
  | 'unknown_value_for_known_claim'
  | 'evidence_not_verified'
export type ValueOrigin = 'ai_extraction' | 'reviewer_correction'
export type ReviewerAttestationStatus = 'independent_reviewer_attested' | 'issuer_attested' | 'not_applicable'

// ── Domain models ─────────────────────────────────────────────────────────────

export interface Asset {
  id: string
  // Owning issuer (tenant) from the backend access-control registry; immutable
  issuer_id: string
  blockchain_network: BlockchainNetwork
  stellar_asset_code: string
  stellar_issuer_public_key: string
  issuer_organization_name: string
  asset_type: AssetType
  jurisdiction: string
  status: AssetStatus
  created_at: string
  updated_at: string
  // Incremented on every material change; approvals bound to an older revision are stale
  material_revision?: number
}

// Same shape as the backend's DocumentResponse: the hash belongs to a version, and
// current_version is populated in detail responses only (null in list responses).
export interface Document {
  id: string
  asset_id: string
  document_type: DocumentType
  title: string
  status: DocumentStatus
  current_version_number: number
  created_at: string
  updated_at: string
  current_version?: DocumentVersion | null
}

export interface DocumentVersion {
  id: string
  document_id: string
  version_number: number
  original_filename: string
  file_size: number
  sha256_hash: string
  created_at: string
}

export interface DocumentPage {
  page_number: number
  text_content: string
}

// GET /documents/{id}/versions/{n}/text (Stage 9B-4, step 8): the page texts recorded at upload
// for exactly this version. Evidence material, not proof of legal validity.
export interface DocumentVersionText {
  document_id: string
  document_status: DocumentStatus
  version_id: string
  version_number: number
  version_created_at: string
  sha256_hash: string
  is_current_version: boolean
  current_version_number: number
  page_count: number
  source_text_sha256: string
  text_origin: string
  notice: string
  pages: DocumentPage[]
}

// Deterministic check of a claim's quotation against its extraction's own document version
export interface ClaimEvidence {
  status: EvidenceStatus
  cited_page: number | null
  found_on_pages: number[]
  page_count: number
  // The checked version (the extraction's own); null if that version's text is unavailable
  version_number: number | null
  verification_method: string
  normalization_version: number
  scope: string
  explanation: string
}

export interface LegalClaim {
  id: string
  extraction_id: string
  field_name: string
  value: string
  is_unknown: boolean
  source_page: number | null
  supporting_passage: string | null
  created_at: string
  evidence: ClaimEvidence
}

export interface LegalExtraction {
  id: string
  document_id: string
  version_id: string
  status: ExtractionStatus
  ai_model: string
  disclaimer: string
  extraction_timestamp: string
  created_at: string
  claims: LegalClaim[]
  approval_state: ExtractionApprovalState
}

export interface ExtractionApprovalState {
  state: ExtractionApprovalStateValue
  // False if a newer document version exists; this extraction can no longer be approved
  version_is_current: boolean
  current_approval_types: ApprovalType[]
  reasons: ApprovalStaleReason[]
}

export interface ReviewCorrection {
  id: string
  extraction_id: string
  claim_id: string
  reviewer_id: string
  reviewer_role: ReviewerRole
  original_value: string
  original_is_unknown: boolean
  original_passage: string | null
  corrected_value: string
  corrected_is_unknown: boolean
  corrected_passage: string | null
  correction_note: string | null
  created_at: string
  // Source page history (Stage 9B-4, step 7); false for records that did not record pages
  original_source_page: number | null
  corrected_source_page: number | null
  page_history_recorded: boolean
}

export interface ReviewApproval {
  id: string
  extraction_id: string
  reviewer_id: string
  reviewer_role: ReviewerRole
  approval_type: ApprovalType
  disclaimer: string
  notes: string | null
  approved_at: string
  created_at: string
  // Binding (Stage 9B-3); null only for approvals created before the binding existed
  document_id?: string | null
  document_version_id?: string | null
  document_version_number?: number | null
  document_sha256?: string | null
  evidence_digest?: string | null
  // 1: legacy claim list; 2: claims bound to their verified evidence (Stage 9B-4, step 5)
  evidence_snapshot_format?: number | null
  evidence_record?: unknown
  asset_material_revision?: number | null
  binding_digest?: string | null
  currency: { is_current: boolean; reasons: ApprovalStaleReason[] }
}

export interface ReviewStatus {
  extraction_id: string
  status: ExtractionStatus
  reviewer_id: string
  reviewer_role: ReviewerRole
}

export interface BlockchainRecord {
  asset_type: string
  num_accounts: number
  total_supply: string
  flags: {
    auth_required: boolean
    auth_revocable: boolean
    auth_immutable: boolean
    auth_clawback_enabled: boolean
  }
}

export interface BlockchainStatus {
  asset_id: string
  asset_code: string
  issuer_key: string
  network: BlockchainNetwork
  horizon_url: string
  found_on_network: boolean
  record: BlockchainRecord | null
  retrieved_at: string
  disclaimer: string
}

export interface PassportRightSource {
  document_id: string
  document_title: string
  version_number: number
  value: string
  is_unknown: boolean
  source_page: number | null
  supporting_passage: string | null
  independently_verified: boolean
  evidence_status: EvidenceStatus
  value_origin: ValueOrigin
  reviewer_attestation_status: ReviewerAttestationStatus
}

export interface VerificationSummary {
  source_verified_field_count: number
  independently_attested_field_count: number
  unverified_field_count: number
  unknown_field_count: number
  conflicting_field_count: number
  reviewer_attestation_recorded: boolean
  independent_reviewer_attestation_recorded: boolean
  evidence_scope: string
  verification_method: string
  normalization_version: number
  legal_interpretation_status: string
  meaning: string
}

export interface LegalPassport {
  asset: {
    asset_id: string
    asset_code: string
    issuer_key: string
    network: BlockchainNetwork
    issuer_organization: string
    asset_type: string
    jurisdiction: string
    status: AssetStatus
  }
  // Issuer attestation is never reported as independent legal verification
  verification_level: VerificationLevel
  legal_rights: {
    field_name: string
    value: string
    is_unknown: boolean
    // Source evidence exists; this is NOT independent legal verification
    verified: boolean
    independently_verified: boolean
    // Currently approved documents state different values; no value is chosen
    conflict: boolean
    source_page: number | null
    supporting_passage: string | null
    sources: PassportRightSource[]
    evidence_status: EvidenceStatus
    verification_method: string
    // Document and approved version of the verified quotation; null unless verified
    document_id: string | null
    document_version_number: number | null
    value_origin: ValueOrigin | null
    reviewer_attestation_status: ReviewerAttestationStatus
  }[]
  // Current approvals only
  approvals: {
    approval_type: ApprovalType
    reviewer_role: ReviewerRole
    approved_at: string
    notes: string | null
    document_id: string
    document_version_number: number
    binding_digest: string
  }[]
  source_document: {
    document_id: string
    title: string
    document_type: string
    version_number: number
  }
  source_documents: {
    document_id: string
    title: string
    document_type: string
    version_number: number
    approval_types: ApprovalType[]
    independently_verified: boolean
    source_verified_claim_count: number
    unknown_claim_count: number
  }[]
  // Documents whose approval is no longer current (e.g. a newer version awaits review)
  excluded_documents: {
    document_id: string
    title: string
    document_type: string
    reasons: ApprovalStaleReason[]
  }[]
  extraction_metadata: {
    extraction_id: string
    ai_model: string
    extraction_timestamp: string
    passport_version: number
    effective_date: string
  }
  verification_summary: VerificationSummary
  disclaimer: string
  retrieved_at: string
}

// ── Request shapes ────────────────────────────────────────────────────────────

export interface CreateAssetPayload {
  issuer_id: string
  blockchain_network: BlockchainNetwork
  stellar_asset_code: string
  stellar_issuer_public_key: string
  issuer_organization_name: string
  asset_type: AssetType
  jurisdiction: string
}

// Stage 9B-4, step 7: an omitted page / passage keeps the current one (unchanged value only);
// null clears it. A changed value needs both. The backend verifies the resulting quotation.
export interface ClaimCorrectionPayload {
  corrected_value: string
  is_unknown: boolean
  corrected_source_page?: number | null
  corrected_passage?: string | null
  correction_note?: string
  // Mandatory optimistic concurrency precondition (issue A): the claim exactly as shown to the
  // reviewer; a stored difference gives 409, a missing one 422
  expected_claim: ExpectedClaimState
}

export interface ExpectedClaimState {
  value: string
  is_unknown: boolean
  source_page: number | null
  supporting_passage: string | null
}

export interface ApprovalPayload {
  notes?: string
}
