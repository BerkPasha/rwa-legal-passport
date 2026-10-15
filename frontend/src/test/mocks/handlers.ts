import { http, HttpResponse } from 'msw'
import type { LegalPassport, ReviewApproval } from '../../types'

const BASE = 'http://localhost:8000'

export const MOCK_ASSET = {
  id: 'asset-001',
  blockchain_network: 'stellar-testnet',
  stellar_asset_code: 'MRPROP',
  stellar_issuer_public_key: 'GABC1234567890',
  issuer_organization_name: 'Meridian Property Holdings LLC',
  asset_type: 'real_estate',
  jurisdiction: 'US-NY',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
}

export const MOCK_DOCUMENT = {
  id: 'doc-001',
  asset_id: 'asset-001',
  document_type: 'prospectus',
  status: 'active',
  created_at: '2024-01-01T00:00:00Z',
  updated_at: '2024-01-01T00:00:00Z',
  current_version: {
    id: 'ver-001',
    document_id: 'doc-001',
    version_number: 1,
    filename: 'prospectus_v1.pdf',
    file_size: 102400,
    mime_type: 'application/pdf',
    uploaded_at: '2024-01-01T00:00:00Z',
  },
}

export const MOCK_EXTRACTION = {
  id: 'ext-001',
  document_version_id: 'ver-001',
  status: 'approved',
  ai_model_used: 'claude-3-5-sonnet',
  extracted_at: '2024-01-02T00:00:00Z',
  claims: [
    {
      id: 'claim-001',
      extraction_id: 'ext-001',
      field_name: 'ownership_nature',
      value: 'Direct fractional ownership of underlying real estate',
      is_unknown: false,
      supporting_passage: 'Each token represents a fractional ownership interest in the property.',
      confidence_score: 0.95,
    },
    {
      id: 'claim-002',
      extraction_id: 'ext-001',
      field_name: 'transfer_restrictions',
      value: 'Unknown',
      is_unknown: true,
      supporting_passage: null,
      confidence_score: 0.2,
    },
  ],
}

// Same shape as the backend's LegalPassportResponse (GET /passport/...).
export const MOCK_PASSPORT: LegalPassport = {
  asset: {
    asset_id: 'asset-001',
    asset_code: 'MRPROP',
    issuer_key: 'GABC1234567890',
    network: 'stellar-mainnet',
    issuer_organization: 'Meridian Property Holdings LLC',
    asset_type: 'real_estate',
    jurisdiction: 'US-NY',
  },
  legal_rights: [
    {
      field_name: 'ownership_nature',
      value: 'Direct fractional ownership of underlying real estate',
      is_unknown: false,
      verified: true,
      source_page: 1,
      supporting_passage: 'Each token represents a fractional ownership interest in the property.',
    },
  ],
  approvals: [
    {
      approval_type: 'issuer_attestation',
      reviewer_role: 'issuer_reviewer',
      approved_at: '2024-01-03T00:00:00Z',
      notes: null,
    },
  ],
  source_document: {
    document_id: 'doc-001',
    title: 'Q4 2024 Prospectus',
    document_type: 'prospectus',
    version_number: 1,
  },
  extraction_metadata: {
    extraction_id: 'ext-001',
    ai_model: 'claude-haiku-4-5-20251001',
    extraction_timestamp: '2024-01-02T00:00:00Z',
    passport_version: 1,
    effective_date: '2024-01-03T00:00:00Z',
  },
  disclaimer: 'INSTITUTIONAL USE ONLY - NOT LEGAL ADVICE',
  retrieved_at: '2024-01-04T00:00:00Z',
}

// Same shape as the backend's approval record (POST/GET /extractions/{id}/approvals).
export const MOCK_APPROVAL: ReviewApproval = {
  id: 'appr-001',
  extraction_id: 'ext-001',
  reviewer_id: 'alice-chen-01',
  reviewer_role: 'issuer_reviewer',
  approval_type: 'issuer_attestation',
  disclaimer: 'INTERNAL REVIEW ONLY - NOT LEGAL ADVICE',
  notes: null,
  approved_at: '2024-01-03T00:00:00Z',
  created_at: '2024-01-03T00:00:00Z',
}

export const handlers = [
  // Assets
  http.get(`${BASE}/assets`, () => {
    return HttpResponse.json([MOCK_ASSET])
  }),

  http.get(`${BASE}/assets/:id`, ({ params }) => {
    if (params.id === 'asset-001') {
      return HttpResponse.json(MOCK_ASSET)
    }
    return HttpResponse.json({ detail: 'Not found' }, { status: 404 })
  }),

  http.post(`${BASE}/assets`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>
    return HttpResponse.json({ ...MOCK_ASSET, ...body, id: 'asset-new' }, { status: 201 })
  }),

  // Documents
  http.get(`${BASE}/assets/:id/documents`, () => {
    return HttpResponse.json([MOCK_DOCUMENT])
  }),

  http.post(`${BASE}/assets/:id/documents`, () => {
    return HttpResponse.json(MOCK_DOCUMENT, { status: 201 })
  }),

  // Extractions
  http.get(`${BASE}/documents/:docId/extractions`, () => {
    return HttpResponse.json([MOCK_EXTRACTION])
  }),

  http.post(`${BASE}/documents/:docId/extractions`, () => {
    return HttpResponse.json(MOCK_EXTRACTION, { status: 201 })
  }),

  http.get(`${BASE}/extractions/:extractionId`, () => {
    return HttpResponse.json(MOCK_EXTRACTION)
  }),

  // Review
  http.post(`${BASE}/extractions/:id/review`, () => {
    return HttpResponse.json({ ...MOCK_EXTRACTION, status: 'under_review' })
  }),

  http.patch(`${BASE}/extractions/:id/claims/:claimId`, async ({ request }) => {
    const body = await request.json() as Record<string, unknown>
    return HttpResponse.json({ id: 'corr-001', ...body })
  }),

  http.post(`${BASE}/extractions/:id/approvals`, () => {
    return HttpResponse.json(MOCK_APPROVAL, { status: 201 })
  }),

  http.get(`${BASE}/extractions/:id/corrections`, () => {
    return HttpResponse.json([])
  }),

  http.get(`${BASE}/extractions/:id/approvals`, () => {
    return HttpResponse.json([MOCK_APPROVAL])
  }),

  // Blockchain
  http.get(`${BASE}/assets/:id/blockchain-status`, () => {
    return HttpResponse.json({
      asset_id: 'asset-001',
      found_on_network: true,
      network: 'stellar-testnet',
      horizon_url: 'https://horizon-testnet.stellar.org',
      asset_code: 'MRPROP',
      issuer_public_key: 'GABC1234567890',
      checked_at: '2024-01-01T00:00:00Z',
    })
  }),

  // Passport
  http.get(`${BASE}/passport/:network/:assetCode/:issuerKey`, ({ request }) => {
    const key = request.headers.get('X-API-Key')
    if (!key) {
      return HttpResponse.json({ detail: 'API key required' }, { status: 403 })
    }
    return HttpResponse.json(MOCK_PASSPORT)
  }),
]
