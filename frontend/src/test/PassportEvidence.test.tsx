import { describe, expect, it } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_PASSPORT } from './mocks/handlers'
import { PassportPreview } from '../pages/PassportPreview'
import { AuthProvider } from '../AuthContext'
import type { LegalPassport } from '../types'

// 'test-inst-key-alpha' is a FICTIONAL, NON-SECRET fixture answered by MSW.

async function lookup(passport: LegalPassport = MOCK_PASSPORT) {
  server.use(
    http.get('http://localhost:8000/passport/:network/:assetCode/:issuerKey', () => HttpResponse.json(passport)),
  )
  render(
    <MemoryRouter>
      <AuthProvider>
        <PassportPreview />
      </AuthProvider>
    </MemoryRouter>,
  )
  await userEvent.type(screen.getByPlaceholderText('MRPROP'), 'MRPROP')
  await userEvent.type(screen.getByPlaceholderText('G…'), 'GABC1234')
  await userEvent.type(screen.getByPlaceholderText(/X-API-Key/i), 'test-inst-key-alpha')
  await userEvent.click(screen.getByRole('button', { name: /fetch legal passport/i }))
  return screen.findByTestId('passport-card')
}

const right = MOCK_PASSPORT.legal_rights[0]

describe('Passport evidence display (Stage 9B-4, step 9)', () => {
  it('shows the page and the approved document version next to a verified quotation', async () => {
    const card = await lookup()
    expect(within(card).getByTestId('passport-passage-location')).toHaveTextContent('(page 1, Q4 2024 Prospectus version 1)')
    expect(within(card).getByTestId('verification-meaning')).toHaveTextContent(
      'does not establish legal enforceability',
    )
    expect(within(card).getByText(/AI-extracted values attested by reviewers, not legal opinions/)).toBeInTheDocument()
    expect(within(card).queryByText(/extracted from approved legal documents/)).not.toBeInTheDocument()
    expect(within(card).queryByTestId('passport-evidence-status')).not.toBeInTheDocument()
  })

  it('says when a known value is not source-verified, and why', async () => {
    const card = await lookup({
      ...MOCK_PASSPORT,
      legal_rights: [{
        ...right, verified: false, evidence_status: 'not_found', source_page: null, supporting_passage: null,
        document_id: null, document_version_number: null,
      }],
      verification_summary: { ...MOCK_PASSPORT.verification_summary, source_verified_field_count: 0, unverified_field_count: 1 },
    })
    expect(within(card).getByTestId('passport-evidence-status')).toHaveTextContent(
      'Quotation not verified: quotation not found in this document version',
    )
    expect(within(card).getByTestId('passport-right-badge')).toHaveTextContent('Unverified')
    // The level's floor value is not presented as issuer attestation of verified fields
    expect(within(card).getByTestId('verification-level-badge')).toHaveTextContent('No source-verified fields')
  })

  it('marks a value that a reviewer corrected before approval', async () => {
    const card = await lookup({ ...MOCK_PASSPORT, legal_rights: [{ ...right, value_origin: 'reviewer_correction' }] })
    expect(within(card).getByTestId('value-origin')).toHaveTextContent('Value corrected by a reviewer before approval')
  })

  it('names the approval types of each source document instead of "issuer attested only"', async () => {
    const card = await lookup({
      ...MOCK_PASSPORT,
      source_documents: [{
        ...MOCK_PASSPORT.source_documents[0], approval_types: ['legal_verification'], independently_verified: false,
        source_verified_claim_count: 0,
      }],
    })
    expect(within(card).getByText(/Q4 2024 Prospectus \(v1\) — independent legal reviewer attestation; 0 source-verified fields/))
      .toBeInTheDocument()
    expect(within(card).queryByText(/issuer attested only/)).not.toBeInTheDocument()
  })

  it('shows the new stale reasons of excluded documents in words', async () => {
    const card = await lookup({
      ...MOCK_PASSPORT,
      excluded_documents: [{
        document_id: 'doc-009', title: 'Title Deed', document_type: 'title_deed',
        reasons: ['source_file_integrity_failed', 'source_text_changed', 'evidence_not_verified'],
      }],
    })
    expect(within(card).getByTestId('excluded-documents')).toHaveTextContent(
      'Title Deed: The stored source file does not match the approved file; The stored page text differs from the approved text; A quotation is no longer found on its cited page',
    )
  })
})
