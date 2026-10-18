import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import {
  MOCK_CURRENT_USER,
  MOCK_DOCUMENT,
  MOCK_EXTRACTION,
  MOCK_PASSPORT,
  MOCK_STELLAR_ANCHOR,
  MOCK_VERSION,
  mockEvidence,
  mockVersionText,
} from './mocks/handlers'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'
import { extractionOrigin } from '../aiProcessing'
import type { LegalExtraction, LegalPassport } from '../types'

// Stage 10B-3: manual legal claims in the interface. All responses come from MSW; the server's
// checks themselves are tested in tests/test_manual_claims.py.
const BASE = 'http://localhost:8000'
const SESSION_KEY = 'rwa_passport_session_auth'
const PAGES = [
  'Page one: the Notes are senior unsecured obligations of the Issuer.',
  '',
  'Page three: this Memorandum and the Notes are governed by the laws of Ireland.',
]
const QUOTE = 'this Memorandum and the Notes are governed by the laws of Ireland'
const DOC = {
  ...MOCK_DOCUMENT,
  current_version: {
    ...MOCK_VERSION,
    text_layer: { status: 'partial' as const, page_count: 3, pages_with_text: 2, pages_without_text: [2],
                  requires_ocr: true, notice: 'Page 2 has no text layer.' },
  },
}
const MANUAL: LegalExtraction = {
  ...MOCK_EXTRACTION,
  id: 'ext-manual',
  status: 'draft',
  ai_model: 'manual-entry (no AI model)',
  source: 'manual',
  disclaimer: 'MANUALLY ENTERED DRAFT — typed by a user; no AI model was used.',
  approval_state: { state: 'not_approved', version_is_current: true, current_approval_types: [], reasons: [] },
  claims: [{
    id: 'claim-manual', extraction_id: 'ext-manual', field_name: 'jurisdiction', value: 'Irish law governs the Notes.',
    is_unknown: false, source_page: 3, supporting_passage: QUOTE, created_at: '2024-01-02T00:00:00Z',
    evidence: mockEvidence({ cited_page: 3, found_on_pages: [3], page_count: 3 }),
  }],
}

function signIn(user = MOCK_CURRENT_USER) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }))
  server.use(http.get(`${BASE}/auth/me`, () => HttpResponse.json(user)))
}

function serveDocument() {
  server.use(
    http.get(`${BASE}/assets/:id/documents`, () => HttpResponse.json([DOC])),
    http.get(`${BASE}/documents/:docId/versions/:n/text`, () => HttpResponse.json(mockVersionText(MOCK_VERSION, PAGES))),
    http.get(`${BASE}/documents/:docId/extractions`, () => HttpResponse.json([])),
    http.get(`${BASE}/extractions/:extractionId`, () => HttpResponse.json(MANUAL)),
    http.get(`${BASE}/extractions/:id/approvals`, () => HttpResponse.json([])),
    http.get(`${BASE}/extractions/:id/corrections`, () => HttpResponse.json([])),
  )
}

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

async function openForm() {
  renderApp('/assets/asset-001')
  await userEvent.click(await screen.findByTestId('add-manual-claim'))
  const form = screen.getByTestId('manual-claim-form')
  await userEvent.selectOptions(within(form).getByLabelText('Legal field'), 'jurisdiction')
  await waitFor(() => expect(within(form).getByLabelText('Source page')).toBeEnabled())
  await userEvent.selectOptions(within(form).getByLabelText('Source page'), '3')
  await userEvent.type(within(form).getByLabelText('Claim'), 'Irish law governs the Notes.')
  await userEvent.type(within(form).getByLabelText('Exact quotation from the selected page'), QUOTE)
  return form
}

beforeEach(() => serveDocument())
afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
  vi.restoreAllMocks()
})

