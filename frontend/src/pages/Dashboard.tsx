import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { getAssets, getDocuments, getExtractions, getStellarAnchors, ApiError } from '../api'
import type { ApprovalStaleReason, Asset, Document, LegalExtraction, StellarAnchor } from '../types'
import { PageSpinner } from '../components/Spinner'
import { STALE_REASON_LABELS } from '../components/StatusBadge'
import { ROLE_LABELS } from '../components/Layout'
import { ANCHOR_STATUS_LABELS } from '../anchor'
import { useAuth } from '../AuthContext'
import { useServerInfo } from '../serverInfo'

// The workspace overview: the legal evidence records of the assets in the caller's scope, read from the
// same API as every other page. Nothing here is estimated: every figure is counted from those records,
// and every state is the backend's own (approval states, recorded anchor states).

const FIELD_LABELS: Record<string, string> = {
  ownership_nature: 'Ownership Nature',
  income_rights: 'Income Rights',
  voting_rights: 'Voting Rights',
  redemption_rights: 'Redemption Rights',
  transfer_restrictions: 'Transfer Restrictions',
  jurisdiction: 'Jurisdiction',
  insolvency_provisions: 'Insolvency Provisions',
}

// The overview reads the documents, claims and anchors of this many assets, most recently updated first
const DETAIL_LIMIT = 12
const LABEL = 'font-label text-[10.5px] uppercase tracking-[0.16em]'

interface DocumentRecord { document: Document; extractions: LegalExtraction[] | null }
interface AssetRecord { documents: DocumentRecord[] | null; anchors: StellarAnchor[] | null }

type DocumentState = 'current' | 'reapproval' | 'in_review' | 'draft' | 'no_claims' | 'unknown'

const time = (iso: string) => new Date(iso).getTime()
const fmtDate = (iso: string) => new Date(iso).toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })

// The approval state of one document, from its extractions' own approval states
function documentState({ extractions }: DocumentRecord): { state: DocumentState; reasons: ApprovalStaleReason[]; open?: LegalExtraction } {
  if (extractions === null) return { state: 'unknown', reasons: [] }
  if (extractions.length === 0) return { state: 'no_claims', reasons: [] }
  if (extractions.some(e => e.approval_state?.state === 'current')) return { state: 'current', reasons: [] }
  const open = extractions.filter(e => e.approval_state?.state === 'not_approved').sort((a, b) => time(b.created_at) - time(a.created_at))[0]
  const lapsed = extractions.find(e => e.approval_state?.state === 'stale' || e.approval_state?.state === 'superseded')
  if (lapsed) return { state: 'reapproval', reasons: lapsed.approval_state.reasons, open }
  return { state: open?.status === 'under_review' ? 'in_review' : 'draft', reasons: [], open }
}

type Tone = 'positive' | 'attention' | 'neutral' | 'muted'

const TONE_MARK: Record<Tone, string> = {
  positive: 'bg-accent',
  attention: 'bg-amber-500',
  neutral: 'bg-slate-400',
  muted: 'bg-paper-line',
}

// One line per asset: never "approved" on its own, always the state of its approvals
function approvalSummary(record: AssetRecord | undefined): { text: string; tone: Tone; note?: string } {
  if (!record || record.documents === null) return { text: '—', tone: 'muted' }
  if (record.documents.length === 0) return { text: 'No documents', tone: 'muted' }
  const states = record.documents.map(d => documentState(d).state)
  if (states.includes('unknown')) return { text: '—', tone: 'muted' }
  const open = states.filter(s => s === 'draft' || s === 'in_review').length
  if (states.includes('reapproval')) return { text: 'Reapproval required', tone: 'attention' }
  if (states.includes('current')) return { text: 'Approval current', tone: 'positive', note: open ? `${open} more in review` : undefined }
  if (states.includes('in_review')) return { text: 'Review in progress', tone: 'neutral' }
  if (states.includes('draft')) return { text: 'Awaiting review', tone: 'neutral' }
  return { text: 'No claims yet', tone: 'muted' }
}

