import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test, expect, ctx, signIn, openAsset, navLink, api, bearer, documentCard, API_URL } from '../support'
import type { Page } from '@playwright/test'

// The institutional Legal Passport journey of Stage 10B-2, on seeded claims (E2E_TESTING.md).
// Every step goes through the interface; the backend is read directly only to compare.
//
// With AI_PROVIDER=disabled the AI path creates no claim for the uploaded PDF (asserted below). The
// review, approval and passport steps here continue on E2EREV, a seeded draft of scripted claims
// (test data, labelled as such) of the same synthetic PDF. The complete lifecycle on a newly
// uploaded PDF, with a claim entered manually, is manual-claim.e2e.ts (Stage 10B-3). Nothing is
// approved automatically: both approvals are given in the browser, by two different reviewers.
const fixture = ctx.fixtures!.trust_prospectus
const review = ctx.assets!.review
const JOURNEY = { code: 'E2EJRN', organization: 'E2E Meridian Journey Trust', title: 'Journey prospectus (synthetic)' }
let journeyAssetId = ''

async function openReview(page: Page, token: string) {
  await signIn(page, token)
  await openAsset(page, review.stellar_asset_code)
  await page.getByRole('link', { name: /Review →/ }).click()
  await expect(page.getByRole('heading', { level: 2, name: 'AI Extraction Result' })).toBeVisible()
}

