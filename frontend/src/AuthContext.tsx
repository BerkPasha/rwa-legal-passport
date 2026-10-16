import React, { createContext, useCallback, useContext, useState } from 'react'
import { setUserToken as setApiUserToken } from './api'

interface AuthState {
  // Bearer token for internal endpoints (platform admin or reviewer)
  userToken: string
  // Institutional X-API-Key, used only by the passport lookup
  apiKey: string
}

interface AuthContextValue extends AuthState {
  setUserToken: (t: string) => void
  setApiKey: (k: string) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

// Credentials are kept for the current browser tab only (sessionStorage), never
// in localStorage, so they do not persist on disk after the tab is closed.
const SESSION_KEY = 'rwa_passport_session_auth'
// Key used by earlier versions, which persisted credentials in localStorage.
export const LEGACY_LS_KEY = 'rwa_passport_auth'

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

  return (
    <AuthContext.Provider value={{ ...state, setUserToken, setApiKey }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
