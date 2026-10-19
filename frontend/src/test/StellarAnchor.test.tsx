import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import {
  BACKEND_ANCHOR_DISCLAIMER,
  MOCK_ANCHOR_VERIFICATION,
  MOCK_ASSET,
  MOCK_STELLAR_ANCHOR,
} from './mocks/handlers'
import { AssetDetail } from '../pages/AssetDetail'
import { PassportPreview } from '../pages/PassportPreview'
import { AuthProvider } from '../AuthContext'
import type { AnchorVerification } from '../types'

// Stellar Testnet evidence anchoring in the interface (STELLAR_ANCHORING.md). All network data comes
// from the MSW mock server; no request reaches Stellar.
const BASE = 'http://localhost:8000'
const TX = MOCK_STELLAR_ANCHOR.transaction_hash as string

// A signed-in issuer reviewer (MOCK_CURRENT_USER, with anchor:create): the anchoring actions are offered
beforeEach(() => sessionStorage.setItem('rwa_passport_session_auth', JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' })))
afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
})

function verification(overrides: Partial<AnchorVerification>): AnchorVerification {
  return { ...MOCK_ANCHOR_VERIFICATION, ...overrides }
}

async function openAnchorTab() {
  render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <AuthProvider><Routes><Route path="assets/:id" element={<AssetDetail />} /></Routes></AuthProvider>
    </MemoryRouter>,
  )
  await userEvent.click(await screen.findByRole('button', { name: 'Stellar Evidence Anchor' }))
  return screen.findByTestId('anchor-card')
}

