import { test, expect, ctx, signIn, openAsset, api, bearer } from '../support'

// E. Stellar Evidence Anchor, offline. The synthetic server has no anchoring account and its outbound
// HTTP is blocked, so no real verification is possible here: the interface must show exactly the
// backend's result and never a blockchain success.
const asset = ctx.assets!.approved

test.describe('E. Stellar Evidence Anchor (no anchor, network blocked)', () => {
  test('an asset without a commitment is shown as not anchored, never as verified', async ({ page, request }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const card = page.getByTestId('anchor-card')
    const backend = await api(request, `/assets/${asset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer_a))
    expect([backend.body.result, backend.body.verified]).toEqual(['not_anchored', false])
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
    await expect(card.getByTestId('anchor-explanation')).toContainText('No evidence commitment has been recorded')
    await expect(card.getByTestId('anchor-not-legal')).toBeVisible()
    await expect(card.getByTestId('anchor-tx-hash')).toHaveCount(0)
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    await expect(page.getByText('No anchoring transaction recorded yet.')).toBeVisible()
  })

  test('anchoring cannot submit anything from this server', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 503/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const anchorRequests: string[] = []
    page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/stellar-anchors')) anchorRequests.push(r.url()) })

    // Declining the confirmation sends nothing
    page.once('dialog', dialog => dialog.dismiss())
    await page.getByRole('button', { name: 'Anchor current evidence on Testnet' }).click()
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
    expect(anchorRequests).toEqual([])

    // Confirming reaches the backend, which refuses: no anchoring account is configured
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: 'Anchor current evidence on Testnet' }).click()
    await expect(page.getByTestId('anchor-action-error')).toBeVisible()
    expect(anchorRequests).toHaveLength(1)
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
    const history = await api(request, `/assets/${asset.id}/stellar-anchors`, bearer(ctx.credentials.issuer_a))
    expect(history.body).toEqual([])
  })

  test('the Stellar network is shown as unavailable when Horizon cannot be reached', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 503/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    await page.getByRole('button', { name: 'Blockchain Status' }).click()
    await expect(page.getByText('Horizon Unavailable')).toBeVisible()
    await expect(page.getByText('Asset found on Stellar network')).toHaveCount(0)
  })
})
