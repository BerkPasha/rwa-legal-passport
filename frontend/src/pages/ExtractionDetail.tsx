import { Fragment, useEffect, useRef, useState } from 'react'
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
  LegalClaim,
  LegalExtraction,
  ReviewApproval,
  ReviewCorrection,
} from '../types'
import { PageSpinner } from '../components/Spinner'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { SourceTextPanel } from '../components/SourceTextPanel'
import {
  ApprovalStateBadge,
  ApprovalTypeBadge,
  EvidenceBadge,
  ExtractionStatusBadge,
  STALE_REASON_LABELS,
} from '../components/StatusBadge'
import {
  type ApprovalFailure,
  approvalEvidenceFailures,
  buildCorrectionRequest,
  canKeep,
  type CorrectionDraft,
  correctionConflictMessage,
  correctionErrorMessage,
  effectiveMode,
  EVIDENCE_SCOPE_NOTE,
  evidenceStatusLabel,
  httpErrorMessage,
  initialDraft,
  valueChanged,
} from '../evidence'
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

const sameClaim = (a: LegalClaim, b: LegalClaim) =>
  a.value === b.value && a.is_unknown === b.is_unknown &&
  a.source_page === b.source_page && a.supporting_passage === b.supporting_passage

function keepUnavailableReason(claim: LegalClaim, draft: CorrectionDraft, field: 'page' | 'passage'): string {
  if (claim.is_unknown) return 'the claim is currently unknown'
  if (valueChanged(claim, draft)) return 'not allowed, the value changed'
  return field === 'page' ? 'no page is currently cited' : 'no quotation is currently recorded'
}

