import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test, expect, ctx, signIn, openAsset, navLink, api, bearer, documentCard, openManualClaimForm, API_URL } from '../support'
import type { APIRequestContext, Page } from '@playwright/test'

// The complete manual legal claim lifecycle in one browser chain (Stage 10B-3, E2E_TESTING.md):
// a new asset, a new synthetic PDF uploaded through the form, a legal claim typed by the issuer
// reviewer from a quotation of that PDF and verified by the server, the review with two distinct
// reviewers, the Legal Passport, and the Stellar anchor state. AI_PROVIDER=disabled throughout;
// nothing is approved automatically and no Stellar transaction is submitted.
const fixture = ctx.fixtures!.manual_journey
const entry = ctx.manual_journey!
const issuerKey = ctx.spare_issuer_keys![1]
const ASSET = { code: 'E2EMAN', organization: 'E2E Harbourview Logistics Notes', title: 'Offering memorandum (synthetic)' }
const passportPath = `/passport/stellar-testnet/${ASSET.code}/${issuerKey}`
let assetId = ''
let documentId = ''
let extractionId = ''

const extractionsOf = async (request: APIRequestContext) =>
  (await api(request, `/documents/${documentId}/extractions`, bearer(ctx.credentials.issuer_a))).body as
    { id: string; source: string; status: string; ai_model: string; version_id: string;
      claims: { field_name: string; value: string; is_unknown: boolean; source_page: number | null;
                supporting_passage: string | null; evidence: { status: string; version_number: number } }[];
      approval_state: { state: string; current_approval_types: string[] } }[]

async function openReview(page: Page, token: string) {
  await signIn(page, token)
  await openAsset(page, ASSET.code)
  await documentCard(page, ASSET.title).getByRole('link', { name: /Review →/ }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'Manually Entered Legal Claims' })).toBeVisible()
}

