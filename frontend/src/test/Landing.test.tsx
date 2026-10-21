import { afterEach, describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { App } from '../App'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'
import { DEMO_DIGEST_V2, DEMO_TX_V2 } from '../landing/demoRecord'

// The public website at "/" (pages/Landing.tsx); the application starts at /dashboard.
function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><AuthProvider><App /></AuthProvider></MemoryRouter>)
}

afterEach(() => {
  sessionStorage.clear()
  setUserToken('')
})

describe('Public website', () => {
  it('is the root page, with its headline, its navigation and the way into the application', async () => {
    renderAt('/')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The legal evidence layer for real-world assets.')
    const primary = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(primary).getAllByRole('link').map(a => [a.textContent, a.getAttribute('href')])).toEqual([
      ['Platform', '#platform'], ['Technology', '#how-it-works'], ['Use Cases', '#use-cases'], ['Launch App →', '/dashboard'],
    ])
    expect(screen.getByRole('link', { name: 'Explore Platform' })).toHaveAttribute('href', '#platform')
    expect(screen.getByRole('link', { name: 'How It Works' })).toHaveAttribute('href', '#how-it-works')
    expect(document.title).toBe('RWA Legal Passport · The legal evidence layer for real-world assets')
    // The application's sidebar is not part of the website
    expect(screen.queryByTestId('auth-status')).not.toBeInTheDocument()
  })

  it('has every section, and every in-page link leads to one of them', () => {
    const { container } = renderAt('/')
    for (const title of ['Tokenization moves fast. Legal clarity should too.', 'One legal evidence record for every asset.',
                         "From source document to verifiable record.", "See an asset's legal position at a glance.",
                         'One evidence record. Four operational views.', 'A public commitment to private evidence.',
                         'See the evidence behind an asset.']) {
      expect(screen.getByRole('heading', { level: 2, name: title })).toBeInTheDocument()
    }
    const anchors = [...container.querySelectorAll('a[href^="#"]')].map(a => a.getAttribute('href')!.slice(1))
    expect(anchors.length).toBeGreaterThan(10)
    for (const id of new Set(anchors)) expect(document.getElementById(id), `#${id}`).not.toBeNull()
  })

  it('links every application entry to a real route', () => {
    renderAt('/')
    const routes = new Set(screen.getAllByRole('link').map(a => a.getAttribute('href')!)
      .filter(href => href.startsWith('/')))
    expect(routes).toEqual(new Set(['/dashboard', '/passport', '/assets', '/api-docs']))
  })

  it('shows the demo record with the application\'s own components, labelled as synthetic and Testnet', () => {
    renderAt('/')
    const product = screen.getByRole('region', { name: "See an asset's legal position at a glance." })
    expect(within(product).getByTestId('passport-history')).toBeInTheDocument()
    expect(within(product).getAllByTestId('history-version').map(v => v.dataset.version)).toEqual(['1', '2'])
    expect(within(product).getByTestId('anchor-card')).toHaveTextContent(DEMO_TX_V2)
    expect(within(product).getByTestId('anchor-digests')).toHaveTextContent(DEMO_DIGEST_V2)
    expect(product).toHaveTextContent('The documents, the issuer and the reviewers are synthetic.')
    expect(product).toHaveTextContent('An approval is an internal attestation, not a legal opinion.')
  })

  it('never presents an anchor as legal proof, and claims no customers or metrics', () => {
    renderAt('/')
    const stellar = screen.getByRole('region', { name: 'A public commitment to private evidence.' })
    expect(stellar).toHaveTextContent('It does not establish legal validity, ownership, regulatory compliance or the authenticity of documents.')
    expect(stellar).toHaveTextContent('Mainnet anchoring is disabled.')
    expect(within(stellar).getByRole('link', { name: /tx 843a5a6d/ })).toHaveAttribute('href', `https://stellar.expert/explorer/testnet/tx/${DEMO_TX_V2}`)
    expect(document.body).toHaveTextContent('not a list of customers')
    for (const claim of [/trusted by/i, /testimonial/i, /\bTVL\b/, /audited by/i, /certified/i, /@[a-z0-9-]+\.[a-z]{2,}/i]) {
      expect(document.body.textContent).not.toMatch(claim)
    }
  })

  it('states the business value: today versus with the platform, and why each capability matters', () => {
    renderAt('/')
    const comparison = screen.getByRole('table', { name: 'The legal record today, and with RWA Legal Passport' })
    const rows = within(comparison).getAllByRole('row').slice(1)
    expect(rows.map(r => within(r).getByRole('rowheader').textContent)).toEqual(
      ['01Static records', '02Fragmented documents', '03Unverifiable claims', '04Disconnected approvals'])
    for (const row of rows) expect(within(row).getAllByRole('cell')).toHaveLength(2)
    const platform = screen.getByRole('region', { name: 'One legal evidence record for every asset.' })
    expect(within(platform).getAllByText('Why it matters')).toHaveLength(3)
    const hero = screen.getByRole('region', { name: 'The legal evidence layer for real-world assets.' })
    expect(hero).toHaveTextContent('Less due diligence friction.')
    for (const audience of ['Tokenization platforms', 'Asset issuers', 'Custodians', 'Institutional investors']) {
      expect(within(hero).getByRole('link', { name: audience })).toHaveAttribute('href', '#use-cases')
    }
    // Value is stated without promising certainty, approval or compliance
    for (const promise of [/guarantee/i, /legal certainty/i, /ensures? compliance/i, /regulatory approval/i, /proves? ownership/i]) {
      expect(document.body.textContent).not.toMatch(promise)
    }
  })

  it('opens and closes the menu on small screens', async () => {
    renderAt('/')
    const toggle = screen.getByRole('button', { name: 'Open menu' })
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    await userEvent.click(toggle)
    const menu = screen.getByRole('navigation', { name: 'Menu' })
    expect(within(menu).getByRole('link', { name: 'Launch App' })).toHaveAttribute('href', '/dashboard')
    await userEvent.click(within(menu).getByRole('link', { name: 'Platform' }))
    expect(screen.queryByRole('navigation', { name: 'Menu' })).not.toBeInTheDocument()
  })

  it('leaves the application where it was, now at /dashboard, with the logo back to the website', async () => {
    renderAt('/dashboard')
    expect(await screen.findByRole('heading', { level: 1, name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'RWA Legal Passport website' })).toHaveAttribute('href', '/')
    expect(within(screen.getByRole('navigation')).getByRole('link', { name: 'Dashboard' })).toHaveAttribute('href', '/dashboard')
  })
})
