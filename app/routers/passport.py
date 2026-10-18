"""
Institutional Legal Transparency Passport endpoint.

GET /passport/{network}/{asset_code}/{issuer_key}

Returns currently approved information only — draft extractions are never shown.
API key authentication and request rate limiting are applied.
Every request is recorded with a structured log entry.

Security constraints:
- The X-API-Key header is mandatory; the key must be authorized for the 'passport:read' operation.
- A key only reads passports of the issuers / assets within its scope;
  an out-of-scope asset receives the same 404 response as a non-existent asset.
- Only current approvals are used (Stage 9B-3): an approval stops being current when a newer
  document version exists, when its evidence or binding no longer matches, when material asset
  information changes, or when the asset is not active. Stale information is never presented as
  currently approved: 409 is returned when no current approval remains.
- Each document of the asset contributes its current approved extraction. If approved documents
  state different values for a field, the field is reported as a conflict and no value is chosen.
- The stored PDF of every contributing document version is re-hashed on every read (Stage 9B-4,
  step 6); a missing or altered file excludes the document (source_file_integrity_failed).
- The source PDF file cannot be downloaded through this endpoint.
- Rate limit: by default 60 requests/minute/client.

Verification semantics (Stage 9B-4, step 6; see app/schemas/passport.py):
- Every field's evidence status is re-computed on every read from the stored page text of the
  approved document version (deterministic quotation check); a non-empty quotation proves nothing.
- verified: known, not conflicting, and source-verified in every approved source.
- independently_verified (field, source, document): additionally backed by a current approval of
  an independent legal reviewer. An approval of unknown fields never sets it.
- verification_level is derived from the verified fields only; a passport without any verified
  field is never reported above issuer_attested (verification_summary has the exact counts).
- value_origin separates AI-extracted values from manually entered (Stage 10B-3) and
  reviewer-corrected ones; superseded original values are never shown here (they stay in the
  internal correction history).
- Stage 9B-5B, step 1: verification_status reports source-field coverage regardless of approval
  roles; issuer_attestation_recorded and legal_reviewer_approval_recorded report the current
  approvals of the selected extractions only. verification_level is unchanged (legacy).
- None of this establishes legal correctness, enforceability, ownership, compliance or an
  independent legal opinion.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import cached_property

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import InstitutionalClient, safe_log_id, security_logger
from app.anchoring import verify_passport_anchor
from app.api_keys import require_operation
from app.approval_integrity import (
    ExtractionApprovalSummary,
    ExtractionContext,
    load_extraction_context,
    summarize_extraction,
)
from app.db.session import get_db
from app.enums import (
    ApprovalStaleReason,
    ApprovalType,
    AssetStatus,
    BlockchainNetwork,
    EvidenceStatus,
    ExtractionSource,
    LegalFieldName,
    ReviewerAttestationStatus,
    SourceVerificationStatus,
    ValueOrigin,
    VerificationLevel,
)
from app.evidence import EVIDENCE_SCOPE, MATCH_METHOD, NORMALIZATION_VERSION, EvidenceVerification
from app.evidence_records import verify_claims_for_version
from app.manual_claims import extraction_source
from app.models.asset import Asset
from app.models.document import Document, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction
from app.models.review import ReviewApproval
from app.permissions import InstitutionalOperation
from app.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.schemas.passport import (
    LEGAL_INTERPRETATION_NOT_ESTABLISHED,
    SOURCE_VERIFICATION_MEANING,
    ApprovalEntry,
    ApprovedSourceDocument,
    AssetSummary,
    DocumentReference,
    ExcludedDocument,
    ExtractionMetadata,
    LegalPassportResponse,
    LegalRightEntry,
    RightSource,
    VerificationSummary,
)
from app.schemas.stellar_anchor import AnchorVerificationResponse
from app.services.stellar_anchor import StellarAnchorService, get_anchor_service
from app.services.storage import StorageError, StorageService, get_storage

logger = logging.getLogger("rwa_passport")

PASSPORT_DISCLAIMER = (
    "INSTITUTIONAL USE ONLY — This legal passport reflects approved internal assessments "
    "based on issuer-provided documentation. It does NOT constitute legal advice, regulatory "
    "certification, investment recommendation, or a binding legal opinion under any jurisdiction. "
    "All information must be independently verified by qualified legal counsel before use."
)

CONFLICTING_VALUE = "conflicting"

router = APIRouter(tags=["Legal Passport"])


@dataclass
class _Source:
    """The current approved extraction of one document."""
    ctx: ExtractionContext
    summary: ExtractionApprovalSummary

    @cached_property
    def approvals(self) -> list[ReviewApproval]:
        """
        The current approvals the passport counts (Stage 9B-5C): one per reviewer (the latest), and
        never a legal approval of the reviewer's own correction. The records themselves are kept.
        """
        latest: dict[str, ReviewApproval] = {}
        for approval in self.summary.current_approvals:  # ordered by approval time
            if not self.ctx.is_self_reviewed(approval):
                latest[approval.reviewer_id] = approval
        return sorted(latest.values(), key=lambda a: a.approved_at)

    @property
    def approval_types(self) -> list[ApprovalType]:
        return sorted({ApprovalType(a.approval_type) for a in self.approvals}, key=lambda t: t.value)

    @property
    def latest_current_approval(self) -> datetime:
        return max(a.approved_at for a in self.approvals)

    @property
    def independent_reviewer_attested(self) -> bool:
        """A counted current approval by a legal reviewer exists (an attestation, not an opinion)."""
        return ApprovalType.legal_verification in self.approval_types

    @property
    def issuer_attested(self) -> bool:
        """A current issuer attestation of this extraction exists (the issuer's own statement)."""
        return ApprovalType.issuer_attestation in self.approval_types

    @cached_property
    def evidence(self) -> dict[str, EvidenceVerification]:
        """Deterministic check of every claim against the stored page text of its approved version."""
        return {
            claim.id: check
            for claim, check in verify_claims_for_version(self.ctx.claims, self.ctx.extraction.version_id, self.ctx.source)
        }

    def source_verified(self, claim: LegalClaim) -> bool:
        return not claim.is_unknown and self.evidence[claim.id].status is EvidenceStatus.verified

    def value_origin(self, claim: LegalClaim) -> ValueOrigin:
        # Correction identities are bound to the approval (snapshot format 2)
        if claim.id in self.ctx.latest_corrections:
            return ValueOrigin.reviewer_correction
        if extraction_source(self.ctx.extraction.ai_model) is ExtractionSource.manual:
            return ValueOrigin.manual_entry
        return ValueOrigin.ai_extraction


def _normalized(value: str) -> str:
    return " ".join(value.split()).casefold()


async def _stored_file_intact(storage: StorageService, version: DocumentVersion) -> bool:
    """The stored PDF still has the approved SHA-256 (read and hashed on every passport read)."""
    try:
        await asyncio.to_thread(storage.read_verified, version.stored_path, version.sha256_hash)
    except StorageError:  # missing, unreadable, unsafe path or altered bytes
        return False
    return True


async def _evaluate_asset(
    db: AsyncSession, asset: Asset, storage: StorageService
) -> tuple[list[_Source], list[ExcludedDocument], int]:
    """
    For each document: its current approved extraction, if any. Documents that only have
    non-current approvals are excluded, and so is a document whose stored source file no longer
    matches the approved SHA-256. Fail-closed (Stage 9B-5C, issue B5): a document with several
    current approved extractions is excluded instead of choosing one, and so is a document whose
    only current approvals are legal approvals of the reviewer's own correction.
    Returns (sources, excluded documents, number of approval records of the asset).
    """
    documents = (
        await db.execute(
            select(Document).where(Document.asset_id == asset.id).order_by(Document.created_at, Document.id)
        )
    ).scalars().all()
    sources: list[_Source] = []
    excluded: list[ExcludedDocument] = []
    approval_count = 0
    for document in documents:
        approved_ids = (
            await db.execute(
                select(LegalExtraction.id)
                .join(ReviewApproval, ReviewApproval.extraction_id == LegalExtraction.id)
                .where(LegalExtraction.document_id == document.id)
                .group_by(LegalExtraction.id)
                .order_by(func.max(ReviewApproval.approved_at))
            )
        ).scalars().all()
        candidates: list[_Source] = []
        latest_stale: ExtractionApprovalSummary | None = None
        for extraction_id in approved_ids:
            ctx = await load_extraction_context(db, extraction_id)
            summary = summarize_extraction(ctx)
            approval_count += len(ctx.approvals)
            if summary.current_approvals:
                candidates.append(_Source(ctx, summary))
            else:
                latest_stale = summary
        reasons = latest_stale.reasons if latest_stale is not None else None
        if len(candidates) > 1:
            # Never pick one of several approved extractions (e.g. by the latest approval)
            security_logger.error(
                "passport.competing_current_extractions document=%s extractions=%s",
                document.id, ",".join(s.ctx.extraction.id for s in candidates),
            )
            reasons = [ApprovalStaleReason.competing_current_extractions]
        elif candidates and not candidates[0].approvals:
            security_logger.warning("passport.self_reviewed_approval_only document=%s", document.id)
            reasons = [ApprovalStaleReason.self_reviewed_approval]
        elif candidates:
            (chosen,) = candidates
            if await _stored_file_intact(storage, chosen.ctx.extraction_version):
                sources.append(chosen)
                continue
            security_logger.error(
                "passport.source_file_integrity_failed document=%s version=%d",
                document.id, chosen.ctx.extraction_version.version_number,
            )
            reasons = [ApprovalStaleReason.source_file_integrity_failed]
        if reasons is not None:
            excluded.append(
                ExcludedDocument(
                    document_id=document.id,
                    title=document.title,
                    document_type=document.document_type,
                    reasons=reasons,
                )
            )
    sources.sort(key=lambda s: s.latest_current_approval)
    return sources, excluded, approval_count


def _document_reference(source: _Source) -> DocumentReference:
    return DocumentReference(
        document_id=source.ctx.document.id,
        title=source.ctx.document.title,
        document_type=source.ctx.document.document_type,
        version_number=source.ctx.extraction_version.version_number,
    )


def _approved_source(source: _Source) -> ApprovedSourceDocument:
    verified = sum(1 for c in source.ctx.claims if source.source_verified(c))
    return ApprovedSourceDocument(
        **_document_reference(source).model_dump(),
        approval_types=source.approval_types,
        # An independent approval of unknown fields only verifies nothing
        independently_verified=source.independent_reviewer_attested and verified > 0,
        source_verified_claim_count=verified,
        unknown_claim_count=sum(1 for c in source.ctx.claims if c.is_unknown),
    )


def _right_source(s: _Source, c: LegalClaim) -> RightSource:
    verified = s.source_verified(c)
    if c.is_unknown:
        attestation = ReviewerAttestationStatus.not_applicable
    elif s.independent_reviewer_attested:
        attestation = ReviewerAttestationStatus.independent_reviewer_attested
    else:
        attestation = ReviewerAttestationStatus.issuer_attested
    return RightSource(
        document_id=s.ctx.document.id,
        document_title=s.ctx.document.title,
        version_number=s.ctx.extraction_version.version_number,
        # An unknown claim states nothing, whatever text it still carries
        value=c.value if not c.is_unknown else "unknown",
        is_unknown=c.is_unknown,
        source_page=c.source_page if not c.is_unknown else None,
        supporting_passage=c.supporting_passage if not c.is_unknown else None,
        independently_verified=verified and s.independent_reviewer_attested,
        evidence_status=s.evidence[c.id].status,
        value_origin=s.value_origin(c),
        reviewer_attestation_status=attestation,
    )


def _without_value(name: str, value: str, is_unknown: bool, conflict: bool, sources: list[RightSource]) -> LegalRightEntry:
    """An unknown or conflicting field: no value is asserted, so nothing is verified or attested."""
    return LegalRightEntry(
        field_name=name, value=value, is_unknown=is_unknown, verified=False, independently_verified=False,
        conflict=conflict, sources=sources, evidence_status=EvidenceStatus.not_applicable,
        verification_method=MATCH_METHOD, reviewer_attestation_status=ReviewerAttestationStatus.not_applicable,
    )


def _legal_rights(sources: list[_Source]) -> list[LegalRightEntry]:
    field_names = sorted({c.field_name for s in sources for c in s.ctx.claims})
    entries: list[LegalRightEntry] = []
    for name in field_names:
        per_source = []
        for s in sources:
            claim = next((c for c in s.ctx.claims if c.field_name == name), None)
            if claim is None:
                continue
            per_source.append((s, claim))
        right_sources = [_right_source(s, c) for s, c in per_source]
        known = [(s, c) for s, c in per_source if not c.is_unknown]
        distinct = {_normalized(c.value) for _, c in known}
        if len(distinct) > 1:
            # Approved sources disagree: no value is chosen and nothing is reported as verified
            entries.append(_without_value(name, CONFLICTING_VALUE, False, True, right_sources))
            continue
        if not known:
            entries.append(_without_value(name, "unknown", True, False, right_sources))
            continue
        # Verified only if the quotation is source-verified in every approved source of the value
        verified_known = [(s, c) for s, c in known if s.source_verified(c)]
        verified = len(verified_known) == len(known)
        chosen_source, chosen = (verified_known or known)[-1]  # most recently approved source
        failing = next(
            (s.evidence[c.id].status for s, c in known if not s.source_verified(c)), EvidenceStatus.verified
        )
        independent = all(s.independent_reviewer_attested for s, _ in known)
        entries.append(LegalRightEntry(
            field_name=name,
            value=chosen.value,
            is_unknown=False,
            verified=verified,
            independently_verified=verified and independent,
            conflict=False,
            source_page=chosen.source_page if verified else None,
            supporting_passage=chosen.supporting_passage if verified else None,
            sources=right_sources,
            evidence_status=failing,
            verification_method=MATCH_METHOD,
            document_id=chosen_source.ctx.document.id if verified else None,
            document_version_number=chosen_source.ctx.extraction_version.version_number if verified else None,
            value_origin=chosen_source.value_origin(chosen),
            reviewer_attestation_status=(
                ReviewerAttestationStatus.independent_reviewer_attested if independent
                else ReviewerAttestationStatus.issuer_attested
            ),
        ))
    return entries


def _verification_level(entries: list[LegalRightEntry]) -> VerificationLevel:
    """Derived from the source-verified fields only; no verified field never rises above the floor."""
    verified = [e for e in entries if e.verified]
    if verified and all(e.independently_verified for e in verified):
        return VerificationLevel.independently_verified
    if any(e.independently_verified for e in verified):
        return VerificationLevel.partially_independently_verified
    return VerificationLevel.issuer_attested


def _verification_status(entries: list[LegalRightEntry]) -> SourceVerificationStatus:
    """Source-field coverage only; the approval roles behind the fields play no part."""
    verified = {e.field_name for e in entries if e.verified}
    if not verified:
        return SourceVerificationStatus.no_source_verified_fields
    # Complete only if every legal field is present and verified: an absent field counts as missing
    if all(e.verified for e in entries) and verified >= {f.value for f in LegalFieldName}:
        return SourceVerificationStatus.all_fields_source_verified
    return SourceVerificationStatus.partially_source_verified


def _verification_summary(entries: list[LegalRightEntry], sources: list[_Source]) -> VerificationSummary:
    known = [e for e in entries if not e.is_unknown and not e.conflict]
    # Only the current approvals of the selected extractions (sources) are counted
    legal_reviewer_approval = any(s.independent_reviewer_attested for s in sources)
    return VerificationSummary(
        source_verified_field_count=sum(1 for e in known if e.verified),
        independently_attested_field_count=sum(1 for e in known if e.independently_verified),
        unverified_field_count=sum(1 for e in known if not e.verified),
        unknown_field_count=sum(1 for e in entries if e.is_unknown),
        conflicting_field_count=sum(1 for e in entries if e.conflict),
        reviewer_attestation_recorded=bool(sources),
        independent_reviewer_attestation_recorded=legal_reviewer_approval,  # legacy name of the same fact
        issuer_attestation_recorded=any(s.issuer_attested for s in sources),
        legal_reviewer_approval_recorded=legal_reviewer_approval,
        evidence_scope=EVIDENCE_SCOPE,
        verification_method=MATCH_METHOD,
        normalization_version=NORMALIZATION_VERSION,
        legal_interpretation_status=LEGAL_INTERPRETATION_NOT_ESTABLISHED,
        meaning=SOURCE_VERIFICATION_MEANING,
    )


async def _scoped_passport_asset(
    network: str, asset_code: str, issuer_key: str, db: AsyncSession, client: InstitutionalClient,
    limiter: InMemoryRateLimiter,
) -> Asset:
    """Rate limit, network, asset lookup and institutional scope of a passport request (404 if out of scope)."""
    # ── 1. Rate limit check ───────────────────────────────────────────────────
    if not limiter.is_allowed(client.client_id):
        logger.warning(
            "passport.rate_limited client_id=%s org=%s asset=%s/%s",
            client.client_id,
            client.organization,
            network,
            asset_code,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please try again in a minute.",
        )

    # ── 2. Validate the blockchain network value ──────────────────────────────
    try:
        blockchain_network = BlockchainNetwork(network)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Invalid blockchain network: {network!r}.",
        )

    # ── 3. Find the asset by its blockchain identifiers ───────────────────────
    asset_result = await db.execute(
        select(Asset).where(
            Asset.blockchain_network == blockchain_network.value,
            Asset.stellar_asset_code == asset_code,
            Asset.stellar_issuer_public_key == issuer_key,
        )
    )
    asset = asset_result.scalar_one_or_none()
    if asset is None:
        logger.info(
            "passport.not_found client_id=%s asset=%s/%s/%s",
            client.client_id, network, safe_log_id(asset_code), safe_log_id(issuer_key[:8]),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found.",
        )

    # ── 3b. Institutional scope: a non-permitted asset gets 404, as if it did not exist ────
    if not client.can_access_asset(asset.id, asset.issuer_id):
        logger.warning(
            "passport.out_of_scope client_id=%s asset=%s/%s",
            client.client_id, network, safe_log_id(asset_code),
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Asset not found.",
        )

    return asset


