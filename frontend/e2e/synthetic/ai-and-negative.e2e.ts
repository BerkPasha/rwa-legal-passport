import { test, expect, ctx, signIn, openAsset, navLink, api, bearer, documentCard, API_URL } from '../support'

// G. External AI with AI_PROVIDER=disabled, and the remaining negative journeys.
const approved = ctx.assets!.approved
const other = ctx.assets!.other_tenant

test.describe('G. External AI disabled', () => {
  test('the disabled provider is shown, the action is unavailable and nothing is created', async ({ page, request }) => {
    const prospectus = approved.documents[0]
    const before = await api(request, `/documents/${prospectus.id}/extractions`, bearer(ctx.credentials.issuer_a))
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, approved.stellar_asset_code)
    const status = page.getByTestId('ai-status')
    await expect(status).toContainText('External AI extraction is disabled')
    await expect(status).toContainText('AI extraction is disabled on this server (AI_PROVIDER=disabled): no document is sent to any AI service.')
    await expect(status).toContainText('Extractions listed below were created earlier or are test data')
    const card = documentCard(page, prospectus.title)
    const run = card.getByRole('button', { name: 'Run AI Extraction' })
    await expect(run).toBeDisabled()
    await expect(run).toHaveAttribute('aria-describedby', 'ai-status')
    // The seeded extraction is scripted test data and is labelled as such, never as an AI result
    const origin = card.getByTestId('extraction-origin')
    await expect(origin).toHaveCount(before.body.length)
    await expect(origin.first()).toHaveAttribute('data-origin', 'not_ai')
    await expect(origin.first()).toHaveText(`Not produced by a supported AI model (test or imported data) · ${ctx.seed_model}`)
    await expect(page.getByText(/AI extraction ·/)).toHaveCount(0)

    // The server refuses the request itself too, and stores nothing
    const status503 = await request.post(`${API_URL}/documents/${prospectus.id}/extractions`, { headers: bearer(ctx.credentials.issuer_a) })
    expect(status503.status()).toBe(503)
    expect((await status503.json()).ai_error).toBe('ai_provider_disabled')
    const after = await api(request, `/documents/${prospectus.id}/extractions`, bearer(ctx.credentials.issuer_a))
    expect(after.body.length).toBe(before.body.length)
    const ai = await api(request, '/ai/status', bearer(ctx.credentials.issuer_a))
    expect(ai.body).toMatchObject({ available: false, status: 'ai_provider_disabled', external_processor: null })
  })
})

test.describe('Negative journeys', () => {
  test("another tenant's reviewer sees only its own asset and cannot open this tenant's records", async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 404/)
    await signIn(page, ctx.credentials.issuer_b)
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(1)
    await expect(page.getByText(other.stellar_asset_code)).toBeVisible()
    await page.goto(`/assets/${approved.id}`)
    await expect(page.getByText('Asset not found.')).toBeVisible()
    await page.goto(`/assets/${approved.id}/documents/${approved.documents[0].id}/extractions/${approved.extraction!.id}`)
    await expect(page.getByRole('alert')).toHaveText('Not found: it does not exist or is outside your access.')
    await expect(page.getByRole('heading', { name: 'AI Extraction Result' })).toHaveCount(0)
  })

  test('an incorrect document or extraction reference shows an error, not data', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 404/)
    await signIn(page, ctx.credentials.issuer_a)
    await page.goto(`/assets/${approved.id}/documents/00000000-0000-4000-8000-000000000000/extractions/00000000-0000-4000-8000-000000000001`)
    await expect(page.getByRole('alert')).toHaveText('Not found: it does not exist or is outside your access.')
    await expect(page.getByTestId('evidence-badge-verified')).toHaveCount(0)
  })

  test('when the API cannot be reached the registry reports it instead of showing data', async ({ page, diagnostics }) => {
    diagnostics.allow(/ERR_CONNECTION_REFUSED|ERR_FAILED/)
    await signIn(page, ctx.credentials.issuer_a)
    // Simulated in the browser only: the request never reaches the backend
    await page.route(`${API_URL}/assets`, route => route.abort('connectionrefused'))
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('alert')).toHaveText('Failed to load')
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(0)
  })

  test('a server error is shown as an error, and the page recovers once the API answers again', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 500/)
    await signIn(page, ctx.credentials.issuer_a)
    await page.route(`${API_URL}/assets`, route => route.fulfill({ status: 500, json: { detail: 'Simulated server error (E2E)' } }))
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('alert')).toHaveText('Simulated server error (E2E)')
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(0)
    // The API is reachable again: the next visit shows the data and no stale error
    await page.unroute(`${API_URL}/assets`)
    const own = await api(request, '/assets', bearer(ctx.credentials.issuer_a))
    await navLink(page, 'Dashboard').click()
    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(own.body.length)
    await expect(page.getByRole('alert')).toHaveCount(0)
  })

  test('browser back and forward navigation keeps the pages consistent', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await openAsset(page, approved.stellar_asset_code)
    await page.getByRole('link', { name: /Review →/ }).click()
    await expect(page.getByRole('heading', { name: 'AI Extraction Result' })).toBeVisible()
    await page.goBack()
    await expect(page.getByRole('heading', { level: 2, name: approved.stellar_asset_code })).toBeVisible()
    await page.goForward()
    await expect(page.getByRole('heading', { name: 'AI Extraction Result' })).toBeVisible()
  })

  test('an unknown address shows a "page not found" page with the navigation, and loads nothing', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 403/)   // the registry, opened afterwards without credentials
    const apiRequests: string[] = []
    page.on('request', r => { if (API_URL && r.url().startsWith(API_URL)) apiRequests.push(new URL(r.url()).pathname) })
    const response = await page.goto('/no-such-page')
    expect(response?.status()).toBe(200)      // a single-page application: the address is resolved in the browser
    await expect(page.getByRole('heading', { level: 2, name: 'Page not found' })).toBeVisible()
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Page Not Found')
    await expect(page).toHaveTitle('Page Not Found · RWA Legal Passport')
    await expect(page.getByText('/no-such-page', { exact: true })).toBeVisible()
    await expect(navLink(page, 'Asset Registry')).toBeVisible()
    // Only the public /health route, for the server's policies (Stage 10C-1); no data is requested
    expect(apiRequests.filter(path => path !== '/health')).toEqual([])
    await page.getByRole('link', { name: 'Open the asset registry' }).click()
    await expect(page).toHaveURL(/\/assets$/)
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('Asset Registry')
    await expect(page.getByRole('alert')).toHaveText('Not authenticated')
  })

  test('an unknown address under an asset is not mistaken for a page', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await page.goto(`/assets/${approved.id}/no-such-tab`)
    await expect(page.getByRole('heading', { level: 2, name: 'Page not found' })).toBeVisible()
    await expect(page.getByText(approved.stellar_asset_code)).toHaveCount(0)
  })
})
