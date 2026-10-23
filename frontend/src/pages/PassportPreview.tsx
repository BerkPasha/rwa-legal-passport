import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { getLegalPassport, getPassportAnchorVerification, getPassportHistory, ApiError } from '../api'
import type { AnchorVerification, LegalPassport, BlockchainNetwork, PassportHistory } from '../types'
import { EvidenceAnchorCard } from '../components/EvidenceAnchorCard'
import { PassportCard } from '../components/PassportCard'
import { PassportHistoryCard } from '../components/PassportHistoryCard'
import { Spinner } from '../components/Spinner'
import { useAuth } from '../AuthContext'

export function PassportPreview() {
  const { apiKey } = useAuth()
  // Prefilled from a link such as /passport?network=…&asset_code=…&issuer_key=… (asset page)
  const [params] = useSearchParams()
  const [network, setNetwork] = useState<BlockchainNetwork>(
    params.get('network') === 'stellar-mainnet' ? 'stellar-mainnet' : 'stellar-testnet')
  const [assetCode, setAssetCode] = useState((params.get('asset_code') ?? '').toUpperCase())
  const [issuerKey, setIssuerKey] = useState(params.get('issuer_key') ?? '')
  const [keyOverride, setKeyOverride] = useState('')
  const [passport, setPassport] = useState<LegalPassport | null>(null)
  const [anchor, setAnchor] = useState<AnchorVerification | null>(null)
  const [anchorError, setAnchorError] = useState('')
  const [history, setHistory] = useState<PassportHistory | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  // 409: approvals exist, but none is current any more (e.g. a new document version awaits approval)
  const [notCurrent, setNotCurrent] = useState(false)

  const effectiveKey = keyOverride.trim() || apiKey

  const lookup = async () => {
    if (!effectiveKey) {
      setError('An institutional API key is required. Enter one below or set it via Credentials in the sidebar.')
      return
    }
    setLoading(true)
    setError('')
    setNotCurrent(false)
    setPassport(null)
    setAnchor(null)
    setAnchorError('')
    setHistory(null)
    setHistoryError('')
    const code = assetCode.trim().toUpperCase()
    const issuer = issuerKey.trim()
    // The passport, its Stellar evidence commitment and its history are read independently: the
    // commitment and the history are shown even when no approved passport is current
    const [passportResult, anchorResult, historyResult] = await Promise.allSettled([
      getLegalPassport(network, code, issuer, effectiveKey),
      getPassportAnchorVerification(network, code, issuer, effectiveKey),
      getPassportHistory(network, code, issuer, effectiveKey),
    ])
    if (passportResult.status === 'fulfilled') {
      setPassport(passportResult.value)
    } else if (passportResult.reason instanceof ApiError) {
      setError(`${passportResult.reason.status}: ${passportResult.reason.message}`)
      setNotCurrent(passportResult.reason.status === 409)
    } else {
      setError('Lookup failed — check network and credentials.')
    }
    if (historyResult.status === 'fulfilled') {
      setHistory(historyResult.value)
    } else if (!(historyResult.reason instanceof ApiError && historyResult.reason.status === 404)) {
      setHistoryError(historyResult.reason instanceof ApiError
        ? `Passport history unavailable — ${historyResult.reason.status}: ${historyResult.reason.message}`
        : 'Passport history unavailable — the server could not be reached.')
    }
    if (anchorResult.status === 'fulfilled') {
      setAnchor(anchorResult.value)
    } else if (!(anchorResult.reason instanceof ApiError && anchorResult.reason.status === 404)) {
      setAnchorError(anchorResult.reason instanceof ApiError
        ? `Stellar commitment unavailable — ${anchorResult.reason.status}: ${anchorResult.reason.message}`
        : 'Stellar commitment unavailable — the server could not be reached.')
    }
    setLoading(false)
  }

  const handleLookup = (e: React.FormEvent) => {
    e.preventDefault()
    void lookup()
  }

  // Opened from an asset page's "View Passport History" link: the lookup runs once by itself
  const autoRun = useRef(params.get('view') === 'history')
  useEffect(() => {
    if (!autoRun.current || !assetCode || !issuerKey) return
    autoRun.current = false
    void lookup()
  }, [])

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-2xl font-bold text-slate-900">Legal Passport Lookup</h2>
        <p className="mt-1 text-sm text-slate-500">
          Query the institutional transparency endpoint for a tokenized asset.
        </p>
      </div>

      {/* Lookup form */}
      <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
        <form onSubmit={handleLookup} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <div>
              <label htmlFor="passport-network" className="mb-1 block text-sm font-medium text-slate-700">Network</label>
              <select
                id="passport-network"
                value={network}
                onChange={e => setNetwork(e.target.value as BlockchainNetwork)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="stellar-testnet">Stellar Testnet</option>
                <option value="stellar-mainnet">Stellar Mainnet</option>
              </select>
            </div>
            <div>
              <label htmlFor="passport-asset-code" className="mb-1 block text-sm font-medium text-slate-700">Asset Code</label>
              <input
                id="passport-asset-code"
                required
                value={assetCode}
                onChange={e => setAssetCode(e.target.value.toUpperCase())}
                placeholder="MRPROP"
                maxLength={12}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label htmlFor="passport-issuer-key" className="mb-1 block text-sm font-medium text-slate-700">Issuer Public Key</label>
              <input
                id="passport-issuer-key"
                required
                value={issuerKey}
                onChange={e => setIssuerKey(e.target.value)}
                placeholder="G…"
                maxLength={56}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
          </div>

          <div>
            <label htmlFor="passport-api-key" className="mb-1 block text-sm font-medium text-slate-700">
              Institutional API Key for this lookup
              {apiKey && <span className="ml-2 text-xs font-normal text-green-600">(set via Credentials)</span>}
            </label>
            <input
              id="passport-api-key"
              type="password"
              aria-describedby="passport-api-key-note"
              value={keyOverride}
              onChange={e => setKeyOverride(e.target.value)}
              placeholder={apiKey ? '(using key from Credentials panel)' : 'X-API-Key…'}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <p id="passport-api-key-note" className="mt-1 text-xs text-slate-400">
              Leave blank to use the key set in the sidebar Credentials panel.
            </p>
          </div>

          {error && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 ring-1 ring-red-200">
              <p>{error}</p>
              {notCurrent && (
                <p data-testid="passport-not-current-note" className="mt-1">
                  No passport is served until the changed documents are reviewed and approved again.
                  {history ? ' Passport History below shows which version changed and what requires reapproval.' : ''}
                </p>
              )}
            </div>
          )}

          <div className="flex justify-end">
            <button
              type="submit"
              disabled={loading}
              className="flex items-center gap-2 rounded-lg bg-indigo-600 px-5 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:opacity-60"
            >
              {loading ? <Spinner className="h-4 w-4" /> : null}
              {loading ? 'Looking up…' : 'Fetch Legal Passport'}
            </button>
          </div>
        </form>
      </div>

      {/* Result */}
      {passport && <PassportCard passport={passport} />}
      {anchor && <EvidenceAnchorCard verification={anchor} />}
      {anchorError && (
        <div data-testid="anchor-lookup-error" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200">
          {anchorError}
        </div>
      )}
      {history && <PassportHistoryCard history={history} />}
      {historyError && (
        <div data-testid="history-lookup-error" className="rounded-lg bg-amber-50 p-3 text-sm text-amber-800 ring-1 ring-amber-200">
          {historyError}
        </div>
      )}
    </div>
  )
}
