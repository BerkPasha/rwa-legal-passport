import { DEMO_DIGEST_V2, DEMO_HISTORY, DEMO_SHA256_V1, DEMO_SHA256_V2, DEMO_TX_V1, DEMO_TX_V2 } from './demoRecord'

// The hero figure of the website: the evidence record of one asset, as a stage framework (diagram-design,
// Process type): five stages from the asset to its cryptographic verification, each with the same two slots,
// what is CURRENT (served in the passport) and what version 2 SUPERSEDED (kept as history). Teal marks the
// current evidence chain and its one focal stage, the Stellar Testnet verification. Two layouts of the same
// record: a stage × slot matrix (sm and up) and a single column for phones. Values from the public demo record
// (demoRecord.ts); static, with no motion.
const short = (hash: string, n = 8) => `${hash.slice(0, n)}…${hash.slice(-n)}`

const C = {
  paper: '#0D141D',       // the figure's own ground (bg-ink-raised/60 on bg-ink): label masks
  card: '#0F1620',        // ink.raised
  line: '#2E3A4A',
  dash: '#3A4756',
  text: '#E8EAEC',
  soft: '#A9B3BE',        // ink.soft
  muted: '#8B97A5',       // ink.muted
  past: '#7A8695',        // superseded text: dimmer than muted, still 5:1 on the ground
  accent: '#168A82',
  accentText: '#4FC1B7',  // accent.light
  accentTint: 'rgba(22,138,130,0.10)',
}

type Line =
  | { kind: 'name'; text: string; aside?: string }
  | { kind: 'mono' | 'value' | 'quote'; text: string }

interface Current { eyebrow: string; chip: string; lines: [Line, Line] }
interface Past { chip: string; name: string; value: string }
interface Stage { name: [string, string?]; current: Current; past?: Past; handoff?: string }

const asset = DEMO_HISTORY.asset

const STAGES: Stage[] = [
  {
    name: ['Asset'],
    current: {
      eyebrow: 'TOKENIZED ASSET · REAL ESTATE', chip: 'ACTIVE',
      lines: [
        { kind: 'name', text: asset.asset_code, aside: asset.issuer_organization },
        { kind: 'mono', text: `STELLAR TESTNET · ISSUER ${asset.issuer_key.slice(0, 6)}…${asset.issuer_key.slice(-4)}` },
      ],
    },
    handoff: 'DOCUMENTED BY',
  },
  {
    name: ['Legal', 'documents'],
    current: {
      eyebrow: 'PROSPECTUS', chip: 'CURRENT',
      lines: [{ kind: 'name', text: 'Version 2 · 4 pages' }, { kind: 'mono', text: `SHA-256 ${short(DEMO_SHA256_V2)}` }],
    },
    past: { chip: 'SUPERSEDED', name: 'Prospectus v1', value: `SHA-256 ${short(DEMO_SHA256_V1, 6)}` },
    handoff: 'QUOTED AT P.2',
  },
  {
    name: ['Legal claims', '& evidence'],
    current: {
      eyebrow: 'INCOME RIGHTS · PAGE 2', chip: 'CURRENT',
      lines: [{ kind: 'quote', text: '“Net rental income shall be' }, { kind: 'quote', text: 'distributed quarterly”' }],
    },
    past: { chip: 'SUPERSEDED', name: 'Claims of v1', value: 'NOT IN THE PASSPORT' },
    handoff: 'APPROVED BY',
  },
  {
    name: ['Human', 'approvals'],
    current: {
      eyebrow: 'BOUND TO VERSION 2', chip: 'CURRENT',
      lines: [{ kind: 'name', text: 'Issuer attestation' }, { kind: 'name', text: 'Independent legal review' }],
    },
    past: { chip: 'INVALIDATED', name: 'Approvals of v1', value: 'VERSION SUPERSEDED' },
    handoff: 'HASHED INTO',
  },
  {
    name: ['Cryptographic', 'verification'],
    current: {
      eyebrow: 'STELLAR TESTNET ANCHOR', chip: 'VERIFIED ON HORIZON',
      lines: [
        { kind: 'value', text: `Manifest SHA-256 ${short(DEMO_DIGEST_V2)}` },
        { kind: 'mono', text: `= MEMO HASH OF TX ${short(DEMO_TX_V2)}` },
      ],
    },
    past: { chip: 'NOT CURRENT', name: 'Anchor of v1', value: `TX ${short(DEMO_TX_V1, 6)}` },
  },
]
const FOCAL = STAGES.length - 1

