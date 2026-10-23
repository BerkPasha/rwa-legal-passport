import type { ReactNode } from 'react'
import type { AnchorVerification } from '../types'
import {
  ANCHOR_RESULTS,
  ANCHOR_STATUS_LABELS,
  ANCHOR_TONE_CLASSES,
  shortHash,
  testnetExplorerAccount,
  testnetExplorerTx,
} from '../anchor'

function fmtTime(iso: string | null) {
  return iso ? new Date(iso).toLocaleString('en-US', { timeZone: 'UTC', timeZoneName: 'short' }) : '—'
}

function Check({ label, value }: { label: string; value: boolean | null }) {
  const mark = value === true ? '✓' : value === false ? '✗' : '—'
  const tone = value === true ? 'text-green-700' : value === false ? 'text-rose-700' : 'text-slate-400'
  return (
    <li className="flex items-center gap-2 text-xs text-slate-700">
      <span className={`w-4 font-bold ${tone}`} aria-hidden>{mark}</span>
      <span>{label}</span>
      <span className="sr-only">{value === true ? 'yes' : value === false ? 'no' : 'not checked'}</span>
    </li>
  )
}

function prettyManifest(canonical: string): string {
  try {
    return JSON.stringify(JSON.parse(canonical), null, 2)
  } catch {
    return canonical
  }
}

