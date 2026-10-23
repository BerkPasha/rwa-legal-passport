import { useState } from 'react'
import { Link } from 'react-router-dom'
import { DEMO_RECORDS } from './data'
import { ASSET_TYPE_LABELS, LABEL, LINK, Mark, PageHeader, StatusText, anchorSummary, approvalSummary, paths, shortKey } from './ui'

// The asset registry of the demo (pages/AssetList.tsx): the bundled records, with a search and a filter.
// Registering an asset is a product action and is not offered here.

type Filter = 'all' | 'current' | 'attention'

const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'current', label: 'Approval current' },
  { id: 'attention', label: 'Needs review' },
]

export function AssetRegistry() {
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<Filter>('all')

  const q = query.trim().toLowerCase()
  const shown = DEMO_RECORDS.filter(record => {
    const { asset } = record
    const matches = !q || [asset.stellar_asset_code, asset.issuer_organization_name, asset.jurisdiction]
      .some(text => text.toLowerCase().includes(q))
    const current = approvalSummary(record).text === 'Approval current'
    return matches && (filter === 'all' || (filter === 'current') === current)
  })

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader label="Registry" title="Asset Registry"
        lead={`${DEMO_RECORDS.length} tokenized assets registered, each identified by its Stellar asset code and issuer key. Open an asset to read its documents, claims, approvals and evidence.`} />

      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor="registry-search" className="sr-only">Search assets</label>
        <input
          id="registry-search"
          type="search"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search by code, issuer or jurisdiction"
          autoComplete="off"
          className="w-full max-w-sm border border-paper-line bg-white px-3 py-2 text-[14px] text-graphite placeholder:text-paper-muted focus:border-accent focus:outline-none"
        />
        <div role="group" aria-label="Filter by approval state" className="flex border border-paper-line bg-white">
          {FILTERS.map(f => (
            <button key={f.id} type="button" aria-pressed={filter === f.id} onClick={() => setFilter(f.id)}
                    className={`px-3 py-2 text-[13px] transition-colors ${filter === f.id ? 'bg-graphite text-mist' : 'text-paper-muted hover:text-graphite'}`}>
              {f.label}
            </button>
          ))}
        </div>
        <p className="text-[12.5px] text-paper-muted sm:ml-auto">Registering assets is not available in the read-only demo.</p>
      </div>

      <div className="overflow-x-auto border border-paper-line bg-white">
        <table className="w-full min-w-[860px] text-left">
          <thead>
            <tr className={`${LABEL} text-paper-muted`}>
              <th scope="col" className="px-5 py-3 font-normal">Asset</th>
              <th scope="col" className="px-3 py-3 font-normal">Type</th>
              <th scope="col" className="px-3 py-3 font-normal">Jurisdiction</th>
              <th scope="col" className="px-3 py-3 font-normal">Status</th>
              <th scope="col" className="px-3 py-3 font-normal">Legal record</th>
              <th scope="col" className="px-3 py-3 font-normal">Anchor (recorded)</th>
              <th scope="col" className="px-5 py-3 font-normal"><span className="sr-only">Open</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-paper-line border-t border-paper-line">
            {shown.map(record => {
              const { asset } = record
              const approvals = approvalSummary(record)
              const anchor = anchorSummary(record)
              return (
                <tr key={asset.id} data-testid="registry-row" className="align-top transition-colors hover:bg-paper/60">
                  <td className="px-5 py-4">
                    <p className="font-label text-[13px] font-semibold text-graphite">{asset.stellar_asset_code}</p>
                    <p className="mt-1 text-[13px] text-paper-muted">{asset.issuer_organization_name}</p>
                    <p className="mt-0.5 font-label text-[11.5px] text-paper-muted/80" title={asset.stellar_issuer_public_key}>
                      Testnet · {shortKey(asset.stellar_issuer_public_key)}
                    </p>
                  </td>
                  <td className="px-3 py-4 text-[13px] text-graphite">{ASSET_TYPE_LABELS[asset.asset_type] ?? asset.asset_type}</td>
                  <td className="px-3 py-4 font-label text-[13px] text-graphite">{asset.jurisdiction}</td>
                  <td className="px-3 py-4"><StatusText status={asset.status} /></td>
                  <td className="px-3 py-4">
                    <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={approvals.tone} />{approvals.text}</span>
                  </td>
                  <td className="px-3 py-4">
                    <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={anchor.tone} />{anchor.text}</span>
                  </td>
                  <td className="px-5 py-4 text-right">
                    <Link to={paths.asset(asset.id)} className={`text-[13px] ${LINK}`}
                          aria-label={`Open ${asset.stellar_asset_code}`}>View →</Link>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {shown.length === 0 && (
          <p className="border-t border-paper-line px-5 py-8 text-[14px] text-paper-muted">No asset matches this search.</p>
        )}
      </div>

      <ul className="grid gap-4 md:grid-cols-3">
        {DEMO_RECORDS.map(record => (
          <li key={record.asset.id} className="border-t border-paper-line pt-4">
            <p className="font-label text-[12px] font-semibold text-graphite">{record.asset.stellar_asset_code}</p>
            <p className="mt-1.5 text-[13px] leading-relaxed text-paper-muted">{record.synopsis}</p>
          </li>
        ))}
      </ul>
    </div>
  )
}
