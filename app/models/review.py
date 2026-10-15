"""
İnceleme ve onay iş akışı veritabanı modelleri.

ReviewCorrection — Her düzeltmenin değişmez denetim kaydı
ReviewApproval   — İnceleyici onayı; ihraç eden ve bağımsız hukuki onay ayrıştırılır

Tasarım ilkeleri:
- Hiçbir kayıt silinemez veya güncellenemez; tarihçe korunur.
- Onay türü inceleyicinin rolünden türetilir; elle değiştirilemez.
- Onay, yasal geçerliliği garanti etmez — bu durum disclaimer alanında açıkça belirtilir.
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

    # ── Denetim izi: değişiklik öncesi durum ─────────────────────────────────
    original_value: Mapped[str] = mapped_column(Text, nullable=False)
    original_is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    original_passage: Mapped[str] = mapped_column(Text, nullable=True)

    # ── Değişiklik sonrası durum ──────────────────────────────────────────────
    corrected_value: Mapped[str] = mapped_column(Text, nullable=False)
    corrected_is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False)
    corrected_passage: Mapped[str] = mapped_column(Text, nullable=True)

    correction_note: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )


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
    # Onay türü inceleyicinin rolünden türetilir; elle değiştirilemez
    approval_type: Mapped[str] = mapped_column(String(50), nullable=False)
    # Her onay kaydında yasal geçerlilik reddi zorunludur
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
