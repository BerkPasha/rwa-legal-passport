import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { PassportHistoryCard } from '../components/PassportHistoryCard'
import { ApprovalStateBadge, ExtractionStatusBadge } from '../components/StatusBadge'
import { extractionOrigin } from '../aiProcessing'
import { formatSize } from '../download'
import { LEGAL_FIELD_LABELS } from '../legalFields'
import type { LegalClaim } from '../types'
import { recordById, type DemoDocument, type DemoEvent, type DemoRecord, type DemoState, type VersionApproval } from './data'
import { StellarEvidence } from './StellarEvidence'
import {
  ASSET_TYPE_LABELS, LABEL, LINK, Mark, Tabs, TabPanel, approvalSummary, documentState, fmtDate, fmtDateTime, paths,
  type AssetTab, type Tone,
} from './ui'

// One asset of the demo (pages/AssetDetail.tsx): its documents and versions, what changed and why approvals
// stopped applying, its passport history and its Stellar Testnet evidence. Read only.

const TABS: { id: AssetTab; label: string }[] = [
  { id: 'documents', label: 'Documents & Versions' },
  { id: 'changes', label: 'What Changed' },
  { id: 'history', label: 'Passport History' },
  { id: 'anchor', label: 'Stellar Evidence' },
]

export function AssetRecord() {
  const { assetId } = useParams<{ assetId: string }>()
  const [params, setParams] = useSearchParams()
  const record = recordById(assetId)
  const requested = params.get('tab') as AssetTab | null
  const tab: AssetTab = TABS.some(t => t.id === requested) ? requested! : 'documents'

  if (!record) {
    return (
      <div className="border border-paper-line bg-white p-6 text-[14px] text-paper-muted">
        This asset is not part of the demo. <Link to={paths.assets} className={LINK}>Back to the Asset Registry →</Link>
      </div>
    )
  }
  const { asset } = record
  const summary = approvalSummary(record)

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <nav aria-label="Breadcrumb" className="flex items-center gap-2 text-[13px] text-paper-muted">
        <Link to={paths.assets} className="hover:text-graphite">Assets</Link>
        <span aria-hidden>/</span>
        <span className="font-medium text-graphite">{asset.stellar_asset_code}</span>
      </nav>

      {/* Asset header */}
      <div className="border border-paper-line bg-white p-6">
        <div className="flex flex-wrap items-start gap-4">
          <div className="grid h-14 w-14 shrink-0 place-items-center bg-ink">
            <span className="font-label text-lg font-bold text-accent-light">{asset.stellar_asset_code.slice(0, 2)}</span>
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-display text-[24px] font-semibold tracking-[-0.02em] text-graphite">{asset.stellar_asset_code}</h2>
              <span className="inline-flex items-center rounded-full bg-green-100 px-2.5 py-0.5 text-xs font-medium capitalize text-green-700 ring-1 ring-inset ring-green-200">
                {asset.status}
              </span>
              <span data-testid="asset-approval-summary" className="inline-flex items-center gap-2 text-[13px] text-graphite">
                <Mark tone={summary.tone} />{summary.text}
              </span>
            </div>
            <p className="mt-0.5 text-paper-muted">{asset.issuer_organization_name}</p>
            <p className="mt-2 max-w-3xl text-[13.5px] leading-relaxed text-graphite/80">{record.synopsis}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link data-testid="open-passport-link" to={paths.passportOf(asset.stellar_asset_code)}
                  className="bg-graphite px-3.5 py-2 text-[13px] font-medium text-mist hover:bg-black">
              Open Legal Passport →
            </Link>
            <Link to={paths.passportOf(asset.stellar_asset_code, 'history')}
                  className="border border-paper-line px-3.5 py-2 text-[13px] font-medium text-graphite hover:border-graphite">
              View Passport History →
            </Link>
          </div>
        </div>
        <dl className="mt-5 grid grid-cols-2 gap-4 border-t border-paper-line pt-4 sm:grid-cols-4">
          {[
            ['Network', 'Stellar Testnet'],
            ['Asset Type', ASSET_TYPE_LABELS[asset.asset_type] ?? asset.asset_type],
            ['Jurisdiction', asset.jurisdiction],
            ['Registered', fmtDate(asset.created_at)],
          ].map(([term, value]) => (
            <div key={term}>
              <dt className={`${LABEL} text-paper-muted`}>{term}</dt>
              <dd className="mt-1.5 text-[14px] font-medium text-graphite">{value}</dd>
            </div>
          ))}
        </dl>
        <div className="mt-4 border-t border-paper-line pt-4">
          <p className={`${LABEL} text-paper-muted`}>Issuer Public Key</p>
          <p className="mt-1.5 break-all font-label text-[12px] text-graphite/80">{asset.stellar_issuer_public_key}</p>
        </div>
      </div>

      <div>
        <Tabs label="Asset record" idPrefix="asset" tabs={TABS} selected={tab}
              onSelect={id => setParams(id === 'documents' ? {} : { tab: id }, { replace: true })} />
        <TabPanel idPrefix="asset" selected={tab}>
          {tab === 'documents' && <DocumentsTab key={asset.id} record={record} />}
          {tab === 'changes' && <ChangesTab key={asset.id} record={record} />}
          {tab === 'history' && <PassportHistoryCard history={record.history} />}
          {tab === 'anchor' && <StellarEvidence record={record} />}
        </TabPanel>
      </div>
    </div>
  )
}

