import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_DOCUMENT, MOCK_VERSION } from './mocks/handlers'
import { AssetDetail } from '../pages/AssetDetail'
import { AuthProvider } from '../AuthContext'
import type { TextLayerSummary } from '../types'

// Stage 10A-1: pages without a text layer (scanned PDFs) are identified, never guessed. All data
// comes from the MSW mock server.
const BASE = 'http://localhost:8000'
const NOTICE = 'Page numbers are physical positions in the PDF (1 = first page), not printed page labels.'
const NO_TEXT_DETAIL = 'This document version has no extractable text: no page has a text layer (for example a scanned PDF). OCR is not supported, so the document was not sent to the AI service and no extraction was created.'

afterEach(() => vi.restoreAllMocks())

function layer(overrides: Partial<TextLayerSummary>): TextLayerSummary {
  return { status: 'complete', page_count: 3, pages_with_text: 3, pages_without_text: [], requires_ocr: false, notice: NOTICE, ...overrides }
}

function serveDocument(textLayer: TextLayerSummary | null) {
  server.use(http.get(`${BASE}/assets/:id/documents`, () =>
    HttpResponse.json([{ ...MOCK_DOCUMENT, current_version: { ...MOCK_VERSION, text_layer: textLayer } }])))
}

function renderAsset() {
  render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <AuthProvider><Routes><Route path="assets/:id" element={<AssetDetail />} /></Routes></AuthProvider>
    </MemoryRouter>,
  )
  return screen.findByText(MOCK_DOCUMENT.title)
}

describe('Text layer of the current document version', () => {
  it('names the pages without a text layer of a partly scanned PDF', async () => {
    serveDocument(layer({ status: 'partial', pages_with_text: 2, pages_without_text: [2], requires_ocr: true }))
    await renderAsset()
    expect(screen.getByTestId('text-layer-notice')).toHaveTextContent(
      'No text layer on page 2 of 3 (for example scanned pages). OCR is not supported: quotations on that page can never be verified.')
  })

  it('says that nothing can be extracted from a fully scanned PDF', async () => {
    serveDocument(layer({ status: 'none', page_count: 2, pages_with_text: 0, pages_without_text: [1, 2], requires_ocr: true }))
    await renderAsset()
    expect(screen.getByTestId('text-layer-notice')).toHaveTextContent(
      'No page has a text layer (for example a scanned PDF). OCR is not supported: nothing can be extracted or verified from this version.')
  })

  it('shows no notice when every page has text', async () => {
    serveDocument(layer({}))
    await renderAsset()
    expect(screen.queryByTestId('text-layer-notice')).not.toBeInTheDocument()
  })

  it('shows no notice for a document without a summary', async () => {
    serveDocument(null)
    await renderAsset()
    expect(screen.queryByTestId('text-layer-notice')).not.toBeInTheDocument()
  })

  it('shows the backend refusal when extraction is refused for a document without text', async () => {
    serveDocument(layer({ status: 'none', page_count: 1, pages_with_text: 0, pages_without_text: [1], requires_ocr: true }))
    server.use(http.post(`${BASE}/documents/:docId/extractions`, () =>
      HttpResponse.json({ detail: NO_TEXT_DETAIL, extraction_refusal: 'no_extractable_text' }, { status: 422 })))
    const alert = vi.spyOn(window, 'alert').mockImplementation(() => {})
    await renderAsset()
    await userEvent.click(screen.getByRole('button', { name: /Run AI Extraction/ }))
    expect(await screen.findByTestId('extraction-error')).toHaveTextContent(NO_TEXT_DETAIL)
    expect(alert).not.toHaveBeenCalled()
  })
})