describe('Stellar evidence anchor on the asset page', () => {
  it('shows a verified commitment with its transaction, explorer links and matching digests', async () => {
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Commitment verified on Stellar Testnet')
    expect(within(card).getByTestId('anchor-tx-hash')).toHaveTextContent(TX)
    expect(within(card).getByTestId('anchor-explorer-link')).toHaveAttribute(
      'href', `https://stellar.expert/explorer/testnet/tx/${TX}`)
    expect(within(card).getByTestId('anchor-horizon-link')).toHaveAttribute(
      'href', `https://horizon-testnet.stellar.org/transactions/${TX}`)
    expect(within(card).getByTestId('anchor-evidence-match')).toHaveTextContent(
      'Current evidence matches the recorded commitment')
    expect(within(card).getByTestId('anchor-onchain-checks')).toHaveTextContent('Memo hash equals the digest')
    expect(within(card).getByTestId('anchor-not-legal')).toHaveTextContent(BACKEND_ANCHOR_DISCLAIMER)
    // Already anchored and unchanged: nothing to submit
    expect(screen.queryByRole('button', { name: /Anchor current evidence/ })).not.toBeInTheDocument()
    expect(await screen.findByTestId('anchor-history')).toHaveTextContent('Confirmed')
  })

  it('never presents a blockchain commitment as independent or legal verification', async () => {
    const card = await openAnchorTab()
    const badge = within(card).getByTestId('anchor-result-badge').textContent ?? ''
    const explanation = within(card).getByTestId('anchor-explanation').textContent ?? ''
    for (const text of [badge, explanation]) {
      expect(text).not.toMatch(/independent/i)
      expect(text).not.toMatch(/legally valid(?!\.)/i)
    }
    expect(explanation).toContain('does not show that the evidence is legally valid')
  })

  it('shows changed evidence, the mismatching digests and offers to anchor the current evidence', async () => {
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
      result: 'evidence_changed', verified: false, evidence_matches_anchor: false, current_manifest_digest: 'f'.repeat(64),
    }))))
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Evidence changed since anchoring')
    expect(within(card).getByTestId('anchor-evidence-match')).toHaveTextContent('does not match')
    expect(within(card).getByTestId('anchor-digests')).toHaveTextContent('f'.repeat(64))
    expect(screen.getByRole('button', { name: 'Anchor current evidence on Testnet' })).toBeInTheDocument()
  })

  it('shows a pending transaction and refreshes it on request', async () => {
    const pending = { ...MOCK_STELLAR_ANCHOR, status: 'pending' as const, ledger: null, ledger_closed_at: null }
    let refreshed = 0
    server.use(
      http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
        result: 'pending', verified: false,
        anchor: { ...MOCK_ANCHOR_VERIFICATION.anchor!, status: 'pending', ledger: null, ledger_closed_at: null },
        onchain: { ...MOCK_ANCHOR_VERIFICATION.onchain, transaction_found: false, successful: null, ledger: null,
                   ledger_closed_at: null, source_account_matches: null, memo_matches: null, operation_matches: null },
      }))),
      http.get(`${BASE}/assets/:id/stellar-anchors`, () => HttpResponse.json([pending])),
      http.post(`${BASE}/assets/:id/stellar-anchors/:anchorId/refresh`, () => {
        refreshed += 1
        return HttpResponse.json({ ...pending, status: 'confirmed' })
      }),
    )
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Pending confirmation')
    await userEvent.click(screen.getByRole('button', { name: 'Refresh pending transaction' }))
    expect(await screen.findByTestId('anchor-notice')).toHaveTextContent('confirmed')
    expect(refreshed).toBe(1)
  })

  it('shows a failed anchoring transaction', async () => {
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
      result: 'failed', verified: false, anchor: { ...MOCK_ANCHOR_VERIFICATION.anchor!, status: 'failed' },
      onchain: { ...MOCK_ANCHOR_VERIFICATION.onchain, checked: false },
    }))))
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Anchoring failed')
    expect(within(card).getByTestId('anchor-explanation')).toHaveTextContent('Nothing was recorded on-chain')
  })

  it('shows a tampered record as invalid: its stored status and equal digest are not presented as proof', async () => {
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
      result: 'anchor_record_invalid', verified: false, evidence_matches_anchor: true,
      onchain: { ...MOCK_ANCHOR_VERIFICATION.onchain, checked: false },
    }))))
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Anchor record invalid')
    expect(within(card).getByTestId('anchor-evidence-match')).toHaveTextContent('fails its integrity check')
    expect(within(card).getByTestId('anchor-evidence-match')).not.toHaveTextContent('matches the recorded commitment')
    expect(within(card).getByTestId('anchor-status-unconfirmed')).toHaveTextContent('not confirmed on Stellar Testnet')
    expect(within(card).queryByTestId('anchor-onchain-checks')).not.toBeInTheDocument()
    expect(within(card).queryByText('Commitment verified on Stellar Testnet')).not.toBeInTheDocument()
  })

  it('marks the stored status as unconfirmed whenever the commitment is not verified', async () => {
    server.use(http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
      result: 'network_unavailable', verified: false,
      onchain: { ...MOCK_ANCHOR_VERIFICATION.onchain, error: 'horizon_unreachable', transaction_found: null,
                 successful: null, source_account_matches: null, memo_matches: null, operation_matches: null },
    }))))
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Stellar network unavailable')
    expect(within(card).getByTestId('anchor-status-unconfirmed')).toBeInTheDocument()
  })

  it('shows no "unconfirmed" note for a verified commitment', async () => {
    const card = await openAnchorTab()
    expect(within(card).queryByTestId('anchor-status-unconfirmed')).not.toBeInTheDocument()
  })

  it('anchors the evidence of a re-approved new version; the earlier anchor never stands for it', async () => {
    // Version 2 was approved again: the current evidence (digest f…) differs from version 1's anchor
    const current = 'f'.repeat(64)
    const v2 = { ...MOCK_STELLAR_ANCHOR, id: 'anchor-002', manifest_digest: current, transaction_hash: 'e'.repeat(64),
                 created_at: '2026-10-20T09:00:00Z' }
    let anchored = false
    server.use(
      http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(anchored
        ? verification({ current_manifest_digest: current,
                         anchor: { ...MOCK_ANCHOR_VERIFICATION.anchor!, manifest_digest: current, transaction_hash: v2.transaction_hash } })
        : verification({ result: 'evidence_changed', verified: false, evidence_matches_anchor: false,
                         current_manifest_digest: current }))),
      http.get(`${BASE}/assets/:id/stellar-anchors`, () =>
        HttpResponse.json(anchored ? [v2, MOCK_STELLAR_ANCHOR] : [MOCK_STELLAR_ANCHOR])),
      http.post(`${BASE}/assets/:id/stellar-anchors`, () => {
        anchored = true
        return HttpResponse.json(v2, { status: 201 })
      }),
    )
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Evidence changed since anchoring')
    expect(screen.getAllByTestId('anchor-history-match').map(m => m.textContent)).toEqual(['Does not match the current evidence'])

    vi.spyOn(window, 'confirm').mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Anchor current evidence on Testnet' }))
    expect(await screen.findByTestId('anchor-notice')).toHaveTextContent('Anchoring confirmed.')
    await waitFor(() => expect(screen.getByTestId('anchor-result-badge')).toHaveTextContent('Commitment verified on Stellar Testnet'))
    expect(screen.getByTestId('anchor-tx-hash')).toHaveTextContent(v2.transaction_hash)
    expect(screen.getAllByTestId('anchor-history-match').map(m => m.textContent)).toEqual([
      'Matches the current evidence', 'Does not match the current evidence'])
    expect(screen.queryByRole('button', { name: 'Anchor current evidence on Testnet' })).not.toBeInTheDocument()
  })

  it('asks for confirmation before submitting a Testnet transaction and explains a refusal', async () => {
    let posts = 0
    server.use(
      http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
        result: 'not_anchored', verified: false, anchor: null, evidence_matches_anchor: false,
        onchain: { ...MOCK_ANCHOR_VERIFICATION.onchain, checked: false }, committed_manifest_canonical_json: null,
      }))),
      http.get(`${BASE}/assets/:id/stellar-anchors`, () => HttpResponse.json([])),
      http.post(`${BASE}/assets/:id/stellar-anchors`, () => {
        posts += 1
        return HttpResponse.json({ detail: 'This server is configured for verification only.',
                                   anchor_refusal: 'anchoring_not_configured' }, { status: 503 })
      }),
    )
    const card = await openAnchorTab()
    expect(within(card).getByTestId('anchor-result-badge')).toHaveTextContent('Not anchored')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false)
    await userEvent.click(screen.getByRole('button', { name: 'Anchor current evidence on Testnet' }))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('Stellar Testnet'))
    expect(posts).toBe(0)  // declined: nothing submitted

    confirm.mockReturnValueOnce(true)
    await userEvent.click(screen.getByRole('button', { name: 'Anchor current evidence on Testnet' }))
    expect(await screen.findByTestId('anchor-action-error')).toHaveTextContent('Anchoring is disabled on this server')
    expect(posts).toBe(1)
  })

  it('offers no anchoring for a Mainnet asset', async () => {
    server.use(
      http.get(`${BASE}/assets/:id`, () => HttpResponse.json({ ...MOCK_ASSET, blockchain_network: 'stellar-mainnet' })),
      http.get(`${BASE}/assets/:id/stellar-anchors/verification`, () => HttpResponse.json(verification({
        result: 'not_anchored', verified: false, anchor: null, evidence_matches_anchor: false,
      }))),
      http.get(`${BASE}/assets/:id/stellar-anchors`, () => HttpResponse.json([])),
    )
    await openAnchorTab()
    expect(screen.getByText(/available for Stellar Testnet assets only/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Anchor current evidence/ })).not.toBeInTheDocument()
  })

  it('links the asset to its Legal Passport lookup', async () => {
    render(
      <MemoryRouter initialEntries={['/assets/asset-001']}>
        <AuthProvider><Routes><Route path="assets/:id" element={<AssetDetail />} /></Routes></AuthProvider>
      </MemoryRouter>,
    )
    const link = await screen.findByTestId('open-passport-link')
    expect(link).toHaveAttribute('href', '/passport?network=stellar-testnet&asset_code=MRPROP&issuer_key=GABC1234567890')
  })
})

