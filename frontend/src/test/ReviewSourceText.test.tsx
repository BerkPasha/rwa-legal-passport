import { beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { useNavigate } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { mockEvidence, mockVersionText } from './mocks/handlers'
import {
  BASE,
  deferred,
  KNOWN,
  renderReview,
  reviewExtraction,
  serveReview,
  setReviewerSession,
  UNKNOWN,
  V1,
  V1_PAGES,
  V2,
  V2_PAGES,
} from './reviewFixtures'

beforeEach(setReviewerSession)

const panel = () => screen.findByTestId('source-text-panel')
const pageTexts = () => screen.queryAllByTestId('source-page').map(p => p.textContent)

describe('Version-specific source text (Stage 9B-4, step 9)', () => {
  it('shows exactly the reviewed version, requested by its number and id', async () => {
    const calls = serveReview({ extractions: reviewExtraction() })
    renderReview()
    const text = await screen.findByTestId('source-text')
    expect(text).toHaveAttribute('data-version-id', V1.id)
    expect(calls.text).toEqual([{ n: '1', versionId: V1.id }])
    expect(screen.getByTestId('source-text-version')).toHaveTextContent('Version 1 · 2 pages · not current (latest is version 2)')
    expect(pageTexts()).toEqual([`Page 1${V1_PAGES[0]}`, `Page 2${V1_PAGES[1]}`])
    expect(screen.getByTestId('source-text-notice')).toHaveTextContent('does not establish legal validity')
    const select = screen.getByLabelText('Document version') as HTMLSelectElement
    expect(select.value).toBe(V1.id)
    expect(within(select).getByRole('option', { name: 'Version 1 — under review' })).toBeInTheDocument()
    expect(within(select).getByRole('option', { name: 'Version 2 (latest)' })).toBeInTheDocument()
    expect(screen.getByTestId('extraction-version')).toHaveTextContent('Document version 1')
  })

  it('never shows the newer version for a historical extraction', async () => {
    serveReview({
      extractions: reviewExtraction({
        approval_state: { state: 'not_approved', version_is_current: false, current_approval_types: [], reasons: [] },
      }),
    })
    renderReview()
    await screen.findByTestId('source-text')
    expect(screen.getByText(V1_PAGES[1])).toBeInTheDocument()
    expect(screen.queryByText(V2_PAGES[0])).not.toBeInTheDocument()
    expect(screen.getByTestId('superseded-notice')).toBeInTheDocument()
  })

  it('switches versions explicitly, marks a version other than the reviewed one, and switches back', async () => {
    const calls = serveReview({ extractions: reviewExtraction() })
    renderReview()
    await screen.findByTestId('source-text')
    await userEvent.selectOptions(screen.getByLabelText('Document version'), V2.id)
    expect(await screen.findByText(V2_PAGES[0])).toBeInTheDocument()
    expect(screen.queryByText(V1_PAGES[0])).not.toBeInTheDocument()
    expect(screen.getByTestId('other-version-notice')).toHaveTextContent(
      'You are viewing version 2. This extraction, and the check of its quotations, belong to version 1.',
    )
    expect(screen.getByTestId('source-text-version')).toHaveTextContent('Version 2 · 1 page · current version')
    await userEvent.selectOptions(screen.getByLabelText('Document version'), V1.id)
    expect(await screen.findByText(V1_PAGES[0])).toBeInTheDocument()
    expect(screen.queryByTestId('other-version-notice')).not.toBeInTheDocument()
    expect(calls.text).toEqual([
      { n: '1', versionId: V1.id }, { n: '2', versionId: V2.id }, { n: '1', versionId: V1.id },
    ])
  })

  it('discards a slow response for a version that is no longer selected', async () => {
    serveReview({ extractions: reviewExtraction() })
    const slow = deferred()
    let slowServed = false
    server.use(
      http.get(`${BASE}/documents/:docId/versions/:n/text`, async ({ params }) => {
        if (params.n === '1') {
          await slow.gate
          slowServed = true
          return HttpResponse.json(mockVersionText(V1, V1_PAGES, 2))
        }
        return HttpResponse.json(mockVersionText(V2, V2_PAGES, 2))
      }),
    )
    renderReview()
    await panel()
    await screen.findByLabelText('Document version')
    await userEvent.selectOptions(screen.getByLabelText('Document version'), V2.id)
    expect(await screen.findByText(V2_PAGES[0])).toBeInTheDocument()
    slow.release()
    await waitFor(() => expect(slowServed).toBe(true))
    await new Promise(r => setTimeout(r, 50))
    expect(screen.getByText(V2_PAGES[0])).toBeInTheDocument()
    expect(screen.queryByText(V1_PAGES[0])).not.toBeInTheDocument()
    expect(screen.getByTestId('source-text')).toHaveAttribute('data-version-id', V2.id)
  })

  it('refuses a response that names another version than the one requested', async () => {
    serveReview({ extractions: reviewExtraction() })
    server.use(
      http.get(`${BASE}/documents/:docId/versions/:n/text`, () => HttpResponse.json(mockVersionText(V2, V2_PAGES, 2))),
    )
    renderReview()
    expect(await screen.findByTestId('source-text-error')).toHaveTextContent(
      'The server returned text of a different document version; it is not shown.',
    )
    expect(screen.queryByText(V2_PAGES[0])).not.toBeInTheDocument()
  })

  it('shows no text and substitutes no version when the reviewed version is not in the version list', async () => {
    const calls = serveReview({ extractions: reviewExtraction(), versions: [V2] })
    renderReview()
    expect(await screen.findByTestId('source-text-error')).toHaveTextContent(
      'The document version this extraction was made from is not available. No source text is shown, and no other version is substituted.',
    )
    expect(calls.text).toEqual([])
    expect(screen.queryByText(V2_PAGES[0])).not.toBeInTheDocument()
    expect(screen.queryByLabelText('Document version')).not.toBeInTheDocument()
  })

  it.each([
    [409, { detail: 'unavailable', source_text_unavailable_reason: 'no_page_records' },
      'The stored source text of version 1 is unavailable (no page records are stored). It is not shown, and no other version\'s text is substituted.'],
    [409, { detail: 'unavailable', source_text_unavailable_reason: 'invalid_page_records' },
      'The stored source text of version 1 is unavailable (its page records are inconsistent).'],
    [409, { detail: 'Version reference mismatch: …' },
      'Version reference mismatch: the requested version identifier does not belong to version 1. No text is shown.'],
    [404, { detail: 'Document version not found.' }, 'Version 1 of this document was not found.'],
    [404, { detail: 'Document not found.' }, 'This document is not available: it does not exist or is outside your access.'],
    [401, { detail: 'Invalid or missing authentication token.' }, 'Authentication failed'],
    [403, { detail: 'Not authenticated' }, 'Your credentials are not authorized to read the source text of this document.'],
    [422, { detail: [{ loc: ['path', 'version_number'], msg: 'bad' }] }, 'Invalid document version reference. No text is shown.'],
  ])('handles HTTP %i from the text endpoint without showing any text', async (status, body, message) => {
    serveReview({ extractions: reviewExtraction() })
    server.use(
      http.get(`${BASE}/documents/:docId/versions/:n/text`, () => HttpResponse.json(body, { status })),
    )
    renderReview()
    expect(await screen.findByTestId('source-text-error')).toHaveTextContent(message)
    expect(screen.queryByTestId('source-text')).not.toBeInTheDocument()
    // The claims stay visible; only the text is withheld
    expect(screen.getByTestId('claim-row-ownership_nature')).toBeInTheDocument()
  })

  it('reports an inaccessible document without revealing anything about it', async () => {
    serveReview({ extractions: reviewExtraction() })
    server.use(
      http.get(`${BASE}/documents/:docId/versions`, () => HttpResponse.json({ detail: 'Document not found.' }, { status: 404 })),
    )
    renderReview()
    expect(await screen.findByTestId('source-text-error')).toHaveTextContent(
      'Not found: it does not exist or is outside your access.',
    )
    expect(screen.queryByLabelText('Document version')).not.toBeInTheDocument()
  })

  it('shows an empty page explicitly, never invented text', async () => {
    serveReview({ extractions: reviewExtraction(), texts: { [V1.id]: ['', V1_PAGES[1]] } })
    renderReview()
    await screen.findByTestId('source-text')
    expect(within(screen.getByRole('region', { name: 'Page 1' })).getByText(
      'No text layer on this page (no OCR is performed).',
    )).toBeInTheDocument()
  })

  it("brings a claim's cited page of the reviewed version into view, even from another version", async () => {
    serveReview({ extractions: reviewExtraction() })
    renderReview()
    await screen.findByTestId('source-text')
    await userEvent.selectOptions(screen.getByLabelText('Document version'), V2.id)
    await screen.findByText(V2_PAGES[0])
    await userEvent.click(screen.getByRole('button', { name: 'View page 2 of the source text for Ownership Nature' }))
    expect(await screen.findByText(V1_PAGES[1])).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Page 2' })).toHaveAttribute('data-highlighted', 'true')
    expect(screen.getByRole('region', { name: 'Page 1' })).not.toHaveAttribute('data-highlighted')
    expect((screen.getByLabelText('Document version') as HTMLSelectElement).value).toBe(V1.id)
  })
})

describe('Evidence labels and counters (Stage 9B-4, step 9)', () => {
  it('labels claims by the backend evidence status, never by the presence of a passage', async () => {
    const notFound = {
      ...KNOWN,
      id: 'claim-003',
      field_name: 'jurisdiction',
      supporting_passage: 'A quotation that the server could not find anywhere',
      source_page: 1,
      evidence: mockEvidence({ status: 'not_found', cited_page: 1, found_on_pages: [] }),
    }
    serveReview({ extractions: reviewExtraction({ claims: [KNOWN, UNKNOWN, notFound] }) })
    renderReview()
    const found = await screen.findByTestId('claim-row-ownership_nature')
    expect(within(found).getByTestId('evidence-badge-verified')).toHaveTextContent('Quote found on page 2')
    expect(within(screen.getByTestId('claim-row-jurisdiction')).getByTestId('evidence-badge-failed'))
      .toHaveTextContent('Quote not found in document')
    expect(within(screen.getByTestId('claim-row-transfer_restrictions')).getByTestId('evidence-badge-unknown'))
      .toHaveTextContent('Unknown')
    expect(screen.getByTestId('count-quote-found')).toHaveTextContent('1')
    expect(screen.getByTestId('count-not-verified')).toHaveTextContent('1')
    expect(screen.getByTestId('count-unknown')).toHaveTextContent('1')
    expect(screen.queryByText('Verified')).not.toBeInTheDocument()
    expect(screen.getAllByText(/not a legal interpretation, a reviewer approval or independent legal verification/).length)
      .toBeGreaterThan(0)
  })

  it('shows the quotation even when no page is cited', async () => {
    const noPage = { ...KNOWN, source_page: null, evidence: mockEvidence({ status: 'page_missing', cited_page: null }) }
    serveReview({ extractions: reviewExtraction({ claims: [noPage] }) })
    renderReview()
    const row = await screen.findByTestId('claim-row-ownership_nature')
    expect(within(row).getByText('No page cited')).toBeInTheDocument()
    expect(within(row).getByText(`“${KNOWN.supporting_passage}”`)).toBeInTheDocument()
    expect(within(row).getByTestId('evidence-badge-failed')).toHaveTextContent('No source page cited')
  })
})

describe('Stale extraction responses (Stage 9B-4, step 9)', () => {
  function Navigator() {
    const navigate = useNavigate()
    return (
      <button type="button" onClick={() => navigate('/assets/asset-001/documents/doc-002/extractions/ext-002')}>
        go to second extraction
      </button>
    )
  }

  it('never shows an earlier extraction whose response arrives after navigating away', async () => {
    const slow = deferred()
    const first = reviewExtraction({ claims: [{ ...KNOWN, value: 'Value of the first extraction' }] })
    const second = reviewExtraction({
      id: 'ext-002',
      document_id: 'doc-002',
      version_id: 'ver-201',
      claims: [{ ...KNOWN, extraction_id: 'ext-002', value: 'Value of the second extraction' }],
    })
    serveReview({ extractions: first })
    server.use(
      http.get(`${BASE}/extractions/:extractionId`, async ({ params }) => {
        if (params.extractionId === 'ext-001') {
          await slow.gate
          return HttpResponse.json(first)
        }
        return HttpResponse.json(second)
      }),
      http.get(`${BASE}/documents/:docId/versions`, ({ params }) =>
        HttpResponse.json(params.docId === 'doc-002'
          ? [{ ...V1, id: 'ver-201', document_id: 'doc-002' }]
          : [V1])),
    )
    renderReview(undefined, <Navigator />)
    await userEvent.click(screen.getByRole('button', { name: 'go to second extraction' }))
    expect(await screen.findByText('Value of the second extraction')).toBeInTheDocument()
    slow.release()
    await new Promise(r => setTimeout(r, 50))
    expect(screen.getByText('Value of the second extraction')).toBeInTheDocument()
    expect(screen.queryByText('Value of the first extraction')).not.toBeInTheDocument()
  })

  it('does not show an extraction for an address it does not belong to', async () => {
    serveReview({ extractions: reviewExtraction({ id: 'ext-999' }) })
    renderReview()
    expect(await screen.findByRole('alert')).toHaveTextContent('The server returned a different extraction; it is not shown.')
    expect(screen.queryByTestId('claim-row-ownership_nature')).not.toBeInTheDocument()
  })

  it.each([
    [404, 'Not found: it does not exist or is outside your access.'],
    [401, 'Authentication failed'],
    [403, 'Your credentials are not authorized to load this extraction.'],
  ])('shows HTTP %i for the extraction itself without any claim', async (status, message) => {
    server.use(
      http.get(`${BASE}/extractions/:extractionId`, () => HttpResponse.json({ detail: 'Extraction not found.' }, { status })),
    )
    renderReview()
    expect(await screen.findByRole('alert')).toHaveTextContent(message)
    expect(screen.queryByTestId('source-text-panel')).not.toBeInTheDocument()
  })
})