@router.get(
    "/passport/{network}/{asset_code}/{issuer_key}",
    response_model=LegalPassportResponse,
    status_code=status.HTTP_200_OK,
    summary="Get the approved legal passport",
    description=(
        "Returns the currently approved legal rights and restrictions of a tokenized asset, "
        "identified by its blockchain identifiers. "
        "**Only current approvals are used — draft, under-review, stale or superseded information is "
        "never shown.** An approval is current only while its exact document version is the latest "
        "version, its evidence is unchanged, the material asset information is unchanged and the "
        "asset is active. Fields on which approved documents disagree are reported as conflicts.\n\n"
        "**Verification indicators.** `evidence_status` is the deterministic check, re-computed on "
        "every read, that the quotation appears on its cited page of the approved document version; "
        "`verified` requires it for every approved source of a known, non-conflicting value. "
        "`independently_verified` additionally requires a current approval by an independent legal "
        "reviewer; approving unknown fields never sets it. `verification_level` is derived from the "
        "verified fields only, and `verification_summary` reports source verification, reviewer "
        "attestation, independent reviewer attestation and legal interpretation separately. "
        "`verification_level` is a legacy indicator that can mislead when read alone: "
        "`verification_status` reports source-field coverage regardless of approval roles, and "
        "`verification_summary.issuer_attestation_recorded` / `legal_reviewer_approval_recorded` "
        "report the current approvals of the selected extractions (the legal reviewer role does not "
        "prove organizational independence). "
        "Fail-closed selection (Stage 9B-5C): a document with several approved extractions of its "
        "current version is excluded (`competing_current_extractions`) rather than resolved by the "
        "latest approval; one reviewer counts once per extraction, and a legal approval of the "
        "reviewer's own correction never counts (`self_reviewed_approval` if it is the only one). "
        "`value_origin` tells AI-extracted values from manually entered and reviewer-corrected ones, "
        "and `extraction_metadata.extraction_source` says whether the claims were typed by a user "
        "(`manual`, Stage 10B-3). The stored PDF of "
        "every contributing version is re-hashed on every read; a missing or altered file excludes "
        "the document. Source-text verification proves only that a quotation exists in the source "
        "document; it does not establish enforceability, ownership, entitlement, compliance, "
        "solvency or an independent legal opinion.\n\n"
        "Authentication with the X-API-Key header is required; a key can only read passports "
        "of the issuers / assets within its scope."
    ),
    responses={
        401: {"description": "Invalid API key."},
        403: {"description": "X-API-Key header missing, or the key lacks the passport:read permission."},
        404: {"description": "Asset not found / out of scope, or no approval exists yet."},
        409: {"description": "The asset is not active, or its approvals are no longer current (re-review required)."},
        429: {"description": "Request rate limit exceeded."},
    },
)
async def get_legal_passport(
    network: str,
    asset_code: str,
    issuer_key: str,
    db: AsyncSession = Depends(get_db),
    client: InstitutionalClient = Depends(require_operation(InstitutionalOperation.passport_read)),
    limiter: InMemoryRateLimiter = Depends(get_rate_limiter),
    storage: StorageService = Depends(get_storage),
) -> LegalPassportResponse:
    asset = await _scoped_passport_asset(network, asset_code, issuer_key, db, client, limiter)

    # ── 4. A suspended or retired asset never has a current passport ──────────
    if asset.status != AssetStatus.active.value:
        logger.info(
            "passport.asset_not_active client_id=%s asset=%s/%s status=%s",
            client.client_id, network, asset_code, asset.status,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The asset is not active; no current legal passport is available.",
        )

    # ── 5. Current approvals only (drafts and stale approvals are never shown) ─
    sources, excluded, approval_count = await _evaluate_asset(db, asset, storage)

    if not sources:
        if approval_count:
            reasons = sorted({r.value for d in excluded for r in d.reasons})
            logger.info(
                "passport.not_current client_id=%s asset=%s/%s reasons=%s",
                client.client_id, network, asset_code, ",".join(reasons),
            )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The legal approval for this asset is no longer current; a re-review is required.",
            )
        logger.info(
            "passport.no_approved_extraction client_id=%s asset=%s/%s",
            client.client_id, network, asset_code,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No approved legal passport is available for this asset yet.",
        )

    # ── 6. Build the response ─────────────────────────────────────────────────
    primary = sources[-1]  # most recently approved current source
    legal_rights = _legal_rights(sources)
    current_approvals = sorted((a for s in sources for a in s.approvals), key=lambda a: a.approved_at)
    approval_entries = [
        ApprovalEntry(
            approval_type=a.approval_type,
            reviewer_role=a.reviewer_role,
            approved_at=a.approved_at,
            notes=a.notes,
            document_id=a.document_id,
            document_version_number=a.document_version_number,
            binding_digest=a.binding_digest,
        )
        for a in current_approvals
    ]
    level = _verification_level(legal_rights)
    conflicts = [e.field_name for e in legal_rights if e.conflict]
    retrieved_at = datetime.now(timezone.utc)

    logger.info(
        "passport.served client_id=%s org=%s asset=%s/%s version=%d rights=%d sources=%d "
        "level=%s conflicts=%d excluded=%d",
        client.client_id,
        client.organization,
        network,
        asset_code,
        approval_count,
        len(legal_rights),
        len(sources),
        level.value,
        len(conflicts),
        len(excluded),
    )

    return LegalPassportResponse(
        asset=AssetSummary(
            asset_id=asset.id,
            asset_code=asset.stellar_asset_code,
            issuer_key=asset.stellar_issuer_public_key,
            network=BlockchainNetwork(asset.blockchain_network),
            issuer_organization=asset.issuer_organization_name,
            asset_type=asset.asset_type,
            jurisdiction=asset.jurisdiction,
            status=asset.status,
        ),
        verification_level=level,
        verification_status=_verification_status(legal_rights),
        legal_rights=legal_rights,
        approvals=approval_entries,
        source_document=_document_reference(primary),
        source_documents=[_approved_source(s) for s in sources],
        excluded_documents=excluded,
        extraction_metadata=ExtractionMetadata(
            extraction_id=primary.ctx.extraction.id,
            ai_model=primary.ctx.extraction.ai_model,
            extraction_source=extraction_source(primary.ctx.extraction.ai_model),
            extraction_timestamp=primary.ctx.extraction.extraction_timestamp,
            passport_version=approval_count,
            effective_date=max(a.approved_at for a in current_approvals),
        ),
        verification_summary=_verification_summary(legal_rights, sources),
        disclaimer=PASSPORT_DISCLAIMER,
        retrieved_at=retrieved_at,
    )


