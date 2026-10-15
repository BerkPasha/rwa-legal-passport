import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  approveExtraction,
  correctClaim,
  getApprovals,
  getCorrections,
  getExtraction,
  startReview,
} from '../api'
import type {
  ApprovalPayload,
  ClaimCorrectionPayload,
  LegalClaim,
  LegalExtraction,
  ReviewApproval,
  ReviewCorrection,
} from '../types'
import { PageSpinner } from '../components/Spinner'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { ApprovalTypeBadge, ClaimStatusBadge, ExtractionStatusBadge } from '../components/StatusBadge'
import { useAuth } from '../AuthContext'

function fmt(iso: string) {
  return new Date(iso).toLocaleString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}

const FIELD_LABELS: Record<string, string> = {
  ownership_nature: 'Ownership Nature',
  income_rights: 'Income Rights',
  voting_rights: 'Voting Rights',
  redemption_rights: 'Redemption Rights',
  transfer_restrictions: 'Transfer Restrictions',
  jurisdiction: 'Jurisdiction',
  insolvency_provisions: 'Insolvency Provisions',
}

function CorrectionForm({
  claim,
  token,
  extractionId,
  onClose,
  onSaved,
}: {
  claim: LegalClaim
  token: string
  extractionId: string
  onClose: () => void
  onSaved: () => void
}) {
  const [form, setForm] = useState<ClaimCorrectionPayload>({
    corrected_value: claim.value,
    is_unknown: claim.is_unknown,
    corrected_passage: claim.supporting_passage ?? '',
    correction_note: '',
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setErr('')
    try {
      const payload: ClaimCorrectionPayload = {
        corrected_value: form.corrected_value,
        is_unknown: form.is_unknown,
      }
      if (form.corrected_passage) payload.corrected_passage = form.corrected_passage
      if (form.correction_note) payload.correction_note = form.correction_note
      await correctClaim(extractionId, claim.id, payload, token)
      onSaved()
    } catch (error) {
      setErr(error instanceof ApiError ? error.message : 'Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <tr>
      <td colSpan={5} className="bg-amber-50 p-5">
        <form onSubmit={handleSubmit} className="space-y-3">
          <p className="text-sm font-semibold text-amber-900">
            Correcting: {FIELD_LABELS[claim.field_name] ?? claim.field_name}
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Corrected Value</label>
              <textarea
                required
                value={form.corrected_value}
                onChange={e => setForm(f => ({ ...f, corrected_value: e.target.value }))}
                rows={2}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Supporting Passage</label>
              <textarea
                value={form.corrected_passage ?? ''}
                onChange={e => setForm(f => ({ ...f, corrected_passage: e.target.value }))}
                rows={2}
                placeholder="Verbatim quote from the document…"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">Correction Note (optional)</label>
            <input
              value={form.correction_note ?? ''}
              onChange={e => setForm(f => ({ ...f, correction_note: e.target.value }))}
              placeholder="Reason for correction…"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id="is_unknown"
              checked={form.is_unknown}
              onChange={e => setForm(f => ({ ...f, is_unknown: e.target.checked }))}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600"
            />
            <label htmlFor="is_unknown" className="text-sm text-slate-700">Mark as unknown (insufficient evidence)</label>
          </div>
          {err && <p className="text-sm text-red-600">{err}</p>}
          <div className="flex gap-2">
            <button type="button" onClick={onClose} className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
            <button type="submit" disabled={saving} className="rounded-lg bg-amber-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-60">
              {saving ? 'Saving…' : 'Save Correction'}
            </button>
          </div>
        </form>
      </td>
    </tr>
  )
}

export function ExtractionDetail() {
  const { id: assetId, docId, extractionId } = useParams<{
    id: string
    docId: string
    extractionId: string
  }>()
  const { reviewerToken } = useAuth()
  const [token, setToken] = useState(reviewerToken)
  const [extraction, setExtraction] = useState<LegalExtraction | null>(null)
  const [corrections, setCorrections] = useState<ReviewCorrection[]>([])
  const [approvals, setApprovals] = useState<ReviewApproval[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedClaim, setExpandedClaim] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)
  const [approving, setApproving] = useState(false)
  const [approvalNotes, setApprovalNotes] = useState('')
  const [actionError, setActionError] = useState('')

  const loadAll = async () => {
    if (!extractionId) return
    try {
      const ex = await getExtraction(extractionId)
      setExtraction(ex)
      if (token) {
        const [corr, approv] = await Promise.all([
          getCorrections(extractionId, token).catch(() => []),
          getApprovals(extractionId, token).catch(() => []),
        ])
        setCorrections(corr)
        setApprovals(approv)
      }
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'Failed to load')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { setLoading(true); loadAll() }, [extractionId])
  useEffect(() => { setToken(reviewerToken) }, [reviewerToken])

  const handleStartReview = async () => {
    if (!extractionId || !token) return
    setStarting(true)
    setActionError('')
    try {
      await startReview(extractionId, token)
      await loadAll()
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : 'Failed')
    } finally {
      setStarting(false)
    }
  }

  const handleApprove = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!extractionId || !token) return
    setApproving(true)
    setActionError('')
    const data: ApprovalPayload = {}
    if (approvalNotes) data.notes = approvalNotes
    try {
      await approveExtraction(extractionId, data, token)
      await loadAll()
    } catch (e) {
      setActionError(e instanceof ApiError ? e.message : 'Failed')
    } finally {
      setApproving(false)
    }
  }

  if (loading) return <PageSpinner />
  if (error) return <div className="rounded-lg bg-red-50 p-4 text-red-700">{error}</div>
  if (!extraction) return null

  const knownCount = extraction.claims.filter(c => !c.is_unknown).length
  const verifiedCount = extraction.claims.filter(
    c => !c.is_unknown && !!c.supporting_passage,
  ).length

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-sm text-slate-500">
        <Link to="/assets" className="hover:text-slate-900">Assets</Link>
        <span>/</span>
        <Link to={`/assets/${assetId}`} className="hover:text-slate-900">Asset</Link>
        <span>/</span>
        <span className="font-medium text-slate-900">Extraction</span>
      </nav>

      {/* Header */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <div className="flex flex-wrap items-start gap-4">
          <div className="flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-bold text-slate-900">AI Extraction Result</h2>
              <ExtractionStatusBadge status={extraction.status} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-4 text-sm text-slate-500">
              <span>Model: <span className="font-mono font-medium text-slate-700">{extraction.ai_model}</span></span>
              <span>Extracted: {fmt(extraction.extraction_timestamp)}</span>
            </div>
          </div>
          <div className="flex gap-3 text-center">
            <div className="rounded-lg bg-slate-50 px-4 py-2">
              <p className="text-2xl font-bold text-slate-900">{extraction.claims.length}</p>
              <p className="text-xs text-slate-500">Total Fields</p>
            </div>
            <div className="rounded-lg bg-emerald-50 px-4 py-2">
              <p className="text-2xl font-bold text-emerald-700">{verifiedCount}</p>
              <p className="text-xs text-slate-500">Verified</p>
            </div>
            <div className="rounded-lg bg-orange-50 px-4 py-2">
              <p className="text-2xl font-bold text-orange-700">{extraction.claims.length - knownCount}</p>
              <p className="text-xs text-slate-500">Unknown</p>
            </div>
          </div>
        </div>
      </div>

      {/* Disclaimer */}
      <DisclaimerBanner text={extraction.disclaimer} />

      {/* Claims table */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200 overflow-hidden">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extracted Legal Rights</h3>
          <p className="mt-1 text-sm text-slate-500">
            AI-extracted fields — all values require human review before approval
          </p>
        </div>
        <table className="min-w-full divide-y divide-slate-200">
          <thead>
            <tr className="bg-slate-50">
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Field</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Value</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Source</th>
              {token && extraction.status !== 'approved' && (
                <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Action</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {extraction.claims.map(claim => (
              <>
                <tr key={claim.id} className={`hover:bg-slate-50 ${expandedClaim === claim.id ? 'bg-amber-50' : ''}`}>
                  <td className="px-5 py-4">
                    <p className="text-sm font-medium text-slate-900">
                      {FIELD_LABELS[claim.field_name] ?? claim.field_name}
                    </p>
                    <p className="font-mono text-xs text-slate-400">{claim.field_name}</p>
                  </td>
                  <td className="px-5 py-4 max-w-xs">
                    <p className={`text-sm ${claim.is_unknown ? 'italic text-slate-400' : 'text-slate-700'}`}>
                      {claim.value}
                    </p>
                  </td>
                  <td className="px-5 py-4">
                    <ClaimStatusBadge
                      isUnknown={claim.is_unknown}
                      verified={!claim.is_unknown && !!claim.supporting_passage}
                    />
                  </td>
                  <td className="px-5 py-4">
                    {claim.source_page ? (
                      <div>
                        <p className="text-xs text-slate-600">Page {claim.source_page}</p>
                        {claim.supporting_passage && (
                          <p className="mt-0.5 max-w-sm truncate text-xs text-slate-400" title={claim.supporting_passage}>
                            "{claim.supporting_passage.slice(0, 60)}…"
                          </p>
                        )}
                      </div>
                    ) : (
                      <span className="text-xs text-slate-400">—</span>
                    )}
                  </td>
                  {token && extraction.status !== 'approved' && (
                    <td className="px-5 py-4">
                      <button
                        onClick={() => setExpandedClaim(prev => prev === claim.id ? null : claim.id)}
                        className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 hover:border-amber-300 hover:text-amber-700"
                      >
                        {expandedClaim === claim.id ? 'Cancel' : 'Correct'}
                      </button>
                    </td>
                  )}
                </tr>
                {expandedClaim === claim.id && token && (
                  <CorrectionForm
                    key={`form-${claim.id}`}
                    claim={claim}
                    token={token}
                    extractionId={extraction.id}
                    onClose={() => setExpandedClaim(null)}
                    onSaved={() => { setExpandedClaim(null); loadAll() }}
                  />
                )}
              </>
            ))}
          </tbody>
        </table>
      </div>

      {/* Reviewer panel */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <h3 className="font-semibold text-slate-900">Review Workflow</h3>

        {!token ? (
          <div className="mt-4 space-y-3">
            <p className="text-sm text-slate-500">Enter your reviewer token to access review controls.</p>
            <div className="flex gap-3">
              <input
                type="password"
                value={token}
                onChange={e => setToken(e.target.value)}
                placeholder="Bearer token…"
                className="flex-1 rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
              <button
                onClick={loadAll}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
              >
                Load
              </button>
            </div>
          </div>
        ) : (
          <div className="mt-4 space-y-4">
            {actionError && (
              <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{actionError}</div>
            )}

            {extraction.status === 'draft' && (
              <button
                onClick={handleStartReview}
                disabled={starting}
                className="flex items-center gap-2 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-60"
              >
                Start Review
              </button>
            )}

            {(extraction.status === 'draft' || extraction.status === 'under_review') && (
              <form onSubmit={handleApprove} className="rounded-xl bg-green-50 p-4 ring-1 ring-green-200">
                <p className="mb-3 font-medium text-green-900">Approve This Extraction</p>
                <p className="mb-3 text-xs text-green-700">
                  Approval requires all known fields to have supporting source evidence.
                  This approval is an internal attestation only — it does not constitute
                  a legally binding opinion.
                </p>
                <input
                  value={approvalNotes}
                  onChange={e => setApprovalNotes(e.target.value)}
                  placeholder="Optional notes…"
                  className="mb-3 w-full rounded-lg border border-green-300 bg-white px-3 py-2 text-sm focus:border-green-500 focus:outline-none"
                />
                <button
                  type="submit"
                  disabled={approving}
                  className="flex items-center gap-2 rounded-lg bg-green-700 px-4 py-2 text-sm font-medium text-white hover:bg-green-800 disabled:opacity-60"
                >
                  <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                  </svg>
                  {approving ? 'Approving…' : 'Approve Extraction'}
                </button>
              </form>
            )}

            {extraction.status === 'approved' && (
              <div className="rounded-xl bg-green-50 p-4 ring-1 ring-green-200">
                <p className="font-medium text-green-900">✓ This extraction has been approved</p>
                <p className="mt-1 text-sm text-green-700">No further corrections can be made.</p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Audit trail */}
      {(corrections.length > 0 || approvals.length > 0) && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
          <div className="border-b border-slate-100 px-6 py-4">
            <h3 className="font-semibold text-slate-900">Audit History</h3>
          </div>

          {corrections.length > 0 && (
            <div className="p-5">
              <h4 className="mb-3 text-sm font-semibold text-slate-700">Corrections ({corrections.length})</h4>
              <div className="space-y-3">
                {corrections.map(c => (
                  <div key={c.id} className="rounded-lg bg-amber-50 p-4 ring-1 ring-amber-200">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-amber-900">
                        {FIELD_LABELS[extraction.claims.find(cl => cl.id === c.claim_id)?.field_name ?? ''] ?? c.claim_id}
                      </p>
                      <span className="text-xs text-amber-700">{fmt(c.created_at)}</span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-3 text-xs">
                      <div>
                        <span className="font-medium text-slate-500">Before: </span>
                        <span className="text-slate-700">{c.original_value}</span>
                      </div>
                      <div>
                        <span className="font-medium text-slate-500">After: </span>
                        <span className="text-slate-700">{c.corrected_value}</span>
                      </div>
                    </div>
                    {c.correction_note && (
                      <p className="mt-1.5 text-xs italic text-amber-700">Note: {c.correction_note}</p>
                    )}
                    <p className="mt-1.5 text-xs text-slate-500">
                      by <span className="font-mono">{c.reviewer_id}</span> ({c.reviewer_role.replace(/_/g, ' ')})
                    </p>
                  </div>
                ))}
              </div>
            </div>
          )}

          {approvals.length > 0 && (
            <div className="border-t border-slate-100 p-5">
              <h4 className="mb-3 text-sm font-semibold text-slate-700">Approvals ({approvals.length})</h4>
              <div className="space-y-3">
                {approvals.map(a => (
                  <div key={a.id} className="rounded-lg bg-green-50 p-4 ring-1 ring-green-200">
                    <div className="flex items-center justify-between">
                      <ApprovalTypeBadge type={a.approval_type} />
                      <span className="text-xs text-slate-500">{fmt(a.approved_at)}</span>
                    </div>
                    <p className="mt-1.5 text-xs text-slate-500">
                      by <span className="font-mono">{a.reviewer_id}</span> ({a.reviewer_role.replace(/_/g, ' ')})
                    </p>
                    {a.notes && <p className="mt-1 text-xs italic text-slate-600">{a.notes}</p>}
                    <p className="mt-2 text-xs text-slate-400">{a.disclaimer.slice(0, 100)}…</p>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
