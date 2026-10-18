import { test, expect, ctx, signIn, openAsset, api, bearer, API_URL, type SeededAsset } from '../support'
import type { Page } from '@playwright/test'

// E. Stellar Evidence Anchor states that need no network (Stage 10B-2). The seeded anchor records of
// E2EPND and E2EINV are synthetic: they were never submitted to any network, the server's anchoring
// account is verification-only, and its outbound HTTP is blocked. Nothing here can be, or may be
// shown as, a ledger verification.
const pendingAsset = ctx.assets!.anchor_pending
const invalidAsset = ctx.assets!.anchor_invalid

async function openAnchorTab(page: Page, asset: SeededAsset) {
  await signIn(page, ctx.credentials.issuer_a)
  await openAsset(page, asset.stellar_asset_code)
  await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
  return page.getByTestId('anchor-card')
}

test.describe('E. Stellar Evidence Anchor (synthetic records, network blocked)', () => {
  test('a tampered anchor record is shown as invalid, without any network check', async ({ page, request }) => {
    const backend = await api(request, `/assets/${invalidAsset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer_a))
    expect(backend.body).toMatchObject({ result: 'anchor_record_invalid', verified: false, onchain: { checked: false } })
    // The stored record even claims "confirmed", and its digest equals the current one: neither is relied on
    expect([backend.body.anchor.status, backend.body.evidence_matches_anchor]).toEqual(['confirmed', true])

    const card = await openAnchorTab(page, invalidAsset)
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Anchor record invalid')
    await expect(card.getByTestId('anchor-explanation')).toHaveText('The stored anchor record fails its own integrity check. It is not a valid commitment.')
    await expect(card.getByTestId('anchor-evidence-match')).toHaveText('The stored record fails its integrity check: its digest is not relied on')
    await expect(card.getByTestId('anchor-status-unconfirmed')).toHaveText('Stored by this server only; not confirmed on Stellar Testnet by this check.')
    await expect(card.getByTestId('anchor-onchain-checks')).toHaveCount(0)
    await expect(card.getByTestId('anchor-not-legal')).toBeVisible()
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    await expect(page.getByText('Current evidence matches the recorded commitment')).toHaveCount(0)
  })

  test('a pending anchor is never shown as verified while Stellar cannot be reached', async ({ page, request }) => {
    const verification = `/assets/${pendingAsset.id}/stellar-anchors/verification`
    const backend = await api(request, verification, bearer(ctx.credentials.issuer_a))
    expect(backend.body).toMatchObject({ result: 'network_unavailable', verified: false, anchor: { status: 'pending' },
                                         onchain: { checked: true } })

    const card = await openAnchorTab(page, pendingAsset)
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    await expect(card.getByTestId('anchor-explanation')).toContainText('Nothing can be concluded right now')
    await expect(card.getByTestId('anchor-status-unconfirmed')).toBeVisible()
    await expect(card.getByText('Pending', { exact: true })).toBeVisible()             // the recorded status
    await expect(page.getByTestId('anchor-history').getByRole('listitem')).toHaveCount(1)
    await expect(page.getByTestId('anchor-history')).toContainText('Pending')
    await expect(page.getByTestId('anchor-pending-note')).toContainText('Until the ledger confirms it, it proves nothing.')

    // Re-checking the transaction cannot reach the network: it changes nothing
    await page.getByRole('button', { name: 'Refresh pending transaction' }).click()
    await expect(page.getByTestId('anchor-notice')).toHaveText('Anchor status: pending.')
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    const history = await api(request, `/assets/${pendingAsset.id}/stellar-anchors`, bearer(ctx.credentials.issuer_a))
    expect(history.body.map((a: { status: string }) => a.status)).toEqual(['pending'])
    expect((await api(request, verification, bearer(ctx.credentials.issuer_a))).body.verified).toBe(false)
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
  })

  test('the independent legal reviewer may read the anchor but is offered no transaction action, and is refused one', async ({ page, request }) => {
    const history = await api(request, `/assets/${pendingAsset.id}/stellar-anchors`, bearer(ctx.credentials.legal_a))
    expect(history.status).toBe(200)
    await signIn(page, ctx.credentials.legal_a)
    await openAsset(page, pendingAsset.stellar_asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    // Stage 10B-3: actions are offered by role (anchor:create); reading and re-checking stay available
    await expect(page.getByTestId('anchor-read-only-note')).toBeVisible()
    await expect(page.getByTestId('anchor-pending-note')).toContainText('not confirmed in a Stellar ledger')
    await expect(page.getByRole('button', { name: 'Refresh pending transaction' })).toHaveCount(0)
    await expect(page.getByRole('button', { name: /Anchor current evidence/ })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Re-check on Stellar' })).toBeVisible()
    // The server refuses the action itself
    const refused = await request.post(`${API_URL}/assets/${pendingAsset.id}/stellar-anchors/${history.body[0].id}/refresh`,
                                       { headers: bearer(ctx.credentials.legal_a) })
    expect(refused.status()).toBe(403)
  })
})