// ── Documents & versions ──────────────────────────────────────────────────────

const REQUIREMENT: Record<string, { text: string; tone: Tone }> = {
  current: { text: 'Current version approved', tone: 'positive' },
  reapproval: { text: 'Reapproval required', tone: 'attention' },
  in_review: { text: 'Review in progress', tone: 'neutral' },
  draft: { text: 'Awaiting review', tone: 'neutral' },
  no_claims: { text: 'No claims yet', tone: 'muted' },
}

function DocumentsTab({ record }: { record: DemoRecord }) {
  return (
    <div className="space-y-4">
      <p className="text-[13px] text-paper-muted">
        Uploading documents or new versions, entering claims and running extraction are product actions; they are not
        available in the read-only demo.
      </p>
      {record.documents.map(doc => <DocumentCard key={doc.document.id} record={record} doc={doc} />)}
    </div>
  )
}

function DocumentCard({ record, doc }: { record: DemoRecord; doc: DemoDocument }) {
  const { document } = doc
  const requirement = REQUIREMENT[documentState(doc).state]
  const [open, setOpen] = useState<number | null>(document.current_version_number)
  const newestFirst = [...doc.versions].reverse()
  return (
    <div data-testid="demo-document" className="border border-paper-line bg-white">
      <div className="flex flex-wrap items-start justify-between gap-3 border-b border-paper-line px-5 py-4">
        <div>
          <p className="font-display text-[17px] font-semibold text-graphite">{document.title}</p>
          <p className="mt-1 text-[12.5px] capitalize text-paper-muted">
            {document.document_type.replace(/_/g, ' ')} · {doc.versions.length} version{doc.versions.length === 1 ? '' : 's'} · current version v{document.current_version_number}
          </p>
        </div>
        <span className="inline-flex items-center gap-2 border border-paper-line px-2.5 py-1 text-[12.5px] text-graphite">
          <Mark tone={requirement.tone} />{requirement.text}
        </span>
      </div>
      <ol className="divide-y divide-paper-line">
        {newestFirst.map(version => {
          const current = version.version_number === document.current_version_number
          const expanded = open === version.version_number
          const extractions = doc.extractions.filter(e => e.version_id === version.id)
          const pages = doc.pages[version.id]?.length ?? 0
          const panelId = `version-${version.id}`
          return (
            <li key={version.id} data-testid="demo-version" data-version={version.version_number}>
              <button type="button" aria-expanded={expanded} aria-controls={panelId}
                      onClick={() => setOpen(expanded ? null : version.version_number)}
                      className="flex w-full flex-wrap items-center gap-x-3 gap-y-1 px-5 py-3.5 text-left transition-colors hover:bg-paper/60">
                <span className="font-label text-[13px] font-semibold text-graphite">v{version.version_number}</span>
                {current
                  ? <span className="bg-accent/10 px-1.5 py-0.5 font-label text-[10.5px] uppercase tracking-[0.12em] text-accent-dark">Current version</span>
                  : <span className="bg-rose-50 px-1.5 py-0.5 font-label text-[10.5px] uppercase tracking-[0.12em] text-rose-700">Superseded</span>}
                <span className="text-[13px] text-graphite">{version.original_filename}</span>
                <span className="text-[12.5px] text-paper-muted">uploaded {fmtDateTime(version.created_at)}</span>
                <span aria-hidden className="ml-auto font-label text-[12px] text-paper-muted">{expanded ? '−' : '+'}</span>
              </button>
              {expanded && (
                <div id={panelId} className="space-y-3 bg-paper/40 px-5 pb-5 pt-1">
                  <dl className="grid gap-3 text-[12.5px] sm:grid-cols-3">
                    <div><dt className="text-paper-muted">Size</dt><dd className="text-graphite">{formatSize(version.file_size)}</dd></div>
                    <div><dt className="text-paper-muted">Pages with text</dt><dd className="text-graphite">{pages} of {pages} (no OCR needed)</dd></div>
                    <div><dt className="text-paper-muted">Status</dt><dd className="text-graphite">{current ? 'Current version' : 'Superseded by a newer version'}</dd></div>
                    <div className="sm:col-span-3">
                      <dt className="text-paper-muted">SHA-256 of the stored PDF</dt>
                      <dd data-testid="demo-version-sha256" className="break-all font-label text-[12px] text-graphite">{version.sha256_hash}</dd>
                    </div>
                  </dl>
                  {extractions.length === 0 ? (
                    <p className="text-[12.5px] text-paper-muted">No claims recorded for this version.</p>
                  ) : extractions.map(ex => {
                    const origin = extractionOrigin(ex.ai_model, [], ex.source)
                    const known = ex.claims.filter(c => !c.is_unknown).length
                    return (
                      <Link key={ex.id} to={paths.claims(record.asset.id, document.id, ex.id)}
                            className="flex flex-wrap items-center gap-3 border border-paper-line bg-white px-3.5 py-2.5 transition-colors hover:border-graphite/40">
                        <ExtractionStatusBadge status={ex.status} />
                        <ApprovalStateBadge state={ex.approval_state.state} />
                        <span className="text-[12.5px] text-graphite">{known} cited claim{known === 1 ? '' : 's'} · {ex.claims.length - known} unknown</span>
                        <span className="text-[12px] text-paper-muted">{origin.label}</span>
                        <span className={`ml-auto text-[13px] ${LINK}`}>Review claims →</span>
                      </Link>
                    )
                  })}
                </div>
              )}
            </li>
          )
        })}
      </ol>
    </div>
  )
}

