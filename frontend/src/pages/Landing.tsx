import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { BrandMark as LogoMark } from '../components/BrandMark'
import { DeploymentBanner } from '../components/Layout'
import { PUBLIC_SITE } from '../site'
import { EvidenceAnchorCard } from '../components/EvidenceAnchorCard'
import { PassportHistoryCard } from '../components/PassportHistoryCard'
import { EvidenceGraph } from '../landing/EvidenceGraph'
import { DEMO_HISTORY, DEMO_LEDGER_V2, DEMO_TX_V2, DEMO_VERIFICATION } from '../landing/demoRecord'
import { testnetExplorerTx } from '../anchor'

// The public website of RWA Legal Passport, at "/". The application itself starts at /dashboard. Every
// statement describes what the platform implements; there are no customers, metrics or contacts here,
// and nothing promises legal certainty, ownership proof or compliance.

const CONTAINER = 'mx-auto w-full max-w-[1240px] px-6 sm:px-8 lg:px-12'
const SECTION = 'py-24 lg:py-32'
const FOCUS = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent-light'
const FOCUS_LIGHT = 'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent-dark'
const BUTTON = 'group inline-flex items-center justify-center gap-3 px-6 py-3.5 font-body text-[14px] font-medium transition-colors'
const H2 = 'font-display text-[clamp(2rem,3.6vw,3.25rem)] font-semibold leading-[1.05] tracking-[-0.032em] text-balance'
const LABEL = 'font-label text-[11px] uppercase tracking-[0.16em]'

const NAV_LINKS = [
  { href: '#platform', label: 'Platform' },
  { href: '#how-it-works', label: 'Technology' },
  { href: '#use-cases', label: 'Use Cases' },
]

const pad = (n: number) => String(n).padStart(2, '0')

function Arrow({ up = false }: { up?: boolean }) {
  return (
    <span aria-hidden className={`inline-block transition-transform ${up ? 'group-hover:-translate-y-0.5' : 'group-hover:translate-x-0.5'}`}>
      {up ? '↑' : '→'}
    </span>
  )
}

function Eyebrow({ children, tone }: { children: ReactNode; tone: 'dark' | 'light' }) {
  return (
    <p className={`flex items-center gap-3 font-label text-[11px] uppercase tracking-[0.2em] ${
      tone === 'dark' ? 'text-accent-light' : 'text-accent-dark'}`}>
      <span aria-hidden className="h-[7px] w-[7px] bg-current" />
      {children}
    </p>
  )
}

// The opening of every section: the eyebrow and title on the left, the lead on the right
function SectionHeader({ id, eyebrow, title, lead, tone }: {
  id: string; eyebrow: string; title: string; lead: ReactNode; tone: 'dark' | 'light'
}) {
  return (
    <div className="grid gap-8 lg:grid-cols-12 lg:items-end lg:gap-10">
      <div className="lg:col-span-7">
        <Eyebrow tone={tone}>{eyebrow}</Eyebrow>
        <h2 id={id} className={`mt-7 ${H2}`}>{title}</h2>
      </div>
      <p className={`font-body text-[17px] leading-relaxed text-pretty lg:col-span-4 lg:col-start-9 ${
        tone === 'dark' ? 'text-ink-soft' : 'text-paper-muted'}`}>
        {lead}
      </p>
    </div>
  )
}

// The way into the product: the application itself, or on the public website (site.ts), which carries
// no application, the product section of this page
function EntryLink({ className, onClick }: { className: string; onClick?: () => void }) {
  return PUBLIC_SITE
    ? <a href="#product" onClick={onClick} className={className}>View Product <Arrow /></a>
    : <Link to="/dashboard" onClick={onClick} className={className}>Launch App <Arrow /></Link>
}

