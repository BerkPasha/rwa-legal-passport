"""
Legal approval version integrity (Stage 9B-3) and evidence binding (Stage 9B-4, step 5).

An approval is bound, when it is created, to exactly what the reviewer approved:
- the document version (id, number, SHA-256 of the stored file),
- the evidence: a canonical snapshot of every claim of the extraction,
- the material asset information: a canonical snapshot plus the asset's material revision,
- one binding digest over all of the above.

Evidence snapshot formats (the evidence_snapshot column, canonical JSON; evidence_digest is the
SHA-256 of exactly that JSON):
- Format 1 (Stage 9B-3, legacy): a list of the claims (value, cited page, quotation). It records
  no evidence verification and no source text, so it proves nothing about the source.
- Format 2 (every new approval): {"format": 2, "method", "source", "claims"}. Every claim also
  records its evidence status (verified for known claims, not_applicable for unknown ones), the
  SHA-256 of the raw stored text of its cited page, the SHA-256 of its normalized quotation, its
  document version and its latest reviewer correction. "source" records the version, its page
  count and one SHA-256 over all its page texts; "method" records the verification rules.
Hashes identify content. They prove neither legal correctness nor issuer authenticity or
document ownership, and they are not signatures: whoever can rewrite the database can
recompute them. Currency therefore never relies on what a snapshot says about itself.

Approval records are never modified. Whether an approval is *current* is evaluated on every
read by comparing its binding with the live state (`evaluate_approval`):
- every bound approval: the 9B-3 checks, and the live re-verification of every claim's
  quotation against the stored page text of the approved version (evidence_not_verified);
- format 2 also: the approved claims against the live claims (evidence_changed), the recorded
  page-text hashes against the live page text (source_text_changed), the recorded statuses,
  quotation hashes and method against an independent re-computation (evidence_not_verified),
  and the snapshot's version identity against the approval's own binding
  (binding_integrity_failed);
- every bound approval also (Stage 9B-5C C1): its extraction, document, version id and version
  number against the live extraction, document and version loaded from the database; a
  mismatch or missing context is binding_integrity_failed. A record forged together with a
  recomputed digest is thus refuted whenever it names another extraction, document or version.
  A complete forgery for another extraction (snapshot and digests rewritten) is not detectable
  without a keyed signature.

Legacy approvals are read as they are; nothing is upgraded or rewritten:
- without a binding (before Stage 9B-3): never current (unbound_legacy_approval);
- format 1: current only while the 9B-3 checks pass and every live claim still verifies against
  the stored page text of the approved version; otherwise stale (evidence_not_verified). Because
  format 1 recorded no text hash, a change of the page text that keeps the quotations cannot be
  detected for it.
Either is renewed only by a re-review (POST /review) and a new approval, which is bound in
format 2 and must pass the evidence gate and the source-PDF check (app/source_integrity.py).

Nothing is ever transferred to a newer document version: a new version starts without
approvals, and a stale approval only becomes current again through a new approval after
re-review.

Concurrency: endpoints that change the approval state first take the database write lock
(`lock_extraction_for_update`) and only then load and check the state, so that a concurrent
upload, asset change or correction cannot slip in between the check and the write.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import (
    ApprovalStaleReason,
    ApprovalType,
    AssetStatus,
    DocumentStatus,
    EvidenceStatus,
    ExtractionApprovalState,
    ExtractionStatus,
)
from app.evidence import (
    EVIDENCE_SCOPE,
    MATCH_METHOD,
    MIN_QUOTATION_CHARS,
    MIN_QUOTATION_WORDS,
    NORMALIZATION_VERSION,
    normalize_text,
)
from app.evidence_records import (
    ExtractionSourceText,
    accepted_status,
    load_extraction_source_text,
    verify_claims_for_version,
)
from app.models.asset import Asset
from app.models.document import Document, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval, ReviewCorrection

# Asset information that an approval depends on. A change of any of these values
# increments Asset.material_revision and makes every approval of the asset stale.
MATERIAL_ASSET_FIELDS = (
    "issuer_organization_name",
    "asset_type",
    "jurisdiction",
    "status",
)

BINDING_VERSION = 1


# ── Canonical snapshots and digests ──────────────────────────────────────────


def _plain(value):
    return value.value if hasattr(value, "value") else value


def canonical_json(data) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8 preserved."""
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_digest(data) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def asset_snapshot(asset: Asset) -> dict:
    """Identifiers and material information of the asset, as approved."""
    return {
        "asset_id": asset.id,
        "issuer_id": asset.issuer_id,
        "blockchain_network": _plain(asset.blockchain_network),
        "stellar_asset_code": asset.stellar_asset_code,
        "stellar_issuer_public_key": asset.stellar_issuer_public_key,
        **{name: _plain(getattr(asset, name)) for name in MATERIAL_ASSET_FIELDS},
    }


