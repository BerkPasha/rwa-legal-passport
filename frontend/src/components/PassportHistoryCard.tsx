import type { ApprovalStaleReason, DocumentHistory, PassportHistory } from '../types'
import { ApprovalStateBadge, ApprovalTypeBadge, STALE_REASON_LABELS } from './StatusBadge'
import { ANCHOR_STATUS_LABELS, ANCHOR_TONE_CLASSES, shortHash, testnetExplorerTx, type AnchorTone } from '../anchor'

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString('en-US', { timeZone: 'UTC', timeZoneName: 'short' })
}

const reasonText = (reasons: ApprovalStaleReason[]) => reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ')

type Tone = Exclude<AnchorTone, 'blue'>

// Whether the document's current version stands behind the current passport, or needs (re)approval
function currentVersionRequirement(doc: DocumentHistory): { tone: Tone; label: string; detail: string } {
  const n = doc.current_version_number
  const current = doc.approved_versions.filter(v => v.document_version_number === n)
  if (current.some(v => v.served_in_current_passport)) {
    return { tone: 'green', label: 'Current version approved', detail: `Version ${n} is approved and used by the current Legal Passport.` }
  }
  if (current.some(v => v.approval_state === 'current')) {
    return { tone: 'amber', label: 'Approved, not used', detail: `Version ${n} has a current approval, but the current Legal Passport does not use it.` }
  }
  if (doc.approved_versions.length > 0) {
    return { tone: 'red', label: 'Reapproval required',
             detail: `Version ${n} has no current approval: earlier approvals no longer apply. Its claims must be reviewed and approved again.` }
  }
  return { tone: 'slate', label: 'Not approved yet', detail: `Version ${n} has not been approved yet.` }
}

