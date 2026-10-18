import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { test, expect, ctx, signIn, openAsset, navLink, api, bearer, documentCard, API_URL } from '../support'
import type { APIRequestContext } from '@playwright/test'

// The public read-only showcase (Stage 10C-1, GRANT_DEMO.md): the synthetic dataset, served with
// READ_ONLY_MODE=true, SYNTHETIC_DATA_NOTICE=true and API_DOCS_ENABLED=false. An evaluator can read
// everything, nothing can be changed, and the server, not the interface, enforces it.
const manual = ctx.assets!.manual
const manualDoc = manual.documents[0]
const pending = ctx.assets!.anchor_pending
const invalid = ctx.assets!.anchor_invalid
const approved = ctx.assets!.approved
const json = (body: unknown) => ({ data: body })

// What the server holds, read with the operator's credential (the source of truth to compare)
async function state(request: APIRequestContext) {
  const admin = bearer(ctx.credentials.admin)
  return JSON.stringify({
    assets: (await api(request, '/assets', admin)).body,
    documents: (await api(request, `/assets/${manual.id}/documents`, admin)).body,
    extractions: (await api(request, `/documents/${manualDoc.id}/extractions`, admin)).body,
    approvals: (await api(request, `/extractions/${manual.extraction!.id}/approvals`, admin)).body,
    anchors: (await api(request, `/assets/${pending.id}/stellar-anchors`, admin)).body,
  })
}

