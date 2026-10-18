import { defineConfig } from '@playwright/test'

// Real-browser end-to-end tests (Stage 10B-1, E2E_TESTING.md). Run through scripts/e2e_run.py, which
// starts an isolated backend and a production build of this frontend and sets the E2E_* variables.
const output = process.env.E2E_OUTPUT ?? 'test-results/e2e'

// The page snapshot that Playwright adds to error-context.md contains the values of input fields,
// including typed credentials: it is always off (Stage 10B-2). Workers inherit this variable.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1'

export default defineConfig({
  testDir: './e2e',
  testMatch: /.*\.e2e\.ts/,           // not *.test.ts / *.spec.ts, so Vitest never picks them up
  fullyParallel: false,
  workers: 1,                         // one shared, seeded database per environment
  retries: 0,
  forbidOnly: true,
  timeout: 30_000,
  expect: { timeout: 10_000 },
  outputDir: `${output}/artifacts`,
  // No HTML report and no traces: they record typed values, and the tests type credentials
  reporter: [['list'], ['json', { outputFile: `${output}/report.json` }]],
  use: {
    baseURL: process.env.E2E_BASE_URL,
    browserName: 'chromium',
    channel: 'chromium',              // Playwright's full Chromium build (new headless mode)
    headless: true,
    locale: 'en-US',
    timezoneId: 'UTC',
    viewport: { width: 1440, height: 900 },
    trace: 'off',
    video: 'off',
    screenshot: 'only-on-failure',
  },
  projects: [
    { name: 'synthetic', testMatch: /synthetic[\\/].*\.e2e\.ts/ },
    { name: 'showcase', testMatch: /showcase[\\/].*\.e2e\.ts/ },
    { name: 'rehearsal', testMatch: /rehearsal[\\/].*\.e2e\.ts/ },
    { name: 'demo-offline', testMatch: /demo-offline[\\/].*\.e2e\.ts/ },
    { name: 'demo-testnet', testMatch: /demo-testnet[\\/].*\.e2e\.ts/ },
  ],
})
