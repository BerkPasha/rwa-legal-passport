import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ASSET, MOCK_DOCUMENT, MOCK_EXTRACTION, MOCK_VERSION } from './mocks/handlers'
import { AssetDetail } from '../pages/AssetDetail'
import { Dashboard } from '../pages/Dashboard'

const BASE = 'http://localhost:8000'

function renderAsset() {
  return render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <Routes>
        <Route path="assets/:id" element={<AssetDetail />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('AssetDetail with the backend document schema (Stage 9B-4, step 9)', () => {
  it('lists documents from a list response, which carries no version hash, without crashing', async () => {
    renderAsset()
    expect(await screen.findByText('Q4 2024 Prospectus')).toBeInTheDocument()
    expect(screen.getByText('v1')).toBeInTheDocument()
    const link = await screen.findByRole('link', { name: /Review →/ })
    expect(link).toHaveAttribute('href', `/assets/asset-001/documents/doc-001/extractions/${MOCK_EXTRACTION.id}`)
    expect(within(link).getByTestId('approval-state-badge')).toHaveTextContent('Approval current')
  })

  it('shows the hash of the current version when the response includes it', async () => {
    server.use(
      http.get(`${BASE}/assets/:id/documents`, () =>
        HttpResponse.json([{ ...MOCK_DOCUMENT, current_version: MOCK_VERSION }])),
    )
    renderAsset()
    expect(await screen.findByText(`${MOCK_VERSION.sha256_hash.slice(0, 16)}…`)).toBeInTheDocument()
  })

  it('shows an inaccessible asset as not found', async () => {
    server.use(http.get(`${BASE}/assets/:id`, () => HttpResponse.json({ detail: 'Asset not found.' }, { status: 404 })))
    renderAsset()
    expect(await screen.findByText('Asset not found.')).toBeInTheDocument()
    expect(screen.queryByText('Q4 2024 Prospectus')).not.toBeInTheDocument()
  })
})

describe('Dashboard asset status (Stage 9B-4, step 9)', () => {
  it('shows the registry status of an asset, never an approval', async () => {
    server.use(
      http.get(`${BASE}/assets`, () => HttpResponse.json([
        MOCK_ASSET,
        { ...MOCK_ASSET, id: 'asset-002', stellar_asset_code: 'HBRBND', status: 'suspended' },
      ])),
    )
    render(<MemoryRouter><Dashboard /></MemoryRouter>)
    const badges = await screen.findAllByTestId('asset-status-badge')
    expect(badges.map(b => b.textContent)).toEqual(['active', 'suspended'])
    expect(screen.queryByText('Approved')).not.toBeInTheDocument()
    expect(screen.queryByText('Under Review')).not.toBeInTheDocument()
  })
})
