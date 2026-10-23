import { Link } from 'react-router-dom'
import { STALE_REASON_LABELS } from '../components/StatusBadge'
import { DEMO_RECORDS, FEATURED_ASSET_ID, recordById, type DemoRecord } from './data'
import {
  LABEL, LINK, Chips, Figure, Mark, PageHeader, Panel, StatusText, anchorSummary, approvalSummary, documentState, fmtDate, paths,
  type Tone,
} from './ui'

// The workspace overview of the demo: the same figures, queues and record activity as the application's
// dashboard (pages/Dashboard.tsx), counted from the bundled records only.

interface Item { key: string; code: string; title: string; detail: string; to: string; tone: Tone }

function attentionItems(records: DemoRecord[]): Item[] {
  const items: Item[] = []
  for (const { asset, documents } of records) {
    for (const doc of documents) {
      const { state, open } = documentState(doc)
      const n = doc.document.current_version_number
      const lapsed = doc.extractions.find(e => e.approval_state.state === 'superseded' || e.approval_state.state === 'stale')
      if (state === 'reapproval') {
        items.push({ key: `${doc.document.id}-reapproval`, code: asset.stellar_asset_code, tone: 'attention',
                     to: paths.asset(asset.id, 'changes'), title: `${doc.document.title} v${n}: reapproval required`,
                     detail: lapsed!.approval_state.reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ') })
      } else if ((state === 'draft' || state === 'in_review') && open) {
        items.push({ key: `${open.id}-review`, code: asset.stellar_asset_code, tone: 'neutral',
                     to: paths.claims(asset.id, doc.document.id, open.id),
                     title: `${doc.document.title}: ${state === 'in_review' ? 'review in progress' : 'claims awaiting review'}`,
                     detail: 'Nothing is served to institutions until the claims are approved.' })
      }
    }
  }
  return items.sort((a, b) => (a.tone === 'attention' ? 0 : 1) - (b.tone === 'attention' ? 0 : 1))
}

function activityItems(records: DemoRecord[]) {
  return records
    .flatMap(({ asset, timeline }) => timeline.map((event, i) => ({ ...event, key: `${asset.id}-${i}`, code: asset.stellar_asset_code, assetId: asset.id })))
    .sort((a, b) => b.at.localeCompare(a.at))
    .slice(0, 8)
}

const TOUR = [
  { title: 'See what changed', text: 'Version 1 of the LPDEMO prospectus was approved, then replaced. Follow how its approvals stopped applying.',
    to: paths.asset(FEATURED_ASSET_ID, 'changes') },
  { title: 'Read the claims and their quotations', text: 'Each claim cites a page and an exact quotation, shown in the page text of its own version.',
    to: (() => {
      const doc = recordById(FEATURED_ASSET_ID)!.documents[0]
      return paths.claims(FEATURED_ASSET_ID, doc.document.id, doc.extractions[doc.extractions.length - 1].id)
    })() },
  { title: 'Open the Legal Passport', text: 'What an institution reads: the current approved claims, who attested them and why.',
    to: paths.passportOf('LPDEMO') },
  { title: 'Inspect the Stellar Testnet evidence', text: 'Two real Testnet commitments, and the check recorded for the current one.',
    to: paths.asset(FEATURED_ASSET_ID, 'anchor') },
]

export function Overview() {
  const records = DEMO_RECORDS
  const documents = records.flatMap(r => r.documents)
  const versions = documents.reduce((n, d) => n + d.versions.length, 0)
  const approvedNow = records.filter(r => approvalSummary(r).text === 'Approval current').length
  const attention = attentionItems(records)
  const activity = activityItems(records)
  const anchored = records.filter(r => r.history.anchors.some(a => a.status === 'confirmed')).length

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <PageHeader label="Workspace overview" title="Overview"
        lead="The legal evidence records of three synthetic assets: what is approved, what has changed and what needs attention. Everything here can be opened and read; nothing can be changed."
        aside={<Chips label="Environment" items={['Read only', 'Synthetic data', 'Stellar Testnet', 'Not legal advice']} />} />

      {/* Guided tour */}
      <section aria-labelledby="demo-tour" className="border border-graphite/15 bg-ink text-mist">
        <div className="flex flex-wrap items-baseline justify-between gap-3 border-b border-white/10 px-5 py-3.5">
          <div className="flex items-baseline gap-3">
            <span className={`${LABEL} text-accent-light`}>Start here</span>
            <h3 id="demo-tour" className="font-display text-[15px] font-semibold">Follow one asset through a document change</h3>
          </div>
          <span className="text-[12.5px] text-ink-muted">LPDEMO · four steps</span>
        </div>
        <ol className="grid gap-px bg-white/10 sm:grid-cols-2 xl:grid-cols-4">
          {TOUR.map((step, i) => (
            <li key={step.title} className="bg-ink">
              <Link to={step.to} className="group flex h-full flex-col px-5 py-5 transition-colors hover:bg-white/[0.04]">
                <span className="font-label text-[11px] tracking-[0.16em] text-accent-light">{String(i + 1).padStart(2, '0')}</span>
                <span className="mt-3 font-display text-[16px] font-semibold leading-snug">{step.title}</span>
                <span className="mt-2 text-[13px] leading-relaxed text-ink-soft">{step.text}</span>
                <span aria-hidden className="mt-auto pt-4 text-[13px] text-accent-light transition-transform group-hover:translate-x-0.5">Open →</span>
              </Link>
            </li>
          ))}
        </ol>
      </section>

      {/* Summary: counted from the bundled records */}
      <dl className="grid grid-cols-2 gap-px border border-paper-line bg-paper-line lg:grid-cols-4">
        <Figure label="Assets in scope" value={records.length} detail={`${records.filter(r => r.asset.status === 'active').length} active · all on Stellar Testnet`} />
        <Figure label="Legal documents" value={documents.length} detail={`${versions} versions on record`} />
        <Figure label="Current approvals" value={approvedNow} detail={`of ${records.length} assets with documents`} />
        <Figure label="Requires attention" value={attention.length} detail="Reapproval or review" />
      </dl>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel id="demo-assets" label="Registry" title="Assets" className="xl:col-span-2"
               action={<Link to={paths.assets} className={`text-[13px] ${LINK}`}>Asset Registry →</Link>}>
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-left">
              <thead>
                <tr className={`${LABEL} text-paper-muted`}>
                  <th scope="col" className="px-5 py-3 font-normal">Asset</th>
                  <th scope="col" className="px-3 py-3 font-normal">Status</th>
                  <th scope="col" className="px-3 py-3 font-normal">Approvals</th>
                  <th scope="col" className="px-3 py-3 font-normal">Documents</th>
                  <th scope="col" className="px-5 py-3 font-normal">Anchor (recorded)</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-paper-line border-t border-paper-line">
                {records.map(record => {
                  const approvals = approvalSummary(record)
                  const anchor = anchorSummary(record)
                  return (
                    <tr key={record.asset.id} className="align-top transition-colors hover:bg-paper/60">
                      <td className="px-5 py-4">
                        <Link to={paths.asset(record.asset.id)} className="font-label text-[13px] font-semibold text-graphite hover:text-accent-dark">
                          {record.asset.stellar_asset_code}
                        </Link>
                        <p className="mt-1 text-[13px] text-paper-muted">{record.asset.issuer_organization_name}</p>
                      </td>
                      <td className="px-3 py-4"><StatusText status={record.asset.status} /></td>
                      <td className="px-3 py-4">
                        <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={approvals.tone} />{approvals.text}</span>
                      </td>
                      <td className="px-3 py-4 text-[13px] text-graphite">{record.documents.length}</td>
                      <td className="px-5 py-4">
                        <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={anchor.tone} />{anchor.text}</span>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel id="demo-attention" label="Queue" title="Requires attention">
          <ul className="divide-y divide-paper-line">
            {attention.map(item => (
              <li key={item.key}>
                <Link to={item.to} className="block px-5 py-4 transition-colors hover:bg-paper/60">
                  <p className="flex items-start gap-2.5 text-[13.5px] font-medium text-graphite">
                    <span className="mt-[7px]"><Mark tone={item.tone} /></span>
                    <span><span className="font-label text-[12px]">{item.code}</span> · {item.title}</span>
                  </p>
                  <p className="mt-1 pl-4 text-[12.5px] leading-snug text-paper-muted">{item.detail}</p>
                </Link>
              </li>
            ))}
          </ul>
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        <Panel id="demo-activity" label="Activity" title="Recent record activity" className="xl:col-span-2">
          <ol className="divide-y divide-paper-line">
            {activity.map(item => (
              <li key={item.key}>
                <Link to={paths.asset(item.assetId, 'changes')}
                      className="grid grid-cols-[96px_1fr] items-baseline gap-x-4 gap-y-1 px-5 py-3 transition-colors hover:bg-paper/60 sm:grid-cols-[110px_80px_1fr_auto]">
                  <span className="text-[12.5px] text-paper-muted">{fmtDate(item.at)}</span>
                  <span className={`${LABEL} hidden text-accent-dark sm:inline`}>{item.kind}</span>
                  <span className="text-[13.5px] text-graphite">{item.title}</span>
                  <span className="hidden font-label text-[12px] text-paper-muted sm:inline">{item.code}</span>
                </Link>
              </li>
            ))}
          </ol>
        </Panel>

        <Panel id="demo-anchoring" label="Stellar" title="Evidence anchoring">
          <dl className="grid grid-cols-2 gap-px bg-paper-line">
            {([['Confirmed', anchored, 'positive'], ['Not anchored', records.length - anchored, 'muted']] as const).map(([label, count, tone]) => (
              <div key={label} className="bg-white px-5 py-4">
                <dt className={`flex items-center gap-2 ${LABEL} text-paper-muted`}><Mark tone={tone} />{label}</dt>
                <dd className="mt-2 font-display text-[22px] font-semibold text-graphite">{count}</dd>
              </div>
            ))}
          </dl>
          <p className="border-t border-paper-line px-5 py-4 text-[12.5px] leading-relaxed text-paper-muted">
            The latest recorded state per asset. A record is not a verification: LPDEMO's commitments are real Stellar
            Testnet transactions, and the demo shows the check recorded for the current one. It never runs a live check.
          </p>
        </Panel>
      </div>

      <AboutDemo />
    </div>
  )
}

const ABOUT: { title: string; text: string }[] = [
  { title: 'What is real',
    text: 'LPDEMO is a snapshot of the public demo record. Its two evidence commitments are real Stellar Testnet transactions; anyone can open them on Stellar Expert or Horizon.' },
  { title: 'What is synthetic',
    text: 'Every issuer, document, reviewer and value is fictional. LPFUND and LPNOTE are example records bundled with this demo to show other states; they have no Stellar transaction.' },
  { title: 'What the demo does not do',
    text: 'It sends no request: no server, database or wallet is involved. Uploads, claim entry, approvals and anchoring are product actions that are not available here.' },
  { title: 'Page-grounded evidence',
    text: 'A claim is marked as found only when its quotation appears on the cited page of that exact document version. This proves that the text is there, not that a claim is legally valid.' },
  { title: 'Approvals',
    text: 'An issuer attestation and an independent legal review are internal attestations bound to one document version. A newer version supersedes them. Neither is a legal opinion.' },
  { title: 'Anchoring',
    text: 'A Stellar commitment shows that a digest was recorded at a ledger time and that the evidence has not changed since. It does not prove legal validity, ownership or regulatory compliance.' },
]

function AboutDemo() {
  return (
    <details data-testid="about-demo" className="group border border-paper-line bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 marker:hidden [&::-webkit-details-marker]:hidden">
        <span className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>Notes</span>
          <span className="font-display text-[15px] font-semibold text-graphite">About this demo</span>
          <span className="hidden text-[13px] text-paper-muted md:inline">What is real, what is synthetic · not legal advice</span>
        </span>
        <span aria-hidden className="font-label text-[12px] text-paper-muted transition-transform group-open:rotate-45">+</span>
      </summary>
      <dl className="grid grid-cols-1 gap-x-10 gap-y-6 border-t border-paper-line px-5 py-6 md:grid-cols-2 xl:grid-cols-3">
        {ABOUT.map(item => (
          <div key={item.title} className="border-t border-paper-line pt-4">
            <dt className="text-[13.5px] font-semibold text-graphite">{item.title}</dt>
            <dd className="mt-1.5 text-[13px] leading-relaxed text-paper-muted">{item.text}</dd>
          </div>
        ))}
      </dl>
    </details>
  )
}
