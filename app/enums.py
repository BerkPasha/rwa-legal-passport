import enum


class BlockchainNetwork(str, enum.Enum):
    stellar_mainnet = "stellar-mainnet"
    stellar_testnet = "stellar-testnet"


class AnchorStatus(str, enum.Enum):
    """
    Stored state of a Stellar Testnet evidence anchor (STELLAR_ANCHORING.md).
    'pending'   → signed and submitted (or submission outcome unknown); not yet confirmed
    'confirmed' → the network reported the transaction as successful in a ledger
    'failed'    → rejected or unsuccessful, or expired (time bounds passed) without inclusion
    """
    pending = "pending"
    confirmed = "confirmed"
    failed = "failed"


class AnchorVerificationResult(str, enum.Enum):
    """
    Result of verifying a passport's evidence commitment against live Stellar network data.
    Only 'verified' is positive. None of these values says anything about legal validity.
    """
    verified = "verified"                                  # on-chain commitment matches the current evidence
    evidence_changed = "evidence_changed"                  # valid on-chain commitment of DIFFERENT (earlier) evidence
    not_anchored = "not_anchored"                          # no commitment was ever recorded
    pending = "pending"                                    # submitted, not yet confirmed
    failed = "failed"                                      # the anchoring transaction failed or expired
    onchain_mismatch = "onchain_mismatch"                  # the network transaction does not match the record
    transaction_unavailable = "transaction_unavailable"    # the network no longer returns it (e.g. Testnet reset)
    network_unavailable = "network_unavailable"            # the network could not be queried (or wrong network)
    anchoring_not_configured = "anchoring_not_configured"  # no trusted anchoring account is configured
    anchor_record_invalid = "anchor_record_invalid"        # the stored record fails its own digest check


class AssetType(str, enum.Enum):
    real_estate = "real_estate"
    commodity = "commodity"
    equity = "equity"
    bond = "bond"
    fund = "fund"
    other = "other"


class AssetStatus(str, enum.Enum):
    active = "active"
    suspended = "suspended"
    retired = "retired"


class DocumentType(str, enum.Enum):
    prospectus = "prospectus"
    legal_opinion = "legal_opinion"
    title_deed = "title_deed"
    regulatory_filing = "regulatory_filing"
    audit_report = "audit_report"
    other = "other"


class DocumentStatus(str, enum.Enum):
    active = "active"
    archived = "archived"


class LegalFieldName(str, enum.Enum):
    """Fixed list of the legal-rights fields to extract — seven fields."""
    ownership_nature = "ownership_nature"
    income_rights = "income_rights"
    voting_rights = "voting_rights"
    redemption_rights = "redemption_rights"
    transfer_restrictions = "transfer_restrictions"
    jurisdiction = "jurisdiction"
    insolvency_provisions = "insolvency_provisions"


class ExtractionStatus(str, enum.Enum):
    """
    AI extraction workflow states.
    'draft'        → extracted by the AI, not yet reviewed
    'under_review' → review in progress
    'approved'     → approved by at least one authorized reviewer
    Automatic approval is never applied.
    """
    draft = "draft"
    under_review = "under_review"
    approved = "approved"


class ReviewerRole(str, enum.Enum):
    """
    The reviewer's role within the organization.
    'issuer_reviewer'            → employee of the token-issuing organization
    'independent_legal_reviewer' → independent legal expert (higher reliability)
    """
    issuer_reviewer = "issuer_reviewer"
    independent_legal_reviewer = "independent_legal_reviewer"


class UserRole(str, enum.Enum):
    """
    System role of the Bearer-token holder (determines the permission set).
    'platform_admin'             → platform operator; asset registry, all issuers
    'issuer_reviewer'            → issuer employee; only the assets of their own issuer
    'independent_legal_reviewer' → independent legal expert; only the assigned issuers
    The reviewer-role values are identical to those of ReviewerRole.
    """
    platform_admin = "platform_admin"
    issuer_reviewer = "issuer_reviewer"
    independent_legal_reviewer = "independent_legal_reviewer"


class ApprovalType(str, enum.Enum):
    """
    Approval type — distinguishes the issuer's approval from independent legal verification.
    """
    issuer_attestation = "issuer_attestation"
    legal_verification = "legal_verification"


