import { afterEach, describe, expect, it, vi } from 'vitest'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_CURRENT_USER, MOCK_DOCUMENT, MOCK_EXTRACTION, MOCK_VERSION } from './mocks/handlers'
import { AssetDetail } from '../pages/AssetDetail'
import { AuthProvider } from '../AuthContext'
import { setUserToken } from '../api'
import type { DocumentVersion, LegalExtraction } from '../types'

// Uploading a new version of a document from the asset page (POST /documents/{id}/versions). The
// server's own behaviour (approvals superseded, nothing carried over) is tested in the backend suite.
const BASE = 'http://localhost:8000'
const SESSION_KEY = 'rwa_passport_session_auth'
const V2: DocumentVersion = { ...MOCK_VERSION, id: 'ver-002', version_number: 2, original_filename: 'prospectus_v2.pdf',
                              sha256_hash: 'b'.repeat(64), created_at: '2024-02-01T00:00:00Z' }
const SUPERSEDED: LegalExtraction = {
  ...MOCK_EXTRACTION,
  approval_state: { state: 'superseded', version_is_current: false, current_approval_types: [],
                    reasons: ['document_version_superseded'] },
}
const pdf = () => new File(['%PDF-1.4 fictional amendment %%EOF'], 'amendment.pdf', { type: 'application/pdf' })

function signIn(permissions = MOCK_CURRENT_USER.permissions) {
  sessionStorage.setItem(SESSION_KEY, JSON.stringify({ userToken: 'fictional-reviewer-token', apiKey: '' }))
  server.use(http.get(`${BASE}/auth/me`, () => HttpResponse.json({ ...MOCK_CURRENT_USER, permissions })))
}

// The request body as handed to fetch. A browser sends this FormData as multipart/form-data; under
// jsdom, Node's fetch cannot serialize jsdom's FormData, so the body is checked here, not in MSW.
function captureUploads() {
  const uploads: { body: FormData; headers: Record<string, string> }[] = []
  const intercepted = globalThis.fetch   // MSW's fetch, installed by server.listen()
  vi.spyOn(globalThis, 'fetch').mockImplementation((input, init) => {
    if (init?.body instanceof FormData) {
      uploads.push({ body: init.body, headers: (init.headers ?? {}) as Record<string, string> })
    }
    return intercepted(input, init)
  })
  return uploads
}

// The server's state before and after the upload; `answer` decides the upload's response
function serve(answer: () => Response | Promise<Response>, before: LegalExtraction[] = [MOCK_EXTRACTION]) {
  const seen = { uploaded: false, authorization: '', documentReads: 0, uploads: captureUploads() }
  server.use(
    http.get(`${BASE}/assets/:id/documents`, () => {
      seen.documentReads += 1
      return HttpResponse.json([seen.uploaded
        ? { ...MOCK_DOCUMENT, current_version_number: 2, current_version: V2 }
        : { ...MOCK_DOCUMENT, current_version: MOCK_VERSION }])
    }),
    http.get(`${BASE}/documents/:docId/extractions`, () => HttpResponse.json(seen.uploaded ? before.map(() => SUPERSEDED) : before)),
    http.post(`${BASE}/documents/:docId/versions`, async ({ request, params }) => {
      expect(params.docId).toBe('doc-001')
      seen.authorization = request.headers.get('Authorization') ?? ''
      const response = await answer()
      seen.uploaded = response.status === 201
      return response
    }),
  )
  return seen
}

function renderAsset() {
  return render(
    <MemoryRouter initialEntries={['/assets/asset-001']}>
      <AuthProvider>
        <Routes>
          <Route path="assets/:id" element={<AssetDetail />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  )
}

async function chooseAndUpload(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByTestId('upload-new-version'))
  const form = screen.getByTestId('new-version-form')
  await user.upload(within(form).getByLabelText('PDF File'), pdf())
  await user.click(within(form).getByRole('button', { name: 'Upload Version' }))
  return form
}

afterEach(() => {
  vi.restoreAllMocks()
  sessionStorage.clear()
  setUserToken('')
})

