"""
Database models for the review and approval workflow.

ReviewCorrection — Immutable audit record of every correction
ReviewApproval   — Reviewer approval; issuer and independent legal approvals are kept distinct

Design principles:
- No record can be deleted or updated; the history is preserved.
- The approval type is derived from the reviewer's role; it cannot be changed manually.
- Approval does not guarantee legal validity — this is stated explicitly in the disclaimer field.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ReviewCorrection(Base):
    __tablename__ = "review_corrections"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    extraction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("legal_extractions.id"), nullable=False, index=True
    )
    claim_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("legal_claims.id"), nullable=False
    )
    reviewer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(50), nullable=False)

    # ── Audit trail: state before the change ─────────────────────────────────
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    original_is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    original_passage: Mapped[str] = mapped_column(Text, nullable=True)

    # ── State after the change ────────────────────────────────────────────────
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    corrected_passage: Mapped[str] = mapped_column(Text, nullable=True)

    correction_note: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Source page history (Stage 9B-4, step 7) ─────────────────────────────
    # Appended last, as the migration (scripts/migrate_9b4_correction_evidence.py) adds them.
    # NULL in records created before step 7, which did not record pages (evidence_audit_version NULL).
    original_source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrected_source_page: Mapped[int | None] = mapped_column(Integer, nullable=True)
    evidence_audit_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    @property
    def page_history_recorded(self) -> bool:
        """False for legacy records: their source pages were never recorded."""
        return self.evidence_audit_version is not None


class ReviewApproval(Base):
    __tablename__ = "review_approvals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    extraction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("legal_extractions.id"), nullable=False, index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(100), nullable=False)
    reviewer_role: Mapped[str] = mapped_column(String(50), nullable=False)
    # The approval type is derived from the reviewer's role; it cannot be changed manually
    approval_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Every approval record must carry the legal-validity disclaimer
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # ── Approval binding (Stage 9B-3) ────────────────────────────────────────
    # What exactly was approved: the document version and its SHA-256, the evidence
    # (claims) and the material asset information, each with a SHA-256 digest, plus one
    # digest over the whole binding. Written once at approval time and never updated.
    # NULL only for legacy approvals created before Stage 9B-3; those are never current.
    document_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id"), nullable=True
    )
    document_version_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_versions.id"), nullable=True
    )
    document_version_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    document_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Canonical JSON list of the approved claims (historical snapshot)
    evidence_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    asset_material_revision: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Canonical JSON object of the material asset information (historical snapshot)
    asset_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    asset_snapshot_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    binding_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