// Verification of a passport's Stellar Testnet evidence commitment. A successful transaction is a
// commitment of time and content only: it never turns into legal or independent verification.
// checksLabel: the heading of the network checks; a recorded check (the public demo) says so instead
export function EvidenceAnchorCard({ verification, actions, checksLabel = 'Live network checks' }: {
  verification: AnchorVerification; actions?: ReactNode; checksLabel?: string
}) {
  const result = ANCHOR_RESULTS[verification.result] ?? {
    label: verification.result, tone: 'slate' as const, explanation: '',
  }
  const { anchor, onchain } = verification
  return (
    <div data-testid="anchor-card" className="rounded-xl bg-white shadow-sm ring-1 ring-slate-200">
      <div className="border-b border-slate-100 px-6 py-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="font-semibold text-slate-900">Stellar Evidence Commitment</h3>
            <p className="mt-0.5 text-xs text-slate-500">
              {verification.asset.asset_code} · {verification.asset.network === 'stellar-testnet' ? 'Stellar Testnet' : 'Stellar Mainnet'}
              {' · '}manifest {verification.manifest_schema} v{verification.manifest_version}
            </p>
          </div>
          <span
            data-testid="anchor-result-badge"
            className={`inline-flex items-center rounded-full px-3 py-1 text-xs font-semibold ring-1 ring-inset ${ANCHOR_TONE_CLASSES[result.tone]}`}
          >
            {result.label}
          </span>
        </div>
        <p data-testid="anchor-explanation" className="mt-2 text-sm text-slate-600">{result.explanation}</p>
        <p data-testid="anchor-not-legal" className="mt-2 rounded-lg bg-slate-50 px-3 py-2 text-xs text-slate-600 ring-1 ring-slate-200">
          Not a legal approval: {verification.disclaimer}
        </p>
      </div>

      {anchor ? (
        <div className="space-y-4 px-6 py-4">
          <dl className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="sm:col-span-2">
              <dt className="text-xs text-slate-400">Transaction Hash</dt>
              <dd className="mt-1 flex flex-wrap items-center gap-3">
                <span data-testid="anchor-tx-hash" className="break-all font-mono text-xs text-slate-800">
                  {anchor.transaction_hash ?? '—'}
                </span>
                {anchor.transaction_hash && (
                  <>
                    <a
                      data-testid="anchor-explorer-link"
                      href={testnetExplorerTx(anchor.transaction_hash)}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                    >
                      View on Stellar Expert ↗
                    </a>
                    {anchor.horizon_transaction_url && (
                      <a
                        data-testid="anchor-horizon-link"
                        href={anchor.horizon_transaction_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-xs font-medium text-indigo-600 hover:text-indigo-800"
                      >
                        Horizon (raw) ↗
                      </a>
                    )}
                  </>
                )}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Recorded Status</dt>
              <dd className="mt-1 text-sm text-slate-800">{ANCHOR_STATUS_LABELS[anchor.status] ?? anchor.status}</dd>
              {/* The server's own record is not evidence: only a verified result was checked on the ledger (Stage 10B-2) */}
              {!verification.verified && (
                <dd data-testid="anchor-status-unconfirmed" className="mt-0.5 text-xs text-amber-700">
                  Stored by this server only; not confirmed on Stellar Testnet by this check.
                </dd>
              )}
            </div>
            <div>
              <dt className="text-xs text-slate-400">Ledger</dt>
              <dd className="mt-1 text-sm text-slate-800">
                {(onchain.ledger ?? anchor.ledger) ?? '—'}
                <span className="ml-2 text-xs text-slate-500">{fmtTime(onchain.ledger_closed_at ?? anchor.ledger_closed_at)}</span>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Anchoring Account</dt>
              <dd className="mt-1">
                <a
                  href={testnetExplorerAccount(anchor.anchor_account)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="font-mono text-xs text-indigo-600 hover:text-indigo-800"
                >
                  {shortHash(anchor.anchor_account, 8)}
                </a>
              </dd>
            </div>
            <div>
              <dt className="text-xs text-slate-400">Data Entry</dt>
              <dd className="mt-1 break-all font-mono text-xs text-slate-800">{anchor.data_name}</dd>
            </div>
          </dl>

          <div data-testid="anchor-digests" className="rounded-lg bg-slate-50 p-3 ring-1 ring-slate-200">
            <p className="text-xs text-slate-400">Anchored manifest digest (SHA-256)</p>
            <p className="break-all font-mono text-xs text-slate-800">{anchor.manifest_digest}</p>
            <p className="mt-2 text-xs text-slate-400">Current manifest digest</p>
            <p className="break-all font-mono text-xs text-slate-800">
              {verification.current_manifest_digest ?? 'No current approved passport'}
            </p>
            {/* An invalid record's digest is not relied on, even when it equals the current one */}
            <p
              data-testid="anchor-evidence-match"
              className={`mt-2 text-xs font-medium ${verification.result === 'anchor_record_invalid' ? 'text-rose-700'
                : verification.evidence_matches_anchor ? 'text-green-700' : 'text-amber-800'}`}
            >
              {verification.result === 'anchor_record_invalid'
                ? 'The stored record fails its integrity check: its digest is not relied on'
                : verification.evidence_matches_anchor
                  ? 'Current evidence matches the recorded commitment'
                  : 'Current evidence does not match the recorded commitment'}
            </p>
          </div>

          {onchain.checked && (
            <div>
              <p className="text-xs font-medium uppercase tracking-wider text-slate-400">{checksLabel}</p>
              <ul data-testid="anchor-onchain-checks" className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
                <Check label="Horizon is on Stellar Testnet" value={onchain.network_passphrase_matches} />
                <Check label="Transaction found" value={onchain.transaction_found} />
                <Check label="Transaction successful" value={onchain.successful} />
                <Check label="Sent by the trusted anchoring account" value={onchain.source_account_matches} />
                <Check label="Memo hash equals the digest" value={onchain.memo_matches} />
                <Check label="Data entry equals the digest" value={onchain.operation_matches} />
              </ul>
              {onchain.error && <p className="mt-2 text-xs text-amber-800">Network: {onchain.error}</p>}
            </div>
          )}

          {verification.committed_manifest_canonical_json && (
            <details>
              <summary className="cursor-pointer text-xs text-indigo-600 hover:text-indigo-800">
                View the committed evidence manifest
              </summary>
              <p className="mt-2 text-xs text-slate-500">
                Its SHA-256 over this exact canonical JSON is the anchored digest. It stays off-chain; only the digest is on Stellar.
              </p>
              <pre data-testid="anchor-manifest" className="mt-2 max-h-80 overflow-auto rounded-lg bg-slate-900 p-3 text-xs text-slate-100">
                {prettyManifest(verification.committed_manifest_canonical_json)}
              </pre>
            </details>
          )}
        </div>
      ) : (
        <p className="px-6 py-6 text-sm text-slate-500">
          {verification.current_passport_available
            ? 'The current evidence can be anchored on Stellar Testnet by an authorized issuer reviewer or operator.'
            : 'There is no current approved passport for this asset yet.'}
        </p>
      )}

      {actions && <div className="border-t border-slate-100 px-6 py-3">{actions}</div>}
      <p className="border-t border-slate-100 px-6 py-3 text-xs text-slate-400">
        {verification.meaning} Checked {fmtTime(verification.checked_at)}.
      </p>
    </div>
  )
}
