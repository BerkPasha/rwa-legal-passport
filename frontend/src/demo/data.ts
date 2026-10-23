import type {
  AnchorVerification,
  ApprovalStaleReason,
  ApprovalType,
  Asset,
  Document,
  DocumentVersion,
  ExtractionApprovalState,
  ExtractionSource,
  ExtractionStatus,
  LegalClaim,
  LegalExtraction,
  LegalPassport,
  PassportHistory,
  ReviewerRole,
} from '../types'
import {
  CHECKED_AT,
  DEMO_DIGEST_V1,
  DEMO_DIGEST_V2,
  DEMO_HISTORY,
  DEMO_LEDGER_V2,
  DEMO_SHA256_V1,
  DEMO_SHA256_V2,
  DEMO_TX_V1,
  DEMO_TX_V2,
  DEMO_VERIFICATION,
} from '../landing/demoRecord'
import { MANIFEST_V2 } from './manifests'

// The bundled records of the public, read-only demo (/demo). Nothing here is read from a server: the demo
// has no backend, no credential and no network request.
//
// - LPDEMO is a snapshot of the public demo record (DEMO.md, landing/demoRecord.ts): its documents, claims,
//   page texts, approvals and the two evidence commitments are those of the demo dataset, and both
//   commitments are real Stellar Testnet transactions. Its verification is the check recorded at
//   CHECKED_AT; the demo never runs a live one.
// - LPFUND and LPNOTE are synthetic example records bundled with the demo to show two other states
//   (reapproval required, awaiting review). They have no Stellar transaction of any kind.
//
// Every issuer, document, reviewer and value is fictional. The records follow the server's rules: a claim
// is "verified" only if its quotation is on its cited page, approvals are bound to one version, and a
// newer version supersedes them.

export const DEMO_LABEL = 'Interactive Demo — Synthetic Data — Read Only — Stellar Testnet'

export interface DemoPage { page_number: number; text: string }

// An approval as the review record shows it; reviewers appear by role only, never by identity
export interface DemoApproval {
  approval_type: ApprovalType
  reviewer_role: ReviewerRole
  approved_at: string
  document_version_number: number
  binding_digest: string
  is_current: boolean
  reasons: ApprovalStaleReason[]
}

export interface DemoDocument {
  document: Document
  versions: DocumentVersion[]               // oldest first
  pages: Record<string, DemoPage[]>         // by version id
  extractions: LegalExtraction[]            // oldest first
  approvals: Record<string, DemoApproval[]> // by extraction id
}

export type VersionApproval = 'not_approved' | 'in_review' | 'current' | 'superseded'

// The record's position after an event: what the Legal Passport serves, the approval state of every
// version, and whether the latest commitment matches the current evidence
export interface DemoState {
  passport: 'none' | 'current' | 'withheld'
  versions: { version: number; approval: VersionApproval }[]
  anchor: 'none' | 'matches' | 'stale'
}

export interface DemoEvent {
  at: string
  kind: 'Asset' | 'Document' | 'Claims' | 'Approval' | 'Anchor' | 'Check'
  title: string
  detail: string
  // Why the event changed the record's position (shown for the events that invalidate approvals)
  consequence?: string
  invalidates?: boolean
  state: DemoState
}

export interface DemoRecord {
  asset: Asset
  synopsis: string
  // Whether the evidence commitments of this record are real Stellar Testnet transactions
  anchoredOnTestnet: boolean
  documents: DemoDocument[]
  passport: LegalPassport | null
  history: PassportHistory
  // The recorded verification of the latest commitment (LPDEMO only); never a live check
  verification: AnchorVerification | null
  timeline: DemoEvent[]
}

// ── Builders ──────────────────────────────────────────────────────────────────

const METHOD = 'exact_substring_token_boundary'
const SCOPE = 'source_text_presence_only'

// Whitespace-insensitive presence of a quotation in a page text (the fixtures use plain text only)
const squash = (text: string) => text.replace(/\s+/g, ' ').trim()
const pagesQuoting = (pages: DemoPage[], quotation: string) =>
  pages.filter(p => squash(p.text).includes(squash(quotation))).map(p => p.page_number)

type ClaimSpec = { field: string; id?: string; value?: string; page?: number; quotation?: string }

// The claims of one extraction, with the server's evidence result computed from the version's page text
function claimsOf(extractionId: string, versionNumber: number, pages: DemoPage[], createdAt: string, specs: ClaimSpec[]): LegalClaim[] {
  return specs.map(spec => {
    const id = spec.id ?? `${extractionId.slice(0, 8)}-${spec.field}`
    const base = { verification_method: METHOD, normalization_version: 1, scope: SCOPE, page_count: pages.length, version_number: versionNumber }
    if (spec.value === undefined) {
      return {
        id, extraction_id: extractionId, field_name: spec.field, value: 'unknown', is_unknown: true,
        source_page: null, supporting_passage: null, created_at: createdAt,
        evidence: { ...base, status: 'not_applicable', cited_page: null, found_on_pages: [],
                    explanation: 'The claim is marked unknown; no source evidence is required.' },
      }
    }
    const found = pagesQuoting(pages, spec.quotation!)
    const page = spec.page!
    const onPage = found.includes(page)
    return {
      id, extraction_id: extractionId, field_name: spec.field, value: spec.value, is_unknown: false,
      source_page: page, supporting_passage: spec.quotation!, created_at: createdAt,
      evidence: {
        ...base,
        status: onPage ? 'verified' : found.length ? 'found_on_other_page' : 'not_found',
        cited_page: page,
        found_on_pages: found,
        explanation: onPage
          ? `The quotation appears on the cited page ${page}. This proves source-text presence only, not legal correctness.`
          : 'The quotation does not appear on the cited page of this document version.',
      },
    }
  })
}