@router.get(
    "/passport/{network}/{asset_code}/{issuer_key}/stellar-anchor",
    response_model=AnchorVerificationResponse,
    status_code=status.HTTP_200_OK,
    summary="Verify the passport's evidence commitment against Stellar Testnet",
    description=(
        "Recomputes the passport's evidence manifest from the live records and checks the anchored "
        "commitment on the live Stellar network (Horizon): the transaction must be successful, come "
        "from the configured anchoring account, and carry the manifest digest as memo hash and as the "
        "manage_data entry `lp:<asset id>`. `result` is `verified` only if all of this holds AND the "
        "current manifest has the anchored digest; pending, failed, missing (e.g. after a Testnet "
        "reset), mismatched or unreachable commitments are never reported as verified. "
        "`committed_manifest_canonical_json` is the exact anchored text: its SHA-256 is the digest.\n\n"
        "**An anchor is not a legal approval.** It proves that the anchoring account recorded the digest "
        "at a ledger time; it does not establish legal validity, ownership or document authenticity.\n\n"
        "Authentication with the X-API-Key header (passport:read) and the same scope as the passport."
    ),
    responses={
        401: {"description": "Invalid API key."},
        403: {"description": "X-API-Key header missing, or the key lacks the passport:read permission."},
        404: {"description": "Asset not found / out of scope."},
        429: {"description": "Request rate limit exceeded."},
    },
)
async def verify_passport_stellar_anchor(
    network: str,
    asset_code: str,
    issuer_key: str,
    db: AsyncSession = Depends(get_db),
    client: InstitutionalClient = Depends(require_operation(InstitutionalOperation.passport_read)),
    limiter: InMemoryRateLimiter = Depends(get_rate_limiter),
    storage: StorageService = Depends(get_storage),
    anchors: StellarAnchorService = Depends(get_anchor_service),
) -> AnchorVerificationResponse:
    asset = await _scoped_passport_asset(network, asset_code, issuer_key, db, client, limiter)
    verification = await verify_passport_anchor(db, asset, storage, anchors)
    logger.info(
        "passport.anchor_verification client_id=%s asset=%s/%s result=%s",
        client.client_id, network, asset_code, verification.result.value,
    )
    return verification
