import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ANCHOR_VERIFICATION, MOCK_PASSPORT_HISTORY } from './mocks/handlers'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'
import type { PassportHistory } from '../types'

// The Passport History view (GET /passport/.../history). The backend's evaluation of the records is
// tested in tests/test_passport_history.py; here only what the page shows from them.
const BASE = 'http://localhost:8000'
const SESSION_KEY = 'rwa_passport_session_auth'
const [DOC] = MOCK_PASSPORT_HISTORY.documents
const [V1] = DOC.approved_versions

// After version 2 was uploaded: version 1's approvals are superseded, version 2 is not approved yet
const AFTER_NEW_VERSION: PassportHistory = {
  ...MOCK_PASSPORT_HISTORY,
  current_passport_available: false,
  current_manifest_digest: null,
  documents: [{
    ...DOC,
    current_version_number: 2,
    versions: [
      { ...DOC.versions[0], is_current: false },
      { version_number: 2, sha256_hash: 'b'.repeat(64), uploaded_at: '2024-02-01T00:00:00Z', is_current: true },
    ],
    approved_versions: [{
      ...V1, approval_state: 'superseded', stale_reasons: ['document_version_superseded'], served_in_current_passport: false,
      approvals: V1.approvals.map(a => ({ ...a, is_current: false, counted_in_current_passport: false,
                                          stale_reasons: ['document_version_superseded' as const] })),
    }],
  }],
  anchors: MOCK_PASSPORT_HISTORY.anchors.map(a => ({ ...a, matches_current_evidence: false })),
}

function signIn(apiKey = 'test-inst-key-alpha') {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey }))
}

function renderApp(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

async function lookUp() {
  renderApp('/passport?network=stellar-mainnet&asset_code=MRPROP&issuer_key=GABC1234567890')
  await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
  return screen.findByTestId('passport-history')
}

afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
})

describe('Passport History', () => {
  it('explains a 409 passport: which version changed, why approvals lapsed, what needs reapproval', async () => {
    signIn()
    server.use(
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey`, () => HttpResponse.json(
        { detail: 'The legal approval for this asset is no longer current; a re-review is required.' }, { status: 409 })),
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/history`, () => HttpResponse.json(AFTER_NEW_VERSION)),
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/stellar-anchor`, () => HttpResponse.json({
        ...MOCK_ANCHOR_VERIFICATION, current_passport_available: false, current_manifest_digest: null,
        evidence_matches_anchor: false, result: 'evidence_changed', verified: false })),
    )
    const history = await lookUp()

    expect(screen.getByText(/409: The legal approval for this asset is no longer current/)).toBeInTheDocument()
    expect(screen.getByTestId('passport-not-current-note')).toHaveTextContent(
      'Passport History below shows which version changed and what requires reapproval.')
    expect(screen.queryByTestId('passport-card')).not.toBeInTheDocument()
    expect(within(history).getByTestId('history-summary')).toHaveTextContent(
      'No current Legal Passport: the approvals behind it are no longer current. Reapproval is required.')

    // Chronological versions; version 1's approvals and why they lapsed
    const versions = within(history).getAllByTestId('history-version')
    expect(versions.map(v => v.dataset.version)).toEqual(['1', '2'])
    expect(within(versions[0]).getByTestId('approval-state-badge')).toHaveTextContent('Superseded version')
    expect(within(versions[0]).getAllByTestId('history-approval').map(a => a.textContent)).toEqual([
      expect.stringMatching(/^Issuer Attestationapproved .*no longer current$/),
      expect.stringMatching(/^Independent Legal Reviewapproved .*no longer current$/),
    ])
    expect(within(versions[0]).getByTestId('history-invalidation')).toHaveTextContent(
      'Why it is no longer current: A newer document version exists.')
    expect(versions[1]).toHaveTextContent('Current version')
    expect(versions[1]).toHaveTextContent('Never approved.')

    // The current version requires reapproval
    expect(within(history).getByTestId('history-requirement')).toHaveTextContent('Reapproval required')
    expect(within(history).getByTestId('history-requirement-detail')).toHaveTextContent(
      'Version 2 has no current approval: earlier approvals no longer apply.')

    // The stored anchor no longer matches; the live check says the same
    expect(within(history).getByTestId('history-anchor')).toHaveTextContent('Confirmed')
    expect(within(history).getByTestId('history-anchor')).toHaveTextContent('Does not match the current evidence')
    expect(screen.getByTestId('anchor-result-badge')).toHaveTextContent(/evidence changed/i)
  })

  it('shows a current passport\'s approved version and matching anchor', async () => {
    signIn()
    const history = await lookUp()
    expect(await screen.findByTestId('passport-card')).toBeInTheDocument()
    expect(within(history).getByTestId('history-summary')).toHaveTextContent('A current Legal Passport is available.')
    expect(within(history).getByTestId('history-requirement')).toHaveTextContent('Current version approved')
    const [version] = within(history).getAllByTestId('history-version')
    expect(version).toHaveTextContent('Used by the current Legal Passport')
    expect(within(version).getAllByTestId('history-approval').every(a => a.textContent?.endsWith('current')
      && !a.textContent.includes('no longer'))).toBe(true)
    expect(within(version).queryByTestId('history-invalidation')).not.toBeInTheDocument()
    expect(within(history).getByTestId('history-anchor')).toHaveTextContent('Matches the current evidence')
  })

  it('opens from the asset page and loads the history by itself', async () => {
    signIn()
    renderApp('/assets/asset-001')
    const link = await screen.findByTestId('open-passport-history-link')
    expect(link).toHaveAttribute('href', expect.stringMatching(/^\/passport\?.*&view=history$/))
    await userEvent.click(link)
    // No click on "Fetch Legal Passport": the lookup ran with the key from the Credentials panel
    expect(await screen.findByTestId('passport-history')).toBeInTheDocument()
    expect(screen.getByLabelText('Asset Code')).toHaveValue('MRPROP')
  })

  it('never calls a never-approved asset stale', async () => {
    signIn()
    server.use(
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey`, () => HttpResponse.json(
        { detail: 'No approved legal passport is available for this asset yet.' }, { status: 404 })),
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/history`, () => HttpResponse.json({
        ...MOCK_PASSPORT_HISTORY, current_passport_available: false, current_manifest_digest: null, anchors: [],
        documents: [{ ...DOC, approved_versions: [] }] })),
    )
    const history = await lookUp()
    expect(within(history).getByTestId('history-summary')).toHaveTextContent(
      'No current Legal Passport: no document has been approved yet.')
    expect(within(history).getByTestId('history-requirement')).toHaveTextContent('Not approved yet')
    expect(screen.queryByTestId('passport-not-current-note')).not.toBeInTheDocument()   // a 404, not a 409
  })

  it('reports an unavailable history without hiding the passport', async () => {
    signIn()
    server.use(http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/history`, () =>
      HttpResponse.json({ detail: 'Internal error' }, { status: 500 })))
    renderApp('/passport?network=stellar-mainnet&asset_code=MRPROP&issuer_key=GABC1234567890')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    expect(await screen.findByTestId('history-lookup-error')).toHaveTextContent(
      'Passport history unavailable — 500: Internal error')
    expect(screen.getByTestId('passport-card')).toBeInTheDocument()
    expect(screen.queryByTestId('passport-history')).not.toBeInTheDocument()
  })
})