describe('Stellar evidence commitment on the Legal Passport lookup', () => {
  function renderPassport(path = '/passport') {
    return render(
      <MemoryRouter initialEntries={[path]}>
        <AuthProvider><PassportPreview /></AuthProvider>
      </MemoryRouter>,
    )
  }

  it('prefills the lookup from the asset link', () => {
    renderPassport('/passport?network=stellar-testnet&asset_code=lpdemo&issuer_key=GB5M55')
    expect(screen.getByPlaceholderText('MRPROP')).toHaveValue('LPDEMO')
    expect(screen.getByPlaceholderText('G…')).toHaveValue('GB5M55')
  })

  it('shows the passport and its verified commitment, read with the institutional key', async () => {
    let anchorKey: string | null = null
    server.use(http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/stellar-anchor`, ({ request }) => {
      anchorKey = request.headers.get('X-API-Key')
      return HttpResponse.json(MOCK_ANCHOR_VERIFICATION)
    }))
    renderPassport('/passport?network=stellar-testnet&asset_code=MRPROP&issuer_key=GABC1234567890')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    const passport = await screen.findByTestId('passport-card')
    const anchor = await screen.findByTestId('anchor-card')
    expect(anchorKey).toBe('test-inst-key-alpha')
    expect(within(anchor).getByTestId('anchor-result-badge')).toHaveTextContent('Commitment verified on Stellar Testnet')
    // The issuer-attested passport stays issuer attested: the commitment does not raise it
    expect(within(passport).getByTestId('verification-level-badge')).toHaveTextContent('Issuer attested')
    expect(within(passport).getByTestId('verification-level-badge')).not.toHaveTextContent(/independent reviewer/i)
  })

  it('still shows the commitment when no approved passport is current', async () => {
    server.use(
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey`, () => HttpResponse.json(
        { detail: 'The legal approval for this asset is no longer current; a re-review is required.' }, { status: 409 })),
      http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/stellar-anchor`, () => HttpResponse.json(verification({
        result: 'evidence_changed', verified: false, current_passport_available: false, current_manifest_digest: null,
        evidence_matches_anchor: false,
      }))),
    )
    renderPassport('/passport?network=stellar-testnet&asset_code=MRPROP&issuer_key=GABC1234567890')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    expect(await screen.findByText(/409: The legal approval for this asset is no longer current/)).toBeInTheDocument()
    const anchor = await screen.findByTestId('anchor-card')
    expect(within(anchor).getByTestId('anchor-result-badge')).toHaveTextContent('Evidence changed since anchoring')
    expect(within(anchor).getByTestId('anchor-digests')).toHaveTextContent('No current approved passport')
    expect(screen.queryByTestId('passport-card')).not.toBeInTheDocument()
  })

  it('reports an unavailable commitment without hiding the passport', async () => {
    server.use(http.get(`${BASE}/passport/:network/:assetCode/:issuerKey/stellar-anchor`, () =>
      HttpResponse.json({ detail: 'Rate limit exceeded. Please try again in a minute.' }, { status: 429 })))
    renderPassport('/passport?network=stellar-testnet&asset_code=MRPROP&issuer_key=GABC1234567890')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    expect(await screen.findByTestId('passport-card')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('anchor-lookup-error')).toHaveTextContent('429'))
    expect(screen.queryByTestId('anchor-card')).not.toBeInTheDocument()
  })
})