class ApprovalStaleReason(str, enum.Enum):
    """
    Why an approval record is not current (Stage 9B-3). An approval is current only
    when none of these apply; approval records themselves are never modified.
    """
    unbound_legacy_approval = "unbound_legacy_approval"          # created before approval binding existed
    binding_integrity_failed = "binding_integrity_failed"        # stored binding no longer matches its digest
    re_review_in_progress = "re_review_in_progress"              # the extraction was reopened for review
    document_not_active = "document_not_active"
    document_version_superseded = "document_version_superseded"  # a newer document version exists
    document_hash_mismatch = "document_hash_mismatch"            # the approved version's SHA-256 changed
    evidence_changed = "evidence_changed"                        # claims differ from the approved evidence
    asset_information_changed = "asset_information_changed"      # material asset information changed
    asset_not_active = "asset_not_active"                        # asset suspended or retired
    # Stage 9B-4, step 5
    source_text_changed = "source_text_changed"                  # the approved page text no longer matches its hash
    evidence_not_verified = "evidence_not_verified"              # a quotation does not verify against the page text
    # Stage 9B-4, step 6 — reported by the passport only, which re-hashes the stored file on every read
    source_file_integrity_failed = "source_file_integrity_failed"  # stored PDF missing or not the approved bytes
    # Stage 9B-5C — reported by the passport only; the approval records themselves stay as they are
    competing_current_extractions = "competing_current_extractions"  # several extractions of one version are approved
    self_reviewed_approval = "self_reviewed_approval"  # only a legal approval of the reviewer's own correction


class ApprovalRefusal(str, enum.Enum):
    """
    Why an approval request was refused with 409 (Stage 9B-5C). A refused approval stores nothing.
    """
    duplicate_approval = "duplicate_approval"      # the reviewer already has a current approval of it
    self_review = "self_review"                    # a legal reviewer approving their own latest correction
    competing_extraction = "competing_extraction"  # another extraction of the version is already approved


class ValueOrigin(str, enum.Enum):
    """
    Where an approved passport value comes from (Stage 9B-4, step 6). Derived from the
    correction audit trail, whose identity is bound to the approval (snapshot format 2).
    """
    ai_extraction = "ai_extraction"              # no reviewer correction recorded; the AI's value, approved
    reviewer_correction = "reviewer_correction"  # a reviewer corrected the claim before approval
    manual_entry = "manual_entry"                # entered manually by a user (no AI), uncorrected (Stage 10B-3)


class ExtractionSource(str, enum.Enum):
    """
    How the claims of an extraction record were produced (Stage 10B-3). Derived from the stored
    record (app/manual_claims.py), never chosen by a client.
    """
    ai_service = "ai_service"  # returned by the configured extraction service (an AI model or a test double)
    manual = "manual"          # typed by an authorized user; no AI service was involved


class ManualClaimRefusal(str, enum.Enum):
    """
    Why a manual claim request was refused (Stage 10B-3). A refused request stores nothing.
    """
    document_not_in_asset = "document_not_in_asset"      # the document belongs to another asset
    version_not_in_document = "version_not_in_document"  # the version belongs to another document (or none)
    version_not_current = "version_not_current"          # a newer version of the document exists
    document_not_active = "document_not_active"
    asset_not_active = "asset_not_active"
    source_integrity_failed = "source_integrity_failed"  # the stored PDF does not match its version's records
    duplicate_field = "duplicate_field"                  # one legal field given more than once
    unknown_value = "unknown_value"                      # the value states "unknown": nothing is claimed
    evidence_not_verified = "evidence_not_verified"      # a quotation is not verified on its cited page


class ReviewerAttestationStatus(str, enum.Enum):
    """
    Which current reviewer approvals stand behind a passport value (Stage 9B-4, step 6). An
    approval is an internal attestation; it is never a legal opinion.
    """
    independent_reviewer_attested = "independent_reviewer_attested"  # every source: an independent legal reviewer
    issuer_attested = "issuer_attested"                              # some source: the issuer's attestation only
    not_applicable = "not_applicable"                                # no value is asserted (unknown or conflicting)


class CorrectionRejection(str, enum.Enum):
    """
    Why a reviewer correction was refused (Stage 9B-4, step 7). A refused correction changes
    nothing; the claim and the audit trail stay as they were.
    """
    evidence_required = "evidence_required"            # a changed value without its own page and quotation
    unknown_with_evidence = "unknown_with_evidence"    # an unknown claim cannot carry source evidence
    unknown_value_for_known_claim = "unknown_value_for_known_claim"  # value states unknown, flag says known
    evidence_not_verified = "evidence_not_verified"    # the effective quotation is not verified on its page


