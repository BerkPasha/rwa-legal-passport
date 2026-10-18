import { useEffect, useState } from 'react'
import { ApiError, createManualExtraction, getDocumentVersionText } from '../api'
import type { Document, DocumentVersionText, LegalExtraction } from '../types'
import { LEGAL_FIELD_LABELS } from '../legalFields'
import { evidenceStatusLabel, httpErrorMessage } from '../evidence'

// Manual legal claims (Stage 10B-3). The user reads the stored text of the document's current
// version page by page and copies a quotation from the cited page. The server checks it against
// that page before anything is stored; this form decides nothing itself and never alters a
// quotation. The result is an unapproved draft labelled as a manual entry.

interface EvidenceFailure {
  field_name: string
  status: string
  explanation: string
}

export function manualClaimErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    const failures = (error.body as { evidence_failures?: EvidenceFailure[] } | null)?.evidence_failures
    if (error.field('manual_claim_refusal') === 'evidence_not_verified' && Array.isArray(failures) && failures.length > 0) {
      const reasons = failures.map(f =>
        `${LEGAL_FIELD_LABELS[f.field_name] ?? f.field_name}: ${evidenceStatusLabel(f.status)}. ${f.explanation}`)
      return `Not created — ${reasons.join(' ')} Nothing was stored.`
    }
    if (error.field('manual_claim_refusal')) return `Not created — ${error.message}`
    if (error.status === 403) return 'Not created — your role cannot create legal claims. Nothing was stored.'
    if (error.status === 404) return 'Not created — the document does not exist or is outside your access.'
    // Server errors: no internal detail is repeated
    if (error.status >= 500) return 'Not created — the server could not process the request. Nothing was stored; try again later.'
    return `Not created — ${error.message}`
  }
  return 'Not created — the server could not be reached. Nothing was stored.'
}

export function ManualClaimForm({
  assetId,
  document,
  onCreated,
  onCancel,
}: {
  assetId: string
  document: Document
  onCreated: (extraction: LegalExtraction) => void
  onCancel: () => void
}) {
  const version = document.current_version ?? null
  const [text, setText] = useState<DocumentVersionText | null>(null)
  const [textError, setTextError] = useState('')
  const [field, setField] = useState('')
  const [value, setValue] = useState('')
  const [page, setPage] = useState<number | null>(null)
  const [quotation, setQuotation] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const id = (name: string) => `manual-claim-${name}-${document.id}`

  // The stored text of exactly the current version; another version's text is never shown
  useEffect(() => {
    if (!version) return
    let cancelled = false
    getDocumentVersionText(document.id, version.version_number, version.id)
      .then(t => {
        if (cancelled) return
        if (t.document_id === document.id && t.version_id === version.id) setText(t)
        else setTextError('The server returned text of a different document version; it is not shown.')
      })
      .catch(e => { if (!cancelled) setTextError(httpErrorMessage(e, 'read the text of this document version')) })
    return () => { cancelled = true }
  }, [document.id, version?.id])

  if (!version) return null
  const pageText = page != null ? text?.pages.find(p => p.page_number === page) ?? null : null

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (page == null) return
    setSubmitting(true)
    setError('')
    try {
      const created = await createManualExtraction(document.id, {
        asset_id: assetId,
        version_id: version.id,
        claims: [{ field_name: field, value, source_page: page, supporting_passage: quotation }],
      })
      onCreated(created)
    } catch (err) {
      setError(manualClaimErrorMessage(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <form onSubmit={submit} data-testid="manual-claim-form" aria-labelledby={id('heading')}
          className="mt-3 space-y-3 rounded-lg bg-slate-50 p-4 ring-1 ring-slate-200">
      <div>
        <h5 id={id('heading')} className="font-semibold text-slate-900">Add a Manual Legal Claim</h5>
        <p data-testid="manual-claim-note" className="mt-1 text-xs text-slate-600">
          Version {version.version_number} of this document. The claim is created as an unapproved draft labelled
          {' '}<span className="font-medium">Manual entry</span>; no AI is used. The server checks that your quotation
          appears on the selected page: this proves only that the text is there, not that the claim is legally
          correct. Nothing is approved until reviewers approve it.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor={id('field')} className="mb-1 block text-xs font-medium text-slate-700">Legal field</label>
          <select id={id('field')} required value={field} onChange={e => setField(e.target.value)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none">
            <option value="" disabled>Select a legal field…</option>
            {Object.entries(LEGAL_FIELD_LABELS).map(([key, label]) => <option key={key} value={key}>{label}</option>)}
          </select>
        </div>
        <div>
          <label htmlFor={id('page')} className="mb-1 block text-xs font-medium text-slate-700">Source page</label>
          <select id={id('page')} required value={page ?? ''} disabled={!text}
                  onChange={e => setPage(e.target.value ? Number(e.target.value) : null)}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none disabled:opacity-60">
            <option value="" disabled>{text ? 'Select the page that holds the quotation…' : 'Loading pages…'}</option>
            {text?.pages.map(p => (
              <option key={p.page_number} value={p.page_number} disabled={!p.text_content}>
                {p.text_content ? `Page ${p.page_number}` : `Page ${p.page_number} — no text layer, cannot be quoted`}
              </option>
            ))}
          </select>
        </div>
      </div>

      {textError && <p role="alert" data-testid="manual-claim-text-error" className="text-sm text-red-700">{textError}</p>}
      {pageText && (
        <section aria-label={`Text of page ${pageText.page_number}`} data-testid="manual-claim-page-text"
                 className="max-h-64 overflow-auto rounded-lg bg-white p-3 ring-1 ring-slate-200">
          <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">
            Page {pageText.page_number} of {text?.page_count} · stored text of version {version.version_number}
          </p>
          <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm text-slate-700">{pageText.text_content}</pre>
        </section>
      )}

      <div>
        <label htmlFor={id('value')} className="mb-1 block text-xs font-medium text-slate-700">Claim</label>
        <textarea id={id('value')} required rows={2} maxLength={2000} value={value} onChange={e => setValue(e.target.value)}
                  placeholder="The legal right as stated by the document, in your words"
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none" />
      </div>
      <div>
        <label htmlFor={id('quotation')} className="mb-1 block text-xs font-medium text-slate-700">
          Exact quotation from the selected page
        </label>
        <textarea id={id('quotation')} required rows={3} maxLength={2000} value={quotation}
                  onChange={e => setQuotation(e.target.value)} aria-describedby={id('quotation-note')}
                  className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none" />
        <p id={id('quotation-note')} className="mt-1 text-xs text-slate-500">
          Copy it word for word: at least 20 characters and 3 words. It is never completed or corrected for you.
        </p>
      </div>

      {error && <p role="alert" data-testid="manual-claim-error" className="text-sm text-red-700">{error}</p>}
      <div className="flex gap-3">
        <button type="button" onClick={onCancel} className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">
          Cancel
        </button>
        <button type="submit" disabled={submitting || page == null}
                className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-60">
          {submitting ? 'Checking…' : 'Create Draft Claim'}
        </button>
      </div>
    </form>
  )
}
