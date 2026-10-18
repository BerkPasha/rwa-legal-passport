import { test, expect, ctx, signIn, openAsset, setCredentials, navLink, API_URL } from '../support'
import type { Page } from '@playwright/test'

// F. Legal Passport, read with an institutional API key, compared field by field with the backend.
const approved = ctx.assets!.approved
const draft = ctx.assets!.draft

async function passportJson(page: Page, asset: typeof approved, key: string) {
  const path = `/passport/${asset.blockchain_network}/${asset.stellar_asset_code}/${asset.stellar_issuer_public_key}`
  const response = await page.request.get(`${API_URL}${path}`, { headers: { 'X-API-Key': key } })
  return { status: response.status(), body: await response.json() }
}

async function lookup(page: Page, asset: typeof approved, key: string) {
  await page.goto('/api-docs')
  await setCredentials(page, { apiKey: key })
  await navLink(page, 'Legal Passport').click()
  await page.getByPlaceholder('MRPROP').fill(asset.stellar_asset_code)
  await page.getByPlaceholder('G…').fill(asset.stellar_issuer_public_key)
  await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
}

test.describe('F. Legal Passport', () => {
  test('the passport shows the approved claims, their evidence and the attestations exactly as served', async ({ page }) => {
    // From the asset page: the link carries the asset identity into the lookup form
    await signIn(page, ctx.credentials.issuer_a, ctx.credentials.inst_a)
    await openAsset(page, approved.stellar_asset_code)
    await page.getByTestId('open-passport-link').click()
    await expect(page.getByPlaceholder('MRPROP')).toHaveValue(approved.stellar_asset_code)
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()

    const served = await passportJson(page, approved, ctx.credentials.inst_a)
    expect(served.status).toBe(200)
    const card = page.getByTestId('passport-card')
    await expect(card).toContainText(`Legal Passport — v${served.body.extraction_metadata.passport_version}`)
    await expect(card).toContainText(approved.stellar_asset_code)
    await expect(card).toContainText('Stellar Testnet')
    await expect(card.getByTestId('disclaimer-banner')).toBeVisible()
    await expect(card.getByTestId('verification-meaning')).toHaveText(served.body.verification_summary.meaning)

    // Every right: the badge follows the backend's flags; evidence alone is never "independent"
    const badges = card.getByTestId('passport-right-badge')
    await expect(badges).toHaveCount(served.body.legal_rights.length)
    for (const [i, right] of served.body.legal_rights.entries()) {
      const expected = right.is_unknown ? 'Unknown' : right.independently_verified ? 'Independent reviewer attested'
        : right.verified ? 'Evidence cited' : 'Unverified'
      await expect(badges.nth(i)).toHaveText(expected)
    }
    await expect(card.getByTestId('passport-passage-location').first())
      .toContainText(`page ${served.body.legal_rights[0].source_page}`)

    // Approval chain: reviewer roles only (no identities), with the two attestation types
    const chain = card.locator('li').filter({ has: page.getByTestId('approval-type-badge') })
    await expect(chain).toHaveCount(served.body.approvals.length)
    await expect(chain.filter({ hasText: 'Issuer Reviewer' }).getByTestId('approval-type-badge')).toHaveText('Issuer Attestation')
    await expect(chain.filter({ hasText: 'Independent Legal Reviewer' }).getByTestId('approval-type-badge'))
      .toHaveText('Independent Legal Review')
    expect([served.body.verification_summary.issuer_attestation_recorded,
            served.body.verification_summary.legal_reviewer_approval_recorded]).toEqual([true, true])
    await expect(card).not.toContainText('e2e-issuer-reviewer-a')   // no reviewer identity

    // The commitment card on the passport page: the same honest offline result
    await expect(page.getByTestId('anchor-card').getByTestId('anchor-result-badge')).toHaveText('Not anchored')
  })

  test('a draft without approval has no passport', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 404/, /status of 409/)
    const served = await passportJson(page, draft, ctx.credentials.inst_a)
    expect(served.status).not.toBe(200)
    await lookup(page, draft, ctx.credentials.inst_a)
    await expect(page.getByTestId('passport-card')).toHaveCount(0)
    await expect(page.getByText(served.body.detail)).toBeVisible()
  })

  test("another tenant's institutional key cannot read the passport", async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 404/)
    const served = await passportJson(page, approved, ctx.credentials.inst_b)
    expect(served.status).toBe(404)
    await lookup(page, approved, ctx.credentials.inst_b)
    await expect(page.getByTestId('passport-card')).toHaveCount(0)
    await expect(page.getByText(served.body.detail)).toBeVisible()
  })

  test('without an institutional key the passport is refused', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 40[13]/)
    await page.goto('/passport')
    await page.getByPlaceholder('MRPROP').fill(approved.stellar_asset_code)
    await page.getByPlaceholder('G…').fill(approved.stellar_issuer_public_key)
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    await expect(page.getByTestId('passport-card')).toHaveCount(0)
    await expect(page.locator('.bg-red-50').first()).toBeVisible()
  })
})
