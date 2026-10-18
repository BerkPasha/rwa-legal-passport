import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { getAssets, ApiError } from '../api'
import type { Asset } from '../types'
import { PageSpinner } from '../components/Spinner'
import { useAuth } from '../AuthContext'

function StatCard({
  label,
  value,
  sub,
  color,
}: {
  label: string
  value: string | number
  sub?: string
  color: string
}) {
  return (
    <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
      <p className="text-sm font-medium text-slate-500">{label}</p>
      <p className={`mt-2 text-3xl font-bold ${color}`}>{value}</p>
      {sub && <p className="mt-1 text-xs text-slate-400">{sub}</p>}
    </div>
  )
}

const FIELD_LABELS: Record<string, string> = {
  ownership_nature: 'Ownership Nature',
  income_rights: 'Income Rights',
  voting_rights: 'Voting Rights',
  redemption_rights: 'Redemption Rights',
  transfer_restrictions: 'Transfer Restrictions',
  jurisdiction: 'Jurisdiction',
  insolvency_provisions: 'Insolvency Provisions',
}

export function Dashboard() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const { activeToken } = useAuth()

  // Reloaded whenever the active credential changes, so the overview always matches it
  useEffect(() => {
    let current = true
    setError('')
    getAssets()
      .then(data => { if (current) setAssets(data) })
      .catch(e => {
        if (!current) return
        setAssets([])
        setError(e instanceof ApiError ? e.message : 'Failed to load')
      })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [activeToken])

  if (loading) return <PageSpinner />

  const active = assets.filter(a => a.status === 'active').length
  const mainnet = assets.filter(a => a.blockchain_network === 'stellar-mainnet').length
  const testnet = assets.filter(a => a.blockchain_network === 'stellar-testnet').length

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h2 className="text-2xl font-bold text-slate-900">Overview</h2>
        <p className="mt-1 text-sm text-slate-500">
          Institutional Legal Transparency Platform for Tokenized Real-World Assets
        </p>
      </div>

      {/* Legal notice */}
      <div className="rounded-xl border border-amber-200 bg-amber-50 p-5">
        <div className="flex gap-3">
          <svg className="mt-0.5 h-5 w-5 shrink-0 text-amber-600" viewBox="0 0 20 20" fill="currentColor">
            <path fillRule="evenodd" d="M8.485 2.495c.673-1.167 2.357-1.167 3.03 0l6.28 10.875c.673 1.167-.17 2.625-1.516 2.625H3.72c-1.347 0-2.189-1.458-1.515-2.625L8.485 2.495zM10 5a.75.75 0 01.75.75v3.5a.75.75 0 01-1.5 0v-3.5A.75.75 0 0110 5zm0 9a1 1 0 100-2 1 1 0 000 2z" clipRule="evenodd" />
          </svg>
          <div>
            <p className="font-semibold text-amber-900">Internal Use Only</p>
            <p className="mt-1 text-sm text-amber-800">
              This platform facilitates internal review of legal information taken from issuer documents,
              entered manually or extracted by AI. All data shown here is for compliance review purposes
              only and does not constitute
              legal advice, regulatory certification, or investment guidance.
              Consult qualified legal counsel before any legal conclusions.
            </p>
          </div>
        </div>
      </div>

      <HowToRead />

      {error && (
        <div role="alert" className="rounded-lg bg-red-50 p-4 text-sm text-red-700 ring-1 ring-red-200">
          {error}
        </div>
      )}

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Registered Assets" value={assets.length} color="text-slate-900" />
        <StatCard label="Active Assets" value={active} sub={`${assets.length - active} inactive`} color="text-green-700" />
        <StatCard label="Mainnet" value={mainnet} color="text-blue-700" />
        <StatCard label="Testnet" value={testnet} color="text-indigo-700" />
      </div>

      {/* Quick actions */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <Link
          to="/assets"
          className="group flex items-center gap-4 rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-200 transition hover:ring-indigo-300"
        >
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-indigo-50 text-indigo-600 group-hover:bg-indigo-100">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
            </svg>
          </div>
          <div>
            <p className="font-semibold text-slate-900">Asset Registry</p>
            <p className="text-sm text-slate-500">Register and manage tokenized assets</p>
          </div>
        </Link>

        <Link
          to="/passport"
          className="group flex items-center gap-4 rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-200 transition hover:ring-indigo-300"
        >
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-green-50 text-green-600 group-hover:bg-green-100">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
            </svg>
          </div>
          <div>
            <p className="font-semibold text-slate-900">Legal Passport</p>
            <p className="text-sm text-slate-500">View approved legal rights</p>
          </div>
        </Link>

        <Link
          to="/api-docs"
          className="group flex items-center gap-4 rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-200 transition hover:ring-indigo-300"
        >
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-slate-50 text-slate-600 group-hover:bg-slate-100">
            <svg className="h-5 w-5" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M17.25 6.75L22.5 12l-5.25 5.25m-10.5 0L1.5 12l5.25-5.25m7.5-3l-4.5 16.5" />
            </svg>
          </div>
          <div>
            <p className="font-semibold text-slate-900">API Reference</p>
            <p className="text-sm text-slate-500">Integration documentation</p>
          </div>
        </Link>
      </div>

      {/* Recent assets */}
      {assets.length > 0 && (
        <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
          <div className="border-b border-slate-100 px-6 py-4">
            <h3 className="font-semibold text-slate-900">Registered Assets</h3>
          </div>
          <ul className="divide-y divide-slate-100">
            {assets.slice(0, 5).map(asset => (
              <li key={asset.id}>
                <Link
                  to={`/assets/${asset.id}`}
                  className="flex items-center justify-between px-6 py-4 hover:bg-slate-50"
                >
                  <div>
                    <p className="font-mono font-semibold text-slate-900">{asset.stellar_asset_code}</p>
                    <p className="text-sm text-slate-500">{asset.issuer_organization_name}</p>
                  </div>
                  <div className="flex items-center gap-3">
                    {/* The asset's registry status — not an approval of its legal information */}
                    <span
                      data-testid="asset-status-badge"
                      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${
                        asset.status === 'active' ? 'bg-green-100 text-green-700 ring-green-200' :
                        asset.status === 'suspended' ? 'bg-amber-100 text-amber-700 ring-amber-200' :
                        'bg-slate-100 text-slate-600 ring-slate-200'
                      }`}
                    >
                      {asset.status}
                    </span>
                    <span className="text-xs text-slate-400">
                      {asset.blockchain_network === 'stellar-mainnet' ? 'Mainnet' : 'Testnet'}
                    </span>
                    <svg className="h-4 w-4 text-slate-400" viewBox="0 0 20 20" fill="currentColor">
                      <path fillRule="evenodd" d="M7.21 14.77a.75.75 0 01.02-1.06L11.168 10 7.23 6.29a.75.75 0 111.04-1.08l4.5 4.25a.75.75 0 010 1.08l-4.5 4.25a.75.75 0 01-1.06-.02z" clipRule="evenodd" />
                    </svg>
                  </div>
                </Link>
              </li>
            ))}
          </ul>
          {assets.length > 5 && (
            <div className="border-t border-slate-100 px-6 py-3">
              <Link to="/assets" className="text-sm font-medium text-indigo-600 hover:text-indigo-800">
                View all {assets.length} assets →
              </Link>
            </div>
          )}
        </div>
      )}

      {/* Legal fields reference */}
      <div className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
        <div className="border-b border-slate-100 px-6 py-4">
          <h3 className="font-semibold text-slate-900">Tracked Legal Fields</h3>
          <p className="mt-1 text-sm text-slate-500">
            AI extracts these 7 fields from uploaded legal documents
          </p>
        </div>
        <div className="grid grid-cols-1 gap-3 p-6 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {Object.entries(FIELD_LABELS).map(([key, label]) => (
            <div key={key} className="rounded-lg bg-slate-50 px-4 py-3">
              <p className="text-sm font-medium text-slate-700">{label}</p>
              <p className="mt-0.5 font-mono text-xs text-slate-400">{key}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

// What an evaluator needs to read the application correctly (Stage 10C-1, GRANT_DEMO.md). Each
// point states what a feature establishes and what it does not.
const HOW_TO_READ: { title: string; text: string }[] = [
  {
    title: 'The problem',
    text: 'A tokenized real-world asset on Stellar carries legal rights that are defined off-chain, in the ' +
      "issuer's documents. Institutions need to see which rights a token carries and where each statement comes from.",
  },
  {
    title: 'A Legal Passport',
    text: "An asset's approved legal claims (ownership, income, voting, redemption, transfer restrictions, " +
      'jurisdiction, insolvency), each with its source document version, cited page and exact quotation, the ' +
      'reviewer approvals behind it, and its Stellar evidence commitment. Institutions read it with an API key.',
  },
  {
    title: 'Page-grounded verification',
    text: 'The server checks that each quotation appears word for word on the cited page of that exact document ' +
      'version. This proves only that the text is there. It does not prove that a claim is legally valid, ' +
      'enforceable, or that anyone owns anything.',
  },
  {
    title: 'Human review',
    text: "Claims start as unapproved drafts. An issuer attestation is the issuer's own statement; an independent " +
      'legal review is given separately by another reviewer. Both are internal attestations bound to the exact ' +
      'document version and evidence: any later change makes them stale. Neither is a legal opinion.',
  },
  {
    title: 'Stellar Testnet anchoring',
    text: "The SHA-256 digest of a passport's evidence manifest can be recorded on Stellar Testnet. It is shown as " +
      'verified only when Stellar Horizon itself returns that transaction, from the trusted account, carrying the ' +
      'same digest. A local record, a pending transaction or an unreachable network is never shown as verified. ' +
      'New assets are not anchored. Testnet only; Mainnet anchoring is not supported.',
  },
  {
    title: 'Experimental',
    text: 'AI extraction is experimental: it can be disabled on a server and has not been verified against a real ' +
      'model. Claims can be entered manually from the stored page text instead. This is not a production service.',
  },
]

function HowToRead() {
  return (
    <section aria-labelledby="how-to-read-heading" data-testid="how-to-read"
             className="rounded-xl bg-white p-5 shadow-sm ring-1 ring-slate-200">
      <h3 id="how-to-read-heading" className="font-semibold text-slate-900">How to read this application</h3>
      <dl className="mt-3 grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
        {HOW_TO_READ.map(item => (
          <div key={item.title}>
            <dt className="text-sm font-medium text-slate-800">{item.title}</dt>
            <dd className="mt-1 text-xs leading-relaxed text-slate-600">{item.text}</dd>
          </div>
        ))}
      </dl>
    </section>
  )
}
