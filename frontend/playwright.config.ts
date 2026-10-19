import { defineConfig } from '@playwright/test'
import { join } from 'node:path'

// Real-browser end-to-end tests (Stage 10B-1, E2E_TESTING.md). Run through scripts/e2e_run.py, which
// starts an isolated backend and a production build of this frontend and sets the E2E_* variables.
const output = process.env.E2E_OUTPUT ?? 'test-results/e2e'

// The page snapshot that Playwright adds to error-context.md contains the values of input fields,
// including typed credentials: it is always off (Stage 10B-2). Workers inherit this variable.
process.env.PLAYWRIGHT_NO_COPY_PROMPT = '1'

// Offline isolation (Stage 10C-3B, E2E_TESTING.md): scripts/e2e_run.py starts the tests only once its
// preflight has found this computer disconnected, and gives every invocation a local sinkhole and a
// net-log directory. The sinkhole is Chromium's only proxy (which also turns proxy auto-discovery
// off): it records and refuses every request. Only the exact address 127.0.0.1, this run's servers, is
// reached directly, and no other name resolves. These flags are a second line and the evidence, not
// the boundary: Chromium's DNS error page queries DNS outside the resolver rules.
const sinkhole = process.env.E2E_SINKHOLE ?? ''
const netlogDir = process.env.E2E_NETLOG_DIR ?? ''
if (!/^127\.0\.0\.1:\d{1,5}$/.test(sinkhole) || !netlogDir) {
  throw new Error('No offline isolation: run the suite through scripts/e2e_run.py --offline-host (E2E_TESTING.md)')
}

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
    serviceWorkers: 'block',          // a service worker could answer requests that the context's route never sees
    launchOptions: {
      // No --disable-features here: a second one would replace Playwright's own list
      args: [
        `--proxy-server=http://${sinkhole}`,
        '--proxy-bypass-list=<-loopback>;127.0.0.1',     // localhost and 127.0.0.2 go to the sinkhole too
        '--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1',
        `--log-net-log=${join(netlogDir, `netlog-${process.pid}.json`)}`,   // each worker loads this file: one log per browser
      ],
    },
  },
  projects: [
    { name: 'isolation', testMatch: /isolation[\\/].*\.e2e\.ts/ },   // the positive controls, run first
    { name: 'synthetic', testMatch: /synthetic[\\/].*\.e2e\.ts/ },
    { name: 'showcase', testMatch: /showcase[\\/].*\.e2e\.ts/ },
    { name: 'rehearsal', testMatch: /rehearsal[\\/].*\.e2e\.ts/ },
    { name: 'demo-offline', testMatch: /demo-offline[\\/].*\.e2e\.ts/ },
    { name: 'demo-testnet', testMatch: /demo-testnet[\\/].*\.e2e\.ts/ },
  ],
})