const BOTH: ApprovalType[] = ['issuer_attestation', 'legal_verification']

const approvalState = {
  current: (types: ApprovalType[] = BOTH): ExtractionApprovalState =>
    ({ state: 'current', version_is_current: true, current_approval_types: types, reasons: [] }),
  superseded: (): ExtractionApprovalState =>
    ({ state: 'superseded', version_is_current: false, current_approval_types: [], reasons: ['document_version_superseded'] }),
  open: (): ExtractionApprovalState =>
    ({ state: 'not_approved', version_is_current: true, current_approval_types: [], reasons: [] }),
}

function extraction(id: string, documentId: string, versionId: string, at: string, opts: {
  status: ExtractionStatus; source: ExtractionSource; aiModel: string; disclaimer: string
  state: ExtractionApprovalState; claims: LegalClaim[]
}): LegalExtraction {
  return {
    id, document_id: documentId, version_id: versionId, status: opts.status, ai_model: opts.aiModel,
    source: opts.source, disclaimer: opts.disclaimer, extraction_timestamp: at, created_at: at,
    claims: opts.claims, approval_state: opts.state,
  }
}

function version(id: string, documentId: string, n: number, filename: string, size: number, sha256: string, at: string,
                 pages: DemoPage[]): DocumentVersion {
  return {
    id, document_id: documentId, version_number: n, original_filename: filename, file_size: size, sha256_hash: sha256,
    created_at: at,
    text_layer: { status: 'complete', page_count: pages.length, pages_with_text: pages.length, pages_without_text: [],
                  requires_ocr: false, notice: '' },
  }
}

const approval = (type: ApprovalType, at: string, n: number, digest: string, current: boolean): DemoApproval => ({
  approval_type: type,
  reviewer_role: type === 'issuer_attestation' ? 'issuer_reviewer' : 'independent_legal_reviewer',
  approved_at: at,
  document_version_number: n,
  binding_digest: digest,
  is_current: current,
  reasons: current ? [] : ['document_version_superseded'],
})

// The passport history (GET …/history) of a record without a current passport, from its documents
function historyOf(asset: Asset, documents: DemoDocument[]): PassportHistory {
  return {
    asset: {
      asset_id: asset.id, asset_code: asset.stellar_asset_code, issuer_key: asset.stellar_issuer_public_key,
      network: asset.blockchain_network, issuer_organization: asset.issuer_organization_name,
      asset_type: asset.asset_type, jurisdiction: asset.jurisdiction, status: asset.status,
    },
    current_passport_available: false,
    current_manifest_digest: null,
    documents: documents.map(({ document, versions, extractions, approvals }) => ({
      document_id: document.id,
      title: document.title,
      document_type: document.document_type,
      status: document.status,
      current_version_number: document.current_version_number,
      versions: versions.map(v => ({ version_number: v.version_number, sha256_hash: v.sha256_hash, uploaded_at: v.created_at,
                                     is_current: v.version_number === document.current_version_number })),
      approved_versions: extractions.filter(e => (approvals[e.id] ?? []).length > 0).map(e => ({
        extraction_id: e.id,
        document_version_number: versions.find(v => v.id === e.version_id)?.version_number ?? null,
        extraction_source: e.source,
        approval_state: e.approval_state.state,
        stale_reasons: e.approval_state.reasons,
        served_in_current_passport: false,
        approvals: approvals[e.id].map(a => ({
          approval_type: a.approval_type, reviewer_role: a.reviewer_role, approved_at: a.approved_at,
          binding_digest: a.binding_digest, is_current: a.is_current, stale_reasons: a.reasons,
          counted_in_current_passport: false, approved_claims: [],
        })),
      })),
    })),
    anchors: [],
    disclaimer: DEMO_HISTORY.disclaimer,
    retrieved_at: CHECKED_AT,
  }
}

// The disclaimers the server stores with an extraction record (app/services, app/manual_claims.py)
const AI_DISCLAIMER = 'AI-GENERATED DRAFT — The legal-rights values in this extraction were generated by an artificial intelligence model from the text of one uploaded document version. They are not legal advice and may be incomplete or incorrect. A quotation check confirms only that the quoted text appears on the cited page of that document version; it does not confirm that the extracted value is correct. Reviewer corrections and approvals are internal attestations, not legal opinions. None of these establishes legal validity, ownership or enforceability. Do not use this output for any legal, investment or compliance decision without review by qualified, independent legal counsel.'
const MANUAL_DISCLAIMER = 'MANUALLY ENTERED DRAFT — The legal-rights values in this record were typed by a user from the text of one uploaded document version; no AI model was used. They are not legal advice and may be incomplete or incorrect. A quotation check confirms only that the quoted text appears on the cited page of that document version; it does not confirm that the entered value is correct. Reviewer corrections and approvals are internal attestations, not legal opinions. None of these establishes legal validity, ownership or enforceability. Do not use this record for any legal, investment or compliance decision without review by qualified, independent legal counsel.'
const MANUAL_MODEL = 'manual-entry (no AI model)'

// ── LPDEMO: the public demo record ────────────────────────────────────────────