const TITLE = `Evidence record of one tokenized asset (${asset.asset_code})`
const DESC =
  `Synthetic demo record of ${asset.asset_code} in five stages: version 2 of the prospectus is current and ` +
  'version 1 superseded; an Income Rights claim quotes page 2 of version 2; an issuer attestation and an ' +
  'independent legal review are bound to version 2, while the version 1 approvals are invalidated; and the ' +
  'evidence manifest digest is the memo hash of a Stellar Testnet transaction verified on Horizon, while the ' +
  'version 1 anchor no longer matches the current evidence.'

// Mono labels: 8.5px, tracked 0.1em. Chip widths come from the same per-character budget (0.62em + tracking).
const CHIP_FONT = 8.5
const chipWidth = (text: string) => Math.ceil((text.length * CHIP_FONT * 0.72 + 14) / 4) * 4

function Chip({ x, y, text, tone, align = 'start' }: {
  x: number; y: number; text: string; tone: 'current' | 'past' | 'focal'; align?: 'start' | 'end'
}) {
  const w = chipWidth(text)
  const left = align === 'end' ? x - w : x
  const stroke = tone === 'focal' ? C.accent : tone === 'past' ? C.dash : 'rgba(232,234,236,0.45)'
  const fill = tone === 'focal' ? C.accentText : tone === 'past' ? C.past : C.text
  return (
    <g>
      <rect x={left} y={y} width={w} height={16} rx={2} fill="none" stroke={stroke} strokeWidth={0.8}
            strokeDasharray={tone === 'past' ? '3 2' : undefined} />
      <text x={left + w / 2} y={y + 11.5} textAnchor="middle" className="font-label" fontSize={CHIP_FONT}
            letterSpacing="0.1em" fill={fill}>{text}</text>
    </g>
  )
}

function LineText({ line, x, y }: { line: Line; x: number; y: number }) {
  switch (line.kind) {
    case 'name':
      return (
        <text x={x} y={y} className="font-display" fontSize={14} fontWeight={600} fill={C.text}>
          {line.text}
          {line.aside && <tspan className="font-body" fontSize={12.5} fontWeight={400} fill={C.soft}>{`  ${line.aside}`}</tspan>}
        </text>
      )
    case 'quote':
      return <text x={x} y={y} className="font-body" fontSize={13.5} fontStyle="italic" fill={C.text}>{line.text}</text>
    case 'value':
      return <text x={x} y={y} className="font-label" fontSize={10.5} fill={C.text}>{line.text}</text>
    case 'mono':
      return <text x={x} y={y} className="font-label" fontSize={9.5} letterSpacing="0.04em" fill={C.soft}>{line.text}</text>
  }
}

// A current record: eyebrow and state chip, then two lines. The focal one carries the accent.
const CARD_H = 80
function CurrentCard({ x, y, w, slot, focal }: { x: number; y: number; w: number; slot: Current; focal: boolean }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={CARD_H} rx={2} fill={C.card} />
      <rect x={x} y={y} width={w} height={CARD_H} rx={2} fill={focal ? C.accentTint : C.card}
            stroke={focal ? C.accent : C.line} strokeWidth={focal ? 1.2 : 1} />
      <text x={x + 16} y={y + 22} className="font-label" fontSize={8.5} letterSpacing="0.14em"
            fill={focal ? C.accentText : C.muted}>{slot.eyebrow}</text>
      <Chip x={x + w - 12} y={y + 10} text={slot.chip} tone={focal ? 'focal' : 'current'} align="end" />
      <LineText line={slot.lines[0]} x={x + 16} y={y + 48} />
      <LineText line={slot.lines[1]} x={x + 16} y={y + 66} />
    </g>
  )
}

// A connector label: mono, on an opaque mask, 8px beside its vertical segment
function HandoffLabel({ x, y, text }: { x: number; y: number; text: string }) {
  const w = Math.ceil((text.length * 8.5 * 0.72 + 8) / 4) * 4
  return (
    <g>
      <rect x={x} y={y} width={w} height={12} fill={C.paper} />
      <text x={x + 4} y={y + 9} className="font-label" fontSize={8.5} letterSpacing="0.1em" fill={C.muted}>{text}</text>
    </g>
  )
}