// ── What changed ──────────────────────────────────────────────────────────────

function ChangesTab({ record }: { record: DemoRecord }) {
  const changed = record.documents.find(d => d.versions.length > 1)
  return (
    <div className="space-y-6">
      <Timeline record={record} />
      {changed ? <VersionComparison record={record} doc={changed} /> : (
        <p className="border border-paper-line bg-white px-5 py-4 text-[13.5px] text-paper-muted">
          Each document of {record.asset.stellar_asset_code} has a single version: there is no earlier version to compare.
        </p>
      )}
    </div>
  )
}

const KIND_TONE: Record<DemoEvent['kind'], string> = {
  Asset: 'text-paper-muted', Document: 'text-graphite', Claims: 'text-graphite', Approval: 'text-accent-dark',
  Anchor: 'text-accent-dark', Check: 'text-accent-dark',
}

function Timeline({ record }: { record: DemoRecord }) {
  const events = record.timeline
  const initial = events.findIndex(e => e.invalidates)
  const [selected, setSelected] = useState(initial >= 0 ? initial : events.length - 1)
  const event = events[selected]
  return (
    <section aria-labelledby="timeline-title" className="border border-paper-line bg-white">
      <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-paper-line px-5 py-3.5">
        <div className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>Timeline</span>
          <h3 id="timeline-title" className="font-display text-[15px] font-semibold text-graphite">How the record changed</h3>
        </div>
        <span className="text-[12.5px] text-paper-muted">Select an event to see the record's position after it</span>
      </div>
      <div className="grid lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <ol className="border-b border-paper-line py-2 lg:border-b-0 lg:border-r">
          {events.map((e, i) => (
            <li key={`${e.at}-${i}`}>
              <button type="button" aria-pressed={i === selected} onClick={() => setSelected(i)}
                      data-testid="timeline-event"
                      className={`grid w-full grid-cols-[18px_1fr] gap-3 px-5 py-2.5 text-left transition-colors ${
                        i === selected ? 'bg-paper' : 'hover:bg-paper/50'}`}>
                <span aria-hidden className={`mt-1.5 h-2.5 w-2.5 border ${
                  e.invalidates ? 'border-amber-500 bg-amber-400' : i === selected ? 'border-accent bg-accent' : 'border-paper-line bg-white'}`} />
                <span>
                  <span className="flex flex-wrap items-baseline gap-x-2">
                    <span className={`${LABEL} ${KIND_TONE[e.kind]}`}>{e.kind}</span>
                    <span className="text-[12px] text-paper-muted">{fmtDateTime(e.at)}</span>
                  </span>
                  <span className={`mt-0.5 block text-[13.5px] ${i === selected ? 'font-semibold text-graphite' : 'text-graphite'}`}>
                    {e.title}
                    {e.invalidates && <span className="ml-2 font-label text-[10.5px] uppercase tracking-[0.12em] text-amber-700">Approvals invalidated</span>}
                  </span>
                </span>
              </button>
            </li>
          ))}
        </ol>
        <div data-testid="timeline-state" className="space-y-4 px-5 py-5">
          <div>
            <p className={`${LABEL} text-paper-muted`}>{fmtDateTime(event.at)} · {event.kind}</p>
            <p className="mt-2 font-display text-[18px] font-semibold leading-snug text-graphite">{event.title}</p>
            <p className="mt-1.5 text-[13.5px] leading-relaxed text-paper-muted">{event.detail}</p>
          </div>
          {event.consequence && (
            <p data-testid="timeline-consequence" className="border-l-2 border-amber-400 bg-amber-50 px-4 py-3 text-[13.5px] leading-relaxed text-amber-950">
              {event.consequence}
            </p>
          )}
          <StateView state={event.state} record={record} />
        </div>
      </div>
      <p className="border-t border-paper-line px-5 py-3 text-[12px] leading-relaxed text-paper-muted">
        The position after each event follows from the recorded times and the approval rules that the platform
        applies on every read. {record.anchoredOnTestnet
          ? 'The anchoring events are real Stellar Testnet transactions.'
          : 'This synthetic record has no Stellar transaction.'}
      </p>
    </section>
  )
}

