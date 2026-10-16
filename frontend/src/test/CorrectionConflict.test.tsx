import { beforeEach, describe, expect, it } from 'vitest'
import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { mockEvidence } from './mocks/handlers'
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
import { ApiError } from '../api'
import { correctionConflictMessage, observedClaimState } from '../evidence'
import type { LegalClaim } from '../types'

// Stage 9B-4 closure, issue A: every correction states the claim as displayed; a server 409
// (the claim changed after it was read) is shown as a conflict, never retried automatically.

beforeEach(setReviewerSession)

type Body = Record<string, unknown>
const CHANGED: LegalClaim = {
  ...KNOWN,
  source_page: 1,
  supporting_passage: 'each token represents a fractional ownership interest in the property',
  evidence: mockEvidence({ cited_page: 1, found_on_pages: [1] }),
}
const CONFLICT = {
  detail: 'Correction rejected — the claim changed after it was read (source_page, supporting_passage). Nothing was changed: reload the claim and review its current value and evidence before correcting it.',
  correction_conflict: 'claim_changed',
  changed_fields: ['source_page', 'supporting_passage'],
}

function capture(respond: (call: number) => Response) {
  const bodies: Body[] = []
  server.use(
    http.patch(`${BASE}/extractions/:id/claims/:claimId`, async ({ request }) => {
      bodies.push(await request.json() as Body)
      return respond(bodies.length)
    }),
  )
  return bodies
}

async function openForm(field: string) {
  const row = await screen.findByTestId(`claim-row-${field}`)
  await userEvent.click(within(row).getByRole('button', { name: 'Correct' }))
  return screen.getByRole('form', { name: /^Correct / })
}

const save = () => userEvent.click(screen.getByRole('button', { name: 'Save Correction' }))

describe('Correction precondition (issue A)', () => {
  it('states the claim as displayed, not the reviewer’s draft', async () => {
    serveReview({ extractions: reviewExtraction() })
    const bodies = capture(() => HttpResponse.json(correctionRecord()))
    renderReview()
    const form = await openForm(KNOWN.field_name)
    const value = within(form).getByLabelText('Corrected Value')
    await userEvent.clear(value)
    await userEvent.type(value, 'Leasehold interest')
    await userEvent.type(within(form).getByLabelText('Cited page number'), '1')
    await userEvent.type(within(form).getByLabelText('Supporting quotation'), 'each token represents a fractional ownership interest')
    await save()
    await waitFor(() => expect(bodies).toHaveLength(1))
    expect(bodies[0].corrected_value).toBe('Leasehold interest')
    expect(bodies[0].expected_claim).toEqual({
      value: KNOWN.value, is_unknown: false, source_page: 2, supporting_passage: KNOWN.supporting_passage,
    })
  })

  it('states an unknown claim with nulls', () => {
    expect(observedClaimState(UNKNOWN)).toEqual({ value: 'unknown', is_unknown: true, source_page: null, supporting_passage: null })
  })

  it('shows a stale correction as a conflict, reloads the claim and never retries on its own', async () => {
    // Initial load, the form's re-read before sending (unchanged), then the reload after the conflict
    const calls = serveReview({
      extractions: [reviewExtraction(), reviewExtraction(), reviewExtraction({ claims: [CHANGED, UNKNOWN] })],
    })
    const bodies = capture(() => HttpResponse.json(CONFLICT, { status: 409 }))
    renderReview()
    await openForm(KNOWN.field_name)
    await save()
    const notice = await screen.findByTestId('correction-conflict')
    expect(notice).toHaveTextContent(
      'Another change to this claim was saved after you opened the correction form (changed: cited page, quotation). Nothing was saved.',
    )
    expect(notice).toHaveTextContent('review its value and evidence before correcting it again')
    await waitFor(() => expect(screen.queryByRole('form', { name: /^Correct / })).not.toBeInTheDocument())
    // The current claim is shown; the stale request is not sent again
    const row = screen.getByTestId('claim-row-ownership_nature')
    expect(await within(row).findByText('Page 1')).toBeInTheDocument()
    await new Promise(r => setTimeout(r, 100))
    expect(bodies).toHaveLength(1)
    expect(calls.extraction).toBe(3)
  })

  it('after reloading, the next correction states the new claim', async () => {
    serveReview({
      extractions: [reviewExtraction(), reviewExtraction(), reviewExtraction({ claims: [CHANGED, UNKNOWN] })],
    })
    const bodies = capture(call => call === 1
      ? HttpResponse.json(CONFLICT, { status: 409 })
      : HttpResponse.json(correctionRecord()))
    renderReview()
    await openForm(KNOWN.field_name)
    await save()
    await screen.findByTestId('correction-conflict')
    await waitFor(() => expect(screen.queryByRole('form', { name: /^Correct / })).not.toBeInTheDocument())
    await within(screen.getByTestId('claim-row-ownership_nature')).findByText('Page 1')
    // The reviewer reopens the form on the reloaded claim and saves again
    const form = await openForm(KNOWN.field_name)
    expect(within(form).getByTestId('current-evidence')).toHaveTextContent(`page 1 — “${CHANGED.supporting_passage}”`)
    await save()
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[1].expected_claim).toEqual({
      value: CHANGED.value, is_unknown: false, source_page: 1, supporting_passage: CHANGED.supporting_passage,
    })
  })
})

