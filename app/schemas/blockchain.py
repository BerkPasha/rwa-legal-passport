from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.enums import BlockchainNetwork


class AssetFlagsResponse(BaseModel):
    """Stellar ihraçcı hesabının izin bayrakları."""
    auth_required: bool
    auth_revocable: bool
    auth_immutable: bool
    auth_clawback_enabled: bool


class HorizonRecordResponse(BaseModel):
    """Horizon'dan alınan ham varlık kaydının normalize edilmiş biçimi."""
    asset_type: str     # credit_alphanum4 veya credit_alphanum12
    num_accounts: int   # ağdaki token sahiplerinin sayısı
    total_supply: str   # toplam dolaşım miktarı (7 ondalık basamak)
    flags: AssetFlagsResponse


class BlockchainStatusResponse(BaseModel):
    """
    GET /assets/{id}/blockchain-status yanıtı.

    Önemli: found_on_network=True, yasal mülkiyeti kanıtlamaz.
    Yanıt her zaman disclaimer içerir.
    """
    asset_id: str
    asset_code: str
    issuer_key: str
    network: BlockchainNetwork
    horizon_url: str
    found_on_network: bool
    record: Optional[HorizonRecordResponse] = None
    retrieved_at: datetime
    # Her yanıtta zorunlu — blockchain kaydı ≠ yasal hak
    disclaimer: str