describe('Manual claim creation', () => {
  it('shows the stored page text, sends exactly what was entered and opens the new draft', async () => {
    signIn()
    let posted: unknown = null
    server.use(http.post(`${BASE}/documents/:docId/manual-extractions`, async ({ request }) => {
      posted = await request.json()
      return HttpResponse.json(MANUAL, { status: 201 })
    }))
    const form = await openForm()
    // The page without a text layer cannot be chosen; the chosen page's stored text is shown
    expect(within(form).getByRole('option', { name: 'Page 2 — no text layer, cannot be quoted' })).toBeDisabled()
    expect(within(form).getByTestId('manual-claim-page-text')).toHaveTextContent(PAGES[2])
    expect(within(form).getByTestId('manual-claim-note')).toHaveTextContent('unapproved draft')
    await userEvent.click(within(form).getByRole('button', { name: 'Create Draft Claim' }))

    expect(await screen.findByRole('heading', { name: 'Manually Entered Legal Claims' })).toBeInTheDocument()
    expect(posted).toEqual({
      asset_id: 'asset-001',
      version_id: MOCK_VERSION.id,
      claims: [{ field_name: 'jurisdiction', value: 'Irish law governs the Notes.', source_page: 3, supporting_passage: QUOTE }],
    })
    expect(screen.getByTestId('manual-claim-created')).toHaveTextContent('Nothing is approved')
    expect(screen.getByTestId('extraction-origin')).toHaveTextContent('Manual entry · no AI')
    expect(screen.getByTestId('extraction-origin')).toHaveAttribute('data-origin', 'manual')
    expect(screen.getByTestId('extraction-status-badge')).toHaveTextContent('Draft')
    expect(screen.getAllByTestId('disclaimer-banner')[0]).toHaveTextContent('MANUALLY ENTERED DRAFT')
    expect(screen.queryByText(/^Model:/)).not.toBeInTheDocument()
  })

  it('shows why the server refused an unverified quotation, and stays on the form', async () => {
    signIn()
    server.use(http.post(`${BASE}/documents/:docId/manual-extractions`, () => HttpResponse.json({
      detail: 'The quotation is not verified on its cited page of this document version (jurisdiction).',
      manual_claim_refusal: 'evidence_not_verified', fields: ['jurisdiction'],
      evidence_failures: [{ field_name: 'jurisdiction', status: 'found_on_other_page',
                            explanation: 'The quotation does not appear on the cited page 1; it appears on page 3.' }],
    }, { status: 422 })))
    const form = await openForm()
    await userEvent.click(within(form).getByRole('button', { name: 'Create Draft Claim' }))
    const error = await within(form).findByTestId('manual-claim-error')
    expect(error).toHaveTextContent('Jurisdiction: quotation is not on the cited page.')
    expect(error).toHaveTextContent('it appears on page 3')
    expect(error).toHaveTextContent('Nothing was stored.')
    expect(screen.queryByRole('heading', { name: 'Manually Entered Legal Claims' })).not.toBeInTheDocument()
    // What was typed is kept, unchanged, for the user to correct
    expect(within(form).getByLabelText('Exact quotation from the selected page')).toHaveValue(QUOTE)
  })

  it('does not repeat server internals when the server fails', async () => {
    signIn()
    server.use(http.post(`${BASE}/documents/:docId/manual-extractions`, () =>
      HttpResponse.json({ detail: 'Traceback: internal database path /srv/secret.db' }, { status: 500 })))
    const form = await openForm()
    await userEvent.click(within(form).getByRole('button', { name: 'Create Draft Claim' }))
    const error = await within(form).findByTestId('manual-claim-error')
    expect(error).toHaveTextContent('the server could not process the request. Nothing was stored')
    expect(error).not.toHaveTextContent('Traceback')
  })

  it('reports an unreachable server', async () => {
    signIn()
    server.use(http.post(`${BASE}/documents/:docId/manual-extractions`, () => HttpResponse.error()))
    const form = await openForm()
    await userEvent.click(within(form).getByRole('button', { name: 'Create Draft Claim' }))
    expect(await within(form).findByTestId('manual-claim-error')).toHaveTextContent('the server could not be reached')
  })

  it('is not offered to a role that cannot create claims', async () => {
    signIn({ ...MOCK_CURRENT_USER, role: 'independent_legal_reviewer',
             permissions: ['asset:read', 'document:read', 'extraction:read', 'review:start', 'claim:correct',
                           'approval:create', 'audit:read'] })
    renderApp('/assets/asset-001')
    expect(await screen.findByText(DOC.title)).toBeInTheDocument()
    await screen.findByText(/Signed in as/)
    expect(screen.queryByTestId('add-manual-claim')).not.toBeInTheDocument()
  })

  it('labels a manual record as such, wherever it is listed', () => {
    expect(extractionOrigin('manual-entry (no AI model)', null, 'manual')).toEqual({ kind: 'manual', label: 'Manual entry · no AI' })
    // The label follows the server's source field, never the model name alone
    expect(extractionOrigin('manual-entry (no AI model)', ['claude-opus-5-5'], 'ai_service').kind).toBe('not_ai')
  })
})

