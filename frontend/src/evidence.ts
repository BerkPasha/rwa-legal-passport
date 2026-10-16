// Evidence display and reviewer-correction rules of the review UI (Stage 9B-4, step 9).
//
// The backend is the only authority: it checks every quotation against the page text of the
// extraction's own document version and applies the correction rules of step 7
// (app/correction_rules.py). This module only labels the backend's results and builds requests
// that state the reviewer's intent explicitly; it never computes or assumes a verification.

import { ApiError } from './api'
import type {
  ClaimCorrectionPayload,
  ClaimEvidence,
  CorrectionRejection,
  EvidenceStatus,
  ExpectedClaimState,
  LegalClaim,
} from './types'

export const EVIDENCE_SCOPE_NOTE =
  'A found quote shows only that the quoted text appears on the cited page of this document version. ' +
  'It is not a legal interpretation, a reviewer approval or independent legal verification.'

// Short phrases for a status; used where only the status is known (error responses)
export const EVIDENCE_STATUS_LABELS: Record<EvidenceStatus, string> = {
  verified: 'quote found on the cited page',
  not_found: 'quotation not found in this document version',
  found_on_other_page: 'quotation is not on the cited page',
  page_missing: 'no source page cited',
  page_out_of_range: 'cited page does not exist in this document version',
  no_text_layer: 'cited page has no text layer (no OCR is performed)',
  quotation_missing: 'no quotation',
  quotation_too_short: 'quotation too short (at least 20 characters and 3 words)',
  document_version_mismatch: 'checked against a different document version',
  source_text_unavailable: 'source text of this version is unavailable',
  source_text_invalid: 'source text of this version is inconsistent',
  not_applicable: 'not applicable (unknown claim)',
}

export function evidenceStatusLabel(status: string | undefined | null): string {
  return (status && EVIDENCE_STATUS_LABELS[status as EvidenceStatus]) || status || 'unknown status'
}

// Badge text for a claim's evidence, from the backend's evidence object only
export function claimEvidenceLabel(claim: LegalClaim): string {
  const e: ClaimEvidence | undefined = claim.evidence
  if (claim.is_unknown) return 'Unknown'
  if (!e) return 'Evidence status unavailable'
  const page = e.cited_page
  switch (e.status) {
    case 'verified':
      return `Quote found on page ${page}`
    case 'not_found':
      return 'Quote not found in document'
    case 'found_on_other_page':
      return page != null
        ? `Not on cited page ${page}; found on page ${e.found_on_pages.join(', ')}`
        : `Found on page ${e.found_on_pages.join(', ')}; no page cited`
    case 'page_missing':
      return 'No source page cited'
    case 'page_out_of_range':
      return page != null ? `Page ${page} does not exist (${e.page_count} pages)` : 'Cited page does not exist'
    case 'no_text_layer':
      return `Page ${page} has no text layer`
    case 'quotation_missing':
      return 'No quotation'
    case 'quotation_too_short':
      return 'Quotation too short'
    case 'document_version_mismatch':
      return 'Version mismatch'
    case 'source_text_unavailable':
      return 'Source text unavailable'
    case 'source_text_invalid':
      return 'Source text inconsistent'
    default:
      return 'Not verified'
  }
}

// ── HTTP errors ───────────────────────────────────────────────────────────────

// A message for the user; never reveals whether an inaccessible resource exists
export function httpErrorMessage(error: unknown, action: string): string {
  if (!(error instanceof ApiError)) return `Could not ${action}: the server could not be reached.`
  switch (error.status) {
    case 401:
      return 'Authentication failed: the token is missing, invalid or disabled. Update it under Credentials.'
    case 403:
      return `Your credentials are not authorized to ${action}.`
    case 404:
      return 'Not found: it does not exist or is outside your access.'
    case 429:
      return 'Too many requests. Please wait a moment and try again.'
    default:
      return error.message || `Could not ${action} (HTTP ${error.status}).`
  }
}

const CORRECTION_RULE_MESSAGES: Record<CorrectionRejection, string> = {
  evidence_required:
    'The value changed, so the previous page and quotation cannot be reused. Enter the page and the ' +
    'quotation that support the new value.',
  unknown_with_evidence: 'An unknown claim cannot carry a page or a quotation.',
  unknown_value_for_known_claim: "The value says the field is unknown. Mark the claim as unknown instead.",
  evidence_not_verified: 'The quotation could not be verified on the cited page of this document version',
}

export function correctionErrorMessage(error: unknown): string {
  if (error instanceof ApiError && error.status === 422) {
    const rule = error.field('correction_rule') as CorrectionRejection | undefined
    if (rule && rule in CORRECTION_RULE_MESSAGES) {
      const status = error.field('evidence_status')
      const reason = rule === 'evidence_not_verified' ? `: ${evidenceStatusLabel(status)}.` : ''
      return `Correction refused — ${CORRECTION_RULE_MESSAGES[rule]}${reason} Nothing was changed.`
    }
    return `Correction refused — ${error.message} Nothing was changed.`
  }
  return httpErrorMessage(error, 'correct this claim')
}

const CLAIM_STATE_LABELS: Record<string, string> = {
  value: 'value',
  is_unknown: 'unknown flag',
  source_page: 'cited page',
  supporting_passage: 'quotation',
}

// A 409 on a correction: the claim changed after it was read (issue A), or the extraction is approved
export function correctionConflictMessage(error: ApiError): string {
  if (error.field('correction_conflict') === 'claim_changed') {
    const fields = (error.body as { changed_fields?: unknown } | null)?.changed_fields
    const changed = Array.isArray(fields) ? fields.map(f => CLAIM_STATE_LABELS[String(f)] ?? String(f)).join(', ') : ''
    return (
      `Another change to this claim was saved after you opened the correction form${changed ? ` (changed: ${changed})` : ''}. ` +
      'Nothing was saved. The current claim is shown below; review its value and evidence before correcting it again.'
    )
  }
  return `${error.message} Nothing was saved.`
}

