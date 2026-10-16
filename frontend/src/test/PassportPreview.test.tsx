import { describe, it, expect } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_PASSPORT } from './mocks/handlers'
import { PassportPreview } from '../pages/PassportPreview'
import { AuthProvider } from '../AuthContext'

// 'test-inst-key-alpha' is a FICTIONAL, NON-SECRET test fixture: requests are
// answered by the MSW mock server, and the real backend does not accept it.
// Real API keys live only in the backend .env file.

function renderWithProviders() {
  return render(
    <MemoryRouter>
      <AuthProvider>
        <PassportPreview />
      </AuthProvider>
    </MemoryRouter>,
  )
}

describe('PassportPreview', () => {
  it('renders the lookup form', () => {
    renderWithProviders()
    expect(screen.getByText('Legal Passport Lookup')).toBeInTheDocument()
    expect(screen.getByPlaceholderText('MRPROP')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /fetch legal passport/i })).toBeInTheDocument()
  })

  it('shows error when API key is missing', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    expect(await screen.findByText(/API key is required/i)).toBeInTheDocument()
  })

  it('displays passport card after successful lookup', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    // fill in API key
    await userEvent.type(
      screen.getByPlaceholderText(/X-API-Key/i),
      'test-inst-key-alpha',
    )
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    await waitFor(() => {
      expect(screen.getByTestId('passport-card')).toBeInTheDocument()
    })
    expect(screen.getByText(/Legal Passport — v1/)).toBeInTheDocument()
  })

  it('shows disclaimer from passport response', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    await screen.findByTestId('passport-card')
    const banner = screen.getByTestId('disclaimer-banner')
    expect(banner).toBeInTheDocument()
    expect(banner.textContent).toMatch(/INSTITUTIONAL USE ONLY/)
  })

  it('shows legal rights in passport', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    await screen.findByTestId('passport-card')
    expect(screen.getByText('Ownership Nature')).toBeInTheDocument()
    expect(screen.getByText(/Direct fractional ownership/)).toBeInTheDocument()
  })

  it('shows error message on 403 without API key', async () => {
    server.use(
      http.get('http://localhost:8000/passport/:network/:assetCode/:issuerKey', () => {
        return HttpResponse.json({ detail: 'Forbidden' }, { status: 403 })
      }),
    )
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'bad-key')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    await waitFor(() => {
      expect(screen.getByText(/403/)).toBeInTheDocument()
    })
  })

  it('shows approval chain in passport', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    const card = await screen.findByTestId('passport-card')
    // The public passport shows the reviewer's role, never their identity.
    expect(within(card).getByText('Issuer Reviewer')).toBeInTheDocument()
    expect(within(card).getByText('Issuer Attestation')).toBeInTheDocument()
  })

  it('shows asset, network and extraction metadata from the backend response', async () => {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))

    const card = await screen.findByTestId('passport-card')
    expect(within(card).getByText('MRPROP')).toBeInTheDocument()
    expect(within(card).getByText(/Meridian Property Holdings LLC/)).toBeInTheDocument()
    expect(within(card).getByText('Stellar Mainnet')).toBeInTheDocument()
    expect(within(card).getByText('ext-001')).toBeInTheDocument()
    expect(within(card).getByText('claude-haiku-4-5-20251001')).toBeInTheDocument()
    expect(within(card).getByText('Q4 2024 Prospectus (v1)')).toBeInTheDocument()
    expect(within(card).queryByText(/Invalid Date/)).not.toBeInTheDocument()
  })
})

describe('PassportPreview approval integrity (Stage 9B-3)', () => {
  async function lookup() {
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    return screen.findByTestId('passport-card')
  }

  it('labels issuer attestation as not independently verified', async () => {
    const card = await lookup()
    expect(within(card).getByTestId('verification-level-badge')).toHaveTextContent(
      'Issuer attested — not independently verified',
    )
    expect(within(card).getByTestId('passport-right-badge')).toHaveTextContent('Evidence cited')
    expect(within(card).queryByText('Independently verified')).not.toBeInTheDocument()
  })

  it('shows conflicting sources without presenting a value as approved', async () => {
    const conflicting = {
      ...MOCK_PASSPORT,
      legal_rights: [
        {
          ...MOCK_PASSPORT.legal_rights[0],
          value: 'conflicting',
          verified: false,
          independently_verified: false,
          conflict: true,
          source_page: null,
          supporting_passage: null,
          sources: [
            { ...MOCK_PASSPORT.legal_rights[0].sources[0] },
            {
              ...MOCK_PASSPORT.legal_rights[0].sources[0],
              document_id: 'doc-002',
              document_title: 'Legal Opinion',
              value: 'Leasehold interest only',
            },
          ],
        },
      ],
      excluded_documents: [
        {
          document_id: 'doc-003',
          title: 'Title Deed',
          document_type: 'title_deed',
          reasons: ['document_version_superseded' as const],
        },
      ],
    }
    server.use(
      http.get('http://localhost:8000/passport/:network/:assetCode/:issuerKey', () => HttpResponse.json(conflicting)),
    )
    const card = await lookup()
    expect(within(card).getByTestId('passport-right-badge')).toHaveTextContent('Conflicting sources')
    expect(within(card).getByText(/Approved documents disagree/)).toBeInTheDocument()
    expect(within(card).getByText(/Leasehold interest only/)).toBeInTheDocument()
    expect(within(card).getByText(/Direct fractional ownership/)).toBeInTheDocument()
    expect(within(card).getByTestId('excluded-documents')).toHaveTextContent(
      'Title Deed: A newer document version exists',
    )
  })

  it('shows the 409 stale-approval response as an error, never as a passport', async () => {
    server.use(
      http.get('http://localhost:8000/passport/:network/:assetCode/:issuerKey', () =>
        HttpResponse.json(
          { detail: 'The legal approval for this asset is no longer current; a re-review is required.' },
          { status: 409 },
        ),
      ),
    )
    renderWithProviders()
    await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
    await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
    await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
    await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
    expect(await screen.findByText(/409: The legal approval for this asset is no longer current/)).toBeInTheDocument()
    expect(screen.queryByTestId('passport-card')).not.toBeInTheDocument()
  })
})