const LPDEMO_ID = DEMO_HISTORY.asset.asset_id
const LPDEMO_DOC = DEMO_HISTORY.documents[0].document_id
const LPDEMO_V1 = '6c1a526c-a457-49a1-9fca-033d20be9d9e'
const LPDEMO_V2 = 'd196499a-c2ff-4c38-9309-76345ee36570'
const LPDEMO_EX1 = '4db9c13d-d9d2-4535-9392-b04bdc2cb69f'
const LPDEMO_EX2 = '9125f044-407a-47dc-ba3a-ffae9cecad9d'
const T1 = '2026-10-17T10:16:13Z'
const T2 = '2026-10-19T22:30:25Z'

const LPDEMO_PAGES_V1: DemoPage[] = [
  { page_number: 1, text: 'MERIDIAN PROPERTY TRUST - PROSPECTUS (FICTIONAL TEST DOCUMENT)\nInvestors shall hold a fractional beneficial ownership interest in the Meridian Property Trust.\nNet rental income shall be distributed quarterly, pro-rata to token holdings.\nTransfer of ownership interests is restricted to accredited investors and requires prior written consent of the Trustee.\nThis Trust Agreement is governed by the laws of the State of New York, United States of America.' },
  { page_number: 2, text: 'The Trustee holds fee simple title to the Property on behalf of the Trust.\nDistributions are paid within thirty days after the end of each calendar quarter.' },
]

const LPDEMO_PAGES_V2: DemoPage[] = [
  { page_number: 1, text: 'Meridian Harbour Property Trust\nProspectus — Series A Digital Units (fictional test document)\n1. Nature of the Investment\nEach Digital Unit represents a fractional beneficial interest in the Meridian Harbour\nProperty Trust. Unitholders do not acquire legal title to the underlying real estate; legal\ntitle remains vested in the Trustee.\nThe Trust’s sole asset is the office building known as “Harbour Point”, 14 Quay Street.' },
  { page_number: 2, text: '2. Income and Distributions\nNet rental income shall be distributed quarterly, pro rata to the number of Digital Units\nheld, within thirty days after the end of each calendar quarter.\nItem Amount\nMinimum subscription$1,000\nTotal offering $12,500,000\n3. Voting\nUnitholders holding at least 25% of the outstanding Digital Units may requisition a\ngeneral meeting; each Digital Unit carries one vote on matters reserved to unitholders.' },
  { page_number: 3, text: '4. Transfer Restrictions\nDigital Units may be transferred only to verified accredited investors whose wallet\naddresses have been approved by the Trustee — any other transfer is void.\n5. Redemption\nThe Trust does not offer a redemption facility; unitholders may exit only by transfer on an\napproved venue.' },
  { page_number: 4, text: '6. Insolvency\nIn the event of the Trustee’s insolvency, the Trust property shall not form part of the\nTrustee’s estate and remains held for the benefit of the unitholders.\n7. Governing Law\nThis Prospectus and the Trust Deed are governed by the laws of England and Wales, and\nthe courts of England have exclusive jurisdiction.' },
]

const lpdemoHistoryDoc = DEMO_HISTORY.documents[0]
const recordedApprovals = (n: number) => lpdemoHistoryDoc.approved_versions.find(v => v.document_version_number === n)!.approvals
  .map(a => approval(a.approval_type, a.approved_at, n, a.binding_digest!, a.is_current))

const LPDEMO_VERSIONS = [
  version(LPDEMO_V1, LPDEMO_DOC, 1, 'prospectus.pdf', 1501, DEMO_SHA256_V1, T1, LPDEMO_PAGES_V1),
  version(LPDEMO_V2, LPDEMO_DOC, 2, 'trust_prospectus.pdf', 56546, DEMO_SHA256_V2, T2, LPDEMO_PAGES_V2),
]

