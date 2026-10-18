import { ApiError } from './api'
import type { ExternalProcessingSummary, ExtractionSource } from './types'

// External AI processing (Stage 10A-2, AI_EXTRACTION.md): the backend refuses to send document
// text to an external provider until the user confirms that exact request (HTTP 428).

// The summary of a 428 confirmation request, or null for any other error
export function confirmationRequest(err: unknown): ExternalProcessingSummary | null {
  if (!(err instanceof ApiError) || err.status !== 428) return null
  if (err.field('extraction_refusal') !== 'external_processing_confirmation_required') return null
  const summary = (err.body as { external_processing?: ExternalProcessingSummary } | null)?.external_processing
  return summary && typeof summary.version_id === 'string' ? summary : null
}

// Where a stored extraction's claims came from (Stage 10B-2). Only a model that the server lists as
// supported marks a genuine AI result; anything else (the test suite's mock, scripted seed data,
// retired or imported records) is never presented as AI extraction. Claims typed by a user are
// reported by the server as source 'manual' (Stage 10B-3).
export type ExtractionOriginKind = 'ai' | 'manual' | 'not_ai' | 'unconfirmed'

export function extractionOrigin(aiModel: string, supportedModels: readonly string[] | null, source?: ExtractionSource):
  { kind: ExtractionOriginKind; label: string } {
  if (source === 'manual') return { kind: 'manual', label: 'Manual entry · no AI' }
  if (supportedModels === null) return { kind: 'unconfirmed', label: `Origin not confirmed · ${aiModel}` }
  if (supportedModels.includes(aiModel)) return { kind: 'ai', label: `AI extraction · ${aiModel}` }
  return { kind: 'not_ai', label: `Not produced by a supported AI model (test or imported data) · ${aiModel}` }
}

// What the user is asked before anything is sent: who receives which text, and what the result is
export function confirmationPrompt(summary: ExternalProcessingSummary): string {
  const pages = summary.pages_without_text
  const unreadable = pages.length === 0 ? ''
    : pages.length === 1 ? ` Page ${pages[0]} has no text layer and cannot be read.`
      : ` Pages ${pages.join(', ')} have no text layer and cannot be read.`
  return (
    `Send this document to ${summary.processor}?\n\n` +
    `The extracted text of version ${summary.version_number} (${summary.page_count} pages, ` +
    `${summary.input_chars} characters including instructions) will be sent to ${summary.processor}, ` +
    `an external AI service (model ${summary.model}).${unreadable}\n\n` +
    'The result is an unverified AI draft. It is never approved automatically.'
  )
}
