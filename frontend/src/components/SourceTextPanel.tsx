import { useEffect, useRef, useState } from 'react'
import { ApiError, getDocumentVersions, getDocumentVersionText } from '../api'
import type { DocumentVersion, DocumentVersionText } from '../types'
import { httpErrorMessage } from '../evidence'

// Source text of exactly one document version (Stage 9B-4, step 9; endpoint of step 8).
//
// - The version under review is located by its exact id in the document's version list; if it
//   is not there, no text is shown (no fallback to the current or any other version).
// - Every text request names the version number AND its id; the backend refuses a mismatch.
// - A response is shown only if it is the latest request and names exactly the requested
//   document and version, so a slow earlier response can never replace the selected text.

interface Selection {
  versionNumber: number
  versionId: string
}

type TextState =
  | { kind: 'idle' }
  | { kind: 'loading'; key: string }
  | { kind: 'loaded'; key: string; text: DocumentVersionText }
  | { kind: 'error'; key: string; message: string }

const keyOf = (documentId: string, s: Selection) => `${documentId}#${s.versionNumber}#${s.versionId}`

function textErrorMessage(error: unknown, versionNumber: number): string {
  if (error instanceof ApiError) {
    if (error.status === 404 && error.message === 'Document version not found.') {
      return `Version ${versionNumber} of this document was not found.`
    }
    if (error.status === 404) {
      return 'This document is not available: it does not exist or is outside your access.'
    }
    if (error.status === 409 && error.field('source_text_unavailable_reason')) {
      const reason = error.field('source_text_unavailable_reason') === 'no_page_records'
        ? 'no page records are stored'
        : 'its page records are inconsistent'
      return `The stored source text of version ${versionNumber} is unavailable (${reason}). It is not shown, and no other version's text is substituted.`
    }
    if (error.status === 409) {
      return `Version reference mismatch: the requested version identifier does not belong to version ${versionNumber}. No text is shown.`
    }
    if (error.status === 422) return 'Invalid document version reference. No text is shown.'
  }
  return httpErrorMessage(error, 'read the source text of this document')
}