class SourceIntegrityFailure(str, enum.Enum):
    """
    Why the stored source of a document version cannot be trusted for an approval (Stage 9B-4,
    step 5): the PDF bytes on disk, the version record and the DocumentPage records must agree.
    """
    version_unavailable = "version_unavailable"            # the version record does not exist
    file_unavailable = "file_unavailable"                  # the stored file is missing or cannot be read
    file_hash_mismatch = "file_hash_mismatch"              # the bytes differ from the recorded SHA-256
    file_unreadable = "file_unreadable"                    # the bytes cannot be parsed with the upload method
    version_changed = "version_changed"                    # the version record changed after the file was checked
    page_count_mismatch = "page_count_mismatch"            # the file and the page records differ in page count
    page_numbering_mismatch = "page_numbering_mismatch"    # page records are not numbered 1..n
    page_text_mismatch = "page_text_mismatch"              # a page record differs from the file's page text


class ExtractionApprovalState(str, enum.Enum):
    """
    Approval state of an extraction, derived from its approval records.
    'not_approved' → no approval record exists
    'current'      → at least one approval is current
    'stale'        → approvals exist, none is current; a re-review can restore currency
    'superseded'   → approvals exist, but a newer document version exists; only a new
                     extraction on the current version can be approved
    """
    not_approved = "not_approved"
    current = "current"
    stale = "stale"
    superseded = "superseded"


class VerificationLevel(str, enum.Enum):
    """
    Strength of the reviewer attestation behind the passport's source-verified values (Stage
    9B-4, step 6: derived from verified fields, no longer from approvals alone). A field counts
    only if its value is known, not conflicting and its quotation is source-verified. Neither
    level is a legal opinion or proof of legal correctness.
    'issuer_attested'                  → no verified field is backed by an independent reviewer;
                                         also the level of a passport without any verified field
                                         (see verification_summary)
    'partially_independently_verified' → some, but not all, verified fields are backed by a
                                         current approval of an independent legal reviewer in
                                         every source
    'independently_verified'           → at least one verified field, and every verified field is
                                         backed by a current independent reviewer approval
    Legacy indicator, kept unchanged for compatibility. Read alone it can mislead: its floor
    'issuer_attested' is also returned when no field is verified and when no current issuer
    attestation exists at all (Stage 9B-5B, step 1). Use SourceVerificationStatus and the
    attestation flags of the verification summary instead.
    """
    issuer_attested = "issuer_attested"
    partially_independently_verified = "partially_independently_verified"
    independently_verified = "independently_verified"


class SourceVerificationStatus(str, enum.Enum):
    """
    Source-field coverage of a passport (Stage 9B-5B, step 1), independent of which approval roles
    are present. A field counts only if its value is known, not conflicting and its quotation is
    source-verified in every approved source. Proves source-text presence only: never legal
    validity, institutional independence or policy satisfaction.
    'no_source_verified_fields'  → no field is source-verified
    'partially_source_verified'  → some fields are source-verified; at least one legal field is
                                   unknown, conflicting, not verified or absent
    'all_fields_source_verified' → every legal field (all of LegalFieldName) is source-verified
    """
    no_source_verified_fields = "no_source_verified_fields"
    partially_source_verified = "partially_source_verified"
    all_fields_source_verified = "all_fields_source_verified"


class EvidenceStatus(str, enum.Enum):
    """
    Result of the deterministic source-evidence check (Stage 9B-4, app/evidence.py).
    Only 'verified' means that the quotation was found on the cited page of the checked
    document version. It proves source-text presence only, never legal correctness.
    """
    verified = "verified"
    not_found = "not_found"                                  # on no page of the document version
    found_on_other_page = "found_on_other_page"              # present, but not on the cited page
    page_missing = "page_missing"                            # no source page is cited
    page_out_of_range = "page_out_of_range"                  # cited page is not a page of this version
    no_text_layer = "no_text_layer"                          # cited page has no extractable text
    quotation_missing = "quotation_missing"
    quotation_too_short = "quotation_too_short"              # below the minimum length or word count
    document_version_mismatch = "document_version_mismatch"  # page text of another version was supplied
    source_text_unavailable = "source_text_unavailable"      # no page records exist for the version
    source_text_invalid = "source_text_invalid"              # duplicate, missing or malformed page records
    not_applicable = "not_applicable"                        # the claim is unknown; no evidence is required


class TextLayerStatus(str, enum.Enum):
    """
    Which pages of one document version have extractable text (Stage 10A-1). Derived from the
    page texts stored at upload with the evidence check's own rule; OCR is not supported.
    """
    complete = "complete"  # every page has a text layer
    partial = "partial"    # some pages have no text layer (e.g. scanned or blank pages)
    none = "none"          # no page has a text layer: nothing can be quoted or verified without OCR
