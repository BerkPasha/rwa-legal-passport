import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { ApiError, getMe, setUserToken as setApiUserToken } from './api'
import type { CurrentUser } from './types'

interface AuthState {
  // Bearer token for internal endpoints (platform admin or reviewer)
  userToken: string
  // Institutional X-API-Key, used only by the passport lookup
  apiKey: string
}

// What the server says about the current Bearer credential (Stage 10B-2). Shown in the sidebar;
// never the credential itself.
export type Identity =
  | { state: 'none' }
  | { state: 'checking' }
  | { state: 'signed_in'; user: CurrentUser }
  | { state: 'rejected'; status: number }
  | { state: 'unavailable' }

interface AuthContextValue extends AuthState {
  setUserToken: (t: string) => void
  setApiKey: (k: string) => void
  // The user token once typing has paused: pages reload their data when it changes
  activeToken: string
  identity: Identity
}

const AuthContext = createContext<AuthContextValue | null>(null)

// Credentials are kept for the current browser tab only (sessionStorage), never
// in localStorage, so they do not persist on disk after the tab is closed.
const SESSION_KEY = 'rwa_passport_session_auth'
// Key used by earlier versions, which persisted credentials in localStorage.
export const LEGACY_LS_KEY = 'rwa_passport_auth'
// Pause after the last keystroke before a typed credential is checked and data is reloaded
export const CREDENTIAL_SETTLE_MS = 300

const EMPTY: AuthState = { userToken: '', apiKey: '' }

function purgeLegacyStorage() {
  try {
    localStorage.removeItem(LEGACY_LS_KEY)
  } catch { /* storage unavailable */ }
}

function load(): AuthState {
  purgeLegacyStorage()
  let state = EMPTY
  try {
    const raw = sessionStorage.getItem(SESSION_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as Partial<AuthState>
      state = {
        userToken: typeof parsed.userToken === 'string' ? parsed.userToken : '',
        apiKey: typeof parsed.apiKey === 'string' ? parsed.apiKey : '',
      }
    }
  } catch { /* ignore malformed or unavailable storage */ }
  // Sync before any child effect issues a request
  setApiUserToken(state.userToken)
  return state
}

function save(state: AuthState) {
  try {
    if (state.userToken || state.apiKey) {
      sessionStorage.setItem(SESSION_KEY, JSON.stringify(state))
    } else {
      sessionStorage.removeItem(SESSION_KEY)
    }
  } catch { /* storage unavailable: credentials stay in memory only */ }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>(load)
  const [activeToken, setActiveToken] = useState(state.userToken)
  const [identity, setIdentity] = useState<Identity>({ state: state.userToken ? 'checking' : 'none' })

  const setUserToken = useCallback((userToken: string) => {
    setApiUserToken(userToken)
    setState(s => {
      const next = { ...s, userToken }
      save(next)
      return next
    })
  }, [])

  const setApiKey = useCallback((apiKey: string) => {
    setState(s => {
      const next = { ...s, apiKey }
      save(next)
      return next
    })
  }, [])

  // A typed credential becomes active once typing pauses
  useEffect(() => {
    if (state.userToken === activeToken) return
    const timer = setTimeout(() => setActiveToken(state.userToken), CREDENTIAL_SETTLE_MS)
    return () => clearTimeout(timer)
  }, [state.userToken, activeToken])

  // The server says whom the active credential belongs to; an older answer is discarded
  useEffect(() => {
    if (!activeToken) {
      setIdentity({ state: 'none' })
      return
    }
    let current = true
    setIdentity({ state: 'checking' })
    getMe()
      .then(user => { if (current) setIdentity({ state: 'signed_in', user }) })
      .catch(e => {
        if (current) setIdentity(e instanceof ApiError ? { state: 'rejected', status: e.status } : { state: 'unavailable' })
      })
    return () => { current = false }
  }, [activeToken])

  return (
    <AuthContext.Provider value={{ ...state, setUserToken, setApiKey, activeToken, identity }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
