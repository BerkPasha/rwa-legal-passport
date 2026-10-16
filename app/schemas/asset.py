from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.access_registry import ISSUER_ID_PATTERN
from app.enums import AssetStatus, AssetType, BlockchainNetwork
from app.validators.stellar import is_valid_stellar_asset_code, is_valid_stellar_public_key


class _BlockchainMeta(BaseModel):
    """
    Blockchain-level identity fields.
    These are set at creation time and cannot be changed afterwards —
    they uniquely identify the asset on the Stellar network.
    """

    blockchain_network: BlockchainNetwork
    stellar_asset_code: str = Field(..., min_length=1, max_length=12)
    stellar_issuer_public_key: str = Field(..., min_length=56, max_length=56)

    @field_validator("stellar_asset_code")
    @classmethod
    def _check_asset_code(cls, v: str) -> str:
        v = v.strip()  # Strip whitespace but do not upper-case — the API must stay strict
        if not is_valid_stellar_asset_code(v):
            raise ValueError(
                "stellar_asset_code must be 1-12 uppercase alphanumeric characters "
                "and cannot be 'XLM' (the native Stellar asset)."
            )
        return v

    @field_validator("stellar_issuer_public_key")
    @classmethod
    def _check_issuer_key(cls, v: str) -> str:
        v = v.strip()
        if not is_valid_stellar_public_key(v):
            raise ValueError(
                "stellar_issuer_public_key must be a valid 56-character Stellar public key "
                "starting with 'G' and containing only RFC 4648 base32 characters (A-Z, 2-7)."
            )
        return v


class _LegalMeta(BaseModel):
    """
    Legal and organizational metadata.
    These fields describe the real-world legal entity behind the token
    and can be updated as circumstances change.
    """

    issuer_organization_name: str = Field(..., min_length=1, max_length=255)
    asset_type: AssetType
    jurisdiction: str = Field(..., min_length=1, max_length=100)


# ── Public schemas ────────────────────────────────────────────────────────────


class _TenantMeta(BaseModel):
    """
    Owning issuer (tenant) — must reference an issuer in the access-control
    registry. Set at creation time and immutable afterwards.
    """

    issuer_id: str = Field(..., min_length=1, max_length=64, pattern=ISSUER_ID_PATTERN)


class AssetCreate(_BlockchainMeta, _TenantMeta, _LegalMeta):
    """Full payload required to register a new tokenized asset."""


class AssetUpdate(BaseModel):
    """
    Partial update schema — only legal/organizational fields may change.
    Blockchain identity fields (network, code, issuer key) and the owning
    issuer_id are immutable.
    """

    issuer_organization_name: Optional[str] = Field(None, min_length=1, max_length=255)
    asset_type: Optional[AssetType] = None
    jurisdiction: Optional[str] = Field(None, min_length=1, max_length=100)
    status: Optional[AssetStatus] = None


class AssetResponse(_BlockchainMeta, _TenantMeta, _LegalMeta):
    id: str
    status: AssetStatus
    created_at: datetime
    updated_at: datetime
    # Incremented on every material change; approvals bound to an older revision are stale
    material_revision: int

    model_config = ConfigDict(from_attributes=True)