def evidence_snapshot(claims: list[LegalClaim]) -> list[dict]:
    """
    Format 1 (Stage 9B-3): every claim with its value and source reference, in a fixed order.
    Only used to evaluate legacy format 1 approvals; new approvals are bound in format 2.
    """
    return [
        {
            "claim_id": c.id,
            "field_name": c.field_name,
            "value": c.value,
            "is_unknown": bool(c.is_unknown),
            "source_page": c.source_page,
            "supporting_passage": c.supporting_passage,
        }
        for c in sorted(claims, key=lambda c: (c.field_name, c.id))
    ]


def _utc_naive_iso(moment: datetime) -> str:
    # SQLite returns stored datetimes without tzinfo; normalize both forms to naive UTC
    if moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment.isoformat(timespec="microseconds")


def binding_payload(approval: ReviewApproval) -> dict:
    return {
        "binding_version": BINDING_VERSION,
        "approval_id": approval.id,
        "extraction_id": approval.extraction_id,
        "approval_type": approval.approval_type,
        "reviewer_id": approval.reviewer_id,
        "reviewer_role": approval.reviewer_role,
        "approved_at": _utc_naive_iso(approval.approved_at),
        "document_id": approval.document_id,
        "document_version_id": approval.document_version_id,
        "document_version_number": approval.document_version_number,
        "document_sha256": approval.document_sha256,
        "evidence_digest": approval.evidence_digest,
        "asset_material_revision": approval.asset_material_revision,
        "asset_snapshot_digest": approval.asset_snapshot_digest,
    }


_BINDING_FIELDS = (
    "document_id",
    "document_version_id",
    "document_version_number",
    "document_sha256",
    "evidence_snapshot",
    "evidence_digest",
    "asset_material_revision",
    "asset_snapshot",
    "asset_snapshot_digest",
    "binding_digest",
)


# ── Loading the state an approval is compared with ───────────────────────────


@dataclass
class ExtractionContext:
    extraction: LegalExtraction
    document: Document
    asset: Asset
    extraction_version: DocumentVersion | None
    current_version: DocumentVersion | None
    claims: list[LegalClaim]
    approvals: list[ReviewApproval]
    # The DocumentPage text of the extraction's own version (empty if that version is unavailable)
    source: ExtractionSourceText
    # claim id → id of its most recent reviewer correction
    latest_corrections: dict[str, str]
    # claim id → reviewer who made its most recent correction (Stage 9B-5C, self-review)
    latest_correction_authors: dict[str, str] = field(default_factory=dict)

    @property
    def version_is_current(self) -> bool:
        return (
            self.current_version is not None
            and self.extraction.version_id == self.current_version.id
        )

    def authored_latest_correction(self, reviewer_id: str) -> bool:
        """The reviewer made the most recent correction of at least one claim of this extraction."""
        return reviewer_id in self.latest_correction_authors.values()

    def is_self_reviewed(self, approval: ReviewApproval) -> bool:
        """
        A legal approval of the reviewer's own latest correction (Stage 9B-5C). Such an approval is
        refused today; a historical one is kept as recorded but never counted as independent review.
        An issuer attestation of the issuer's own correction is the issuer's own statement anyway.
        """
        return (
            approval.approval_type == ApprovalType.legal_verification.value
            and self.authored_latest_correction(approval.reviewer_id)
        )


async def lock_extraction_for_update(db: AsyncSession, extraction_id: str) -> None:
    """
    Takes the database write lock before the approval state is checked.
    A no-op UPDATE starts the write transaction; until it commits, no other request can
    upload a version, change the asset, correct a claim or approve concurrently.
    """
    await db.execute(
        update(LegalExtraction)
        .where(LegalExtraction.id == extraction_id)
        .values(status=LegalExtraction.status)
        .execution_options(synchronize_session=False)
    )


