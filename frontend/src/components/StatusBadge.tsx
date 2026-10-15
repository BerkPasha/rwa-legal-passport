import type { ExtractionStatus, ApprovalType } from '../types'

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

const approvalLabels: Record<ApprovalType, string> = {
  issuer_attestation: 'Issuer Attestation',
  legal_verification: 'Legal Verification',
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

// ── Claim verification ────────────────────────────────────────────────────────

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
