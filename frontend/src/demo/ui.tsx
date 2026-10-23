import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import type { LegalExtraction } from '../types'
import type { DemoDocument, DemoRecord } from './data'

// Shared pieces of the public demo: its addresses, the workspace's visual primitives (as in
// pages/Dashboard.tsx) and the record states derived from the bundled records.

export type AssetTab = 'documents' | 'changes' | 'history' | 'anchor'
export type PassportTab = 'passport' | 'history' | 'anchor'

export const paths = {
  overview: '/demo',
  assets: '/demo/assets',
  passport: '/demo/passport',
  asset: (assetId: string, tab?: AssetTab) => `/demo/assets/${assetId}${tab && tab !== 'documents' ? `?tab=${tab}` : ''}`,
  claims: (assetId: string, documentId: string, extractionId: string) =>
    `/demo/assets/${assetId}/documents/${documentId}/extractions/${extractionId}`,
  passportOf: (assetCode: string, tab?: PassportTab) =>
    `/demo/passport?asset=${assetCode}${tab && tab !== 'passport' ? `&tab=${tab}` : ''}`,
}

export const LABEL = 'font-label text-[10.5px] uppercase tracking-[0.16em]'
export const LINK = 'font-medium text-accent-dark hover:text-graphite'

// Dates in UTC, so that every visitor reads the recorded times the same way
export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', timeZone: 'UTC' })
export const fmtDateTime = (iso: string) =>
  `${new Date(iso).toLocaleString('en-GB', { day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'UTC' })} UTC`

export const ASSET_TYPE_LABELS: Record<string, string> = {
  real_estate: 'Real Estate', commodity: 'Commodity', equity: 'Equity', bond: 'Bond', fund: 'Fund', other: 'Other',
}

export type Tone = 'positive' | 'attention' | 'neutral' | 'muted'

const TONE_MARK: Record<Tone, string> = {
  positive: 'bg-accent',
  attention: 'bg-amber-500',
  neutral: 'bg-slate-400',
  muted: 'bg-paper-line',
}

