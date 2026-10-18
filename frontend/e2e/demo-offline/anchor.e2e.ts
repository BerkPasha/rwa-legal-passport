import { test, expect, ctx, signIn, openAsset, setCredentials, navLink, api, bearer } from '../support'

// E/F. The demo dataset's real Stellar Testnet commitment, with the backend's outbound network blocked.
// A real anchor record exists, but nothing can be checked against the ledger: the interface must say
// so, and must never present the stored record as a verified commitment.
const asset = ctx.asset!
const anchor = ctx.anchor!

test.describe('E. Real anchor record, network unavailable', () => {
  test('the commitment is reported as unverifiable, never as verified', async ({ page, request }) => {
    const backend = await api(request, `/assets/${asset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer))
    expect([backend.body.result, backend.body.verified]).toEqual(['network_unavailable', false])
    expect(backend.body.anchor.transaction_hash).toBe(anchor.transaction_hash)

    await signIn(page, ctx.credentials.issuer)
    await openAsset(page, asset.asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const card = page.getByTestId('anchor-card')
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    await expect(card.getByTestId('anchor-explanation')).toContainText('Nothing can be concluded right now')
    await expect(card.getByTestId('anchor-tx-hash')).toContainText(anchor.transaction_hash.slice(0, 8))
    await expect(card.getByTestId('anchor-not-legal')).toBeVisible()
    // The stored status (Confirmed) is labelled as the server's record, not as a ledger check
    await expect(card.getByTestId('anchor-status-unconfirmed')).toBeVisible()
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    await expect(page.getByTestId('anchor-evidence-match')).not.toContainText('verified on Stellar')
    // The evidence still matches the recorded digest, so nothing is offered for anchoring
    await expect(page.getByRole('button', { name: 'Anchor current evidence on Testnet' })).toHaveCount(0)
  })

  test('the passport page shows the same unverifiable commitment', async ({ page }) => {
    await page.goto('/api-docs')
    await setCredentials(page, { apiKey: ctx.credentials.inst })
    await navLink(page, 'Legal Passport').click()
    await page.getByPlaceholder('MRPROP').fill(asset.asset_code)
    await page.getByPlaceholder('G…').fill(asset.issuer_key)
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    await expect(page.getByTestId('passport-card')).toContainText(asset.asset_code)
    await expect(page.getByTestId('anchor-card').getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
  })
})