const LPDEMO_DOCUMENT: DemoDocument = {
  document: {
    id: LPDEMO_DOC, asset_id: LPDEMO_ID, document_type: 'prospectus', title: 'Prospectus', status: 'active',
    current_version_number: 2, created_at: T1, updated_at: T2, current_version: LPDEMO_VERSIONS[1],
  },
  versions: LPDEMO_VERSIONS,
  pages: { [LPDEMO_V1]: LPDEMO_PAGES_V1, [LPDEMO_V2]: LPDEMO_PAGES_V2 },
  extractions: [
    extraction(LPDEMO_EX1, LPDEMO_DOC, LPDEMO_V1, T1, {
      status: 'approved', source: 'ai_service', aiModel: 'mock-v1', disclaimer: AI_DISCLAIMER, state: approvalState.superseded(),
      claims: claimsOf(LPDEMO_EX1, 1, LPDEMO_PAGES_V1, T1, [
        { field: 'ownership_nature', id: 'f4a84ddf-dc87-4549-9e7b-96337fd25878', page: 1,
          value: 'Fractional beneficial ownership interest in the underlying real estate trust.',
          quotation: 'Investors shall hold a fractional beneficial ownership interest in the Meridian Property Trust.' },
        { field: 'income_rights', id: 'eb99a1ee-17ce-4d71-a004-275199bbb02e', page: 1,
          value: 'Quarterly distributions of net rental income, pro-rata to token holdings.',
          quotation: 'Net rental income shall be distributed quarterly, pro-rata to token holdings.' },
        { field: 'voting_rights', id: '707b1800-bbb5-4b7b-85b6-4ef50036601b' },
        { field: 'redemption_rights', id: 'e3bc375a-d199-4355-93a4-787430c87240' },
        { field: 'transfer_restrictions', id: 'e426536f-7c90-4599-9e0c-c61a2b1e6a35', page: 1,
          value: 'Transfer restricted to accredited investors only; prior written trustee consent required.',
          quotation: 'Transfer of ownership interests is restricted to accredited investors and requires prior written consent of the Trustee.' },
        { field: 'jurisdiction', id: 'bcb45c0b-36a4-4dd8-bd15-90d58307e669', page: 1,
          value: 'State of New York, United States of America.',
          quotation: 'This Trust Agreement is governed by the laws of the State of New York, United States of America.' },
        { field: 'insolvency_provisions', id: '09f0365f-b8e6-411c-873b-b2e7748723ee' },
      ]),
    }),
    extraction(LPDEMO_EX2, LPDEMO_DOC, LPDEMO_V2, T2, {
      status: 'approved', source: 'manual', aiModel: MANUAL_MODEL, disclaimer: MANUAL_DISCLAIMER, state: approvalState.current(),
      claims: claimsOf(LPDEMO_EX2, 2, LPDEMO_PAGES_V2, T2, [
        { field: 'ownership_nature', id: '04c9e95b-e062-4867-9bf7-ff0bb622ab8b' },
        { field: 'income_rights', id: '940180e2-9131-433f-8c77-89fc123d8d2d', page: 2,
          value: 'Net rental income is distributed quarterly, pro rata.',
          quotation: 'Net rental income shall be distributed quarterly' },
        { field: 'voting_rights', id: '5c85e83f-b37c-40b5-a3d5-d1826ec52ade' },
        { field: 'redemption_rights', id: '74bd1fc3-c0d7-461f-ad84-fc4cf9517a70' },
        { field: 'transfer_restrictions', id: '724c4127-8a80-41f2-89fb-c7d6cb8d85f0' },
        { field: 'jurisdiction', id: 'b2904214-a3d5-4666-bb4d-c95e1b0f20ac' },
        { field: 'insolvency_provisions', id: '4aa24e4f-b043-4f81-aede-7cbdf741c2a4' },
      ]),
    }),
  ],
  approvals: { [LPDEMO_EX1]: recordedApprovals(1), [LPDEMO_EX2]: recordedApprovals(2) },
}

const UNKNOWN_FIELDS = ['insolvency_provisions', 'jurisdiction', 'ownership_nature', 'redemption_rights', 'transfer_restrictions', 'voting_rights']

const LPDEMO_RIGHTS: LegalPassport['legal_rights'] = [
    {
      field_name: 'income_rights', value: 'Net rental income is distributed quarterly, pro rata.', is_unknown: false,
      verified: true, independently_verified: true, conflict: false, source_page: 2,
      supporting_passage: 'Net rental income shall be distributed quarterly',
      sources: [{
        document_id: LPDEMO_DOC, document_title: 'Prospectus', version_number: 2,
        value: 'Net rental income is distributed quarterly, pro rata.', is_unknown: false, source_page: 2,
        supporting_passage: 'Net rental income shall be distributed quarterly', independently_verified: true,
        evidence_status: 'verified', value_origin: 'manual_entry', reviewer_attestation_status: 'independent_reviewer_attested',
      }],
      evidence_status: 'verified', verification_method: METHOD, document_id: LPDEMO_DOC, document_version_number: 2,
      value_origin: 'manual_entry', reviewer_attestation_status: 'independent_reviewer_attested',
    },
    ...UNKNOWN_FIELDS.map(field => ({
      field_name: field, value: 'unknown', is_unknown: true, verified: false, independently_verified: false, conflict: false,
      source_page: null, supporting_passage: null,
      sources: [{
        document_id: LPDEMO_DOC, document_title: 'Prospectus', version_number: 2, value: 'unknown', is_unknown: true,
        source_page: null, supporting_passage: null, independently_verified: false, evidence_status: 'not_applicable' as const,
        value_origin: 'manual_entry' as const, reviewer_attestation_status: 'not_applicable' as const,
      }],
      evidence_status: 'not_applicable' as const, verification_method: METHOD, document_id: null, document_version_number: null,
      value_origin: null, reviewer_attestation_status: 'not_applicable' as const,
    })),
]

// GET /passport/stellar-testnet/LPDEMO/… after version 2 was approved (app/routers/passport.py): the rights
// in the server's field order, one source-verified field and six unknown ones, and both current approvals
const LPDEMO_PASSPORT: LegalPassport = {
  asset: DEMO_HISTORY.asset,
  verification_level: 'independently_verified',
  verification_status: 'partially_source_verified',
  legal_rights: [...LPDEMO_RIGHTS].sort((a, b) => a.field_name.localeCompare(b.field_name)),
  approvals: LPDEMO_DOCUMENT.approvals[LPDEMO_EX2].map(a => ({
    approval_type: a.approval_type, reviewer_role: a.reviewer_role, approved_at: a.approved_at, notes: null,
    document_id: LPDEMO_DOC, document_version_number: 2, binding_digest: a.binding_digest,
  })),
  source_document: { document_id: LPDEMO_DOC, title: 'Prospectus', document_type: 'prospectus', version_number: 2 },
  source_documents: [{
    document_id: LPDEMO_DOC, title: 'Prospectus', document_type: 'prospectus', version_number: 2, approval_types: BOTH,
    independently_verified: true, source_verified_claim_count: 1, unknown_claim_count: 6,
  }],
  excluded_documents: [],
  extraction_metadata: {
    extraction_id: LPDEMO_EX2, ai_model: MANUAL_MODEL, extraction_source: 'manual', extraction_timestamp: T2,
    passport_version: 2, effective_date: T2,
  },
  verification_summary: {
    source_verified_field_count: 1, independently_attested_field_count: 1, unverified_field_count: 0,
    unknown_field_count: 6, conflicting_field_count: 0, reviewer_attestation_recorded: true,
    independent_reviewer_attestation_recorded: true, issuer_attestation_recorded: true,
    legal_reviewer_approval_recorded: true, evidence_scope: SCOPE, verification_method: METHOD,
    normalization_version: 1, legal_interpretation_status: 'not_independently_established',
    meaning: 'Source-text verification establishes only that the quotation appears on the cited page of the approved document version. It does not establish legal enforceability, ownership authenticity, investor entitlement, regulatory compliance, issuer solvency or an independent legal opinion. Reviewer approvals are internal attestations, not legal opinions.',
  },
  disclaimer: DEMO_HISTORY.disclaimer,
  retrieved_at: CHECKED_AT,
}

