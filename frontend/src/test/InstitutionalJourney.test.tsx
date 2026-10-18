import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_AI_STATUS, MOCK_ASSET, MOCK_CURRENT_USER, MOCK_DOCUMENT, MOCK_EXTRACTION, MOCK_VERSION } from './mocks/handlers'
import { BASE, renderReview, reviewExtraction, serveReview, setReviewerSession } from './reviewFixtures'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { pageTitle } from '../components/Layout'
import { AssetDetail } from '../pages/AssetDetail'
import { setUserToken } from '../api'

// Stage 10B-2: the institutional user journey's UI fixes. All responses come from MSW.
const SESSION_KEY = 'rwa_passport_session_auth'

afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
  vi.restoreAllMocks()
})

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

function renderAsset() {
  return render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <AuthProvider><Routes><Route path="assets/:id" element={<AssetDetail />} /></Routes></AuthProvider>
    </MemoryRouter>,
  )
}

async function sha256(bytes: Uint8Array): Promise<string> {
  const digest = await crypto.subtle.digest('SHA-256', bytes.buffer as ArrayBuffer)
  return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, '0')).join('')
}

describe('Unknown addresses and page titles', () => {
  it('an unknown address shows a page-not-found message with a way back', async () => {
    renderApp('/no-such-page')
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument()
    expect(screen.getByText('/no-such-page')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to the dashboard' })).toHaveAttribute('href', '/')
    expect(screen.getByRole('link', { name: 'Open the asset registry' })).toHaveAttribute('href', '/assets')
    expect(screen.getByRole('heading', { level: 1, name: 'Page Not Found' })).toBeInTheDocument()
    expect(screen.getByRole('navigation')).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe('Page Not Found · RWA Legal Passport'))
  })

  it('every page has the title of what it shows', () => {
    expect(pageTitle('/')).toBe('Dashboard')
    expect(pageTitle('/assets')).toBe('Asset Registry')
    expect(pageTitle('/assets/')).toBe('Asset Registry')
    expect(pageTitle('/assets/a1')).toBe('Asset Detail')
    expect(pageTitle('/assets/a1/documents/d1/extractions/e1')).toBe('Extraction Detail')
    expect(pageTitle('/passport')).toBe('Legal Passport')
    expect(pageTitle('/api-docs')).toBe('API Reference')
    expect(pageTitle('/assets/a1/documents')).toBe('Page Not Found')
  })
})