const latestAnchor = (anchors: StellarAnchor[]) => [...anchors].sort((a, b) => time(b.created_at) - time(a.created_at))[0]

function anchorSummary(record: AssetRecord | undefined): { text: string; tone: Tone } {
  if (!record || record.anchors === null) return { text: '—', tone: 'muted' }
  if (record.anchors.length === 0) return { text: 'Not anchored', tone: 'muted' }
  const latest = latestAnchor(record.anchors)
  const tone: Tone = latest.status === 'confirmed' ? 'positive' : latest.status === 'pending' ? 'neutral' : 'attention'
  return { text: `${ANCHOR_STATUS_LABELS[latest.status]} · ${fmtDate(latest.created_at)}`, tone }
}

interface AttentionItem { key: string; asset: Asset; title: string; detail: string; to: string; rank: number }

function attentionItems(assets: Asset[], records: Record<string, AssetRecord>): AttentionItem[] {
  const items: AttentionItem[] = []
  for (const asset of assets) {
    const record = records[asset.id]
    if (asset.status !== 'active') {
      items.push({ key: `${asset.id}-status`, asset, rank: 3, to: `/assets/${asset.id}`,
                   title: `Asset is ${asset.status}`, detail: 'No Legal Passport is served while the asset is not active.' })
    }
    for (const doc of record?.documents ?? []) {
      const { state, reasons, open } = documentState(doc)
      const version = doc.document.current_version_number
      if (state === 'reapproval') {
        items.push({ key: `${doc.document.id}-reapproval`, asset, rank: 0, to: `/assets/${asset.id}`,
                     title: `${doc.document.title} v${version}: reapproval required`,
                     detail: reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ') || 'Its earlier approvals no longer apply.' })
      } else if ((state === 'draft' || state === 'in_review') && open) {
        items.push({ key: `${open.id}-review`, asset, rank: 1, to: `/assets/${asset.id}/documents/${doc.document.id}/extractions/${open.id}`,
                     title: `${doc.document.title}: ${state === 'in_review' ? 'review in progress' : 'claims awaiting review'}`,
                     detail: 'Nothing is served to institutions until the claims are approved.' })
      }
    }
    if (record?.anchors?.length) {
      const latest = latestAnchor(record.anchors)
      if (latest.status !== 'confirmed') {
        items.push({ key: `${latest.id}-anchor`, asset, rank: 2, to: `/assets/${asset.id}`,
                     title: latest.status === 'pending' ? 'Anchoring transaction pending' : 'Last anchoring attempt failed',
                     detail: latest.status === 'pending'
                       ? 'Submitted, but not confirmed in a Stellar ledger; it proves nothing until then.'
                       : latest.failure_reason ?? 'Nothing was recorded on Stellar Testnet.' })
      }
    }
  }
  return items.sort((a, b) => a.rank - b.rank)
}

interface ActivityItem { key: string; at: string; asset: Asset; kind: string; text: string; to: string }

function activityItems(assets: Asset[], records: Record<string, AssetRecord>): ActivityItem[] {
  const items: ActivityItem[] = []
  for (const asset of assets) {
    items.push({ key: `${asset.id}-registered`, at: asset.created_at, asset, kind: 'Asset', text: 'Registered', to: `/assets/${asset.id}` })
    const record = records[asset.id]
    for (const { document, extractions } of record?.documents ?? []) {
      const version = document.current_version
      if (version) {
        items.push({ key: version.id, at: version.created_at, asset, kind: 'Document', to: `/assets/${asset.id}`,
                     text: `${document.title} · version ${version.version_number} uploaded` })
      }
      for (const extraction of extractions ?? []) {
        items.push({ key: extraction.id, at: extraction.created_at, asset, kind: 'Claims',
                     to: `/assets/${asset.id}/documents/${document.id}/extractions/${extraction.id}`,
                     text: `${document.title} · ${extraction.source === 'manual' ? 'claims entered manually' : 'claims extracted'}` })
      }
    }
    for (const anchor of record?.anchors ?? []) {
      items.push({ key: anchor.id, at: anchor.created_at, asset, kind: 'Anchor', to: `/assets/${asset.id}`,
                   text: `Anchoring recorded · ${ANCHOR_STATUS_LABELS[anchor.status].toLowerCase()}` })
    }
  }
  return items.sort((a, b) => time(b.at) - time(a.at)).slice(0, 8)
}

function Mark({ tone }: { tone: Tone }) {
  return <span aria-hidden className={`inline-block h-1.5 w-1.5 shrink-0 ${TONE_MARK[tone]}`} />
}

function Panel({ id, label, title, action, children, className = '' }: {
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

function StatusText({ status }: { status: Asset['status'] }) {
  return (
    // The asset's registry status — not an approval of its legal information
    <span data-testid="asset-status-badge" className="inline-flex items-center gap-2 text-[13px] capitalize text-graphite">
      <Mark tone={status === 'active' ? 'positive' : status === 'suspended' ? 'attention' : 'muted'} />
      {status}
    </span>
  )
}

function Figure({ label, value, detail }: { label: string; value: ReactNode; detail: string }) {
  return (
    <div className="bg-white px-5 py-5">
      <dt className={`${LABEL} text-paper-muted`}>{label}</dt>
      <dd className="mt-3 font-display text-[30px] font-semibold leading-none tracking-[-0.02em] text-graphite">{value}</dd>
      <dd className="mt-2 text-[12.5px] leading-snug text-paper-muted">{detail}</dd>
    </div>
  )
}

export function Dashboard() {
  const [assets, setAssets] = useState<Asset[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [records, setRecords] = useState<Record<string, AssetRecord>>({})
  const [recordsLoading, setRecordsLoading] = useState(false)
  const { activeToken, identity } = useAuth()
  const serverInfo = useServerInfo()
  const permissions = identity.state === 'signed_in' ? identity.user.permissions : null
  const permissionKey = permissions?.join(',') ?? ''

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

  // The records behind the overview, only with the permissions the signed-in role has
  useEffect(() => {
    if (!permissions || assets.length === 0) {
      setRecords({})
      return
    }
    let current = true
    const canDocuments = permissions.includes('document:read')
    const canClaims = permissions.includes('extraction:read')
    const canAnchors = permissions.includes('asset:read')
    const targets = [...assets].sort((a, b) => time(b.updated_at) - time(a.updated_at)).slice(0, DETAIL_LIMIT)
    setRecordsLoading(true)
    Promise.all(targets.map(async asset => {
      const [documents, anchors] = await Promise.all([
        canDocuments ? getDocuments(asset.id).catch(() => null) : Promise.resolve(null),
        canAnchors ? getStellarAnchors(asset.id).catch(() => null) : Promise.resolve(null),
      ])
      const withClaims = documents === null ? null : await Promise.all(documents.map(async document => ({
        document, extractions: canClaims ? await getExtractions(document.id).catch(() => null) : null,
      })))
      return [asset.id, { documents: withClaims, anchors }] as const
    })).then(entries => {
      if (!current) return
      setRecords(Object.fromEntries(entries))
      setRecordsLoading(false)
    })
    return () => { current = false }
  }, [assets, permissionKey])

  const attention = useMemo(() => attentionItems(assets, records), [assets, records])
  const activity = useMemo(() => activityItems(assets, records), [assets, records])

  if (loading) return <PageSpinner />

  const read = Object.values(records)
  const haveRecords = read.length > 0
  const active = assets.filter(a => a.status === 'active').length
  const testnet = assets.filter(a => a.blockchain_network === 'stellar-testnet').length
  const documents = read.flatMap(r => r.documents ?? [])
  const versions = documents.reduce((n, d) => n + d.document.current_version_number, 0)
  const withDocuments = assets.filter(a => (records[a.id]?.documents?.length ?? 0) > 0)
  const approvedNow = withDocuments.filter(a => approvalSummary(records[a.id]).text === 'Approval current').length
  const anchorCounts = { confirmed: 0, pending: 0, failed: 0, none: 0 }
  for (const record of read) {
    if (record.anchors === null) continue
    if (record.anchors.length === 0) anchorCounts.none += 1
    else anchorCounts[latestAnchor(record.anchors).status] += 1
  }
  const pendingFigure = (value: number) => (recordsLoading ? '…' : haveRecords ? value : '—')
  const recordsNote = !permissions ? 'Sign in to read the records' : recordsLoading ? 'Reading the records…' : ''
  const shown = [...assets].sort((a, b) => time(b.updated_at) - time(a.updated_at)).slice(0, 8)

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      {/* Header */}
      <div className="flex flex-wrap items-end justify-between gap-6 border-b border-paper-line pb-6">
        <div>
          <p className={`${LABEL} text-accent-dark`}>Workspace overview</p>
          <h2 className="mt-3 font-display text-[30px] font-semibold leading-tight tracking-[-0.025em] text-graphite">Overview</h2>
          <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-paper-muted">
            The legal evidence records of the assets in your scope: what is approved, what has changed and what needs attention.
          </p>
        </div>
        <ul aria-label="Environment" className="flex flex-wrap gap-2">
          {identity.state === 'signed_in' && (
            <li className={`border border-paper-line bg-white px-2.5 py-1 ${LABEL} text-graphite/80`}>
              {ROLE_LABELS[identity.user.role] ?? identity.user.role} · {identity.user.scope === 'global' ? 'all issuers' : identity.user.issuer_ids.join(', ')}
            </li>
          )}
          {serverInfo?.read_only && <li className={`border border-amber-300 bg-amber-50 px-2.5 py-1 ${LABEL} text-amber-900`}>Read-only</li>}
          <li className={`border border-paper-line bg-white px-2.5 py-1 ${LABEL} text-paper-muted`}>Stellar Testnet anchoring</li>
          <li className={`border border-paper-line bg-white px-2.5 py-1 ${LABEL} text-paper-muted`}>Not legal advice</li>
        </ul>
      </div>

      {error && (
        <div role="alert" className="border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      )}

      {/* Summary: counted from the records, nothing estimated */}
      <dl className="grid grid-cols-2 gap-px border border-paper-line bg-paper-line lg:grid-cols-4">
        <Figure label="Assets in scope" value={assets.length}
                detail={`${active} active · ${testnet} on Testnet · ${assets.length - testnet} on Mainnet`} />
        <Figure label="Legal documents" value={pendingFigure(documents.length)}
                detail={recordsNote || `${versions} version${versions === 1 ? '' : 's'} on record`} />
        <Figure label="Current approvals" value={pendingFigure(approvedNow)}
                detail={recordsNote || `of ${withDocuments.length} asset${withDocuments.length === 1 ? '' : 's'} with documents`} />
        <Figure label="Requires attention" value={pendingFigure(attention.length)}
                detail={recordsNote || 'Reapproval, review, anchoring or asset status'} />
      </dl>

      <div className="grid gap-6 xl:grid-cols-3">
        {/* Assets */}
        <Panel id="dashboard-assets" label="Registry" title="Assets" className="xl:col-span-2"
               action={<Link to="/assets" className="text-[13px] font-medium text-accent-dark hover:text-graphite">Asset Registry →</Link>}>
          {assets.length === 0 ? (
            <p className="px-5 py-8 text-sm text-paper-muted">{error ? 'No assets could be read.' : 'No assets in your scope yet.'}</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[720px] text-left">
                <thead>
                  <tr className={`${LABEL} text-paper-muted`}>
                    <th scope="col" className="px-5 py-3 font-normal">Asset</th>
                    <th scope="col" className="px-3 py-3 font-normal">Status</th>
                    <th scope="col" className="px-3 py-3 font-normal">Approvals</th>
                    <th scope="col" className="px-3 py-3 font-normal">Documents</th>
                    <th scope="col" className="px-3 py-3 font-normal">Anchor (recorded)</th>
                    <th scope="col" className="px-5 py-3 text-right font-normal">Updated</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-paper-line border-t border-paper-line">
                  {shown.map(asset => {
                    const record = records[asset.id]
                    const approvals = approvalSummary(record)
                    const anchor = anchorSummary(record)
                    return (
                      <tr key={asset.id} className="align-top transition-colors hover:bg-paper/60">
                        <td className="px-5 py-4">
                          <Link to={`/assets/${asset.id}`} className="font-label text-[13px] font-semibold text-graphite hover:text-accent-dark">
                            {asset.stellar_asset_code}
                          </Link>
                          <p className="mt-1 text-[13px] text-paper-muted">{asset.issuer_organization_name}</p>
                          <p className="mt-0.5 text-[12px] text-paper-muted/80">{asset.blockchain_network === 'stellar-mainnet' ? 'Mainnet' : 'Testnet'}</p>
                        </td>
                        <td className="px-3 py-4"><StatusText status={asset.status} /></td>
                        <td className="px-3 py-4">
                          <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={approvals.tone} />{approvals.text}</span>
                          {approvals.note && <p className="mt-1 pl-3.5 text-[12px] text-paper-muted">{approvals.note}</p>}
                        </td>
                        <td className="px-3 py-4 text-[13px] text-graphite">
                          {record?.documents ? record.documents.length : '—'}
                        </td>
                        <td className="px-3 py-4">
                          <span className="inline-flex items-center gap-2 text-[13px] text-graphite"><Mark tone={anchor.tone} />{anchor.text}</span>
                        </td>
                        <td className="whitespace-nowrap px-5 py-4 text-right text-[13px] text-paper-muted">{fmtDate(asset.updated_at)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
          {assets.length > shown.length && (
            <div className="border-t border-paper-line px-5 py-3">
              <Link to="/assets" className="text-[13px] font-medium text-accent-dark hover:text-graphite">View all {assets.length} assets →</Link>
            </div>
          )}
        </Panel>

        {/* Requires attention */}
        <Panel id="dashboard-attention" label="Queue" title="Requires attention">
          {!haveRecords ? (
            <p className="px-5 py-8 text-sm text-paper-muted">{recordsNote || 'No records to read.'}</p>
          ) : attention.length === 0 ? (
            <p className="px-5 py-8 text-sm text-paper-muted">Nothing requires attention: every approval in scope is current.</p>
          ) : (
            <ul className="divide-y divide-paper-line">
              {attention.slice(0, 8).map(item => (
                <li key={item.key}>
                  <Link to={item.to} className="block px-5 py-4 transition-colors hover:bg-paper/60">
                    <p className="flex items-start gap-2.5 text-[13.5px] font-medium text-graphite">
                      <Mark tone={item.rank === 0 ? 'attention' : 'neutral'} />
                      <span className="-mt-[5px]"><span className="font-label text-[12px]">{item.asset.stellar_asset_code}</span> · {item.title}</span>
                    </p>
                    <p className="mt-1 pl-4 text-[12.5px] leading-snug text-paper-muted">{item.detail}</p>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Panel>
      </div>

      <div className="grid gap-6 xl:grid-cols-3">
        {/* Record activity */}
        <Panel id="dashboard-activity" label="Activity" title="Recent record activity" className="xl:col-span-2">
          {activity.length === 0 ? (
            <p className="px-5 py-8 text-sm text-paper-muted">{recordsNote || 'No activity on record.'}</p>
          ) : (
            <ol className="divide-y divide-paper-line">
              {activity.map(item => (
                <li key={item.key}>
                  <Link to={item.to} className="grid grid-cols-[96px_80px_1fr] items-baseline gap-4 px-5 py-3 transition-colors hover:bg-paper/60 sm:grid-cols-[110px_90px_1fr_auto]">
                    <span className="text-[12.5px] text-paper-muted">{fmtDate(item.at)}</span>
                    <span className={`${LABEL} text-accent-dark`}>{item.kind}</span>
                    <span className="truncate text-[13.5px] text-graphite">{item.text}</span>
                    <span className="hidden font-label text-[12px] text-paper-muted sm:inline">{item.asset.stellar_asset_code}</span>
                  </Link>
                </li>
              ))}
            </ol>
          )}
        </Panel>

        {/* Anchoring */}
        <Panel id="dashboard-anchoring" label="Stellar" title="Evidence anchoring">
          {!haveRecords ? (
            <p className="px-5 py-8 text-sm text-paper-muted">{recordsNote || 'No records to read.'}</p>
          ) : (
            <>
              <dl className="grid grid-cols-2 gap-px bg-paper-line">
                {([['Confirmed', anchorCounts.confirmed, 'positive'], ['Pending', anchorCounts.pending, 'neutral'],
                   ['Failed', anchorCounts.failed, 'attention'], ['Not anchored', anchorCounts.none, 'muted']] as const).map(([label, count, tone]) => (
                  <div key={label} className="bg-white px-5 py-4">
                    <dt className={`flex items-center gap-2 ${LABEL} text-paper-muted`}><Mark tone={tone} />{label}</dt>
                    <dd className="mt-2 font-display text-[22px] font-semibold text-graphite">{count}</dd>
                  </div>
                ))}
              </dl>
              <p className="border-t border-paper-line px-5 py-4 text-[12.5px] leading-relaxed text-paper-muted">
                The latest recorded state per asset. A record is not a verification: the live check against Stellar
                Horizon runs on each asset's Stellar Evidence Anchor tab.
              </p>
            </>
          )}
        </Panel>
      </div>

      <AboutEnvironment />
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

// The notes on this environment, collapsed by default: the legal notice, how to read the records and
// the tracked legal fields
function AboutEnvironment() {
  return (
    <details data-testid="how-to-read" className="group border border-paper-line bg-white">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-4 px-5 py-4 marker:hidden [&::-webkit-details-marker]:hidden">
        <span className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>Notes</span>
          <span className="font-display text-[15px] font-semibold text-graphite">About this environment</span>
          <span className="hidden text-[13px] text-paper-muted md:inline">Internal use only · how to read the records · not legal advice</span>
        </span>
        <span aria-hidden className="font-label text-[12px] text-paper-muted transition-transform group-open:rotate-45">+</span>
      </summary>
      <div className="border-t border-paper-line px-5 py-6">
        <p className="max-w-4xl border-l-2 border-accent pl-4 text-[13.5px] leading-relaxed text-graphite">
          <span className="font-semibold">Internal use only.</span> This platform supports the internal review of
          legal information taken from issuer documents, entered manually or extracted by AI. Nothing here
          constitutes legal advice, regulatory certification or investment guidance; consult qualified legal counsel
          before drawing any legal conclusion.
        </p>
        <section aria-labelledby="how-to-read-heading" className="mt-8">
          <h3 id="how-to-read-heading" className={`${LABEL} text-paper-muted`}>How to read this application</h3>
          <dl className="mt-4 grid grid-cols-1 gap-x-10 gap-y-6 md:grid-cols-2 xl:grid-cols-3">
            {HOW_TO_READ.map(item => (
              <div key={item.title} className="border-t border-paper-line pt-4">
                <dt className="text-[13.5px] font-semibold text-graphite">{item.title}</dt>
                <dd className="mt-1.5 text-[13px] leading-relaxed text-paper-muted">{item.text}</dd>
              </div>
            ))}
          </dl>
        </section>
        <section aria-labelledby="tracked-fields-heading" className="mt-8">
          <h3 id="tracked-fields-heading" className={`${LABEL} text-paper-muted`}>Tracked legal fields</h3>
          <ul className="mt-4 flex flex-wrap gap-2">
            {Object.entries(FIELD_LABELS).map(([key, label]) => (
              <li key={key} className="border border-paper-line px-2.5 py-1.5 text-[12.5px] text-graphite" title={key}>
                {label} <span className="font-label text-[11px] text-paper-muted">{key}</span>
              </li>
            ))}
          </ul>
        </section>
      </div>
    </details>
  )
}