test.describe.serial('Legal Passport journey', () => {
  test('1–2. the platform operator registers a new asset through the form', async ({ page, request }) => {
    await signIn(page, ctx.credentials.admin)
    await expect(page.getByTestId('auth-status')).toContainText('Platform Operator · all issuers')
    await navLink(page, 'Asset Registry').click()
    await page.getByRole('button', { name: 'Register Asset' }).click()
    const form = page.locator('form').filter({ has: page.getByLabel('Issuer ID') })
    await form.getByLabel('Issuer ID').fill('e2e-meridian')
    await form.getByLabel('Network').selectOption('stellar-testnet')
    await form.getByLabel('Asset Type').selectOption('real_estate')
    await form.getByLabel('Stellar Asset Code').fill(JOURNEY.code)
    await form.getByLabel('Issuer Public Key').fill(ctx.spare_issuer_keys![0])
    await form.getByLabel('Issuer Organization').fill(JOURNEY.organization)
    await form.getByLabel('Jurisdiction').fill('GB')
    await form.getByRole('button', { name: 'Register Asset' }).click()
    await expect(page.getByRole('row').filter({ hasText: JOURNEY.code })).toHaveCount(1)

    const assets = (await api(request, '/assets', bearer(ctx.credentials.admin))).body as
      { id: string; stellar_asset_code: string; issuer_id: string; stellar_issuer_public_key: string; status: string }[]
    const created = assets.find(a => a.stellar_asset_code === JOURNEY.code)!
    expect(created).toMatchObject({ issuer_id: 'e2e-meridian', stellar_issuer_public_key: ctx.spare_issuer_keys![0] })
    journeyAssetId = created.id
    // The tenant's reviewer sees it; the other tenant does not
    expect((await api(request, `/assets/${created.id}`, bearer(ctx.credentials.issuer_a))).status).toBe(200)
    expect((await api(request, `/assets/${created.id}`, bearer(ctx.credentials.issuer_b))).status).toBe(404)
  })

  test('3–4. the issuer reviewer uploads the PDF; version, size, pages and SHA-256 match the file', async ({ page, request }) => {
    expect(journeyAssetId).not.toBe('')
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, JOURNEY.code)
    await expect(page.getByText('No documents uploaded yet.')).toBeVisible()
    await page.getByRole('button', { name: 'Upload Document' }).click()
    await page.getByLabel('Document Type').selectOption('prospectus')
    await page.getByLabel('Title', { exact: true }).fill(JOURNEY.title)
    await page.getByLabel('PDF File').setInputFiles(fixture.path)
    await page.getByRole('button', { name: 'Upload', exact: true }).click()

    const card = documentCard(page, JOURNEY.title)
    await expect(card).toBeVisible()
    const docs = (await api(request, `/assets/${journeyAssetId}/documents`, bearer(ctx.credentials.issuer_a))).body
    expect(docs).toHaveLength(1)
    const version = docs[0].current_version
    expect(version).toMatchObject({ version_number: 1, sha256_hash: fixture.sha256, file_size: fixture.size,
                                    original_filename: 'trust_prospectus.pdf' })
    expect(version.text_layer).toMatchObject({ page_count: fixture.pages, pages_without_text: [] })
    await expect(card).toContainText('v1')
    await expect(card.getByTestId('document-sha256')).toHaveText(`SHA-256 ${fixture.sha256.slice(0, 16)}…`)
    await expect(card.getByTestId('document-sha256')).toHaveAttribute('title', fixture.sha256)
    await expect(card.getByTestId('document-file')).toContainText(`trust_prospectus.pdf · ${(fixture.size / 1024).toFixed(1)} KB`)
    await expect(card.getByTestId('document-file')).toContainText(`${fixture.pages} pages`)
    await expect(card.getByTestId('text-layer-notice')).toHaveCount(0)

    // The stored file is the uploaded file: downloaded through the authorized API and hashed again
    const download = page.waitForEvent('download')
    await card.getByTestId('download-document').click()
    const bytes = readFileSync((await (await download).path())!)
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(fixture.sha256)
    await expect(card.getByTestId('download-status')).toHaveText('Version 1 downloaded · SHA-256 verified')
  })

  test('5–6. with AI disabled the AI path creates no claim for the uploaded PDF; manual entry is offered', async ({ page, request }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, JOURNEY.code)
    const card = documentCard(page, JOURNEY.title)
    await expect(card.getByRole('button', { name: 'Run AI Extraction' })).toBeDisabled()
    await expect(card.getByTestId('add-manual-claim')).toBeVisible()   // exercised in manual-claim.e2e.ts
    await expect(card.getByTestId('extraction-origin')).toHaveCount(0)
    const docs = (await api(request, `/assets/${journeyAssetId}/documents`, bearer(ctx.credentials.issuer_a))).body
    const refused = await request.post(`${API_URL}/documents/${docs[0].id}/extractions`, { headers: bearer(ctx.credentials.issuer_a) })
    expect(refused.status()).toBe(503)
    expect((await api(request, `/documents/${docs[0].id}/extractions`, bearer(ctx.credentials.issuer_a))).body).toEqual([])
  })

  test('5–7. the review draft: every claim is tied to its document version, cited page and quotation', async ({ page, request }) => {
    const doc = review.documents[0]
    const backend = (await api(request, `/extractions/${review.extraction!.id}`, bearer(ctx.credentials.issuer_a))).body
    expect([backend.status, backend.document_id, backend.version_id]).toEqual(['draft', doc.id, doc.version_id])
    expect(doc.sha256).toBe(fixture.sha256)                 // the same synthetic PDF as the upload above

    await openReview(page, ctx.credentials.issuer_a)
    await expect(page.getByTestId('extraction-origin')).toHaveAttribute('data-origin', 'not_ai')
    await expect(page.getByTestId('extraction-version')).toHaveText(`Document version ${doc.version_number}`)
    await expect(page.getByTestId('source-text')).toHaveAttribute('data-version-id', doc.version_id)
    await expect(page.getByTestId('count-quote-found')).toHaveText(String(backend.claims.length))
    for (const claim of backend.claims as { field_name: string; source_page: number; supporting_passage: string;
                                             evidence: { status: string; version_number: number } }[]) {
      expect([claim.evidence.status, claim.evidence.version_number]).toEqual(['verified', doc.version_number])
      const row = page.getByTestId(`claim-row-${claim.field_name}`)
      await expect(row.getByTestId('evidence-badge-verified')).toHaveText(`Quote found on page ${claim.source_page}`)
      await row.getByRole('button', { name: /^View page \d+ of the source text/ }).click()
      const cited = page.getByTestId('source-page').nth(claim.source_page - 1)
      await expect(cited).toBeInViewport()
      await expect(cited).toContainText(claim.supporting_passage)
    }
  })

  test('8a. the issuer reviewer starts the review and gives the issuer attestation', async ({ page, request }) => {
    await openReview(page, ctx.credentials.issuer_a)
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Draft')
    await page.getByRole('button', { name: 'Start Review' }).click()
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Under Review')
    expect((await api(request, `/extractions/${review.extraction!.id}`, bearer(ctx.credentials.issuer_a))).body.status).toBe('under_review')

    await page.getByLabel('Approval notes (optional)').fill('E2E issuer attestation (synthetic)')
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByTestId('extraction-status-badge').first()).toHaveText('Approved')
    await expect(page.getByRole('heading', { name: 'Approvals (1)' })).toBeVisible()
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Issuer Attestation' })).toHaveCount(1)
    // Its own type is given: nothing more for this reviewer to add
    await expect(page.getByText('Add Your Approval')).toHaveCount(0)
    const state = (await api(request, `/extractions/${review.extraction!.id}`, bearer(ctx.credentials.issuer_a))).body.approval_state
    expect(state).toMatchObject({ state: 'current', current_approval_types: ['issuer_attestation'] })
  })

  test('8b. the independent legal reviewer adds the second approval', async ({ page, request }) => {
    await openReview(page, ctx.credentials.legal_a)
    await expect(page.getByText('Add Your Approval')).toBeVisible()
    await expect(page.getByTestId('add-approval-note')).toContainText('independent legal review')
    await page.getByLabel('Approval notes (optional)').fill('E2E independent legal review (synthetic)')
    await page.getByRole('button', { name: 'Approve Extraction' }).click()
    await expect(page.getByRole('heading', { name: 'Approvals (2)' })).toBeVisible()
    await expect(page.getByTestId('approval-type-badge').filter({ hasText: 'Independent Legal Review' })).toHaveCount(1)
    await expect(page.getByText('Add Your Approval')).toHaveCount(0)
    const state = (await api(request, `/extractions/${review.extraction!.id}`, bearer(ctx.credentials.legal_a))).body.approval_state
    expect(new Set(state.current_approval_types)).toEqual(new Set(['issuer_attestation', 'legal_verification']))
  })

  test('9–10. the institution reads the passport; it matches the approved claims and the backend', async ({ page, request }) => {
    const path = `/passport/${review.blockchain_network}/${review.stellar_asset_code}/${review.stellar_issuer_public_key}`
    const served = await request.get(`${API_URL}${path}`, { headers: { 'X-API-Key': ctx.credentials.inst_a } })
    expect(served.status()).toBe(200)
    const passport = await served.json()
    const claims = (await api(request, `/extractions/${review.extraction!.id}`, bearer(ctx.credentials.issuer_a))).body.claims as
      { field_name: string; value: string; source_page: number; supporting_passage: string }[]
    // Every right comes from an approved claim of exactly this document version
    expect(passport.legal_rights).toHaveLength(claims.length)
    for (const right of passport.legal_rights) {
      const claim = claims.find(c => c.field_name === right.field_name)!
      expect([right.value, right.source_page, right.supporting_passage]).toEqual([claim.value, claim.source_page, claim.supporting_passage])
      expect([right.document_id, right.document_version_number]).toEqual([review.documents[0].id, review.documents[0].version_number])
    }
    expect(passport.verification_summary).toMatchObject({ issuer_attestation_recorded: true, legal_reviewer_approval_recorded: true })

    await signIn(page, ctx.credentials.issuer_a, ctx.credentials.inst_a)
    await openAsset(page, review.stellar_asset_code)
    await page.getByTestId('open-passport-link').click()
    await expect(page.getByLabel('Asset Code', { exact: true })).toHaveValue(review.stellar_asset_code)
    await expect(page.getByLabel('Issuer Public Key', { exact: true })).toHaveValue(review.stellar_issuer_public_key)
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    const card = page.getByTestId('passport-card')
    await expect(card).toContainText(`Legal Passport — v${passport.extraction_metadata.passport_version}`)
    await expect(card).toContainText(review.stellar_asset_code)
    await expect(card.getByTestId('disclaimer-banner')).toBeVisible()
    await expect(card.getByTestId('verification-meaning')).toHaveText(passport.verification_summary.meaning)
    const badges = card.getByTestId('passport-right-badge')
    await expect(badges).toHaveCount(passport.legal_rights.length)
    for (const [i, right] of passport.legal_rights.entries()) {
      await expect(badges.nth(i)).toHaveText(right.independently_verified ? 'Independent reviewer attested'
        : right.verified ? 'Evidence cited' : 'Unverified')
      await expect(card.getByText(right.value, { exact: true }).first()).toBeVisible()
    }
    const chain = card.locator('li').filter({ has: page.getByTestId('approval-type-badge') })
    await expect(chain).toHaveCount(2)
    await expect(chain.filter({ hasText: 'Issuer Reviewer' }).getByTestId('approval-type-badge')).toHaveText('Issuer Attestation')
    await expect(chain.filter({ hasText: 'Independent Legal Reviewer' }).getByTestId('approval-type-badge')).toHaveText('Independent Legal Review')
    await expect(card).not.toContainText('e2e-legal-reviewer-a')     // reviewer roles only, no identities
  })

  test('11. the new passport has no Stellar commitment, and none is submitted', async ({ page, request }) => {
    const backend = (await api(request, `/assets/${review.id}/stellar-anchors/verification`, bearer(ctx.credentials.issuer_a))).body
    expect(backend).toMatchObject({ result: 'not_anchored', verified: false, current_passport_available: true })
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, review.stellar_asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
    await expect(page.getByText('No anchoring transaction recorded yet.')).toBeVisible()
    await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    expect((await api(request, `/assets/${review.id}/stellar-anchors`, bearer(ctx.credentials.issuer_a))).body).toEqual([])
  })
})
