import { test, expect, ctx, signIn, setCredentials, navLink, api, bearer, blockExternal } from '../support'

// A. Authentication through the real sidebar Credentials panel; the backend decides what each identity may see.
const assets = ctx.assets!

test.describe('A. Authentication', () => {
  test('signing in on the dashboard reloads it in place and shows whom the credential belongs to', async ({ page, request, diagnostics }) => {
    diagnostics.allow(/status of 403/)   // the dashboard's first request, before any credential is set
    await page.goto('/')
    await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible()
    await expect(page.getByRole('alert')).toHaveText('Not authenticated')
    await expect(page.getByTestId('auth-status')).toHaveText('Not signed in')

    await setCredentials(page, { userToken: ctx.credentials.issuer_a })
    // Without navigating: the identity is confirmed by the server and the overview reloads
    await expect(page.getByTestId('auth-status'))
      .toHaveText(`Signed in as ${ctx.display_names!.issuer_a} Issuer Reviewer · issuers: e2e-meridian`)
    await expect(page.getByRole('alert')).toHaveCount(0)
    const visible = await api(request, '/assets', bearer(ctx.credentials.issuer_a))
    expect(visible.body.length).toBeGreaterThanOrEqual(5)
    // The overview lists the five most recent assets, in the backend's order
    for (const asset of (visible.body as { stellar_asset_code: string }[]).slice(0, 5)) {
      await expect(page.getByText(asset.stellar_asset_code, { exact: true }).first()).toBeVisible()
    }
    // The credential itself is never shown
    await expect(page.locator('body')).not.toContainText(ctx.credentials.issuer_a)
  })

  test('the issuer reviewer sees exactly the assets of its own tenant', async ({ page, request }) => {
    const own = (await api(request, '/assets', bearer(ctx.credentials.issuer_a))).body as { stellar_asset_code: string; issuer_id: string }[]
    expect(own.every(a => a.issuer_id === 'e2e-meridian')).toBe(true)
    await signIn(page, ctx.credentials.issuer_a)
    await navLink(page, 'Asset Registry').click()
    const rows = page.getByRole('row').filter({ has: page.getByRole('link', { name: 'View →' }) })
    await expect(rows).toHaveCount(own.length)
    for (const asset of own) await expect(rows.filter({ hasText: asset.stellar_asset_code })).toHaveCount(1)
    await expect(page.getByText(assets.other_tenant.stellar_asset_code)).toHaveCount(0)
  })

  test('the independent legal reviewer of the same tenant sees the same assets', async ({ page, request }) => {
    const own = (await api(request, '/assets', bearer(ctx.credentials.legal_a))).body as unknown[]
    await signIn(page, ctx.credentials.legal_a)
    await expect(page.getByTestId('auth-status')).toContainText('Independent Legal Reviewer · issuers: e2e-meridian')
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(own.length)
    await expect(page.getByText(assets.other_tenant.stellar_asset_code)).toHaveCount(0)
  })

  test('without credentials the registry is refused and shows no asset', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 403/)
    await page.goto('/assets')
    await expect(page.getByRole('alert')).toHaveText('Not authenticated')
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(0)
  })

  test('an invalid token is refused, shown as not accepted, and shows no asset', async ({ page, diagnostics }) => {
    diagnostics.allow(/status of 401/)
    await page.goto('/api-docs')
    await setCredentials(page, { userToken: 'e2e-not-a-valid-token' })
    await expect(page.getByTestId('auth-status')).toHaveText('Credential not accepted')
    await navLink(page, 'Asset Registry').click()
    await expect(page.getByRole('heading', { level: 2, name: 'Asset Registry' })).toBeVisible()
    await expect(page.getByRole('link', { name: 'View →' })).toHaveCount(0)
    await expect(page.getByRole('alert')).toBeVisible()
  })

  test('credentials are kept for the browser tab only', async ({ page, browser }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await page.reload()
    await expect(page.getByTestId('auth-status')).toContainText('Signed in as')   // same tab: kept
    const other = await browser.newContext()                                      // another session: nothing
    const external: string[] = []
    await blockExternal(other, origin => external.push(origin))                   // the isolation of the test's own context
    const fresh = await other.newPage()
    await fresh.goto('/api-docs')
    await expect(fresh.getByTestId('auth-status')).toHaveText('Not signed in')
    expect(external, 'external requests of the application (refused in the browser)').toEqual([])
    await other.close()
  })
})
