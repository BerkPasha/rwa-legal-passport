import { test as base, expect, type Page, type APIRequestContext } from '@playwright/test'
import { readFileSync } from 'node:fs'

// Shared helpers of the real-browser end-to-end tests (E2E_TESTING.md). The context file is written
// by scripts/e2e_run.py (identifiers and the run's fictional credentials); it lives in the run's
// temporary directory and is deleted with it.

export interface SeededDocument {
  id: string; title: string; filename?: string; version_id: string; version_number: number; sha256: string
  size?: number; page_count: number; pages_without_text: number[]
}

export interface SeededAsset {
  id: string
  issuer_id: string
  stellar_asset_code: string
  stellar_issuer_public_key: string
  issuer_organization_name: string
  blockchain_network: string
  asset_type: string
  jurisdiction: string
  status: string
  documents: SeededDocument[]
  extraction?: { id: string; document_id: string;
                 claims: Record<string, { known: boolean; cited_page: number | null; evidence_status: string }> }
  approval_types?: string[]
}

export interface E2EContext {
  environment: string
  network?: 'offline' | 'testnet'
  credentials: Record<string, string>
  seed_model?: string
  assets?: Record<string, SeededAsset>
  fixtures?: Record<string, { path: string; sha256: string; pages: number; size: number }>
  spare_issuer_keys?: string[]
  display_names?: Record<string, string>
  damaged_documents?: { missing: string; altered: string }
  // The manual claim journey (Stage 10B-3): what the browser types, from the new synthetic PDF
  manual_journey?: { field_name: string; value: string; page: number; quotation: string;
                     fabricated_quotation: string; page_text: string }
  asset?: { id: string; network: string; asset_code: string; issuer_key: string }
  anchor?: { manifest_digest: string; transaction_hash: string; ledger: number; anchor_account: string; horizon: string; explorer: string }
}

function loadContext(): E2EContext {
  const path = process.env.E2E_CONTEXT
  if (!path) throw new Error('E2E_CONTEXT is not set: run the suite through scripts/e2e_run.py (E2E_TESTING.md)')
  return JSON.parse(readFileSync(path, 'utf-8')) as E2EContext
}

export const ctx = loadContext()

