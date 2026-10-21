import { test, expect, ctx, signIn, openAsset, api, bearer, documentCard, API_URL } from '../support'

// Deployment rehearsal (Stage 10C-2, DEPLOYMENT.md): the bundle of scripts/build_showcase.py, run as
// on a server. The backend is uvicorn with the production flags and the bundle's showcase.env, behind
// deploy/rehearsal_proxy.py, the local stand-in for the Caddyfile: the static build, single-page
// routing and the API under /api, all on one origin. The genuine Stellar Testnet commitment of the
// demo dataset is included when that dataset exists.
const base = process.env.E2E_BASE_URL!
const origin = new URL(base).origin
const manual = ctx.assets!.manual
const harbor = ctx.assets!.other_tenant
const genuine = ctx.asset          // the demo dataset's asset, with its real Testnet anchor
const anchor = ctx.anchor
const HORIZON = 'https://horizon-testnet.stellar.org'

test.describe('Deployment rehearsal', () => {
  test('the static build: single-page routing, immutable hashed files, a strict CSP, no server banner', async ({ request }) => {
    const index = await request.get(`${base}/`)
    expect(index.status()).toBe(200)
    const headers = index.headers()
    expect(headers['content-type']).toContain('text/html')
    expect(headers['cache-control']).toBe('no-cache')
    expect(headers['content-security-policy']).toContain("default-src 'self'; script-src 'self'; style-src 'self'")
    expect(headers['content-security-policy']).toContain("frame-ancestors 'none'")
    expect(headers['x-frame-options']).toBe('DENY')
    expect(headers['server']).toBeUndefined()
    const html = await index.text()
    expect(html).not.toContain('fonts.googleapis.com')            // no third-party request from the page
    const script = html.match(/\/static\/[^"]+\.js/)![0]
    const file = await request.get(`${base}${script}`)
    expect(file.status()).toBe(200)
    expect(file.headers()['cache-control']).toBe('public, max-age=31536000, immutable')
    expect(file.headers()['content-type']).toContain('javascript')
    expect((await request.get(`${base}/static/missing.js`)).status()).toBe(404)
    // An application route loaded directly is answered with the application, never cached
    const deep = await request.get(`${base}/assets/${manual.id}`)
    expect([deep.status(), deep.headers()['cache-control']]).toEqual([200, 'no-cache'])
    expect(await deep.text()).toBe(html)
    expect((await request.post(`${base}/assets/${manual.id}`)).status()).toBe(405)
  })

  test('the public website loads under the same CSP, sends nothing elsewhere and leads into the application', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 403/)   // the dashboard, opened without a credential
    const requests: string[] = []
    page.on('request', r => requests.push(r.url()))
    await page.goto('/')
    await expect(page.getByRole('heading', { level: 1 })).toHaveText('The legal evidence layer for real-world assets.')
    await expect(page.getByTestId('deployment-banner')).toContainText('Read-only showcase')
    const primary = page.getByRole('navigation', { name: 'Primary' })
    await primary.getByRole('link', { name: 'Platform' }).click()
    await expect(page).toHaveURL(/#platform$/)
    await expect(page.getByTestId('passport-history')).toBeAttached()          // the product preview
    await primary.getByRole('link', { name: 'Launch App' }).click()
    await expect(page).toHaveURL(/\/dashboard$/)
    await expect(page.getByRole('heading', { level: 1, name: 'Dashboard' })).toBeVisible()
    expect(diagnostics.externalRequests).toEqual([])
    expect(requests.every(url => new URL(url).origin === origin)).toBe(true)
  })

  test('the browser uses the API on its own origin, sends nothing elsewhere, and violates no CSP', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 403/)   // the deep link, opened before signing in
    const requests: string[] = []
    page.on('request', r => requests.push(r.url()))
    await page.goto(`/assets/${manual.id}`)                    // a deep link, loaded directly
    await expect(page.getByTestId('deployment-banner')).toContainText('Read-only showcase')
    await signIn(page, ctx.credentials.evaluator_a, ctx.credentials.institution_a)
    await openAsset(page, manual.stellar_asset_code)
    await expect(documentCard(page, manual.documents[0].title).getByTestId('document-sha256'))
      .toHaveAttribute('title', manual.documents[0].sha256)
    await page.getByTestId('open-passport-link').click()
    await page.getByRole('button', { name: 'Fetch Legal Passport' }).click()
    await expect(page.getByTestId('passport-extraction-source')).toHaveText('Manual entry (no AI model)')
    expect(diagnostics.externalRequests).toEqual([])
    expect(requests.every(url => new URL(url).origin === origin)).toBe(true)
    expect(requests.some(url => new URL(url).pathname.startsWith('/api/passport/'))).toBe(true)
    // A content-security-policy violation is a console error: the diagnostics fixture fails the test on any
  })

  test('the genuine Stellar Testnet commitment is verified only when the ledger confirms it', async ({ page, request }) => {
    test.skip(!genuine || !anchor, 'the local demo dataset with its genuine anchor is not available')
    const live = ctx.network === 'testnet'
    const backend = await api(request, `/assets/${genuine!.id}/stellar-anchors/verification`, bearer(ctx.credentials.evaluator_a))
    expect(backend.body.anchor.transaction_hash).toBe(anchor!.transaction_hash)
    expect(backend.body.result).toBe(live ? 'verified' : 'network_unavailable')
    expect(backend.body.verified).toBe(live)
    if (live) {
      // Independently, on public Horizon: the transaction, its account and the digest in its memo
      const tx = await (await request.get(`${HORIZON}/transactions/${anchor!.transaction_hash}`)).json()
      expect([tx.successful, tx.source_account, Buffer.from(tx.memo, 'base64').toString('hex')])
        .toEqual([true, anchor!.anchor_account, anchor!.manifest_digest])
    }
    await signIn(page, ctx.credentials.evaluator_a)
    await openAsset(page, genuine!.asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    await expect(page.getByTestId('anchor-result-badge'))
      .toHaveText(live ? 'Commitment verified on Stellar Testnet' : 'Stellar network unavailable')
    await expect(page.getByTestId('anchor-tx-hash')).toHaveText(anchor!.transaction_hash)
    // A synthetic asset of the same showcase has no commitment, and says so
    await openAsset(page, manual.stellar_asset_code)
    await page.getByRole('button', { name: 'Stellar Evidence Anchor' }).click()
    await expect(page.getByTestId('anchor-result-badge')).toHaveText('Not anchored')
  })

  test('through the proxy too: writes refused, documentation hidden, tenants isolated', async ({ request }) => {
    const write = await request.post(`${API_URL}/assets`, { headers: bearer(ctx.credentials.evaluator_a), data: {} })
    expect([write.status(), (await write.json()).read_only_mode]).toEqual([403, true])
    for (const path of ['/docs', '/redoc', '/openapi.json']) {
      expect((await request.get(`${API_URL}${path}`)).status(), path).toBe(404)
    }
    const health = await request.get(`${API_URL}/health`)
    expect(await health.json()).toMatchObject({ read_only: true, synthetic_data: true, api_docs: false })
    expect(health.headers()['content-security-policy']).toBe("default-src 'none'; frame-ancestors 'none'")
    expect(health.headers()['server']).toBeUndefined()
    expect((await request.get(`${API_URL}/assets`)).status()).toBe(403)           // no anonymous reads
    // Evaluator B sees only the other tenant; Evaluator A cannot see it
    expect((await api(request, `/assets/${manual.id}`, bearer(ctx.credentials.evaluator_b))).status).toBe(404)
    expect((await api(request, `/assets/${harbor.id}`, bearer(ctx.credentials.evaluator_b))).status).toBe(200)
    expect((await api(request, `/assets/${harbor.id}`, bearer(ctx.credentials.evaluator_a))).status).toBe(404)
    const passport = `/passport/${manual.blockchain_network}/${manual.stellar_asset_code}/${manual.stellar_issuer_public_key}`
    expect((await request.get(`${API_URL}${passport}`, { headers: { 'X-API-Key': ctx.credentials.institution_b } })).status()).toBe(404)
    expect((await request.get(`${API_URL}${passport}`, { headers: { 'X-API-Key': ctx.credentials.institution_a } })).status()).toBe(200)
  })

  test('the frontend build holds no credential or signing material', async ({ request }) => {
    const html = await (await request.get(`${base}/`)).text()
    const files = [html, ...await Promise.all([...html.matchAll(/\/static\/[^"]+/g)]
      .map(async ([path]) => (await request.get(`${base}${path}`)).text()))]
    expect(files.join('')).toContain('"/api"')                       // the API URL of this build
    for (const body of files) {
      for (const value of Object.values(ctx.credentials)) expect(body.includes(value)).toBe(false)
      for (const marker of ['secret_seed', 'sk-ant-', 'token_sha256', 'PRIVATE KEY']) expect(body.includes(marker)).toBe(false)
    }
  })

  // Last: it blocks this test client's address for a minute
  test('repeated failed authentication is refused for a minute', async ({ request }) => {
    for (let i = 0; i < 10; i++) {
      expect((await request.get(`${API_URL}/assets`, { headers: bearer(`rehearsal-wrong-token-${i}-0000000`) })).status()).toBe(401)
    }
    const blocked = await request.get(`${API_URL}/assets`, { headers: bearer(ctx.credentials.evaluator_a) })
    expect(blocked.status()).toBe(429)
    expect(blocked.headers()['retry-after']).toBe('60')
    expect((await blocked.json()).rate_limit).toBe('authentication_failures')
  })
})