const APPROVAL_TEXT: Record<VersionApproval, { text: string; tone: Tone }> = {
  not_approved: { text: 'Not approved', tone: 'muted' },
  in_review: { text: 'In review', tone: 'neutral' },
  current: { text: 'Approval current', tone: 'positive' },
  superseded: { text: 'Superseded — no longer applies', tone: 'attention' },
}

function StateView({ state, record }: { state: DemoState; record: DemoRecord }) {
  const passport = {
    none: { text: 'Not available: nothing has been approved', tone: 'muted' as Tone },
    current: { text: 'Served from the current approvals', tone: 'positive' as Tone },
    withheld: { text: 'Withheld until the current version is approved again', tone: 'attention' as Tone },
  }[state.passport]
  const anchor = {
    none: { text: record.anchoredOnTestnet ? 'Not anchored yet' : 'Not anchored', tone: 'muted' as Tone },
    matches: { text: 'Latest commitment matches the current evidence', tone: 'positive' as Tone },
    stale: { text: 'Latest commitment no longer matches the current evidence', tone: 'attention' as Tone },
  }[state.anchor]
  return (
    <dl className="divide-y divide-paper-line border border-paper-line">
      <div className="grid gap-1 px-4 py-3 sm:grid-cols-[140px_1fr]">
        <dt className={`${LABEL} pt-0.5 text-paper-muted`}>Legal Passport</dt>
        <dd className="flex items-center gap-2 text-[13.5px] text-graphite"><Mark tone={passport.tone} />{passport.text}</dd>
      </div>
      <div className="grid gap-1 px-4 py-3 sm:grid-cols-[140px_1fr]">
        <dt className={`${LABEL} pt-0.5 text-paper-muted`}>Approvals</dt>
        <dd className="space-y-1">
          {state.versions.length === 0 ? <span className="text-[13.5px] text-paper-muted">No document yet</span> : state.versions.map(v => (
            <p key={v.version} className="flex items-center gap-2 text-[13.5px] text-graphite">
              <Mark tone={APPROVAL_TEXT[v.approval].tone} />
              <span className="font-label text-[12px]">v{v.version}</span> {APPROVAL_TEXT[v.approval].text}
            </p>
          ))}
        </dd>
      </div>
      <div className="grid gap-1 px-4 py-3 sm:grid-cols-[140px_1fr]">
        <dt className={`${LABEL} pt-0.5 text-paper-muted`}>Stellar evidence</dt>
        <dd className="flex items-center gap-2 text-[13.5px] text-graphite"><Mark tone={anchor.tone} />{anchor.text}</dd>
      </div>
    </dl>
  )
}

