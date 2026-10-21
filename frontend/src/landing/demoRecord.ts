import type { AnchorVerification, PassportHistory } from '../types'

// A snapshot of the public demo record (DEMO.md: the LPDEMO asset of scratch/stellar-demo/demo-v2), shown on
// the website by the application's own components. Every value is what the API returned after version 2 of
// the prospectus was approved and its evidence manifest anchored on Stellar Testnet (ledger 5285093). The
// documents, the issuer and the reviewers are synthetic; an approval is an internal attestation, not a legal
// opinion, and the anchor check is the one performed at CHECKED_AT, not a live one.

export const DEMO_TX_V1 = '1687c0ddc4a85b07b3ec13e9064aa57a4556e8993b9ff78080b5ac9d628123d2'
export const DEMO_TX_V2 = '843a5a6d9101b3e93a591c88008cf81eca9f25d0f2bd803d5741ef6c3c1240da'
export const DEMO_DIGEST_V1 = '5c760dba64aaf5633ec2a89c9b8641e2c3867b8a08ade7c6c882ff153f4dd057'
export const DEMO_DIGEST_V2 = 'c15a72785380fbf31fd15ad331662c25b7b209d66c1dceec39f5c8b192d1e8ab'
export const DEMO_LEDGER_V2 = 5285093
export const DEMO_SHA256_V1 = '86b976e59a78114268a13a6b8ce17844f0d7995119abbdc127e31acfa5b111c4'
export const DEMO_SHA256_V2 = 'a2ee44089c7ce48c3042a6ef88200fc5c349867745daf4ecd1e5a214ef017772'
const ANCHOR_ACCOUNT = 'GADWA6F3V4BTB3M56BWUBRD5AJVX3WSVIGGWN5BQRTHFXKNUKHRWPDOT'
const ASSET_ID = '74fa4d3b-3b2d-4105-b982-bee9c49e9f6f'
export const CHECKED_AT = '2026-10-19T22:31:22Z'

// The backend's own texts (app/routers/passport.py, app/schemas/stellar_anchor.py)
const PASSPORT_DISCLAIMER = 'INSTITUTIONAL USE ONLY — This legal passport reflects approved internal assessments based on issuer-provided documentation. It does NOT constitute legal advice, regulatory certification, investment recommendation, or a binding legal opinion under any jurisdiction. All information must be independently verified by qualified legal counsel before use.'
const ANCHOR_DISCLAIMER = 'BLOCKCHAIN ANCHOR ONLY — A Stellar transaction proves that the anchoring account recorded this evidence digest at a ledger time. It does not establish legal validity, ownership, regulatory compliance, the authenticity of documents or an independent legal approval. Stellar Testnet is reset periodically; Testnet anchors are for demonstration and testing.'
const ANCHOR_MEANING = "verified means: a successful Stellar Testnet transaction from the configured anchoring account carries this manifest digest (memo hash and manage_data entry), the stored manifest hashes to that digest, and the passport's current evidence manifest has exactly the same digest. Any other result is not a verification."

const approval = (type: 'issuer_attestation' | 'legal_verification', at: string, digest: string, current: boolean) => ({
  approval_type: type,
  reviewer_role: type === 'issuer_attestation' ? 'issuer_reviewer' : 'independent_legal_reviewer',
  approved_at: at,
  binding_digest: digest,
  is_current: current,
  stale_reasons: current ? [] : ['document_version_superseded' as const],
  counted_in_current_passport: current,
  approved_claims: [],
})

const anchorSummary = (digest: string, tx: string, ledger: number, closedAt: string, anchoredAt: string) => ({
  manifest_digest: digest,
  transaction_hash: tx,
  status: 'confirmed' as const,
  anchor_account: ANCHOR_ACCOUNT,
  data_name: `lp:${ASSET_ID}`,
  ledger,
  ledger_closed_at: closedAt,
  anchored_at: anchoredAt,
  horizon_transaction_url: `https://horizon-testnet.stellar.org/transactions/${tx}`,
})
const V2_ANCHOR = anchorSummary(DEMO_DIGEST_V2, DEMO_TX_V2, DEMO_LEDGER_V2, '2026-10-19T22:30:52Z', '2026-10-19T22:30:49Z')
const V1_ANCHOR = anchorSummary(DEMO_DIGEST_V1, DEMO_TX_V1, 5241718, '2026-10-17T10:16:17Z', '2026-10-17T10:16:14Z')

