"""
Stellar Testnet evidence anchor (STELLAR_ANCHORING.md).

One row per committed evidence manifest of an asset: the manifest itself (canonical JSON, kept
off-chain), its SHA-256 digest (committed on-chain) and the anchoring transaction. A manifest
digest is anchored at most once per asset and network (idempotent submission); a failed attempt
is retried on the same row. Rows are never deleted.
"""

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class StellarAnchor(Base):
    __tablename__ = "stellar_anchors"
    __table_args__ = (
        UniqueConstraint("asset_id", "network", "manifest_digest", name="uq_stellar_anchor_manifest"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    asset_id: Mapped[str] = mapped_column(String(36), ForeignKey("assets.id"), nullable=False, index=True)
    network: Mapped[str] = mapped_column(String(50), nullable=False)

    # ── What is committed ────────────────────────────────────────────────────
    manifest_schema: Mapped[str] = mapped_column(String(100), nullable=False)
    manifest_version: Mapped[int] = mapped_column(Integer, nullable=False)
    manifest_digest: Mapped[str] = mapped_column(String(64), nullable=False)  # SHA-256 hex of manifest_json
    manifest_json: Mapped[str] = mapped_column(Text, nullable=False)          # canonical JSON, off-chain only

    # ── How it is committed ──────────────────────────────────────────────────
    anchor_account: Mapped[str] = mapped_column(String(56), nullable=False)   # signing account (public key)
    data_name: Mapped[str] = mapped_column(String(64), nullable=False)        # manage_data entry name
    transaction_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, unique=True)
    envelope_xdr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # signed envelope (public data)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # ── Outcome ──────────────────────────────────────────────────────────────
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    ledger: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    ledger_closed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_reason: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    requested_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
