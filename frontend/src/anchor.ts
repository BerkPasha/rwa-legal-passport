// Stellar Testnet evidence anchoring: labels and explanations (STELLAR_ANCHORING.md).
// A blockchain commitment proves when evidence was recorded and that it has not changed since.
// It is never a legal approval, and it never makes a passport field independently verified.

import { ApiError } from './api'
import type { AnchorStatus, AnchorVerificationResult } from './types'

export type AnchorTone = 'green' | 'amber' | 'red' | 'blue' | 'slate'

export const ANCHOR_RESULTS: Record<AnchorVerificationResult, { label: string; tone: AnchorTone; explanation: string }> = {
  verified: {
    label: 'Commitment verified on Stellar Testnet',
    tone: 'green',
    explanation:
      "The passport's current evidence manifest has exactly the digest recorded on Stellar Testnet by the " +
      'trusted anchoring account. This shows when the evidence was recorded and that it has not changed since; ' +
      'it does not show that the evidence is legally valid.',
  },
  evidence_changed: {
    label: 'Evidence changed since anchoring',
    tone: 'amber',
    explanation:
      "A valid commitment exists on Stellar Testnet, but the passport's current evidence differs from what was " +
      'committed (or no approved passport is current). The current evidence is not covered by a commitment.',
  },
  not_anchored: {
    label: 'Not anchored',
    tone: 'slate',
    explanation: 'No evidence commitment has been recorded on Stellar Testnet for this asset.',
  },
  pending: {
    label: 'Pending confirmation',
    tone: 'blue',
    explanation: 'The anchoring transaction was submitted to Stellar Testnet but is not yet confirmed in a ledger.',
  },
  failed: {
    label: 'Anchoring failed',
    tone: 'red',
    explanation: 'The anchoring transaction failed or expired without being included. Nothing was recorded on-chain.',
  },
  onchain_mismatch: {
    label: 'On-chain record does not match',
    tone: 'red',
    explanation:
      'The network transaction does not carry this digest from the trusted anchoring account. It is not a valid commitment.',
  },
  transaction_unavailable: {
    label: 'Transaction no longer available',
    tone: 'amber',
    explanation:
      'The network no longer returns this transaction (for example after a Stellar Testnet reset). The commitment ' +
      'cannot be re-verified now.',
  },
  network_unavailable: {
    label: 'Stellar network unavailable',
    tone: 'amber',
    explanation:
      'Stellar Horizon could not be queried, or it is not on Stellar Testnet. Nothing can be concluded right now.',
  },
  anchoring_not_configured: {
    label: 'Anchoring not configured',
    tone: 'slate',
    explanation: 'This server has no trusted Stellar anchoring account configured, so nothing can be verified.',
  },
  anchor_record_invalid: {
    label: 'Anchor record invalid',
    tone: 'red',
    explanation: 'The stored anchor record fails its own integrity check. It is not a valid commitment.',
  },
}

export const ANCHOR_STATUS_LABELS: Record<AnchorStatus, string> = {
  pending: 'Pending',
  confirmed: 'Confirmed',
  failed: 'Failed',
}

export const ANCHOR_TONE_CLASSES: Record<AnchorTone, string> = {
  green: 'bg-green-100 text-green-800 ring-green-200',
  amber: 'bg-amber-100 text-amber-800 ring-amber-300',
  red: 'bg-rose-100 text-rose-800 ring-rose-200',
  blue: 'bg-sky-100 text-sky-800 ring-sky-200',
  slate: 'bg-slate-100 text-slate-700 ring-slate-300',
}

// Public explorers of Stellar Testnet (Mainnet anchoring is not supported)
export const testnetExplorerTx = (hash: string) => `https://stellar.expert/explorer/testnet/tx/${hash}`
export const testnetExplorerAccount = (account: string) => `https://stellar.expert/explorer/testnet/account/${account}`

const REFUSALS: Record<string, string> = {
  anchoring_not_configured:
    'Anchoring is disabled on this server (verification-only, or no Testnet anchoring account configured).',
  mainnet_not_supported: 'Evidence anchoring is available for Stellar Testnet assets only.',
  no_current_passport: 'There is no current approved passport to anchor yet.',
  invalid_asset_identity: "The asset's Stellar code or issuer key is not a valid Stellar identifier.",
  anchor_account_not_found:
    'The anchoring account does not exist on Stellar Testnet (it must be funded again, e.g. after a Testnet reset).',
  wrong_network: 'The configured Stellar server is not on Testnet. Nothing was submitted.',
  network_unavailable: 'Stellar Testnet could not be reached. Nothing was submitted.',
}

// Message for a failed anchoring request (never includes credentials: the server holds them)
export function anchorErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 403) return 'Your role cannot anchor evidence.'
    const refusal = error.field('anchor_refusal')
    if (refusal && REFUSALS[refusal]) return REFUSALS[refusal]
    if (error.status === 502) return 'Stellar Testnet rejected the anchoring transaction. Nothing was recorded on-chain.'
    return error.message
  }
  return 'The server could not be reached.'
}

export function shortHash(value: string, size = 10): string {
  return value.length > size * 2 ? `${value.slice(0, size)}…${value.slice(-size)}` : value
}
