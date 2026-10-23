import { createHash } from 'node:crypto'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { PublicSite } from '../PublicSite'
import { DEMO_LABEL, DEMO_RECORDS, recordByCode } from '../demo/data'
import { MANIFEST_V1, MANIFEST_V2 } from '../demo/manifests'
import { paths } from '../demo/ui'
import { DEMO_DIGEST_V1, DEMO_DIGEST_V2, DEMO_HISTORY, DEMO_TX_V1, DEMO_TX_V2 } from '../landing/demoRecord'

// The public, read-only demo at /demo (demo/): bundled synthetic records rendered by the application's own
// record components, with no backend, no credential, no request and no action that changes anything.
vi.mock('../site', () => ({ PUBLIC_SITE: true }))

const LPDEMO = recordByCode('LPDEMO')!
const LPFUND = recordByCode('LPFUND')!
const lpdemoDoc = LPDEMO.documents[0]
const [lpdemoV1Claims, lpdemoV2Claims] = lpdemoDoc.extractions
const FIRST_LOAD = { timeout: 5000 }

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><PublicSite /></MemoryRouter>)
}

const watchRequests = () => ({ fetch: vi.spyOn(globalThis, 'fetch'), xhr: vi.spyOn(XMLHttpRequest.prototype, 'open') })
let requests: ReturnType<typeof watchRequests>
beforeEach(() => { requests = watchRequests() })
afterEach(() => {
  // No page or interaction of the demo sends a request
  expect(requests.fetch).not.toHaveBeenCalled()
  expect(requests.xhr).not.toHaveBeenCalled()
  vi.restoreAllMocks()
})

// The application's product actions, which must never appear in the demo, even as inert buttons
const APP_ACTIONS = ['Register Asset', 'Upload Document', 'Upload New Version', 'Add Manual Claim', 'Run AI Extraction',
  'Download PDF', 'Start Review', 'Start Re-review', 'Approve Extraction', 'Add Your Approval', 'Correct',
  'Anchor current evidence on Testnet', 'Re-check on Stellar', 'Refresh pending transaction', 'Credentials', 'Fetch Legal Passport']