function Markers({ id }: { id: string }) {
  return (
    <defs>
      <marker id={`${id}-arrow-accent`} markerWidth={8} markerHeight={6} refX={7} refY={3} orient="auto">
        <polygon points="0 0, 8 3, 0 6" fill={C.accent} />
      </marker>
      <marker id={`${id}-arrow`} markerWidth={6} markerHeight={5} refX={5} refY={2.5} orient="auto">
        <polygon points="0 0, 6 2.5, 0 5" fill={C.dash} />
      </marker>
    </defs>
  )
}

// Slot headers double as the legend: a solid swatch for the current record, a dashed one for history
function SlotHeader({ x, y, text, past }: { x: number; y: number; text: string; past?: boolean }) {
  return (
    <g>
      <rect x={x + 0.5} y={y - 8.5} width={9} height={9} rx={1} fill={past ? 'none' : 'rgba(232,234,236,0.6)'}
            stroke={past ? C.past : 'none'} strokeDasharray={past ? '2 1.5' : undefined} />
      <text x={x + 16} y={y} className="font-label" fontSize={8.5} letterSpacing="0.14em" fill={past ? C.muted : C.soft}>{text}</text>
    </g>
  )
}

// ── Stage × slot matrix (sm and up) ─────────────────────────────────────────
const D = { w: 608, h: 600, curX: 104, curW: 320, pastX: 448, pastW: 160, top: 32, pitch: 120 }
const D_CHAIN_X = D.curX + 24
const D_PAST_CHAIN_X = D.pastX + 20
const rowY = (i: number) => D.top + i * D.pitch

function PastCard({ x, y, w, slot }: { x: number; y: number; w: number; slot: Past }) {
  return (
    <g>
      <rect x={x} y={y} width={w} height={CARD_H} rx={2} fill={C.paper} stroke={C.dash} strokeDasharray="4 3" />
      <Chip x={x + 12} y={y + 10} text={slot.chip} tone="past" />
      <text x={x + 12} y={y + 48} className="font-display" fontSize={12.5} fontWeight={600} fill={C.past}>{slot.name}</text>
      <text x={x + 12} y={y + 66} className="font-label" fontSize={9} letterSpacing="0.04em" fill={C.past}>{slot.value}</text>
    </g>
  )
}

function MatrixLayout() {
  return (
    <svg viewBox={`0 0 ${D.w} ${D.h}`} className="mx-auto block h-auto w-full max-w-[760px]" role="img"
         aria-labelledby="evidence-record-title evidence-record-desc">
      <title id="evidence-record-title">{TITLE}</title>
      <desc id="evidence-record-desc">{DESC}</desc>
      <Markers id="evidence-record" />

      <SlotHeader x={D.curX} y={14} text="CURRENT · IN THE PASSPORT" />
      <SlotHeader x={D.pastX} y={14} text="SUPERSEDED BY V2" past />

      {/* Connectors first: the current evidence chain, then the superseded one */}
      {STAGES.slice(0, -1).map((stage, i) => (
        <g key={stage.name[0]}>
          <line x1={D_CHAIN_X} y1={rowY(i) + CARD_H} x2={D_CHAIN_X} y2={rowY(i + 1) - 2} stroke={C.accent}
                strokeWidth={1.2} markerEnd="url(#evidence-record-arrow-accent)" />
          <HandoffLabel x={D_CHAIN_X + 8} y={rowY(i) + CARD_H + 14} text={stage.handoff!} />
          {stage.past && STAGES[i + 1].past && (
            <line x1={D_PAST_CHAIN_X} y1={rowY(i) + CARD_H} x2={D_PAST_CHAIN_X} y2={rowY(i + 1) - 2} stroke={C.dash}
                  strokeDasharray="4 3" markerEnd="url(#evidence-record-arrow)" />
          )}
        </g>
      ))}

      {STAGES.map((stage, i) => (
        <g key={stage.name[0]}>
          <text x={2} y={rowY(i) + 22} className="font-label" fontSize={10} letterSpacing="0.1em"
                fill={i === FOCAL ? C.accentText : C.muted}>{String(i + 1).padStart(2, '0')}</text>
          {stage.name.map((part, k) => (
            <text key={part} x={2} y={rowY(i) + 46 + k * 17} className="font-display" fontSize={13} fontWeight={600}
                  fill={C.text}>{part}</text>
          ))}
          <CurrentCard x={D.curX} y={rowY(i)} w={D.curW} slot={stage.current} focal={i === FOCAL} />
          {stage.past
            ? <PastCard x={D.pastX} y={rowY(i)} w={D.pastW} slot={stage.past} />
            : <text x={D.pastX + 12} y={rowY(i) + 46} className="font-label" fontSize={9} letterSpacing="0.1em"
                    fill={C.past}>— NO EARLIER VERSION</text>}
        </g>
      ))}
    </svg>
  )
}

