import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { useEffect, useState, type ReactNode } from 'react'
import { useAuth } from '../AuthContext'
import { useServerInfo } from '../serverInfo'

const ROLE_LABELS: Record<string, string> = {
  platform_admin: 'Platform Operator',
  issuer_reviewer: 'Issuer Reviewer',
  independent_legal_reviewer: 'Independent Legal Reviewer',
}

// The page title for every route; anything else is a page that does not exist
export function pageTitle(pathname: string): string {
  const p = pathname.replace(/\/+$/, '') || '/'
  if (p === '/') return 'Dashboard'
  if (/^\/assets\/[^/]+\/documents\/[^/]+\/extractions\/[^/]+$/.test(p)) return 'Extraction Detail'
  if (/^\/assets\/[^/]+$/.test(p)) return 'Asset Detail'
  if (p === '/assets') return 'Asset Registry'
  if (p === '/passport') return 'Legal Passport'
  if (p === '/api-docs') return 'API Reference'
  return 'Page Not Found'
}

// A read-only showcase or synthetic dataset, as the server declares it (Stage 10C-1, GRANT_DEMO.md)
function DeploymentBanner() {
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
          <span className="block text-slate-200">Signed in as {identity.user.display_name}</span>{' '}
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
    <div role="status" aria-live="polite" data-testid="auth-status" className="mb-2 px-3 text-xs text-slate-400">
      {text}
      {apiKey && <span className="block">Institutional API key set (checked on passport lookup)</span>}
    </div>
  )
}

const nav = [
  {
    to: '/',
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
    <div className="absolute bottom-full left-0 right-0 mx-3 mb-2 rounded-lg bg-slate-800 p-4 shadow-xl ring-1 ring-white/10">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-400">Credentials</span>
        <button onClick={onClose} aria-label="Close credentials" className="text-slate-400 hover:text-white">
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M6.28 5.22a.75.75 0 00-1.06 1.06L8.94 10l-3.72 3.72a.75.75 0 101.06 1.06L10 11.06l3.72 3.72a.75.75 0 101.06-1.06L11.06 10l3.72-3.72a.75.75 0 00-1.06-1.06L10 8.94 6.28 5.22z" />
          </svg>
        </button>
      </div>
      <label htmlFor="credential-user-token" className="mb-1 block text-xs text-slate-400">User Token (Bearer)</label>
      <input
        id="credential-user-token"
        aria-describedby="credential-storage-note"
        type="password"
        value={userToken}
        onChange={e => setUserToken(e.target.value)}
        placeholder="Bearer token…"
        autoComplete="off"
        className="mb-3 w-full rounded bg-slate-700 px-3 py-1.5 text-xs text-white placeholder-slate-500 outline-none ring-1 ring-slate-600 focus:ring-indigo-500"
      />
      <label htmlFor="credential-api-key" className="mb-1 block text-xs text-slate-400">Institutional API Key</label>
      <input
        id="credential-api-key"
        aria-describedby="credential-storage-note"
        type="password"
        value={apiKey}
        onChange={e => setApiKey(e.target.value)}
        placeholder="X-API-Key…"
        autoComplete="off"
        className="w-full rounded bg-slate-700 px-3 py-1.5 text-xs text-white placeholder-slate-500 outline-none ring-1 ring-slate-600 focus:ring-indigo-500"
      />
      <p id="credential-storage-note" className="mt-2 text-xs text-slate-500">Kept for this browser tab only.</p>
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
    <div className="flex h-screen overflow-hidden bg-slate-50">
      {/* Sidebar */}
      <aside className="flex w-64 flex-col bg-slate-900">
        {/* Logo */}
        <div className="flex h-16 items-center gap-3 border-b border-slate-800 px-5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-indigo-600">
            <svg className="h-5 w-5 text-white" fill="none" viewBox="0 0 24 24" strokeWidth={2} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
          </div>
          <div>
            <p className="text-sm font-semibold text-white">RWA Legal</p>
            <p className="text-xs text-slate-400">Passport Platform</p>
          </div>
        </div>

        {/* Nav */}
        <nav className="flex-1 overflow-y-auto px-3 py-4">
          <ul className="space-y-1">
            {nav.map(({ to, label, icon }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  end={to === '/'}
                  className={({ isActive }) =>
                    `flex items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium transition-colors ${
                      isActive
                        ? 'bg-slate-800 text-white'
                        : 'text-slate-400 hover:bg-slate-800 hover:text-white'
                    }`
                  }
                >
                  {icon}
                  {label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>

        {/* Credentials toggle */}
        <div className="relative border-t border-slate-800 p-3">
          <AuthStatus />
          <CredentialsPanel open={credOpen} onClose={() => setCredOpen(false)} />
          <button
            onClick={() => setCredOpen(o => !o)}
            aria-expanded={credOpen}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-sm font-medium text-slate-400 transition-colors hover:bg-slate-800 hover:text-white"
          >
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 5.25a3 3 0 013 3m3 0a6 6 0 01-7.029 5.912c-.563-.097-1.159.026-1.563.43L10.5 17.25H8.25v2.25H6v2.25H2.25v-2.818c0-.597.237-1.17.659-1.591l6.499-6.499c.404-.404.527-1 .43-1.563A6 6 0 1121.75 8.25z" />
            </svg>
            <span>Credentials</span>
            {hasCredentials && (
              <span className="ml-auto h-2 w-2 rounded-full bg-green-400" title="Credentials set" />
            )}
          </button>
        </div>
      </aside>

      {/* Main */}
      <div className="flex flex-1 flex-col overflow-hidden">
        {/* Header */}
        <header className="flex h-16 shrink-0 items-center border-b border-slate-200 bg-white px-6">
          <h1 className="text-lg font-semibold text-slate-900">{title}</h1>
          <div className="ml-auto flex items-center gap-2">
            <span className="rounded-full bg-indigo-50 px-3 py-1 text-xs font-medium text-indigo-700">
              Internal Use Only
            </span>
          </div>
        </header>
        <DeploymentBanner />

        {/* Content */}
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
