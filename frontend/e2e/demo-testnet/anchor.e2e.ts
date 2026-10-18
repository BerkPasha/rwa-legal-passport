import { test, expect, ctx, signIn, openAsset, api, bearer, API_URL } from '../support'

// E. The demo dataset's commitment against the live Stellar Testnet (read-only; run only with
// scripts/e2e_run.py --testnet-readonly). "Verified" may appear only when the public ledger itself
// confirms it, which this test checks independently on Horizon.
const asset = ctx.asset!
const anchor = ctx.anchor!
const HORIZON = 'https://horizon-testnet.stellar.org'

// Serial: the second test changes the asset record of this run's disposable copy of the dataset
test.describe.serial('E. Real Stellar Testnet verification (read-only)', () => {
  test('the public ledger holds the commitment the interface reports as verified', async ({ page, request }) => {
    // Independent check on public Horizon: the transaction exists, succeeded and carries the digest
    const tx = await request.get(`${HORIZON}/transactions/${anchor.transaction_hash}`)
    expect(tx.status()).toBe(200)
    const onchain = await tx.json()
    expect(onchain.successful).toBe(true)
    expect(onchain.ledger).toBe(anchor.ledger)
    expect(onchain.source_account).toBe(anchor.anchor_account)
    expect(onchain.memo_type).toBe('hash')
    expect(Buffer.from(onchain.memo, 'base64').toString('hex')).toBe(anchor.manifest_digest)

    // The backend's verification agrees
    const backend = await api(request, `/assets/${asset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer))
    expect([backend.body.result, backend.body.verified]).toEqual(['verified', true])
    expect(backend.body.current_manifest_digest).toBe(anchor.manifest_digest)

    // And so does the interface
    await signIn(page, ctx.credentials.issuer)
    await openAsset(page, asset.asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const card = page.getByTestId('anchor-card')
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Commitment verified on Stellar Testnet')
    await expect(card.getByTestId('anchor-tx-hash')).toContainText(anchor.transaction_hash.slice(0, 8))
    await expect(card.getByTestId('anchor-explorer-link')).toHaveAttribute('href', anchor.explorer)
    await expect(card.getByTestId('anchor-evidence-match')).toHaveText('Current evidence matches the recorded commitment')
    await expect(card.getByTestId('anchor-onchain-checks')).toContainText('Memo hash equals the digest')
    await expect(card.getByTestId('anchor-not-legal')).toBeVisible()
    await expect(card.getByTestId('anchor-status-unconfirmed')).toHaveCount(0)
  })

  test('after the asset record changes, the same valid commitment no longer covers the evidence', async ({ page, request }) => {
    // A material change (jurisdiction) of this run's copy: the approvals stop being current
    const patched = await request.patch(`${API_URL}/assets/${asset.id}`,
                                        { headers: bearer(ctx.credentials.admin), data: { jurisdiction: 'E2E-CHANGED' } })
    expect(patched.status()).toBe(200)
    const backend = await api(request, `/assets/${asset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer))
    expect(backend.body).toMatchObject({ result: 'evidence_changed', verified: false, evidence_matches_anchor: false })
    // The ledger still holds the original, valid commitment
    expect(backend.body.onchain).toMatchObject({ checked: true, transaction_found: true, successful: true,
                                                 source_account_matches: true, memo_matches: true, operation_matches: true })

    await signIn(page, ctx.credentials.issuer)
    await openAsset(page, asset.asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const card = page.getByTestId('anchor-card')
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Evidence changed since anchoring')
    await expect(card.getByTestId('anchor-evidence-match')).toHaveText('Current evidence does not match the recorded commitment')
    await expect(card.getByTestId('anchor-status-unconfirmed')).toBeVisible()
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
  })
})
