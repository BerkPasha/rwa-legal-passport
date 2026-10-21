import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'
import { useEffect, useState, type ReactNode } from 'react'
import { useAuth } from '../AuthContext'
import { useServerInfo } from '../serverInfo'
import { BrandMark } from './BrandMark'

export const ROLE_LABELS: Record<string, string> = {
  platform_admin: 'Platform Operator',
  issuer_reviewer: 'Issuer Reviewer',
  independent_legal_reviewer: 'Independent Legal Reviewer',
}

// The page title for every route; anything else is a page that does not exist
export function pageTitle(pathname: string): string {
  const p = pathname.replace(/\/+$/, '') || '/'
  if (p === '/dashboard') return 'Dashboard'
  if (/^\/assets\/[^/]+\/documents\/[^/]+\/extractions\/[^/]+$/.test(p)) return 'Extraction Detail'
  if (/^\/assets\/[^/]+$/.test(p)) return 'Asset Detail'
  if (p === '/assets') return 'Asset Registry'
  if (p === '/passport') return 'Legal Passport'
  if (p === '/api-docs') return 'API Reference'
  return 'Page Not Found'
}

// A read-only showcase or synthetic dataset, as the server declares it (Stage 10C-1, GRANT_DEMO.md); the
// public website shows it too
export function DeploymentBanner() {
  const info = useServerInfo()
  if (!info || (!info.read_only && !info.synthetic_data)) return null
  return (
    <div role="note" data-testid="deployment-banner" className="border-b border-amber-200 bg-amber-50 px-6 py-2 text-xs text-amber-900">
      {info.read_only && <span className="font-semibold">Read-only showcase: nothing can be created or changed on this server. </span>}
      {info.synthetic_data && 'All assets, documents, reviewers and institutions shown here are synthetic and fictional. '}
      Evidence anchoring uses Stellar Testnet only. This is not a production service, and nothing shown here is legal advice.
    </div>
  )
}

// Whom the server says the credential belongs to (Stage 10B-2); never the credential itself
function AuthStatus() {
  const { identity, apiKey } = useAuth()
  let text: ReactNode
  switch (identity.state) {
    case 'none':
      text = 'Not signed in'
      break
    case 'checking':
      text = 'Checking credential…'
      break
    case 'signed_in':
      text = (
        <>
          <span className="block text-mist">Signed in as {identity.user.display_name}</span>{' '}
          <span className="block">
            {ROLE_LABELS[identity.user.role] ?? identity.user.role}
            {' · '}
            {identity.user.scope === 'global' ? 'all issuers' : `issuers: ${identity.user.issuer_ids.join(', ')}`}
          </span>
        </>
      )
      break
    case 'rejected':
      text = identity.status === 401 ? 'Credential not accepted' : `Credential refused (HTTP ${identity.status})`
      break
    case 'unavailable':
      text = 'Credential could not be checked: the server is not reachable'
      break
  }
  return (
    <div role="status" aria-live="polite" data-testid="auth-status" className="mb-3 px-3 text-xs leading-relaxed text-ink-muted">
      {text}
      {apiKey && <span className="block">Institutional API key set (checked on passport lookup)</span>}
    </div>
  )
}

const nav = [
  {
    to: '/dashboard',
    label: 'Dashboard',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M3.75 6A2.25 2.25 0 016 3.75h2.25A2.25 2.25 0 0110.5 6v2.25a2.25 2.25 0 01-2.25 2.25H6a2.25 2.25 0 01-2.25-2.25V6zM3.75 15.75A2.25 2.25 0 016 13.5h2.25a2.25 2.25 0 012.25 2.25V18a2.25 2.25 0 01-2.25 2.25H6A2.25 2.25 0 013.75 18v-2.25zM13.5 6a2.25 2.25 0 012.25-2.25H18A2.25 2.25 0 0120.25 6v2.25A2.25 2.25 0 0118 10.5h-2.25a2.25 2.25 0 01-2.25-2.25V6zM13.5 15.75a2.25 2.25 0 012.25-2.25H18a2.25 2.25 0 012.25 2.25V18A2.25 2.25 0 0118 20.25h-2.25A2.25 2.25 0 0113.5 18v-2.25z" />
      </svg>
    ),
  },
  {
    to: '/assets',
    label: 'Asset Registry',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
      </svg>
    ),
  },
  {
    to: '/passport',
    label: 'Legal Passport',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
      </svg>
    ),
  },
  {
    to: '/api-docs',
    label: 'API Reference',
    icon: (
      <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
        <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
      </svg>
    ),
  },
]

function CredentialsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { userToken, apiKey, setUserToken, setApiKey } = useAuth()
  if (!open) return null
  return (
    <div className="absolute bottom-full left-0 right-0 mx-3 mb-2 rounded-sm bg-ink-raised p-4 shadow-2xl ring-1 ring-white/10">
      <div className="mb-3 flex items-center justify-between">
        <span className="font-label text-[10.5px] uppercase tracking-[0.16em] text-ink-muted">Credentials</span>
        <button onClick={onClose} aria-label="Close credentials" className="text-ink-muted hover:text-mist">
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
          </svg>
        </button>
      </div>
      <label htmlFor="credential-user-token" className="mb-1 block text-xs text-ink-muted">User Token (Bearer)</label>
      <input
        id="credential-user-token"
        aria-describedby="credential-storage-note"
        type="password"
        value={userToken}
        onChange={e => setUserToken(e.target.value)}
        placeholder="Bearer token…"
        autoComplete="off"
        className="mb-3 w-full rounded-sm bg-ink px-3 py-1.5 text-xs text-mist placeholder-ink-muted outline-none ring-1 ring-white/15 focus:ring-accent-light"
      />
      <label htmlFor="credential-api-key" className="mb-1 block text-xs text-ink-muted">Institutional API Key</label>
      <input
        id="credential-api-key"
        aria-describedby="credential-storage-note"
        type="password"
        value={apiKey}
        onChange={e => setApiKey(e.target.value)}
        placeholder="X-API-Key…"
        autoComplete="off"
        className="w-full rounded-sm bg-ink px-3 py-1.5 text-xs text-mist placeholder-ink-muted outline-none ring-1 ring-white/15 focus:ring-accent-light"
      />
      <p id="credential-storage-note" className="mt-2 text-xs text-ink-muted">Kept for this browser tab only.</p>
    </div>
  )
}

export function Layout() {
  const location = useLocation()
  const [credOpen, setCredOpen] = useState(false)
  const { userToken, apiKey } = useAuth()
  const hasCredentials = userToken || apiKey

  const title = pageTitle(location.pathname)
  useEffect(() => { document.title = `${title} · RWA Legal Passport` }, [title])

  return (
    <div className="flex h-screen overflow-hidden bg-paper">
      {/* Sidebar */}
      <aside className="flex w-64 shrink-0 flex-col border-r border-white/[0.06] bg-ink">
        {/* Logo: back to the public website */}
        <Link to="/" aria-label="RWA Legal Passport website" className="flex h-16 items-center gap-3 border-b border-white/[0.07] px-5 text-mist transition-colors hover:bg-white/[0.03]">
          <BrandMark className="h-7 w-7 shrink-0" />
          <div className="leading-tight">
            <p className="font-display text-[14px] font-semibold tracking-[-0.01em]">RWA Legal Passport</p>
            <p className="font-label text-[10px] uppercase tracking-[0.16em] text-ink-muted">Workspace</p>
          </div>
        </Link>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-5">
          <p className="mb-2 px-3 font-label text-[10px] uppercase tracking-[0.16em] text-ink-muted/70">Records</p>
          <ul className="space-y-0.5">
            {nav.map(({ to, label, icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={to === '/dashboard'}
                  className={({ isActive }) =>
                    `flex items-center gap-3 border-l-2 px-3 py-2.5 font-body text-[14px] transition-colors ${
                      isActive
                        ? 'border-accent-light bg-white/[0.05] text-mist'
                        : 'border-transparent text-ink-soft hover:bg-white/[0.03] hover:text-mist'
                    }`
                  }
                >
                  <span className="opacity-80">{icon}</span>
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Credentials toggle */}
        <div className="relative border-t border-white/[0.07] p-3">
          <AuthStatus />
          <CredentialsPanel open={credOpen} onClose={() => setCredOpen(false)} />
          <button
            onClick={() => setCredOpen(o => !o)}
            aria-expanded={credOpen}
            className="flex w-full items-center gap-3 border border-white/10 px-3 py-2.5 font-body text-[14px] text-ink-soft transition-colors hover:border-white/25 hover:text-mist"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
            </svg>
            <span>Credentials</span>
            {hasCredentials && (
              <span className="ml-auto h-2 w-2 rounded-full bg-accent-light" title="Credentials set" />
            )}
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* Header */}
        <header className="flex h-16 shrink-0 items-center gap-4 border-b border-paper-line bg-[#FAFAF7] px-6 lg:px-8">
          <span aria-hidden className="hidden font-label text-[10.5px] uppercase tracking-[0.16em] text-paper-muted sm:inline">Workspace /</span>
          <h1 className="font-display text-[17px] font-semibold tracking-[-0.01em] text-graphite">{title}</h1>
          <div className="ml-auto flex items-center gap-2">
            <span title="For internal review only: not legal advice, regulatory certification or investment guidance"
                  className="inline-flex items-center gap-2 border border-paper-line px-2.5 py-1 font-label text-[10.5px] uppercase tracking-[0.14em] text-paper-muted">
              <span aria-hidden className="h-1.5 w-1.5 bg-accent" />
              Internal Use Only
            </span>
          </div>
        </header>
        <DeploymentBanner />

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6 lg:p-8">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
