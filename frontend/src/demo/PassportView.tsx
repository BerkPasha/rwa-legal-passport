import { useRef, type KeyboardEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { PassportCard } from '../components/PassportCard'
import { PassportHistoryCard } from '../components/PassportHistoryCard'
import { STALE_REASON_LABELS } from '../components/StatusBadge'
import { DEMO_RECORDS, recordByCode, type DemoRecord } from './data'
import { StellarEvidence } from './StellarEvidence'
import { LABEL, LINK, Mark, PageHeader, Tabs, TabPanel, approvalSummary, fmtDateTime, paths, shortKey, type PassportTab } from './ui'

// The institutional view of the demo (pages/PassportPreview.tsx): what the read-only Passport API returns
// for an asset, shown from the bundled snapshot. No request is sent and no API key is asked for.

const TABS: { id: PassportTab; label: string }[] = [
  { id: 'passport', label: 'Legal Passport' },
  { id: 'history', label: 'Passport History' },
  { id: 'anchor', label: 'Stellar Evidence' },
]

function AssetSwitcher({ selected, onSelect }: { selected: DemoRecord; onSelect: (code: string) => void }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const step = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const next = (index + step + DEMO_RECORDS.length) % DEMO_RECORDS.length
    onSelect(DEMO_RECORDS[next].asset.stellar_asset_code)
    refs.current[next]?.focus()
  }
  return (
    <div role="radiogroup" aria-label="Asset" className="grid gap-px border border-paper-line bg-paper-line sm:grid-cols-3">
      {DEMO_RECORDS.map((record, i) => {
        const checked = record === selected
        const summary = approvalSummary(record)
        return (
          <button key={record.asset.id} ref={el => { refs.current[i] = el }} type="button" role="radio" aria-checked={checked}
                  tabIndex={checked ? 0 : -1} onClick={() => onSelect(record.asset.stellar_asset_code)} onKeyDown={e => onKeyDown(e, i)}
                  className={`px-5 py-4 text-left transition-colors ${checked ? 'bg-graphite text-mist' : 'bg-white text-graphite hover:bg-paper/60'}`}>
            <span className="flex items-center justify-between gap-3">
              <span className="font-label text-[13px] font-semibold">{record.asset.stellar_asset_code}</span>
              <span className={`inline-flex items-center gap-2 text-[12px] ${checked ? 'text-ink-soft' : 'text-paper-muted'}`}>
                <Mark tone={summary.tone} />{record.passport ? 'Passport current' : 'No current passport'}
              </span>
            </span>
            <span className={`mt-1 block text-[13px] ${checked ? 'text-ink-soft' : 'text-paper-muted'}`}>{record.asset.issuer_organization_name}</span>
          </button>
        )
      })}
    </div>
  )
}

function NoPassport({ record }: { record: DemoRecord }) {
  const lapsed = record.history.documents.flatMap(d => d.approved_versions).find(v => v.approval_state !== 'current')
  return (
    <div data-testid="demo-no-passport" className="border border-rose-200 bg-rose-50 px-5 py-4 text-[13.5px] text-rose-900">
      <p className="font-semibold">No current Legal Passport for {record.asset.stellar_asset_code}</p>
      {lapsed ? (
        <p className="mt-1 leading-relaxed">
          Its earlier approvals are no longer current ({lapsed.stale_reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ').toLowerCase()}).
          No passport is served until the changed documents are reviewed and approved again; nothing from the superseded
          version is presented as approved.
        </p>
      ) : (
        <p className="mt-1 leading-relaxed">
          No document has been approved yet. Nothing is served to institutions until the claims are reviewed and approved.
        </p>
      )}
      <p className="mt-2 flex flex-wrap gap-x-5 gap-y-1">
        <Link to={paths.passportOf(record.asset.stellar_asset_code, 'history')} className="font-medium underline-offset-2 hover:underline">
          Passport History →
        </Link>
        <Link to={paths.asset(record.asset.id, 'changes')} className="font-medium underline-offset-2 hover:underline">
          What changed →
        </Link>
      </p>
    </div>
  )
}

export function PassportView() {
  const [params, setParams] = useSearchParams()
  const record = recordByCode(params.get('asset')) ?? DEMO_RECORDS[0]
  const requested = params.get('tab') as PassportTab | null
  const tab: PassportTab = TABS.some(t => t.id === requested) ? requested! : 'passport'
  const { asset } = record
  const show = (code: string, next: PassportTab) =>
    setParams(next === 'passport' ? { asset: code } : { asset: code, tab: next }, { replace: true })

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader label="Institutional view" title="Legal Passport"
        lead="What an institution reads through the scoped, read-only Passport API: the currently approved claims of an asset, the approvals behind them, every earlier version and approval, and the Stellar Testnet commitment of the evidence." />

      <AssetSwitcher selected={record} onSelect={code => show(code, tab)} />

      <div className="grid gap-px border border-paper-line bg-paper-line lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <div className="bg-ink px-5 py-4 text-mist">
          <p className={`${LABEL} text-ink-muted`}>Request in the product</p>
          <pre data-testid="demo-request" className="mt-2 overflow-x-auto font-label text-[12.5px] leading-relaxed text-mist">
{`GET /passport/stellar-testnet/${asset.stellar_asset_code}/${shortKey(asset.stellar_issuer_public_key)}
X-API-Key: ‹institutional API key›`}
          </pre>
        </div>
        <div className="bg-white px-5 py-4 text-[13px] leading-relaxed text-paper-muted">
          <p className={`${LABEL} text-paper-muted`}>In this demo</p>
          <p className="mt-2">
            No request is sent and no key is needed: the response below is a snapshot bundled with the demo
            {record.passport ? `, as retrieved on ${fmtDateTime(record.passport.retrieved_at)}` : ''}.
            {' '}<Link to={paths.asset(asset.id)} className={LINK}>Open the asset record →</Link>
          </p>
        </div>
      </div>

      <div>
        <Tabs label="Passport response" idPrefix="passport" tabs={TABS} selected={tab} onSelect={id => show(asset.stellar_asset_code, id)} />
        <TabPanel idPrefix="passport" selected={tab}>
          {tab === 'passport' && (record.passport ? <PassportCard passport={record.passport} /> : <NoPassport record={record} />)}
          {tab === 'history' && <PassportHistoryCard history={record.history} />}
          {tab === 'anchor' && <StellarEvidence record={record} />}
        </TabPanel>
      </div>
    </div>
  )
}
