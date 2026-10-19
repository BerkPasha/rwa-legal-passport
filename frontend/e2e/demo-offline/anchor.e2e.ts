import { existsSync } from 'node:fs'
import { resolve } from 'node:path'
import type { APIRequestContext, Page } from '@playwright/test'
import { test, expect, ctx, signIn, openAsset, setCredentials, navLink, api, bearer, documentCard, openManualClaimForm } from '../support'

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

// G. The MVP update journey (DEMO.md): a new version of the prospectus invalidates its approvals, the
// Passport History says why and what needs reapproval, the issuer and the legal reviewer approve a
// claim of the new version, and version 1's anchor is shown as not covering the current evidence.
// It runs after E (same file, one worker): it changes this run's copy of the dataset, never the dataset.
const TITLE = 'Prospectus'
const V2_PDF = resolve(process.cwd(), '..', 'tests', 'fixtures', 'real_pdfs', 'trust_prospectus.pdf')
const QUOTE = 'Net rental income shall be distributed quarterly'
const CLAIM = 'Net rental income is distributed quarterly, pro rata.'

const passportHistory = async (request: APIRequestContext) => (await api(request,
  `/passport/${asset.network}/${asset.asset_code}/${asset.issuer_key}/history`, { 'X-API-Key': ctx.credentials.inst })).body

// The asset page's "View Passport History" link: the passport page runs the lookup by itself
async function openHistory(page: Page) {
  await openAsset(page, asset.asset_code)
  await page.getByTestId('open-passport-history-link').click()
  return page.getByTestId('passport-history')
}

