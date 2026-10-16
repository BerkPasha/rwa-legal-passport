import { describe, it, expect, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_APPROVAL, mockEvidence } from './mocks/handlers'
import { ExtractionDetail } from '../pages/ExtractionDetail'
import { AuthProvider } from '../AuthContext'
import type { LegalExtraction, ReviewApproval } from '../types'

// 'fictional-reviewer-token' is a FICTIONAL, NON-SECRET fixture answered by the MSW mock server.
const BASE = 'http://localhost:8000'

function extraction(overrides: Partial<LegalExtraction>): LegalExtraction {
  return {
    id: 'ext-001',
    document_id: 'doc-001',
    version_id: 'ver-001',
    status: 'approved',
    ai_model: 'mock-v1',
    disclaimer: 'Unverified AI draft.',
    extraction_timestamp: '2024-01-02T00:00:00Z',
    created_at: '2024-01-02T00:00:00Z',
    claims: [
      {
        id: 'claim-001',
        extraction_id: 'ext-001',
        field_name: 'ownership_nature',
        value: 'Fee simple',
        is_unknown: false,
        source_page: 1,
        supporting_passage: 'Fee simple ownership.',
        created_at: '2024-01-02T00:00:00Z',
        evidence: mockEvidence(),
      },
    ],
    approval_state: {
      state: 'current',
      version_is_current: true,
      current_approval_types: ['issuer_attestation'],
      reasons: [],
    },
    ...overrides,
  }
}

function serve(ex: LegalExtraction, approvals: ReviewApproval[] = [MOCK_APPROVAL]) {
  server.use(
    http.get(`${BASE}/extractions/:extractionId`, () => HttpResponse.json(ex)),
    http.get(`${BASE}/extractions/:id/approvals`, () => HttpResponse.json(approvals)),
  )
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={['/assets/asset-001/documents/doc-001/extractions/ext-001']}>
      <AuthProvider>
        <Routes>
          <Route path="assets/:id/documents/:docId/extractions/:extractionId" element={<ExtractionDetail />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('ExtractionDetail approval currency', () => {
  beforeEach(() => {
    sessionStorage.setItem(
      'rwa_passport_session_auth',
      JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }),
    )
  })

  it('shows a current approval as approved', async () => {
    serve(extraction({}))
    renderPage()
    expect(await screen.findByText(/This extraction has been approved/)).toBeInTheDocument()
    expect(screen.getByTestId('approval-state-badge')).toHaveTextContent('Approval current')
    expect(screen.queryByTestId('stale-approval-notice')).not.toBeInTheDocument()
    expect(await screen.findByTestId('approval-currency')).toHaveTextContent('Current')
  })

  it('never presents a stale approval as approved and offers a re-review', async () => {
    const staleApproval: ReviewApproval = {
      ...MOCK_APPROVAL,
      currency: { is_current: false, reasons: ['asset_information_changed'] },
    }
    serve(
      extraction({
        approval_state: {
          state: 'stale',
          version_is_current: true,
          current_approval_types: [],
          reasons: ['asset_information_changed'],
        },
      }),
      [staleApproval],
    )
    let reviewStarted = false
    server.use(
      http.post(`${BASE}/extractions/:id/review`, () => {
        reviewStarted = true
        return HttpResponse.json({ extraction_id: 'ext-001', status: 'under_review' })
      }),
    )
    renderPage()

    const notice = await screen.findByTestId('stale-approval-notice')
    expect(notice).toHaveTextContent('This approval is no longer current')
    expect(notice).toHaveTextContent('Asset information changed after approval')
    expect(screen.queryByText(/This extraction has been approved/)).not.toBeInTheDocument()
    expect(screen.getByTestId('approval-state-badge')).toHaveTextContent('Approval stale')
    expect(await screen.findByTestId('approval-currency')).toHaveTextContent('Not current')

    await userEvent.click(screen.getByRole('button', { name: /start re-review/i }))
    await waitFor(() => expect(reviewStarted).toBe(true))
  })

  it('does not offer approval for an extraction of a superseded document version', async () => {
    serve(
      extraction({
        status: 'under_review',
        approval_state: {
          state: 'not_approved',
          version_is_current: false,
          current_approval_types: [],
          reasons: [],
        },
      }),
      [],
    )
    renderPage()
    expect(await screen.findByTestId('superseded-notice')).toHaveTextContent('A newer version of this document exists')
    expect(screen.queryByText('Approve This Extraction')).not.toBeInTheDocument()
    expect(screen.queryByText(/This extraction has been approved/)).not.toBeInTheDocument()
  })
})
