import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { createAsset, getAssets, ApiError } from '../api'
import type { Asset, AssetType, BlockchainNetwork, CreateAssetPayload } from '../types'
import { PageSpinner } from '../components/Spinner'

const EMPTY: CreateAssetPayload = {
  issuer_id: '',
  blockchain_network: 'stellar-testnet',
  stellar_asset_code: '',
  stellar_issuer_public_key: '',
  issuer_organization_name: '',
  asset_type: 'real_estate',
  jurisdiction: '',
}

const ASSET_TYPE_LABELS: Record<AssetType, string> = {
  real_estate: 'Real Estate',
  commodity: 'Commodity',
  equity: 'Equity',
  bond: 'Bond',
  fund: 'Fund',
  other: 'Other',
}

const STATUS_PILL: Record<string, string> = {
  active: 'bg-green-100 text-green-700',
  suspended: 'bg-amber-100 text-amber-700',
  retired: 'bg-slate-100 text-slate-500',
}

export function AssetList() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState<CreateAssetPayload>(EMPTY)
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState('')

  const load = () => {
    setLoading(true)
    getAssets()
      .then(setAssets)
      .catch(e => setError(e instanceof ApiError ? e.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }

  useEffect(load, [])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setSubmitting(true)
    setFormError('')
    try {
      await createAsset(form)
      setForm(EMPTY)
      setShowForm(false)
      load()
    } catch (err) {
      setFormError(err instanceof ApiError ? err.message : 'Creation failed')
    } finally {
      setSubmitting(false)
    }
  }

  if (loading) return <PageSpinner />

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-2xl font-bold text-slate-900">Asset Registry</h2>
          <p className="mt-1 text-sm text-slate-500">
            {assets.length} tokenized asset{assets.length !== 1 ? 's' : ''} registered
          </p>
        </div>
        <button
          onClick={() => setShowForm(s => !s)}
          className="flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 active:bg-indigo-800"
        >
          <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
            <path d="M10.75 4.75a.75.75 0 00-1.5 0v4.5h-4.5a.75.75 0 000 1.5h4.5v4.5a.75.75 0 001.5 0v-4.5h4.5a.75.75 0 000-1.5h-4.5v-4.5z" />
          </svg>
          Register Asset
        </button>
      </div>

      {error && (
        <div className="rounded-lg bg-red-50 p-4 text-sm text-red-700 ring-1 ring-red-200">{error}</div>
      )}

      {/* Create form */}
      {showForm && (
        <div className="rounded-xl bg-white p-6 shadow-sm ring-1 ring-slate-200">
          <h3 className="mb-5 font-semibold text-slate-900">Register New Tokenized Asset</h3>
          <form onSubmit={handleSubmit} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <label htmlFor="issuer_id" className="mb-1 block text-sm font-medium text-slate-700">Issuer ID</label>
              <input
                id="issuer_id"
                required
                value={form.issuer_id}
                onChange={e => setForm(f => ({ ...f, issuer_id: e.target.value.trim().toLowerCase() }))}
                placeholder="issuer-id (e.g. acme-holdings)"
                maxLength={64}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
              <p className="mt-1 text-xs text-slate-500">
                Owning issuer from the access-control registry. Determines who can see this asset; cannot be changed later.
              </p>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Network</label>
              <select
                value={form.blockchain_network}
                onChange={e => setForm(f => ({ ...f, blockchain_network: e.target.value as BlockchainNetwork }))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                <option value="stellar-testnet">Stellar Testnet</option>
                <option value="stellar-mainnet">Stellar Mainnet</option>
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Asset Type</label>
              <select
                value={form.asset_type}
                onChange={e => setForm(f => ({ ...f, asset_type: e.target.value as AssetType }))}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              >
                {Object.entries(ASSET_TYPE_LABELS).map(([v, l]) => (
                  <option key={v} value={v}>{l}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Stellar Asset Code</label>
              <input
                required
                value={form.stellar_asset_code}
                onChange={e => setForm(f => ({ ...f, stellar_asset_code: e.target.value.toUpperCase() }))}
                placeholder="MRPROP"
                maxLength={12}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Issuer Public Key</label>
              <input
                required
                value={form.stellar_issuer_public_key}
                onChange={e => setForm(f => ({ ...f, stellar_issuer_public_key: e.target.value }))}
                placeholder="G…"
                maxLength={56}
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 font-mono text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Issuer Organization</label>
              <input
                required
                value={form.issuer_organization_name}
                onChange={e => setForm(f => ({ ...f, issuer_organization_name: e.target.value }))}
                placeholder="Meridian Property Holdings LLC"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            <div>
              <label className="mb-1 block text-sm font-medium text-slate-700">Jurisdiction</label>
              <input
                required
                value={form.jurisdiction}
                onChange={e => setForm(f => ({ ...f, jurisdiction: e.target.value }))}
                placeholder="US-NY"
                className="w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm focus:border-indigo-500 focus:outline-none focus:ring-1 focus:ring-indigo-500"
              />
            </div>
            {formError && (
              <p className="col-span-2 text-sm text-red-600">{formError}</p>
            )}
            <div className="col-span-2 flex justify-end gap-3">
              <button
                type="button"
                onClick={() => setShowForm(false)}
                className="rounded-lg px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={submitting}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm hover:bg-indigo-700 disabled:opacity-60"
              >
                {submitting ? 'Registering…' : 'Register Asset'}
              </button>
            </div>
          </form>
        </div>
      )}

      {/* Asset table */}
      {assets.length === 0 ? (
        <div className="rounded-xl bg-white py-16 text-center shadow-sm ring-1 ring-slate-200">
          <svg className="mx-auto h-12 w-12 text-slate-300" fill="none" viewBox="0 0 24 24" strokeWidth={1} stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3.75h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
          </svg>
          <p className="mt-4 font-semibold text-slate-700">No assets registered</p>
          <p className="mt-1 text-sm text-slate-500">Register your first tokenized asset above.</p>
        </div>
      ) : (
        <div className="overflow-hidden rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
          <table className="min-w-full divide-y divide-slate-200">
            <thead>
              <tr className="bg-slate-50">
                <th className="px-6 py-3.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Asset</th>
                <th className="px-6 py-3.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Network</th>
                <th className="px-6 py-3.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Type</th>
                <th className="px-6 py-3.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Jurisdiction</th>
                <th className="px-6 py-3.5 text-left text-xs font-semibold uppercase tracking-wider text-slate-500">Status</th>
                <th className="relative px-6 py-3.5"><span className="sr-only">Actions</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {assets.map(asset => (
                <tr key={asset.id} className="hover:bg-slate-50">
                  <td className="px-6 py-4">
                    <p className="font-mono font-semibold text-slate-900">{asset.stellar_asset_code}</p>
                    <p className="mt-0.5 text-sm text-slate-500">{asset.issuer_organization_name}</p>
                  </td>
                  <td className="px-6 py-4">
                    <span className="text-sm text-slate-700">
                      {asset.blockchain_network === 'stellar-mainnet' ? 'Mainnet' : 'Testnet'}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className="text-sm text-slate-700">
                      {ASSET_TYPE_LABELS[asset.asset_type] ?? asset.asset_type}
                    </span>
                  </td>
                  <td className="px-6 py-4">
                    <span className="font-mono text-sm text-slate-700">{asset.jurisdiction}</span>
                  </td>
                  <td className="px-6 py-4">
                    <span className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium capitalize ring-1 ring-inset ${STATUS_PILL[asset.status] ?? 'bg-slate-100 text-slate-600 ring-slate-200'}`}>
                      {asset.status}
                    </span>
                  </td>
                  <td className="px-6 py-4 text-right">
                    <Link
                      to={`/assets/${asset.id}`}
                      className="text-sm font-medium text-indigo-600 hover:text-indigo-800"
                    >
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