function DocumentHistoryView({ doc }: { doc: DocumentHistory }) {
  const requirement = currentVersionRequirement(doc)
  return (
    <div data-testid="history-document" className="px-6 py-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="font-semibold text-slate-900">{doc.title}</p>
          <p className="text-xs capitalize text-slate-500">{doc.document_type.replace(/_/g, ' ')} · current version v{doc.current_version_number}</p>
        </div>
        <span data-testid="history-requirement"
              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ring-1 ring-inset ${ANCHOR_TONE_CLASSES[requirement.tone]}`}>
          {requirement.label}
        </span>
      </div>
      <p data-testid="history-requirement-detail" className="mt-1 text-xs text-slate-600">{requirement.detail}</p>

      {/* Versions, oldest first, each with what happened to its approvals */}
      <ol className="mt-3 space-y-2">
        {doc.versions.map(version => {
          const approved = doc.approved_versions.filter(v => v.document_version_number === version.version_number)
          return (
            <li key={version.version_number} data-testid="history-version" data-version={version.version_number}
                className="rounded-lg bg-slate-50 px-3 py-2 ring-1 ring-slate-200">
              <div className="flex flex-wrap items-center gap-2 text-xs">
                <span className="font-semibold text-slate-900">v{version.version_number}</span>
                {version.is_current && <span className="rounded bg-indigo-100 px-1.5 py-0.5 text-indigo-700">Current version</span>}
                <span className="text-slate-500">uploaded {fmtTime(version.uploaded_at)}</span>
                <span className="font-mono text-slate-400" title={version.sha256_hash}>SHA-256 {version.sha256_hash.slice(0, 16)}…</span>
              </div>
              {approved.length === 0 ? (
                <p className="mt-1 text-xs text-slate-500">Never approved.</p>
              ) : approved.map(extraction => (
                <div key={extraction.extraction_id} className="mt-2 space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <ApprovalStateBadge state={extraction.approval_state} />
                    {extraction.served_in_current_passport && (
                      <span className="text-xs font-medium text-green-700">Used by the current Legal Passport</span>
                    )}
                  </div>
                  <ul className="space-y-0.5">
                    {extraction.approvals.map(approval => (
                      <li key={`${approval.approval_type}-${approval.approved_at}`} data-testid="history-approval"
                          className="flex flex-wrap items-center gap-2 text-xs text-slate-600">
                        <ApprovalTypeBadge type={approval.approval_type} />
                        <span>approved {fmtTime(approval.approved_at)}</span>
                        <span className={approval.is_current ? 'text-green-700' : 'text-rose-700'}>
                          {approval.is_current ? 'current' : 'no longer current'}
                        </span>
                      </li>
                    ))}
                  </ul>
                  {extraction.stale_reasons.length > 0 && (
                    <p data-testid="history-invalidation" className="text-xs text-rose-800">
                      Why it is no longer current: {reasonText(extraction.stale_reasons)}.
                    </p>
                  )}
                </div>
              ))}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// The passport's audit trail (GET …/history): versions, approvals and anchoring records. Historical
// records are never presented as currently approved information.
export function PassportHistoryCard({ history }: { history: PassportHistory }) {
  const testnet = history.asset.network === 'stellar-testnet'
  const everApproved = history.documents.some(d => d.approved_versions.length > 0)
  const summary = history.current_passport_available
    ? { tone: 'green' as const, text: 'A current Legal Passport is available.' }
    : history.asset.status !== 'active'
      ? { tone: 'amber' as const, text: `No current Legal Passport: the asset is ${history.asset.status}.` }
      : everApproved
        ? { tone: 'red' as const, text: 'No current Legal Passport: the approvals behind it are no longer current. Reapproval is required.' }
        : { tone: 'slate' as const, text: 'No current Legal Passport: no document has been approved yet.' }
  return (
    <section id="passport-history" data-testid="passport-history" aria-labelledby="passport-history-title"
             className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
      <div className="border-b border-slate-100 px-6 py-4">
        <h3 id="passport-history-title" className="text-lg font-semibold text-slate-900">Passport History</h3>
        <p className="mt-0.5 text-xs text-slate-500">
          Every document version and every approval, including those that are no longer current. Historical
          records are shown for audit only; only the Legal Passport above is currently approved information.
        </p>
        <p data-testid="history-summary" className={`mt-3 rounded-lg px-3 py-2 text-sm ring-1 ring-inset ${ANCHOR_TONE_CLASSES[summary.tone]}`}>
          {summary.text}
        </p>
      </div>

      {history.documents.length === 0 ? (
        <p className="px-6 py-4 text-sm text-slate-500">No document has been uploaded for this asset.</p>
      ) : (
        <div className="divide-y divide-slate-100">
          {history.documents.map(doc => <DocumentHistoryView key={doc.document_id} doc={doc} />)}
        </div>
      )}

      <div className="border-t border-slate-100 px-6 py-4">
        <h4 className="text-sm font-semibold text-slate-900">Stellar Anchoring Records</h4>
        <p className="mt-0.5 text-xs text-slate-500">
          Statuses as recorded by this server; the live Stellar check is the Stellar Evidence Commitment card.
        </p>
        {history.anchors.length === 0 ? (
          <p className="mt-2 text-sm text-slate-500">No anchoring transaction recorded.</p>
        ) : (
          <ul className="mt-2 divide-y divide-slate-100">
            {history.anchors.map(anchor => (
              <li key={anchor.manifest_digest} data-testid="history-anchor" className="flex flex-wrap items-center gap-3 py-2 text-xs">
                <span className="font-medium text-slate-800">{ANCHOR_STATUS_LABELS[anchor.status]}</span>
                <span className="font-mono text-slate-500">digest {shortHash(anchor.manifest_digest, 8)}</span>
                <span className={anchor.matches_current_evidence ? 'text-green-700' : 'text-amber-800'}>
                  {anchor.matches_current_evidence ? 'Matches the current evidence' : 'Does not match the current evidence'}
                </span>
                {anchor.transaction_hash && (testnet ? (
                  <a href={testnetExplorerTx(anchor.transaction_hash)} target="_blank" rel="noopener noreferrer"
                     className="font-mono text-indigo-600 hover:text-indigo-800">tx {shortHash(anchor.transaction_hash, 8)} ↗</a>
                ) : <span className="font-mono text-slate-500">tx {shortHash(anchor.transaction_hash, 8)}</span>)}
                <span className="text-slate-500">{fmtTime(anchor.anchored_at)}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  )
}
