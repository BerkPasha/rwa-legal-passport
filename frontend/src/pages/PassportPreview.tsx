import { useState } from 'react'
import { getLegalPassport, ApiError } from '../api'
import type { LegalPassport, BlockchainNetwork } from '../types'
import { ClaimStatusBadge, ApprovalTypeBadge } from '../components/StatusBadge'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { Spinner } from '../components/Spinner'
import { useAuth } from '../AuthContext'

const FIELD_LABELS: Record<string, string> = {
  ownership_nature: 'Ownership Nature',
  income_rights: 'Income Rights',
  voting_rights: 'Voting Rights',
  redemption_rights: 'Redemption Rights',
  transfer_restrictions: 'Transfer Restrictions',
  jurisdiction: 'Jurisdiction',
  insolvency_provisions: 'Insolvency Provisions',
}

function PassportCard({ passport }: { passport: LegalPassport }) {
  return (
    <div className="space-y-6" data-testid="passport-card">
      <DisclaimerBanner variant="legal" text={passport.disclaimer} />

      {/* Asset summary */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-900">
                Legal Passport — v{passport.passport_version}
              </h3>
              <p className="mt-1 text-sm text-slate-500">
                Asset: <span className="font-mono font-medium text-slate-800">{passport.asset.stellar_asset_code}</span>
                {' · '}
                {passport.asset.issuer_organization_name}
              </p>
            </div>
            <div className="text-right">
              <p className="text-xs text-slate-400">Effective Date</p>
              <p className="font-mono text-sm font-medium text-slate-800">
                {new Date(passport.effective_date).toLocaleDateString('en-US', {
                  year: 'numeric', month: 'short', day: 'numeric',
                })}
              </p>
            </div>
          </div>
        </div>
        <div className="grid grid-cols-2 gap-0 divide-x divide-y divide-slate-100 sm:grid-cols-4">
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Network</p>
            <p className="mt-1 text-sm font-medium text-slate-800">
              {passport.asset.blockchain_network === 'stellar-mainnet' ? 'Stellar Mainnet' : 'Stellar Testnet'}
            </p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Asset Type</p>
            <p className="mt-1 text-sm font-medium capitalize text-slate-800">
              {passport.asset.asset_type.replace('_', ' ')}
            </p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Jurisdiction</p>
            <p className="mt-1 font-mono text-sm font-medium text-slate-800">{passport.asset.jurisdiction}</p>
          </div>
          <div className="px-6 py-4">
            <p className="text-xs text-slate-400">Passport Version</p>
            <p className="mt-1 text-sm font-medium text-slate-800">v{passport.passport_version}</p>
          </div>
        </div>
      </div>

      {/* Legal rights */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extracted Legal Rights</h3>
          <p className="mt-1 text-sm text-slate-500">
            {passport.legal_rights.length} field{passport.legal_rights.length !== 1 ? 's' : ''} extracted from approved legal documents
          </p>
        </div>
        {passport.legal_rights.length === 0 ? (
          <p className="px-6 py-8 text-center text-sm text-slate-500">No legal rights data available.</p>
        ) : (
          <div className="divide-y divide-slate-100">
            {passport.legal_rights.map((right, i) => (
              <div key={i} className="px-6 py-4">
                <div className="flex items-start justify-between gap-4">
                  <div className="min-w-0 flex-1">
                    <p className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                      {FIELD_LABELS[right.field_name] ?? right.field_name}
                    </p>
                    <p className="mt-1 text-sm text-slate-800">{right.value}</p>
                    {right.supporting_passage && (
                      <details className="mt-2">
                        <summary className="cursor-pointer text-xs text-indigo-600 hover:text-indigo-800">
                          View source passage
                        </summary>
                        <blockquote className="mt-2 border-l-2 border-indigo-200 pl-3 text-xs italic text-slate-600">
                          {right.supporting_passage}
                        </blockquote>
                      </details>
                    )}
                  </div>
                  <ClaimStatusBadge
                    isUnknown={right.is_unknown}
                    verified={right.verified ?? false}
                  />
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Approvals */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Approval Chain</h3>
        </div>
        {passport.approvals.length === 0 ? (
          <p className="px-6 py-8 text-center text-sm text-slate-500">No approvals on record.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {passport.approvals.map((ap, i) => (
              <li key={i} className="flex items-center justify-between px-6 py-4">
                <div>
                  <p className="text-sm font-medium text-slate-800">{ap.reviewer_id}</p>
                  <p className="text-xs text-slate-400">
                    {new Date(ap.approved_at).toLocaleString()}
                  </p>
                </div>
                <ApprovalTypeBadge type={ap.approval_type} />
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Extraction metadata */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Extraction Metadata</h3>
        </div>
        <div className="px-6 py-4">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-slate-400">Extraction ID</dt>
              <dd className="mt-1 font-mono text-xs text-slate-800">{passport.extraction.id}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Model Used</dt>
              <dd className="mt-1 font-mono text-xs text-slate-800">{passport.extraction.ai_model_used ?? '—'}</dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Extracted At</dt>
              <dd className="mt-1 text-xs text-slate-800">
                {new Date(passport.extraction.extracted_at).toLocaleString()}
              </dd>
            </div>
            {passport.extraction.document && (
              <>
                <div>
                  <dt className="text-xs text-slate-400">Source Document</dt>
                  <dd className="mt-1 text-xs text-slate-800">{passport.extraction.document.filename}</dd>
                </div>
                <div>
                  <dt className="text-xs text-slate-400">Document Type</dt>
                  <dd className="mt-1 text-xs capitalize text-slate-800">
                    {passport.extraction.document.document_type.replace('_', ' ')}
                  </dd>
                </div>
              </>
            )}
          </dl>
        </div>
      </div>
    </div>
  )
}

export function PassportPreview() {
  const { apiKey } = useAuth()
  const [network, setNetwork] = useState<BlockchainNetwork>('stellar-testnet')
  const [assetCode, setAssetCode] = useState('')
  const [issuerKey, setIssuerKey] = useState('')
  const [keyOverride, setKeyOverride] = useState('')
  const [passport, setPassport] = useState<LegalPassport | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const effectiveKey = keyOverride.trim() || apiKey

  const handleLookup = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!effectiveKey) {
      setError('An institutional API key is required. Enter one below or set it via Credentials in the sidebar.')
      return
    }
    setLoading(true)
    setError('')
    setPassport(null)
    try {
      const result = await getLegalPassport(network, assetCode.trim().toUpperCase(), issuerKey.trim(), effectiveKey)
      setPassport(result)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(`${err.status}: ${err.message}`)
      } else {
        setError('Lookup failed — check network and credentials.')
      }
    } finally {
      setLoading(false)
    }
  }

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
              <label className="mb-1 block text-sm font-medium text-slate-700">Network</label>
              <select
                value={network}
                onChange={e => setNetwork(e.target.value as BlockchainNetwork)}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="stellar-testnet">Stellar Testnet</option>
                <option value="stellar-mainnet">Stellar Mainnet</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Asset Code</label>
              <input
                required
                value={assetCode}
                onChange={e => setAssetCode(e.target.value.toUpperCase())}
                placeholder="MRPROP"
                maxLength={12}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Issuer Public Key</label>
              <input
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
            <label className="mb-1 block text-sm font-medium text-slate-700">
              Institutional API Key
              {apiKey && <span className="ml-2 text-xs font-normal text-green-600">(set via Credentials)</span>}
            </label>
            <input
              type="password"
              value={keyOverride}
              onChange={e => setKeyOverride(e.target.value)}
              placeholder={apiKey ? '(using key from Credentials panel)' : 'X-API-Key…'}
              className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
            />
            <p className="mt-1 text-xs text-slate-400">
              Leave blank to use the key set in the sidebar Credentials panel.
            </p>
          </div>

          {error && (
            <div className="rounded-lg bg-red-50 p-3 text-sm text-red-700 ring-1 ring-red-200">
              {error}
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
    </div>
  )
}
