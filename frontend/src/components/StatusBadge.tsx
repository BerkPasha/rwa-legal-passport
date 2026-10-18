import type {
  ExtractionStatus,
  ApprovalType,
  ApprovalStaleReason,
  ExtractionApprovalStateValue,
  LegalClaim,
  VerificationLevel,
} from '../types'
import { claimEvidenceLabel } from '../evidence'

// ── Extraction status ─────────────────────────────────────────────────────────

const extractionStyles: Record<ExtractionStatus, string> = {
  draft: 'bg-slate-100 text-slate-600 ring-slate-200',
  under_review: 'bg-amber-100 text-amber-700 ring-amber-200',
  approved: 'bg-green-100 text-green-700 ring-green-200',
}

const extractionLabels: Record<ExtractionStatus, string> = {
  draft: 'Draft',
  under_review: 'Under Review',
  approved: 'Approved',
}

interface ExtractionStatusBadgeProps {
  status: ExtractionStatus
  className?: string
}

export function ExtractionStatusBadge({ status, className = '' }: ExtractionStatusBadgeProps) {
  const style = extractionStyles[status] ?? 'bg-slate-100 text-slate-600 ring-slate-200'
  const label = extractionLabels[status] ?? status
  return (
    <span
      data-testid="extraction-status-badge"
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style} ${className}`}
    >
      {label}
    </span>
  )
}

// ── Approval type ─────────────────────────────────────────────────────────────

const approvalStyles: Record<ApprovalType, string> = {
  issuer_attestation: 'bg-blue-100 text-blue-700 ring-blue-200',
  legal_verification: 'bg-indigo-100 text-indigo-700 ring-indigo-200',
}

// An approval is an internal attestation; the machine value legal_verification does not mean
// that legal validity was verified (Stage 9B-4, step 10)
const approvalLabels: Record<ApprovalType, string> = {
  issuer_attestation: 'Issuer Attestation',
  legal_verification: 'Independent Legal Review',
}

interface ApprovalTypeBadgeProps {
  type: ApprovalType
  className?: string
}

export function ApprovalTypeBadge({ type, className = '' }: ApprovalTypeBadgeProps) {
  const style = approvalStyles[type] ?? 'bg-slate-100 text-slate-600 ring-slate-200'
  const label = approvalLabels[type] ?? type
  return (
    <span
      data-testid="approval-type-badge"
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${style} ${className}`}
    >
      {label}
    </span>
  )
}

// ── Claim evidence (Stage 9B-4) ───────────────────────────────────────────────

// The backend's quotation check for one claim: "found" proves source-text presence only
export function EvidenceBadge({ claim }: { claim: LegalClaim }) {
  const status = claim.is_unknown ? 'unknown' : claim.evidence?.status === 'verified' ? 'verified' : 'failed'
  const style = {
    unknown: 'bg-orange-100 text-orange-700 ring-orange-200',
    verified: 'bg-sky-100 text-sky-700 ring-sky-200',
    failed: 'bg-rose-100 text-rose-700 ring-rose-200',
  }[status]
  return (
    <span
      data-testid={`evidence-badge-${status}`}
      title={claim.is_unknown ? 'No value is asserted; no evidence is required.' : claim.evidence?.explanation}
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      {claimEvidenceLabel(claim)}
    </span>
  )
}

// ── Claim verification ────────────────────────────────────────────────────────

// Earlier claim badge, kept for compatibility; the review page shows EvidenceBadge, because
// a cited passage is not a verification.
interface ClaimStatusBadgeProps {
  isUnknown: boolean
  verified: boolean
}

export function ClaimStatusBadge({ isUnknown, verified }: ClaimStatusBadgeProps) {
  if (isUnknown) {
    return (
      <span
        data-testid="claim-status-badge-unknown"
        className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-orange-100 text-orange-700 ring-1 ring-inset ring-orange-200"
      >
        Unknown
      </span>
    )
  }
  if (verified) {
    return (
      <span
        data-testid="claim-status-badge-verified"
        className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-emerald-100 text-emerald-700 ring-1 ring-inset ring-emerald-200"
      >
        Verified
      </span>
    )
  }
  return (
    <span
      data-testid="claim-status-badge-unverified"
      className="inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium bg-amber-100 text-amber-700 ring-1 ring-inset ring-amber-200"
    >
      Unverified
    </span>
  )
}

