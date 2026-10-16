"""
Source-evidence checks on stored records (Stage 9B-4, steps 2 and 4).

Connects the pure verifier (app/evidence.py) to the database. The page text is taken only
from the DocumentPage records of the extraction's own document version (extraction.version_id):
never from a newer version, another document, another tenant or the AI output. If that version
does not exist or does not belong to the extraction's document, no text is used and every
check fails closed.

Results are computed on every use. Used by the extraction API (evidence of every claim), by the
approval gate (approval_evidence_failures, step 4) and by the evidence binding and currency
evaluation of approvals (app/approval_integrity.py, step 5).
"""

from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import EvidenceStatus
from app.evidence import EvidenceClaim, EvidenceVerification, SourceDocumentText, verify_evidence
from app.models.document import DocumentPage, DocumentVersion
from app.models.extraction import LegalClaim, LegalExtraction


@dataclass(frozen=True)
class ExtractionSourceText:
    """The authoritative page text of an extraction's document version."""
    text: SourceDocumentText
    version_number: int | None  # None if the extraction's version is unavailable


async def load_extraction_source_text(db: AsyncSession, extraction: LegalExtraction) -> ExtractionSourceText:
    """Loads the live page records of exactly extraction.version_id (bypassing session caches)."""
    fresh = {"populate_existing": True}
    version = (
        await db.execute(
            select(DocumentVersion).where(DocumentVersion.id == extraction.version_id).execution_options(**fresh)
        )
    ).scalar_one_or_none()
    if version is None or version.document_id != extraction.document_id:
        # No fallback to any other version or document: without authoritative text, nothing verifies
        return ExtractionSourceText(SourceDocumentText(extraction.version_id, ()), None)
    pages = (
        await db.execute(
            select(DocumentPage)
            .where(DocumentPage.version_id == version.id)
            .order_by(DocumentPage.page_number, DocumentPage.id)
            .execution_options(**fresh)
        )
    ).scalars().all()
    return ExtractionSourceText(SourceDocumentText.from_page_records(version.id, pages), version.version_number)


def verify_claim_evidence(
    claim: LegalClaim, extraction: LegalExtraction, source: ExtractionSourceText
) -> EvidenceVerification:
    """Checks one stored claim against its extraction's own document version."""
    return verify_evidence(
        EvidenceClaim(
            document_version_id=extraction.version_id,
            source_page=claim.source_page,
            quotation=claim.supporting_passage,
            is_unknown=bool(claim.is_unknown),
        ),
        source.text,
    )


# ── Approval gate (Stage 9B-4, step 4) ───────────────────────────────────────


@dataclass(frozen=True)
class ApprovalEvidenceFailure:
    """A claim that blocks an approval, and why. Carries no claim content or page text."""
    claim_id: str
    field_name: str
    status: EvidenceStatus


def accepted_status(claim: LegalClaim) -> EvidenceStatus:
    """The only evidence status a claim may have in an approval: unknown claims are never promoted."""
    return EvidenceStatus.not_applicable if claim.is_unknown else EvidenceStatus.verified


def verify_claims_for_version(
    claims: Iterable[LegalClaim], version_id: str, source: ExtractionSourceText
) -> list[tuple[LegalClaim, EvidenceVerification]]:
    """
    Checks every claim, as stored now, against the page text of exactly version_id, in a fixed
    order (field name, claim id). Shared by the approval gate, the evidence snapshot of a new
    approval and the currency evaluation of existing approvals (app/approval_integrity.py).
    """
    return [
        (
            claim,
            verify_evidence(
                EvidenceClaim(
                    document_version_id=version_id,
                    source_page=claim.source_page,
                    quotation=claim.supporting_passage,
                    is_unknown=bool(claim.is_unknown),
                ),
                source.text,
            ),
        )
        for claim in sorted(claims, key=lambda c: (c.field_name, c.id))
    ]


def approval_evidence_failures(
    claims: Iterable[LegalClaim], approved_version_id: str, source: ExtractionSourceText
) -> list[ApprovalEvidenceFailure]:
    """
    Every known claim must be `verified` against the page text of exactly the document version
    the approval will be bound to; any other result blocks the approval. The claims are checked
    as stored now, so reviewer corrections are included. Unknown claims state nothing: they need
    no evidence and are never promoted (not_applicable). Failures come in a fixed order.
    """
    return [
        ApprovalEvidenceFailure(claim.id, claim.field_name, check.status)
        for claim, check in verify_claims_for_version(claims, approved_version_id, source)
        if check.status is not accepted_status(claim)
    ]