function SiteHeader() {
  const [open, setOpen] = useState(false)
  return (
    <header className="sticky top-0 z-50 border-b border-white/[0.07] bg-ink text-mist">
      <div className={`${CONTAINER} flex h-[72px] items-center justify-between`}>
        <a href="#top" className={`flex items-center gap-3 ${FOCUS}`} aria-label="RWA Legal Passport, back to top">
          <LogoMark />
          <span className="font-display text-[15px] font-semibold tracking-[-0.01em]">RWA Legal Passport</span>
        </a>
        <nav aria-label="Primary" className="hidden items-center gap-9 md:flex">
          {NAV_LINKS.map(link => (
            <a key={link.href} href={link.href} className={`font-body text-[14px] text-ink-soft transition-colors hover:text-mist ${FOCUS}`}>
              {link.label}
            </a>
          ))}
          <EntryLink className={`${BUTTON} border border-white/20 !px-4 !py-2 text-mist hover:border-mist hover:bg-mist hover:text-ink ${FOCUS}`} />
        </nav>
        <button
          type="button"
          onClick={() => setOpen(o => !o)}
          aria-expanded={open}
          aria-controls="site-menu"
          className={`-mr-2 p-2 text-mist md:hidden ${FOCUS}`}
        >
          <span className="sr-only">{open ? 'Close menu' : 'Open menu'}</span>
          <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden fill="none" stroke="currentColor" strokeWidth={1.5}>
            {open ? <path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" /> : <path d="M4 8h16M4 16h16" strokeLinecap="round" />}
          </svg>
        </button>
      </div>
      {open && (
        <nav id="site-menu" aria-label="Menu" className="border-t border-white/[0.07] md:hidden">
          <ul className={`${CONTAINER} flex flex-col py-4`}>
            {NAV_LINKS.map(link => (
              <li key={link.href}>
                <a href={link.href} onClick={() => setOpen(false)} className={`block py-3 font-body text-[15px] text-ink-soft hover:text-mist ${FOCUS}`}>
                  {link.label}
                </a>
              </li>
            ))}
            <li className="pt-3">
              <EntryLink className={`${BUTTON} w-full bg-mist text-ink ${FOCUS}`} onClick={() => setOpen(false)} />
            </li>
          </ul>
        </nav>
      )}
    </header>
  )
}

const HERO_OUTCOMES = [
  ['Less due diligence friction', 'Reviewers start from an organised record, not a data room.'],
  ['A readable legal position', 'What is approved today, and why anything else is not.'],
  ['Changes in plain sight', 'Every new document version, and the approvals it affects.'],
]

const STEP_NAMES = ['Register asset', 'Attach evidence', 'Review and approve', 'Verify integrity']
const AUDIENCE_NAMES = ['Tokenization platforms', 'Asset issuers', 'Custodians', 'Institutional investors']

function Hero() {
  return (
    <section aria-labelledby="hero-title" className="relative overflow-hidden bg-ink text-mist">
      {/* A fine architectural grid, fading out towards the bottom */}
      <div aria-hidden className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgba(255,255,255,0.035)_1px,transparent_1px),linear-gradient(to_bottom,rgba(255,255,255,0.035)_1px,transparent_1px)] bg-[size:80px_80px] [-webkit-mask-image:linear-gradient(to_bottom,black,transparent_90%)] [mask-image:linear-gradient(to_bottom,black,transparent_90%)]" />
      <div className={`${CONTAINER} relative pb-20 pt-20 lg:pb-24 lg:pt-28`}>
        <Eyebrow tone="dark">Legal infrastructure for tokenized assets</Eyebrow>
        {/* Full width and balanced: two even lines on wide screens */}
        <h1 id="hero-title" className="mt-8 max-w-[1100px] font-display text-[clamp(2.75rem,5.2vw,4.75rem)] font-semibold leading-[1.02] tracking-[-0.04em] text-balance">
          The legal evidence layer for real-world assets.
        </h1>

        <div className="mt-14 grid gap-14 lg:mt-16 lg:grid-cols-12 lg:gap-10">
          <div className="lg:col-span-5">
            <p className="max-w-xl font-body text-lg leading-relaxed text-ink-soft text-pretty sm:text-xl">
              Give every tokenized asset one reviewable legal record: its documents, the claims drawn from them, who
              approved them and what has changed since — with an integrity record anchored to Stellar.
            </p>
            <div className="mt-10 flex flex-wrap gap-3">
              <a href="#platform" className={`${BUTTON} bg-mist text-ink hover:bg-white ${FOCUS}`}>Explore Platform <Arrow /></a>
              <a href="#how-it-works" className={`${BUTTON} border border-white/20 text-mist hover:border-white/60 ${FOCUS}`}>How It Works</a>
            </div>
            <ul className="mt-14 divide-y divide-white/10 border-y border-white/10">
              {HERO_OUTCOMES.map(([title, text]) => (
                <li key={title} className="flex gap-4 py-4">
                  <span aria-hidden className="mt-[10px] h-px w-4 shrink-0 bg-accent-light" />
                  <p className="font-body text-[15px] leading-snug">
                    <span className="text-mist">{title}.</span> <span className="text-ink-muted">{text}</span>
                  </p>
                </li>
              ))}
            </ul>
          </div>

          <figure className="hidden sm:block lg:col-span-7">
            <div className={`flex items-center justify-between border border-b-0 border-white/10 px-5 py-3 ${LABEL} text-ink-muted`}>
              <span>One asset · one record</span>
              <span>Demo record · Stellar Testnet</span>
            </div>
            <div className="border border-white/10 bg-ink-raised/60 p-3 sm:p-5">
              <EvidenceGraph />
            </div>
            <figcaption className="mt-4 flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
              <ol className={`flex flex-wrap gap-x-5 gap-y-2 ${LABEL} text-ink-muted`}>
                {STEP_NAMES.map((name, i) => (
                  <li key={name}><span className="text-accent-light">{pad(i + 1)}</span> {name}</li>
                ))}
              </ol>
              <a href="#how-it-works" className={`group inline-flex items-center gap-2 font-body text-[13px] text-ink-soft hover:text-mist ${FOCUS}`}>
                How each step works <Arrow />
              </a>
            </figcaption>
          </figure>
        </div>

        <div className="mt-20 grid gap-4 border-t border-white/10 pt-6 lg:grid-cols-12 lg:items-center lg:gap-10">
          <p className={`${LABEL} text-ink-muted lg:col-span-2`}>Designed for</p>
          <ul className="flex flex-wrap gap-x-8 gap-y-2 font-body text-[15px] text-mist/90 lg:col-span-10">
            {AUDIENCE_NAMES.map(name => (
              <li key={name}><a href="#use-cases" className={`hover:text-white ${FOCUS}`}>{name}</a></li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  )
}

const COMPARISON = [
  {
    friction: 'Static records',
    today: 'A token is issued once, but its legal documents keep changing. A file in a data room says nothing about what changed since.',
    withPassport: 'Every document version is kept with its SHA-256, and the record always shows which version is current.',
  },
  {
    friction: 'Fragmented documents',
    today: 'Prospectuses, opinions and filings sit in different places, with no shared reference to the asset they describe.',
    withPassport: 'All legal evidence is registered against the asset itself, identified by its Stellar asset code and issuer.',
  },
  {
    friction: 'Unverifiable claims',
    today: 'Summaries restate the terms, but nobody can tell which page of which version they come from.',
    withPassport: 'Every claim cites a page and an exact quotation, checked against the text of that version.',
  },
  {
    friction: 'Disconnected approvals',
    today: 'Sign-offs are filed apart from the version they covered, so nobody can tell whether they still apply.',
    withPassport: 'Approvals are bound to one version. A new version supersedes them until it is reviewed again.',
  },
]

function Problem() {
  return (
    <section id="problem" aria-labelledby="problem-title" className="scroll-mt-[72px] bg-paper text-graphite">
      <div className={`${CONTAINER} ${SECTION}`}>
        <SectionHeader id="problem-title" tone="light" eyebrow="The problem" title="Tokenization moves fast. Legal clarity should too."
          lead="Assets can be issued on-chain in minutes. The legal position behind them still lives in static files, scattered folders and email threads, so every review begins by reconstructing a trail that should already exist." />

        <div className="mt-16 border-t border-graphite" role="table" aria-label="The legal record today, and with RWA Legal Passport">
          <div role="row" className={`hidden grid-cols-12 gap-6 border-b border-paper-line py-4 lg:grid ${LABEL}`}>
            <span role="columnheader" className="col-span-3 text-paper-muted">Friction</span>
            <span role="columnheader" className="col-span-4 text-paper-muted">Today</span>
            <span role="columnheader" className="col-span-5 text-accent-dark">With RWA Legal Passport</span>
          </div>
          {COMPARISON.map((row, i) => (
            <div key={row.friction} role="row" className="grid gap-4 border-b border-paper-line py-8 lg:grid-cols-12 lg:gap-6">
              <div role="rowheader" className="flex items-baseline gap-4 lg:col-span-3">
                <span className="font-label text-[11px] tracking-[0.16em] text-accent-dark">{pad(i + 1)}</span>
                <h3 className="font-display text-xl font-semibold tracking-[-0.01em]">{row.friction}</h3>
              </div>
              <div role="cell" className="lg:col-span-4">
                <p className={`${LABEL} mb-2 text-paper-muted lg:hidden`}>Today</p>
                <p className="font-body text-[15px] leading-relaxed text-paper-muted text-pretty">{row.today}</p>
              </div>
              <div role="cell" className="lg:col-span-5">
                <p className={`${LABEL} mb-2 text-accent-dark lg:hidden`}>With RWA Legal Passport</p>
                <p className="flex gap-3 font-body text-[15px] leading-relaxed text-graphite text-pretty">
                  <span aria-hidden className="mt-[10px] h-px w-4 shrink-0 bg-accent" />
                  {row.withPassport}
                </p>
              </div>
            </div>
          ))}
        </div>
        <p className="mt-12 max-w-3xl font-display text-[clamp(1.375rem,2.2vw,1.75rem)] font-medium leading-snug tracking-[-0.015em] text-balance">
          Due diligence starts from a current, organised record — not from a reconstruction.
        </p>
      </div>
    </section>
  )
}

const CAPABILITIES = [
  {
    id: 'evidence-registry',
    title: 'Evidence Registry',
    lead: 'Source documents, the claims drawn from them, and the exact page and quotation behind every claim.',
    matters: 'Any reviewer can go from a claim to the page that supports it, without searching a data room.',
    points: [
      'Every PDF version stored with its SHA-256',
      'The page-level text of every version',
      'Claims tied to a cited page and an exact quotation',
      'Quotations checked deterministically against the cited page',
    ],
    footer: 'sha256 · page · quotation',
  },
  {
    id: 'legal-change-tracking',
    title: 'Legal Change Tracking',
    lead: 'Every document version and every approval, with the reason an approval stopped applying.',
    matters: 'Teams can tell whether an approval still applies, without comparing documents by hand.',
    points: [
      'Approvals bound to one exact version and its evidence',
      'A new version supersedes earlier approvals until re-review',
      'Issuer attestation and independent legal review kept apart',
      'A full history of what changed and what needs reapproval',
    ],
    footer: 'version · binding · re-review',
  },
  {
    id: 'verifiable-records',
    title: 'Verifiable Records',
    lead: 'An audit history and an integrity commitment that institutions can check for themselves.',
    matters: 'Institutions receive evidence they can check, not only a summary they have to accept.',
    points: [
      'A scoped, read-only Legal Passport API for institutions',
      'A canonical evidence manifest per asset, identified by its SHA-256',
      'Manifest digests anchored on Stellar Testnet',
      'Live checks against Stellar Horizon; changed evidence is never shown as verified',
    ],
    footer: 'manifest · anchor · verification',
  },
]

const VALUES = [
  ['Operational visibility', 'The current legal record of every asset, and its full history, in one place.'],
  ['Workflow clarity', 'Reviewers approve exact versions; a document change starts the re-review by itself.'],
  ['Evidence traceability', 'From any claim back to its page, its document version and its approvals.'],
]

function Platform() {
  return (
    <section id="platform" aria-labelledby="platform-title" className="scroll-mt-[72px] bg-ink text-mist">
      <div className={`${CONTAINER} ${SECTION}`}>
        <SectionHeader id="platform-title" tone="dark" eyebrow="Platform" title="One legal evidence record for every asset."
          lead="Documents, claims, approvals and their integrity commitment stay linked, so legal, risk and operations teams read the same current record, and can trace any part of it back to its source." />
        <div className="mt-20 grid border-t border-white/10 lg:grid-cols-3">
          {CAPABILITIES.map((capability, i) => (
            <article key={capability.id} id={capability.id} aria-labelledby={`${capability.id}-title`}
                     className="flex scroll-mt-[96px] flex-col border-b border-white/10 py-12 lg:border-b-0 lg:border-r lg:px-10 lg:first:pl-0 lg:last:border-r-0 lg:last:pr-0">
              <p className={LABEL}>
                <span className="text-accent-light">{pad(i + 1)}</span>
                <span className="text-ink-muted"> / Capability</span>
              </p>
              <h3 id={`${capability.id}-title`} className="mt-8 font-display text-[28px] font-semibold leading-tight tracking-[-0.02em]">
                {capability.title}
              </h3>
              <p className="mt-4 font-body text-[16px] leading-relaxed text-ink-soft text-pretty">{capability.lead}</p>
              <div className="mt-8 border-l border-accent pl-4">
                <p className={`${LABEL} text-accent-light`}>Why it matters</p>
                <p className="mt-2 font-body text-[15px] leading-relaxed text-mist text-pretty">{capability.matters}</p>
              </div>
              <ul className="mt-8 space-y-3.5">
                {capability.points.map(point => (
                  <li key={point} className="flex gap-4 font-body text-[15px] leading-snug text-mist/85">
                    <span aria-hidden className="mt-[9px] h-px w-4 shrink-0 bg-white/30" />
                    {point}
                  </li>
                ))}
              </ul>
              <p className="mt-auto pt-10">
                <span className="block border-t border-white/10 pt-4 font-label text-[11px] tracking-[0.12em] text-ink-muted">{capability.footer}</span>
              </p>
            </article>
          ))}
        </div>
        <dl className="mt-16 grid gap-px bg-white/10 sm:grid-cols-3">
          {VALUES.map(([term, text]) => (
            <div key={term} className="bg-ink py-8 sm:px-8 sm:first:pl-0">
              <dt className={`${LABEL} text-accent-light`}>{term}</dt>
              <dd className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft text-pretty">{text}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  )
}

const STEPS = [
  { title: 'Register Asset', who: 'Platform operator', text: 'An asset is registered with its Stellar asset code and issuer key, both strictly validated, under its issuer.', label: 'POST /assets' },
  { title: 'Attach Legal Evidence', who: 'Issuer', text: 'Legal documents are uploaded as versioned PDFs. Each claim cites a page and quotes it exactly; the server checks the quotation.', label: 'POST /assets/{id}/documents' },
  { title: 'Review & Track Changes', who: 'Issuer and independent legal reviewer', text: 'The issuer attests and an independent legal reviewer approves. A new document version supersedes both until it is reviewed again.', label: 'POST /extractions/{id}/approvals' },
  { title: 'Verify Evidence Integrity', who: 'Issuer anchors · institutions verify', text: 'The evidence manifest digest is anchored on Stellar Testnet and checked again against live ledger data.', label: 'GET /passport/…/stellar-anchor' },
]

const LINEAGE = ['asset', 'document_version.sha256', 'claim.page + quotation', 'approval.binding_digest', 'manifest.sha256', 'stellar.memo_hash']

function HowItWorks() {
  return (
    <section id="how-it-works" aria-labelledby="how-title" className="scroll-mt-[72px] bg-paper text-graphite">
      <div className={`${CONTAINER} ${SECTION}`}>
        <SectionHeader id="how-title" tone="light" eyebrow="How it works" title="From source document to verifiable record."
          lead="A workflow that legal and operations teams already recognise — register, document, review, verify — in which every step leaves a record the next one builds on." />

        <ol className="relative mt-20 grid gap-14 lg:grid-cols-4 lg:gap-0">
          <span aria-hidden className="absolute bottom-0 left-[19px] top-0 w-px bg-paper-line lg:bottom-auto lg:left-0 lg:right-0 lg:top-[19px] lg:h-px lg:w-auto" />
          {STEPS.map((step, i) => (
            <li key={step.title} className="relative flex flex-col pl-16 lg:pl-0 lg:pr-10">
              <span className={`absolute left-0 top-0 grid h-10 w-10 place-items-center border bg-paper font-label text-[12px] lg:static ${
                i === STEPS.length - 1 ? 'border-accent text-accent-dark' : 'border-graphite text-graphite'}`}>
                {pad(i + 1)}
              </span>
              <p className={`${LABEL} text-accent-dark lg:mt-8`}>Step {pad(i + 1)}</p>
              <h3 className="mt-3 font-display text-2xl font-semibold leading-tight tracking-[-0.02em]">{step.title}</h3>
              <p className="mt-2 font-body text-[13px] text-graphite/70">{step.who}</p>
              <p className="mt-4 font-body text-[15px] leading-relaxed text-paper-muted text-pretty">{step.text}</p>
              <code className="mt-6 self-start border border-paper-line bg-white/70 px-2.5 py-1.5 font-label text-[11.5px] text-graphite">{step.label}</code>
            </li>
          ))}
        </ol>

        <div className="mt-20 border border-paper-line bg-white/60 p-6 lg:p-8">
          <p className={`${LABEL} text-paper-muted`}>Data lineage</p>
          <ol className="mt-5 flex flex-wrap items-center gap-x-3 gap-y-3 font-label text-[12px] text-graphite">
            {LINEAGE.map((node, i) => (
              <li key={node} className="flex items-center gap-3">
                <span className="border border-paper-line bg-paper px-2.5 py-1.5">{node}</span>
                {i < LINEAGE.length - 1 && <span aria-hidden className="text-accent-dark">→</span>}
              </li>
            ))}
          </ol>
          <p className="mt-5 max-w-3xl font-body text-[15px] leading-relaxed text-paper-muted text-pretty">
            Each link is checked again on every read. A changed file, page text, claim or asset field makes the
            approvals that depend on it stale, and the passport stops presenting them as current.
          </p>
        </div>
      </div>
    </section>
  )
}

const PREVIEW_NOTES = [
  ['Version history', 'Version 1 superseded, version 2 current, with the reason in plain language.'],
  ['Approval state', 'Which approvals stand behind the current version, and which no longer apply.'],
  ['Integrity check', 'Whether the evidence still matches its Stellar Testnet commitment.'],
]

function ProductPreview() {
  return (
    <section id="product" aria-labelledby="product-title" className="scroll-mt-[72px] bg-ink text-mist">
      <div className={`${CONTAINER} ${SECTION}`}>
        <SectionHeader id="product-title" tone="dark" eyebrow="Product" title="See an asset's legal position at a glance."
          lead="What changed, what is approved and whether the evidence still matches its commitment, on one screen. Shown here with the application's own components and the public demo record." />

        <figure className="mt-16">
          <div className="border border-white/10 bg-ink-raised">
            <div className={`flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-3 sm:px-5 ${LABEL} text-ink-muted`}>
              <span>/passport · LPDEMO</span>
              <span>Demo dataset · synthetic · Stellar Testnet</span>
            </div>
            <div className="grid items-start gap-6 bg-slate-50 p-4 text-slate-900 sm:p-6 lg:grid-cols-2 lg:p-8">
              <PassportHistoryCard history={DEMO_HISTORY} />
              {/* As tall as the history beside it on large screens */}
              <div className="relative lg:self-stretch lg:overflow-hidden">
                <div className="lg:absolute lg:inset-0">
                  <EvidenceAnchorCard verification={DEMO_VERIFICATION} />
                </div>
                <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 hidden h-28 bg-gradient-to-t from-slate-50 to-transparent lg:block" />
              </div>
            </div>
          </div>
          <ol className="mt-8 grid gap-6 sm:grid-cols-3">
            {PREVIEW_NOTES.map(([title, text], i) => (
              <li key={title} className="border-t border-white/10 pt-5">
                <p className={LABEL}><span className="text-accent-light">{pad(i + 1)}</span> <span className="text-mist">{title}</span></p>
                <p className="mt-2 font-body text-[14px] leading-relaxed text-ink-soft text-pretty">{text}</p>
              </li>
            ))}
          </ol>
          <figcaption className="mt-10 flex flex-col gap-4 border-t border-white/10 pt-6 font-body text-[14px] leading-relaxed text-ink-muted sm:flex-row sm:items-start sm:justify-between">
            <span className="max-w-3xl">
              A snapshot of the public demo record, checked against Stellar Testnet on 19 October 2026 (UTC). The
              documents, the issuer and the reviewers are synthetic. An approval is an internal attestation, not a
              legal opinion.
            </span>
            {!PUBLIC_SITE && (
              <Link to="/passport" className={`group inline-flex shrink-0 items-center gap-2 text-mist hover:text-white ${FOCUS}`}>
                Open the Legal Passport lookup <Arrow />
              </Link>
            )}
          </figcaption>
        </figure>
      </div>
    </section>
  )
}

const AUDIENCES = [
  {
    title: 'Tokenization platforms',
    text: 'List every asset with a legal evidence record that institutions can review, and show when its documents change and whether the change has been reviewed.',
    uses: ['Asset registry', 'Passport API', 'History'],
  },
  {
    title: 'Asset issuers',
    text: 'Publish amendments with a clear trail. Counterparties see what changed, which claims were entered again, and that your attestation and an independent legal review stand behind them.',
    uses: ['Version upload', 'Manual claims', 'Issuer attestation'],
  },
  {
    title: 'Custodians',
    text: 'Before onboarding or servicing an asset, confirm that its legal evidence is current, approved and unchanged since it was committed.',
    uses: ['Approval state', 'Integrity check'],
  },
  {
    title: 'Institutional investors',
    text: 'Start due diligence from the current, approved record, read through a scoped API, with its history and an integrity check you can run yourself.',
    uses: ['X-API-Key', 'Read-only', 'Scoped'],
  },
]

function Institutions() {
  return (
    <section id="use-cases" aria-labelledby="use-cases-title" className="scroll-mt-[72px] bg-paper text-graphite">
      <div className={`${CONTAINER} ${SECTION}`}>
        <SectionHeader id="use-cases-title" tone="light" eyebrow="Built for institutions" title="One evidence record. Four operational views."
          lead="Everyone who relies on the legal position of a tokenized asset reads the same record, each with the access their role requires." />
        <div className="mt-16 border-t border-graphite">
          <div className={`hidden grid-cols-12 gap-6 border-b border-paper-line py-4 lg:grid ${LABEL} text-paper-muted`}>
            <span className="col-span-5">Who</span>
            <span className="col-span-5">What it gives you</span>
            <span className="col-span-2">Uses</span>
          </div>
          {AUDIENCES.map((audience, i) => (
            <article key={audience.title} aria-labelledby={`audience-${i}`}
                     className="grid gap-4 border-b border-paper-line py-10 lg:grid-cols-12 lg:gap-6">
              <div className="flex items-baseline gap-4 lg:col-span-5">
                <span className="font-label text-[11px] tracking-[0.16em] text-accent-dark">{pad(i + 1)}</span>
                <h3 id={`audience-${i}`} className="font-display text-[26px] font-semibold leading-tight tracking-[-0.02em]">{audience.title}</h3>
              </div>
              <p className="font-body text-[16px] leading-relaxed text-paper-muted text-pretty lg:col-span-5">{audience.text}</p>
              <ul className="flex flex-wrap gap-x-4 gap-y-1.5 font-label text-[11px] uppercase tracking-[0.12em] text-graphite/70 lg:col-span-2 lg:flex-col lg:pt-1.5">
                {audience.uses.map(use => <li key={use}>{use}</li>)}
              </ul>
            </article>
          ))}
        </div>
        <p className="mt-8 font-body text-[13px] text-paper-muted">
          These are the roles the platform is designed for, not a list of customers.
        </p>
      </div>
    </section>
  )
}

const ANCHOR_SPEC = [
  ['Network', 'Stellar Testnet. Mainnet anchoring is disabled.'],
  ['Commitment', 'The SHA-256 of the canonical evidence manifest. The manifest stays off-chain.'],
  ['Transaction', <>A memo hash and a <code className="font-label text-[13px]">manage_data</code> entry <code className="font-label text-[13px]">lp:&lt;asset id&gt;</code>, both equal to the digest.</>],
  ['Submission', 'Recorded before it is sent; a confirmed or pending digest is never submitted again.'],
  ['Signing', 'A server-side Testnet account. No signing key ever reaches the browser.'],
] as const

const CHECKS = [
  'Horizon is on Stellar Testnet',
  'The transaction exists and succeeded',
  'Sent by the trusted anchoring account',
  'The memo hash equals the digest',
  'The data entry equals the digest',
  'The current evidence has the same digest',
]

function Stellar() {
  return (
    <section id="stellar" aria-labelledby="stellar-title" className="scroll-mt-[72px] bg-ink text-mist">
      <div className={`${CONTAINER} grid gap-16 ${SECTION} lg:grid-cols-12 lg:gap-10`}>
        <div className="lg:col-span-5">
          <Eyebrow tone="dark">Stellar infrastructure</Eyebrow>
          <h2 id="stellar-title" className={`mt-7 ${H2}`}>A public commitment to private evidence.</h2>
          <p className="mt-8 font-body text-[17px] leading-relaxed text-ink-soft text-pretty">
            Counterparties should not have to rely on a screenshot. The platform commits the SHA-256 digest of an
            asset's evidence manifest to Stellar Testnet. The evidence itself stays with the platform; anyone with
            access to the record can check that it has not changed since it was committed.
          </p>
          <div className="mt-10 border-l-2 border-accent pl-6">
            <p className={`${LABEL} text-accent-light`}>What an anchor does not prove</p>
            <p className="mt-3 font-body text-[15px] leading-relaxed text-ink-soft text-pretty">
              A Stellar transaction shows that the anchoring account recorded a digest at a ledger time. It does not
              establish legal validity, ownership, regulatory compliance or the authenticity of documents. Stellar
              Testnet is reset periodically.
            </p>
          </div>
        </div>
        <div className="lg:col-span-6 lg:col-start-7">
          <dl className="border-t border-white/10">
            {ANCHOR_SPEC.map(([term, detail]) => (
              <div key={term} className="grid gap-2 border-b border-white/10 py-5 sm:grid-cols-[160px_1fr] sm:gap-6">
                <dt className={`${LABEL} text-ink-muted sm:pt-1`}>{term}</dt>
                <dd className="font-body text-[15px] leading-relaxed text-mist">{detail}</dd>
              </div>
            ))}
          </dl>
          <h3 className={`mt-12 ${LABEL} text-ink-muted`}>Verified only if all of these hold</h3>
          <ul className="mt-5 grid gap-x-8 gap-y-3 sm:grid-cols-2">
            {CHECKS.map(check => (
              <li key={check} className="flex gap-3 font-body text-[15px] text-mist/90">
                <span aria-hidden className="mt-[3px] grid h-4 w-4 shrink-0 place-items-center border border-accent text-[10px] leading-none text-accent-light">✓</span>
                {check}
              </li>
            ))}
          </ul>
          <p className="mt-6 font-body text-[14px] leading-relaxed text-ink-muted">
            Otherwise the result is evidence changed, pending, failed, not anchored or network unavailable — and never verified.
          </p>
          <a href={testnetExplorerTx(DEMO_TX_V2)} target="_blank" rel="noopener noreferrer"
             className={`group mt-10 flex flex-col gap-2 border border-white/10 px-5 py-4 transition-colors hover:border-white/30 sm:flex-row sm:items-center sm:justify-between ${FOCUS}`}>
            <span className="font-body text-[14px] text-ink-soft">Demo record · LPDEMO version 2 · ledger {DEMO_LEDGER_V2}</span>
            <span className="font-label text-[12px] text-mist">
              tx {DEMO_TX_V2.slice(0, 8)}…{DEMO_TX_V2.slice(-8)} <span aria-hidden>↗</span>
              <span className="sr-only"> (opens Stellar Expert, Testnet, in a new tab)</span>
            </span>
          </a>
        </div>
      </div>
    </section>
  )
}

function FinalCta() {
  return (
    <section aria-labelledby="cta-title" className="bg-paper text-graphite">
      <div className={`${CONTAINER} grid gap-12 py-24 lg:grid-cols-12 lg:items-end lg:gap-10 lg:py-36`}>
        <div className="lg:col-span-8">
          <Eyebrow tone="light">Get started</Eyebrow>
          <h2 id="cta-title" className="mt-7 font-display text-[clamp(2.5rem,5vw,4.5rem)] font-semibold leading-[1.02] tracking-[-0.04em] text-balance">
            See the evidence behind an asset.
          </h2>
          <p className="mt-8 max-w-2xl font-body text-lg leading-relaxed text-paper-muted text-pretty">
            {PUBLIC_SITE
              ? 'The application is not publicly hosted. Follow the demo record LPDEMO in the product section — a first ' +
                'prospectus, an approved second version and its commitment on Stellar Testnet — or read how each step ' +
                'of the evidence record works.'
              : 'Open the application to register assets, attach documents and review claims, or look up a Legal ' +
                'Passport with an institutional key. In the public showcase, the demo record LPDEMO shows the full ' +
                'cycle: a first prospectus, an approved second version and its commitment on Stellar Testnet.'}
          </p>
        </div>
        <div className="flex flex-col gap-3 sm:flex-row lg:col-span-4 lg:flex-col">
          {PUBLIC_SITE ? (
            <>
              <a href="#product" className={`${BUTTON} justify-between bg-graphite text-mist hover:bg-black ${FOCUS_LIGHT}`}>
                See the product <Arrow />
              </a>
              <a href="#how-it-works" className={`${BUTTON} justify-between border border-graphite/25 text-graphite hover:border-graphite ${FOCUS_LIGHT}`}>
                How it works <Arrow />
              </a>
            </>
          ) : (
            <>
              <Link to="/dashboard" className={`${BUTTON} justify-between bg-graphite text-mist hover:bg-black ${FOCUS_LIGHT}`}>
                Launch App <Arrow />
              </Link>
              <Link to="/passport" className={`${BUTTON} justify-between border border-graphite/25 text-graphite hover:border-graphite ${FOCUS_LIGHT}`}>
                Look up a Legal Passport <Arrow />
              </Link>
              <Link to="/api-docs" className={`group inline-flex items-center gap-2 px-1 py-2 font-body text-[14px] text-graphite/80 hover:text-graphite ${FOCUS_LIGHT}`}>
                API Reference <Arrow />
              </Link>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

const FOOTER_LINKS: { title: string; links: { label: string; href?: string; to?: string }[] }[] = [
  { title: 'Platform', links: [
    { label: 'Evidence Registry', href: '#evidence-registry' },
    { label: 'Legal Change Tracking', href: '#legal-change-tracking' },
    { label: 'Verifiable Records', href: '#verifiable-records' },
  ] },
  { title: 'Technology', links: [
    { label: 'How it works', href: '#how-it-works' },
    { label: 'Product', href: '#product' },
    { label: 'Stellar infrastructure', href: '#stellar' },
  ] },
  // The public website carries no application (site.ts)
  ...(PUBLIC_SITE ? [] : [{ title: 'Application', links: [
    { label: 'Launch App', to: '/dashboard' },
    { label: 'Legal Passport', to: '/passport' },
    { label: 'Asset Registry', to: '/assets' },
    { label: 'API Reference', to: '/api-docs' },
  ] }]),
]

function SiteFooter() {
  return (
    <footer className="bg-ink text-ink-soft">
      <div className={`${CONTAINER} pb-10 pt-20`}>
        <div className="grid gap-14 lg:grid-cols-12">
          <div className="lg:col-span-5">
            <div className="flex items-center gap-3 text-mist">
              <LogoMark />
              <span className="font-display text-[15px] font-semibold tracking-[-0.01em]">RWA Legal Passport</span>
            </div>
            <p className="mt-6 max-w-sm font-body text-[14px] leading-relaxed">
              Legal evidence and transparency infrastructure for tokenized real-world assets.
            </p>
          </div>
          <nav aria-label="Footer" className="grid grid-cols-2 gap-10 sm:grid-cols-3 lg:col-span-7">
            {FOOTER_LINKS.map(group => (
              <div key={group.title}>
                <h2 className={`${LABEL} text-ink-muted`}>{group.title}</h2>
                <ul className="mt-5 space-y-3">
                  {group.links.map(link => (
                    <li key={link.label}>
                      {link.to
                        ? <Link to={link.to} className={`font-body text-[14px] hover:text-mist ${FOCUS}`}>{link.label}</Link>
                        : <a href={link.href} className={`font-body text-[14px] hover:text-mist ${FOCUS}`}>{link.label}</a>}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        </div>
        <div className="mt-20 flex flex-col gap-4 border-t border-white/10 pt-8 font-label text-[11px] uppercase tracking-[0.12em] text-ink-muted sm:flex-row sm:items-center sm:justify-between">
          <span>© 2026 RWA Legal Passport</span>
          <span>Stellar Testnet only · Not legal advice</span>
          <a href="#top" className={`group inline-flex items-center gap-2 hover:text-mist ${FOCUS}`}>Back to top <Arrow up /></a>
        </div>
      </div>
    </footer>
  )
}

export function Landing() {
  useEffect(() => { document.title = 'RWA Legal Passport · The legal evidence layer for real-world assets' }, [])
  return (
    <div id="top" className="min-h-screen bg-ink font-body antialiased">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[60] focus:bg-mist focus:px-4 focus:py-2 focus:text-ink">
        Skip to content
      </a>
      {/* The application server's own notice; the public website has no server to ask */}
      {!PUBLIC_SITE && <DeploymentBanner />}
      <SiteHeader />
      <main id="main">
        <Hero />
        <Problem />
        <Platform />
        <HowItWorks />
        <ProductPreview />
        <Institutions />
        <Stellar />
        <FinalCta />
      </main>
      <SiteFooter />
    </div>
  )
}