describe('Authorization state', () => {
  it('labels the credential fields and shows whom the server says the credential belongs to', async () => {
    renderApp('/api-docs')
    expect(screen.getByTestId('auth-status')).toHaveTextContent('Not signed in')
    await userEvent.click(screen.getByRole('button', { name: /^Credentials/ }))
    expect(screen.getByRole('button', { name: /^Credentials/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('button', { name: 'Close credentials' })).toBeInTheDocument()
    expect(screen.getByLabelText('Institutional API Key')).toHaveAttribute('type', 'password')
    expect(screen.getByLabelText('User Token (Bearer)')).toHaveAttribute('type', 'password')
    await userEvent.type(screen.getByLabelText('User Token (Bearer)'), 'fictional-token')
    await waitFor(() => expect(screen.getByTestId('auth-status')).toHaveTextContent(`Signed in as ${MOCK_CURRENT_USER.display_name}`))
    expect(screen.getByTestId('auth-status')).toHaveTextContent('Issuer Reviewer · issuers: meridian-property')
    expect(document.body).not.toHaveTextContent('fictional-token')
  })

  it('a credential the server rejects is shown as not accepted', async () => {
    server.use(http.get(`${BASE}/auth/me`, () => HttpResponse.json({ detail: 'Invalid or expired credential' }, { status: 401 })))
    sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-bad-token', apiKey: '' }))
    renderApp('/api-docs')
    await waitFor(() => expect(screen.getByTestId('auth-status')).toHaveTextContent('Credential not accepted'))
  })

  it('the dashboard reloads when a credential is entered, without navigating', async () => {
    server.use(http.get(`${BASE}/assets`, ({ request }) => request.headers.get('Authorization')
      ? HttpResponse.json([MOCK_ASSET])
      : HttpResponse.json({ detail: 'Not authenticated' }, { status: 403 })))
    renderApp('/')
    expect(await screen.findByRole('alert')).toHaveTextContent('Not authenticated')
    await userEvent.click(screen.getByRole('button', { name: /^Credentials/ }))
    await userEvent.type(screen.getByLabelText('User Token (Bearer)'), 'fictional-token')
    await waitFor(() => expect(screen.queryByText('Not authenticated')).not.toBeInTheDocument())
    expect((await screen.findAllByText(MOCK_ASSET.stellar_asset_code)).length).toBeGreaterThan(0)
  })
})

describe('PDF download', () => {
  const PDF = new TextEncoder().encode('%PDF-1.4 fictional synthetic test bytes %%EOF')

  function serveDocument(sha256Hash: string, download: () => Response) {
    server.use(
      http.get(`${BASE}/assets/:id/documents`, () =>
        HttpResponse.json([{ ...MOCK_DOCUMENT, current_version: { ...MOCK_VERSION, sha256_hash: sha256Hash } }])),
      http.get(`${BASE}/documents/:docId/download`, download),
    )
    URL.createObjectURL = vi.fn(() => 'blob:fictional')
    URL.revokeObjectURL = vi.fn()
    return vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  }

  it('saves the file only after its bytes match the SHA-256 recorded for the version', async () => {
    const save = serveDocument(await sha256(PDF), () => new HttpResponse(PDF, { headers: { 'Content-Type': 'application/pdf' } }))
    renderAsset()
    expect(await screen.findByTestId('document-file')).toHaveTextContent(`${MOCK_VERSION.original_filename} · 100.0 KB`)
    await userEvent.click(screen.getByTestId('download-document'))
    expect(await screen.findByTestId('download-status')).toHaveTextContent('Version 1 downloaded · SHA-256 verified')
    expect(save).toHaveBeenCalledTimes(1)
  })

  it('refuses bytes that do not match the recorded hash', async () => {
    const save = serveDocument('f'.repeat(64), () => new HttpResponse(PDF, { headers: { 'Content-Type': 'application/pdf' } }))
    renderAsset()
    await userEvent.click(await screen.findByTestId('download-document'))
    expect(await screen.findByTestId('download-error')).toHaveTextContent('does not match the SHA-256 recorded for version 1; it was not saved')
    expect(save).not.toHaveBeenCalled()
  })

  it.each([
    [404, 'Document not found.'],
    [500, 'Document integrity check failed; the file was not served.'],
  ])('shows a %s from the server as a failed download', async (status, detail) => {
    const save = serveDocument('a'.repeat(64), () => HttpResponse.json({ detail }, { status }))
    renderAsset()
    await userEvent.click(await screen.findByTestId('download-document'))
    expect(await screen.findByTestId('download-error')).toHaveTextContent(`Download failed: ${detail}`)
    expect(save).not.toHaveBeenCalled()
  })
})

describe('AI status and extraction origin', () => {
  it('says that AI extraction is disabled, disables the action, and labels test data as not AI', async () => {
    server.use(http.get(`${BASE}/ai/status`, () => HttpResponse.json({
      ...MOCK_AI_STATUS, available: false, status: 'ai_provider_disabled', external_processor: null, model: null,
      message: 'AI extraction is disabled on this server (AI_PROVIDER=disabled): no document is sent to any AI service.',
    })))
    renderAsset()
    expect(await screen.findByTestId('ai-status')).toHaveTextContent('External AI extraction is disabled')
    expect(screen.getByRole('button', { name: /Run AI Extraction/ })).toBeDisabled()
    const origin = await screen.findByTestId('extraction-origin')
    expect(origin).toHaveTextContent(`Not produced by a supported AI model (test or imported data) · ${MOCK_EXTRACTION.ai_model}`)
    expect(origin).toHaveAttribute('data-origin', 'not_ai')
  })

  it('labels an extraction from a supported model as AI extraction', async () => {
    server.use(http.get(`${BASE}/documents/:docId/extractions`, () =>
      HttpResponse.json([{ ...MOCK_EXTRACTION, ai_model: 'claude-sonnet-4-6' }])))
    renderAsset()
    const origin = await screen.findByTestId('extraction-origin')
    expect(origin).toHaveTextContent('AI extraction · claude-sonnet-4-6')
    expect(origin).toHaveAttribute('data-origin', 'ai')
  })
})

describe('A second reviewer adds an approval', () => {
  const issuerAttested = () => reviewExtraction({
    status: 'approved',
    approval_state: { state: 'current', version_is_current: true, current_approval_types: ['issuer_attestation'], reasons: [] },
  })

  it('an independent legal reviewer can add the legal review to an issuer-attested extraction', async () => {
    server.use(http.get(`${BASE}/auth/me`, () => HttpResponse.json({
      ...MOCK_CURRENT_USER, role: 'independent_legal_reviewer', display_name: 'Test Legal Reviewer' })))
    let posted = 0
    server.use(http.post(`${BASE}/extractions/:id/approvals`, () => {
      posted += 1
      return HttpResponse.json({ id: 'appr-002' }, { status: 201 })
    }))
    setReviewerSession()
    serveReview({ extractions: issuerAttested() })
    renderReview()
    expect(await screen.findByText('Add Your Approval')).toBeInTheDocument()
    expect(screen.getByTestId('add-approval-note')).toHaveTextContent('independent legal review')
    expect(screen.getByTestId('extraction-origin')).toHaveTextContent('Not produced by a supported AI model')
    await userEvent.click(screen.getByRole('button', { name: 'Approve Extraction' }))
    await waitFor(() => expect(posted).toBe(1))
  })

  it('a reviewer whose approval type is already current is not offered another approval', async () => {
    setReviewerSession()   // MOCK_CURRENT_USER is an issuer reviewer
    serveReview({ extractions: issuerAttested() })
    renderReview()
    expect(await screen.findByText('✓ This extraction has been approved')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('extraction-origin')).toHaveAttribute('data-origin', 'not_ai'))
    expect(screen.queryByRole('button', { name: 'Approve Extraction' })).not.toBeInTheDocument()
  })
})