describe('Upload New Version', () => {
  it('uploads the PDF as multipart "file", then shows that the earlier approval is no longer current', async () => {
    signIn()
    let release!: () => void
    const pending = new Promise<void>(resolve => { release = resolve })
    const seen = serve(async () => { await pending; return HttpResponse.json(V2, { status: 201 }) })
    const user = userEvent.setup()
    renderAsset()
    expect(within(await screen.findByRole('link', { name: /Review →/ })).getByTestId('approval-state-badge'))
      .toHaveTextContent('Approval current')

    const form = await chooseAndUpload(user)
    // Loading: the request is in flight
    expect(within(form).getByRole('button', { name: 'Uploading…' })).toBeDisabled()
    release()

    const status = await screen.findByTestId('version-upload-status')
    expect(status).toHaveTextContent('Version 2 uploaded and is now the current version (SHA-256 bbbbbbbbbbbbbbbb…).')
    expect(status).toHaveTextContent('The approvals of the earlier version are no longer current')
    expect(status).toHaveTextContent('until version 2 has its own claims and is approved again')
    expect(within(status).getByRole('link', { name: 'View the Passport History →' })).toHaveAttribute(
      'href', expect.stringMatching(/^\/passport\?network=stellar.*&view=history$/))
    expect(seen.authorization).toBe('Bearer fictional-reviewer-token')
    // The backend's format: one multipart field "file"; the browser sets the multipart Content-Type itself
    const [upload] = seen.uploads
    expect(seen.uploads).toHaveLength(1)
    expect([...upload.body.keys()]).toEqual(['file'])
    expect((upload.body.get('file') as File).name).toBe('amendment.pdf')
    expect(Object.keys(upload.headers).map(h => h.toLowerCase())).not.toContain('content-type')

    // The documents and extractions were read again
    expect(seen.documentReads).toBe(2)
    expect(await screen.findByText('v2')).toBeInTheDocument()
    await waitFor(() => expect(within(screen.getByRole('link', { name: /Review →/ })).getByTestId('approval-state-badge'))
      .toHaveTextContent('Superseded version'))
    expect(screen.queryByTestId('new-version-form')).not.toBeInTheDocument()
  })

  it('says so when no current approval was affected', async () => {
    signIn()
    serve(() => HttpResponse.json(V2, { status: 201 }), [])
    const user = userEvent.setup()
    renderAsset()
    await chooseAndUpload(user)
    expect(await screen.findByTestId('version-upload-status')).toHaveTextContent(
      'No current approval of this document was affected; version 2 needs its own claims and approval')
  })

  it.each([
    [403, { detail: 'Insufficient permissions.' }, 'Upload failed: your role may not upload document versions.'],
    [401, { detail: 'Invalid token.' }, 'Upload failed: your credential was not accepted. Check the user token in the sidebar.'],
    [415, { detail: 'File does not appear to be a valid PDF (magic byte mismatch).' },
     'Upload failed: File does not appear to be a valid PDF (magic byte mismatch).'],
    [413, { detail: 'File exceeds the 50 MB limit.' }, 'Upload failed: File exceeds the 50 MB limit.'],
  ])('shows a %i refusal and changes nothing', async (code, body, message) => {
    signIn()
    const seen = serve(() => HttpResponse.json(body, { status: code }))
    const user = userEvent.setup()
    renderAsset()
    const form = await chooseAndUpload(user)
    expect(await within(form).findByTestId('new-version-error')).toHaveTextContent(message)
    expect(screen.queryByTestId('version-upload-status')).not.toBeInTheDocument()
    expect(within(form).getByRole('button', { name: 'Upload Version' })).toBeEnabled()
    expect(seen.documentReads).toBe(1)
  })

  it('reports an unreachable server', async () => {
    signIn()
    serve(() => HttpResponse.error())
    const user = userEvent.setup()
    renderAsset()
    const form = await chooseAndUpload(user)
    expect(await within(form).findByTestId('new-version-error')).toHaveTextContent(
      'Upload failed: the server could not be reached.')
  })

  it('is not offered to a role without document:upload', async () => {
    signIn(MOCK_CURRENT_USER.permissions.filter(p => p !== 'document:upload'))
    serve(() => HttpResponse.json(V2, { status: 201 }))
    renderAsset()
    expect(await screen.findByText('Q4 2024 Prospectus')).toBeInTheDocument()
    await waitFor(() => expect(screen.getByTestId('add-manual-claim')).toBeInTheDocument())  // signed in
    expect(screen.queryByTestId('upload-new-version')).not.toBeInTheDocument()
  })
})