function changeOf(before: LegalClaim | undefined, after: LegalClaim | undefined): { text: string; tone: Tone } {
  const was = before && !before.is_unknown
  const is = after && !after.is_unknown
  if (!was && !is) return { text: 'Unknown in both', tone: 'muted' }
  if (was && !is) return { text: 'No longer asserted', tone: 'attention' }
  if (!was && is) return { text: 'Newly asserted', tone: 'neutral' }
  return before!.value === after!.value
    ? { text: 'Same value, cited again', tone: 'positive' }
    : { text: 'Changed', tone: 'attention' }
}

function ClaimCell({ claim }: { claim: LegalClaim | undefined }) {
  if (!claim || claim.is_unknown) return <span className="text-[13px] italic text-paper-muted">unknown</span>
  return (
    <span className="block">
      <span className="block text-[13px] text-graphite">{claim.value}</span>
      <span className="mt-0.5 block text-[12px] text-paper-muted">page {claim.source_page}</span>
    </span>
  )
}

function VersionComparison({ record, doc }: { record: DemoRecord; doc: DemoDocument }) {
  const current = doc.versions[doc.versions.length - 1]
  const previous = doc.versions[doc.versions.length - 2]
  const latest = (versionId: string) => [...doc.extractions].reverse().find(e => e.version_id === versionId)
  const before = latest(previous.id)
  const after = latest(current.id)
  const fields = Object.keys(LEGAL_FIELD_LABELS)
  const claim = (ex: typeof before, field: string) => ex?.claims.find(c => c.field_name === field)
  return (
    <section aria-labelledby="comparison-title" data-testid="version-comparison" className="border border-paper-line bg-white">
      <div className="border-b border-paper-line px-5 py-3.5">
        <div className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>Comparison</span>
          <h3 id="comparison-title" className="font-display text-[15px] font-semibold text-graphite">
            {doc.document.title}: version {previous.version_number} → version {current.version_number}
          </h3>
        </div>
        <p className="mt-2 max-w-4xl text-[13px] leading-relaxed text-paper-muted">
          Approvals are bound to one exact version. Version {current.version_number} superseded version {previous.version_number},
          so its claims were entered again from its own pages and reviewed again; nothing was carried over. Version {previous.version_number}'s
          claims remain for audit only and are not part of any current Legal Passport.
        </p>
      </div>
      <div className="overflow-x-auto">
        <table className="w-full min-w-[720px] text-left">
          <thead>
            <tr className={`${LABEL} text-paper-muted`}>
              <th scope="col" className="px-5 py-3 font-normal">Field</th>
              <th scope="col" className="px-3 py-3 font-normal">
                Version {previous.version_number} · {before ? (before.approval_state.state === 'superseded' ? 'approved, superseded' : before.status) : 'no claims'}
              </th>
              <th scope="col" className="px-3 py-3 font-normal">
                Version {current.version_number} · {after ? (after.approval_state.state === 'current' ? 'approved, current' : after.status.replace('_', ' ')) : 'no claims'}
              </th>
              <th scope="col" className="px-5 py-3 font-normal">Change</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-paper-line border-t border-paper-line">
            {fields.map(field => {
              const change = changeOf(claim(before, field), claim(after, field))
              return (
                <tr key={field} data-testid={`comparison-${field}`} className="align-top">
                  <td className="px-5 py-3.5 text-[13px] font-medium text-graphite">{LEGAL_FIELD_LABELS[field]}</td>
                  <td className="px-3 py-3.5"><ClaimCell claim={claim(before, field)} /></td>
                  <td className="px-3 py-3.5"><ClaimCell claim={claim(after, field)} /></td>
                  <td className="whitespace-nowrap px-5 py-3.5">
                    <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={change.tone} />{change.text}</span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap gap-x-6 gap-y-2 border-t border-paper-line px-5 py-3 text-[13px]">
        {before && <Link to={paths.claims(record.asset.id, doc.document.id, before.id)} className={LINK}>Version {previous.version_number} claims and quotations →</Link>}
        {after && <Link to={paths.claims(record.asset.id, doc.document.id, after.id)} className={LINK}>Version {current.version_number} claims and quotations →</Link>}
      </div>
    </section>
  )
}