const short = (hash: string) => `${hash.slice(0, 8)}…`
const v = (version: number, approval: VersionApproval) => ({ version, approval })

const LPDEMO_RECORD: DemoRecord = {
  asset: {
    id: LPDEMO_ID, issuer_id: 'meridian-property', blockchain_network: 'stellar-testnet', stellar_asset_code: 'LPDEMO',
    stellar_issuer_public_key: DEMO_HISTORY.asset.issuer_key, issuer_organization_name: DEMO_HISTORY.asset.issuer_organization,
    asset_type: 'real_estate', jurisdiction: 'US-NY', status: 'active', created_at: T1, updated_at: T1, material_revision: 1,
  },
  synopsis: 'A prospectus replaced by a second version: the first approvals were superseded, the new version was approved again and its evidence anchored on Stellar Testnet.',
  anchoredOnTestnet: true,
  documents: [LPDEMO_DOCUMENT],
  passport: LPDEMO_PASSPORT,
  history: DEMO_HISTORY,
  // The committed manifest is the stored canonical text whose SHA-256 is the anchored digest
  verification: { ...DEMO_VERIFICATION, committed_manifest_canonical_json: MANIFEST_V2 },
  timeline: [
    { at: T1, kind: 'Asset', title: 'LPDEMO registered',
      detail: 'Registered for Meridian Property Holdings LLC with its Stellar Testnet asset code and issuer key.',
      state: { passport: 'none', versions: [], anchor: 'none' } },
    { at: T1, kind: 'Document', title: 'Prospectus version 1 uploaded',
      detail: `prospectus.pdf · 2 pages · SHA-256 ${short(DEMO_SHA256_V1)}`,
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
    { at: T1, kind: 'Claims', title: 'Seven claims recorded for version 1',
      detail: 'Four claims cite page 1 with an exact quotation that the server found there; three fields are unknown. Recorded from the test suite’s extraction output (no AI call).',
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
    { at: T1, kind: 'Approval', title: 'Version 1 approved by both roles',
      detail: 'An issuer attestation and an independent legal review, each bound to version 1: its SHA-256, its page text and the evidence of every claim.',
      state: { passport: 'current', versions: [v(1, 'current')], anchor: 'none' } },
    { at: '2026-10-17T10:16:14Z', kind: 'Anchor', title: 'Version 1 evidence anchored on Stellar Testnet',
      detail: `Manifest digest ${short(DEMO_DIGEST_V1)} recorded in ledger 5241718 (transaction ${short(DEMO_TX_V1)}).`,
      state: { passport: 'current', versions: [v(1, 'current')], anchor: 'matches' } },
    { at: T2, kind: 'Document', title: 'Prospectus version 2 uploaded', invalidates: true,
      detail: `trust_prospectus.pdf · 4 pages · SHA-256 ${short(DEMO_SHA256_V2)}`,
      consequence: 'Version 1’s approvals stopped applying at once (reason: a newer document version exists). Nothing was carried over: no Legal Passport was served until version 2 had its own claims and approvals, and the version 1 commitment no longer matched the current evidence.',
      state: { passport: 'withheld', versions: [v(1, 'superseded'), v(2, 'not_approved')], anchor: 'stale' } },
    { at: T2, kind: 'Claims', title: 'Claims entered for version 2',
      detail: 'Entered manually from version 2’s own pages (no AI): income rights cite page 2; the other six fields are recorded as unknown.',
      state: { passport: 'withheld', versions: [v(1, 'superseded'), v(2, 'not_approved')], anchor: 'stale' } },
    { at: T2, kind: 'Approval', title: 'Version 2 approved by both roles',
      detail: 'A new issuer attestation and a new independent legal review, bound to version 2. The Legal Passport is served again, as passport version 2.',
      state: { passport: 'current', versions: [v(1, 'superseded'), v(2, 'current')], anchor: 'stale' } },
    { at: '2026-10-19T22:30:49Z', kind: 'Anchor', title: 'Version 2 evidence anchored on Stellar Testnet',
      detail: `Manifest digest ${short(DEMO_DIGEST_V2)} recorded in ledger ${DEMO_LEDGER_V2} (transaction ${short(DEMO_TX_V2)}).`,
      state: { passport: 'current', versions: [v(1, 'superseded'), v(2, 'current')], anchor: 'matches' } },
    { at: CHECKED_AT, kind: 'Check', title: 'Commitment checked against Stellar Testnet',
      detail: 'Recorded result: verified, with all six network checks passed at that time. This demo shows the recorded result; it does not query Stellar.',
      state: { passport: 'current', versions: [v(1, 'superseded'), v(2, 'current')], anchor: 'matches' } },
  ],
}

// ── LPFUND: synthetic, reapproval required ────────────────────────────────────

const LPFUND_ID = '6443149d-df4b-5935-adc7-c6d56686939e'
const LPFUND_DOC = '4c63c286-fba4-52a8-91fe-926964f11c21'
const LPFUND_V1 = '83b0ab01-6402-5e8a-a9cd-d6ae54427dd5'
const LPFUND_V2 = 'df66a0d9-92cb-5c98-ae31-27cfc86824a9'
const LPFUND_EX1 = '70a19275-98ce-58f4-8adf-678508f05298'
const LPFUND_EX2 = '4522296f-51ea-5400-b302-77b0230bee84'

const FUND_PAGE_1 = 'Each token represents a limited partnership interest in Kestrel Bay Credit Fund LP.\nNet interest income from the loan portfolio shall be distributed monthly, pro rata to tokens held.\nLimited partners have no right to vote on the management of the Fund.'
const FUND_PAGE_2_TAIL = 'Tokens may be transferred only to qualified purchasers approved by the General Partner.\nThis Memorandum is governed by the laws of the State of Delaware.'

const LPFUND_PAGES_V1: DemoPage[] = [
  { page_number: 1, text: `KESTREL BAY CREDIT FUND LP - OFFERING MEMORANDUM (FICTIONAL TEST DOCUMENT)\n${FUND_PAGE_1}` },
  { page_number: 2, text: `Limited partners may redeem their interests quarterly on thirty days' written notice to the General Partner.\n${FUND_PAGE_2_TAIL}` },
]
const LPFUND_PAGES_V2: DemoPage[] = [
  { page_number: 1, text: `KESTREL BAY CREDIT FUND LP - OFFERING MEMORANDUM, AMENDMENT NO. 1 (FICTIONAL TEST DOCUMENT)\n${FUND_PAGE_1}` },
  { page_number: 2, text: `Limited partners may redeem their interests semi-annually on ninety days' written notice, subject to a gate of 10% of net asset value per redemption date.\n${FUND_PAGE_2_TAIL}` },
]

const fundClaims = (redemption: ClaimSpec): ClaimSpec[] => [
  { field: 'ownership_nature', page: 1, value: 'Limited partnership interest in the Fund.',
    quotation: 'Each token represents a limited partnership interest in Kestrel Bay Credit Fund LP.' },
  { field: 'income_rights', page: 1, value: 'Monthly distribution of net interest income, pro rata to tokens held.',
    quotation: 'Net interest income from the loan portfolio shall be distributed monthly, pro rata to tokens held.' },
  { field: 'voting_rights', page: 1, value: 'No voting rights on the management of the Fund.',
    quotation: 'Limited partners have no right to vote on the management of the Fund.' },
  redemption,
  { field: 'transfer_restrictions', page: 2, value: 'Transfers only to qualified purchasers approved by the General Partner.',
    quotation: 'Tokens may be transferred only to qualified purchasers approved by the General Partner.' },
  { field: 'jurisdiction', page: 2, value: 'State of Delaware.',
    quotation: 'This Memorandum is governed by the laws of the State of Delaware.' },
  { field: 'insolvency_provisions' },
]

const FUND_T = {
  registered: '2026-10-12T09:05:00Z', v1: '2026-10-12T09:12:00Z', claims1: '2026-10-12T09:40:00Z',
  issuer: '2026-10-13T14:02:00Z', legal: '2026-10-14T10:25:00Z', v2: '2026-10-21T08:30:00Z', claims2: '2026-10-21T09:15:00Z',
  review: '2026-10-21T11:00:00Z',
}

const LPFUND_VERSIONS = [
  version(LPFUND_V1, LPFUND_DOC, 1, 'offering_memorandum.pdf', 48210,
          '8216424a4151ef067339e0ebc6cb24798318edb71b2adf942e53c7eed6a1b80d', FUND_T.v1, LPFUND_PAGES_V1),
  version(LPFUND_V2, LPFUND_DOC, 2, 'offering_memorandum_amendment_1.pdf', 51877,
          '51204953060b915652e824fc2264b499e54f00aef4f9cb82e513af2fb57b569c', FUND_T.v2, LPFUND_PAGES_V2),
]

const LPFUND_DOCUMENT: DemoDocument = {
  document: {
    id: LPFUND_DOC, asset_id: LPFUND_ID, document_type: 'prospectus', title: 'Offering Memorandum', status: 'active',
    current_version_number: 2, created_at: FUND_T.v1, updated_at: FUND_T.v2, current_version: LPFUND_VERSIONS[1],
  },
  versions: LPFUND_VERSIONS,
  pages: { [LPFUND_V1]: LPFUND_PAGES_V1, [LPFUND_V2]: LPFUND_PAGES_V2 },
  extractions: [
    extraction(LPFUND_EX1, LPFUND_DOC, LPFUND_V1, FUND_T.claims1, {
      status: 'approved', source: 'manual', aiModel: MANUAL_MODEL, disclaimer: MANUAL_DISCLAIMER, state: approvalState.superseded(),
      claims: claimsOf(LPFUND_EX1, 1, LPFUND_PAGES_V1, FUND_T.claims1, fundClaims(
        { field: 'redemption_rights', page: 2, value: 'Quarterly redemption on 30 days’ written notice.',
          quotation: "Limited partners may redeem their interests quarterly on thirty days' written notice to the General Partner." })),
    }),
    extraction(LPFUND_EX2, LPFUND_DOC, LPFUND_V2, FUND_T.claims2, {
      status: 'under_review', source: 'manual', aiModel: MANUAL_MODEL, disclaimer: MANUAL_DISCLAIMER, state: approvalState.open(),
      claims: claimsOf(LPFUND_EX2, 2, LPFUND_PAGES_V2, FUND_T.claims2, fundClaims(
        { field: 'redemption_rights', page: 2,
          value: 'Semi-annual redemption on 90 days’ written notice, subject to a gate of 10% of net asset value per redemption date.',
          quotation: "Limited partners may redeem their interests semi-annually on ninety days' written notice, subject to a gate of 10% of net asset value per redemption date." })),
    }),
  ],
  approvals: {
    [LPFUND_EX1]: [
      approval('issuer_attestation', FUND_T.issuer, 1, '4bb360ebc77198484d3e73db7e2dc2b70b374dc9171dc8677b179a632956b2c0', false),
      approval('legal_verification', FUND_T.legal, 1, '3bb4f0ac1155e564d1c0b6a172b33d245e618e07460ff34ec5c4b0f557893bd1', false),
    ],
  },
}

const LPFUND_ASSET: Asset = {
  id: LPFUND_ID, issuer_id: 'kestrel-bay', blockchain_network: 'stellar-testnet', stellar_asset_code: 'LPFUND',
  stellar_issuer_public_key: 'GDUV76KO7MMSOHVYYODY3O5NAPKNHZTHXVO4TZLU6CGXPJGN5MS4M2PL',
  issuer_organization_name: 'Kestrel Bay Credit Fund LP', asset_type: 'fund', jurisdiction: 'US-DE', status: 'active',
  created_at: FUND_T.registered, updated_at: FUND_T.registered, material_revision: 1,
}

const LPFUND_RECORD: DemoRecord = {
  asset: LPFUND_ASSET,
  synopsis: 'An amended offering memorandum changes the redemption terms: the earlier approvals no longer apply, and no Legal Passport is served until the amendment is approved.',
  anchoredOnTestnet: false,
  documents: [LPFUND_DOCUMENT],
  passport: null,
  history: historyOf(LPFUND_ASSET, [LPFUND_DOCUMENT]),
  verification: null,
  timeline: [
    { at: FUND_T.registered, kind: 'Asset', title: 'LPFUND registered',
      detail: 'Registered for Kestrel Bay Credit Fund LP with its Stellar Testnet asset code and issuer key.',
      state: { passport: 'none', versions: [], anchor: 'none' } },
    { at: FUND_T.v1, kind: 'Document', title: 'Offering Memorandum version 1 uploaded',
      detail: 'offering_memorandum.pdf · 2 pages',
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
    { at: FUND_T.claims1, kind: 'Claims', title: 'Claims entered for version 1',
      detail: 'Six claims cite pages 1 and 2 with quotations found there; insolvency provisions are unknown. Entered manually (no AI).',
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
    { at: FUND_T.issuer, kind: 'Approval', title: 'Issuer attestation of version 1',
      detail: 'The issuer’s own statement, bound to version 1. The Legal Passport is served as issuer-attested only.',
      state: { passport: 'current', versions: [v(1, 'current')], anchor: 'none' } },
    { at: FUND_T.legal, kind: 'Approval', title: 'Independent legal review of version 1',
      detail: 'Given separately by an independent legal reviewer, also bound to version 1.',
      state: { passport: 'current', versions: [v(1, 'current')], anchor: 'none' } },
    { at: FUND_T.v2, kind: 'Document', title: 'Amendment No. 1 uploaded as version 2', invalidates: true,
      detail: 'offering_memorandum_amendment_1.pdf · 2 pages · the redemption terms on page 2 change.',
      consequence: 'Both approvals of version 1 stopped applying (a newer document version exists). The Legal Passport is withheld until version 2 is reviewed and approved again; the earlier approvals stay in the history for audit only.',
      state: { passport: 'withheld', versions: [v(1, 'superseded'), v(2, 'not_approved')], anchor: 'none' } },
    { at: FUND_T.claims2, kind: 'Claims', title: 'Claims entered for version 2',
      detail: 'All seven fields entered again from version 2’s own pages; only the redemption rights differ from version 1.',
      state: { passport: 'withheld', versions: [v(1, 'superseded'), v(2, 'not_approved')], anchor: 'none' } },
    { at: FUND_T.review, kind: 'Claims', title: 'Review of version 2 started',
      detail: 'Awaiting a new issuer attestation and a new independent legal review.',
      state: { passport: 'withheld', versions: [v(1, 'superseded'), v(2, 'in_review')], anchor: 'none' } },
  ],
}

// ── LPNOTE: synthetic, awaiting review ────────────────────────────────────────

const LPNOTE_ID = '659fc0e5-0c1c-56ac-9b0a-4f933cd2f675'
const LPNOTE_DOC = '8a84db00-bde6-5c2b-8880-4117ddc2380a'
const LPNOTE_V1 = 'e8e66ee1-f4d0-5f53-9573-5f38ad21e5f9'
const LPNOTE_EX1 = 'ade66ac3-c33c-58e3-ba01-b9b3687ca4b8'
const NOTE_T = { registered: '2026-10-22T13:20:00Z', v1: '2026-10-22T13:31:00Z', claims: '2026-10-22T15:04:00Z' }

const LPNOTE_PAGES: DemoPage[] = [
  { page_number: 1, text: 'ALDER QUAY NOTES LTD - SERIES 1 NOTE TERMS (FICTIONAL TEST DOCUMENT)\nThe Notes are senior secured debt obligations of the Issuer and confer no equity interest.\nInterest accrues at 6.25% per annum and is payable semi-annually in arrear.\nThe Notes will be redeemed at par on the maturity date in October 2031.' },
  { page_number: 2, text: 'Noteholders holding not less than 75% of the outstanding principal may approve amendments by extraordinary resolution.\nThese Terms are governed by English law and the courts of England have exclusive jurisdiction.' },
]

const LPNOTE_VERSIONS = [
  version(LPNOTE_V1, LPNOTE_DOC, 1, 'series_1_note_terms.pdf', 38544,
          'b9a66af5c1b171cb587b27934808dd5f1dc48669b8acf11264771373144a1656', NOTE_T.v1, LPNOTE_PAGES),
]

const LPNOTE_DOCUMENT: DemoDocument = {
  document: {
    id: LPNOTE_DOC, asset_id: LPNOTE_ID, document_type: 'prospectus', title: 'Series 1 Note Terms', status: 'active',
    current_version_number: 1, created_at: NOTE_T.v1, updated_at: NOTE_T.v1, current_version: LPNOTE_VERSIONS[0],
  },
  versions: LPNOTE_VERSIONS,
  pages: { [LPNOTE_V1]: LPNOTE_PAGES },
  extractions: [
    extraction(LPNOTE_EX1, LPNOTE_DOC, LPNOTE_V1, NOTE_T.claims, {
      status: 'draft', source: 'manual', aiModel: MANUAL_MODEL, disclaimer: MANUAL_DISCLAIMER, state: approvalState.open(),
      claims: claimsOf(LPNOTE_EX1, 1, LPNOTE_PAGES, NOTE_T.claims, [
        { field: 'ownership_nature', page: 1, value: 'Senior secured debt of the Issuer; no equity interest.',
          quotation: 'The Notes are senior secured debt obligations of the Issuer and confer no equity interest.' },
        { field: 'income_rights', page: 1, value: 'Interest of 6.25% per annum, paid semi-annually in arrear.',
          quotation: 'Interest accrues at 6.25% per annum and is payable semi-annually in arrear.' },
        { field: 'voting_rights', page: 2, value: 'Holders of at least 75% of the principal may approve amendments.',
          quotation: 'Noteholders holding not less than 75% of the outstanding principal may approve amendments by extraordinary resolution.' },
        { field: 'redemption_rights', page: 1, value: 'Redeemed at par at maturity (October 2031).',
          quotation: 'The Notes will be redeemed at par on the maturity date in October 2031.' },
        { field: 'transfer_restrictions' },
        { field: 'jurisdiction', page: 2, value: 'English law; exclusive jurisdiction of the courts of England.',
          quotation: 'These Terms are governed by English law and the courts of England have exclusive jurisdiction.' },
        { field: 'insolvency_provisions' },
      ]),
    }),
  ],
  approvals: {},
}

const LPNOTE_ASSET: Asset = {
  id: LPNOTE_ID, issuer_id: 'alder-quay', blockchain_network: 'stellar-testnet', stellar_asset_code: 'LPNOTE',
  stellar_issuer_public_key: 'GAOWJEEODHYUXLCITR32BIAOXCLYJYNPNSVWEJJPQC3D6EF6ASQCSNFV',
  issuer_organization_name: 'Alder Quay Notes Ltd', asset_type: 'bond', jurisdiction: 'GB-ENG', status: 'active',
  created_at: NOTE_T.registered, updated_at: NOTE_T.registered, material_revision: 1,
}

const LPNOTE_RECORD: DemoRecord = {
  asset: LPNOTE_ASSET,
  synopsis: 'Note terms with claims entered from their pages but not yet reviewed: nothing is served to institutions before approval.',
  anchoredOnTestnet: false,
  documents: [LPNOTE_DOCUMENT],
  passport: null,
  history: historyOf(LPNOTE_ASSET, [LPNOTE_DOCUMENT]),
  verification: null,
  timeline: [
    { at: NOTE_T.registered, kind: 'Asset', title: 'LPNOTE registered',
      detail: 'Registered for Alder Quay Notes Ltd with its Stellar Testnet asset code and issuer key.',
      state: { passport: 'none', versions: [], anchor: 'none' } },
    { at: NOTE_T.v1, kind: 'Document', title: 'Series 1 Note Terms version 1 uploaded',
      detail: 'series_1_note_terms.pdf · 2 pages',
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
    { at: NOTE_T.claims, kind: 'Claims', title: 'Claims entered as a draft',
      detail: 'Five claims cite pages 1 and 2 with quotations found there; two fields are unknown. Entered manually (no AI); not yet reviewed.',
      state: { passport: 'none', versions: [v(1, 'not_approved')], anchor: 'none' } },
  ],
}

// ── Lookups ───────────────────────────────────────────────────────────────────

export const DEMO_RECORDS: DemoRecord[] = [LPDEMO_RECORD, LPFUND_RECORD, LPNOTE_RECORD]
export const FEATURED_ASSET_ID = LPDEMO_ID

export const recordById = (assetId: string | undefined) => DEMO_RECORDS.find(r => r.asset.id === assetId)
export const recordByCode = (code: string | null) => DEMO_RECORDS.find(r => r.asset.stellar_asset_code === code)
