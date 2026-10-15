"""
Salt okunur Stellar Horizon ağ istemcisi.

Stellar Horizon REST API üzerinden httpx ile varlık bilgisi alır.
Hiçbir zaman özel anahtar, cüzdan oluşturma veya işlem imzalama gerektirmez.

API referansı: https://developers.stellar.org/api/horizon
Testnet Horizon: https://horizon-testnet.stellar.org
Mainnet Horizon: https://horizon.stellar.org

SINIRLAMALAR:
- Blockchain varlığı, yasal mülkiyetin kanıtı değildir.
- Bu servis ağ verisini olduğu gibi aktarır; hukuki yorum yapmaz.
- Yalnızca /assets endpoint'i kullanılır — okuma dışı işlem yoktur.
"""

from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import BaseModel

from app.enums import BlockchainNetwork

# ── Horizon endpoint URL'leri ──────────────────────────────────────────────────

HORIZON_URLS: dict[BlockchainNetwork, str] = {
    BlockchainNetwork.stellar_testnet: "https://horizon-testnet.stellar.org",
    BlockchainNetwork.stellar_mainnet: "https://horizon.stellar.org",
}

BLOCKCHAIN_DISCLAIMER = (
    "BLOCKCHAIN VERIFICATION ONLY — Asset presence on the Stellar network does NOT "
    "establish legal ownership, regulatory compliance, investment suitability, or any "
    "legally binding rights. Always consult qualified legal counsel for legal conclusions."
)


# ── İç veri modelleri ─────────────────────────────────────────────────────────


class AssetFlags(BaseModel):
    """Stellar varlık bayrakları — ihraçcının belirlediği izin kısıtlamaları."""
    auth_required: bool = False
    auth_revocable: bool = False
    auth_immutable: bool = False
    auth_clawback_enabled: bool = False


class HorizonAssetRecord(BaseModel):
    """Horizon /assets endpoint'inden normalize edilmiş tek kayıt."""
    asset_type: str          # credit_alphanum4 / credit_alphanum12
    num_accounts: int        # token tutan hesap sayısı
    total_supply: str        # toplam dolaşımdaki miktar (7 ondalık)
    flags: AssetFlags


class StellarAssetInfo(BaseModel):
    """StellarService.get_asset_info() dönüş tipi."""
    found_on_network: bool
    network: BlockchainNetwork
    horizon_url: str
    record: Optional[HorizonAssetRecord] = None
    retrieved_at: datetime


# ── Hata tipi ─────────────────────────────────────────────────────────────────


class StellarNetworkError(Exception):
    """Horizon API ulaşılamadı veya beklenmeyen HTTP yanıtı aldı."""


# ── Servis ───────────────────────────────────────────────────────────────────


class StellarService:
    """
    Salt okunur Stellar Horizon istemcisi.

    Horizon REST API ile doğrudan HTTP çağrıları yapar:
      GET {horizon_url}/assets?asset_code={code}&asset_issuer={key}

    Özel anahtar, cüzdan veya imzalama işlemi gerektirmez.
    Ağ hatalarında StellarNetworkError fırlatır; çağıran 503 döndürür.
    """

    _DEFAULT_TIMEOUT = 10.0

    def __init__(self, timeout: float = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    async def get_asset_info(
        self,
        asset_code: str,
        issuer_key: str,
        network: BlockchainNetwork,
    ) -> StellarAssetInfo:
        """
        Horizon'dan varlık bilgisi sorgular.

        - Varlık kayıtlıysa  → found_on_network=True, record dolu
        - Varlık bulunamazsa → found_on_network=False, record=None
        - Ağ hatası          → StellarNetworkError fırlatılır
        """
        horizon_url = HORIZON_URLS[network]
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(
                    f"{horizon_url}/assets",
                    params={"asset_code": asset_code, "asset_issuer": issuer_key},
                )
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise StellarNetworkError(
                f"Horizon bağlantısı zaman aşımına uğradı ({network.value})."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise StellarNetworkError(
                f"Horizon HTTP hatası: {exc.response.status_code} ({network.value})."
            ) from exc
        except httpx.RequestError as exc:
            raise StellarNetworkError(
                f"Horizon ağ hatası: {type(exc).__name__} ({network.value})."
            ) from exc

        records = response.json().get("_embedded", {}).get("records", [])
        if not records:
            return StellarAssetInfo(
                found_on_network=False,
                network=network,
                horizon_url=horizon_url,
                retrieved_at=datetime.now(timezone.utc),
            )

        raw = records[0]
        raw_flags = raw.get("flags", {})
        record = HorizonAssetRecord(
            asset_type=raw["asset_type"],
            num_accounts=raw.get("num_accounts", 0),
            total_supply=raw.get("amount", "0.0000000"),
            flags=AssetFlags(
                auth_required=raw_flags.get("auth_required", False),
                auth_revocable=raw_flags.get("auth_revocable", False),
                auth_immutable=raw_flags.get("auth_immutable", False),
                auth_clawback_enabled=raw_flags.get("auth_clawback_enabled", False),
            ),
        )
        return StellarAssetInfo(
            found_on_network=True,
            network=network,
            horizon_url=horizon_url,
            record=record,
            retrieved_at=datetime.now(timezone.utc),
        )


def get_stellar_service() -> StellarService:
    """FastAPI bağımlılığı — testlerde mock ile değiştirilir."""
    return StellarService()
