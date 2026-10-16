import { describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { mockEvidence } from './mocks/handlers'
import { ApiError, getAssets } from '../api'
import {
  approvalEvidenceFailures,
  buildCorrectionRequest,
  canKeep,
  claimEvidenceLabel,
  correctionErrorMessage,
  type CorrectionDraft,
  httpErrorMessage,
  initialDraft,
  valueChanged,
} from '../evidence'
import type { LegalClaim } from '../types'

const KNOWN: LegalClaim = {
  id: 'claim-001',
  extraction_id: 'ext-001',
  field_name: 'ownership_nature',
  value: 'Fee simple title',
  is_unknown: false,
  source_page: 2,
  supporting_passage: 'The Trustee holds fee simple title to the Property',
  created_at: '2024-01-02T00:00:00Z',
  evidence: mockEvidence({ cited_page: 2, found_on_pages: [2] }),
}
const UNKNOWN: LegalClaim = {
  ...KNOWN,
  id: 'claim-002',
  value: 'unknown',
  is_unknown: true,
  source_page: null,
  supporting_passage: null,
  evidence: mockEvidence({ status: 'not_applicable', cited_page: null, found_on_pages: [] }),
}

const draft = (claim: LegalClaim, patch: Partial<CorrectionDraft> = {}): CorrectionDraft => ({ ...initialDraft(claim), ...patch })
const has = (o: object, key: string) => Object.prototype.hasOwnProperty.call(o, key)
// Every request states the claim as read (issue A precondition)
const asRead = (claim: LegalClaim) => ({
  expected_claim: {
    value: claim.value, is_unknown: claim.is_unknown, source_page: claim.source_page, supporting_passage: claim.supporting_passage,
  },
})

describe('correction request: omitted, explicit and cleared evidence', () => {
  it('keeps the current evidence of an unchanged value by omitting both fields', () => {
    const r = buildCorrectionRequest(KNOWN, draft(KNOWN, { note: '  checked  ' }))
    expect(r.ok).toBe(true)
    if (!r.ok) return
    expect(r.payload).toEqual({ corrected_value: 'Fee simple title', is_unknown: false, correction_note: 'checked', ...asRead(KNOWN) })
    expect(has(r.payload, 'corrected_source_page')).toBe(false)
    expect(has(r.payload, 'corrected_passage')).toBe(false)
    expect(r.summary).toMatch(/current page 2 is kept; the current quotation is kept/)
  })

  it('may replace only the page of an unchanged value and keep the quotation', () => {
    const r = buildCorrectionRequest(KNOWN, draft(KNOWN, { pageMode: 'replace', pageInput: ' 3 ' }))
    expect(r.ok && r.payload).toEqual({ corrected_value: 'Fee simple title', is_unknown: false, corrected_source_page: 3, ...asRead(KNOWN) })
  })

  it('never reuses the previous evidence for a changed value', () => {
    const changed = draft(KNOWN, { value: 'Leasehold interest' })
    expect(valueChanged(KNOWN, changed)).toBe(true)
    expect(canKeep(KNOWN, changed, 'page')).toBe(false)
    expect(canKeep(KNOWN, changed, 'passage')).toBe(false)
    // Still in "keep" mode: refused instead of silently inheriting the old page and quotation
    const refused = buildCorrectionRequest(KNOWN, changed)
    expect(refused).toEqual({ ok: false, error: 'Enter the cited page as a whole number of 1 or more.' })
    const onlyPage = buildCorrectionRequest(KNOWN, { ...changed, pageInput: '2' })
    expect(onlyPage.ok).toBe(false)
    const both = buildCorrectionRequest(KNOWN, { ...changed, pageInput: '2', passageInput: '  The lease runs for 99 years from completion  ' })
    expect(both.ok && both.payload).toEqual({
      corrected_value: 'Leasehold interest',
      is_unknown: false,
      corrected_source_page: 2,
      corrected_passage: 'The lease runs for 99 years from completion',
      ...asRead(KNOWN),
    })
  })

  it('compares the value exactly, like the backend', () => {
    expect(valueChanged(KNOWN, draft(KNOWN, { value: 'Fee simple title ' }))).toBe(true)
    expect(valueChanged(KNOWN, draft(KNOWN, { value: 'fee simple title' }))).toBe(true)
    expect(valueChanged(KNOWN, draft(KNOWN))).toBe(false)
  })

  it('clears the evidence explicitly when a claim becomes unknown', () => {
    const r = buildCorrectionRequest(KNOWN, draft(KNOWN, { isUnknown: true, pageInput: '9', passageInput: 'ignored text here' }))
    expect(r.ok && r.payload).toEqual({
      corrected_value: 'unknown',
      is_unknown: true,
      corrected_source_page: null,
      corrected_passage: null,
      ...asRead(KNOWN),
    })
    if (r.ok) {
      expect(has(r.payload, 'corrected_source_page')).toBe(true)
      expect(has(r.payload, 'corrected_passage')).toBe(true)
    }
  })

  it('treats an unknown claim made known as a change that needs a value, page and quotation', () => {
    const start = initialDraft(UNKNOWN)
    expect(start).toMatchObject({ isUnknown: true, value: '', pageMode: 'replace', passageMode: 'replace' })
    const known = { ...start, isUnknown: false }
    expect(valueChanged(UNKNOWN, known)).toBe(true)
    expect(buildCorrectionRequest(UNKNOWN, known)).toEqual({ ok: false, error: 'Enter a value, or mark the claim as unknown.' })
    expect(buildCorrectionRequest(UNKNOWN, { ...known, value: ' Unknown ' })).toEqual({
      ok: false, error: 'The value says "unknown". Mark the claim as unknown instead.',
    })
    expect(buildCorrectionRequest(UNKNOWN, { ...known, value: 'Freehold', pageInput: '1' }).ok).toBe(false)
    const r = buildCorrectionRequest(UNKNOWN, { ...known, value: 'Freehold', pageInput: '1', passageInput: 'Freehold title is held by the Trust' })
    expect(r.ok && r.payload).toEqual({
      corrected_value: 'Freehold', is_unknown: false, corrected_source_page: 1, corrected_passage: 'Freehold title is held by the Trust',
      ...asRead(UNKNOWN),
    })
  })

  it('never lets a legacy unknown claim that still carries old evidence keep it', () => {
    // Before Stage 9B-4 step 7 an unknown claim could keep its old value, page and quotation
    const legacy: LegalClaim = {
      ...UNKNOWN,
      value: 'Freely transferable',
      source_page: 1,
      supporting_passage: 'Interests are freely transferable without consent',
    }
    const d = { ...initialDraft(legacy), isUnknown: false, value: 'Freely transferable' }
    expect(valueChanged(legacy, d)).toBe(true)
    expect(canKeep(legacy, d, 'page')).toBe(false)
    expect(canKeep(legacy, d, 'passage')).toBe(false)
    expect(buildCorrectionRequest(legacy, { ...d, pageMode: 'keep', passageMode: 'keep' }).ok).toBe(false)
  })

  it.each(['0', '-1', '1.5', 'abc', '1e3', '', '2147483648'])('rejects the page %j without sending anything', page => {
    const r = buildCorrectionRequest(KNOWN, draft(KNOWN, { pageMode: 'replace', pageInput: page }))
    expect(r).toEqual({ ok: false, error: 'Enter the cited page as a whole number of 1 or more.' })
  })

  it('rejects an empty new quotation instead of clearing it', () => {
    const r = buildCorrectionRequest(KNOWN, draft(KNOWN, { passageMode: 'replace', passageInput: '   ' }))
    expect(r.ok).toBe(false)
  })

  it('cannot keep evidence that does not exist', () => {
    const noPage = { ...KNOWN, source_page: null }
    expect(canKeep(noPage, initialDraft(noPage), 'page')).toBe(false)
    expect(initialDraft(noPage).pageMode).toBe('replace')
    expect(canKeep(noPage, initialDraft(noPage), 'passage')).toBe(true)
  })
})

describe('evidence labels come from the backend status only', () => {
  it('labels each status without claiming more than source-text presence', () => {
    expect(claimEvidenceLabel(KNOWN)).toBe('Quote found on page 2')
    expect(claimEvidenceLabel(UNKNOWN)).toBe('Unknown')
    expect(claimEvidenceLabel({ ...KNOWN, evidence: mockEvidence({ status: 'not_found', cited_page: 2, found_on_pages: [] }) }))
      .toBe('Quote not found in document')
    expect(claimEvidenceLabel({ ...KNOWN, evidence: mockEvidence({ status: 'found_on_other_page', cited_page: 2, found_on_pages: [1] }) }))
      .toBe('Not on cited page 2; found on page 1')
    expect(claimEvidenceLabel({ ...KNOWN, evidence: mockEvidence({ status: 'page_out_of_range', cited_page: 7, page_count: 2 }) }))
      .toBe('Page 7 does not exist (2 pages)')
    // A cited passage alone is never shown as found
    const unverified = { ...KNOWN, evidence: mockEvidence({ status: 'quotation_too_short' }) }
    expect(claimEvidenceLabel(unverified)).toBe('Quotation too short')
    expect(claimEvidenceLabel({ ...KNOWN, evidence: undefined as unknown as LegalClaim['evidence'] }))
      .toBe('Evidence status unavailable')
  })
})

describe('API error messages', () => {
  const err = (status: number, body: unknown, message = 'x') => new ApiError(status, message, body)

  it('explains each correction rule and says that nothing was changed', () => {
    expect(correctionErrorMessage(err(422, { correction_rule: 'evidence_required' })))
      .toMatch(/value changed, so the previous page and quotation cannot be reused.*Nothing was changed\./)
    expect(correctionErrorMessage(err(422, { correction_rule: 'unknown_with_evidence' })))
      .toMatch(/unknown claim cannot carry a page or a quotation/)
    expect(correctionErrorMessage(err(422, { correction_rule: 'unknown_value_for_known_claim' })))
      .toMatch(/Mark the claim as unknown instead/)
    expect(correctionErrorMessage(err(422, { correction_rule: 'evidence_not_verified', evidence_status: 'no_text_layer' })))
      .toBe('Correction refused — The quotation could not be verified on the cited page of this document version: cited page has no text layer (no OCR is performed). Nothing was changed.')
    expect(correctionErrorMessage(err(422, null, 'Invalid request — corrected_passage: too long')))
      .toBe('Correction refused — Invalid request — corrected_passage: too long Nothing was changed.')
  })

  it('maps authentication, authorization and access errors without revealing resources', () => {
    expect(httpErrorMessage(err(401, null), 'x')).toMatch(/^Authentication failed/)
    expect(httpErrorMessage(err(403, null), 'correct this claim')).toBe('Your credentials are not authorized to correct this claim.')
    expect(httpErrorMessage(err(404, { detail: 'Document not found.' }), 'x'))
      .toBe('Not found: it does not exist or is outside your access.')
    expect(httpErrorMessage(new TypeError('Failed to fetch'), 'load it')).toBe('Could not load it: the server could not be reached.')
  })

  it('reads the failing claims of a refused approval', () => {
    const failures = [{ claim_id: 'c1', field_name: 'jurisdiction', status: 'not_found' }, { bogus: true }]
    expect(approvalEvidenceFailures(err(422, { evidence_failures: failures }))).toEqual([failures[0]])
    expect(approvalEvidenceFailures(err(409, { evidence_failures: failures }))).toEqual([])
  })

  it('keeps the error body and turns validation errors into a readable message', async () => {
    server.use(
      http.get('http://localhost:8000/assets', () => HttpResponse.json(
        { detail: [{ loc: ['body', 'corrected_source_page'], msg: 'Input should be a valid integer', type: 'int_type' }] },
        { status: 422 },
      )),
    )
    const error = await getAssets().catch(e => e)
    expect(error).toBeInstanceOf(ApiError)
    expect(error.status).toBe(422)
    expect(error.message).toBe('Invalid request — corrected_source_page: Input should be a valid integer')
    expect(Array.isArray(error.body.detail)).toBe(true)
  })

  it('exposes structured string fields of the error body', async () => {
    server.use(
      http.get('http://localhost:8000/assets', () => HttpResponse.json(
        { detail: 'Refused.', correction_rule: 'evidence_required', evidence_status: null },
        { status: 422 },
      )),
    )
    const error = await getAssets().catch(e => e) as ApiError
    expect(error.message).toBe('Refused.')
    expect(error.field('correction_rule')).toBe('evidence_required')
    expect(error.field('evidence_status')).toBeUndefined()
  })
})
