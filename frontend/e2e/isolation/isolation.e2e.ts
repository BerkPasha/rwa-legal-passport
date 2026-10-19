import { createServer } from 'node:net'
import { existsSync, readFileSync } from 'node:fs'
import { test, expect, API_URL, PROBE_SUFFIX, blockExternal, undeclaredExternal } from '../support'

// Positive controls of the offline isolation (Stage 10C-3B, E2E_TESTING.md "Offline isolation").
// scripts/e2e_run.py runs this project first, on the synthetic environment, only after its preflight has
// found the computer disconnected, and stops the run if it fails. Afterwards it checks, from its sinkhole
// and Chromium's net-log, that these probes were refused and that nothing else was attempted.
//
// Every target is local by construction: names under .localhost (Chromium answers them itself, as
// loopback) and 127.0.0.2, a loopback address outside the exact proxy bypass. No page is ever left on a
// DNS error: Chromium's DNS error page runs its own DNS diagnosis, outside the resolver rules.
const probe = (label: string) => `${label}${PROBE_SUFFIX}`
const SINKHOLE_LOG = process.env.E2E_SINKHOLE_LOG ?? ''

interface SinkholeRecord { method: string; host?: string; port?: number; malformed?: boolean }

// What the run's sinkhole has refused so far (method and host:port only)
function refused(host: string): SinkholeRecord[] {
  if (!existsSync(SINKHOLE_LOG)) return []
  return readFileSync(SINKHOLE_LOG, 'utf-8').split('\n').filter(Boolean)
    .map(line => JSON.parse(line) as SinkholeRecord).filter(record => record.host === host)
}
const destinations = (host: string) => [...new Set(refused(host).map(r => `${r.method} ${r.port}`))].sort()

// A listener on 127.0.0.2 that counts connections: one that reached it went around the sinkhole
async function trap() {
  let connections = 0
  const server = createServer(socket => { connections += 1; socket.destroy() })
  await new Promise<void>((resolve, reject) => { server.once('error', reject); server.listen(0, '127.0.0.2', resolve) })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('the trap has no port')
  return { port: address.port, connections: () => connections,
           close: () => new Promise<void>(resolve => server.close(() => resolve())) }
}

test.describe.serial('Offline isolation: positive controls', () => {
  test.beforeAll(() => {
    expect(SINKHOLE_LOG, 'E2E_SINKHOLE_LOG is set by scripts/e2e_run.py').not.toBe('')
  })

  test("the run's own servers are reached directly over 127.0.0.1", async ({ page }) => {
    await page.goto('/api-docs')
    const status = await page.evaluate(async url => (await fetch(`${url}/health`)).status, API_URL)
    expect(status).toBe(200)
    expect(refused('127.0.0.1'), 'loopback traffic went to the sinkhole').toEqual([])
  })

  test('requests that escape the interception of a page reach only the local sinkhole, which refuses them', async ({ browser }) => {
    const bypass = await trap()
    const context = await browser.newContext()        // no route: only the browser's own proxy settings apply
    try {
      const page = await context.newPage()
      await page.goto('/api-docs')
      const outcome = await page.evaluate(async ({ http, https, ip, ws }) => {
        const settle = (request: Promise<unknown>) => request.then(() => 'answered', () => 'failed')
        const socket = await new Promise<string>(resolve => {
          const connection = new WebSocket(ws)
          connection.onopen = () => resolve('open')
          connection.onerror = () => resolve('failed')
        })
        return { http: await settle(fetch(http, { mode: 'no-cors' })), https: await settle(fetch(https, { mode: 'no-cors' })),
                 ip: await settle(fetch(ip, { mode: 'no-cors' })), ws: socket }
      }, { http: `http://${probe('egress')}/probe`, https: `https://${probe('egress-tls')}/probe`,
           ip: `http://127.0.0.2:${bypass.port}/probe`, ws: `ws://${probe('ws')}/probe` })
      expect(outcome.https, 'the refused tunnel').toBe('failed')
      expect(outcome.ws, 'the refused WebSocket').toBe('failed')
      await expect.poll(() => ({ http: destinations(probe('egress')), https: destinations(probe('egress-tls')),
                                 ip: destinations('127.0.0.2'), ws: destinations(probe('ws')) }))
        .toEqual({ http: ['GET 80'], https: ['CONNECT 443'], ip: [`GET ${bypass.port}`], ws: ['CONNECT 80'] })
      expect(bypass.connections(), 'a connection to 127.0.0.2 went around the sinkhole').toBe(0)
    } finally {
      await context.close()
      await bypass.close()
    }
  })

  test("a popup to an external address is refused by the test context's route, before any network", async ({ page, diagnostics }) => {
    diagnostics.expectBlocked(probe('popup'))
    await page.goto('/api-docs')
    await page.evaluate(href => {
      document.body.append(Object.assign(document.createElement('a'), { href, target: '_blank', id: 'isolation-popup', textContent: 'popup probe' }))
    }, `http://${probe('popup')}/probe`)
    const [popup] = await Promise.all([page.context().waitForEvent('page'), page.click('#isolation-popup')])
    await popup.waitForLoadState().catch(() => { /* the refused navigation leaves an error page */ })
    expect(diagnostics.externalRequests).toContain(`http://${probe('popup')}`)
    expect(refused(probe('popup')), 'the popup reached the sinkhole: the context route missed it').toEqual([])
  })

  test('a link rendered to an external address is not contacted (DNS prefetch, preconnect on hover)', async ({ page, diagnostics }) => {
    diagnostics.expectBlocked(probe('prefetch'))
    await page.goto('/api-docs')
    await page.evaluate(href => {
      document.body.append(Object.assign(document.createElement('a'), { href, id: 'isolation-prefetch', textContent: 'prefetch probe' }))
    }, `https://${probe('prefetch')}/probe`)
    await page.hover('#isolation-prefetch')
    await page.waitForTimeout(1_000)
    // A preconnect may only end at the sinkhole; a DNS lookup outside the resolver rules shows in the
    // net-log, which the runner judges after this project
    expect(destinations(probe('prefetch')).filter(d => d !== 'CONNECT 443')).toEqual([])
  })

  test('service workers cannot be registered (they could answer requests that the route never sees)', async ({ page }) => {
    await page.goto('/api-docs')
    const registration = await page.evaluate(async () => (await navigator.serviceWorker.register('/isolation-probe-sw.js')) ?? 'blocked')
    expect(registration).toBe('blocked')
  })

  test('an undeclared external request of the application is refused in the browser and fails the policy', async ({ browser }) => {
    const context = await browser.newContext()
    const external: string[] = []
    await blockExternal(context, origin => external.push(origin))
    try {
      const page = await context.newPage()
      await page.goto('/api-docs')
      await page.evaluate(src => { document.body.append(Object.assign(new Image(), { src })) }, `http://${probe('undeclared')}/pixel.png`)
      await expect.poll(() => external).toContain(`http://${probe('undeclared')}`)
      // What the diagnostics fixture asserts after every test: an undeclared origin fails it
      expect(undeclaredExternal(external, new Set())).toEqual([`http://${probe('undeclared')}`])
      expect(undeclaredExternal(external, new Set([probe('undeclared')]))).toEqual([])
      expect(refused(probe('undeclared')), 'refused in the browser, it never reaches the sinkhole').toEqual([])
    } finally {
      await context.close()
    }
  })

  test('only positive-control hosts can be declared as expected external requests', async ({ diagnostics }) => {
    expect(() => diagnostics.expectBlocked('external.invalid')).toThrow(/only positive-control hosts/)
  })
})
