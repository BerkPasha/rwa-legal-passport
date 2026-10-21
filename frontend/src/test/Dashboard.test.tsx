import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ASSET, MOCK_CURRENT_USER, MOCK_DOCUMENT, MOCK_EXTRACTION, MOCK_STELLAR_ANCHOR, MOCK_VERSION } from './mocks/handlers'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'
import type { LegalExtraction } from '../types'

// The workspace overview (pages/Dashboard.tsx): every figure and state is read from the API records.
const BASE = 'http://localhost:8000'
const SESSION_KEY = 'rwa_passport_session_auth'
const SECOND = { ...MOCK_ASSET, id: 'asset-002', stellar_asset_code: 'HBRBND', issuer_organization_name: 'Harbor Bonds Ltd',
                 updated_at: '2024-03-01T00:00:00Z' }
const SUPERSEDED: LegalExtraction = {
  ...MOCK_EXTRACTION, id: 'ext-old',
  approval_state: { state: 'superseded', version_is_current: false, current_approval_types: [], reasons: ['document_version_superseded'] },
}
const DRAFT: LegalExtraction = {
  ...MOCK_EXTRACTION, id: 'ext-new', status: 'draft', created_at: '2024-03-02T00:00:00Z', source: 'manual',
  approval_state: { state: 'not_approved', version_is_current: true, current_approval_types: [], reasons: [] },
}

function signIn(permissions = MOCK_CURRENT_USER.permissions) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }))
  server.use(http.get(`${BASE}/auth/me`, () => HttpResponse.json({ ...MOCK_CURRENT_USER, permissions })))
}

// MRPROP: an approval current, a confirmed anchor. HBRBND: a new version awaiting review, a pending anchor.
function serveRecords() {
  const reads: string[] = []
  server.use(
    http.get(`${BASE}/assets`, () => HttpResponse.json([MOCK_ASSET, SECOND])),
    http.get(`${BASE}/assets/:id/documents`, ({ params }) => {
      reads.push(`documents:${params.id}`)
      return HttpResponse.json(params.id === 'asset-002'
        ? [{ ...MOCK_DOCUMENT, id: 'doc-002', asset_id: 'asset-002', title: 'Bond Indenture', current_version_number: 2,
             current_version: { ...MOCK_VERSION, id: 'ver-002', document_id: 'doc-002', version_number: 2, created_at: '2024-03-01T12:00:00Z' } }]
        : [{ ...MOCK_DOCUMENT, current_version: MOCK_VERSION }])
    }),
    http.get(`${BASE}/documents/:docId/extractions`, ({ params }) => {
      reads.push(`extractions:${params.docId}`)
      return HttpResponse.json(params.docId === 'doc-002' ? [SUPERSEDED, DRAFT] : [MOCK_EXTRACTION])
    }),
    http.get(`${BASE}/assets/:id/stellar-anchors`, ({ params }) => {
      reads.push(`anchors:${params.id}`)
      return HttpResponse.json(params.id === 'asset-002' ? [{ ...MOCK_STELLAR_ANCHOR, id: 'anchor-002', status: 'pending' }] : [MOCK_STELLAR_ANCHOR])
    }),
  )
  return reads
}

function renderDashboard() {
  return render(<MemoryRouter initialEntries={['/dashboard']}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
})