export function Mark({ tone }: { tone: Tone }) {
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 shrink-0 ${TONE_MARK[tone]}`} />
}

export function Panel({ id, label, title, action, children, className = '' }: {
  id: string; label: string; title: string; action?: ReactNode; children: ReactNode; className?: string
}) {
  return (
    <section aria-labelledby={id} className={`border border-paper-line bg-white ${className}`}>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-paper-line px-5 py-3.5">
        <div className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>{label}</span>
          <h3 id={id} className="font-display text-[15px] font-semibold tracking-[-0.005em] text-graphite">{title}</h3>
        </div>
        {action}
      </div>
      {children}
    </section>
  )
}

export function Figure({ label, value, detail }: { label: string; value: ReactNode; detail: string }) {
  return (
    <div className="bg-white px-5 py-5">
      <dt className={`${LABEL} text-paper-muted`}>{label}</dt>
      <dd className="mt-3 font-display text-[30px] font-semibold leading-none tracking-[-0.02em] text-graphite">{value}</dd>
      <dd className="mt-2 text-[12.5px] leading-snug text-paper-muted">{detail}</dd>
    </div>
  )
}

export function PageHeader({ label, title, lead, aside }: { label: string; title: string; lead: ReactNode; aside?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-6 border-b border-paper-line pb-6">
      <div>
        <p className={`${LABEL} text-accent-dark`}>{label}</p>
        <h2 className="mt-3 font-display text-[30px] font-semibold leading-tight tracking-[-0.025em] text-graphite">{title}</h2>
        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-paper-muted">{lead}</p>
      </div>
      {aside}
    </div>
  )
}

export function Chips({ items, label }: { items: string[]; label: string }) {
  return (
    <ul aria-label={label} className="flex flex-wrap gap-2">
      {items.map(item => (
        <li key={item} className={`border border-paper-line bg-white px-2.5 py-1 ${LABEL} text-paper-muted`}>{item}</li>
      ))}
    </ul>
  )
}

// An accessible tab list: arrow keys move between tabs, the selected tab names its panel
export function Tabs<T extends string>({ label, tabs, selected, onSelect, idPrefix }: {
  label: string; tabs: { id: T; label: string }[]; selected: T; onSelect: (id: T) => void; idPrefix: string
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([])
  const onKeyDown = (event: KeyboardEvent, index: number) => {
    const step = event.key === 'ArrowRight' ? 1 : event.key === 'ArrowLeft' ? -1 : 0
    if (!step) return
    event.preventDefault()
    const next = (index + step + tabs.length) % tabs.length
    onSelect(tabs[next].id)
    refs.current[next]?.focus()
  }
  return (
    <div role="tablist" aria-label={label} className="flex gap-1 overflow-x-auto border-b border-paper-line">
      {tabs.map((tab, i) => {
        const active = tab.id === selected
        return (
          <button
            key={tab.id}
            ref={el => { refs.current[i] = el }}
            type="button"
            role="tab"
            id={`${idPrefix}-tab-${tab.id}`}
            aria-selected={active}
            aria-controls={`${idPrefix}-panel`}
            tabIndex={active ? 0 : -1}
            onClick={() => onSelect(tab.id)}
            onKeyDown={e => onKeyDown(e, i)}
            className={`-mb-px shrink-0 whitespace-nowrap border-b-2 px-4 py-3 text-[13.5px] font-medium transition-colors ${
              active ? 'border-accent text-graphite' : 'border-transparent text-paper-muted hover:text-graphite'}`}
          >
            {tab.label}
          </button>
        )
      })}
    </div>
  )
}

export function TabPanel({ idPrefix, selected, children }: { idPrefix: string; selected: string; children: ReactNode }) {
  return (
    <div role="tabpanel" id={`${idPrefix}-panel`} aria-labelledby={`${idPrefix}-tab-${selected}`} className="pt-5">
      {children}
    </div>
  )
}

// ── Record states, as the workspace overview derives them ─────────────────────

export type DocumentState = 'current' | 'reapproval' | 'in_review' | 'draft' | 'no_claims'

export function documentState(doc: DemoDocument): { state: DocumentState; open?: LegalExtraction } {
  const { extractions } = doc
  if (extractions.length === 0) return { state: 'no_claims' }
  if (extractions.some(e => e.approval_state.state === 'current')) return { state: 'current' }
  const open = [...extractions].reverse().find(e => e.approval_state.state === 'not_approved')
  if (extractions.some(e => e.approval_state.state === 'stale' || e.approval_state.state === 'superseded')) {
    return { state: 'reapproval', open }
  }
  return { state: open?.status === 'under_review' ? 'in_review' : 'draft', open }
}

// One line per asset: never "approved" on its own, always the state of its approvals
export function approvalSummary(record: DemoRecord): { text: string; tone: Tone } {
  const states = record.documents.map(d => documentState(d).state)
  if (states.length === 0) return { text: 'No documents', tone: 'muted' }
  if (states.includes('reapproval')) return { text: 'Reapproval required', tone: 'attention' }
  if (states.includes('current')) return { text: 'Approval current', tone: 'positive' }
  if (states.includes('in_review')) return { text: 'Review in progress', tone: 'neutral' }
  if (states.includes('draft')) return { text: 'Awaiting review', tone: 'neutral' }
  return { text: 'No claims yet', tone: 'muted' }
}

// The latest recorded anchoring state (stored records; a record is not a verification)
export function anchorSummary(record: DemoRecord): { text: string; tone: Tone } {
  const latest = [...record.history.anchors].sort((a, b) => b.anchored_at.localeCompare(a.anchored_at))[0]
  if (!latest) return { text: 'Not anchored', tone: 'muted' }
  const label = latest.status === 'confirmed' ? 'Confirmed' : latest.status === 'pending' ? 'Pending' : 'Failed'
  return { text: `${label} · ${fmtDate(latest.anchored_at)}`, tone: latest.status === 'confirmed' ? 'positive' : 'attention' }
}

export function StatusText({ status }: { status: string }) {
  return (
    // The asset's registry status — not an approval of its legal information
    <span className="inline-flex items-center gap-2 text-[13px] capitalize text-graphite">
      <Mark tone={status === 'active' ? 'positive' : status === 'suspended' ? 'attention' : 'muted'} />
      {status}
    </span>
  )
}

export const shortKey = (key: string) => `${key.slice(0, 6)}…${key.slice(-4)}`