function expectReadOnly(container: HTMLElement) {
  expect(container.querySelectorAll('form, input[type="file"], input[type="password"], textarea')).toHaveLength(0)
  // Every button only changes the view: a tab, a choice, a toggle, or a page of the source text
  const buttons = [...container.querySelectorAll('button')]
  const actions = buttons.filter(b => !(['tab', 'radio'].includes(b.getAttribute('role') ?? '') || b.hasAttribute('aria-pressed')
    || b.hasAttribute('aria-expanded') || /^View page \d+ of the source text/.test(b.getAttribute('aria-label') ?? '')))
  expect(actions.map(b => b.textContent)).toEqual([])
  const labels = [...container.querySelectorAll('button, a')].map(e => e.textContent?.replace(/[→←↗]/g, '').trim())
  expect(labels.filter(text => APP_ACTIONS.includes(text ?? ''))).toEqual([])
  expect(container).not.toHaveTextContent(/Not signed in|Failed to load|Sign in to read|not authenticated/i)
  // External links lead only to public Stellar Testnet records
  const external = [...container.querySelectorAll('a')].map(a => a.getAttribute('href')!).filter(h => /^https?:/.test(h))
  expect(external.filter(h => !/^https:\/\/(stellar\.expert\/explorer\/testnet|horizon-testnet\.stellar\.org)\//.test(h))).toEqual([])
}

describe('Public demo', () => {
  it('opens on a populated overview, labelled as a read-only demo of synthetic Testnet data', async () => {
    const { container } = renderAt('/demo')
    expect(await screen.findByTestId('demo-banner', {}, FIRST_LOAD)).toHaveTextContent(DEMO_LABEL)
    expect(DEMO_LABEL).toBe('Interactive Demo — Synthetic Data — Read Only — Stellar Testnet')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('Overview')
    await waitFor(() => expect(document.title).toBe('Overview · Interactive Demo · RWA Legal Passport'))
    const assets = screen.getByRole('region', { name: 'Assets' })
    for (const code of ['LPDEMO', 'LPFUND', 'LPNOTE']) expect(within(assets).getByRole('link', { name: code })).toBeInTheDocument()
    expect(within(assets).getByText('Approval current')).toBeInTheDocument()
    expect(within(assets).getByText('Reapproval required')).toBeInTheDocument()
    const queue = screen.getByRole('region', { name: 'Requires attention' })
    expect(within(queue).getAllByRole('link')).toHaveLength(2)
    expect(screen.getAllByRole('link', { name: /Back to website/ })[0]).toHaveAttribute('href', '/')
    expectReadOnly(container)
  })

  it('opens every page by direct navigation, without any request', async () => {
    const pages: [string, string][] = [
      ['/demo/assets', 'Asset Registry'],
      [paths.asset(LPDEMO.asset.id), 'Asset Detail'],
      [paths.asset(LPDEMO.asset.id, 'changes'), 'Asset Detail'],
      [paths.asset(LPDEMO.asset.id, 'history'), 'Asset Detail'],
      [paths.asset(LPDEMO.asset.id, 'anchor'), 'Asset Detail'],
      [paths.claims(LPDEMO.asset.id, lpdemoDoc.document.id, lpdemoV1Claims.id), 'Legal Claims'],
      [paths.passportOf('LPDEMO'), 'Legal Passport'],
      [paths.passportOf('LPFUND', 'history'), 'Legal Passport'],
      [paths.passportOf('LPNOTE', 'anchor'), 'Legal Passport'],
      ['/demo/no-such-page', 'Page Not Found'],
    ]
    for (const [path, title] of pages) {
      const { container, unmount } = renderAt(path)
      expect(await screen.findByRole('heading', { level: 1, name: title }, FIRST_LOAD)).toBeInTheDocument()
      expectReadOnly(container)
      unmount()
    }
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(requests.fetch).not.toHaveBeenCalled()
    expect(requests.xhr).not.toHaveBeenCalled()
  })

  it('explains what changed and why the earlier approvals stopped applying', async () => {
    const user = userEvent.setup()
    renderAt(paths.asset(LPDEMO.asset.id, 'changes'))
    // The invalidating event is selected first
    const state = await screen.findByTestId('timeline-state', {}, FIRST_LOAD)
    expect(state).toHaveTextContent('Prospectus version 2 uploaded')
    expect(screen.getByTestId('timeline-consequence')).toHaveTextContent('Version 1’s approvals stopped applying at once')
    expect(state).toHaveTextContent('Withheld until the current version is approved again')
    expect(state).toHaveTextContent('v1 Superseded — no longer applies')
    expect(state).toHaveTextContent('Latest commitment no longer matches the current evidence')
    // The re-approved and re-anchored version
    const events = screen.getAllByTestId('timeline-event')
    await user.click(events[events.length - 2])
    expect(state).toHaveTextContent('Version 2 evidence anchored on Stellar Testnet')
    expect(state).toHaveTextContent('Served from the current approvals')
    expect(state).toHaveTextContent('v2 Approval current')
    expect(state).toHaveTextContent('Latest commitment matches the current evidence')
    // Field by field, from the superseded version to the current one
    expect(screen.getByTestId('comparison-income_rights')).toHaveTextContent('Changed')
    expect(screen.getByTestId('comparison-jurisdiction')).toHaveTextContent('No longer asserted')
    expect(screen.getByTestId('comparison-voting_rights')).toHaveTextContent('Unknown in both')
  })

  it('shows the passport history with the superseded version and the approval that replaced it', async () => {
    renderAt(paths.asset(LPDEMO.asset.id, 'history'))
    const versions = await screen.findAllByTestId('history-version', {}, FIRST_LOAD)
    expect(versions).toHaveLength(2)
    expect(screen.getByTestId('history-summary')).toHaveTextContent('A current Legal Passport is available.')
    expect(within(versions[0]).getByTestId('history-invalidation')).toHaveTextContent('A newer document version exists')
    expect(within(versions[1]).getByText('Used by the current Legal Passport')).toBeInTheDocument()
  })

  it('leads from superseded claims to the current ones, and marks the cited quotation in the page text', async () => {
    const user = userEvent.setup()
    renderAt(paths.claims(LPDEMO.asset.id, lpdemoDoc.document.id, lpdemoV1Claims.id))
    const notice = await screen.findByTestId('demo-superseded-notice', {}, FIRST_LOAD)
    expect(screen.getAllByTestId('demo-approval').map(a => a.textContent)).toEqual([
      expect.stringContaining('No longer current'), expect.stringContaining('No longer current'),
    ])
    expect(screen.getAllByTestId('demo-approval')[0]).toHaveTextContent('Why: A newer document version exists')
    await user.click(within(notice).getByRole('link', { name: 'Open the claims of version 2 →' }))
    expect(await screen.findByTestId('demo-extraction-version')).toHaveTextContent('Document version 2 (current)')
    expect(screen.queryByTestId('demo-superseded-notice')).not.toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'View page 2 of the source text for Income Rights' }))
    expect(screen.getByTestId('source-page')).toHaveAttribute('data-page', '2')
    expect(screen.getByTestId('quotation-focused')).toHaveTextContent('Net rental income shall be distributed quarterly')
    await user.click(screen.getByRole('button', { name: 'Details for Income Rights' }))
    expect(screen.getByText('The quotation appears on the cited page 2. This proves source-text presence only, not legal correctness.')).toBeInTheDocument()
    expect(screen.getByTestId('demo-review-read-only')).toHaveTextContent('Read-only demo')
  })

  it('shows the Stellar Testnet evidence as a recorded check, never as a live verification', async () => {
    renderAt(paths.asset(LPDEMO.asset.id, 'anchor'))
    expect(await screen.findByTestId('recorded-check-note', {}, FIRST_LOAD)).toHaveTextContent('Recorded check — not a live verification')
    expect(screen.getByTestId('anchor-tx-hash')).toHaveTextContent(DEMO_TX_V2)
    expect(screen.queryByText('Live network checks')).not.toBeInTheDocument()
    expect(screen.getByText('Network checks recorded on 19 Oct 2026, 22:31:22 UTC')).toBeInTheDocument()
    expect(screen.getByTestId('demo-anchor-read-only')).toHaveTextContent('no re-check is run')
    expect(screen.getAllByTestId('demo-anchor-match').map(m => m.textContent)).toEqual([
      'Matches the current evidence', 'Does not match the current evidence',
    ])
    expect(screen.getByRole('link', { name: `tx ${DEMO_TX_V1.slice(0, 8)}…${DEMO_TX_V1.slice(-8)} ↗` }))
      .toHaveAttribute('href', `https://stellar.expert/explorer/testnet/tx/${DEMO_TX_V1}`)
  })

  it('states that a synthetic record is not anchored', async () => {
    renderAt(paths.asset(LPFUND.asset.id, 'anchor'))
    expect(await screen.findByTestId('demo-not-anchored', {}, FIRST_LOAD)).toHaveTextContent('Not anchored')
    expect(screen.queryByTestId('anchor-card')).not.toBeInTheDocument()
  })

  it('serves the Legal Passport of an approved asset only, and switches between assets', async () => {
    const user = userEvent.setup()
    renderAt('/demo/passport')
    expect(await screen.findByTestId('passport-card', {}, FIRST_LOAD)).toHaveTextContent('Legal Passport — v2')
    expect(screen.getByTestId('demo-request')).toHaveTextContent('X-API-Key: ‹institutional API key›')
    await user.click(screen.getByRole('radio', { name: /LPFUND/ }))
    expect(screen.queryByTestId('passport-card')).not.toBeInTheDocument()
    expect(screen.getByTestId('demo-no-passport')).toHaveTextContent('a newer document version exists')
    await user.click(screen.getByRole('tab', { name: 'Passport History' }))
    expect(screen.getByTestId('history-summary')).toHaveTextContent('Reapproval is required')
    await user.click(screen.getByRole('radio', { name: /LPNOTE/ }))
    expect(screen.getByTestId('history-requirement')).toHaveTextContent('Not approved yet')
  })

  it('filters the registry without leaving the page', async () => {
    const user = userEvent.setup()
    renderAt('/demo/assets')
    expect(await screen.findAllByTestId('registry-row', {}, FIRST_LOAD)).toHaveLength(3)
    await user.click(screen.getByRole('button', { name: 'Needs review' }))
    expect(screen.getAllByTestId('registry-row').map(r => r.textContent)).toEqual([
      expect.stringContaining('LPFUND'), expect.stringContaining('LPNOTE'),
    ])
    await user.click(screen.getByRole('button', { name: 'All' }))
    await user.type(screen.getByRole('searchbox', { name: 'Search assets' }), 'meridian')
    expect(screen.getAllByTestId('registry-row')).toHaveLength(1)
  })
})