async def load_extraction_context(db: AsyncSession, extraction_id: str) -> ExtractionContext:
    """Loads the live state from the database, bypassing objects cached in the session."""
    fresh = {"populate_existing": True}
    extraction = (
        await db.execute(
            select(LegalExtraction).where(LegalExtraction.id == extraction_id).execution_options(**fresh)
        )
    ).scalar_one()
    document = (
        await db.execute(
            select(Document).where(Document.id == extraction.document_id).execution_options(**fresh)
        )
    ).scalar_one()
    asset = (
        await db.execute(select(Asset).where(Asset.id == document.asset_id).execution_options(**fresh))
    ).scalar_one()
    versions = (
        await db.execute(
            select(DocumentVersion)
            .where(DocumentVersion.document_id == document.id)
            .execution_options(**fresh)
        )
    ).scalars().all()
    by_id = {v.id: v for v in versions}
    current = next((v for v in versions if v.version_number == document.current_version_number), None)
    claims = (
        await db.execute(
            select(LegalClaim).where(LegalClaim.extraction_id == extraction.id).execution_options(**fresh)
        )
    ).scalars().all()
    approvals = (
        await db.execute(
            select(ReviewApproval)
            .where(ReviewApproval.extraction_id == extraction.id)
            .order_by(ReviewApproval.approved_at, ReviewApproval.created_at)
            .execution_options(**fresh)
        )
    ).scalars().all()
    corrections = (
        await db.execute(
            select(ReviewCorrection.claim_id, ReviewCorrection.id, ReviewCorrection.reviewer_id)
            .where(ReviewCorrection.extraction_id == extraction.id)
            .order_by(ReviewCorrection.created_at, ReviewCorrection.id)
        )
    ).all()
    return ExtractionContext(
        extraction=extraction,
        document=document,
        asset=asset,
        extraction_version=by_id.get(extraction.version_id),
        current_version=current,
        claims=list(claims),
        approvals=list(approvals),
        source=await load_extraction_source_text(db, extraction),
        latest_corrections={claim_id: correction_id for claim_id, correction_id, _ in corrections},
        latest_correction_authors={claim_id: reviewer_id for claim_id, _, reviewer_id in corrections},
    )


async def competing_current_extractions(db: AsyncSession, extraction: LegalExtraction) -> list[str]:
    """
    Other extractions of the same document version with at least one current approval (Stage
    9B-5C, issue B5). Only one extraction per document version may be approved: an approval of a
    competing extraction is refused, and the passport never chooses between several.
    """
    candidates = (
        await db.execute(
            select(LegalExtraction.id)
            .join(ReviewApproval, ReviewApproval.extraction_id == LegalExtraction.id)
            .where(
                LegalExtraction.document_id == extraction.document_id,
                LegalExtraction.version_id == extraction.version_id,
                LegalExtraction.id != extraction.id,
            )
            .group_by(LegalExtraction.id)
            .order_by(LegalExtraction.id)
        )
    ).scalars().all()
    competing = []
    for candidate_id in candidates:
        if summarize_extraction(await load_extraction_context(db, candidate_id)).current_approvals:
            competing.append(candidate_id)
    return competing


# ── Evidence snapshot format 2 (Stage 9B-4, step 5) ──────────────────────────

EVIDENCE_SNAPSHOT_FORMAT = 2

_SNAPSHOT_KEYS = {"format", "method", "source", "claims"}
_SOURCE_KEYS = {"document_version_id", "version_number", "document_sha256", "page_count", "source_text_sha256"}
# What the approved claim is: compared with the live claims (evidence_changed)
_CLAIM_CONTENT_KEYS = (
    "claim_id", "field_name", "value", "is_unknown", "source_page", "supporting_passage",
    "correction_id", "document_version_id",
)
# What the verification found: re-checked independently (source_text_changed, evidence_not_verified)
_CLAIM_EVIDENCE_KEYS = ("evidence_status", "page_text_sha256", "quotation_sha256")


def evidence_method() -> dict:
    """The verification rules an approval's evidence was checked with (part of the snapshot)."""
    return {
        "scope": EVIDENCE_SCOPE,
        "verification_method": MATCH_METHOD,
        "normalization_version": NORMALIZATION_VERSION,
        "min_quotation_chars": MIN_QUOTATION_CHARS,
        "min_quotation_words": MIN_QUOTATION_WORDS,
        "hash_algorithm": "sha256",
        "page_text_hash": "utf8_raw_stored_page_text",
        "quotation_hash": "utf8_normalized_quotation",
        "source_text_hash": "utf8_json_page_number_and_raw_text_list",
    }


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _live_claim_content(ctx: ExtractionContext, version_id: str) -> list[dict]:
    return [
        {
            "claim_id": c.id,
            "field_name": c.field_name,
            "value": c.value,
            "is_unknown": bool(c.is_unknown),
            "source_page": c.source_page,
            "supporting_passage": c.supporting_passage,
            "correction_id": ctx.latest_corrections.get(c.id),
            "document_version_id": version_id,
        }
        for c in sorted(ctx.claims, key=lambda c: (c.field_name, c.id))
    ]


