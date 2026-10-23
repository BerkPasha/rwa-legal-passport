import { useEffect, useRef, useState } from 'react'
import { Link, NavLink, Outlet, Route, Routes, useLocation } from 'react-router-dom'
import { BrandMark } from '../components/BrandMark'
import { DEMO_LABEL } from './data'
import { LABEL, paths } from './ui'
import { Overview } from './Overview'
import { AssetRegistry } from './AssetRegistry'
import { AssetRecord } from './AssetRecord'
import { ClaimsReview } from './ClaimsReview'
import { PassportView } from './PassportView'

// The public, read-only product demo at /demo (part of the public website build, site.ts). It renders the
// application's own record components from bundled synthetic records (data.ts): there is no backend, no
// credential, no request and no action that creates or changes anything.

const NAV = [
  {
    to: paths.overview,
    label: 'Overview',
    icon: 'M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z',
  },
  {
    to: paths.assets,
    label: 'Asset Registry',
    icon: 'M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z',
  },
  {
    to: paths.passport,
    label: 'Legal Passport',
    icon: 'M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z',
  },
]

export function demoPageTitle(pathname: string): string {
  const p = pathname.replace(/\/+$/, '') || '/'
  if (p === '/demo') return 'Overview'
  if (p === '/demo/assets') return 'Asset Registry'
  if (/^\/demo\/assets\/[^/]+\/documents\/[^/]+\/extractions\/[^/]+$/.test(p)) return 'Legal Claims'
  if (/^\/demo\/assets\/[^/]+$/.test(p)) return 'Asset Detail'
  if (p === '/demo/passport') return 'Legal Passport'
  return 'Page Not Found'
}

function NavItems({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <ul className="space-y-0.5">
      {NAV.map(({ to, label, icon }) => (
        <li key={to}>
          <NavLink
            to={to}
            end={to === paths.overview}
            onClick={onNavigate}
            className={({ isActive }) =>
              `flex items-center gap-3 border-l-2 px-3 py-2.5 font-body text-[14px] transition-colors ${
                isActive
                  ? 'border-accent-light bg-white/[0.05] text-mist'
                  : 'border-transparent text-ink-soft hover:bg-white/[0.03] hover:text-mist'
              }`
            }
          >
            <svg aria-hidden className="h-5 w-5 opacity-80" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d={icon} />
            </svg>
            {label}
          </NavLink>
        </li>
      ))}
    </ul>
  )
}

// Where the application shows the credentials panel: what this demo is, and the way back
function DemoNotice() {
  return (
    <div data-testid="demo-notice" className="border border-white/10 p-3.5">
      <p className={`${LABEL} text-accent-light`}>Demo mode</p>
      <p className="mt-2 text-[12.5px] leading-relaxed text-ink-soft">
        Read only, synthetic data. Nothing can be created, uploaded, approved or anchored here, and no server is contacted.
      </p>
      <Link to="/" className="mt-3 inline-flex items-center gap-2 text-[13px] text-mist hover:text-white">
        <span aria-hidden>←</span> Back to website
      </Link>
    </div>
  )
}

