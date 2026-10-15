import React, { createContext, useCallback, useContext, useState } from 'react'

interface AuthState {
  reviewerToken: string
  apiKey: string
}

interface AuthContextValue extends AuthState {
  setReviewerToken: (t: string) => void
  setApiKey: (k: string) => void
}

const AuthContext = createContext<AuthContextValue | null>(null)

const LS_KEY = 'rwa_passport_auth'

function load(): AuthState {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (raw) return JSON.parse(raw) as AuthState
  } catch { /* ignore */ }
  return { reviewerToken: '', apiKey: '' }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<AuthState>(load)

  const setReviewerToken = useCallback((reviewerToken: string) => {
    setState(s => {
      const next = { ...s, reviewerToken }
      localStorage.setItem(LS_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  const setApiKey = useCallback((apiKey: string) => {
    setState(s => {
      const next = { ...s, apiKey }
      localStorage.setItem(LS_KEY, JSON.stringify(next))
      return next
    })
  }, [])

  return (
    <AuthContext.Provider value={{ ...state, setReviewerToken, setApiKey }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside AuthProvider')
  return ctx
}
