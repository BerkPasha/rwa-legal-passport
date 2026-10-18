import '@testing-library/jest-dom'
import { afterAll, afterEach, beforeAll } from 'vitest'
import { server } from './mocks/server'
import { resetServerInfo } from '../serverInfo'

beforeAll(() => server.listen({ onUnhandledRequest: 'warn' }))
afterEach(() => {
  server.resetHandlers()
  resetServerInfo()   // the server's policies are read again by the next test
})
afterAll(() => server.close())
