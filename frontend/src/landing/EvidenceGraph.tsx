import type { ReactNode } from 'react'
import { DEMO_DIGEST_V2, DEMO_LEDGER_V2, DEMO_SHA256_V1, DEMO_SHA256_V2, DEMO_TX_V1, DEMO_TX_V2 } from './demoRecord'

// The hero composition of the website: how one asset's legal evidence is recorded, step by step (the step
// numbers are those of "How it works"), from the asset to its Stellar Testnet commitment. The values are
// the public demo record's (demoRecord.ts).
const short = (hash: string, head = 8, tail = 8) => `${hash.slice(0, head)}…${hash.slice(-tail)}`

const LINE = '#2A3543'
const TEAL = '#168A82'
const TEAL_TEXT = '#4FC1B7'
const TEXT = '#E8EAEC'
const MUTED = '#8B97A5'
const DIM = '#5E6A78'

function Card({ x, y, w, h, step, title, aside, delay, children }: {
  x: number; y: number; w: number; h: number; step?: string; title: string; aside?: string; delay: number; children: ReactNode
}) {
  return (
    <g className="motion-safe:animate-rise" style={{ animationDelay: `${delay}ms` }}>
      <rect x={x} y={y} width={w} height={h} rx={2} fill="#0F1620" stroke="#263140" />
      {step && <text x={x + 16} y={y + 24} className="font-label" fontSize={9.5} letterSpacing="0.14em" fill={TEAL_TEXT}>{step}</text>}
      <text x={x + (step ? 42 : 16)} y={y + 24} className="font-label" fontSize={9.5} letterSpacing="0.14em" fill={MUTED}>{title}</text>
      {aside && (
        <text x={x + w - 16} y={y + 24} textAnchor="end" className="font-label" fontSize={9.5} letterSpacing="0.14em"
              fill={TEAL_TEXT}>{aside}</text>
      )}
      <line x1={x} y1={y + 36} x2={x + w} y2={y + 36} stroke="#263140" />
      {children}
    </g>
  )
}

// A connector that draws itself in once (not at all when the visitor asks for reduced motion)
function Connector({ d, delay, color = TEAL }: { d: string; delay: number; color?: string }) {
  return (
    <path d={d} pathLength={1} fill="none" stroke={color} strokeWidth={1}
          className="motion-safe:animate-draw motion-safe:[stroke-dasharray:1] motion-safe:[stroke-dashoffset:1]"
          style={{ animationDelay: `${delay}ms` }} />
  )
}

function Label({ x, y, children, anchor = 'start' }: { x: number; y: number; children: string; anchor?: 'start' | 'middle' }) {
  return (
    <text x={x} y={y} textAnchor={anchor} className="font-label" fontSize={9} letterSpacing="0.08em" fill={TEAL_TEXT}>
      {children}
    </text>
  )
}

const Dot = ({ x, y }: { x: number; y: number }) => <circle cx={x} cy={y} r={2.5} fill={TEAL} />