// This run's credential values. Any of them in a failure message (a matcher's "Received" value,
// an element preview in the call log) is replaced before Playwright writes the error to
// error-context.md, the JSON report or the console (Stage 10B-2). Terminal colour codes are removed
// first: a matcher's diff highlighting can fall inside a value, and error-context.md strips them
// when it is written, which would join the value again (found by the self-test, Stage 10B-3).
export const REDACTED = '[credential redacted]'
const CREDENTIAL_VALUES = Object.values(ctx.credentials).filter(v => typeof v === 'string' && v.length >= 8)
const ANSI_CODES = /\u001b\[[0-9;]*m/g
export const redact = (text: string) =>
  CREDENTIAL_VALUES.reduce((t, v) => t.split(v).join(REDACTED), text.replace(ANSI_CODES, ''))
export const API_URL = process.env.E2E_API_URL ?? ''
const LOCAL_ORIGINS = [new URL(process.env.E2E_BASE_URL ?? 'http://127.0.0.1').origin, API_URL && new URL(API_URL).origin]

// ── Diagnostics: browser console errors, page errors and failed requests ─────

export class Diagnostics {
  readonly consoleErrors: string[] = []
  readonly pageErrors: string[] = []
  readonly failedRequests: { method: string; path: string; status: number | string }[] = []
  readonly externalRequests: string[] = []
  private allowed: RegExp[] = []

  constructor(page: Page) {
    page.on('console', message => { if (message.type() === 'error') this.consoleErrors.push(message.text()) })
    page.on('pageerror', error => this.pageErrors.push(error.message))
    // Method, path and status only: never headers (they carry the credentials), queries or bodies
    page.on('requestfailed', request => this.failedRequests.push(
      { method: request.method(), path: new URL(request.url()).pathname, status: request.failure()?.errorText ?? 'failed' }))
    page.on('response', response => {
      if (response.status() >= 400) {
        this.failedRequests.push({ method: response.request().method(), path: new URL(response.url()).pathname, status: response.status() })
      }
    })
  }

  // Declares console errors that the test provokes on purpose (Chromium logs every failed fetch)
  allow(...patterns: RegExp[]) {
    this.allowed.push(...patterns)
  }

  unexpected(): string[] {
    return [...this.pageErrors.map(e => `page error: ${e}`),
            ...this.consoleErrors.filter(e => !this.allowed.some(p => p.test(e)))]
  }
}

export const test = base.extend<{ diagnostics: Diagnostics }>({
  diagnostics: [async ({ page }, use, testInfo) => {
    const diagnostics = new Diagnostics(page)
    // The browser talks to this run's frontend and API only. Anything else (the stylesheet of an
    // external font service, for example) is answered locally and recorded: the tests never depend
    // on, or send anything to, a third party
    await page.route(url => !LOCAL_ORIGINS.includes(url.origin), route => {
      diagnostics.externalRequests.push(new URL(route.request().url()).origin)
      return route.fulfill({ status: 204, body: '' })
    })
    await use(diagnostics)
    if (testInfo.status !== testInfo.expectedStatus || testInfo.status === 'failed') {
      // Before Playwright captures anything more of a failed page: no credential stays in an input
      // field or in the tab's session storage (Stage 10B-2)
      await page.evaluate(() => {
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set
        for (const input of Array.from(document.querySelectorAll('input'))) setter?.call(input, '')
        sessionStorage.clear()
      }).catch(() => { /* the page may be gone */ })
    }
    // Runs before Playwright writes error-context.md and reports the result: both read these objects
    for (const error of testInfo.errors) {
      // A failed matcher's own page snapshot (it holds input values) is dropped, like the page
      // snapshot that PLAYWRIGHT_NO_COPY_PROMPT turns off
      delete (error as { errorContext?: string }).errorContext
      if (error.message) error.message = redact(error.message)
      if (error.stack) error.stack = redact(error.stack)
      if (error.value) error.value = redact(error.value)
    }
    if (diagnostics.failedRequests.length) {
      await testInfo.attach('failed-requests.json', {
        body: JSON.stringify(diagnostics.failedRequests, null, 2), contentType: 'application/json' })
    }
    if (diagnostics.externalRequests.length) {
      await testInfo.attach('external-requests-answered-locally.json', {
        body: JSON.stringify([...new Set(diagnostics.externalRequests)], null, 2), contentType: 'application/json' })
    }
    expect(diagnostics.unexpected().map(redact), 'unexpected browser console or page errors').toEqual([])
  }, { auto: true }],
})

export { expect }

// ── Credentials, entered through the real sidebar panel ─────────────────────

export async function setCredentials(page: Page, values: { userToken?: string; apiKey?: string }) {
  // Its accessible name gains "Credentials set" (the status dot's title) once a credential is set
  const toggle = page.getByRole('button', { name: /^Credentials( Credentials set)?$/ })
  await toggle.click()
  const userToken = page.getByLabel('User Token (Bearer)', { exact: true })
  if (values.userToken !== undefined) await userToken.fill(values.userToken)
  if (values.apiKey !== undefined) await page.getByLabel('Institutional API Key', { exact: true }).fill(values.apiKey)
  await toggle.click()
  await expect(userToken).toBeHidden()
}

// Opens the application on a page that makes no API request (API Reference), signs in as
// `userToken` (and optionally an institutional key), waits until the server has confirmed the
// identity, and continues with client-side navigation
export async function signIn(page: Page, userToken: string, apiKey?: string) {
  await page.goto('/api-docs')
  await setCredentials(page, { userToken, apiKey })
  await expect(page.getByTestId('auth-status')).toContainText('Signed in as')
}

// A link of the sidebar navigation (the dashboard repeats some of them as cards)
export const navLink = (page: Page, name: string) => page.getByRole('navigation').getByRole('link', { name })

export async function openAsset(page: Page, code: string) {
  await navLink(page, 'Asset Registry').click()
  const row = page.getByRole('row').filter({ hasText: code })
  await expect(row).toHaveCount(1)
  await row.getByRole('link', { name: 'View →' }).click()
  await expect(page.getByRole('heading', { level: 2, name: code })).toBeVisible()
}

export const documentCard = (page: Page, title: string) =>
  page.getByTestId('document-card').filter({ has: page.getByText(title, { exact: true }) })

// The manual claim form of one document card (Stage 10B-3), opened
export async function openManualClaimForm(page: Page, title: string) {
  await documentCard(page, title).getByTestId('add-manual-claim').click()
  const form = documentCard(page, title).getByTestId('manual-claim-form')
  await expect(form.getByLabel('Source page')).toBeEnabled()   // the page text has loaded
  return form
}

// Reads the backend directly (the source of truth the UI must agree with)
export async function api(request: APIRequestContext, path: string, headers: Record<string, string>) {
  const response = await request.get(`${API_URL}${path}`, { headers })
  return { status: response.status(), body: await response.json().catch(() => null) }
}

export const bearer = (token: string) => ({ Authorization: `Bearer ${token}` })
