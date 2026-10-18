"""
Stellar Testnet evidence anchoring responses (STELLAR_ANCHORING.md).

An anchor proves only that the configured anchoring account recorded a manifest digest on
Stellar Testnet at a ledger time. It does not establish legal validity, ownership, document
authenticity or independent legal approval.
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.enums import AnchorStatus, AnchorVerificationResult, BlockchainNetwork

ANCHOR_DISCLAIMER = (
    "BLOCKCHAIN ANCHOR ONLY — A Stellar transaction proves that the anchoring account recorded this "
    "evidence digest at a ledger time. It does not establish legal validity, ownership, regulatory "
    "compliance, the authenticity of documents or an independent legal approval. Stellar Testnet is "
    "reset periodically; Testnet anchors are for demonstration and testing."
)
VERIFICATION_MEANING = (
    "verified means: a successful Stellar Testnet transaction from the configured anchoring account "
    "carries this manifest digest (memo hash and manage_data entry), the stored manifest hashes to "
    "that digest, and the passport's current evidence manifest has exactly the same digest. Any other "
    "result is not a verification."
)


class StellarAnchorResponse(BaseModel):
    """A stored anchor (internal API)."""
    id: str
    asset_id: str
    network: BlockchainNetwork
    manifest_schema: str
    manifest_version: int
    manifest_digest: str = Field(description="SHA-256 (hex) of the canonical evidence manifest.")
    anchor_account: str = Field(description="Public key of the anchoring (signing) account.")
    data_name: str = Field(description="manage_data entry name written by the transaction.")
    transaction_hash: Optional[str] = None
    status: AnchorStatus = Field(description="pending, confirmed or failed, as last observed.")
    attempts: int
    ledger: Optional[int] = None
    ledger_closed_at: Optional[datetime] = None
    valid_until: Optional[datetime] = Field(
        default=None, description="End of the transaction's time bounds; never included afterwards."
    )
    failure_reason: Optional[str] = None
    requested_by: str
    created_at: datetime
    updated_at: datetime
    horizon_transaction_url: Optional[str] = None
    disclaimer: str = ANCHOR_DISCLAIMER


class AnchorAssetIdentity(BaseModel):
    identifier_type: str = Field(description="stellar_classic_asset")
    network: BlockchainNetwork
    asset_code: str
    asset_issuer: str
    stellar_asset_type: str = Field(description="credit_alphanum4 or credit_alphanum12")


class AnchorSummary(BaseModel):
    """The anchor that was verified (no internal identifiers)."""
    manifest_digest: str
    transaction_hash: Optional[str] = None
    status: AnchorStatus
    anchor_account: str
    data_name: str
    ledger: Optional[int] = None
    ledger_closed_at: Optional[datetime] = None
    anchored_at: datetime = Field(description="When the anchor was requested (server time).")
    horizon_transaction_url: Optional[str] = None


class OnchainCheck(BaseModel):
    """What the live network check found; null where it was not reached."""
    checked: bool
    network_passphrase_matches: Optional[bool] = None
    transaction_found: Optional[bool] = None
    successful: Optional[bool] = None
    source_account_matches: Optional[bool] = None
    memo_matches: Optional[bool] = None
    operation_matches: Optional[bool] = None
    ledger: Optional[int] = None
    ledger_closed_at: Optional[datetime] = None
    error: Optional[str] = None


class AnchorVerificationResponse(BaseModel):
    """Verification of a passport's evidence commitment against live Stellar network data."""
    asset: AnchorAssetIdentity
    manifest_schema: str
    manifest_version: int
    current_passport_available: bool = Field(
        description="False if the asset has no current approved passport (nothing current to compare)."
    )
    current_manifest_digest: Optional[str] = Field(
        default=None, description="SHA-256 of the evidence manifest recomputed now from the live records."
    )
    anchor: Optional[AnchorSummary] = None
    evidence_matches_anchor: bool = Field(
        description="True only if the current manifest digest equals the anchored digest."
    )
    onchain: OnchainCheck
    result: AnchorVerificationResult
    verified: bool = Field(description="True only for result 'verified'.")
    committed_manifest_canonical_json: Optional[str] = Field(
        default=None,
        description="The exact canonical JSON that was anchored; its SHA-256 is the anchored digest.",
    )
    meaning: str = VERIFICATION_MEANING
    disclaimer: str = ANCHOR_DISCLAIMER
    checked_at: datetime