def evidence_snapshot_v2(ctx: ExtractionContext) -> dict:
    """
    Format 2 snapshot of the extraction's claims, verified against the page text of its own
    version as loaded in ctx (under the write lock when an approval is created).
    """
    version = ctx.extraction_version
    checks = dict(
        (claim.id, check) for claim, check in verify_claims_for_version(ctx.claims, version.id, ctx.source)
    )
    claims = []
    for content in _live_claim_content(ctx, version.id):
        check = checks[content["claim_id"]]
        verified = check.status is EvidenceStatus.verified
        claims.append({
            **content,
            "evidence_status": check.status.value,
            "page_text_sha256": check.cited_page_text_sha256 if verified else None,
            "quotation_sha256": check.quotation_sha256 if verified else None,
        })
    return {
        "format": EVIDENCE_SNAPSHOT_FORMAT,
        "method": evidence_method(),
        "source": {
            "document_version_id": version.id,
            "version_number": version.version_number,
            "document_sha256": version.sha256_hash,
            "page_count": len(ctx.source.text.pages),
            "source_text_sha256": ctx.source.text.text_sha256,
        },
        "claims": claims,
    }


def _is_optional(value, kind) -> bool:
    return value is None or (isinstance(value, kind) and not isinstance(value, bool))


def _valid_v2(data) -> bool:
    """Structure of a format 2 snapshot (types and keys only; the content is checked separately)."""
    if not isinstance(data, dict) or set(data) != _SNAPSHOT_KEYS:
        return False
    if type(data["format"]) is not int or data["format"] != EVIDENCE_SNAPSHOT_FORMAT:
        return False
    source, claims = data["source"], data["claims"]
    if not isinstance(data["method"], dict) or not isinstance(source, dict) or set(source) != _SOURCE_KEYS:
        return False
    if not (
        isinstance(source["document_version_id"], str) and isinstance(source["document_sha256"], str)
        and isinstance(source["source_text_sha256"], str)
        and type(source["version_number"]) is int and type(source["page_count"]) is int
    ):
        return False
    if not isinstance(claims, list):
        return False
    for c in claims:
        if not isinstance(c, dict) or set(c) != set(_CLAIM_CONTENT_KEYS + _CLAIM_EVIDENCE_KEYS):
            return False
        if not (
            isinstance(c["claim_id"], str) and isinstance(c["field_name"], str) and isinstance(c["value"], str)
            and type(c["is_unknown"]) is bool and _is_optional(c["source_page"], int)
            and _is_optional(c["supporting_passage"], str) and _is_optional(c["correction_id"], str)
            and isinstance(c["document_version_id"], str) and isinstance(c["evidence_status"], str)
            and _is_optional(c["page_text_sha256"], str) and _is_optional(c["quotation_sha256"], str)
        ):
            return False
    order = [(c["field_name"], c["claim_id"]) for c in claims]
    return order == sorted(order) and len({c["claim_id"] for c in claims}) == len(claims)


def stored_evidence(approval: ReviewApproval) -> tuple[int | None, Any]:
    """
    (format, parsed JSON) of an approval's stored evidence snapshot: 1 for a list, 2 for a
    structurally valid format 2 object, None for anything else (unreadable or unknown format).
    """
    try:
        data = json.loads(approval.evidence_snapshot)
    except (TypeError, ValueError):
        return None, None
    if isinstance(data, list):
        return 1, data
    if _valid_v2(data):
        return EVIDENCE_SNAPSHOT_FORMAT, data
    return None, data


# ── Currency evaluation ──────────────────────────────────────────────────────


@dataclass
class ApprovalCurrency:
    is_current: bool
    reasons: list[ApprovalStaleReason] = field(default_factory=list)


def _binding_intact(approval: ReviewApproval) -> bool:
    try:
        stored_evidence = json.loads(approval.evidence_snapshot)
        stored_asset = json.loads(approval.asset_snapshot)
    except (TypeError, ValueError):
        return False
    return (
        sha256_digest(stored_evidence) == approval.evidence_digest
        and sha256_digest(stored_asset) == approval.asset_snapshot_digest
        and sha256_digest(binding_payload(approval)) == approval.binding_digest
    )