export const DEMO_HISTORY: PassportHistory = {
  asset: {
    asset_id: ASSET_ID,
    asset_code: 'LPDEMO',
    issuer_key: 'GB5M55ZEKFTRJIU76EFLE3BMMHMUP2PWHFD77NCXOOO6GUDFEIF2RDCC',
    network: 'stellar-testnet',
    issuer_organization: 'Meridian Property Holdings LLC',
    asset_type: 'real_estate',
    jurisdiction: 'US-NY',
    status: 'active',
  },
  current_passport_available: true,
  current_manifest_digest: DEMO_DIGEST_V2,
  documents: [{
    document_id: 'bba962f6-31c2-4046-931d-bcc79195c774',
    title: 'Prospectus',
    document_type: 'prospectus',
    status: 'active',
    current_version_number: 2,
    versions: [
      { version_number: 1, sha256_hash: DEMO_SHA256_V1, uploaded_at: '2026-10-17T10:16:13Z', is_current: false },
      { version_number: 2, sha256_hash: DEMO_SHA256_V2, uploaded_at: '2026-10-19T22:30:25Z', is_current: true },
    ],
    approved_versions: [
      {
        extraction_id: '4db9c13d-d9d2-4535-9392-b04bdc2cb69f', document_version_number: 1, extraction_source: 'ai_service',
        approval_state: 'superseded', stale_reasons: ['document_version_superseded'], served_in_current_passport: false,
        approvals: [
          approval('issuer_attestation', '2026-10-17T10:16:13Z', '5249ba4e62ac2985b0490b38b89faaa428eb201ce3fad100145906e87a70cdad', false),
          approval('legal_verification', '2026-10-17T10:16:13Z', '377a31a86bf57db8e1e55e26388e5d3d9f977fdb5c822c308d81e9722021383e', false),
        ],
      },
      {
        extraction_id: '9125f044-407a-47dc-ba3a-ffae9cecad9d', document_version_number: 2, extraction_source: 'manual',
        approval_state: 'current', stale_reasons: [], served_in_current_passport: true,
        approvals: [
          approval('issuer_attestation', '2026-10-19T22:30:25Z', '02f06cc35e7af0314dfaa7181bca62f552423b164e87b3f972ba5d3577182047', true),
          approval('legal_verification', '2026-10-19T22:30:25Z', 'd3bc7286c698614ca814d1454a3353215f7f2854bf64f5021709764e3fd3192c', true),
        ],
      },
    ],
  }],
  anchors: [{ ...V2_ANCHOR, matches_current_evidence: true }, { ...V1_ANCHOR, matches_current_evidence: false }],
  disclaimer: PASSPORT_DISCLAIMER,
  retrieved_at: CHECKED_AT,
}

export const DEMO_VERIFICATION: AnchorVerification = {
  asset: {
    identifier_type: 'stellar_classic_asset',
    network: 'stellar-testnet',
    asset_code: 'LPDEMO',
    asset_issuer: DEMO_HISTORY.asset.issuer_key,
    stellar_asset_type: 'credit_alphanum12',
  },
  manifest_schema: 'rwa-legal-passport/evidence-manifest',
  manifest_version: 1,
  current_passport_available: true,
  current_manifest_digest: DEMO_DIGEST_V2,
  anchor: V2_ANCHOR,
  evidence_matches_anchor: true,
  onchain: {
    checked: true, network_passphrase_matches: true, transaction_found: true, successful: true,
    source_account_matches: true, memo_matches: true, operation_matches: true,
    ledger: DEMO_LEDGER_V2, ledger_closed_at: '2026-10-19T22:30:52Z', error: null,
  },
  result: 'verified',
  verified: true,
  committed_manifest_canonical_json: null,
  meaning: ANCHOR_MEANING,
  disclaimer: ANCHOR_DISCLAIMER,
  checked_at: CHECKED_AT,
}
