import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_DOCUMENT, MOCK_VERSION } from './mocks/handlers'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'

// Stage 10C-1: a read-only showcase of synthetic data, as the server declares it on /health. The
// interface labels it and does not offer actions the server refuses; the server enforces it
// itself (tests/test_showcase_policies.py).
const BASE = 'http://localhost:8000'
const SESSION_KEY = 'rwa_passport_session_auth'

function showcase(info: Partial<{ read_only: boolean; synthetic_data: boolean; api_docs: boolean }>) {
  server.use(http.get(`${BASE}/health`, () => HttpResponse.json({
    status: 'ok', version: '0.1.0', environment: 'showcase', read_only: false, synthetic_data: false, api_docs: true, ...info,
  })))
}

function renderApp(path: string, signedIn = true) {
  if (signedIn) sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }))
  server.use(http.get(`${BASE}/assets/:id/documents`, () =>
    HttpResponse.json([{ ...MOCK_DOCUMENT, current_version: MOCK_VERSION }])))
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
})

describe('Read-only showcase of synthetic data', () => {
  it('says so on every page', async () => {
    showcase({ read_only: true, synthetic_data: true })
    renderApp('/api-docs', false)
    const banner = await screen.findByTestId('deployment-banner')
    expect(banner).toHaveTextContent('Read-only showcase: nothing can be created or changed on this server.')
    expect(banner).toHaveTextContent('synthetic and fictional')
    expect(banner).toHaveTextContent('Stellar Testnet only')
    expect(banner).toHaveTextContent('not a production service')
  })

  it('offers no registration in the asset registry', async () => {
    showcase({ read_only: true })
    renderApp('/assets')
    await screen.findByTestId('deployment-banner')
    expect(await screen.findByRole('heading', { level: 2, name: 'Asset Registry' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Register Asset' })).not.toBeInTheDocument()
  })

  it('offers no upload, extraction, manual claim or anchoring on an asset', async () => {
    showcase({ read_only: true })
    renderApp('/assets/asset-001')
    await screen.findByTestId('deployment-banner')
    await screen.findByText(/Signed in as/)
    const card = await screen.findByTestId('document-card')
    expect(screen.queryByRole('button', { name: 'Upload Document' })).not.toBeInTheDocument()
    expect(within(card).queryByRole('button', { name: /Run AI Extraction/ })).not.toBeInTheDocument()
    expect(within(card).queryByTestId('add-manual-claim')).not.toBeInTheDocument()
    expect(within(card).getByTestId('download-document')).toBeInTheDocument()   // reading stays possible
    await userEvent.click(screen.getByRole('button', { name: 'Stellar Evidence Anchor' }))
    expect(await screen.findByTestId('anchor-read-only-note')).toHaveTextContent('Read-only showcase')
    expect(screen.queryByRole('button', { name: /Anchor current evidence|Refresh pending/ })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-check on Stellar' })).toBeInTheDocument()
  })

  it('offers no review, correction or approval, and still shows the record', async () => {
    showcase({ read_only: true })
    renderApp('/assets/asset-001/documents/doc-001/extractions/ext-001')
    expect(await screen.findByTestId('review-read-only-note')).toHaveTextContent('cannot be given on this server')
    expect(screen.queryByRole('button', { name: 'Start Review' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Approve Extraction/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Correct' })).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Approvals \(/ })).toBeInTheDocument()
  })

  it('says that the interactive API documentation is not published', async () => {
    showcase({ read_only: true, api_docs: false })
    renderApp('/api-docs', false)
    expect(await screen.findByTestId('api-docs-disabled')).toHaveTextContent('does not publish its interactive documentation')
    expect(screen.queryByText(/\/docs$/)).not.toBeInTheDocument()
  })
})

describe('An ordinary server', () => {
  it('shows no showcase banner and offers its actions', async () => {
    renderApp('/assets')
    expect(await screen.findByRole('button', { name: 'Register Asset' })).toBeInTheDocument()
    expect(screen.queryByTestId('deployment-banner')).not.toBeInTheDocument()
  })

  it('offers the actions when the policies cannot be read: the server decides', async () => {
    server.use(http.get(`${BASE}/health`, () => HttpResponse.error()))
    renderApp('/assets')
    expect(await screen.findByRole('button', { name: 'Register Asset' })).toBeInTheDocument()
    expect(screen.queryByTestId('deployment-banner')).not.toBeInTheDocument()
  })
})

describe('How to read this application', () => {
  it('explains each concept without equating a quotation match with legal validity', async () => {
    renderApp('/', false)
    const section = await screen.findByTestId('how-to-read')
    for (const title of ['The problem', 'A Legal Passport', 'Page-grounded verification', 'Human review',
                         'Stellar Testnet anchoring', 'Experimental']) {
      expect(within(section).getByText(title)).toBeInTheDocument()
    }
    expect(section).toHaveTextContent('This proves only that the text is there. It does not prove that a claim is legally valid')
    expect(section).toHaveTextContent('Neither is a legal opinion')
    expect(section).toHaveTextContent('never shown as verified')
    expect(section).toHaveTextContent('not been verified against a real model')
  })
})