test.describe('Read-only showcase', () => {
  test('every page says what this server is, and an anonymous visitor sees no data', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 403/)
    await page.goto('/')
    const banner = page.getByTestId('deployment-banner')
    await expect(banner).toContainText('Read-only showcase: nothing can be created or changed on this server.')
    await expect(banner).toContainText('synthetic and fictional')
    await expect(banner).toContainText('Stellar Testnet only')
    await expect(page.getByTestId('how-to-read')).toContainText('It does not prove that a claim is legally valid')
    await expect(page.getByRole('alert')).toHaveText('Not authenticated')            // no anonymous reads
    expect((await request.get(`${API_URL}/assets`)).status()).toBe(403)
    await navLink(page, 'API Reference').click()
    await expect(page.getByTestId('api-docs-disabled')).toBeVisible()
    await expect(page.getByTestId('deployment-banner')).toBeVisible()
  })

  test('an evaluator reads a passport of manually entered claims from document to approvals', async ({ page }) => {
    await signIn(page, ctx.credentials.legal_a, ctx.credentials.inst_a)
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('heading', { level: 2, name: 'Asset Registry' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'Register Asset' })).toHaveCount(0)
    await openAsset(page, manual.stellar_asset_code)
    await expect(page.getByRole('button', { name: 'Upload Document' })).toHaveCount(0)
    const card = documentCard(page, manualDoc.title)
    await expect(card.getByTestId('document-sha256')).toHaveAttribute('title', manualDoc.sha256)
    await expect(card.getByRole('button', { name: /Run AI Extraction/ })).toHaveCount(0)
    await expect(card.getByTestId('add-manual-claim')).toHaveCount(0)
    // Reading stays possible: the PDF, hash-checked
    const download = page.waitForEvent('download')
    await card.getByTestId('download-document').click()
    const bytes = readFileSync((await (await download).path())!)
    expect(createHash('sha256').update(bytes).digest('hex')).toBe(manualDoc.sha256)

    await card.getByRole('link', { name: /Review →/ }).click()
    await expect(page.getByRole('heading', { level: 2, name: 'Manually Entered Legal Claims' })).toBeVisible()
    await expect(page.getByTestId('extraction-origin')).toHaveText('Manual entry · no AI')
    await expect(page.getByTestId('claim-row-jurisdiction').getByTestId('evidence-badge-verified')).toHaveText('Quote found on page 4')
    await expect(page.getByTestId('review-read-only-note')).toBeVisible()
    await expect(page.getByRole('heading', { name: 'Approvals (2)' })).toBeVisible()
    await expect(page.getByRole('button', { name: /Approve Extraction|Start Review|^Correct$/ })).toHaveCount(0)

    await page.getByRole('link', { name: 'Asset', exact: true }).click()   // the breadcrumb
    await page.getByTestId('open-passport-link').click()
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    const passport = page.getByTestId('passport-card')
    await expect(passport.getByTestId('value-origin')).toHaveText(
      Array(2).fill('Entered manually by a user of the issuer from the cited page (no AI)'))
    await expect(passport.getByTestId('passport-attestation').first()).toContainText('attested by an independent legal reviewer')
    await expect(passport.getByTestId('passport-extraction-source')).toHaveText('Manual entry (no AI model)')
    await expect(page.getByTestId('anchor-card').getByTestId('anchor-result-badge')).toHaveText('Not anchored')
  })

  test('the Stellar evidence states are shown as they are, and no anchoring action is offered', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)       // a role that could anchor on a writable server
    for (const [asset, badge] of [[invalid, 'Anchor record invalid'], [pending, 'Stellar network unavailable'],
                                  [approved, 'Not anchored']] as const) {
      await openAsset(page, asset.stellar_asset_code)
      await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
      await expect(page.getByTestId('anchor-result-badge')).toHaveText(badge)
      await expect(page.getByTestId('anchor-read-only-note')).toContainText('Read-only showcase')
      await expect(page.getByRole('button', { name: /Anchor current evidence|Refresh pending/ })).toHaveCount(0)
      await expect(page.getByText('Commitment verified on Stellar Testnet')).toHaveCount(0)
    }
    await expect(page.getByTestId('anchor-pending-note')).toHaveCount(0)   // the last asset has no anchor record
  })

  test('the server refuses every write, for every credential, and nothing changes', async ({ request }) => {
    const before = await state(request)
    const claimId = (await api(request, `/extractions/${manual.extraction!.id}`, bearer(ctx.credentials.issuer_a))).body.claims[0].id
    const anchorId = (await api(request, `/assets/${pending.id}/stellar-anchors`, bearer(ctx.credentials.issuer_a))).body[0].id
    const pdf = { name: 'x.pdf', mimeType: 'application/pdf', buffer: readFileSync(ctx.fixtures!.trust_prospectus.path) }
    const writes: [string, string, object][] = [
      ['POST', '/assets', json({ issuer_id: 'e2e-meridian' })],
      ['PATCH', `/assets/${manual.id}`, json({ jurisdiction: 'XX' })],
      ['POST', `/assets/${manual.id}/documents`, { multipart: { document_type: 'prospectus', title: 'x', file: pdf } }],
      ['POST', `/documents/${manualDoc.id}/versions`, { multipart: { file: pdf } }],
      ['POST', `/documents/${manualDoc.id}/extractions`, {}],
      ['POST', `/documents/${manualDoc.id}/manual-extractions`, json({ asset_id: manual.id, version_id: manualDoc.version_id,
        claims: [{ field_name: 'voting_rights', value: 'x', source_page: 4, supporting_passage: 'governed by the laws of England and Wales' }] })],
      ['POST', `/extractions/${manual.extraction!.id}/review`, {}],
      ['PATCH', `/extractions/${manual.extraction!.id}/claims/${claimId}`, json({ corrected_value: 'x', is_unknown: false })],
      ['POST', `/extractions/${manual.extraction!.id}/approvals`, json({})],
      ['POST', `/assets/${manual.id}/stellar-anchors`, {}],
      ['POST', `/assets/${pending.id}/stellar-anchors/${anchorId}/refresh`, {}],
    ]
    const credentials = [ctx.credentials.admin, ctx.credentials.issuer_a, ctx.credentials.legal_a, null, 'not-a-token']
    for (const token of credentials) {
      for (const [method, path, options] of writes) {
        const r = await request.fetch(`${API_URL}${path}`, { method, headers: token ? bearer(token) : {}, ...options })
        expect(r.status(), `${method} ${path}`).toBe(403)
        expect(await r.json()).toEqual({ detail: 'This server is a read-only showcase: no data can be created or changed.',
                                         read_only_mode: true })
      }
    }
    expect(await state(request)).toBe(before)
  })

  test('the API documentation is not published, and responses carry the security headers', async ({ request }) => {
    for (const path of ['/docs', '/redoc', '/openapi.json']) {
      expect((await request.get(`${API_URL}${path}`)).status(), path).toBe(404)
    }
    const health = await request.get(`${API_URL}/health`)
    expect(await health.json()).toMatchObject({ read_only: true, synthetic_data: true, api_docs: false })
    const headers = health.headers()
    expect(headers['x-content-type-options']).toBe('nosniff')
    expect(headers['x-frame-options']).toBe('DENY')
    expect(headers['content-security-policy']).toBe("default-src 'none'; frame-ancestors 'none'")
    expect(headers['cache-control']).toBe('no-store')
  })
})