def _matches_binding(snapshot: dict, approval: ReviewApproval) -> bool:
    """A format 2 snapshot describes exactly the version its approval is bound to."""
    source = snapshot["source"]
    return (
        source["document_version_id"] == approval.document_version_id
        and source["version_number"] == approval.document_version_number
        and source["document_sha256"] == approval.document_sha256
        and all(c["document_version_id"] == approval.document_version_id for c in snapshot["claims"])
    )


def _claims_changed(fmt: int | None, snapshot, approval: ReviewApproval, ctx: ExtractionContext) -> bool:
    if fmt == 1:
        return sha256_digest(evidence_snapshot(ctx.claims)) != approval.evidence_digest
    if fmt == EVIDENCE_SNAPSHOT_FORMAT:
        approved = [{k: c[k] for k in _CLAIM_CONTENT_KEYS} for c in snapshot["claims"]]
        return approved != _live_claim_content(ctx, ctx.extraction.version_id)
    return False  # an unreadable snapshot is reported as binding_integrity_failed


def _source_text_changed(snapshot: dict, ctx: ExtractionContext) -> bool:
    """The live page text of the version differs from the text the approval recorded hashes of."""
    live, recorded = ctx.source.text, snapshot["source"]
    if (
        live.problem is not None
        or len(live.pages) != recorded["page_count"]
        or live.text_sha256 != recorded["source_text_sha256"]
    ):
        return True
    for c in snapshot["claims"]:
        if c["page_text_sha256"] is None:
            continue
        page = c["source_page"]
        if not (isinstance(page, int) and 1 <= page <= len(live.pages)):
            return True
        if _text_sha256(live.page_text(page)) != c["page_text_sha256"]:
            return True
    return False


def _evidence_not_verified(fmt: int | None, snapshot, approval: ReviewApproval, ctx: ExtractionContext) -> bool:
    """
    The live claims must verify against the stored page text of the approved version, whatever
    the snapshot says. A format 2 snapshot's own statements (method, statuses, quotation hashes)
    are re-computed and must agree; they are never taken as proof.
    """
    live = verify_claims_for_version(ctx.claims, approval.document_version_id, ctx.source)
    if any(check.status is not accepted_status(claim) for claim, check in live):
        return True
    if fmt != EVIDENCE_SNAPSHOT_FORMAT:
        return False
    if snapshot["method"] != evidence_method():
        return True
    for c in snapshot["claims"]:
        if c["is_unknown"]:
            if (c["evidence_status"], c["page_text_sha256"], c["quotation_sha256"]) != (
                EvidenceStatus.not_applicable.value, None, None
            ):
                return True
        elif (
            c["evidence_status"] != EvidenceStatus.verified.value
            or c["page_text_sha256"] is None
            or not isinstance(c["supporting_passage"], str)
            or c["quotation_sha256"] != _text_sha256(normalize_text(c["supporting_passage"]))
        ):
            return True
    return False


def _identity_matches(approval: ReviewApproval, ctx: ExtractionContext) -> bool:
    """
    Stage 9B-5C C1: the approval's own identity columns name exactly the live context — this
    extraction, its document and its version (id and number). The context comes from the
    database relations (extraction → document → version), never from the approval or a caller's
    claim, and missing context never matches. The bound claims are compared separately
    (evidence_changed) by their claim ids.
    """
    version = ctx.extraction_version  # None unless the version belongs to ctx.document
    return (
        version is not None
        and approval.extraction_id == ctx.extraction.id
        and approval.document_id == ctx.document.id == ctx.extraction.document_id
        and approval.document_version_id == ctx.extraction.version_id == version.id
        and approval.document_version_number == version.version_number
    )


