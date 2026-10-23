import type { ApprovalType, LegalPassport, ReviewerRole } from '../types'
import { EVIDENCE_SCOPE_NOTE, evidenceStatusLabel } from '../evidence'
import {
  ApprovalTypeBadge,
  PassportRightBadge,
  STALE_REASON_LABELS,
  VerificationLevelBadge,
} from './StatusBadge'
import { DisclaimerBanner } from './DisclaimerBanner'

// The Legal Passport as the institutional endpoint returns it: rights, evidence, approvals and metadata.
// Used by the passport lookup (pages/PassportPreview.tsx) and by the public read-only demo (demo/).

const FIELD_LABELS: Record<string, string> = {
  ownership_nature: 'Ownership Nature',
  income_rights: 'Income Rights',
  voting_rights: 'Voting Rights',
  redemption_rights: 'Redemption Rights',
  transfer_restrictions: 'Transfer Restrictions',
  jurisdiction: 'Jurisdiction',
  insolvency_provisions: 'Insolvency Provisions',
}

// The public passport exposes only the reviewer's role, never their identity.
const REVIEWER_ROLE_LABELS: Record<ReviewerRole, string> = {
  issuer_reviewer: 'Issuer Reviewer',
  independent_legal_reviewer: 'Independent Legal Reviewer',
}

const APPROVAL_TYPE_TEXT: Record<ApprovalType, string> = {
  issuer_attestation: 'issuer attestation',
  legal_verification: 'independent legal reviewer attestation',
}

