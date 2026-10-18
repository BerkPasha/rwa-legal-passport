import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test, expect, ctx, signIn, openAsset, api, bearer, documentCard, API_URL } from '../support'

// B. Asset discovery and C. legal document inspection, against the seeded synthetic dataset.
const asset = ctx.assets!.approved
const draft = ctx.assets!.draft
const [prospectus, scanned] = asset.documents

test.describe('B. Asset discovery', () => {
  test('a synthetic RWA asset shows its metadata and status', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    await expect(page.getByRole('heading', { level: 1, name: 'Asset Detail' })).toBeVisible()
    await expect(page.getByText(asset.issuer_organization_name).first()).toBeVisible()
    const facts = page.locator('dl').first()
    await expect(facts.getByText('Testnet', { exact: true })).toBeVisible()
    await expect(facts.getByText('real estate', { exact: true })).toBeVisible()
    await expect(facts.getByText(asset.jurisdiction, { exact: true })).toBeVisible()
    await expect(page.getByText(asset.stellar_issuer_public_key)).toBeVisible()
    await expect(page.getByText('active', { exact: true })).toBeVisible()
    await expect(page.getByTestId('open-passport-link')).toHaveAttribute('href', new RegExp(asset.stellar_asset_code))
  })
})

test.describe('C. Legal document inspection', () => {
  test('documents show their version, file, hash and text layer', async ({ page, request }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    for (const doc of [prospectus, scanned]) {
      const card = documentCard(page, doc.title)
      await expect(card).toContainText(`v${doc.version_number}`)
      await expect(card.getByTestId('document-sha256')).toHaveText(`SHA-256 ${doc.sha256.slice(0, 16)}…`)
      await expect(card.getByTestId('document-sha256')).toHaveAttribute('title', doc.sha256)
      await expect(card.getByTestId('document-file')).toContainText(`${doc.filename} · `)
      await expect(card.getByTestId('document-file')).toContainText(`${doc.page_count} pages`)
    }
    // The scanned annex: its image-only page is identified, never guessed (Stage 10A-1)
    await expect(documentCard(page, scanned.title).getByTestId('text-layer-notice'))
      .toHaveText(`No text layer on page 2 of ${scanned.page_count} (for example scanned pages). OCR is not supported: quotations on that page can never be verified.`)
    await expect(documentCard(page, prospectus.title).getByTestId('text-layer-notice')).toHaveCount(0)
    const versions = await api(request, `/documents/${scanned.id}/versions`, bearer(ctx.credentials.issuer_a))
    expect(versions.body[0].text_layer.pages_without_text).toEqual([2])
  })

  test('the PDF is downloaded through the authorized API and matches its recorded SHA-256', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    const card = documentCard(page, prospectus.title)
    const download = page.waitForEvent('download')
    await card.getByTestId('download-document').click()
    const file = await download
    expect(file.suggestedFilename()).toBe(prospectus.filename)
    const bytes = readFileSync((await file.path())!)
    const digest = createHash('sha256').update(bytes).digest('hex')
    expect(digest).toBe(prospectus.sha256)
    expect(digest).toBe(ctx.fixtures!.trust_prospectus.sha256)   // the reviewed synthetic fixture itself
    await expect(card.getByTestId('download-status')).toHaveText(`Version ${prospectus.version_number} downloaded · SHA-256 verified`)
  })

  test('a removed or altered stored file is reported, never served', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 500/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, draft.stellar_asset_code)
    let downloads = 0
    page.on('download', () => { downloads += 1 })
    await documentCard(page, 'Missing file (synthetic)').getByTestId('download-document').click()
    await expect(documentCard(page, 'Missing file (synthetic)').getByTestId('download-error'))
      .toHaveText('Download failed: Stored file not found.')
    await documentCard(page, 'Altered file (synthetic)').getByTestId('download-document').click()
    await expect(documentCard(page, 'Altered file (synthetic)').getByTestId('download-error'))
      .toHaveText('Download failed: Document integrity check failed; the file was not served.')
    expect(downloads).toBe(0)
  })

  test("another tenant cannot download this tenant's PDF", async ({ request }) => {
    const response = await request.get(`${API_URL}/documents/${prospectus.id}/download`, { headers: bearer(ctx.credentials.issuer_b) })
    expect(response.status()).toBe(404)
    expect(await response.json()).toEqual({ detail: 'Document not found.' })
    const anonymous = await request.get(`${API_URL}/documents/${prospectus.id}/download`)
    expect(anonymous.status()).toBe(403)
  })

  test('the source text of exactly the reviewed version can be inspected page by page', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, asset.stellar_asset_code)
    await page.getByRole('link', { name: /Review →/ }).click()
    const panel = page.getByTestId('source-text-panel')
    await expect(panel.getByTestId('source-text-version'))
      .toHaveText(`Version ${prospectus.version_number} · ${prospectus.page_count} pages · current version`)
    await expect(panel.getByTestId('source-text')).toHaveAttribute('data-version-id', prospectus.version_id)
    await expect(panel.getByTestId('source-page')).toHaveCount(prospectus.page_count)
    await expect(panel.getByTestId('source-text-notice')).toContainText('no OCR')
    await expect(panel.getByTestId('source-page').nth(3)).toContainText('governed by the laws of England and Wales')
  })
})
