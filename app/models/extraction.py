"""
AI yasal hak çıkarım modelleri.

LegalExtraction  — Bir belge versiyonuna ait tek çıkarım çalışması
LegalClaim       — Çıkarılan her yasal hak alanı için bir kayıt

Kritik tasarım kararları:
- status her zaman "draft" olarak başlar; otomatik onay yoktur.
- Veriler, onaylanmış kayıtlardan (Asset, Document) ayrı tablolarda tutulur.
- Silme veya güncelleme endpoint'i yoktur; geçmiş korunur.
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
    # Yalnızca "draft" — otomatik onay uygulanmaz
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="draft")
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False)
    # Yanıtta her zaman görünen sorumluluk reddi metni
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
    # Kanıt yetersizse "unknown" saklanır; asla uydurulmaz
    value: Mapped[str] = mapped_column(Text, nullable=False)
    is_unknown: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Kaynak referansları — kanıt iz kaydı
    source_page: Mapped[int] = mapped_column(Integer, nullable=True)
    supporting_passage: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
