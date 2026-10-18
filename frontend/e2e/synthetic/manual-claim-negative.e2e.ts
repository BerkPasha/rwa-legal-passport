import { test, expect, ctx, signIn, openAsset, api, bearer, documentCard, openManualClaimForm, API_URL } from '../support'
import type { APIRequestContext } from '@playwright/test'

// Manual legal claims refused (Stage 10B-3): every refusal is decided by the server, stores
// nothing, and is shown without internal detail. Uses the seeded synthetic dataset.
const approved = ctx.assets!.approved
const draft = ctx.assets!.draft
const [prospectus, scanned] = approved.documents
const draftDoc = draft.documents[0]
const altered = draft.documents.find(d => d.title === 'Altered file (synthetic)')!
const missing = draft.documents.find(d => d.title === 'Missing file (synthetic)')!
// A real quotation of the synthetic prospectus (page 4)
const REAL = { field_name: 'jurisdiction', value: 'English law.', source_page: 4,
               supporting_passage: 'governed by the laws of England and Wales' }

const manualUrl = (documentId: string) => `${API_URL}/documents/${documentId}/manual-extractions`
const payload = (assetId: string, versionId: string, claim: object = REAL) => ({ asset_id: assetId, version_id: versionId, claims: [claim] })
const extractionCount = async (request: APIRequestContext, documentId: string) =>
  ((await api(request, `/documents/${documentId}/extractions`, bearer(ctx.credentials.issuer_a))).body as unknown[]).length

test.describe('Manual claims refused', () => {
  test('a role without extraction:run is not offered the form, and the server refuses it', async ({ page, request }) => {
    await signIn(page, ctx.credentials.legal_a)
    await openAsset(page, approved.stellar_asset_code)
    await expect(documentCard(page, prospectus.title)).toBeVisible()
    await expect(page.getByTestId('add-manual-claim')).toHaveCount(0)
    const before = await extractionCount(request, prospectus.id)
    const r = await request.post(manualUrl(prospectus.id), { headers: bearer(ctx.credentials.legal_a),
                                                              data: payload(approved.id, prospectus.version_id) })
    expect(r.status()).toBe(403)
    expect(await extractionCount(request, prospectus.id)).toBe(before)
  })

  test('missing, invalid and institutional credentials are refused; another tenant sees no document', async ({ request }) => {
    const data = payload(approved.id, prospectus.version_id)
    expect((await request.post(manualUrl(prospectus.id), { data })).status()).toBe(403)
    expect((await request.post(manualUrl(prospectus.id), { headers: bearer('e2e-not-a-valid-token'), data })).status()).toBe(401)
    expect((await request.post(manualUrl(prospectus.id), { headers: { 'X-API-Key': ctx.credentials.inst_a }, data })).status()).toBe(403)
    const foreign = await request.post(manualUrl(prospectus.id), { headers: bearer(ctx.credentials.issuer_b), data })
    expect(foreign.status()).toBe(404)
    expect(await foreign.json()).toEqual({ detail: 'Document not found.' })
  })

  test('a document of another asset, or a version of another document, is refused', async ({ request }) => {
    const before = await extractionCount(request, draftDoc.id)
    const crossAsset = await request.post(manualUrl(draftDoc.id), { headers: bearer(ctx.credentials.issuer_a),
                                                                     data: payload(approved.id, draftDoc.version_id) })
    expect([crossAsset.status(), (await crossAsset.json()).manual_claim_refusal]).toEqual([422, 'document_not_in_asset'])
    const crossVersion = await request.post(manualUrl(draftDoc.id), { headers: bearer(ctx.credentials.issuer_a),
                                                                       data: payload(draft.id, prospectus.version_id) })
    expect([crossVersion.status(), (await crossVersion.json()).manual_claim_refusal]).toEqual([422, 'version_not_in_document'])
    const tooShort = await request.post(manualUrl(draftDoc.id), { headers: bearer(ctx.credentials.issuer_a),
      data: payload(draft.id, draftDoc.version_id, { ...REAL, supporting_passage: 'laws of England' }) })
    expect((await tooShort.json()).evidence_failures[0].status).toBe('quotation_too_short')
    expect(await extractionCount(request, draftDoc.id)).toBe(before)
  })

  test('a page without a text layer cannot be chosen, and the server refuses it', async ({ page, request }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, approved.stellar_asset_code)
    const form = await openManualClaimForm(page, scanned.title)
    await expect(form.getByRole('option', { name: 'Page 2 — no text layer, cannot be quoted' })).toBeDisabled()
    const r = await request.post(manualUrl(scanned.id), { headers: bearer(ctx.credentials.issuer_a),
      data: payload(approved.id, scanned.version_id, { ...REAL, source_page: 2, supporting_passage: 'a clause read on the scanned page' }) })
    expect([r.status(), (await r.json()).evidence_failures[0].status]).toEqual([422, 'no_text_layer'])
  })

  test('a version whose stored PDF is missing or altered cannot be quoted', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 409/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, draft.stellar_asset_code)
    const form = await openManualClaimForm(page, altered.title)
    await form.getByLabel('Legal field').selectOption(REAL.field_name)
    await form.getByLabel('Source page').selectOption(String(REAL.source_page))
    await form.getByLabel('Claim', { exact: true }).fill(REAL.value)
    await form.getByLabel('Exact quotation from the selected page').fill(REAL.supporting_passage)
    await form.getByRole('button', { name: 'Create Draft Claim' }).click()
    await expect(form.getByTestId('manual-claim-error')).toHaveText(
      'Not created — The stored PDF of this version does not match its records (file_hash_mismatch), so its text cannot be quoted. Nothing was stored.')
    expect(await extractionCount(request, altered.id)).toBe(0)
    const r = await request.post(manualUrl(missing.id), { headers: bearer(ctx.credentials.issuer_a),
                                                           data: payload(draft.id, missing.version_id) })
    expect([r.status(), (await r.json()).source_integrity_failure]).toEqual([409, 'file_unavailable'])
  })

  test('a server failure or an unreachable server is reported safely, and nothing is created', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 500/, /ERR_CONNECTION_REFUSED|ERR_FAILED/)
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, draft.stellar_asset_code)
    const before = await extractionCount(request, draftDoc.id)
    const form = await openManualClaimForm(page, draftDoc.title)
    await form.getByLabel('Legal field').selectOption(REAL.field_name)
    await form.getByLabel('Source page').selectOption(String(REAL.source_page))
    await form.getByLabel('Claim', { exact: true }).fill(REAL.value)
    await form.getByLabel('Exact quotation from the selected page').fill(REAL.supporting_passage)
    const submit = form.getByRole('button', { name: 'Create Draft Claim' })
    const error = form.getByTestId('manual-claim-error')

    // Simulated in the browser only: the requests never reach the backend
    await page.route(manualUrl(draftDoc.id), route => route.fulfill({ status: 500, json: { detail: 'Traceback at /srv/internal/db.py' } }))
    await submit.click()
    await expect(error).toHaveText('Not created — the server could not process the request. Nothing was stored; try again later.')
    await page.unroute(manualUrl(draftDoc.id))
    await page.route(manualUrl(draftDoc.id), route => route.abort('connectionrefused'))
    await submit.click()
    await expect(error).toHaveText('Not created — the server could not be reached. Nothing was stored.')
    expect(await extractionCount(request, draftDoc.id)).toBe(before)
  })
})
