// Shared fixtures for the review page tests (Stage 9B-4, step 9). Shapes follow the backend
// schemas. 'fictional-reviewer-token' is a FICTIONAL, NON-SECRET value answered by MSW.
import { render } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_EXTRACTION, MOCK_VERSION, mockEvidence, mockVersionText } from './mocks/handlers'
import { ExtractionDetail } from '../pages/ExtractionDetail'
import { AuthProvider } from '../AuthContext'
import type {
  DocumentVersion,
  LegalClaim,
  LegalExtraction,
  ReviewApproval,
  ReviewCorrection,
} from '../types'

export const BASE = 'http://localhost:8000'

export const V1: DocumentVersion = MOCK_VERSION
export const V2: DocumentVersion = {
  ...MOCK_VERSION,
  id: 'ver-002',
  version_number: 2,
  original_filename: 'prospectus_v2.pdf',
  sha256_hash: 'e'.repeat(64),
  created_at: '2024-02-01T00:00:00Z',
}
export const V1_PAGES = [
  'Version one, page one: each token represents a fractional ownership interest in the property.',
  'Version one, page two: the Trustee holds fee simple title to the Property.',
]
export const V2_PAGES = ['Version two, page one: amended terms apply to every holder of the token.']

export const KNOWN: LegalClaim = {
  id: 'claim-001',
  extraction_id: 'ext-001',
  field_name: 'ownership_nature',
  value: 'Fee simple title',
  is_unknown: false,
  source_page: 2,
  supporting_passage: 'the Trustee holds fee simple title to the Property',
  created_at: '2024-01-02T00:00:00Z',
  evidence: mockEvidence({ cited_page: 2, found_on_pages: [2] }),
}
export const UNKNOWN: LegalClaim = {
  id: 'claim-002',
  extraction_id: 'ext-001',
  field_name: 'transfer_restrictions',
  value: 'unknown',
  is_unknown: true,
  source_page: null,
  supporting_passage: null,
  created_at: '2024-01-02T00:00:00Z',
  evidence: mockEvidence({ status: 'not_applicable', cited_page: null, found_on_pages: [] }),
}

export function reviewExtraction(overrides: Partial<LegalExtraction> = {}): LegalExtraction {
  return {
    ...MOCK_EXTRACTION,
    status: 'under_review',
    claims: [KNOWN, UNKNOWN],
    approval_state: { state: 'not_approved', version_is_current: true, current_approval_types: [], reasons: [] },
    ...overrides,
  }
}

export function correctionRecord(overrides: Partial<ReviewCorrection> = {}): ReviewCorrection {
  return {
    id: 'corr-001',
    extraction_id: 'ext-001',
    claim_id: KNOWN.id,
    reviewer_id: 'legal-reviewer-01',
    reviewer_role: 'independent_legal_reviewer',
    original_value: 'Fee simple title',
    original_is_unknown: false,
    original_passage: KNOWN.supporting_passage,
    corrected_value: 'Leasehold interest',
    corrected_is_unknown: false,
    corrected_passage: 'each token represents a fractional ownership interest',
    correction_note: null,
    created_at: '2024-01-03T00:00:00Z',
    original_source_page: 2,
    corrected_source_page: 1,
    page_history_recorded: true,
    ...overrides,
  }
}

// Serves one extraction (or a sequence: the last one repeats), its document's versions and texts
export function serveReview({
  extractions,
  versions = [V1, V2],
  texts = { [V1.id]: V1_PAGES, [V2.id]: V2_PAGES },
  approvals = [],
  corrections = [],
}: {
  extractions: LegalExtraction | LegalExtraction[]
  versions?: DocumentVersion[]
  texts?: Record<string, string[]>
  approvals?: ReviewApproval[]
  corrections?: ReviewCorrection[]
}) {
  const sequence = Array.isArray(extractions) ? extractions : [extractions]
  const calls = { extraction: 0, text: [] as { n: string; versionId: string | null }[] }
  const latest = Math.max(...versions.map(v => v.version_number))
  server.use(
    http.get(`${BASE}/extractions/:extractionId`, () => {
      const ex = sequence[Math.min(calls.extraction, sequence.length - 1)]
      calls.extraction += 1
      return HttpResponse.json(ex)
    }),
    http.get(`${BASE}/extractions/:id/approvals`, () => HttpResponse.json(approvals)),
    http.get(`${BASE}/extractions/:id/corrections`, () => HttpResponse.json(corrections)),
    http.get(`${BASE}/documents/:docId/versions`, () => HttpResponse.json(versions)),
    http.get(`${BASE}/documents/:docId/versions/:n/text`, ({ params, request }) => {
      const versionId = new URL(request.url).searchParams.get('version_id')
      calls.text.push({ n: String(params.n), versionId })
      const version = versions.find(v => String(v.version_number) === params.n)
      if (!version) return HttpResponse.json({ detail: 'Document version not found.' }, { status: 404 })
      if (versionId !== null && versionId !== version.id) {
        return HttpResponse.json({ detail: 'Version reference mismatch.' }, { status: 409 })
      }
      return HttpResponse.json(mockVersionText(version, texts[version.id] ?? [], latest))
    }),
  )
  return calls
}

export function setReviewerSession() {
  sessionStorage.setItem(
    'rwa_passport_session_auth',
    JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }),
  )
}

export function renderReview(path = '/assets/asset-001/documents/doc-001/extractions/ext-001', extra?: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        {extra}
        <Routes>
          <Route path="assets/:id/documents/:docId/extractions/:extractionId" element={<ExtractionDetail />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

export function deferred() {
  let release: () => void = () => {}
  const gate = new Promise<void>(resolve => { release = resolve })
  return { gate, release }
}
