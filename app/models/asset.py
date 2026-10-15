import uuid
from datetime import datetime, timezone

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Asset(Base):
    __tablename__ = "assets"
    __table_args__ = (
        # Same Stellar asset code + issuer pair must be globally unique
        UniqueConstraint("stellar_asset_code", "stellar_issuer_public_key", name="uq_stellar_asset"),
    )

    # ── Primary key ──────────────────────────────────────────────────────────
    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )

    # ── Blockchain metadata (immutable after creation) ────────────────────────
    blockchain_network: Mapped[str] = mapped_column(String(50), nullable=False)
    stellar_asset_code: Mapped[str] = mapped_column(String(12), nullable=False)
    stellar_issuer_public_key: Mapped[str] = mapped_column(String(56), nullable=False)

    # ── Legal / organizational metadata (mutable) ────────────────────────────
    issuer_organization_name: Mapped[str] = mapped_column(String(255), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(50), nullable=False)
    jurisdiction: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")

    # ── Audit timestamps ──────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