// ── Approval currency (Stage 9B-3) ────────────────────────────────────────────

export const STALE_REASON_LABELS: Record<ApprovalStaleReason, string> = {
  unbound_legacy_approval: 'Approved before approval binding existed',
  binding_integrity_failed: 'Approval record failed its integrity check',
  re_review_in_progress: 'Re-review in progress',
  document_not_active: 'Document is not active',
  document_version_superseded: 'A newer document version exists',
  document_hash_mismatch: 'Document file hash differs from the approved version',
  evidence_changed: 'Evidence differs from what was approved',
  asset_information_changed: 'Asset information changed after approval',
  asset_not_active: 'Asset is not active',
  source_text_changed: 'The stored page text differs from the approved text',
  evidence_not_verified: 'A quotation is no longer found on its cited page',
  source_file_integrity_failed: 'The stored source file does not match the approved file',
  competing_current_extractions: 'Several extractions of this document version are approved; none is used',
  self_reviewed_approval: 'Only a self-reviewed legal approval exists; it is not counted',
}

const approvalStateStyles: Record<Exclude<ExtractionApprovalStateValue, 'not_approved'>, string> = {
  current: 'bg-green-100 text-green-700 ring-green-200',
  stale: 'bg-amber-100 text-amber-800 ring-amber-300',
  superseded: 'bg-rose-100 text-rose-700 ring-rose-200',
}

const approvalStateLabels: Record<Exclude<ExtractionApprovalStateValue, 'not_approved'>, string> = {
  current: 'Approval current',
  stale: 'Approval stale',
  superseded: 'Superseded version',
}

export function ApprovalStateBadge({ state }: { state: ExtractionApprovalStateValue | undefined }) {
  if (!state || state === 'not_approved') return null
  return (
    <span
      data-testid="approval-state-badge"
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${approvalStateStyles[state]}`}
    >
      {approvalStateLabels[state]}
    </span>
  )
}

const verificationLevelStyles: Record<VerificationLevel, string> = {
  issuer_attested: 'bg-amber-100 text-amber-800 ring-amber-300',
  partially_independently_verified: 'bg-sky-100 text-sky-700 ring-sky-200',
  independently_verified: 'bg-emerald-100 text-emerald-700 ring-emerald-200',
}

// The levels describe reviewer attestation behind source-verified quotations, never legal validity
export const VERIFICATION_LEVEL_LABELS: Record<VerificationLevel, string> = {
  issuer_attested: 'Issuer attested — not independently verified',
  partially_independently_verified: 'Partly attested by an independent legal reviewer',
  independently_verified: 'Attested by an independent legal reviewer',
}

export function VerificationLevelBadge({ level }: { level: VerificationLevel }) {
  return (
    <span
      data-testid="verification-level-badge"
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${verificationLevelStyles[level]}`}
    >
      {VERIFICATION_LEVEL_LABELS[level]}
    </span>
  )
}

interface PassportRightBadgeProps {
  isUnknown: boolean
  verified: boolean
  independentlyVerified: boolean
  conflict: boolean
}

// Passport field status: evidence alone is never labelled as independent verification
export function PassportRightBadge({ isUnknown, verified, independentlyVerified, conflict }: PassportRightBadgeProps) {
  const [label, style] = conflict
    ? ['Conflicting sources', 'bg-rose-100 text-rose-700 ring-rose-200']
    : isUnknown
      ? ['Unknown', 'bg-orange-100 text-orange-700 ring-orange-200']
      : independentlyVerified
        ? ['Independent reviewer attested', 'bg-emerald-100 text-emerald-700 ring-emerald-200']
        : verified
          ? ['Evidence cited', 'bg-sky-100 text-sky-700 ring-sky-200']
          : ['Unverified', 'bg-amber-100 text-amber-700 ring-amber-200']
  return (
    <span
      data-testid="passport-right-badge"
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${style}`}
    >
      {label}
    </span>
  )
}
