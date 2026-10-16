import { useEffect, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  getAsset,
  getBlockchainStatus,
  getDocuments,
  getExtractions,
  triggerExtraction,
  uploadDocument,
} from '../api'
import type { Asset, BlockchainStatus, Document, DocumentType, LegalExtraction } from '../types'
import { PageSpinner } from '../components/Spinner'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { ApprovalStateBadge, ExtractionStatusBadge } from '../components/StatusBadge'

type Tab = 'documents' | 'blockchain'

const DOC_TYPES: DocumentType[] = [
  'prospectus', 'legal_opinion', 'title_deed', 'regulatory_filing', 'audit_report', 'other',
]

function fmt(iso: string) {
  return new Date(iso).toLocaleDateString('en-GB', {
    day: '2-digit', month: 'short', year: 'numeric',
  })
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
    try {
      await triggerExtraction(docId)
      await loadExtractions(docId)
    } catch (err) {
      alert(err instanceof ApiError ? err.message : 'Extraction failed')
    } finally {
      setExtracting(null)
    }
  }

  if (loading) return <PageSpinner />

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-sm text-slate-500">{docs.length} document{docs.length !== 1 ? 's' : ''}</p>
        <button
          onClick={() => setShowUpload(s => !s)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-700"
        >
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
          </svg>
          Upload Document
        </button>
      </div>

      {error && <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</div>}

      {showUpload && (
        <form onSubmit={handleUpload} className="rounded-xl bg-white p-5 ring-1 ring-slate-200 space-y-3">
          <h4 className="font-semibold text-slate-900">Upload Legal Document</h4>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label className="mb-1 block text-xs font-medium text-slate-700">Document Type</label>
              <select
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
              <label className="mb-1 block text-xs font-medium text-slate-700">Title</label>
              <input
                required
                value={title}
                onChange={e => setTitle(e.target.value)}
                placeholder="Q4 2024 Prospectus"
                className="w-full rounded-lg border border-slate-300 px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none"
              />
            </div>
          </div>
          <div>
            <label className="mb-1 block text-xs font-medium text-slate-700">PDF File</label>
            <input
              required
              type="file"
              accept=".pdf"
              onChange={e => setFile(e.target.files?.[0] ?? null)}
              className="w-full text-sm text-slate-600 file:mr-3 file:rounded-lg file:border-0 file:bg-indigo-50 file:px-4 file:py-2 file:text-sm file:font-medium file:text-indigo-700 hover:file:bg-indigo-100"
            />
          </div>
          {uploadError && <p className="text-sm text-red-600">{uploadError}</p>}
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
            return (
              <div key={doc.id} className="rounded-xl bg-white p-5 ring-1 ring-slate-200">
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
                    {/* The hash belongs to a version; list responses carry no current_version */}
                    {doc.current_version?.sha256_hash && (
                      <p className="mt-1 font-mono text-xs text-slate-400">{doc.current_version.sha256_hash.slice(0, 16)}…</p>
                    )}
                  </div>
                  <button
                    onClick={() => handleTriggerExtraction(doc.id)}
                    disabled={extracting === doc.id}
                    className="flex items-center gap-1.5 rounded-lg bg-violet-50 px-3 py-1.5 text-xs font-medium text-violet-700 hover:bg-violet-100 disabled:opacity-60"
                  >
                    <svg className="h-3.5 w-3.5" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
                      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09z" />
                    </svg>
                    {extracting === doc.id ? 'Extracting…' : 'Run AI Extraction'}
                  </button>
                </div>

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
                          <span className="font-mono text-xs text-slate-400">{ex.ai_model}</span>
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
          {(['documents', 'blockchain'] as Tab[]).map(t => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`flex-1 rounded-lg py-2 text-sm font-medium transition-colors ${
                tab === t ? 'bg-white text-slate-900 shadow-sm' : 'text-slate-500 hover:text-slate-900'
              }`}
            >
              {t === 'documents' ? 'Documents & Extractions' : 'Blockchain Status'}
            </button>
          ))}
        </div>

        <div className="mt-4">
          {tab === 'documents' ? (
            <DocumentsTab asset={asset} />
          ) : (
            <BlockchainTab asset={asset} />
          )}
        </div>
      </div>
    </div>
  )
}
