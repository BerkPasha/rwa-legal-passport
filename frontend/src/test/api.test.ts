import { afterEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from './mocks/server'
import { MOCK_ASSET, MOCK_PASSPORT } from './mocks/handlers'
import {
  ApiError,
  approveExtraction,
  getAssets,
  getLegalPassport,
  setUserToken,
} from '../api'

// FICTIONAL, NON-SECRET test credentials: requests are answered by MSW.
const BASE = 'http://localhost:8000'

function captureHeaders(method: 'get' | 'post', path: string, body: unknown) {
  const seen: Headers[] = []
  server.use(
    http[method](`${BASE}${path}`, ({ request }) => {
      seen.push(request.headers)
      return HttpResponse.json(body as object)
    }),
  )
  return seen
}

afterEach(() => setUserToken(''))

describe('api credentials', () => {
  it('attaches the user Bearer token to internal requests', async () => {
    const seen = captureHeaders('get', '/assets', [MOCK_ASSET])
    setUserToken('user-token-123')
    await getAssets()
    expect(seen[0].get('Authorization')).toBe('Bearer user-token-123')
  })

  it('sends no Authorization header when no token is set', async () => {
    const seen = captureHeaders('get', '/assets', [MOCK_ASSET])
    await getAssets()
    expect(seen[0].get('Authorization')).toBeNull()
  })

  it('never sends the user token to the institutional passport endpoint', async () => {
    const seen = captureHeaders('get', '/passport/:network/:code/:key', MOCK_PASSPORT)
    setUserToken('user-token-123')
    await getLegalPassport('stellar-mainnet', 'MRPROP', 'GABC', 'inst-key-xyz')
    expect(seen[0].get('Authorization')).toBeNull()
    expect(seen[0].get('X-API-Key')).toBe('inst-key-xyz')
  })

  it('uses the explicitly passed token for review calls', async () => {
    const seen = captureHeaders('post', '/extractions/:id/approvals', {})
    setUserToken('global-token')
    await approveExtraction('ext-001', {}, 'explicit-token')
    expect(seen[0].get('Authorization')).toBe('Bearer explicit-token')
  })

  it('surfaces 401/403 as ApiError with the status code', async () => {
    server.use(
      http.get(`${BASE}/assets`, () =>
        HttpResponse.json({ detail: 'Not authenticated' }, { status: 403 }),
      ),
    )
    await expect(getAssets()).rejects.toMatchObject({ status: 403 })
    await expect(getAssets()).rejects.toBeInstanceOf(ApiError)
  })
})
