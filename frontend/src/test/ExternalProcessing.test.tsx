import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_DOCUMENT, MOCK_EXTRACTION } from './mocks/handlers'
import { AssetDetail } from '../pages/AssetDetail'
import { AuthProvider } from '../AuthContext'
import { ApiError } from '../api'
import { confirmationPrompt, confirmationRequest } from '../aiProcessing'
import type { ExternalProcessingSummary } from '../types'

// Stage 10A-2: document text reaches an external AI provider only after the user confirms that exact
// request. All responses come from the MSW mock server; nothing is sent anywhere.
const BASE = 'http://localhost:8000'
const SUMMARY: ExternalProcessingSummary = {
  processor: 'Anthropic', model: 'claude-opus-5-5', document_id: 'doc-001', version_id: 'ver-001',
  version_number: 1, page_count: 3, pages_without_text: [2], input_chars: 4210,
  max_input_chars: 200000, max_pages: 200, max_output_tokens: 8192,
}
const CONFIRMATION_REQUIRED = {
  detail: 'Confirmation required: …', extraction_refusal: 'external_processing_confirmation_required',
  external_processing: SUMMARY,
}

afterEach(() => vi.restoreAllMocks())

// Records the body of every extraction request; answers 428 until a confirmation arrives
function serveExtractions(first: { status: number; body: object }) {
  const bodies: string[] = []
  server.use(http.post(`${BASE}/documents/:docId/extractions`, async ({ request }) => {
    const text = await request.text()
    bodies.push(text)
    if (!text) return HttpResponse.json(first.body, { status: first.status })
    return HttpResponse.json(MOCK_EXTRACTION, { status: 201 })
  }))
  return bodies
}

async function runExtraction() {
  render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <AuthProvider><Routes><Route path="assets/:id" element={<AssetDetail />} /></Routes></AuthProvider>
    </MemoryRouter>,
  )
  await screen.findByText(MOCK_DOCUMENT.title)
  await userEvent.click(screen.getByRole('button', { name: /Run AI Extraction/ }))
}

describe('Explicit confirmation before external AI processing', () => {
  it('asks before sending and then confirms exactly the version it was shown', async () => {
    const bodies = serveExtractions({ status: 428, body: CONFIRMATION_REQUIRED })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await runExtraction()
    await waitFor(() => expect(bodies).toHaveLength(2))
    expect(bodies[0]).toBe('')   // the first request confirms nothing
    expect(JSON.parse(bodies[1])).toEqual({ confirm_external_processing: true, version_id: 'ver-001' })
    expect(confirm).toHaveBeenCalledTimes(1)
    const prompt = confirm.mock.calls[0][0] as string
    expect(prompt).toContain('Send this document to Anthropic?')
    expect(prompt).toContain('version 1 (3 pages, 4210 characters including instructions)')
    expect(prompt).toContain('model claude-opus-5-5')
    expect(prompt).toContain('Page 2 has no text layer and cannot be read.')
    expect(prompt).toContain('never approved automatically')
    expect(alert).not.toHaveBeenCalled()
  })

  it('sends nothing more when the user declines', async () => {
    const bodies = serveExtractions({ status: 428, body: CONFIRMATION_REQUIRED })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await runExtraction()
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1))
    await waitFor(() => expect(screen.getByRole('button', { name: /Run AI Extraction/ })).toBeEnabled())
    expect(bodies).toEqual([''])
    expect(alert).not.toHaveBeenCalled()
  })

  it.each([
    [503, { detail: 'AI extraction is disabled on this server (AI_PROVIDER=disabled): no document is sent to any AI service. See AI_EXTRACTION.md.', ai_error: 'ai_provider_disabled' }],
    [403, { detail: 'The issuer of this document has not authorized external AI processing, so no document text was sent.', extraction_refusal: 'external_processing_not_authorized' }],
    [422, { detail: 'The model input would be 250000 characters; the limit is 200000 (AI_MAX_INPUT_CHARS). Nothing was sent.', extraction_refusal: 'input_too_large', external_processing: SUMMARY }],
  ])('shows a %s refusal without asking for confirmation', async (status, body) => {
    const bodies = serveExtractions({ status, body })
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await runExtraction()
    expect(await screen.findByTestId('extraction-error')).toHaveTextContent(body.detail)
    expect(alert).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    expect(bodies).toEqual([''])
  })
})

describe('confirmationRequest', () => {
  it('accepts only a 428 confirmation request with a summary', () => {
    expect(confirmationRequest(new ApiError(428, 'x', CONFIRMATION_REQUIRED))).toEqual(SUMMARY)
    expect(confirmationRequest(new ApiError(409, 'x', { ...CONFIRMATION_REQUIRED, extraction_refusal: 'confirmation_version_mismatch' }))).toBeNull()
    expect(confirmationRequest(new ApiError(428, 'x', { extraction_refusal: 'external_processing_confirmation_required' }))).toBeNull()
    expect(confirmationRequest(new ApiError(428, 'x', { ...CONFIRMATION_REQUIRED, extraction_refusal: 'other' }))).toBeNull()
    expect(confirmationRequest(new Error('x'))).toBeNull()
  })

  it('names several unreadable pages', () => {
    expect(confirmationPrompt({ ...SUMMARY, pages_without_text: [2, 5] })).toContain('Pages 2, 5 have no text layer')
    expect(confirmationPrompt({ ...SUMMARY, pages_without_text: [] })).not.toContain('text layer')
  })
})
