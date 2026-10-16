import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act, render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ASSET } from './mocks/handlers'
import { AuthProvider, LEGACY_LS_KEY, useAuth } from '../AuthContext'
import { getAssets, setUserToken as resetApiToken } from '../api'

// FICTIONAL, NON-SECRET test credentials.

function Probe() {
  const { userToken, apiKey, setUserToken, setApiKey } = useAuth()
  return (
    <div>
      <span data-testid="token">{userToken}</span>
      <span data-testid="key">{apiKey}</span>
      <button onClick={() => setUserToken('fresh-user-token')}>set token</button>
      <button onClick={() => setApiKey('fresh-api-key')}>set key</button>
      <button onClick={() => { setUserToken(''); setApiKey('') }}>clear</button>
    </div>
  )
}

beforeEach(() => {
  localStorage.clear()
  sessionStorage.clear()
})

afterEach(() => resetApiToken(''))

describe('AuthProvider credential storage', () => {
  it('purges credentials persisted in localStorage by earlier versions', () => {
    localStorage.setItem(LEGACY_LS_KEY, JSON.stringify({ reviewerToken: 'old', apiKey: 'old-key' }))
    render(<AuthProvider><Probe /></AuthProvider>)
    expect(localStorage.getItem(LEGACY_LS_KEY)).toBeNull()
    expect(screen.getByTestId('token').textContent).toBe('')
  })

  it('keeps credentials in sessionStorage only, never localStorage', async () => {
    render(<AuthProvider><Probe /></AuthProvider>)
    await act(async () => { screen.getByText('set token').click() })
    await act(async () => { screen.getByText('set key').click() })

    expect(localStorage.length).toBe(0)
    const stored = Object.keys(sessionStorage).map(k => sessionStorage.getItem(k) ?? '').join('')
    expect(stored).toContain('fresh-user-token')
    expect(stored).toContain('fresh-api-key')

    await act(async () => { screen.getByText('clear').click() })
    expect(sessionStorage.length).toBe(0)
  })

  it('restores the session token and applies it to API requests', async () => {
    sessionStorage.setItem('rwa_passport_session_auth', JSON.stringify({ userToken: 'restored-token', apiKey: '' }))
    const seen: Headers[] = []
    server.use(
      http.get('http://localhost:8000/assets', ({ request }) => {
        seen.push(request.headers)
        return HttpResponse.json([MOCK_ASSET])
      }),
    )
    render(<AuthProvider><Probe /></AuthProvider>)
    expect(screen.getByTestId('token').textContent).toBe('restored-token')
    await getAssets()
    expect(seen[0].get('Authorization')).toBe('Bearer restored-token')
  })
})
