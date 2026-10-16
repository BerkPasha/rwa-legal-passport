"""
AI legal-rights extraction models.

LegalExtraction  — A single extraction run for one document version
LegalClaim       — One record for each extracted legal-rights field

Critical design decisions:
- status always starts as "draft"; there is no automatic approval.
- The data is kept in tables separate from the approved records (Asset, Document).
- There is no delete or update endpoint; the history is preserved.
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LegalExtraction(Base):
    __tablename__ = "legal_extractions"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    document_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id"), nullable=False, index=True
    )
    version_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("document_versions.id"), nullable=False
    )
    # "draft" only — automatic approval is never applied
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Disclaimer text that is always shown in the response
    disclaimer: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


class LegalClaim(Base):
    __tablename__ = "legal_claims"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    extraction_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("legal_extractions.id"), nullable=False, index=True
    )
    field_name: Mapped[str] = mapped_column(String(50), nullable=False)
    # If evidence is insufficient, "unknown" is stored; values are never fabricated
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Source references — evidence audit trail
    source_page: Mapped[int] = mapped_column(Integer, nullable=True)
    supporting_passage: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
