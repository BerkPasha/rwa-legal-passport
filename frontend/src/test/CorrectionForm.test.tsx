import { beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_APPROVAL } from './mocks/handlers'
import {
  BASE,
  correctionRecord,
  KNOWN,
  renderReview,
  reviewExtraction,
  serveReview,
  setReviewerSession,
  UNKNOWN,
} from './reviewFixtures'
import type { LegalClaim } from '../types'

beforeEach(setReviewerSession)

type Body = Record<string, unknown>
const has = (o: Body, key: string) => Object.prototype.hasOwnProperty.call(o, key)
// Every correction states the claim as displayed (issue A precondition)
const asRead = (claim: LegalClaim) => ({
  expected_claim: {
    value: claim.value, is_unknown: claim.is_unknown, source_page: claim.source_page, supporting_passage: claim.supporting_passage,
  },
})

// Records every PATCH body; answers with `respond` (default: an accepted correction)
function capturePatches(respond?: (body: Body) => Response) {
  const bodies: Body[] = []
  server.use(
    http.patch(`${BASE}/extractions/:id/claims/:claimId`, async ({ request }) => {
      const body = await request.json() as Body
      bodies.push(body)
      return respond ? respond(body) : HttpResponse.json(correctionRecord())
    }),
  )
  return bodies
}

async function openForm(claim: LegalClaim) {
  const row = await screen.findByTestId(`claim-row-${claim.field_name}`)
  await userEvent.click(within(row).getByRole('button', { name: 'Correct' }))
  return screen.getByRole('form', { name: /^Correct / })
}

const save = () => userEvent.click(screen.getByRole('button', { name: 'Save Correction' }))