export function EvidenceGraph() {
  return (
    <svg viewBox="0 0 600 616" className="h-auto w-full" role="img" aria-labelledby="evidence-graph-title evidence-graph-desc">
      <title id="evidence-graph-title">How the legal evidence behind one asset is recorded</title>
      <desc id="evidence-graph-desc">
        Step 1: the asset LPDEMO is registered on Stellar Testnet. Step 2: version 2 of its prospectus supersedes
        version 1, and a claim cites page 2 of version 2 with an exact quotation. Step 3: an issuer attestation and an
        independent legal review are bound to version 2, and together with the evidence they form a manifest. Step 4:
        the manifest's SHA-256 digest is anchored in a Stellar Testnet transaction. Values from the public demo record.
      </desc>
      <defs>
        <pattern id="evidence-graph-dots" width={24} height={24} patternUnits="userSpaceOnUse">
          <circle cx={1} cy={1} r={1} fill="#1A2330" />
        </pattern>
      </defs>
      <rect width={600} height={616} fill="url(#evidence-graph-dots)" />

      {/* 01: the asset */}
      <g className="motion-safe:animate-rise">
        <rect x={20} y={16} width={560} height={44} rx={2} fill="#0F1620" stroke="#263140" />
        <text x={36} y={43} className="font-label" fontSize={9.5} letterSpacing="0.14em" fill={TEAL_TEXT}>01</text>
        <text x={62} y={43} className="font-label" fontSize={10} letterSpacing="0.14em" fill={TEXT}>ASSET · LPDEMO</text>
        <text x={564} y={43} textAnchor="end" className="font-label" fontSize={9} letterSpacing="0.12em" fill={MUTED}>
          STELLAR TESTNET · ISSUER GB5M55…RDCC
        </text>
      </g>
      <Connector d="M178 60 V92" delay={500} color={LINE} />
      <Label x={186} y={80}>its legal documents</Label>

      <g transform="translate(0 64)">
        {/* 02: document versions */}
        <Card x={20} y={28} w={316} h={168} step="02" title="DOCUMENT · PROSPECTUS" aside="2 VERSIONS" delay={100}>
          <text x={36} y={96} className="font-label" fontSize={12} fill={DIM}>v1</text>
          <text x={68} y={96} className="font-label" fontSize={11} fill={DIM}>sha256 {short(DEMO_SHA256_V1)}</text>
          <text x={320} y={96} textAnchor="end" className="font-label" fontSize={9} letterSpacing="0.12em" fill={DIM}>SUPERSEDED</text>
          <text x={68} y={114} className="font-body" fontSize={10.5} fill={DIM}>Its approvals no longer apply</text>
          <line x1={36} y1={130} x2={320} y2={130} stroke="#1E2835" strokeDasharray="3 4" />
          <text x={36} y={160} className="font-label" fontSize={12} fill={TEXT}>v2</text>
          <text x={68} y={160} className="font-label" fontSize={11} fill={TEXT}>sha256 {short(DEMO_SHA256_V2)}</text>
          <rect x={258} y={147} width={62} height={18} rx={1} fill="none" stroke={TEAL} />
          <text x={289} y={159.5} textAnchor="middle" className="font-label" fontSize={9} letterSpacing="0.12em" fill={TEAL_TEXT}>CURRENT</text>
          <text x={68} y={178} className="font-body" fontSize={10.5} fill={MUTED}>Uploaded 2026-10-19 · 4 pages</text>
        </Card>

        {/* 02: a claim of version 2 */}
        <Card x={372} y={118} w={208} h={112} step="02" title="CLAIM · INCOME RIGHTS" delay={250}>
          <text x={388} y={178} className="font-body" fontSize={12} fill={TEXT}>Page 2 · exact quotation</text>
          <text x={388} y={198} className="font-body" fontSize={11} fontStyle="italic" fill={MUTED}>“Net rental income shall be</text>
          <text x={388} y={214} className="font-body" fontSize={11} fontStyle="italic" fill={MUTED}>distributed quarterly”</text>
        </Card>

        {/* 03: approvals bound to version 2 */}
        <Card x={20} y={236} w={316} h={124} step="03" title="APPROVAL BINDING · V2" delay={400}>
          <text x={36} y={300} className="font-body" fontSize={12} fill={TEXT}>Issuer attestation</text>
          <text x={320} y={300} textAnchor="end" className="font-label" fontSize={9} letterSpacing="0.12em" fill={TEAL_TEXT}>CURRENT</text>
          <text x={36} y={326} className="font-body" fontSize={12} fill={TEXT}>Independent legal review</text>
          <text x={320} y={326} textAnchor="end" className="font-label" fontSize={9} letterSpacing="0.12em" fill={TEAL_TEXT}>CURRENT</text>
          <text x={36} y={348} className="font-body" fontSize={10.5} fill={DIM}>Internal attestations bound to this version</text>
        </Card>

        {/* 03: the evidence manifest */}
        <Card x={372} y={270} w={208} h={96} step="03" title="EVIDENCE MANIFEST" delay={550}>
          <text x={388} y={330} className="font-label" fontSize={9} letterSpacing="0.12em" fill={MUTED}>SHA-256</text>
          <text x={388} y={350} className="font-label" fontSize={12} fill={TEXT}>{short(DEMO_DIGEST_V2)}</text>
        </Card>

        {/* 04: the Stellar Testnet commitment */}
        <Card x={20} y={410} w={560} h={122} step="04" title="STELLAR TESTNET · ANCHOR" aside="VERIFIED ON HORIZON" delay={700}>
          <text x={36} y={470} className="font-label" fontSize={9} letterSpacing="0.12em" fill={MUTED}>LEDGER</text>
          <text x={36} y={492} className="font-label" fontSize={12} fill={TEXT}>{DEMO_LEDGER_V2}</text>
          <text x={196} y={470} className="font-label" fontSize={9} letterSpacing="0.12em" fill={MUTED}>TRANSACTION</text>
          <text x={196} y={492} className="font-label" fontSize={12} fill={TEXT}>{short(DEMO_TX_V2)}</text>
          <text x={400} y={470} className="font-label" fontSize={9} letterSpacing="0.12em" fill={MUTED}>MEMO HASH</text>
          <text x={400} y={492} className="font-label" fontSize={12} fill={TEXT}>= manifest digest</text>
          <text x={36} y={518} className="font-body" fontSize={10.5} fill={DIM}>
            v1 anchor {short(DEMO_TX_V1, 8, 6)} · does not match the current evidence
          </text>
        </Card>

        {/* How they relate */}
        <Connector d="M336 160 H372" delay={800} />
        <Label x={354} y={153} anchor="middle">cites</Label>
        <Dot x={336} y={160} /><Dot x={372} y={160} />

        <Connector d="M178 196 V236" delay={950} color={LINE} />
        <Label x={186} y={220}>approved version</Label>

        <Connector d="M336 318 H372" delay={1100} />
        <Label x={354} y={311} anchor="middle">binds</Label>
        <Dot x={336} y={318} /><Dot x={372} y={318} />

        <Connector d="M476 230 V270" delay={1250} />
        <Label x={484} y={254}>verified quotation</Label>

        <Connector d="M476 366 V410" delay={1400} />
        <Label x={484} y={392}>sha256 → memo</Label>
        <Dot x={476} y={366} /><Dot x={476} y={410} />
      </g>
    </svg>
  )
}