def evaluate_approval(approval: ReviewApproval, ctx: ExtractionContext) -> ApprovalCurrency:
    """Compares one approval's binding with the live state. All failing checks are reported."""
    if any(getattr(approval, name) is None for name in _BINDING_FIELDS):
        return ApprovalCurrency(False, [ApprovalStaleReason.unbound_legacy_approval])

    fmt, snapshot = stored_evidence(approval)
    reasons: list[ApprovalStaleReason] = []
    if (
        not _binding_intact(approval)
        or not _identity_matches(approval, ctx)
        or fmt is None
        or (fmt == EVIDENCE_SNAPSHOT_FORMAT and not _matches_binding(snapshot, approval))
    ):
        reasons.append(ApprovalStaleReason.binding_integrity_failed)
    if ctx.extraction.status != ExtractionStatus.approved.value:
        reasons.append(ApprovalStaleReason.re_review_in_progress)
    if ctx.document.status != DocumentStatus.active.value:
        reasons.append(ApprovalStaleReason.document_not_active)
    if ctx.current_version is None or approval.document_version_id != ctx.current_version.id:
        reasons.append(ApprovalStaleReason.document_version_superseded)
    if ctx.extraction_version is None or ctx.extraction_version.sha256_hash != approval.document_sha256:
        reasons.append(ApprovalStaleReason.document_hash_mismatch)
    if _claims_changed(fmt, snapshot, approval, ctx):
        reasons.append(ApprovalStaleReason.evidence_changed)
    if (
        ctx.asset.material_revision != approval.asset_material_revision
        or sha256_digest(asset_snapshot(ctx.asset)) != approval.asset_snapshot_digest
    ):
        reasons.append(ApprovalStaleReason.asset_information_changed)
    if ctx.asset.status != AssetStatus.active.value:
        reasons.append(ApprovalStaleReason.asset_not_active)
    if fmt == EVIDENCE_SNAPSHOT_FORMAT and _source_text_changed(snapshot, ctx):
        reasons.append(ApprovalStaleReason.source_text_changed)
    if _evidence_not_verified(fmt, snapshot, approval, ctx):
        reasons.append(ApprovalStaleReason.evidence_not_verified)
    return ApprovalCurrency(not reasons, reasons)


@dataclass
class ExtractionApprovalSummary:
    state: ExtractionApprovalState
    version_is_current: bool
    current_approvals: list[ReviewApproval]
    reasons: list[ApprovalStaleReason]
    currency: dict[str, ApprovalCurrency]

    @property
    def current_approval_types(self) -> list[ApprovalType]:
        return sorted({ApprovalType(a.approval_type) for a in self.current_approvals}, key=lambda t: t.value)


def summarize_extraction(ctx: ExtractionContext) -> ExtractionApprovalSummary:
    currency = {a.id: evaluate_approval(a, ctx) for a in ctx.approvals}
    current = [a for a in ctx.approvals if currency[a.id].is_current]
    if not ctx.approvals:
        state = ExtractionApprovalState.not_approved
    elif current:
        state = ExtractionApprovalState.current
    elif not ctx.version_is_current:
        state = ExtractionApprovalState.superseded
    else:
        state = ExtractionApprovalState.stale
    reasons: list[ApprovalStaleReason] = []
    if state in (ExtractionApprovalState.stale, ExtractionApprovalState.superseded):
        found = {r for c in currency.values() for r in c.reasons}
        reasons = [r for r in ApprovalStaleReason if r in found]
    return ExtractionApprovalSummary(state, ctx.version_is_current, current, reasons, currency)


# ── Creating a bound approval ────────────────────────────────────────────────


class EvidenceBindingError(RuntimeError):
    """Unverified evidence reached the binding; the approval gate must have rejected it first."""


def bind_approval(approval: ReviewApproval, ctx: ExtractionContext) -> ReviewApproval:
    """
    Fills the binding of a new approval from the state loaded under the write lock. The evidence
    is bound in format 2 and is re-verified here: unverified evidence is never bound.
    """
    version = ctx.extraction_version
    evidence = evidence_snapshot_v2(ctx)
    unverified = sorted(
        c["field_name"] for c in evidence["claims"]
        if c["evidence_status"] != (
            EvidenceStatus.not_applicable.value if c["is_unknown"] else EvidenceStatus.verified.value
        )
    )
    if unverified:
        raise EvidenceBindingError(f"unverified evidence for: {', '.join(unverified)}")
    asset = asset_snapshot(ctx.asset)
    approval.document_id = ctx.document.id
    approval.document_version_id = version.id
    approval.document_version_number = version.version_number
    approval.document_sha256 = version.sha256_hash
    approval.evidence_snapshot = canonical_json(evidence)
    approval.evidence_digest = sha256_digest(evidence)
    approval.asset_material_revision = ctx.asset.material_revision
    approval.asset_snapshot = canonical_json(asset)
    approval.asset_snapshot_digest = sha256_digest(asset)
    approval.binding_digest = sha256_digest(binding_payload(approval))
    return approval