describe('Mandatory precondition: the state actually shown (issue A)', () => {
  it('keeps the claim as shown when the form was opened, even if the page reloads meanwhile', async () => {
    const draftState = { status: 'draft' as const }
    // Initial load; the reload after "Start Review" and the re-read before sending see another reviewer's change
    serveReview({
      extractions: [reviewExtraction(draftState), reviewExtraction({ ...draftState, claims: [CHANGED, UNKNOWN] })],
    })
    server.use(http.post(`${BASE}/extractions/:id/review`, () => HttpResponse.json({
      extraction_id: 'ext-001', status: 'under_review', reviewer_id: 'r', reviewer_role: 'issuer_reviewer',
    })))
    const bodies = capture(() => HttpResponse.json(correctionRecord()))
    renderReview()
    const form = await openForm(KNOWN.field_name)
    await userEvent.click(screen.getByRole('button', { name: 'Start Review' }))
    // The table shows the reloaded claim; the open form still shows what the reviewer is deciding on
    expect(await within(screen.getByTestId('claim-row-ownership_nature')).findByText('Page 1')).toBeInTheDocument()
    expect(within(form).getByTestId('current-evidence')).toHaveTextContent(`page 2 — “${KNOWN.supporting_passage}”`)
    await save()
    expect(await screen.findByTestId('correction-conflict')).toHaveTextContent('Nothing was saved.')
    // Nothing was sent: neither the stale state nor a newly fetched one replaced the reviewer's view
    expect(bodies).toHaveLength(0)
  })

  it('puts the precondition into every correction request it builds', async () => {
    const { buildCorrectionRequest, initialDraft } = await import('../evidence')
    const drafts = [
      initialDraft(KNOWN),
      { ...initialDraft(KNOWN), isUnknown: true },
      { ...initialDraft(KNOWN), pageMode: 'replace' as const, pageInput: '1' },
      { ...initialDraft(UNKNOWN), isUnknown: false, value: 'Known now', pageInput: '1', passageInput: 'A quotation of the page' },
    ]
    for (const [claim, draft] of [[KNOWN, drafts[0]], [KNOWN, drafts[1]], [KNOWN, drafts[2]], [UNKNOWN, drafts[3]]] as const) {
      const request = buildCorrectionRequest(claim, draft)
      expect(request.ok).toBe(true)
      if (request.ok) expect(request.payload.expected_claim).toEqual(observedClaimState(claim))
    }
  })
})

describe('Conflict messages', () => {
  it('names the changed parts of the claim', () => {
    const error = new ApiError(409, CONFLICT.detail, CONFLICT)
    expect(correctionConflictMessage(error)).toBe(
      'Another change to this claim was saved after you opened the correction form (changed: cited page, quotation). ' +
      'Nothing was saved. The current claim is shown below; review its value and evidence before correcting it again.',
    )
  })

  it('keeps the backend message for other conflicts', () => {
    const error = new ApiError(409, 'An approved extraction cannot be corrected.', { detail: 'An approved extraction cannot be corrected.' })
    expect(correctionConflictMessage(error)).toBe('An approved extraction cannot be corrected. Nothing was saved.')
  })
})