describe('Correction form: evidence integrity (Stage 9B-4, step 9)', () => {
  it('keeps the current evidence of an unchanged value by omitting page and quotation', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(KNOWN)
    expect(within(form).getByLabelText('Keep the current page (2)')).toBeChecked()
    expect(within(form).getByLabelText('Keep the current quotation')).toBeChecked()
    expect(within(form).getByTestId('current-evidence')).toHaveTextContent(`page 2 — “${KNOWN.supporting_passage}”`)
    await userEvent.type(within(form).getByLabelText('Correction Note (optional)'), 'Wording confirmed')
    expect(within(form).getByTestId('correction-summary')).toHaveTextContent(
      'Evidence: the current page 2 is kept; the current quotation is kept.',
    )
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ corrected_value: KNOWN.value, is_unknown: false, correction_note: 'Wording confirmed', ...asRead(KNOWN) })
    expect(has(bodies[0], 'corrected_source_page')).toBe(false)
    expect(has(bodies[0], 'corrected_passage')).toBe(false)
    await waitFor(() => expect(screen.queryByRole('form', { name: /^Correct / })).not.toBeInTheDocument())
  })

  it('requires fresh evidence for a changed value and never pre-fills the previous one', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(KNOWN)
    const value = within(form).getByLabelText('Corrected Value')
    await userEvent.clear(value)
    await userEvent.type(value, 'Leasehold interest')
    expect(within(form).getByTestId('fresh-evidence-required')).toHaveTextContent(
      'The previous page and quotation cannot be reused',
    )
    const keepPage = within(form).getByLabelText('Keep the current page (not allowed, the value changed)')
    const keepPassage = within(form).getByLabelText('Keep the current quotation (not allowed, the value changed)')
    expect(keepPage).toBeDisabled()
    expect(keepPage).not.toBeChecked()
    expect(keepPassage).toBeDisabled()
    expect(within(form).getByLabelText('Cite a page')).toBeChecked()
    expect(within(form).getByLabelText('Cited page number')).toHaveValue('')
    expect(within(form).getByLabelText('Supporting quotation')).toHaveValue('')

    await save()
    expect(within(form).getByTestId('correction-error')).toHaveTextContent('Enter the cited page as a whole number of 1 or more.')
    await userEvent.type(within(form).getByLabelText('Cited page number'), '1')
    await save()
    expect(within(form).getByTestId('correction-error')).toHaveTextContent('Enter the supporting quotation')
    expect(bodies).toHaveLength(0)

    await userEvent.type(within(form).getByLabelText('Supporting quotation'), 'each token represents a fractional ownership interest')
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      corrected_value: 'Leasehold interest',
      is_unknown: false,
      corrected_source_page: 1,
      corrected_passage: 'each token represents a fractional ownership interest',
      ...asRead(KNOWN),
    })
    expect(typeof bodies[0].corrected_source_page).toBe('number')
  })

  it('allows keeping the evidence again once the value is restored exactly', async () => {
    serveReview({ extractions: reviewExtraction() })
    renderReview()
    const form = await openForm(KNOWN)
    const value = within(form).getByLabelText('Corrected Value')
    await userEvent.type(value, ' ')
    expect(within(form).getByTestId('fresh-evidence-required')).toBeInTheDocument()
    await userEvent.type(value, '{backspace}')
    expect(within(form).queryByTestId('fresh-evidence-required')).not.toBeInTheDocument()
    expect(within(form).getByLabelText('Keep the current page (2)')).toBeChecked()
  })

  it('may replace only the page of an unchanged value', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(KNOWN)
    await userEvent.type(within(form).getByLabelText('Cited page number'), '1')
    expect(within(form).getByLabelText('Cite a page')).toBeChecked()
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({ corrected_value: KNOWN.value, is_unknown: false, corrected_source_page: 1, ...asRead(KNOWN) })
  })

  it('clears the evidence explicitly when a known claim becomes unknown', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(KNOWN)
    await userEvent.type(within(form).getByLabelText('Cited page number'), '7')
    await userEvent.click(within(form).getByLabelText('Mark as unknown (insufficient evidence)'))
    expect(within(form).getByTestId('unknown-clears-evidence')).toHaveTextContent(
      'its value becomes “unknown”, and its page and quotation are cleared',
    )
    expect(within(form).queryByLabelText('Cited page number')).not.toBeInTheDocument()
    expect(within(form).queryByLabelText('Corrected Value')).not.toBeInTheDocument()
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      corrected_value: 'unknown', is_unknown: true, corrected_source_page: null, corrected_passage: null, ...asRead(KNOWN),
    })
    expect(has(bodies[0], 'corrected_source_page') && has(bodies[0], 'corrected_passage')).toBe(true)
  })

  it('makes an unknown claim known only with a typed value, page and quotation', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(UNKNOWN)
    const unknownBox = within(form).getByLabelText('Mark as unknown (insufficient evidence)')
    expect(unknownBox).toBeChecked()
    await userEvent.click(unknownBox)
    expect(within(form).getByLabelText('Corrected Value')).toHaveValue('')
    expect(within(form).getByLabelText('Keep the current page (the claim is currently unknown)')).toBeDisabled()
    expect(within(form).getByTestId('fresh-evidence-required')).toHaveTextContent('The claim is currently unknown.')
    await save()
    expect(within(form).getByTestId('correction-error')).toHaveTextContent('Enter a value, or mark the claim as unknown.')
    await userEvent.type(within(form).getByLabelText('Corrected Value'), 'unknown')
    await save()
    expect(within(form).getByTestId('correction-error')).toHaveTextContent('Mark the claim as unknown instead.')
    await userEvent.clear(within(form).getByLabelText('Corrected Value'))
    await userEvent.type(within(form).getByLabelText('Corrected Value'), 'Transfers need trustee consent')
    await userEvent.type(within(form).getByLabelText('Cited page number'), '2')
    await userEvent.type(within(form).getByLabelText('Supporting quotation'), 'the Trustee holds fee simple title')
    expect(bodies).toHaveLength(0)
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0]).toEqual({
      corrected_value: 'Transfers need trustee consent',
      is_unknown: false,
      corrected_source_page: 2,
      corrected_passage: 'the Trustee holds fee simple title',
      ...asRead(UNKNOWN),
    })
  })

  it('offers no reuse of old evidence when a legacy unknown claim is made known with its old value', async () => {
    const legacy = { ...UNKNOWN, value: 'Freely transferable', source_page: 1, supporting_passage: 'Interests are freely transferable' }
    serveReview({ extractions: reviewExtraction({ claims: [KNOWN, legacy] }) })
    const bodies = capturePatches()
    renderReview()
    const form = await openForm(legacy)
    await userEvent.click(within(form).getByLabelText('Mark as unknown (insufficient evidence)'))
    await userEvent.type(within(form).getByLabelText('Corrected Value'), 'Freely transferable')
    expect(within(form).getByTestId('fresh-evidence-required')).toBeInTheDocument()
    expect(within(form).getByLabelText('Keep the current page (the claim is currently unknown)')).toBeDisabled()
    expect(within(form).getByLabelText('Keep the current quotation (the claim is currently unknown)')).toBeDisabled()
    await save()
    expect(within(form).getByTestId('correction-error')).toHaveTextContent('Enter the cited page')
    expect(bodies).toHaveLength(0)
  })

  it.each([
    [{ correction_rule: 'evidence_not_verified', evidence_status: 'found_on_other_page' },
      'could not be verified on the cited page of this document version: quotation is not on the cited page. Nothing was changed.'],
    [{ correction_rule: 'evidence_not_verified', evidence_status: 'quotation_too_short' },
      'quotation too short (at least 20 characters and 3 words)'],
    [{ correction_rule: 'evidence_required', evidence_status: null },
      'The value changed, so the previous page and quotation cannot be reused.'],
    [{ correction_rule: 'unknown_with_evidence', evidence_status: null }, 'An unknown claim cannot carry a page or a quotation.'],
  ])('shows the backend refusal %j and keeps the form open', async (refusal, message) => {
    serveReview({ extractions: reviewExtraction() })
    capturePatches(() => HttpResponse.json({ detail: 'Correction rejected — …', ...refusal }, { status: 422 }))
    renderReview()
    const form = await openForm(KNOWN)
    await userEvent.type(within(form).getByLabelText('Supporting quotation'), 'a quotation that is not where it is cited')
    await save()
    expect(await within(form).findByTestId('correction-error')).toHaveTextContent(message)
    expect(screen.getByRole('form', { name: /^Correct / })).toBeInTheDocument()
    // The claim shown is unchanged: nothing is applied on the client
    expect(within(screen.getByTestId('claim-row-ownership_nature')).getByText(KNOWN.value)).toBeInTheDocument()
  })

  it('shows field validation errors of the backend readably', async () => {
    serveReview({ extractions: reviewExtraction() })
    capturePatches(() => HttpResponse.json(
      { detail: [{ loc: ['body', 'corrected_passage'], msg: 'String should have at most 2000 characters', type: 'string_too_long' }] },
      { status: 422 },
    ))
    renderReview()
    const form = await openForm(KNOWN)
    await userEvent.type(within(form).getByLabelText('Correction Note (optional)'), 'x')
    await save()
    expect(await within(form).findByTestId('correction-error')).toHaveTextContent(
      'Invalid request — corrected_passage: String should have at most 2000 characters',
    )
  })

  it.each([
    [401, 'Authentication failed'],
    [403, 'Your credentials are not authorized to correct this claim.'],
    [404, 'Not found: it does not exist or is outside your access.'],
  ])('reports HTTP %i on saving without changing anything', async (status, message) => {
    serveReview({ extractions: reviewExtraction() })
    capturePatches(() => HttpResponse.json({ detail: 'refused' }, { status }))
    renderReview()
    const form = await openForm(KNOWN)
    await save()
    expect(await within(form).findByTestId('correction-error')).toHaveTextContent(message)
  })

  it('closes the form and shows the live state on a 409 conflict', async () => {
    const approved = reviewExtraction({
      status: 'approved',
      approval_state: { state: 'current', version_is_current: true, current_approval_types: ['issuer_attestation'], reasons: [] },
    })
    const calls = serveReview({ extractions: [reviewExtraction(), reviewExtraction(), approved] })
    capturePatches(() => HttpResponse.json({ detail: 'An approved extraction cannot be corrected.' }, { status: 409 }))
    renderReview()
    await openForm(KNOWN)
    await save()
    expect(await screen.findByTestId('correction-conflict')).toHaveTextContent(
      'An approved extraction cannot be corrected. Nothing was saved.',
    )
    await waitFor(() => expect(screen.queryByRole('form', { name: /^Correct / })).not.toBeInTheDocument())
    expect(calls.extraction).toBeGreaterThanOrEqual(3)
    expect(await screen.findByText(/This extraction has been approved/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Correct' })).not.toBeInTheDocument()
  })

  it('does not send a correction decided on a claim that has changed since the form was opened', async () => {
    const changedClaim = { ...KNOWN, supporting_passage: 'each token represents a fractional ownership interest', source_page: 1 }
    serveReview({ extractions: [reviewExtraction(), reviewExtraction({ claims: [changedClaim, UNKNOWN] })] })
    const bodies = capturePatches()
    renderReview()
    await openForm(KNOWN)
    await save()
    expect(await screen.findByTestId('correction-conflict')).toHaveTextContent(
      'This claim or its extraction changed after the correction form was opened. Nothing was saved.',
    )
    expect(bodies).toHaveLength(0)
    const row = screen.getByTestId('claim-row-ownership_nature')
    expect(await within(row).findByText('Page 1')).toBeInTheDocument()
  })

  it('offers no correction for an approved extraction', async () => {
    serveReview({
      extractions: reviewExtraction({
        status: 'approved',
        approval_state: { state: 'current', version_is_current: true, current_approval_types: ['issuer_attestation'], reasons: [] },
      }),
    })
    renderReview()
    await screen.findByTestId('claim-row-ownership_nature')
    expect(screen.queryByRole('button', { name: 'Correct' })).not.toBeInTheDocument()
  })
})

