import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { PublicSite } from '../PublicSite'

// The public website build (site.ts, `npm run build:public`, Vercel): the landing page and the read-only demo
// at /demo, with no application route, no backend request and no credential handling.
vi.mock('../site', () => ({ PUBLIC_SITE: true }))

function renderAt(path: string) {
  return render(<MemoryRouter initialEntries={[path]}><PublicSite /></MemoryRouter>)
}

afterEach(() => vi.restoreAllMocks())

describe('Public website build', () => {
  it('leads every product entry to the read-only demo, never to the application', () => {
    const { container } = renderAt('/')
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The legal evidence layer for real-world assets.')
    const primary = screen.getByRole('navigation', { name: 'Primary' })
    expect(within(primary).getByRole('link', { name: 'Launch App' })).toHaveAttribute('href', '/demo')
    expect(within(primary).queryByRole('link', { name: 'View Product' })).not.toBeInTheDocument()
    const cta = screen.getByRole('region', { name: 'See the evidence behind an asset.' })
    expect(within(cta).getByRole('link', { name: 'Launch Interactive Demo' })).toHaveAttribute('href', '/demo')
    expect(within(cta).getByRole('link', { name: 'See the product' })).toHaveAttribute('href', '#product')
    expect(within(cta).getByRole('link', { name: 'How it works' })).toHaveAttribute('href', '#how-it-works')
    expect(cta).toHaveTextContent('Explore the interactive demo, with no sign-in')
    const footer = screen.getByRole('navigation', { name: 'Footer' })
    expect(within(footer).getByRole('link', { name: 'Legal Passport' })).toHaveAttribute('href', '/demo/passport')
    // No route of the application: every route link is a demo page, and every in-page link has its target
    const hrefs = [...container.querySelectorAll('a')].map(a => a.getAttribute('href')!)
    expect(hrefs.filter(href => href.startsWith('/') && !/^\/demo(\/|$)/.test(href))).toEqual([])
    for (const href of hrefs.filter(h => h.startsWith('#'))) expect(document.getElementById(href.slice(1)), href).not.toBeNull()
    // External links only to public Stellar Testnet records (explorer and Horizon), never to a backend
    const external = hrefs.filter(href => /^https?:/.test(href))
    expect(external.filter(href => !/^https:\/\/(stellar\.expert\/explorer\/testnet|horizon-testnet\.stellar\.org)\//.test(href))).toEqual([])
  })

  it('sends no request when it is opened: there is no server behind it', async () => {
    const fetch = vi.spyOn(globalThis, 'fetch')
    renderAt('/')
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.queryByTestId('deployment-banner')).not.toBeInTheDocument()
  })

  it('answers every application address with the website itself', () => {
    for (const path of ['/dashboard', '/passport', '/assets/asset-001', '/api-docs', '/no-such-page']) {
      const { unmount } = renderAt(path)
      expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('The legal evidence layer for real-world assets.')
      expect(screen.queryByTestId('auth-status')).not.toBeInTheDocument()
      unmount()
    }
  })
})