describe('Anchor actions by role', () => {
  it('a reviewer without anchor:create can read the pending record but is offered no transaction action', async () => {
    signIn({ ...MOCK_CURRENT_USER, role: 'independent_legal_reviewer',
             permissions: ['asset:read', 'document:read', 'extraction:read', 'audit:read'] })
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors`, () =>
      HttpResponse.json([{ ...MOCK_STELLAR_ANCHOR, status: 'pending', ledger: null, ledger_closed_at: null }])))
    renderApp('/assets/asset-001')
    await screen.findByText(/Signed in as/)
    await userEvent.click(await screen.findByRole('button', { name: 'Stellar Evidence Anchor' }))
    expect(await screen.findByTestId('anchor-read-only-note')).toBeInTheDocument()
    expect(screen.getByTestId('anchor-pending-note')).toHaveTextContent('not confirmed in a Stellar ledger')
    expect(screen.queryByRole('button', { name: 'Refresh pending transaction' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Anchor current evidence/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-check on Stellar' })).toBeInTheDocument()
  })

  it('the issuer reviewer is offered the refresh of a pending record', async () => {
    signIn()
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors`, () =>
      HttpResponse.json([{ ...MOCK_STELLAR_ANCHOR, status: 'pending', ledger: null, ledger_closed_at: null }])))
    renderApp('/assets/asset-001')
    await screen.findByText(/Signed in as/)
    await userEvent.click(await screen.findByRole('button', { name: 'Stellar Evidence Anchor' }))
    expect(await screen.findByRole('button', { name: 'Refresh pending transaction' })).toBeInTheDocument()
    expect(screen.queryByTestId('anchor-read-only-note')).not.toBeInTheDocument()
  })
})

describe('Manual values on the Legal Passport', () => {
  it('names the manual origin and who attested each value', async () => {
    const passport: LegalPassport = {
      ...MOCK_PASSPORT,
      // Two manual values: one also attested by an independent legal reviewer, one by the issuer only
      legal_rights: [
        { ...MOCK_PASSPORT.legal_rights[0], value_origin: 'manual_entry',
          reviewer_attestation_status: 'independent_reviewer_attested', independently_verified: true },
        { ...MOCK_PASSPORT.legal_rights[0], field_name: 'jurisdiction', value: 'Irish law.', value_origin: 'manual_entry',
          reviewer_attestation_status: 'issuer_attested', independently_verified: false },
      ],
      extraction_metadata: { ...MOCK_PASSPORT.extraction_metadata, ai_model: 'manual-entry (no AI model)', extraction_source: 'manual' },
    }
    server.use(http.get(`${BASE}/passport/:network/:assetCode/:issuerKey`, () => HttpResponse.json(passport)))
    renderApp('/passport?network=stellar-testnet&asset_code=MRPROP&issuer_key=GABC1234567890')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    const card = await screen.findByTestId('passport-card')
    const known = passport.legal_rights.filter(r => !r.is_unknown && !r.conflict).length
    expect(within(card).getAllByTestId('value-origin')).toHaveLength(known)
    expect(within(card).getAllByTestId('value-origin')[0]).toHaveTextContent('Entered manually by a user of the issuer from the cited page (no AI)')
    const attestations = within(card).getAllByTestId('passport-attestation')
    expect(attestations[0]).toHaveTextContent('attested by an independent legal reviewer')
    expect(attestations[1]).toHaveTextContent('Issuer attestation only — not reviewed by an independent legal reviewer')
    expect(within(card).getByTestId('passport-extraction-source')).toHaveTextContent('Manual entry (no AI model)')
    expect(within(card).queryByText('Model Used')).not.toBeInTheDocument()
  })
})
