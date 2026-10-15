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

// ── Domain models ─────────────────────────────────────────────────────────────

export interface Asset {
  id: string
  blockchain_network: BlockchainNetwork
  stellar_asset_code: string
  stellar_issuer_public_key: string
  issuer_organization_name: string
  asset_type: AssetType
  jurisdiction: string
  status: AssetStatus
  created_at: string
  updated_at: string
}

export interface Document {
  id: string
  asset_id: string
  document_type: DocumentType
  title: string
  current_version_number: number
  sha256_hash: string
  status: string
  created_at: string
  updated_at: string
}

export interface DocumentVersion {
  id: string
  document_id: string
  version_number: number
  sha256_hash: string
  file_size_bytes: number
  created_at: string
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

export interface LegalPassport {
  asset: {
    asset_id: string
    asset_code: string
    issuer_key: string
    network: BlockchainNetwork
    issuer_organization: string
    asset_type: string
    jurisdiction: string
  }
  legal_rights: {
    field_name: string
    value: string
    is_unknown: boolean
    verified: boolean
    source_page: number | null
    supporting_passage: string | null
  }[]
  approvals: {
    approval_type: ApprovalType
    reviewer_role: ReviewerRole
    approved_at: string
    notes: string | null
  }[]
  source_document: {
    document_id: string
    title: string
    document_type: string
    version_number: number
  }
  extraction_metadata: {
    extraction_id: string
    ai_model: string
    extraction_timestamp: string
    passport_version: number
    effective_date: string
  }
  disclaimer: string
  retrieved_at: string
}

// ── Request shapes ────────────────────────────────────────────────────────────

export interface CreateAssetPayload {
  blockchain_network: BlockchainNetwork
  stellar_asset_code: string
  stellar_issuer_public_key: string
  issuer_organization_name: string
  asset_type: AssetType
  jurisdiction: string
}

export interface ClaimCorrectionPayload {
  corrected_value: string
  is_unknown: boolean
  corrected_source_page?: number
  corrected_passage?: string
  correction_note?: string
}

export interface ApprovalPayload {
  notes?: string
}
