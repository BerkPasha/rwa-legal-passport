from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.enums import BlockchainNetwork


class AssetFlagsResponse(BaseModel):
    """Permission flags of the Stellar issuer account."""
    auth_required: bool
    auth_revocable: bool
    auth_immutable: bool
    auth_clawback_enabled: bool


class HorizonRecordResponse(BaseModel):
    """Normalized form of the raw asset record retrieved from Horizon."""
    asset_type: str     # credit_alphanum4 or credit_alphanum12
    num_accounts: int   # number of token holders on the network
    total_supply: str   # total circulating supply (7 decimal places)
    flags: AssetFlagsResponse


class BlockchainStatusResponse(BaseModel):
    """
    Response of GET /assets/{id}/blockchain-status.

    Important: found_on_network=True does not prove legal ownership.
    The response always includes the disclaimer.
    """
    asset_id: str
    asset_code: str
    issuer_key: str
    network: BlockchainNetwork
    horizon_url: str
    found_on_network: bool
    record: Optional[HorizonRecordResponse] = None
    retrieved_at: datetime
    # Mandatory in every response — blockchain record ≠ legal right
    disclaimer: str
