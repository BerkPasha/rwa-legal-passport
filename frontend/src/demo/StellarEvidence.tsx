import { EvidenceAnchorCard } from '../components/EvidenceAnchorCard'
import { ANCHOR_STATUS_LABELS, shortHash, testnetExplorerTx } from '../anchor'
import { DEMO_DIGEST_V1, DEMO_DIGEST_V2 } from '../landing/demoRecord'
import { MANIFEST_V1, MANIFEST_V2 } from './manifests'
import type { DemoRecord } from './data'
import { LABEL, fmtDateTime } from './ui'

// A record's Stellar Testnet evidence in the demo. The verification shown is the check recorded at its
// checked_at time, never a live one: the demo does not contact Stellar. Visitors can check a transaction
// themselves on the public explorers.

const MANIFESTS: Record<string, string> = { [DEMO_DIGEST_V1]: MANIFEST_V1, [DEMO_DIGEST_V2]: MANIFEST_V2 }

const pretty = (canonical: string) => JSON.stringify(JSON.parse(canonical), null, 2)

export function StellarEvidence({ record }: { record: DemoRecord }) {
  const { verification, history } = record
  if (!verification) {
    return (
      <div data-testid="demo-not-anchored" className="border border-paper-line bg-white px-6 py-6">
        <p className={`${LABEL} text-paper-muted`}>Stellar Testnet</p>
        <p className="mt-2 font-display text-[18px] font-semibold text-graphite">Not anchored</p>
        <p className="mt-2 max-w-2xl text-[14px] leading-relaxed text-paper-muted">
          No evidence commitment has been recorded on Stellar Testnet for {record.asset.stellar_asset_code}, and there is no
          current approved Legal Passport to anchor yet. In the product, an authorized issuer reviewer or operator anchors
          the evidence of a current passport; this demo cannot submit transactions.
        </p>
      </div>
    )
  }

  const tx = verification.anchor?.transaction_hash
  return (
    <div className="space-y-4">
      <div role="note" data-testid="recorded-check-note" className="border-l-2 border-amber-400 bg-amber-50 px-4 py-3 text-[13px] leading-relaxed text-amber-950">
        <p className="font-semibold">Recorded check — not a live verification</p>
        <p className="mt-1">
          The result below is the check recorded on {fmtDateTime(verification.checked_at)}. This demo does not query Stellar,
          so it cannot tell whether the transaction is still available now (Stellar Testnet is reset periodically). Open the
          transaction on a public explorer to check it yourself.
        </p>
      </div>

      <EvidenceAnchorCard
        verification={verification}
        checksLabel={`Network checks recorded on ${fmtDateTime(verification.checked_at)}`}
        actions={tx ? (
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs">
            <span className="text-slate-500">Check it yourself:</span>
            <a href={testnetExplorerTx(tx)} target="_blank" rel="noopener noreferrer" className="font-medium text-indigo-600 hover:text-indigo-800">
              Stellar Expert (Testnet) ↗
            </a>
            {verification.anchor?.horizon_transaction_url && (
              <a href={verification.anchor.horizon_transaction_url} target="_blank" rel="noopener noreferrer" className="font-medium text-indigo-600 hover:text-indigo-800">
                Horizon Testnet (raw) ↗
              </a>
            )}
            <span data-testid="demo-anchor-read-only" className="text-slate-500">
              Read-only demo: no re-check is run and no anchoring transaction can be submitted.
            </span>
          </div>
        ) : undefined}
      />

      <div className="border border-paper-line bg-white">
        <div className="border-b border-paper-line px-6 py-3">
          <h4 className="text-sm font-semibold text-graphite">Anchoring History</h4>
          <p className="mt-0.5 text-xs text-paper-muted">
            Statuses as recorded. An anchor of earlier evidence never stands for the current evidence.
          </p>
        </div>
        <ul data-testid="demo-anchor-history" className="divide-y divide-paper-line">
          {history.anchors.map(anchor => {
            const manifest = MANIFESTS[anchor.manifest_digest]
            return (
              <li key={anchor.manifest_digest} className="px-6 py-3 text-xs">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
                  <span className="font-medium text-slate-800">{ANCHOR_STATUS_LABELS[anchor.status]}</span>
                  <span className="font-mono text-slate-500">digest {shortHash(anchor.manifest_digest, 8)}</span>
                  <span data-testid="demo-anchor-match" className={anchor.matches_current_evidence ? 'text-green-700' : 'text-amber-800'}>
                    {anchor.matches_current_evidence ? 'Matches the current evidence' : 'Does not match the current evidence'}
                  </span>
                  {anchor.ledger != null && <span className="text-slate-500">ledger {anchor.ledger}</span>}
                  {anchor.transaction_hash && (
                    <a href={testnetExplorerTx(anchor.transaction_hash)} target="_blank" rel="noopener noreferrer"
                       className="font-mono text-indigo-600 hover:text-indigo-800">tx {shortHash(anchor.transaction_hash, 8)} ↗</a>
                  )}
                  <span className="text-slate-500">{fmtDateTime(anchor.anchored_at)}</span>
                </div>
                {!anchor.matches_current_evidence && (
                  <p className="mt-1 text-slate-500">
                    This commitment covers earlier evidence, whose approvals are no longer current. It stays on the ledger
                    for audit, but it does not support the current Legal Passport.
                  </p>
                )}
                {manifest && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-indigo-600 hover:text-indigo-800">View this committed evidence manifest</summary>
                    <p className="mt-2 text-slate-500">
                      Its SHA-256 over this exact canonical JSON is the digest above. It stays off-chain; only the digest is on Stellar.
                    </p>
                    <pre className="mt-2 max-h-80 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">{pretty(manifest)}</pre>
                  </details>
                )}
              </li>
            )
          })}
        </ul>
      </div>
    </div>
  )
}