export function SourceTextPanel({
  documentId,
  reviewedVersionId,
  focus,
}: {
  documentId: string
  // The document version the extraction under review was made from
  reviewedVersionId: string
  // A page of the reviewed version to bring into view (nonce: repeat the same page)
  focus: { page: number; nonce: number } | null
}) {
  const [versions, setVersions] = useState<DocumentVersion[] | null>(null)
  const [versionsError, setVersionsError] = useState('')
  const [selected, setSelected] = useState<Selection | null>(null)
  const [textState, setTextState] = useState<TextState>({ kind: 'idle' })
  const [highlight, setHighlight] = useState<number | null>(null)
  const latestRequest = useRef(0)

  const reviewed = versions?.find(v => v.id === reviewedVersionId && v.document_id === documentId) ?? null

  // 1. Version list of the document; the reviewed version is selected by its exact id
  useEffect(() => {
    let cancelled = false
    setVersions(null)
    setVersionsError('')
    setSelected(null)
    setTextState({ kind: 'idle' })
    getDocumentVersions(documentId)
      .then(list => {
        if (cancelled) return
        const own = list.filter(v => v.document_id === documentId)
        setVersions(own)
        const match = own.find(v => v.id === reviewedVersionId)
        if (match) {
          setSelected({ versionNumber: match.version_number, versionId: match.id })
        } else {
          setVersionsError(
            'The document version this extraction was made from is not available. No source text is shown, ' +
            'and no other version is substituted.',
          )
        }
      })
      .catch(error => {
        if (cancelled) return
        setVersionsError(httpErrorMessage(error, 'list the versions of this document'))
      })
    return () => { cancelled = true }
  }, [documentId, reviewedVersionId])

  // 2. Text of the selected version; only the latest request may set the displayed text
  useEffect(() => {
    if (!selected) return
    const requestId = ++latestRequest.current
    const key = keyOf(documentId, selected)
    setTextState({ kind: 'loading', key })
    getDocumentVersionText(documentId, selected.versionNumber, selected.versionId)
      .then(text => {
        if (requestId !== latestRequest.current) return
        const exact =
          text.document_id === documentId &&
          text.version_number === selected.versionNumber &&
          text.version_id === selected.versionId
        setTextState(exact
          ? { kind: 'loaded', key, text }
          : { kind: 'error', key, message: 'The server returned text of a different document version; it is not shown.' })
      })
      .catch(error => {
        if (requestId !== latestRequest.current) return
        setTextState({ kind: 'error', key, message: textErrorMessage(error, selected.versionNumber) })
      })
    // A request that is still pending when the selection changes can no longer set the text
    return () => { latestRequest.current += 1 }
  }, [documentId, selected])

  // 3. A claim's "View page" switches back to the reviewed version and marks that page
  useEffect(() => {
    if (!focus || !reviewed) return
    if (selected?.versionId !== reviewed.id) {
      setSelected({ versionNumber: reviewed.version_number, versionId: reviewed.id })
    }
    setHighlight(focus.page)
  }, [focus, reviewed?.id])

  const currentKey = selected ? keyOf(documentId, selected) : ''
  const shown = textState.kind !== 'idle' && textState.key === currentKey ? textState : null
  const viewingOther = !!selected && !!reviewed && selected.versionId !== reviewed.id
  const highlightPage = !viewingOther ? highlight : null

  useEffect(() => {
    if (highlightPage == null || shown?.kind !== 'loaded') return
    document.getElementById(`source-page-${highlightPage}`)?.scrollIntoView?.({ block: 'nearest' })
  }, [highlightPage, shown?.kind, focus?.nonce])

  const latestNumber = versions?.reduce((max, v) => Math.max(max, v.version_number), 0) ?? 0

  return (
    <section
      aria-labelledby="source-text-heading"
      data-testid="source-text-panel"
      className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200"
    >
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-100 px-6 py-4">
        <div>
          <h3 id="source-text-heading" className="font-semibold text-slate-900">Source Text</h3>
          <p className="mt-1 text-sm text-slate-500">
            Text extracted from the PDF text layer of one document version at upload.
          </p>
        </div>
        {versions && reviewed && (
          <div>
            <label htmlFor="source-version" className="mb-1 block text-xs font-medium text-slate-700">
              Document version
            </label>
            <select
              id="source-version"
              value={selected?.versionId ?? ''}
              onChange={e => {
                const v = versions.find(x => x.id === e.target.value)
                if (v) setSelected({ versionNumber: v.version_number, versionId: v.id })
              }}
              className="rounded-lg border border-slate-300 bg-white px-3 py-1.5 text-sm focus:border-indigo-500 focus:outline-none"
            >
              {[...versions].sort((a, b) => b.version_number - a.version_number).map(v => (
                <option key={v.id} value={v.id}>
                  {`Version ${v.version_number}`}
                  {v.id === reviewed.id ? ' — under review' : ''}
                  {v.version_number === latestNumber ? ' (latest)' : ''}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="space-y-3 px-6 py-4">
        {versionsError && (
          <p role="alert" data-testid="source-text-error" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {versionsError}
          </p>
        )}
        {!versions && !versionsError && <p role="status" className="text-sm text-slate-500">Loading document versions…</p>}

        {viewingOther && reviewed && selected && (
          <p data-testid="other-version-notice" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-900 ring-1 ring-amber-200">
            You are viewing version {selected.versionNumber}. This extraction, and the check of its
            quotations, belong to version {reviewed.version_number}.
          </p>
        )}

        {shown?.kind === 'loading' && <p role="status" className="text-sm text-slate-500">Loading source text…</p>}
        {shown?.kind === 'error' && (
          <p role="alert" data-testid="source-text-error" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">
            {shown.message}
          </p>
        )}
        {shown?.kind === 'loaded' && (
          <div data-testid="source-text" data-version-id={shown.text.version_id}>
            <p data-testid="source-text-version" className="text-sm font-medium text-slate-800">
              Version {shown.text.version_number} · {shown.text.page_count} page{shown.text.page_count !== 1 ? 's' : ''}
              {' · '}
              {shown.text.is_current_version
                ? 'current version'
                : `not current (latest is version ${shown.text.current_version_number})`}
            </p>
            <p className="mt-1 font-mono text-xs text-slate-400" title="SHA-256 of this version's PDF file">
              File SHA-256 {shown.text.sha256_hash.slice(0, 16)}… · page text SHA-256 {shown.text.source_text_sha256.slice(0, 16)}…
            </p>
            <p data-testid="source-text-notice" className="mt-2 text-xs text-slate-500">{shown.text.notice}</p>
            <div className="mt-3 space-y-3">
              {shown.text.pages.map(p => (
                <section
                  key={p.page_number}
                  id={`source-page-${p.page_number}`}
                  aria-label={`Page ${p.page_number}`}
                  data-testid="source-page"
                  data-highlighted={highlightPage === p.page_number ? 'true' : undefined}
                  className={`rounded-lg p-3 ring-1 ${highlightPage === p.page_number ? 'bg-amber-50 ring-amber-300' : 'bg-slate-50 ring-slate-200'}`}
                >
                  <p className="text-xs font-semibold uppercase tracking-wider text-slate-500">Page {p.page_number}</p>
                  {p.text_content ? (
                    <pre className="mt-1 whitespace-pre-wrap break-words font-sans text-sm text-slate-700">{p.text_content}</pre>
                  ) : (
                    <p className="mt-1 text-sm italic text-slate-400">No text layer on this page (no OCR is performed).</p>
                  )}
                </section>
              ))}
            </div>
          </div>
        )}
      </div>
    </section>
  )
}
