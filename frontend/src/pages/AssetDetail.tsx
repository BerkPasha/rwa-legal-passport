import { useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import {
  ApiError,
  createStellarAnchor,
  downloadDocument,
  getAiStatus,
  getAnchorVerification,
  getAsset,
  getBlockchainStatus,
  getDocuments,
  getExtractions,
  getStellarAnchors,
  refreshStellarAnchor,
  triggerExtraction,
  uploadDocument,
} from '../api'
import type {
  AIStatus,
  AnchorVerification,
  Asset,
  BlockchainStatus,
  Document,
  DocumentType,
  LegalExtraction,
  StellarAnchor,
  TextLayerSummary,
} from '../types'
import { PageSpinner } from '../components/Spinner'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { EvidenceAnchorCard } from '../components/EvidenceAnchorCard'
import { ApprovalStateBadge, ExtractionStatusBadge } from '../components/StatusBadge'
import { ANCHOR_STATUS_LABELS, anchorErrorMessage, shortHash, testnetExplorerTx } from '../anchor'
import { confirmationPrompt, confirmationRequest, extractionOrigin } from '../aiProcessing'
import { formatSize, saveBlob, sha256Hex } from '../download'
import { ManualClaimForm } from '../components/ManualClaimForm'
import { useAuth } from '../AuthContext'
import { useReadOnly } from '../serverInfo'

type Tab = 'documents' | 'blockchain' | 'anchor'

const TAB_LABELS: Record<Tab, string> = {
  documents: 'Documents & Extractions',
  blockchain: 'Blockchain Status',
  anchor: 'Stellar Evidence Anchor',
}

export function passportLink(asset: Pick<Asset, 'blockchain_network' | 'stellar_asset_code' | 'stellar_issuer_public_key'>) {
  const query = new URLSearchParams({
    network: asset.blockchain_network,
    asset_code: asset.stellar_asset_code,
    issuer_key: asset.stellar_issuer_public_key,
  })
  return `/passport?${query.toString()}`
}

const DOC_TYPES: DocumentType[] = [
  'prospectus', 'legal_opinion', 'title_deed', 'regulatory_filing', 'audit_report', 'other',
]

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
}

// Pages without a text layer (scanned or blank) cannot be quoted or verified; nothing is inferred
function TextLayerNotice({ layer }: { layer: TextLayerSummary }) {
  if (!layer.requires_ocr) return null
  const pages = layer.pages_without_text
  const message = layer.status === 'none'
    ? 'No page has a text layer (for example a scanned PDF). OCR is not supported: nothing can be extracted or verified from this version.'
    : `No text layer on page${pages.length === 1 ? '' : 's'} ${pages.join(', ')} of ${layer.page_count} (for example scanned pages). OCR is not supported: quotations on ${pages.length === 1 ? 'that page' : 'those pages'} can never be verified.`
  return <p role="note" data-testid="text-layer-notice" className="mt-1 text-xs text-amber-700">{message}</p>
}