describe('Demo records', () => {
  it('cite only quotations that are on their cited page, and approve only fully verified claims', () => {
    const squash = (text: string) => text.replace(/\s+/g, ' ')
    for (const record of DEMO_RECORDS) {
      for (const doc of record.documents) {
        for (const extraction of doc.extractions) {
          const pages = doc.pages[extraction.version_id]
          expect(extraction.claims.map(c => c.field_name).sort()).toEqual([
            'income_rights', 'insolvency_provisions', 'jurisdiction', 'ownership_nature', 'redemption_rights',
            'transfer_restrictions', 'voting_rights'])
          for (const claim of extraction.claims.filter(c => !c.is_unknown)) {
            const page = pages.find(p => p.page_number === claim.source_page)!
            expect(squash(page.text), claim.id).toContain(squash(claim.supporting_passage!))
            expect(claim.evidence.status, claim.id).toBe('verified')
            expect(claim.supporting_passage!.length).toBeGreaterThanOrEqual(20)
          }
        }
      }
    }
  })

  it('keep LPDEMO identical to the recorded demo record and its committed manifests', () => {
    expect(createHash('sha256').update(MANIFEST_V1, 'utf8').digest('hex')).toBe(DEMO_DIGEST_V1)
    expect(createHash('sha256').update(MANIFEST_V2, 'utf8').digest('hex')).toBe(DEMO_DIGEST_V2)
    expect(LPDEMO.verification?.committed_manifest_canonical_json).toBe(MANIFEST_V2)
    expect(lpdemoDoc.versions.map(v => v.sha256_hash)).toEqual(DEMO_HISTORY.documents[0].versions.map(v => v.sha256_hash))
    expect(lpdemoV2Claims.approval_state.state).toBe('current')
    expect(LPDEMO.passport?.approvals.map(a => a.binding_digest)).toEqual(
      DEMO_HISTORY.documents[0].approved_versions[1].approvals.map(a => a.binding_digest))
    // Only LPDEMO carries Stellar records; the synthetic records have none
    expect(DEMO_RECORDS.filter(r => r.history.anchors.length > 0 || r.verification).map(r => r.asset.stellar_asset_code)).toEqual(['LPDEMO'])
  })

  it('contain no credential, seed or private address', () => {
    const text = JSON.stringify(DEMO_RECORDS) + MANIFEST_V1 + MANIFEST_V2
    expect(text).not.toMatch(/\bS[A-Z2-7]{55}\b/)                        // a Stellar secret seed
    expect(text).not.toMatch(/sk-ant|test-[a-z]+-token|inst-key|Bearer|localhost|127\.0\.0\.1/i)
    expect(text).not.toMatch(/alice|bob|reviewer_id/i)                  // reviewers appear by role only
  })
})