// ── Single column (phones) ──────────────────────────────────────────────────
// Each stage: its name, the current record, then what it superseded, indented. The chain runs down the left
// edge, beside the superseded records, so it never passes behind one.
const M = { w: 320, top: 32, chainX: 12, indent: 24, pastH: 48, gap: 8, labelGap: 28 }

function stageLayout() {
  let y = M.top
  return STAGES.map(stage => {
    const top = y
    const card = top + 20
    const pastTop = card + CARD_H + M.gap
    const bottom = stage.past ? pastTop + M.pastH : pastTop + 16
    y = bottom + M.labelGap
    return { stage, top, card, pastTop, bottom }
  })
}
const M_ROWS = stageLayout()
const M_H = M_ROWS[M_ROWS.length - 1].bottom + 4

function CompactPastCard({ x, y, w, slot }: { x: number; y: number; w: number; slot: Past }) {
  const chipW = chipWidth(slot.chip)
  return (
    <g>
      <rect x={x} y={y} width={w} height={M.pastH} rx={2} fill={C.paper} stroke={C.dash} strokeDasharray="4 3" />
      <Chip x={x + 10} y={y + 8} text={slot.chip} tone="past" />
      <text x={x + 20 + chipW} y={y + 20.5} className="font-display" fontSize={12.5} fontWeight={600} fill={C.past}>{slot.name}</text>
      <text x={x + 10} y={y + 38} className="font-label" fontSize={9} letterSpacing="0.04em" fill={C.past}>{slot.value}</text>
    </g>
  )
}

function ColumnLayout() {
  return (
    <svg viewBox={`0 0 ${M.w} ${M_H}`} className="mx-auto block h-auto w-full max-w-[400px]" role="img"
         aria-labelledby="evidence-record-mobile-title evidence-record-mobile-desc">
      <title id="evidence-record-mobile-title">{TITLE}</title>
      <desc id="evidence-record-mobile-desc">{DESC}</desc>
      <Markers id="evidence-record-mobile" />

      <SlotHeader x={0} y={14} text="CURRENT" />
      <SlotHeader x={120} y={14} text="SUPERSEDED BY V2" past />

      {M_ROWS.slice(0, -1).map(({ stage, card, bottom }, i) => (
        <g key={stage.name[0]}>
          <line x1={M.chainX} y1={card + CARD_H} x2={M.chainX} y2={M_ROWS[i + 1].card - 2} stroke={C.accent}
                strokeWidth={1.2} markerEnd="url(#evidence-record-mobile-arrow-accent)" />
          <HandoffLabel x={M.chainX + 8} y={bottom + 8} text={stage.handoff!} />
        </g>
      ))}

      {M_ROWS.map(({ stage, top, card, pastTop }, i) => (
        <g key={stage.name[0]}>
          <text x={M.indent} y={top + 11} className="font-label" fontSize={10} letterSpacing="0.1em"
                fill={i === FOCAL ? C.accentText : C.muted}>{String(i + 1).padStart(2, '0')}</text>
          <text x={M.indent + 24} y={top + 11} className="font-display" fontSize={13.5} fontWeight={600}
                fill={C.text}>{stage.name.filter(Boolean).join(' ')}</text>
          <CurrentCard x={0} y={card} w={M.w} slot={stage.current} focal={i === FOCAL} />
          {stage.past
            ? <CompactPastCard x={M.indent} y={pastTop} w={M.w - M.indent} slot={stage.past} />
            : <text x={M.indent + 10} y={pastTop + 11} className="font-label" fontSize={9} letterSpacing="0.1em"
                    fill={C.past}>— NO EARLIER VERSION</text>}
        </g>
      ))}
    </svg>
  )
}

export function EvidenceGraph() {
  return (
    <>
      <div className="hidden sm:block"><MatrixLayout /></div>
      <div className="sm:hidden"><ColumnLayout /></div>
    </>
  )
}