function CorrectionForm({
  claim: displayedClaim,
  token,
  extractionId,
  versionLabel,
  onClose,
  onSaved,
  onConflict,
}: {
  claim: LegalClaim
  token: string
  extractionId: string
  versionLabel: string
  onClose: () => void
  onSaved: () => void
  // The claim or extraction changed underneath the form, or the backend reported a conflict
  onConflict: (message: string) => void
}) {
  // The claim exactly as shown when the form was opened: the evidence the reviewer decides on and
  // the mandatory precondition (issue A). A later reload of the page never replaces it.
  const [claim] = useState<LegalClaim>(displayedClaim)
  const [draft, setDraft] = useState<CorrectionDraft>(() => initialDraft(claim))
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')

  const changed = valueChanged(claim, draft)
  const pageMode = effectiveMode(claim, draft, 'page')
  const passageMode = effectiveMode(claim, draft, 'passage')
  const request = buildCorrectionRequest(claim, draft)
  const update = (patch: Partial<CorrectionDraft>) => { setErr(''); setDraft(d => ({ ...d, ...patch })) }
  const id = (name: string) => `${name}-${claim.id}`

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    const built = buildCorrectionRequest(claim, draft)
    if (!built.ok) {
      setErr(built.error)
      return
    }
    setSaving(true)
    try {
      // The evidence decision was made on the claim shown here; never send it for a claim that
      // has changed since (another correction, an approval) — re-read it first
      const fresh = await getExtraction(extractionId)
      const now = fresh.claims.find(c => c.id === claim.id)
      if (fresh.id !== extractionId || fresh.status === 'approved' || !now || !sameClaim(now, claim)) {
        onConflict(
          'This claim or its extraction changed after the correction form was opened. Nothing was saved. ' +
          'The current state is shown below; review it before correcting again.',
        )
        return
      }
      await correctClaim(extractionId, claim.id, built.payload, token)
      onSaved()
    } catch (error) {
      // Never retried automatically: the reviewer sees the current claim and decides again
      if (error instanceof ApiError && error.status === 409) {
        onConflict(correctionConflictMessage(error))
        return
      }
      setErr(correctionErrorMessage(error))
    } finally {
      setSaving(false)
    }
  }

  const fieldLabel = FIELD_LABELS[claim.field_name] ?? claim.field_name

  return (
    <tr>
      <td colSpan={5} className="bg-amber-50 p-5">
        {/* noValidate: the form's own checks explain each missing piece of evidence */}
        <form onSubmit={handleSubmit} noValidate className="space-y-4" aria-label={`Correct ${fieldLabel}`}>
          <p className="text-sm font-semibold text-amber-900">Correcting: {fieldLabel}</p>

          <div data-testid="current-evidence" className="rounded-lg bg-white p-3 text-xs text-slate-600 ring-1 ring-amber-200">
            <p>
              <span className="font-medium text-slate-700">Current value: </span>
              <span className={claim.is_unknown ? 'italic' : ''}>{claim.value}</span>
            </p>
            <p className="mt-1">
              <span className="font-medium text-slate-700">Current evidence: </span>
              {claim.source_page != null ? `page ${claim.source_page}` : 'no page'}
              {claim.supporting_passage ? ` — “${claim.supporting_passage}”` : ' — no quotation'}
            </p>
            <div className="mt-1.5"><EvidenceBadge claim={claim} /></div>
          </div>

          <div className="flex items-center gap-2">
            <input
              type="checkbox"
              id={id('is_unknown')}
              checked={draft.isUnknown}
              onChange={e => update({ isUnknown: e.target.checked })}
              className="h-4 w-4 rounded border-slate-300 text-indigo-600"
            />
            <label htmlFor={id('is_unknown')} className="text-sm text-slate-700">
              Mark as unknown (insufficient evidence)
            </label>
          </div>

          {draft.isUnknown ? (
            <p role="status" data-testid="unknown-clears-evidence" className="rounded-lg bg-orange-50 p-3 text-sm text-orange-800 ring-1 ring-orange-200">
              The claim will be saved as unknown: its value becomes “unknown”, and its page and quotation are cleared.
            </p>
          ) : (
            <>
              <div>
                <label htmlFor={id('value')} className="mb-1 block text-xs font-medium text-slate-700">Corrected Value</label>
                <textarea
                  id={id('value')}
                  required
                  value={draft.value}
                  onChange={e => update({ value: e.target.value })}
                  rows={2}
                  className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                />
              </div>

              {changed && (
                <p role="status" data-testid="fresh-evidence-required" className="rounded-lg bg-amber-100 p-3 text-sm text-amber-900">
                  {claim.is_unknown ? 'The claim is currently unknown.' : 'The value differs from the current value.'}{' '}
                  The previous page and quotation cannot be reused: cite the page and the quotation that support this value.
                </p>
              )}

              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <fieldset className="space-y-1.5">
                  <legend className="mb-1 text-xs font-medium text-slate-700">Cited page</legend>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="radio"
                      name={id('page-mode')}
                      checked={pageMode === 'keep'}
                      disabled={!canKeep(claim, draft, 'page')}
                      onChange={() => update({ pageMode: 'keep' })}
                    />
                    {canKeep(claim, draft, 'page')
                      ? `Keep the current page (${claim.source_page})`
                      : `Keep the current page (${keepUnavailableReason(claim, draft, 'page')})`}
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="radio"
                      name={id('page-mode')}
                      checked={pageMode === 'replace'}
                      onChange={() => update({ pageMode: 'replace' })}
                    />
                    Cite a page
                  </label>
                  <input
                    id={id('page')}
                    inputMode="numeric"
                    value={draft.pageInput}
                    onChange={e => update({ pageInput: e.target.value, pageMode: 'replace' })}
                    placeholder="Page number"
                    aria-label="Cited page number"
                    className="w-32 rounded-lg border border-slate-300 px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </fieldset>

                <fieldset className="space-y-1.5">
                  <legend className="mb-1 text-xs font-medium text-slate-700">Supporting quotation</legend>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="radio"
                      name={id('passage-mode')}
                      checked={passageMode === 'keep'}
                      disabled={!canKeep(claim, draft, 'passage')}
                      onChange={() => update({ passageMode: 'keep' })}
                    />
                    {canKeep(claim, draft, 'passage')
                      ? 'Keep the current quotation'
                      : `Keep the current quotation (${keepUnavailableReason(claim, draft, 'passage')})`}
                  </label>
                  <label className="flex items-center gap-2 text-sm text-slate-700">
                    <input
                      type="radio"
                      name={id('passage-mode')}
                      checked={passageMode === 'replace'}
                      onChange={() => update({ passageMode: 'replace' })}
                    />
                    Enter a new quotation
                  </label>
                  <textarea
                    id={id('passage')}
                    aria-label="Supporting quotation"
                    value={draft.passageInput}
                    onChange={e => update({ passageInput: e.target.value, passageMode: 'replace' })}
                    rows={3}
                    placeholder={`Copy the quotation exactly from the cited page of ${versionLabel}…`}
                    className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
                  />
                </fieldset>
              </div>
              <p className="text-xs text-slate-500">
                To remove the evidence, mark the claim as unknown. {EVIDENCE_SCOPE_NOTE}
              </p>
            </>
          )}

          <div>
            <label htmlFor={id('note')} className="mb-1 block text-xs font-medium text-slate-700">Correction Note (optional)</label>
            <input
              id={id('note')}
              value={draft.note}
              onChange={e => update({ note: e.target.value })}
              placeholder="Reason for correction…"
              className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
            />
          </div>

          {request.ok && (
            <p data-testid="correction-summary" className="text-xs text-slate-600">{request.summary}</p>
          )}
          {err && <p role="alert" data-testid="correction-error" className="text-sm text-red-600">{err}</p>}
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

function EvidenceChange({ page, passage, recorded }: { page: number | null; passage: string | null; recorded: boolean }) {
  return (
    <span className="block text-slate-500">
      {recorded ? (page != null ? `page ${page}` : 'no page') : 'page not recorded'}
      {passage ? ` — “${passage}”` : ' — no quotation'}
    </span>
  )
}

export function ExtractionDetail() {
  const { id: assetId, extractionId } = useParams<{
    id: string
    docId: string
    extractionId: string
  }>()
  const { userToken } = useAuth()
  const [token, setToken] = useState(userToken)
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
  const [approvalFailures, setApprovalFailures] = useState<ApprovalFailure[]>([])
  const [correctionNotice, setCorrectionNotice] = useState('')
  const [focus, setFocus] = useState<{ page: number; nonce: number } | null>(null)
  // Responses for an extraction that is no longer displayed are discarded
  const activeExtraction = useRef(extractionId)
  activeExtraction.current = extractionId

  const loadAll = async () => {
    const target = extractionId
    if (!target) return
    try {
      const ex = await getExtraction(target)
      if (activeExtraction.current !== target) return
      if (ex.id !== target) {
        setError('The server returned a different extraction; it is not shown.')
        return
      }
      setExtraction(ex)
      if (token) {
        const [corr, approv] = await Promise.all([
          getCorrections(target, token).catch(() => []),
          getApprovals(target, token).catch(() => []),
        ])
        if (activeExtraction.current !== target) return
        setCorrections(corr)
        setApprovals(approv)
      }
    } catch (e) {
      if (activeExtraction.current !== target) return
      setError(httpErrorMessage(e, 'load this extraction'))
    } finally {
      if (activeExtraction.current === target) setLoading(false)
    }
  }

  useEffect(() => {
    setLoading(true)
    setError('')
    setExtraction(null)
    setCorrections([])
    setApprovals([])
    setExpandedClaim(null)
    setCorrectionNotice('')
    setFocus(null)
    loadAll()
  }, [extractionId])
  useEffect(() => { setToken(userToken) }, [userToken])

  const handleStartReview = async () => {
    if (!extractionId || !token) return
    setStarting(true)
    setActionError('')
    setApprovalFailures([])
    try {
      await startReview(extractionId, token)
      await loadAll()
    } catch (e) {
      setActionError(httpErrorMessage(e, 'start a review'))
      if (e instanceof ApiError && e.status === 409) await loadAll()
    } finally {
      setStarting(false)
    }
  }

  const handleApprove = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!extractionId || !token) return
    setApproving(true)
    setActionError('')
    setApprovalFailures([])
    const data: ApprovalPayload = {}
    if (approvalNotes) data.notes = approvalNotes
    try {
      await approveExtraction(extractionId, data, token)
      await loadAll()
    } catch (e) {
      setActionError(httpErrorMessage(e, 'approve this extraction'))
      setApprovalFailures(approvalEvidenceFailures(e))
      // The live state decided the conflict (superseded, stale, integrity): show it
      if (e instanceof ApiError && e.status === 409) await loadAll()
    } finally {
      setApproving(false)
    }
  }

  if (loading) return <PageSpinner />
  if (error) return <div role="alert" className="rounded-lg bg-red-50 p-4 text-red-700">{error}</div>
  if (!extraction) return null

  // Approval currency is evaluated by the backend on every read (Stage 9B-3); without it,
  // nothing is presented as approved or approvable
  const approvalState = extraction.approval_state
  const versionIsCurrent = approvalState?.version_is_current === true
  const approvalIsStale = extraction.status === 'approved' && approvalState?.state === 'stale'
  const approvalIsCurrent = extraction.status === 'approved' && approvalState?.state === 'current'

  // Counted from the backend's evidence check only
  const knownCount = extraction.claims.filter(c => !c.is_unknown).length
  const foundCount = extraction.claims.filter(c => !c.is_unknown && c.evidence?.status === 'verified').length
  const reviewedVersionNumber = extraction.claims.find(c => c.evidence?.version_number != null)?.evidence.version_number ?? null
  const versionLabel = reviewedVersionNumber != null ? `version ${reviewedVersionNumber}` : 'this document version'
  const canCorrect = !!token && extraction.status !== 'approved'

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
              <ApprovalStateBadge state={approvalState?.state} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-4 text-sm text-slate-500">
              <span>Model: <span className="font-mono font-medium text-slate-700">{extraction.ai_model}</span></span>
              <span>Extracted: {fmt(extraction.extraction_timestamp)}</span>
              {reviewedVersionNumber != null && (
                <span data-testid="extraction-version">Document version {reviewedVersionNumber}</span>
              )}
            </div>
          </div>
          <div className="flex gap-3 text-center">
            <div className="rounded-lg bg-slate-50 px-4 py-2">
              <p className="text-2xl font-bold text-slate-900">{extraction.claims.length}</p>
              <p className="text-xs text-slate-500">Total Fields</p>
            </div>
            <div className="rounded-lg bg-sky-50 px-4 py-2">
              <p data-testid="count-quote-found" className="text-2xl font-bold text-sky-700">{foundCount}</p>
              <p className="text-xs text-slate-500">Quote Found</p>
            </div>
            <div className="rounded-lg bg-rose-50 px-4 py-2">
              <p data-testid="count-not-verified" className="text-2xl font-bold text-rose-700">{knownCount - foundCount}</p>
              <p className="text-xs text-slate-500">Not Verified</p>
            </div>
            <div className="rounded-lg bg-orange-50 px-4 py-2">
              <p data-testid="count-unknown" className="text-2xl font-bold text-orange-700">{extraction.claims.length - knownCount}</p>
              <p className="text-xs text-slate-500">Unknown</p>
            </div>
          </div>
        </div>
      </div>

      {/* Disclaimer */}
      <DisclaimerBanner text={extraction.disclaimer} />

      {correctionNotice && (
        <div role="alert" data-testid="correction-conflict" className="rounded-xl bg-amber-50 p-4 text-sm text-amber-900 ring-1 ring-amber-300">
          {correctionNotice}
        </div>
      )}

      {/* Claims table */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200 overflow-hidden">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extracted Legal Rights</h3>
          <p className="mt-1 text-sm text-slate-500">
            AI-extracted fields — all values require human review before approval. {EVIDENCE_SCOPE_NOTE}
          </p>
        </div>
        <table className="min-w-full divide-y divide-slate-200">
          <thead>
            <tr className="bg-slate-50">
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Field</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Value</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Evidence</th>
              <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Source</th>
              {canCorrect && (
                <th className="px-5 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Action</th>
              )}
            </tr>
          </thead>
          <tbody className="divide-y divide-slate-100">
            {extraction.claims.map(claim => (
              <Fragment key={claim.id}>
                <tr data-testid={`claim-row-${claim.field_name}`} className={`hover:bg-slate-50 ${expandedClaim === claim.id ? 'bg-amber-50' : ''}`}>
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
                    <EvidenceBadge claim={claim} />
                  </td>
                  <td className="px-5 py-4">
                    {claim.source_page == null && !claim.supporting_passage ? (
                      <span className="text-xs text-slate-400">—</span>
                    ) : (
                      <div>
                        <p className="text-xs text-slate-600">
                          {claim.source_page != null ? `Page ${claim.source_page}` : 'No page cited'}
                          {claim.source_page != null && (
                            <button
                              type="button"
                              onClick={() => setFocus(f => ({ page: claim.source_page as number, nonce: (f?.nonce ?? 0) + 1 }))}
                              aria-label={`View page ${claim.source_page} of the source text for ${FIELD_LABELS[claim.field_name] ?? claim.field_name}`}
                              className="ml-2 text-indigo-600 hover:text-indigo-800"
                            >
                              View page
                            </button>
                          )}
                        </p>
                        {claim.supporting_passage && (
                          <p className="mt-0.5 max-w-sm truncate text-xs text-slate-400" title={claim.supporting_passage}>
                            “{claim.supporting_passage}”
                          </p>
                        )}
                      </div>
                    )}
                  </td>
                  {canCorrect && (
                    <td className="px-5 py-4">
                      <button
                        onClick={() => { setCorrectionNotice(''); setExpandedClaim(prev => prev === claim.id ? null : claim.id) }}
                        className="rounded-lg border border-slate-200 px-3 py-1 text-xs font-medium text-slate-600 hover:border-amber-300 hover:text-amber-700"
                      >
                        {expandedClaim === claim.id ? 'Cancel' : 'Correct'}
                      </button>
                    </td>
                  )}
                </tr>
                {expandedClaim === claim.id && canCorrect && (
                  <CorrectionForm
                    key={`form-${claim.id}`}
                    claim={claim}
                    token={token}
                    extractionId={extraction.id}
                    versionLabel={versionLabel}
                    onClose={() => setExpandedClaim(null)}
                    onSaved={() => { setExpandedClaim(null); loadAll() }}
                    onConflict={message => { setExpandedClaim(null); setCorrectionNotice(message); loadAll() }}
                  />
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>

      {/* Source text of exactly the extraction's document version */}
      <SourceTextPanel documentId={extraction.document_id} reviewedVersionId={extraction.version_id} focus={focus} />

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
              <div role="alert" data-testid="action-error" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
                <p>{actionError}</p>
                {approvalFailures.length > 0 && (
                  <ul className="mt-2 list-disc pl-5">
                    {approvalFailures.map(f => (
                      <li key={f.claim_id}>
                        {FIELD_LABELS[f.field_name] ?? f.field_name}: {evidenceStatusLabel(f.status)}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}

            {approvalState?.version_is_current === false && (
              <div data-testid="superseded-notice" className="rounded-xl bg-rose-50 p-4 ring-1 ring-rose-200">
                <p className="font-medium text-rose-900">A newer version of this document exists</p>
                <p className="mt-1 text-sm text-rose-700">
                  This extraction belongs to a superseded document version and can no longer be approved.
                  Run a new extraction on the current version. Earlier approvals remain in the audit history.
                </p>
              </div>
            )}

            {approvalIsStale && versionIsCurrent && (
              <div data-testid="stale-approval-notice" className="rounded-xl bg-amber-50 p-4 ring-1 ring-amber-300">
                <p className="font-medium text-amber-900">This approval is no longer current</p>
                <ul className="mt-2 list-disc pl-5 text-sm text-amber-800">
                  {(approvalState?.reasons ?? []).map(reason => (
                    <li key={reason}>{STALE_REASON_LABELS[reason] ?? reason}</li>
                  ))}
                </ul>
                <p className="mt-2 text-sm text-amber-800">
                  A re-review and a new approval are required before this extraction is presented as approved again.
                </p>
                <button
                  onClick={handleStartReview}
                  disabled={starting}
                  className="mt-3 rounded-lg bg-amber-600 px-4 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-60"
                >
                  Start Re-review
                </button>
              </div>
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

            {(extraction.status === 'draft' || extraction.status === 'under_review') && versionIsCurrent && (
              <form onSubmit={handleApprove} className="rounded-xl bg-green-50 p-4 ring-1 ring-green-200">
                <p className="mb-3 font-medium text-green-900">Approve This Extraction</p>
                <p className="mb-3 text-xs text-green-700">
                  Approval requires every known field to have a quotation that the server finds on its
                  cited page of this document version. A found quote proves source-text presence only.
                  This approval is an internal attestation — it does not constitute a legally binding opinion.
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

            {approvalIsCurrent && (
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
                  <div key={c.id} data-testid="correction-record" className="rounded-lg bg-amber-50 p-4 ring-1 ring-amber-200">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-amber-900">
                        {FIELD_LABELS[extraction.claims.find(cl => cl.id === c.claim_id)?.field_name ?? ''] ?? c.claim_id}
                      </p>
                      <span className="text-xs text-amber-700">{fmt(c.created_at)}</span>
                    </div>
                    <div className="mt-2 grid grid-cols-2 gap-3 text-xs">
                      <div>
                        <span className="font-medium text-slate-500">Before: </span>
                        <span className="text-slate-700">{c.original_is_unknown ? 'unknown' : c.original_value}</span>
                        <EvidenceChange page={c.original_source_page} passage={c.original_passage} recorded={c.page_history_recorded} />
                      </div>
                      <div>
                        <span className="font-medium text-slate-500">After: </span>
                        <span className="text-slate-700">{c.corrected_is_unknown ? 'unknown' : c.corrected_value}</span>
                        <EvidenceChange page={c.corrected_source_page} passage={c.corrected_passage} recorded={c.page_history_recorded} />
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
                  <div
                    key={a.id}
                    className={`rounded-lg p-4 ring-1 ${a.currency?.is_current ? 'bg-green-50 ring-green-200' : 'bg-slate-50 ring-slate-200'}`}
                  >
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <ApprovalTypeBadge type={a.approval_type} />
                        <span
                          data-testid="approval-currency"
                          className={`text-xs font-medium ${a.currency?.is_current ? 'text-green-700' : 'text-slate-500'}`}
                        >
                          {a.currency?.is_current ? 'Current' : 'Not current'}
                        </span>
                      </div>
                      <span className="text-xs text-slate-500">{fmt(a.approved_at)}</span>
                    </div>
                    {a.document_version_number != null && (
                      <p className="mt-1.5 text-xs text-slate-500">Bound to document version {a.document_version_number}</p>
                    )}
                    {a.evidence_snapshot_format != null && (
                      <p data-testid="approval-evidence-format" className="mt-1 text-xs text-slate-500">
                        {a.evidence_snapshot_format >= 2
                          ? 'Evidence bound: per-claim quotation check results and page-text hashes'
                          : 'Legacy evidence snapshot: no quotation check was recorded'}
                      </p>
                    )}
                    {a.currency && !a.currency.is_current && a.currency.reasons.length > 0 && (
                      <p className="mt-1 text-xs text-amber-700">
                        {a.currency.reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ')}
                      </p>
                    )}
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