function DocumentsTab({ asset }: { asset: Asset }) {
  const [docs, setDocs] = useState<Document[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showUpload, setShowUpload] = useState(false)
  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState<DocumentType>('prospectus')
  const [title, setTitle] = useState('')
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [extracting, setExtracting] = useState<string | null>(null)
  const [extractions, setExtractions] = useState<Record<string, LegalExtraction[]>>({})
  const [extractionErrors, setExtractionErrors] = useState<Record<string, string>>({})
  const [downloads, setDownloads] = useState<Record<string, { busy?: boolean; ok?: string; error?: string }>>({})
  // Whether AI extraction can run here at all; null while unknown (the server still decides)
  const [aiStatus, setAiStatus] = useState<AIStatus | null>(null)
  // The document whose manual claim form is open (Stage 10B-3)
  const [manualFor, setManualFor] = useState<string | null>(null)
  const navigate = useNavigate()
  const { identity } = useAuth()
  // A read-only showcase refuses every write (Stage 10C-1); its actions are not offered
  const readOnly = useReadOnly()
  // Offered to roles that may create extraction records; the server enforces it
  const canCreateClaims = !readOnly && identity.state === 'signed_in' && identity.user.permissions.includes('extraction:run')

  useEffect(() => {
    getAiStatus().then(setAiStatus).catch(() => setAiStatus(null))
  }, [])

  const loadDocs = () => {
    setLoading(true)
    getDocuments(asset.id)
      .then(setDocs)
      .catch(e => setError(e instanceof ApiError ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  useEffect(loadDocs, [asset.id])

  const loadExtractions = async (docId: string) => {
    try {
      const data = await getExtractions(docId)
      setExtractions(prev => ({ ...prev, [docId]: data }))
    } catch { /* ignore */ }
  }

  useEffect(() => {
    docs.forEach(d => loadExtractions(d.id))
  }, [docs])

  const handleUpload = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!file) return
    setUploading(true)
    setUploadError('')
    const fd = new FormData()
    fd.append('file', file)
    fd.append('document_type', docType)
    fd.append('title', title)
    try {
      await uploadDocument(asset.id, fd)
      setShowUpload(false)
      setFile(null)
      setTitle('')
      loadDocs()
    } catch (err) {
      setUploadError(err instanceof ApiError ? err.message : 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const handleTriggerExtraction = async (docId: string) => {
    setExtracting(docId)
    setExtractionErrors(prev => ({ ...prev, [docId]: '' }))
    try {
      try {
        await triggerExtraction(docId)
      } catch (err) {
        // An external AI provider: nothing is sent until the user confirms this exact version
        const summary = confirmationRequest(err)
        if (!summary) throw err
        if (!window.confirm(confirmationPrompt(summary))) return
        await triggerExtraction(docId, { confirm_external_processing: true, version_id: summary.version_id })
      }
      await loadExtractions(docId)
    } catch (err) {
      setExtractionErrors(prev => ({ ...prev, [docId]: err instanceof ApiError ? err.message : 'Extraction failed: the server could not be reached.' }))
    } finally {
      setExtracting(null)
    }
  }

  // The current version's PDF, saved only if its bytes match the hash recorded for that version
  const handleDownload = async (doc: Document) => {
    const version = doc.current_version
    if (!version) return
    setDownloads(prev => ({ ...prev, [doc.id]: { busy: true } }))
    try {
      const blob = await downloadDocument(doc.id)
      if (await sha256Hex(blob) !== version.sha256_hash) {
        setDownloads(prev => ({ ...prev, [doc.id]: { error:
          `The received file does not match the SHA-256 recorded for version ${version.version_number}; it was not saved. A newer version may exist: reload the page.` } }))
        return
      }
      saveBlob(blob, version.original_filename)
      setDownloads(prev => ({ ...prev, [doc.id]: { ok: `Version ${version.version_number} downloaded · SHA-256 verified` } }))
    } catch (err) {
      setDownloads(prev => ({ ...prev, [doc.id]: { error: err instanceof ApiError
        ? `Download failed: ${err.message}` : 'Download failed: the server could not be reached.' } }))
    }
  }

  const aiUnavailable = aiStatus !== null && !aiStatus.available

  if (loading) return <PageSpinner />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">{docs.length} document{docs.length !== 1 ? 's' : ''}</p>
        {!readOnly && <button
          onClick={() => setShowUpload(s => !s)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
        >
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
          </svg>
          Upload Document
        </button>}
      </div>

      {error && <div role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {aiStatus && (
        <div role="note" id="ai-status" data-testid="ai-status"
             className={`rounded-lg p-3 text-sm ring-1 ${aiStatus.available ? 'bg-sky-50 text-sky-900 ring-sky-200' : 'bg-amber-50 text-amber-900 ring-amber-200'}`}>
          <p className="font-medium">{aiStatus.available ? 'AI extraction available' : 'External AI extraction is disabled'}</p>
          <p className="mt-1">{aiStatus.message}</p>
          {!aiStatus.available && (
            <p className="mt-1">
              Extractions listed below were created earlier or are test data; the origin of each is shown.
              {canCreateClaims && ' Legal claims can still be entered manually from a document\'s pages: Add Manual Claim.'}
            </p>
          )}
        </div>
      )}

      {showUpload && (
        <form onSubmit={handleUpload} className="rounded-xl bg-white p-5 ring-1 ring-slate-200 space-y-3">
          <h4 className="font-semibold text-slate-900">Upload Legal Document</h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="upload-document-type" className="mb-1 block text-xs font-medium text-slate-700">Document Type</label>
              <select
                id="upload-document-type"
                value={docType}
                onChange={e => setDocType(e.target.value as DocumentType)}
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              >
                {DOC_TYPES.map(t => (
                  <option key={t} value={t}>{t.replace(/_/g, ' ')}</option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor="upload-title" className="mb-1 block text-xs font-medium text-slate-700">Title</label>
              <input
                id="upload-title"
                required
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Q4 2024 Prospectus"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label htmlFor="upload-file" className="mb-1 block text-xs font-medium text-slate-700">PDF File</label>
            <input
              id="upload-file"
              required
              type="file"
              accept=".pdf"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              className="w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
            />
          </div>
          {uploadError && <p role="alert" className="text-sm text-red-600">{uploadError}</p>}
          <div className="flex gap-3">
            <button type="button" onClick={() => setShowUpload(false)} className="rounded-lg px-3 py-1.5 text-sm text-slate-600 hover:bg-slate-100">Cancel</button>
            <button type="submit" disabled={uploading} className="rounded-lg bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white disabled:opacity-60">
              {uploading ? 'Uploading…' : 'Upload'}
            </button>
          </div>
        </form>
      )}

      {docs.length === 0 ? (
        <div className="rounded-xl bg-slate-50 py-12 text-center ring-1 ring-slate-200">
          <p className="text-slate-500">No documents uploaded yet.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {docs.map(doc => {
            const docExtractions = extractions[doc.id] ?? []
            const version = doc.current_version
            const download = downloads[doc.id]
            return (
              <div key={doc.id} data-testid="document-card" className="rounded-xl bg-white p-5 ring-1 ring-slate-200">
                <div className="flex items-start justify-between">
                  <div>
                    <p className="font-semibold text-slate-900">{doc.title}</p>
                    <div className="mt-1 flex items-center gap-3 text-xs text-slate-500">
                      <span className="capitalize">{doc.document_type.replace(/_/g, ' ')}</span>
                      <span>•</span>
                      <span>v{doc.current_version_number}</span>
                      <span>•</span>
                      <span>{fmt(doc.created_at)}</span>
                    </div>
                    {version && (
                      <p data-testid="document-file" className="mt-1 text-xs text-slate-500">
                        {version.original_filename} · {formatSize(version.file_size)}
                        {version.text_layer ? ` · ${version.text_layer.page_count} page${version.text_layer.page_count !== 1 ? 's' : ''}` : ''}
                      </p>
                    )}
                    {/* The hash belongs to a version */}
                    {version?.sha256_hash && (
                      <p data-testid="document-sha256" className="mt-1 font-mono text-xs text-slate-400" title={version.sha256_hash}>
                        SHA-256 {version.sha256_hash.slice(0, 16)}…
                      </p>
                    )}
                    {version?.text_layer && <TextLayerNotice layer={version.text_layer} />}
                    {download?.ok && <p role="status" data-testid="download-status" className="mt-1 text-xs text-emerald-700">{download.ok}</p>}
                    {download?.error && <p role="alert" data-testid="download-error" className="mt-1 text-xs text-red-700">{download.error}</p>}
                    {extractionErrors[doc.id] && (
                      <p role="alert" data-testid="extraction-error" className="mt-1 text-xs text-red-700">{extractionErrors[doc.id]}</p>
                    )}
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-2">
                  {version && canCreateClaims && (
                    <button
                      data-testid="add-manual-claim"
                      onClick={() => setManualFor(open => open === doc.id ? null : doc.id)}
                      aria-expanded={manualFor === doc.id}
                      className="rounded-lg bg-indigo-50 px-3 py-1.5 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
                    >
                      Add Manual Claim
                    </button>
                  )}
                  {version && (
                    <button
                      data-testid="download-document"
                      onClick={() => handleDownload(doc)}
                      disabled={download?.busy}
                      className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-60"
                    >
                      {download?.busy ? 'Downloading…' : `Download PDF (v${version.version_number})`}
                    </button>
                  )}
                  {!readOnly && <button
                    onClick={() => handleTriggerExtraction(doc.id)}
                    disabled={extracting === doc.id || aiUnavailable}
                    aria-describedby={aiStatus ? 'ai-status' : undefined}
                    title={aiUnavailable ? 'External AI extraction is disabled on this server' : undefined}
                    className="flex items-center gap-1.5 rounded-lg bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-100 disabled:opacity-60"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                    </svg>
                    {extracting === doc.id ? 'Extracting…' : 'Run AI Extraction'}
                  </button>}
                  </div>
                </div>

                {manualFor === doc.id && version && (
                  <ManualClaimForm
                    assetId={asset.id}
                    document={doc}
                    onCancel={() => setManualFor(null)}
                    onCreated={created => navigate(
                      `/assets/${asset.id}/documents/${doc.id}/extractions/${created.id}`,
                      { state: { manualClaimCreated: true } },
                    )}
                  />
                )}

                {docExtractions.length > 0 && (
                  <div className="mt-3 space-y-1.5 border-t border-slate-100 pt-3">
                    <p className="text-xs font-medium uppercase tracking-wider text-slate-400">Extractions</p>
                    {docExtractions.map(ex => (
                      <Link
                        key={ex.id}
                        to={`/assets/${asset.id}/documents/${doc.id}/extractions/${ex.id}`}
                        className="flex items-center justify-between rounded-lg bg-slate-50 px-3 py-2 hover:bg-slate-100"
                      >
                        <div className="flex items-center gap-3">
                          <ExtractionStatusBadge status={ex.status} />
                          <ApprovalStateBadge state={ex.approval_state?.state} />
                          <span className="text-xs text-slate-500">{fmt(ex.created_at)}</span>
                          <span data-testid="extraction-origin" data-origin={extractionOrigin(ex.ai_model, aiStatus?.supported_models ?? null, ex.source).kind}
                                className="font-mono text-xs text-slate-500">
                            {extractionOrigin(ex.ai_model, aiStatus?.supported_models ?? null, ex.source).label}
                          </span>
                        </div>
                        <span className="text-xs text-indigo-600">Review →</span>
                      </Link>
                    ))}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

function BlockchainTab({ asset }: { asset: Asset }) {
  const [status, setStatus] = useState<BlockchainStatus | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    getBlockchainStatus(asset.id)
      .then(setStatus)
      .catch(e => setError(e instanceof ApiError ? e.message : 'Network error'))
      .finally(() => setLoading(false))
  }, [asset.id])

  if (loading) return <PageSpinner />

  if (error) {
    return (
      <div className="rounded-xl bg-red-50 p-5 ring-1 ring-red-200">
        <p className="font-medium text-red-800">Horizon Unavailable</p>
        <p className="mt-1 text-sm text-red-700">{error}</p>
      </div>
    )
  }

  if (!status) return null

  return (
    <div className="space-y-4">
      <DisclaimerBanner text={status.disclaimer} />

      <div className="rounded-xl bg-white p-5 ring-1 ring-slate-200">
        <div className="flex items-center gap-3">
          <div className={`flex h-10 w-10 items-center justify-center rounded-full ${status.found_on_network ? 'bg-green-100' : 'bg-slate-100'}`}>
            {status.found_on_network ? (
              <svg className="h-5 w-5 text-green-600" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
              </svg>
            ) : (
              <svg className="h-5 w-5 text-slate-500" viewBox="0 0 20 20" fill="currentColor">
                <path fillRule="evenodd" d="M4 10a.75.75 0 01.75-.75h10.5a.75.75 0 010 1.5H4.75A.75.75 0 014 10z" clipRule="evenodd" />
              </svg>
            )}
          </div>
          <div>
            <p className="font-semibold text-slate-900">
              {status.found_on_network ? 'Asset found on Stellar network' : 'Asset not found on Stellar network'}
            </p>
            <p className="text-sm text-slate-500">{status.horizon_url}</p>
          </div>
        </div>

        {status.record && (
          <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-slate-100 pt-4 sm:grid-cols-4">
            <div>
              <dt className="text-xs font-medium text-slate-500">Asset Type</dt>
              <dd className="mt-1 font-mono text-sm text-slate-900">{status.record.asset_type}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-slate-500">Accounts</dt>
              <dd className="mt-1 text-sm font-semibold text-slate-900">{status.record.num_accounts.toLocaleString('en-US')}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-slate-500">Total Supply</dt>
              <dd className="mt-1 text-sm font-semibold text-slate-900">{status.record.total_supply}</dd>
            </div>
            <div>
              <dt className="text-xs font-medium text-slate-500">Auth Flags</dt>
              <dd className="mt-1 space-y-0.5">
                {Object.entries(status.record.flags).map(([k, v]) => (
                  <div key={k} className="flex items-center gap-1.5">
                    <span className={`h-2 w-2 rounded-full ${v ? 'bg-amber-400' : 'bg-slate-200'}`} />
                    <span className="text-xs text-slate-600">{k.replace(/_/g, ' ')}</span>
                  </div>
                ))}
              </dd>
            </div>
          </dl>
        )}
      </div>
    </div>
  )
}

// Anchoring a commitment submits a real Stellar Testnet transaction: an explicit confirmation is required
const ANCHOR_CONFIRMATION =
  'This submits a transaction to Stellar Testnet that records the SHA-256 digest of the current evidence ' +
  'manifest (no document content). Continue?'

function AnchorTab({ asset }: { asset: Asset }) {
  const [verification, setVerification] = useState<AnchorVerification | null>(null)
  const [history, setHistory] = useState<StellarAnchor[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionError, setActionError] = useState('')
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const testnet = asset.blockchain_network === 'stellar-testnet'
  // Submitting or refreshing an anchor is offered only to roles that may do it (Stage 10B-3);
  // the server enforces anchor:create either way. Reading and re-checking stay available to all.
  const { identity } = useAuth()
  const readOnly = useReadOnly()
  const canAnchor = !readOnly && identity.state === 'signed_in' && identity.user.permissions.includes('anchor:create')

  const load = async () => {
    setError('')
    try {
      const [v, h] = await Promise.all([getAnchorVerification(asset.id), getStellarAnchors(asset.id)])
      setVerification(v)
      setHistory(h)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : 'The server could not be reached.')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { void load() }, [asset.id])

  const run = async (action: () => Promise<StellarAnchor>, done: (a: StellarAnchor) => string) => {
    setBusy(true)
    setActionError('')
    setNotice('')
    try {
      setNotice(done(await action()))
    } catch (e) {
      setActionError(anchorErrorMessage(e))
    } finally {
      setBusy(false)
      await load()
    }
  }

  const anchorNow = () => {
    if (!window.confirm(ANCHOR_CONFIRMATION)) return
    void run(() => createStellarAnchor(asset.id), a => `Anchoring ${ANCHOR_STATUS_LABELS[a.status].toLowerCase()}.`)
  }

  if (loading) return <PageSpinner />
  if (error) {
    return <div className="rounded-xl bg-red-50 p-5 text-sm text-red-700 ring-1 ring-red-200">{error}</div>
  }
  if (!verification) return null

  const canOfferAnchor = canAnchor && testnet && verification.current_passport_available && !verification.evidence_matches_anchor
  const pending = canAnchor ? history.filter(a => a.status === 'pending') : []
  const pendingCount = history.filter(a => a.status === 'pending').length

  return (
    <div className="space-y-4">
      {!testnet && (
        <div className="rounded-xl bg-slate-50 p-4 text-sm text-slate-600 ring-1 ring-slate-200">
          Evidence anchoring is available for Stellar Testnet assets only; Mainnet anchoring is disabled.
        </div>
      )}
      <EvidenceAnchorCard
        verification={verification}
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <button
              onClick={() => { setLoading(true); void load() }}
              disabled={busy}
              className="rounded-lg bg-slate-100 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-200 disabled:opacity-60"
            >
              Re-check on Stellar
            </button>
            {pending.map(a => (
              <button
                key={a.id}
                onClick={() => void run(() => refreshStellarAnchor(asset.id, a.id),
                  r => `Anchor status: ${ANCHOR_STATUS_LABELS[r.status].toLowerCase()}.`)}
                disabled={busy}
                className="rounded-lg bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-100 disabled:opacity-60"
              >
                Refresh pending transaction
              </button>
            ))}
            {canOfferAnchor && (
              <button
                onClick={anchorNow}
                disabled={busy}
                className="rounded-lg bg-indigo-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-indigo-700 disabled:opacity-60"
              >
                {busy ? 'Submitting…' : 'Anchor current evidence on Testnet'}
              </button>
            )}
            {notice && <span data-testid="anchor-notice" className="text-xs text-slate-600">{notice}</span>}
            {pendingCount > 0 && (
              <span data-testid="anchor-pending-note" className="text-xs text-sky-800">
                {pendingCount === 1 ? 'A transaction is' : `${pendingCount} transactions are`} recorded as pending by this server:
                submitted, but not confirmed in a Stellar ledger. Until the ledger confirms it, it proves nothing.
              </span>
            )}
            {!canAnchor && (readOnly || identity.state === 'signed_in') && (
              <span data-testid="anchor-read-only-note" className="text-xs text-slate-500">
                {readOnly
                  ? 'Read-only showcase: this commitment can be read and re-checked, but no anchoring transaction can be submitted or refreshed.'
                  : 'Your role can read and re-check this commitment, but not submit or refresh anchoring transactions.'}
              </span>
            )}
            {actionError && <span data-testid="anchor-action-error" className="text-xs text-rose-700">{actionError}</span>}
          </div>
        }
      />

      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-3">
          <h4 className="text-sm font-semibold text-slate-900">Anchoring History</h4>
          <p className="mt-0.5 text-xs text-slate-500">
            Statuses as recorded by this server; only the verification above checks Stellar Testnet.
          </p>
        </div>
        {history.length === 0 ? (
          <p className="px-6 py-4 text-sm text-slate-500">No anchoring transaction recorded yet.</p>
        ) : (
          <ul data-testid="anchor-history" className="divide-y divide-slate-100">
            {history.map(a => (
              <li key={a.id} className="flex flex-wrap items-center justify-between gap-2 px-6 py-3 text-xs">
                <span className="font-medium text-slate-800">{ANCHOR_STATUS_LABELS[a.status]}</span>
                <span className="font-mono text-slate-500">digest {shortHash(a.manifest_digest, 8)}</span>
                {a.transaction_hash ? (
                  <a href={testnetExplorerTx(a.transaction_hash)} target="_blank" rel="noopener noreferrer"
                     className="font-mono text-indigo-600 hover:text-indigo-800">tx {shortHash(a.transaction_hash, 8)} ↗</a>
                ) : <span className="text-slate-400">no transaction</span>}
                <span className="text-slate-500">{new Date(a.created_at).toLocaleString('en-US')}</span>
                {a.failure_reason && <span className="text-rose-700">{a.failure_reason}</span>}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  )
}

export function AssetDetail() {
  const { id } = useParams<{ id: string }>()
  const [asset, setAsset] = useState<Asset | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [tab, setTab] = useState<Tab>('documents')

  useEffect(() => {
    if (!id) return
    getAsset(id)
      .then(setAsset)
      .catch(e => setError(e instanceof ApiError ? e.message : 'Not found'))
      .finally(() => setLoading(false))
  }, [id])

  if (loading) return <PageSpinner />
  if (error) return <div className="rounded-lg bg-red-50 p-4 text-red-700">{error}</div>
  if (!asset) return null

  return (
    <div className="space-y-6">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-2 text-sm text-slate-500">
        <Link to="/assets" className="hover:text-slate-900">Assets</Link>
        <span>/</span>
        <span className="font-medium text-slate-900">{asset.stellar_asset_code}</span>
      </nav>

      {/* Asset header card */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <div className="flex flex-wrap items-start gap-4">
          <div className="flex h-14 w-14 items-center justify-center rounded-xl bg-indigo-100">
            <span className="font-mono text-lg font-bold text-indigo-700">{asset.stellar_asset_code.slice(0, 2)}</span>
          </div>
          <div className="flex-1">
            <div className="flex items-center gap-3">
              <h2 className="text-xl font-bold text-slate-900">{asset.stellar_asset_code}</h2>
              <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${
                asset.status === 'active' ? 'bg-green-100 text-green-700 ring-green-200' :
                asset.status === 'suspended' ? 'bg-amber-100 text-amber-700 ring-amber-200' :
                'bg-slate-100 text-slate-600 ring-slate-200'
              }`}>{asset.status}</span>
            </div>
            <p className="mt-0.5 text-slate-600">{asset.issuer_organization_name}</p>
          </div>
          <Link
            data-testid="open-passport-link"
            to={passportLink(asset)}
            className="rounded-lg bg-indigo-50 px-3 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100"
          >
            Open Legal Passport →
          </Link>
        </div>

        <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-slate-100 pt-4 sm:grid-cols-4">
          <div>
            <dt className="text-xs font-medium text-slate-500">Network</dt>
            <dd className="mt-1 text-sm font-semibold text-slate-900">
              {asset.blockchain_network === 'stellar-mainnet' ? 'Mainnet' : 'Testnet'}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-slate-500">Asset Type</dt>
            <dd className="mt-1 text-sm font-semibold capitalize text-slate-900">
              {asset.asset_type.replace(/_/g, ' ')}
            </dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-slate-500">Jurisdiction</dt>
            <dd className="mt-1 font-mono text-sm font-semibold text-slate-900">{asset.jurisdiction}</dd>
          </div>
          <div>
            <dt className="text-xs font-medium text-slate-500">Registered</dt>
            <dd className="mt-1 text-sm text-slate-900">{fmt(asset.created_at)}</dd>
          </div>
        </dl>

        <div className="mt-4 border-t border-slate-100 pt-4">
          <p className="text-xs font-medium text-slate-500">Issuer Public Key</p>
          <p className="mt-1 break-all font-mono text-xs text-slate-700">{asset.stellar_issuer_public_key}</p>
        </div>
      </div>

      {/* Tabs */}
      <div>
        <div className="flex gap-1 rounded-xl bg-slate-100 p-1">
          {(['documents', 'blockchain', 'anchor'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition-colors ${
                tab === t ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-900'
              }`}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {tab === 'documents' && <DocumentsTab asset={asset} />}
          {tab === 'blockchain' && <BlockchainTab asset={asset} />}
          {tab === 'anchor' && <AnchorTab asset={asset} />}
        </div>
      </div>
    </div>
  )
}
