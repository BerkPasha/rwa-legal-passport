import type {
  Asset,
  ApprovalPayload,
  BlockchainStatus,
  ClaimCorrectionPayload,
  CreateAssetPayload,
  Document,
  DocumentVersion,
  DocumentVersionText,
  LegalExtraction,
  LegalPassport,
  ReviewApproval,
  ReviewCorrection,
  ReviewStatus,
} from './types'

export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// ── Credentials ───────────────────────────────────────────────────────────────
// Every internal endpoint requires the user's Bearer token. AuthProvider keeps
// this in sync; it is attached to internal requests only — never to the
// institutional passport endpoint, which authenticates with X-API-Key.

let userToken = ''

export function setUserToken(token: string): void {
  userToken = token.trim()
}

type Auth = 'user' | 'none'

// ── Error handling ────────────────────────────────────────────────────────────

export class ApiError extends Error {
  // body: the parsed JSON error body, kept for structured fields such as correction_rule,
  // evidence_status, evidence_failures or source_text_unavailable_reason (Stage 9B-4)
  constructor(public readonly status: number, message: string, public readonly body: unknown = null) {
    super(message)
    this.name = 'ApiError'
  }

  // A string field of the error body, or undefined
  field(name: string): string | undefined {
    const value = (this.body as Record<string, unknown> | null)?.[name]
    return typeof value === 'string' ? value : undefined
  }
}

// FastAPI validation errors carry a list of {loc, msg}; anything else is shown as is
function detailMessage(detail: unknown, fallback: string): string {
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const parts = detail.map(item => {
      const { loc, msg } = (item ?? {}) as { loc?: unknown; msg?: unknown }
      const where = Array.isArray(loc) ? loc.filter(p => p !== 'body').join('.') : ''
      return typeof msg === 'string' ? (where ? `${where}: ${msg}` : msg) : ''
    }).filter(Boolean)
    if (parts.length > 0) return `Invalid request — ${parts.join('; ')}`
  }
  return fallback
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  extraHeaders: Record<string, string> = {},
  auth: Auth = 'user',
): Promise<T> {
  const isFormData = init.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...(auth === 'user' && userToken ? { Authorization: `Bearer ${userToken}` } : {}),
    ...extraHeaders,
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  })

  if (!res.ok) {
    let detail = res.statusText
    let body: unknown = null
    try {
      body = await res.json()
      detail = detailMessage((body as { detail?: unknown } | null)?.detail, detail)
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail, body)
  }

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

// ── Assets ────────────────────────────────────────────────────────────────────

export const getAssets = (): Promise<Asset[]> =>
  request('/assets')

export const getAsset = (id: string): Promise<Asset> =>
  request(`/assets/${id}`)

export const createAsset = (data: CreateAssetPayload): Promise<Asset> =>
  request('/assets', { method: 'POST', body: JSON.stringify(data) })

export const updateAsset = (id: string, data: Partial<Asset>): Promise<Asset> =>
  request(`/assets/${id}`, { method: 'PATCH', body: JSON.stringify(data) })

// ── Documents ─────────────────────────────────────────────────────────────────

export const getDocuments = (assetId: string): Promise<Document[]> =>
  request(`/assets/${assetId}/documents`)

export const getDocument = (docId: string): Promise<Document> =>
  request(`/documents/${docId}`)

export const uploadDocument = (
  assetId: string,
  formData: FormData,
): Promise<Document> =>
  request(`/assets/${assetId}/documents`, { method: 'POST', body: formData })

export const getDocumentVersions = (docId: string): Promise<DocumentVersion[]> =>
  request(`/documents/${encodeURIComponent(docId)}/versions`)

// Text of exactly one version (Stage 9B-4, step 8). The backend never falls back to another
// version; versionId, when given, must identify that same version (otherwise 409).
export const getDocumentVersionText = (
  docId: string,
  versionNumber: number,
  versionId?: string,
): Promise<DocumentVersionText> => {
  const query = versionId ? `?version_id=${encodeURIComponent(versionId)}` : ''
  return request(`/documents/${encodeURIComponent(docId)}/versions/${versionNumber}/text${query}`)
}

// ── Extractions ───────────────────────────────────────────────────────────────

export const triggerExtraction = (docId: string): Promise<LegalExtraction> =>
  request(`/documents/${docId}/extractions`, { method: 'POST' })

export const getExtractions = (docId: string): Promise<LegalExtraction[]> =>
  request(`/documents/${docId}/extractions`)

export const getExtraction = (extractionId: string): Promise<LegalExtraction> =>
  request(`/extractions/${extractionId}`)

// ── Review workflow ───────────────────────────────────────────────────────────

export const startReview = (
  extractionId: string,
  token: string,
): Promise<ReviewStatus> =>
  request(`/extractions/${extractionId}/review`, { method: 'POST' }, {
    Authorization: `Bearer ${token}`,
  })

export const correctClaim = (
  extractionId: string,
  claimId: string,
  data: ClaimCorrectionPayload,
  token: string,
): Promise<ReviewCorrection> =>
  request(
    `/extractions/${extractionId}/claims/${claimId}`,
    { method: 'PATCH', body: JSON.stringify(data) },
    { Authorization: `Bearer ${token}` },
  )

export const approveExtraction = (
  extractionId: string,
  data: ApprovalPayload,
  token: string,
): Promise<ReviewApproval> =>
  request(
    `/extractions/${extractionId}/approvals`,
    { method: 'POST', body: JSON.stringify(data) },
    { Authorization: `Bearer ${token}` },
  )

export const getCorrections = (
  extractionId: string,
  token: string,
): Promise<ReviewCorrection[]> =>
  request(`/extractions/${extractionId}/corrections`, {}, {
    Authorization: `Bearer ${token}`,
  })

export const getApprovals = (
  extractionId: string,
  token: string,
): Promise<ReviewApproval[]> =>
  request(`/extractions/${extractionId}/approvals`, {}, {
    Authorization: `Bearer ${token}`,
  })

// ── Blockchain ────────────────────────────────────────────────────────────────

export const getBlockchainStatus = (assetId: string): Promise<BlockchainStatus> =>
  request(`/assets/${assetId}/blockchain-status`)

// ── Legal Passport ────────────────────────────────────────────────────────────

export const getLegalPassport = (
  network: string,
  assetCode: string,
  issuerKey: string,
  apiKey: string,
): Promise<LegalPassport> =>
  request(`/passport/${network}/${assetCode}/${issuerKey}`, {}, {
    'X-API-Key': apiKey,
  }, 'none')