export function PassportCard({ passport }: { passport: LegalPassport }) {
  const { asset, extraction_metadata: meta, source_document: sourceDoc } = passport
  const sourceTitle = (documentId: string | null) =>
    passport.source_documents.find(d => d.document_id === documentId)?.title ?? 'document'
  return (
    <div className="space-y-6" data-testid="passport-card">
      <DisclaimerBanner variant="legal" text={passport.disclaimer} />

      {/* Asset summary */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-900">
                Legal Passport — v{meta.passport_version}
              </h3>
              <p className="mt-1 text-sm text-slate-500">
                Asset: <span className="font-mono font-medium text-slate-800">{asset.asset_code}</span>
                {' · '}
                {asset.issuer_organization}
              </p>
              <div className="mt-2">
                {/* Without any source-verified field, the level's floor value says nothing */}
                {passport.verification_summary && passport.verification_summary.source_verified_field_count === 0 ? (
                  <span
                    data-testid="verification-level-badge"
                    className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-0.5 text-xs font-medium text-slate-700 ring-1 ring-inset ring-slate-300"
                  >
                    No source-verified fields
                  </span>
                ) : (
                  <VerificationLevelBadge level={passport.verification_level} />
                )}
              </div>
            </div>
            <div className="text-right">
              <p className="text-xs text-slate-400">Effective Date</p>
              <p className="font-mono text-sm font-medium text-slate-800">
                {new Date(meta.effective_date).toLocaleDateString('en-US', {
                  year: 'numeric', month: 'short', day: 'numeric',
                })}
              </p>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-0 divide-x divide-y divide-slate-100 sm:grid-cols-4">
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Network</p>
            <p className="mt-1 text-sm font-medium text-slate-800">
              {asset.network === 'stellar-mainnet' ? 'Stellar Mainnet' : 'Stellar Testnet'}
            </p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Asset Type</p>
            <p className="mt-1 text-sm font-medium capitalize text-slate-800">
              {asset.asset_type.replace('_', ' ')}
            </p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Jurisdiction</p>
            <p className="mt-1 font-mono text-sm font-medium text-slate-800">{asset.jurisdiction}</p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Passport Version</p>
            <p className="mt-1 text-sm font-medium text-slate-800">v{meta.passport_version}</p>
          </div>
        </div>
      </div>

      {passport.excluded_documents.length > 0 && (
        <div data-testid="excluded-documents" className="rounded-xl bg-amber-50 p-4 ring-1 ring-amber-300">
          <p className="font-medium text-amber-900">Some documents are not included in this passport</p>
          <p className="mt-1 text-sm text-amber-800">
            Their earlier approval is no longer current; they are shown again only after a new approval.
          </p>
          <ul className="mt-2 list-disc pl-5 text-sm text-amber-800">
            {passport.excluded_documents.map(d => (
              <li key={d.document_id}>
                {d.title}: {d.reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ')}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Legal rights */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extracted Legal Rights</h3>
          <p className="mt-1 text-sm text-slate-500">
            {passport.legal_rights.length} field{passport.legal_rights.length !== 1 ? 's' : ''} from
            currently approved extractions — AI-extracted values attested by reviewers, not legal opinions
          </p>
          {passport.verification_summary && (
            <p data-testid="verification-meaning" className="mt-2 text-xs text-slate-500">
              {passport.verification_summary.meaning}
            </p>
          )}
        </div>
        {passport.legal_rights.length === 0 ? (
          <p className="px-6 py-8 text-center text-sm text-slate-500">No legal rights data available.</p>
        ) : (
          <div className="divide-y divide-slate-100">
            {passport.legal_rights.map((right, i) => (
              <div key={i} className="px-6 py-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      {FIELD_LABELS[right.field_name] ?? right.field_name}
                    </p>
                    {right.conflict ? (
                      <div className="mt-1">
                        <p className="text-sm font-medium text-rose-700">
                          Approved documents disagree — no value is presented as approved
                        </p>
                        <ul className="mt-1 space-y-1 text-xs text-slate-700">
                          {right.sources.map(src => (
                            <li key={src.document_id}>
                              <span className="font-medium">{src.document_title} (v{src.version_number}):</span> {src.value}
                            </li>
                          ))}
                        </ul>
                      </div>
                    ) : (
                      <p className="mt-1 text-sm text-slate-800">{right.value}</p>
                    )}
                    {!right.conflict && right.sources.length > 1 && (
                      <p className="mt-1 text-xs text-slate-500">Stated consistently in {right.sources.length} documents</p>
                    )}
                    {!right.conflict && right.value_origin === 'reviewer_correction' && (
                      <p data-testid="value-origin" className="mt-1 text-xs text-slate-500">Value corrected by a reviewer before approval</p>
                    )}
                    {!right.conflict && right.value_origin === 'manual_entry' && (
                      <p data-testid="value-origin" className="mt-1 text-xs text-slate-500">
                        Entered manually by a user of the issuer from the cited page (no AI)
                      </p>
                    )}
                    {/* Who stands behind the value: the issuer's own statement, or also an independent legal reviewer (Stage 10B-3) */}
                    {!right.is_unknown && !right.conflict && right.reviewer_attestation_status !== 'not_applicable' && (
                      <p data-testid="passport-attestation" className="mt-1 text-xs text-slate-500">
                        {right.reviewer_attestation_status === 'independent_reviewer_attested'
                          ? 'Approved by the issuer and attested by an independent legal reviewer (internal attestations, not a legal opinion)'
                          : 'Issuer attestation only — not reviewed by an independent legal reviewer'}
                      </p>
                    )}
                    {!right.is_unknown && !right.conflict && !right.verified && right.evidence_status && (
                      <p data-testid="passport-evidence-status" className="mt-1 text-xs text-amber-700">
                        Quotation not verified: {evidenceStatusLabel(right.evidence_status)}
                      </p>
                    )}
                    {right.supporting_passage && (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-indigo-600 hover:text-indigo-800">
                          View source passage
                          {right.source_page != null && (
                            <span data-testid="passport-passage-location" className="ml-1 text-slate-500">
                              (page {right.source_page}
                              {right.document_version_number != null ? `, ${sourceTitle(right.document_id)} version ${right.document_version_number}` : ''})
                            </span>
                          )}
                        </summary>
                        <blockquote className="mt-2 border-l-2 border-indigo-200 pl-3 text-xs italic text-slate-600">
                          {right.supporting_passage}
                        </blockquote>
                        <p className="mt-1 text-xs text-slate-400">{EVIDENCE_SCOPE_NOTE}</p>
                      </details>
                    )}
                  </div>
                  <PassportRightBadge
                    isUnknown={right.is_unknown}
                    verified={right.verified ?? false}
                    independentlyVerified={right.independently_verified ?? false}
                    conflict={right.conflict ?? false}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Approvals */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Approval Chain</h3>
        </div>
        {passport.approvals.length === 0 ? (
          <p className="px-6 py-8 text-center text-sm text-slate-500">No approvals on record.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {passport.approvals.map((ap, i) => (
              <li key={i} className="flex items-center justify-between px-6 py-4">
                <div>
                  <p className="text-sm font-medium text-slate-800">
                    {REVIEWER_ROLE_LABELS[ap.reviewer_role] ?? ap.reviewer_role}
                  </p>
                  <p className="text-xs text-slate-400">
                    {new Date(ap.approved_at).toLocaleString('en-US')}
                  </p>
                </div>
                <ApprovalTypeBadge type={ap.approval_type} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Extraction metadata */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extraction Metadata</h3>
        </div>
        <div className="px-6 py-4">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-slate-400">Extraction ID</dt>
              <dd className="mt-1 font-mono text-xs text-slate-800">{meta.extraction_id}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Claim Source</dt>
              <dd data-testid="passport-extraction-source" className="mt-1 text-xs text-slate-800">
                {meta.extraction_source === 'manual' ? 'Manual entry (no AI model)' : 'Extraction service'}
              </dd>
            </div>
            {meta.extraction_source !== 'manual' && (
              <div>
                <dt className="text-xs text-slate-400">Model Used</dt>
                <dd className="mt-1 font-mono text-xs text-slate-800">{meta.ai_model || '—'}</dd>
              </div>
            )}
            <div>
              <dt className="text-xs text-slate-400">{meta.extraction_source === 'manual' ? 'Entered At' : 'Extracted At'}</dt>
              <dd className="mt-1 text-xs text-slate-800">
                {new Date(meta.extraction_timestamp).toLocaleString('en-US')}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Source Document</dt>
              <dd className="mt-1 text-xs text-slate-800">
                {sourceDoc.title} (v{sourceDoc.version_number})
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Document Type</dt>
              <dd className="mt-1 text-xs capitalize text-slate-800">
                {sourceDoc.document_type.replace('_', ' ')}
              </dd>
            </div>
            <div className="col-span-2 sm:col-span-3">
              <dt className="text-xs text-slate-400">Currently Approved Source Documents</dt>
              <dd className="mt-1 space-y-1 text-xs text-slate-800">
                {passport.source_documents.map(d => (
                  <p key={d.document_id}>
                    {d.title} (v{d.version_number}) —{' '}
                    {d.approval_types.map(t => APPROVAL_TYPE_TEXT[t] ?? t).join(', ') || 'no current approval'}
                    {d.independently_verified ? '; independent reviewer attestation on source-verified fields' : ''}
                    {d.source_verified_claim_count != null && `; ${d.source_verified_claim_count} source-verified field${d.source_verified_claim_count !== 1 ? 's' : ''}`}
                  </p>
                ))}
              </dd>
            </div>
          </dl>
        </div>
      </div>
    </div>
  )
}
