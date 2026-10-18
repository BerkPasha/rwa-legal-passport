import { afterEach, describe, it, expect } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ASSET } from './mocks/handlers'
import { setUserToken } from '../api'
import { AssetList } from '../pages/AssetList'
import { AuthProvider } from '../AuthContext'

afterEach(() => {
  setUserToken('')
  sessionStorage.clear()
})

function renderWithRouter() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <AssetList />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('AssetList', () => {
  it('shows registered asset from API', async () => {
    renderWithRouter()
    expect(await screen.findByText('MRPROP')).toBeInTheDocument()
    expect(screen.getByText('Meridian Property Holdings LLC')).toBeInTheDocument()
  })

  it('shows asset count in subtitle', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    expect(screen.getByText(/1 tokenized asset registered/)).toBeInTheDocument()
  })

  it('shows View link for each asset', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    expect(screen.getByRole('link', { name: /view/i })).toBeInTheDocument()
  })

  it('shows network column', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    expect(screen.getByText('Testnet')).toBeInTheDocument()
  })

  it('shows jurisdiction column', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    expect(screen.getByText('US-NY')).toBeInTheDocument()
  })

  it('toggles register form when button clicked', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    const btn = screen.getByRole('button', { name: /register asset/i })
    await userEvent.click(btn)
    expect(screen.getByText('Register New Tokenized Asset')).toBeInTheDocument()
  })

  it('hides form when Cancel clicked', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    const btn = screen.getByRole('button', { name: /register asset/i })
    await userEvent.click(btn)
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }))
    expect(screen.queryByText('Register New Tokenized Asset')).not.toBeInTheDocument()
  })

  it('submits form with entered values', async () => {
    renderWithRouter()
    await screen.findByText('MRPROP')
    await userEvent.click(screen.getByRole('button', { name: /register asset/i }))

    await userEvent.type(screen.getByLabelText('Issuer ID'), 'test-issuer')
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'NEWASSET')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GTEST1234')
    await userEvent.type(screen.getByPlaceholderText(/Meridian/i), 'Test Corp')
    await userEvent.type(screen.getByPlaceholderText('US-NY'), 'US-CA')

    const submitBtn = screen.getAllByRole('button', { name: /register asset/i })[1]
    await userEvent.click(submitBtn)

    await waitFor(() => {
      expect(screen.queryByText('Register New Tokenized Asset')).not.toBeInTheDocument()
    })
  })

  it('sends issuer_id and the user Bearer token when registering', async () => {
    const captured: { body?: Record<string, unknown>; auth?: string | null } = {}
    server.use(
      http.post('http://localhost:8000/assets', async ({ request }) => {
        captured.body = await request.json() as Record<string, unknown>
        captured.auth = request.headers.get('Authorization')
        return HttpResponse.json({ ...MOCK_ASSET, ...captured.body, id: 'asset-new' }, { status: 201 })
      }),
    )
    // The token is entered the way the app keeps it: the AuthProvider loads it for this tab
    sessionStorage.setItem('rwa_passport_session_auth', JSON.stringify({ userToken: 'fictional-admin-token', apiKey: '' }))
    renderWithRouter()
    await screen.findByText('MRPROP')
    await userEvent.click(screen.getByRole('button', { name: /register asset/i }))

    await userEvent.type(screen.getByLabelText('Issuer ID'), 'Meridian-Property ')
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'NEWASSET')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GTEST1234')
    await userEvent.type(screen.getByPlaceholderText(/Meridian/i), 'Test Corp')
    await userEvent.type(screen.getByPlaceholderText('US-NY'), 'US-CA')
    await userEvent.click(screen.getAllByRole('button', { name: /register asset/i })[1])

    await waitFor(() => expect(captured.body).toBeDefined())
    expect(captured.body?.issuer_id).toBe('meridian-property')
    expect(captured.auth).toBe('Bearer fictional-admin-token')
  })
})