describe('Approval and audit history (Stage 9B-4, step 9)', () => {
  it('lists the claims whose evidence failed when an approval is refused', async () => {
    serveReview({ extractions: reviewExtraction() })
    server.use(
      http.post(`${BASE}/extractions/:id/approvals`, () => HttpResponse.json({
        detail: 'Approval rejected — missing source evidence for: ownership_nature.',
        evidence_failures: [{ claim_id: KNOWN.id, field_name: 'ownership_nature', status: 'not_found' }],
      }, { status: 422 })),
    )
    renderReview()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve Extraction' }))
    const alert = await screen.findByTestId('action-error')
    expect(alert).toHaveTextContent('Approval rejected — missing source evidence for: ownership_nature.')
    expect(alert).toHaveTextContent('Ownership Nature: quotation not found in this document version')
    expect(screen.queryByText(/This extraction has been approved/)).not.toBeInTheDocument()
  })

  it('shows a 409 integrity refusal and re-reads the live state', async () => {
    const calls = serveReview({ extractions: reviewExtraction() })
    server.use(
      http.post(`${BASE}/extractions/:id/approvals`, () => HttpResponse.json({
        detail: 'Approval rejected — the stored source document of this version failed its integrity check (file_hash_mismatch).',
        source_integrity_failure: 'file_hash_mismatch',
      }, { status: 409 })),
    )
    renderReview()
    await userEvent.click(await screen.findByRole('button', { name: 'Approve Extraction' }))
    expect(await screen.findByTestId('action-error')).toHaveTextContent('failed its integrity check (file_hash_mismatch)')
    await waitFor(() => expect(calls.extraction).toBeGreaterThanOrEqual(2))
  })

  it('never shows approval without a current backend approval state', async () => {
    serveReview({
      extractions: reviewExtraction({
        status: 'approved',
        approval_state: undefined as unknown as ReturnType<typeof reviewExtraction>['approval_state'],
      }),
    })
    renderReview()
    await screen.findByTestId('claim-row-ownership_nature')
    expect(screen.queryByText(/This extraction has been approved/)).not.toBeInTheDocument()
    expect(screen.queryByText('Approve This Extraction')).not.toBeInTheDocument()
  })

  it('shows page and quotation changes in the correction history', async () => {
    serveReview({
      extractions: reviewExtraction(),
      corrections: [
        correctionRecord(),
        correctionRecord({
          id: 'corr-000', claim_id: UNKNOWN.id, original_value: 'Freely transferable', corrected_value: 'unknown',
          corrected_is_unknown: true, corrected_passage: null, original_source_page: null, corrected_source_page: null,
          page_history_recorded: false,
        }),
      ],
      approvals: [{ ...MOCK_APPROVAL, evidence_snapshot_format: 2 }],
    })
    renderReview()
    const records = await screen.findAllByTestId('correction-record')
    expect(records[0]).toHaveTextContent(`Before: Fee simple titlepage 2 — “${KNOWN.supporting_passage}”`)
    expect(records[0]).toHaveTextContent('After: Leasehold interestpage 1 — “each token represents a fractional ownership interest”')
    expect(records[1]).toHaveTextContent('page not recorded')
    expect(records[1]).toHaveTextContent('After: unknown')
    expect(screen.getByTestId('approval-evidence-format')).toHaveTextContent(
      'Evidence bound: per-claim quotation check results and page-text hashes',
    )
  })
})