describe('Workspace overview', () => {
  it('puts the operational records first: figures, assets, attention, activity and anchoring', async () => {
    signIn()
    serveRecords()
    renderDashboard()
    expect(await screen.findByRole('heading', { level: 2, name: 'Overview' })).toBeInTheDocument()

    // Figures counted from the records
    const figures = screen.getByText('Assets in scope').closest('dl')!
    await waitFor(() => expect(within(figures).getByText('Current approvals').nextSibling).toHaveTextContent('1'))
    expect(within(figures).getByText('Assets in scope').nextSibling).toHaveTextContent('2')
    expect(within(figures).getByText('Legal documents').nextSibling).toHaveTextContent('2')
    expect(figures).toHaveTextContent('3 versions on record')

    // Every asset with its registry status, its approval state and its recorded anchor
    const assets = screen.getByRole('region', { name: 'Assets' })
    const rows = within(assets).getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getAllByRole('cell')[0].textContent)).toEqual([
      expect.stringContaining('HBRBND'), expect.stringContaining('MRPROP')])          // most recently updated first
    expect(rows[1]).toHaveTextContent('Approval current')
    expect(rows[1]).toHaveTextContent('Confirmed')
    expect(rows[0]).toHaveTextContent('Reapproval required')
    expect(rows[0]).toHaveTextContent('Pending')
    expect(within(assets).getAllByTestId('asset-status-badge').map(b => b.textContent)).toEqual(['active', 'active'])

    // What needs attention, each linked to where it is resolved
    const attention = screen.getByRole('region', { name: 'Requires attention' })
    const reapproval = within(attention).getByRole('link', { name: /Bond Indenture v2: reapproval required/ })
    expect(reapproval).toHaveAttribute('href', '/assets/asset-002')
    expect(reapproval).toHaveTextContent('A newer document version exists')
    expect(within(attention).getByRole('link', { name: /Anchoring transaction pending/ })).toHaveAttribute('href', '/assets/asset-002')

    // Recent activity and the recorded anchoring states
    const activity = screen.getByRole('region', { name: 'Recent record activity' })
    const events = within(activity).getAllByRole('link')
    expect(events[0]).toHaveTextContent('Anchoring recorded')                   // the newest: 2026-10-17
    expect(within(activity).getByRole('link', { name: /Bond Indenture · claims entered manually/ }))
      .toHaveAttribute('href', '/assets/asset-002/documents/doc-002/extractions/ext-new')
    expect(events).toHaveLength(8)                                              // the eight most recent of nine
    const anchoring = screen.getByRole('region', { name: 'Evidence anchoring' })
    expect(anchoring).toHaveTextContent('A record is not a verification')
    // Never a bare "Approved" or "Under Review" label (Stage 9B-4, step 9)
    expect(screen.queryByText('Approved')).not.toBeInTheDocument()
    expect(screen.queryByText('Under Review')).not.toBeInTheDocument()
  })

  it('keeps the environment notes, collapsed, and the internal-use notice in the shell', async () => {
    signIn()
    serveRecords()
    renderDashboard()
    const notes = await screen.findByTestId('how-to-read')
    expect(notes.tagName).toBe('DETAILS')
    expect(notes).not.toHaveAttribute('open')
    expect(notes).toHaveTextContent('Nothing here constitutes legal advice, regulatory certification or investment guidance')
    expect(notes).toHaveTextContent('This proves only that the text is there.')
    expect(screen.getByRole('banner')).toHaveTextContent('Internal Use Only')
  })

  it('reads only what the role may read', async () => {
    signIn(MOCK_CURRENT_USER.permissions.filter(p => p !== 'extraction:read'))
    const reads = serveRecords()
    renderDashboard()
    await waitFor(() => expect(reads.filter(r => r.startsWith('documents:'))).toHaveLength(2))
    expect(reads.filter(r => r.startsWith('extractions:'))).toEqual([])
    expect(within(screen.getByRole('region', { name: 'Assets' })).getAllByRole('row')[1]).toHaveTextContent('—')
  })

  it('says so when nobody is signed in, without inventing figures', async () => {
    server.use(http.get(`${BASE}/assets`, () => HttpResponse.json({ detail: 'Not authenticated' }, { status: 403 })))
    renderDashboard()
    expect(await screen.findByRole('alert')).toHaveTextContent('Not authenticated')
    expect(screen.getAllByText('Sign in to read the records').length).toBeGreaterThan(0)
    expect(screen.getByText('Legal documents').nextSibling).toHaveTextContent('—')
  })
})
