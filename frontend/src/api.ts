import type {
  Asset,
  ApprovalPayload,
  BlockchainStatus,
  ClaimCorrectionPayload,
  CreateAssetPayload,
  Document,
  DocumentVersion,
  LegalExtraction,
  LegalPassport,
  ReviewApproval,
  ReviewCorrection,
  ReviewStatus,
} from './types'

export const API_BASE = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

// ── Error handling ────────────────────────────────────────────────────────────

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(
  path: string,
  init: RequestInit = {},
  extraHeaders: Record<string, string> = {},
): Promise<T> {
  const isFormData = init.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...extraHeaders,
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { ...headers, ...(init.headers as Record<string, string> | undefined) },
  })

  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? detail
    } catch { /* ignore */ }
    throw new ApiError(res.status, detail)
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
  request(`/documents/${docId}/versions`)

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
  })
