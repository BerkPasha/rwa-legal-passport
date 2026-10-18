import type {
  AIStatus,
  AnchorVerification,
  Asset,
  CurrentUser,
  ApprovalPayload,
  BlockchainStatus,
  ClaimCorrectionPayload,
  CreateAssetPayload,
  Document,
  DocumentVersion,
  DocumentVersionText,
  ExtractionConfirmation,
  LegalExtraction,
  LegalPassport,
  ManualExtractionPayload,
  ReviewApproval,
  ReviewCorrection,
  ReviewStatus,
  ServerInfo,
  StellarAnchor,
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

  if (!res.ok) throw await apiError(res)

  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

async function apiError(res: Response): Promise<ApiError> {
  let detail = res.statusText
  let body: unknown = null
  try {
    body = await res.json()
    detail = detailMessage((body as { detail?: unknown } | null)?.detail, detail)
  } catch { /* ignore */ }
  return new ApiError(res.status, detail, body)
}

// ── Identity and AI availability (Stage 10B-2) ───────────────────────────────

// Who the Bearer credential belongs to; the response never contains the credential
export const getMe = (): Promise<CurrentUser> => request('/auth/me')

// Public: whether the server is a read-only showcase of synthetic data (Stage 10C-1); no credential is sent
export const getServerInfo = (): Promise<ServerInfo> => request('/health', {}, {}, 'none')

export const getAiStatus = (): Promise<AIStatus> => request('/ai/status')

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

// The current version's PDF (GET /documents/{id}/download): the server re-verifies its SHA-256 and
// enforces the caller's issuer scope. The bytes stay in memory; the caller compares them with the
// version's recorded hash before offering them to the user (Stage 10B-2).
export async function downloadDocument(docId: string): Promise<Blob> {
  const res = await fetch(`${API_BASE}/documents/${encodeURIComponent(docId)}/download`, {
    headers: userToken ? { Authorization: `Bearer ${userToken}` } : {},
  })
  if (!res.ok) throw await apiError(res)
  return res.blob()
}

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

// With an external AI provider the first call answers 428 with what would be sent; the user's
// confirmation is then sent with the second call (Stage 10A-2)
export const triggerExtraction = (docId: string, confirmation?: ExtractionConfirmation): Promise<LegalExtraction> =>
  request(`/documents/${docId}/extractions`, {
    method: 'POST',
    ...(confirmation ? { body: JSON.stringify(confirmation) } : {}),
  })

// Manual legal claims (Stage 10B-3): no AI is involved; the result is an unapproved draft
export const createManualExtraction = (docId: string, payload: ManualExtractionPayload): Promise<LegalExtraction> =>
  request(`/documents/${encodeURIComponent(docId)}/manual-extractions`, { method: 'POST', body: JSON.stringify(payload) })

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

// ── Stellar Testnet evidence anchoring (STELLAR_ANCHORING.md) ─────────────────
// The server signs with its own Testnet account; no signing credential ever reaches the browser.

export const getAnchorVerification = (assetId: string): Promise<AnchorVerification> =>
  request(`/assets/${encodeURIComponent(assetId)}/stellar-anchors/verification`)

export const getStellarAnchors = (assetId: string): Promise<StellarAnchor[]> =>
  request(`/assets/${encodeURIComponent(assetId)}/stellar-anchors`)

// Submits a Stellar Testnet transaction (unless this evidence is already anchored)
export const createStellarAnchor = (assetId: string): Promise<StellarAnchor> =>
  request(`/assets/${encodeURIComponent(assetId)}/stellar-anchors`, { method: 'POST' })

export const refreshStellarAnchor = (assetId: string, anchorId: string): Promise<StellarAnchor> =>
  request(`/assets/${encodeURIComponent(assetId)}/stellar-anchors/${encodeURIComponent(anchorId)}/refresh`,
    { method: 'POST' })

export const getPassportAnchorVerification = (
  network: string,
  assetCode: string,
  issuerKey: string,
  apiKey: string,
): Promise<AnchorVerification> =>
  request(`/passport/${network}/${assetCode}/${issuerKey}/stellar-anchor`, {}, {
    'X-API-Key': apiKey,
  }, 'none')