export interface ApprovalFailure {
  claim_id: string
  field_name: string
  status: string
}

export function approvalEvidenceFailures(error: unknown): ApprovalFailure[] {
  if (!(error instanceof ApiError) || error.status !== 422) return []
  const failures = (error.body as { evidence_failures?: unknown } | null)?.evidence_failures
  return Array.isArray(failures)
    ? failures.filter((f): f is ApprovalFailure =>
        typeof f?.field_name === 'string' && typeof f?.status === 'string' && typeof f?.claim_id === 'string')
    : []
}

// ── Correction requests ───────────────────────────────────────────────────────

export type EvidenceMode = 'keep' | 'replace'

export interface CorrectionDraft {
  value: string
  isUnknown: boolean
  pageMode: EvidenceMode
  pageInput: string
  passageMode: EvidenceMode
  passageInput: string
  note: string
}

export function initialDraft(claim: LegalClaim): CorrectionDraft {
  const draft: CorrectionDraft = {
    // An unknown claim's value is the word "unknown"; a known value has to be typed
    value: claim.is_unknown ? '' : claim.value,
    isUnknown: claim.is_unknown,
    pageMode: 'keep',
    pageInput: '',
    passageMode: 'keep',
    passageInput: '',
    note: '',
  }
  return {
    ...draft,
    pageMode: canKeep(claim, draft, 'page') ? 'keep' : 'replace',
    passageMode: canKeep(claim, draft, 'passage') ? 'keep' : 'replace',
  }
}

// Same comparison as the backend: an unknown claim made known is a change; values are compared exactly
export function valueChanged(claim: LegalClaim, draft: CorrectionDraft): boolean {
  return !draft.isUnknown && (claim.is_unknown || draft.value !== claim.value)
}

// The current page / quotation may be kept (omitted from the request) only for an unchanged,
// known value that has one. A changed value never inherits the previous evidence.
export function canKeep(claim: LegalClaim, draft: CorrectionDraft, field: 'page' | 'passage'): boolean {
  if (draft.isUnknown || claim.is_unknown || valueChanged(claim, draft)) return false
  return field === 'page' ? claim.source_page != null : claim.supporting_passage != null
}

export function effectiveMode(claim: LegalClaim, draft: CorrectionDraft, field: 'page' | 'passage'): EvidenceMode {
  const mode = field === 'page' ? draft.pageMode : draft.passageMode
  return mode === 'keep' && canKeep(claim, draft, field) ? 'keep' : 'replace'
}

export type CorrectionRequest =
  | { ok: true; payload: ClaimCorrectionPayload; summary: string }
  | { ok: false; error: string }

const MAX_PAGE = 2_147_483_647

// Builds the PATCH body. Omitted page / passage = keep the current one; null = clear it.
// Structural checks only: whether the quotation is on the page is decided by the backend.
// The claim exactly as displayed: the server refuses the correction (409) if it no longer matches
export function observedClaimState(claim: LegalClaim): ExpectedClaimState {
  return {
    value: claim.value,
    is_unknown: claim.is_unknown,
    source_page: claim.source_page,
    supporting_passage: claim.supporting_passage,
  }
}

export function buildCorrectionRequest(claim: LegalClaim, draft: CorrectionDraft): CorrectionRequest {
  const note = draft.note.trim()
  // Every request states the claim as shown to the reviewer (mandatory precondition, issue A)
  const expected_claim = observedClaimState(claim)
  const withNote = (payload: ClaimCorrectionPayload): ClaimCorrectionPayload =>
    note ? { ...payload, correction_note: note } : payload

  if (draft.isUnknown) {
    return {
      ok: true,
      payload: withNote({
        corrected_value: 'unknown',
        is_unknown: true,
        corrected_source_page: null,
        corrected_passage: null,
        expected_claim,
      }),
      summary: 'The claim will be marked unknown: its value becomes "unknown" and its page and quotation are cleared.',
    }
  }

  if (draft.value.trim() === '') return { ok: false, error: 'Enter a value, or mark the claim as unknown.' }
  if (draft.value.trim().toLowerCase() === 'unknown') {
    return { ok: false, error: 'The value says "unknown". Mark the claim as unknown instead.' }
  }

  const payload: ClaimCorrectionPayload = { corrected_value: draft.value, is_unknown: false, expected_claim }
  const parts: string[] = []

  if (effectiveMode(claim, draft, 'page') === 'keep') {
    parts.push(`the current page ${claim.source_page} is kept`)
  } else {
    const raw = draft.pageInput.trim()
    const page = /^\d+$/.test(raw) ? Number(raw) : NaN
    if (!Number.isInteger(page) || page < 1 || page > MAX_PAGE) {
      return { ok: false, error: 'Enter the cited page as a whole number of 1 or more.' }
    }
    payload.corrected_source_page = page
    parts.push(`page ${page} is cited`)
  }

  if (effectiveMode(claim, draft, 'passage') === 'keep') {
    parts.push('the current quotation is kept')
  } else {
    const passage = draft.passageInput.trim()
    if (passage === '') return { ok: false, error: 'Enter the supporting quotation, copied exactly from the cited page.' }
    payload.corrected_passage = passage
    parts.push('the new quotation is sent')
  }

  return {
    ok: true,
    payload: withNote(payload),
    summary: `Evidence: ${parts.join('; ')}. The server verifies the quotation on the cited page of this document version before saving.`,
  }
}
