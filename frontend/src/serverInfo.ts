import { useEffect, useState } from 'react'
import { getServerInfo } from './api'
import type { ServerInfo } from './types'

// The server's deployment policies (Stage 10C-1, GRANT_DEMO.md), read once from the public
// /health route. They only label the interface and hide actions that the server would refuse:
// the server enforces read-only mode itself, whatever the interface shows.
const UNKNOWN: ServerInfo = { read_only: false, synthetic_data: false, api_docs: true }

let pending: Promise<ServerInfo> | null = null

export function loadServerInfo(): Promise<ServerInfo> {
  pending ??= getServerInfo().catch(() => UNKNOWN)
  return pending
}

// For tests: the next read asks the server again
export function resetServerInfo(): void {
  pending = null
}

// null until the server has answered
export function useServerInfo(): ServerInfo | null {
  const [info, setInfo] = useState<ServerInfo | null>(null)
  useEffect(() => {
    let cancelled = false
    void loadServerInfo().then(value => { if (!cancelled) setInfo(value) })
    return () => { cancelled = true }
  }, [])
  return info
}

// True only once the server has said so; actions stay visible while unknown (the server decides)
export function useReadOnly(): boolean {
  return useServerInfo()?.read_only === true
}