function DemoLayout() {
  const { pathname } = useLocation()
  const title = demoPageTitle(pathname)
  const main = useRef<HTMLElement>(null)
  const [menuOpen, setMenuOpen] = useState(false)

  useEffect(() => { document.title = `${title} · Interactive Demo · RWA Legal Passport` }, [title])
  // Every page starts at its top, like a page load
  useEffect(() => {
    main.current?.scrollTo?.(0, 0)
    setMenuOpen(false)
  }, [pathname])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-paper font-body antialiased lg:flex-row">
      {/* Sidebar (large screens) */}
      <aside className="hidden w-64 shrink-0 flex-col border-r border-white/[0.06] bg-ink lg:flex">
        <Link to={paths.overview} className="flex h-16 items-center gap-3 border-b border-white/[0.07] px-5 text-mist transition-colors hover:bg-white/[0.03]">
          <BrandMark className="h-7 w-7 shrink-0" />
          <div className="leading-tight">
            <p className="font-display text-[14px] font-semibold tracking-[-0.01em]">RWA Legal Passport</p>
            <p className="font-label text-[10px] uppercase tracking-[0.16em] text-ink-muted">Interactive demo</p>
          </div>
        </Link>
        <nav aria-label="Demo" className="flex-1 overflow-y-auto px-3 py-5">
          <p className="mb-2 px-3 font-label text-[10px] uppercase tracking-[0.16em] text-ink-muted/70">Records</p>
          <NavItems />
        </nav>
        <div className="border-t border-white/[0.07] p-3">
          <DemoNotice />
        </div>
      </aside>

      {/* Top bar (small screens) */}
      <div className="shrink-0 border-b border-white/[0.07] bg-ink text-mist lg:hidden">
        <div className="flex h-14 items-center justify-between px-4">
          <Link to={paths.overview} className="flex items-center gap-2.5">
            <BrandMark className="h-6 w-6" />
            <span className="font-display text-[14px] font-semibold">RWA Legal Passport</span>
            <span className="font-label text-[10px] uppercase tracking-[0.16em] text-ink-muted">Demo</span>
          </Link>
          <button type="button" onClick={() => setMenuOpen(o => !o)} aria-expanded={menuOpen} aria-controls="demo-menu"
                  className="-mr-2 p-2 text-mist">
            <span className="sr-only">{menuOpen ? 'Close menu' : 'Open menu'}</span>
            <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden fill="none" stroke="currentColor" strokeWidth={1.5}>
              {menuOpen ? <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" /> : <path d="M4 8h16M4 16h16" strokeLinecap="round" />}
            </svg>
          </button>
        </div>
        {menuOpen && (
          <nav id="demo-menu" aria-label="Demo menu" className="space-y-4 border-t border-white/[0.07] px-3 py-4">
            <NavItems onNavigate={() => setMenuOpen(false)} />
            <DemoNotice />
          </nav>
        )}
      </div>

      {/* Main */}
      <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden">
        <div role="note" data-testid="demo-banner"
             className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 border-b border-accent/30 bg-accent/[0.07] px-4 py-2 sm:px-6 lg:px-8">
          <p className="flex items-center gap-2.5 font-label text-[11px] uppercase tracking-[0.12em] text-accent-dark">
            <span aria-hidden className="h-1.5 w-1.5 shrink-0 bg-accent" />
            {DEMO_LABEL}
          </p>
          <Link to="/" className="text-[12.5px] text-graphite/80 hover:text-graphite">Back to website →</Link>
        </div>
        <header className="flex h-14 shrink-0 items-center gap-4 border-b border-paper-line bg-[#FAFAF7] px-4 sm:px-6 lg:h-16 lg:px-8">
          <span aria-hidden className="hidden font-label text-[10.5px] uppercase tracking-[0.16em] text-paper-muted sm:inline">Demo /</span>
          <h1 className="font-display text-[17px] font-semibold tracking-[-0.01em] text-graphite">{title}</h1>
          <span title="Nothing in this demo can be created or changed"
                className="ml-auto inline-flex items-center gap-2 border border-paper-line px-2.5 py-1 font-label text-[10.5px] uppercase tracking-[0.14em] text-paper-muted">
            <span aria-hidden className="h-1.5 w-1.5 bg-accent" />
            Read only
          </span>
        </header>
        <main ref={main} id="demo-main" className="flex-1 overflow-y-auto px-4 py-6 sm:px-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function DemoNotFound() {
  return (
    <div className="mx-auto max-w-xl border border-paper-line bg-white p-8">
      <p className={`${LABEL} text-accent-dark`}>Not found</p>
      <h2 className="mt-3 font-display text-[24px] font-semibold text-graphite">This page is not part of the demo.</h2>
      <p className="mt-2 text-[14px] text-paper-muted">The demo holds three synthetic asset records.</p>
      <Link to={paths.overview} className="mt-5 inline-block text-[14px] font-medium text-accent-dark hover:text-graphite">Go to the overview →</Link>
    </div>
  )
}

export default function DemoApp() {
  return (
    <Routes>
      <Route element={<DemoLayout />}>
        <Route index element={<Overview />} />
        <Route path="assets" element={<AssetRegistry />} />
        <Route path="assets/:assetId" element={<AssetRecord />} />
        <Route path="assets/:assetId/documents/:documentId/extractions/:extractionId" element={<ClaimsReview />} />
        <Route path="passport" element={<PassportView />} />
        <Route path="*" element={<DemoNotFound />} />
      </Route>
    </Routes>
  )
}
