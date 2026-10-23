import { Fragment, useRef, useState, type ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { DisclaimerBanner } from '../components/DisclaimerBanner'
import { ApprovalStateBadge, ApprovalTypeBadge, EvidenceBadge, ExtractionStatusBadge, STALE_REASON_LABELS } from '../components/StatusBadge'
import { extractionOrigin } from '../aiProcessing'
import { EVIDENCE_SCOPE_NOTE } from '../evidence'
import { LEGAL_FIELD_LABELS } from '../legalFields'
import type { LegalClaim } from '../types'
import { recordById, type DemoPage } from './data'
import { LABEL, LINK, fmtDateTime, paths } from './ui'

// The claims of one extraction record in the demo (pages/ExtractionDetail.tsx): each claim with its cited
// page and exact quotation, the server's evidence result, the source text of exactly that document version
// and the approvals bound to it. Corrections and approvals are product actions and are not offered here.

const label = (field: string) => LEGAL_FIELD_LABELS[field] ?? field

// The quotation as a pattern over the page text: any run of whitespace matches any other
const quotationPattern = (quotation: string) =>
  new RegExp(quotation.trim().split(/\s+/).map(word => word.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).join('\\s+'), 'g')

// A page's text with the quotations of the claims citing it marked; the selected claim's mark stands out
function MarkedText({ text, claims, selected }: { text: string; claims: LegalClaim[]; selected: string | null }) {
  const ranges: { start: number; end: number; claim: LegalClaim }[] = []
  for (const claim of claims) {
    if (!claim.supporting_passage) continue
    for (const match of text.matchAll(quotationPattern(claim.supporting_passage))) {
      ranges.push({ start: match.index!, end: match.index! + match[0].length, claim })
    }
  }
  ranges.sort((a, b) => a.start - b.start)
  const parts: ReactNode[] = []
  let at = 0
  for (const range of ranges) {
    if (range.start < at) continue
    parts.push(text.slice(at, range.start))
    const focused = range.claim.id === selected
    parts.push(
      <mark key={`${range.claim.id}-${range.start}`} data-testid={focused ? 'quotation-focused' : 'quotation-mark'}
            title={label(range.claim.field_name)}
            className={focused ? 'bg-amber-200 text-graphite ring-2 ring-amber-400' : 'bg-accent/15 text-graphite'}>
        {text.slice(range.start, range.end)}
      </mark>,
    )
    at = range.end
  }
  parts.push(text.slice(at))
  return <>{parts}</>
}

function SourceText({ pages, versionNumber, claims, page, onPage, selected, current }: {
  pages: DemoPage[]; versionNumber: number; claims: LegalClaim[]; page: number; onPage: (n: number) => void
  selected: string | null; current: boolean
}) {
  const shown = pages.find(p => p.page_number === page) ?? pages[0]
  const citing = claims.filter(c => !c.is_unknown && c.source_page === shown.page_number)
  return (
    <div className="border border-paper-line bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-paper-line px-5 py-3.5">
        <div className="flex items-baseline gap-3">
          <span className={`${LABEL} text-accent-dark`}>Source</span>
          <h3 className="font-display text-[15px] font-semibold text-graphite">Page text of version {versionNumber}</h3>
        </div>
        <div role="group" aria-label="Pages" className="flex border border-paper-line">
          {pages.map(p => (
            <button key={p.page_number} type="button" aria-pressed={p.page_number === shown.page_number}
                    onClick={() => onPage(p.page_number)}
                    className={`min-w-9 px-2.5 py-1.5 font-label text-[12px] ${p.page_number === shown.page_number ? 'bg-graphite text-mist' : 'text-paper-muted hover:text-graphite'}`}>
              {p.page_number}
            </button>
          ))}
        </div>
      </div>
      <div className="px-5 py-4">
        <p className="mb-3 text-[12px] text-paper-muted">
          The text recorded at upload for exactly this version{current ? '' : ' (superseded)'}; no other version's text is
          substituted. Marked: the quotations that claims cite on page {shown.page_number}.
        </p>
        <pre data-testid="source-page" data-page={shown.page_number}
             className="whitespace-pre-wrap break-words border border-paper-line bg-paper/50 p-4 font-label text-[12.5px] leading-relaxed text-graphite">
          <MarkedText text={shown.text} claims={citing} selected={selected} />
        </pre>
        {citing.length === 0 && <p className="mt-2 text-[12px] text-paper-muted">No claim cites this page.</p>}
      </div>
    </div>
  )
}

// A new record (e.g. the newer version's claims) starts with its own selection
export function ClaimsReview() {
  const { extractionId } = useParams()
  return <ClaimsReviewView key={extractionId} />
}

function ClaimsReviewView() {
  const { assetId, documentId, extractionId } = useParams()
  const record = recordById(assetId)
  const doc = record?.documents.find(d => d.document.id === documentId)
  const extraction = doc?.extractions.find(e => e.id === extractionId)
  const firstCited = extraction?.claims.find(c => !c.is_unknown)
  const [expanded, setExpanded] = useState<string | null>(null)
  const [selected, setSelected] = useState<string | null>(firstCited?.id ?? null)
  const [page, setPage] = useState(firstCited?.source_page ?? 1)
  const source = useRef<HTMLDivElement>(null)

  if (!record || !doc || !extraction) {
    return (
      <div className="border border-paper-line bg-white p-6 text-[14px] text-paper-muted">
        These claims are not part of the demo. <Link to={paths.assets} className={LINK}>Back to the Asset Registry →</Link>
      </div>
    )
  }

  const version = doc.versions.find(v => v.id === extraction.version_id)!
  const isCurrentVersion = version.version_number === doc.document.current_version_number
  const newer = [...doc.extractions].reverse().find(e => e.version_id === doc.document.current_version?.id)
  const approvals = doc.approvals[extraction.id] ?? []
  const origin = extractionOrigin(extraction.ai_model, [], extraction.source)
  const manual = extraction.source === 'manual'
  const known = extraction.claims.filter(c => !c.is_unknown)
  const found = known.filter(c => c.evidence.status === 'verified').length

  const viewPage = (claim: LegalClaim) => {
    setSelected(claim.id)
    setPage(claim.source_page!)
    source.current?.scrollIntoView?.({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="mx-auto max-w-[1400px] space-y-6">
      <nav aria-label="Breadcrumb" className="flex flex-wrap items-center gap-2 text-[13px] text-paper-muted">
        <Link to={paths.assets} className="hover:text-graphite">Assets</Link>
        <span aria-hidden>/</span>
        <Link to={paths.asset(record.asset.id)} className="hover:text-graphite">{record.asset.stellar_asset_code}</Link>
        <span aria-hidden>/</span>
        <span className="font-medium text-graphite">{doc.document.title} v{version.version_number} · claims</span>
      </nav>

      {/* Header */}
      <div className="border border-paper-line bg-white p-6">
        <div className="flex flex-wrap items-start gap-6">
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="font-display text-[22px] font-semibold tracking-[-0.02em] text-graphite">
                {manual ? 'Manually Entered Legal Claims' : 'Recorded Legal Claims'}
              </h2>
              <ExtractionStatusBadge status={extraction.status} />
              <ApprovalStateBadge state={extraction.approval_state.state} />
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[13px] text-paper-muted">
              <span data-testid="demo-extraction-origin"
                    className={`rounded-full px-2 py-0.5 text-xs font-medium ring-1 ring-inset ${manual ? 'bg-indigo-50 text-indigo-700 ring-indigo-200' : 'bg-slate-100 text-slate-700 ring-slate-300'}`}>
                {origin.label}
              </span>
              <span>{manual ? 'Entered' : 'Recorded'}: {fmtDateTime(extraction.extraction_timestamp)}</span>
              <span data-testid="demo-extraction-version">Document version {version.version_number}{isCurrentVersion ? ' (current)' : ' (superseded)'}</span>
            </div>
          </div>
          <dl className="grid grid-cols-4 gap-px border border-paper-line bg-paper-line text-center">
            {[['Fields', extraction.claims.length, 'text-graphite'], ['Quote found', found, 'text-sky-700'],
              ['Not verified', known.length - found, 'text-rose-700'], ['Unknown', extraction.claims.length - known.length, 'text-orange-700']]
              .map(([term, value, tone]) => (
                <div key={term as string} className="bg-white px-4 py-2.5">
                  <dd className={`font-display text-[22px] font-semibold ${tone}`}>{value}</dd>
                  <dt className="text-[11.5px] text-paper-muted">{term}</dt>
                </div>
              ))}
          </dl>
        </div>
      </div>

      {!isCurrentVersion && (
        <div data-testid="demo-superseded-notice" className="border-l-2 border-rose-400 bg-rose-50 px-4 py-3">
          <p className="font-medium text-rose-900">A newer version of this document exists</p>
          <p className="mt-1 text-[13.5px] text-rose-800">
            These claims belong to version {version.version_number}, which was superseded. Their approvals no longer apply and
            they are not part of the current Legal Passport; they remain here for audit only.
          </p>
          {newer && (
            <Link to={paths.claims(record.asset.id, doc.document.id, newer.id)} className="mt-2 inline-block text-[13.5px] font-medium text-rose-900 underline-offset-2 hover:underline">
              Open the claims of version {doc.document.current_version_number} →
            </Link>
          )}
        </div>
      )}

      <DisclaimerBanner text={extraction.disclaimer} />

      {/* Claims */}
      <div className="border border-paper-line bg-white">
        <div className="border-b border-paper-line px-5 py-4">
          <h3 className="font-display text-[16px] font-semibold text-graphite">Legal Claims</h3>
          <p className="mt-1 text-[13px] leading-relaxed text-paper-muted">
            Seven legal fields. A known claim cites a page and an exact quotation; an unknown claim asserts nothing. {EVIDENCE_SCOPE_NOTE}
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[760px] text-left">
            <thead>
              <tr className={`${LABEL} text-paper-muted`}>
                <th scope="col" className="px-5 py-3 font-normal">Field</th>
                <th scope="col" className="px-3 py-3 font-normal">Value</th>
                <th scope="col" className="px-3 py-3 font-normal">Evidence</th>
                <th scope="col" className="px-3 py-3 font-normal">Source</th>
                <th scope="col" className="px-5 py-3 font-normal"><span className="sr-only">Details</span></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-paper-line border-t border-paper-line">
              {extraction.claims.map(claim => {
                const open = expanded === claim.id
                return (
                  <Fragment key={claim.id}>
                    <tr data-testid={`demo-claim-${claim.field_name}`} className={`align-top ${claim.id === selected ? 'bg-amber-50/60' : ''}`}>
                      <td className="px-5 py-4">
                        <p className="text-[13.5px] font-medium text-graphite">{label(claim.field_name)}</p>
                        <p className="font-label text-[11px] text-paper-muted">{claim.field_name}</p>
                      </td>
                      <td className="max-w-xs px-3 py-4">
                        <p className={`text-[13.5px] ${claim.is_unknown ? 'italic text-paper-muted' : 'text-graphite'}`}>{claim.value}</p>
                      </td>
                      <td className="px-3 py-4"><EvidenceBadge claim={claim} /></td>
                      <td className="px-3 py-4">
                        {claim.is_unknown ? <span className="text-xs text-paper-muted">—</span> : (
                          <div>
                            <p className="text-xs text-graphite">
                              Page {claim.source_page}
                              <button type="button" onClick={() => viewPage(claim)}
                                      aria-label={`View page ${claim.source_page} of the source text for ${label(claim.field_name)}`}
                                      className="ml-2 font-medium text-accent-dark hover:text-graphite">
                                View page
                              </button>
                            </p>
                            <p className="mt-0.5 max-w-sm truncate text-xs text-paper-muted" title={claim.supporting_passage ?? ''}>
                              “{claim.supporting_passage}”
                            </p>
                          </div>
                        )}
                      </td>
                      <td className="px-5 py-4 text-right">
                        <button type="button" aria-expanded={open} aria-controls={`claim-details-${claim.id}`}
                                onClick={() => setExpanded(open ? null : claim.id)}
                                className="border border-paper-line px-2.5 py-1 text-xs text-graphite hover:border-graphite/40">
                          {open ? 'Hide' : 'Details'}<span className="sr-only"> for {label(claim.field_name)}</span>
                        </button>
                      </td>
                    </tr>
                    {open && (
                      <tr id={`claim-details-${claim.id}`} className="bg-paper/40">
                        <td colSpan={5} className="px-5 py-4">
                          <dl className="grid gap-4 text-[12.5px] md:grid-cols-[1fr_1fr]">
                            <div>
                              <dt className={`${LABEL} text-paper-muted`}>Supporting quotation</dt>
                              <dd className="mt-1.5 border-l-2 border-accent/50 pl-3 italic text-graphite">
                                {claim.supporting_passage ?? 'None: an unknown claim cites no page and no quotation.'}
                              </dd>
                            </div>
                            <div>
                              <dt className={`${LABEL} text-paper-muted`}>Server check</dt>
                              <dd className="mt-1.5 text-graphite">{claim.evidence.explanation}</dd>
                              <dd className="mt-1 text-paper-muted">
                                Checked against version {claim.evidence.version_number} ({claim.evidence.page_count} pages) ·
                                method {claim.evidence.verification_method} · scope {claim.evidence.scope}
                                {claim.evidence.found_on_pages.length > 0 && ` · found on page ${claim.evidence.found_on_pages.join(', ')}`}
                              </dd>
                            </div>
                          </dl>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      </div>

      <div ref={source} className="scroll-mt-4">
        <SourceText pages={doc.pages[version.id]} versionNumber={version.version_number} claims={extraction.claims}
                    page={page} onPage={setPage} selected={selected} current={isCurrentVersion} />
      </div>

      {/* Approvals */}
      <div className="border border-paper-line bg-white">
        <div className="border-b border-paper-line px-5 py-4">
          <h3 className="font-display text-[16px] font-semibold text-graphite">Approval Record</h3>
          <p className="mt-1 text-[13px] text-paper-muted">
            Internal attestations bound to version {version.version_number}: its SHA-256, its page text and the evidence of every claim.
            They are not legal opinions. Reviewers appear by role only.
          </p>
        </div>
        {approvals.length === 0 ? (
          <p data-testid="demo-no-approvals" className="px-5 py-5 text-[13.5px] text-paper-muted">
            Not approved yet. {extraction.status === 'under_review' ? 'The review has started; ' : ''}an issuer attestation and an
            independent legal review are required before these claims can be served in a Legal Passport.
          </p>
        ) : (
          <ul className="divide-y divide-paper-line">
            {approvals.map(a => (
              <li key={a.approval_type} data-testid="demo-approval" className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-5 py-3.5">
                <ApprovalTypeBadge type={a.approval_type} />
                <span className={`text-xs font-medium ${a.is_current ? 'text-green-700' : 'text-rose-700'}`}>
                  {a.is_current ? 'Current' : 'No longer current'}
                </span>
                <span className="text-xs text-paper-muted">{a.reviewer_role === 'issuer_reviewer' ? 'Issuer Reviewer' : 'Independent Legal Reviewer'}</span>
                <span className="text-xs text-paper-muted">{fmtDateTime(a.approved_at)}</span>
                <span className="font-label text-[11.5px] text-paper-muted" title={a.binding_digest}>binding {a.binding_digest.slice(0, 12)}…</span>
                {a.reasons.length > 0 && (
                  <span className="w-full text-xs text-amber-800">Why: {a.reasons.map(r => STALE_REASON_LABELS[r] ?? r).join('; ')}</span>
                )}
              </li>
            ))}
          </ul>
        )}
        <p data-testid="demo-review-read-only" className="border-t border-paper-line px-5 py-3 text-[12.5px] text-paper-muted">
          Read-only demo: corrections, reviews and approvals cannot be given here. The status, evidence and approvals are shown as recorded.
        </p>
      </div>
    </div>
  )
}