test.describe.serial('G. A new document version, reapproval and the outdated anchor', () => {
  test('1–2. the asset and its current Legal Passport', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer, ctx.credentials.inst)
    const history = await openHistory(page)
    await expect(page.getByTestId('passport-card')).toContainText(`${TITLE} (v1)`)
    await expect(history.getByTestId('history-summary')).toHaveText('A current Legal Passport is available.')
    await expect(history.getByTestId('history-requirement')).toHaveText('Current version approved')
  })

  test('3–5. a new version invalidates the approvals; the history says why and what needs reapproval', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 409/)   // the passport, while no approval is current
    expect(existsSync(V2_PDF), V2_PDF).toBe(true)
    await signIn(page, ctx.credentials.issuer, ctx.credentials.inst)
    await openAsset(page, asset.asset_code)
    const card = documentCard(page, TITLE)
    await card.getByTestId('upload-new-version').click()
    const form = card.getByTestId('new-version-form')
    await form.getByLabel('PDF File').setInputFiles(V2_PDF)
    await form.getByRole('button', { name: 'Upload Version' }).click()
    const status = card.getByTestId('version-upload-status')
    await expect(status).toContainText('Version 2 uploaded and is now the current version')
    await expect(status).toContainText('The approvals of the earlier version are no longer current')
    await expect(card.getByTestId('approval-state-badge')).toHaveText('Superseded version')

    await status.getByRole('link', { name: 'View the Passport History →' }).click()
    const history = page.getByTestId('passport-history')
    await expect(page.getByText(/^409: The legal approval for this asset is no longer current/)).toBeVisible()
    await expect(page.getByTestId('passport-not-current-note')).toContainText('Passport History below')
    await expect(page.getByTestId('passport-card')).toHaveCount(0)
    await expect(history.getByTestId('history-summary')).toContainText('Reapproval is required.')
    const versions = history.getByTestId('history-version')
    await expect(versions).toHaveCount(2)
    await expect(versions.nth(0)).toHaveAttribute('data-version', '1')
    await expect(versions.nth(0).getByTestId('approval-state-badge')).toHaveText('Superseded version')
    await expect(versions.nth(0).getByTestId('history-invalidation')).toContainText('A newer document version exists')
    await expect(versions.nth(1)).toContainText('Never approved.')
    await expect(history.getByTestId('history-requirement')).toHaveText('Reapproval required')
  })

  test('6a. the issuer reviewer enters a claim from version 2 and attests it', async ({ page, request }) => {
    const documents = (await api(request, `/assets/${asset.id}/documents`, bearer(ctx.credentials.issuer))).body as
      { id: string; title: string }[]
    const documentId = documents.find(d => d.title === TITLE)!.id
    const text = (await api(request, `/documents/${documentId}/versions/2/text`, bearer(ctx.credentials.issuer))).body as
      { pages: { page_number: number; text_content: string }[] }
    const sourcePage = text.pages.find(p => p.text_content.split(/\s+/).join(' ').includes(QUOTE))!.page_number

    await signIn(page, ctx.credentials.issuer)
    await openAsset(page, asset.asset_code)
    const form = await openManualClaimForm(page, TITLE)
    await expect(form).toContainText('Version 2 of this document')
    await form.getByLabel('Legal field').selectOption('income_rights')
    await form.getByLabel('Source page').selectOption(String(sourcePage))
    await form.getByLabel('Claim', { exact: true }).fill(CLAIM)
    await form.getByLabel('Exact quotation from the selected page').fill(QUOTE)
    await form.getByRole('button', { name: 'Create Draft Claim' }).click()
    await expect(page.getByRole('heading', { level: 2, name: 'Manually Entered Legal Claims' })).toBeVisible()
    await expect(page.getByTestId('extraction-version')).toHaveText('Document version 2')
    await expect(page.getByTestId('claim-row-income_rights').getByTestId('evidence-badge-verified'))
      .toHaveText(`Quote found on page ${sourcePage}`)
    await page.getByRole('button', { name: 'Start Review' }).click()
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Under Review')
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByRole('heading', { name: 'Approvals (1)' })).toBeVisible()
  })

  test('6b. the independent legal reviewer approves; the passport is current again, on version 2', async ({ page }) => {
    await signIn(page, ctx.credentials.legal, ctx.credentials.inst)
    await openAsset(page, asset.asset_code)
    await documentCard(page, TITLE).getByRole('link', { name: /Review →/ }).filter({ hasText: 'Manual entry' }).click()
    await expect(page.getByText('Add Your Approval')).toBeVisible()
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByRole('heading', { name: 'Approvals (2)' })).toBeVisible()

    const history = await openHistory(page)
    await expect(page.getByTestId('passport-card')).toContainText(`${TITLE} (v2)`)
    await expect(history.getByTestId('history-summary')).toHaveText('A current Legal Passport is available.')
    await expect(history.getByTestId('history-requirement')).toHaveText('Current version approved')
    const versions = history.getByTestId('history-version')
    await expect(versions.nth(0).getByTestId('approval-state-badge')).toHaveText('Superseded version')
    await expect(versions.nth(1)).toContainText('Used by the current Legal Passport')
  })

  test("7–8. the evidence manifest changed: version 1's anchor is shown as outdated, never as verification", async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 503/)   // the refused anchoring request: this server is verification-only
    const current = (await passportHistory(request)).current_manifest_digest as string
    expect(current).toMatch(/^[0-9a-f]{64}$/)
    expect(current).not.toBe(anchor.manifest_digest)
    const backend = await api(request, `/assets/${asset.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer))
    expect(backend.body).toMatchObject({ current_manifest_digest: current, evidence_matches_anchor: false, verified: false })
    expect(backend.body.anchor.manifest_digest).toBe(anchor.manifest_digest)

    await signIn(page, ctx.credentials.issuer)
    await openAsset(page, asset.asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    const card = page.getByTestId('anchor-card')
    await expect(card.getByTestId('anchor-result-badge')).toHaveText('Stellar network unavailable')
    await expect(card.getByTestId('anchor-evidence-match')).toHaveText('Current evidence does not match the recorded commitment')
    await expect(card.getByTestId('anchor-digests')).toContainText(anchor.manifest_digest)
    await expect(card.getByTestId('anchor-digests')).toContainText(current)
    await expect(page.getByTestId('anchor-history-match')).toHaveText(['Does not match the current evidence'])
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)

    // Offered for the new evidence; this server is verification-only, so nothing can be submitted
    page.once('dialog', dialog => dialog.accept())
    await page.getByRole('button', { name: 'Anchor current evidence on Testnet' }).click()
    await expect(page.getByTestId('anchor-action-error')).toContainText('Anchoring is disabled on this server')
    const anchors = (await api(request, `/assets/${asset.id}/stellar-anchors`, bearer(ctx.credentials.issuer))).body as unknown[]
    expect(anchors).toHaveLength(1)
  })
})