test.describe.serial('Manual legal claim journey', () => {
  test('1–2. the platform operator registers a new asset (issuer reviewers cannot)', async ({ page, request }) => {
    const asset = { issuer_id: 'e2e-meridian', blockchain_network: 'stellar-testnet', stellar_asset_code: ASSET.code,
                    stellar_issuer_public_key: issuerKey, issuer_organization_name: ASSET.organization,
                    asset_type: 'bond', jurisdiction: 'IE' }
    // Registering assets is the operator's role (asset:create), not the issuer reviewer's
    const refused = await request.post(`${API_URL}/assets`, { headers: bearer(ctx.credentials.issuer_a), data: asset })
    expect(refused.status()).toBe(403)

    await signIn(page, ctx.credentials.admin)
    await navLink(page, 'Asset Registry').click()
    await page.getByRole('button', { name: 'Register Asset' }).click()
    const form = page.locator('form').filter({ has: page.getByLabel('Issuer ID') })
    await form.getByLabel('Issuer ID').fill(asset.issuer_id)
    await form.getByLabel('Network').selectOption(asset.blockchain_network)
    await form.getByLabel('Asset Type').selectOption(asset.asset_type)
    await form.getByLabel('Stellar Asset Code').fill(asset.stellar_asset_code)
    await form.getByLabel('Issuer Public Key').fill(asset.stellar_issuer_public_key)
    await form.getByLabel('Issuer Organization').fill(asset.issuer_organization_name)
    await form.getByLabel('Jurisdiction').fill(asset.jurisdiction)
    await form.getByRole('button', { name: 'Register Asset' }).click()
    await expect(page.getByRole('row').filter({ hasText: ASSET.code })).toHaveCount(1)
    const assets = (await api(request, '/assets', bearer(ctx.credentials.issuer_a))).body as { id: string; stellar_asset_code: string }[]
    assetId = assets.find(a => a.stellar_asset_code === ASSET.code)!.id
  })

  test('3–4. the issuer reviewer uploads a new synthetic PDF; version, pages and SHA-256 match the file', async ({ page, request }) => {
    expect(assetId).not.toBe('')
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, ASSET.code)
    await page.getByRole('button', { name: 'Upload Document' }).click()
    await page.getByLabel('Document Type').selectOption('prospectus')
    await page.getByLabel('Title', { exact: true }).fill(ASSET.title)
    await page.getByLabel('PDF File').setInputFiles(fixture.path)
    await page.getByRole('button', { name: 'Upload', exact: true }).click()
    const card = documentCard(page, ASSET.title)
    await expect(card.getByTestId('document-sha256')).toHaveAttribute('title', fixture.sha256)

    const docs = (await api(request, `/assets/${assetId}/documents`, bearer(ctx.credentials.issuer_a))).body
    expect(docs).toHaveLength(1)
    documentId = docs[0].id
    expect(docs[0].current_version).toMatchObject({ version_number: 1, sha256_hash: fixture.sha256, file_size: fixture.size })
    expect(docs[0].current_version.text_layer).toMatchObject({ page_count: fixture.pages, pages_without_text: [] })
    await expect(card.getByTestId('document-file')).toContainText(`${fixture.pages} pages`)
    const download = page.waitForEvent('download')
    await card.getByTestId('download-document').click()
    const bytes = readFileSync((await (await download).path())!)
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(fixture.sha256)
  })

  test('5–6. a manual claim: fabricated and wrong-page quotations are refused, the exact quotation is verified', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 422/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, ASSET.code)
    // External AI stays disabled; the claim is typed by the user
    await expect(page.getByTestId('ai-status')).toContainText('External AI extraction is disabled')
    await expect(documentCard(page, ASSET.title).getByRole('button', { name: 'Run AI Extraction' })).toBeDisabled()

    const form = await openManualClaimForm(page, ASSET.title)
    await expect(form.getByLabel('Source page').getByRole('option'))
      .toHaveText(['Select the page that holds the quotation…', 'Page 1', 'Page 2', 'Page 3'])
    await form.getByLabel('Legal field').selectOption(entry.field_name)
    await form.getByLabel('Source page').selectOption(String(entry.page))
    await expect(form.getByTestId('manual-claim-page-text')).toContainText(entry.page_text)
    await form.getByLabel('Claim', { exact: true }).fill(entry.value)
    const quotation = form.getByLabel('Exact quotation from the selected page')
    const submit = form.getByRole('button', { name: 'Create Draft Claim' })
    const error = form.getByTestId('manual-claim-error')

    await quotation.fill(entry.fabricated_quotation)
    await submit.click()
    await expect(error).toContainText('Jurisdiction: quotation not found in this document version.')
    await expect(error).toContainText('Nothing was stored.')
    expect(await extractionsOf(request)).toEqual([])

    await form.getByLabel('Source page').selectOption('1')
    await quotation.fill(entry.quotation)
    await submit.click()
    await expect(error).toContainText('Jurisdiction: quotation is not on the cited page.')
    await expect(error).toContainText(`it appears on page ${entry.page}`)
    expect(await extractionsOf(request)).toEqual([])

    await form.getByLabel('Source page').selectOption(String(entry.page))
    await submit.click()
    await expect(page.getByRole('heading', { level: 2, name: 'Manually Entered Legal Claims' })).toBeVisible()
    await expect(page.getByTestId('manual-claim-created')).toContainText('Nothing is approved')
    await expect(page.getByTestId('extraction-origin')).toHaveText('Manual entry · no AI')
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Draft')
    await expect(page.getByTestId('disclaimer-banner').first()).toContainText('MANUALLY ENTERED DRAFT')
    await expect(page.getByTestId(`claim-row-${entry.field_name}`).getByTestId('evidence-badge-verified'))
      .toHaveText(`Quote found on page ${entry.page}`)
    await expect(page.getByTestId('count-quote-found')).toHaveText('1')
    await expect(page.getByTestId('count-unknown')).toHaveText('6')
    await expect(page.getByTestId('extraction-version')).toHaveText('Document version 1')

    const [created] = await extractionsOf(request)
    extractionId = created.id
    expect([created.source, created.status, created.ai_model]).toEqual(['manual', 'draft', 'manual-entry (no AI model)'])
    const stored = created.claims.find(c => c.field_name === entry.field_name)!
    expect([stored.value, stored.is_unknown, stored.source_page, stored.supporting_passage]).toEqual(
      [entry.value, false, entry.page, entry.quotation])                       // exactly as typed
    expect([stored.evidence.status, stored.evidence.version_number]).toEqual(['verified', 1])
    expect(created.approval_state).toMatchObject({ state: 'not_approved', current_approval_types: [] })
    await expect(page.getByTestId('source-text')).toHaveAttribute('data-version-id', created.version_id)
    expect((await api(request, `/extractions/${extractionId}/approvals`, bearer(ctx.credentials.issuer_a))).body).toEqual([])
  })

  test('7. the new claim is unapproved: no passport is served', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 40[49]/)
    const served = await request.get(`${API_URL}${passportPath}`, { headers: { 'X-API-Key': ctx.credentials.inst_a } })
    expect(served.status()).not.toBe(200)
    await signIn(page, ctx.credentials.issuer_a, ctx.credentials.inst_a)
    await openAsset(page, ASSET.code)
    await page.getByTestId('open-passport-link').click()
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    await expect(page.getByText((await served.json()).detail)).toBeVisible()
    await expect(page.getByTestId('passport-card')).toHaveCount(0)
  })

  test('8a. the issuer reviewer starts the review and gives the issuer attestation', async ({ page, request }) => {
    await openReview(page, ctx.credentials.issuer_a)
    await page.getByRole('button', { name: 'Start Review' }).click()
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Under Review')
    await page.getByLabel('Approval notes (optional)').fill('E2E issuer attestation of a manual claim (synthetic)')
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Approved')
    await expect(page.getByRole('heading', { name: 'Approvals (1)' })).toBeVisible()
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Issuer Attestation' })).toHaveCount(1)
    await expect(page.getByText('Add Your Approval')).toHaveCount(0)

    // The same reviewer cannot approve twice
    const again = await request.post(`${API_URL}/extractions/${extractionId}/approvals`,
                                     { headers: bearer(ctx.credentials.issuer_a), data: {} })
    expect([again.status(), (await again.json()).approval_refusal]).toEqual([409, 'duplicate_approval'])
    // The passport now states the issuer's attestation only
    const passport = await (await request.get(`${API_URL}${passportPath}`, { headers: { 'X-API-Key': ctx.credentials.inst_a } })).json()
    expect(passport.legal_rights.find((r: { field_name: string }) => r.field_name === entry.field_name)).toMatchObject(
      { value_origin: 'manual_entry', reviewer_attestation_status: 'issuer_attested', independently_verified: false })
  })

  test('8b. the independent legal reviewer adds the second approval; reviewers are distinct', async ({ page, request }) => {
    // Neither another tenant's reviewer nor an institutional key can act on it
    const foreign = await request.post(`${API_URL}/extractions/${extractionId}/approvals`,
                                       { headers: bearer(ctx.credentials.issuer_b), data: {} })
    expect(foreign.status()).toBe(404)
    const institution = await request.post(`${API_URL}/extractions/${extractionId}/approvals`,
                                           { headers: { 'X-API-Key': ctx.credentials.inst_a }, data: {} })
    expect(institution.status()).toBe(403)

    await openReview(page, ctx.credentials.legal_a)
    await expect(page.getByText('Add Your Approval')).toBeVisible()
    await expect(page.getByTestId('add-approval-note')).toContainText('independent legal review')
    await page.getByLabel('Approval notes (optional)').fill('E2E independent legal review of a manual claim (synthetic)')
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByRole('heading', { name: 'Approvals (2)' })).toBeVisible()
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Independent Legal Review' })).toHaveCount(1)

    const approvals = (await api(request, `/extractions/${extractionId}/approvals`, bearer(ctx.credentials.legal_a))).body as
      { reviewer_id: string; reviewer_role: string; approval_type: string }[]
    expect(approvals.map(a => [a.reviewer_role, a.approval_type]).sort()).toEqual([
      ['independent_legal_reviewer', 'legal_verification'], ['issuer_reviewer', 'issuer_attestation']])
    expect(new Set(approvals.map(a => a.reviewer_id)).size).toBe(2)
  })

  test('9–11. the institution reads the passport; claim, evidence, version and approvals match the backend', async ({ page, request }) => {
    const passport = await (await request.get(`${API_URL}${passportPath}`, { headers: { 'X-API-Key': ctx.credentials.inst_a } })).json()
    const right = passport.legal_rights.find((r: { field_name: string }) => r.field_name === entry.field_name)
    expect(right).toMatchObject({
      value: entry.value, source_page: entry.page, supporting_passage: entry.quotation, document_id: documentId,
      document_version_number: 1, verified: true, independently_verified: true, value_origin: 'manual_entry',
      reviewer_attestation_status: 'independent_reviewer_attested',
    })
    expect(passport.legal_rights.filter((r: { is_unknown: boolean }) => r.is_unknown)).toHaveLength(6)
    expect(passport.extraction_metadata).toMatchObject({ extraction_id: extractionId, extraction_source: 'manual' })
    expect(passport.verification_summary).toMatchObject({ issuer_attestation_recorded: true, legal_reviewer_approval_recorded: true })

    await signIn(page, ctx.credentials.issuer_a, ctx.credentials.inst_a)
    await openAsset(page, ASSET.code)
    await page.getByTestId('open-passport-link').click()
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    const card = page.getByTestId('passport-card')
    await expect(card).toContainText(`Legal Passport — v${passport.extraction_metadata.passport_version}`)
    await expect(card.getByText(entry.value, { exact: true })).toBeVisible()
    const index = passport.legal_rights.indexOf(right)
    await expect(card.getByTestId('passport-right-badge').nth(index)).toHaveText('Independent reviewer attested')
    await expect(card.getByTestId('passport-right-badge').filter({ hasText: 'Unknown' })).toHaveCount(6)
    await expect(card.getByTestId('value-origin')).toHaveText('Entered manually by a user of the issuer from the cited page (no AI)')
    await expect(card.getByTestId('passport-attestation')).toHaveText(
      'Approved by the issuer and attested by an independent legal reviewer (internal attestations, not a legal opinion)')
    await expect(card.getByTestId('passport-passage-location')).toContainText(`page ${entry.page}`)
    await expect(card.getByTestId('passport-passage-location')).toContainText(`${ASSET.title} version 1`)
    await expect(card.getByTestId('passport-extraction-source')).toHaveText('Manual entry (no AI model)')
    await expect(card.getByTestId('disclaimer-banner')).toBeVisible()
    await expect(card).not.toContainText('e2e-issuer-reviewer-a')   // reviewer roles only, no identities
  })

  test('12. the new asset is not anchored, and nothing is submitted', async ({ page, request }) => {
    const anchorPosts: string[] = []
    page.on('request', r => { if (r.method() === 'POST' && r.url().includes('/stellar-anchors')) anchorPosts.push(r.url()) })
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, ASSET.code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
    await expect(page.getByText('No anchoring transaction recorded yet.')).toBeVisible()
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    const backend = (await api(request, `/assets/${assetId}/stellar-anchors/verification`, bearer(ctx.credentials.issuer_a))).body
    expect(backend).toMatchObject({ result: 'not_anchored', verified: false, current_passport_available: true })
    expect((await api(request, `/assets/${assetId}/stellar-anchors`, bearer(ctx.credentials.issuer_a))).body).toEqual([])
    expect(anchorPosts).toEqual([])
  })
})
