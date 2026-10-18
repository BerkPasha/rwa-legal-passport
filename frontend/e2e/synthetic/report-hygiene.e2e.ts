import { test, expect, ctx, signIn, redact, REDACTED } from '../support'

// Report hygiene self-test (Stage 10B-2, E2E_TESTING.md). The second test fails on purpose, with a
// credential typed into the sidebar and that credential in its failure message, so that every
// artifact a failure produces is written on each run. scripts/e2e_run.py then scans everything
// retained for the run's credentials and requires the redaction marker in its error-context.md:
// the leak was there, and it was removed before anything was written.
test.use({ screenshot: 'on' })   // a screenshot of the failing page even though the failure is expected

test.describe('Report hygiene (self-test)', () => {
  test('a passing test leaves no credential in its artifacts', async ({ page }) => {
    await signIn(page, ctx.credentials.issuer_a)
    await expect(page.getByTestId('auth-status')).toContainText('Signed in as')
    await expect(page.locator('body')).not.toContainText(ctx.credentials.issuer_a)
  })

  test('a credential split by terminal colour codes is still redacted', () => {
    // A matcher's diff highlighting can fall inside a value; error-context.md strips the codes
    // when it is written, which would join the value again (Stage 10B-3). Nothing is written here.
    const token = ctx.credentials.issuer_a
    const highlighted = `Received: "${token.slice(0, 5)}\u001b[7m${token.slice(5, 9)}\u001b[27m${token.slice(9)}"`
    expect(redact(highlighted)).toBe(`Received: "${REDACTED}"`)
  })

  test('a deliberately failing test leaves no credential in its artifacts', async ({ page }) => {
    test.fail(true, 'deliberate failure: its screenshot, error-context.md and report entry are scanned for credentials')
    await signIn(page, ctx.credentials.issuer_a)
    await page.getByRole('button', { name: /^Credentials( Credentials set)?$/ }).click()
    const field = page.getByLabel('User Token (Bearer)', { exact: true })
    await expect(field).toHaveAttribute('type', 'password')    // rendered masked in any screenshot
    // Fails: Playwright's message names the field's actual value, the credential
    await expect(field).toHaveValue('deliberately-not-the-credential', { timeout: 1_000 })
  })
})
