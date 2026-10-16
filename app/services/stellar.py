"""
Read-only Stellar Horizon network client.

Retrieves asset information through the Stellar Horizon REST API with httpx.
Never requires a private key, wallet creation or transaction signing.

API reference: https://developers.stellar.org/api/horizon
Testnet Horizon: https://horizon-testnet.stellar.org
Mainnet Horizon: https://horizon.stellar.org

LIMITATIONS:
- Presence on the blockchain is not proof of legal ownership.
- This service passes the network data through as is; it makes no legal interpretation.
- Only the /assets endpoint is used — there are no non-read operations.
"""

from datetime import datetime, timezone
from typing import Optional

import httpx
from pydantic import BaseModel

from app.enums import BlockchainNetwork

# ── Horizon endpoint URLs ──────────────────────────────────────────────────────

HORIZON_URLS: dict[BlockchainNetwork, str] = {
    BlockchainNetwork.stellar_testnet: "https://horizon-testnet.stellar.org",
    BlockchainNetwork.stellar_mainnet: "https://horizon.stellar.org",
}

BLOCKCHAIN_DISCLAIMER = (
    "BLOCKCHAIN VERIFICATION ONLY — Asset presence on the Stellar network does NOT "
    "establish legal ownership, regulatory compliance, investment suitability, or any "
    "legally binding rights. Always consult qualified legal counsel for legal conclusions."
)


# ── Internal data models ──────────────────────────────────────────────────────


class AssetFlags(BaseModel):
    """Stellar asset flags — permission restrictions set by the issuer."""
    auth_required: bool = False
    auth_revocable: bool = False
    auth_immutable: bool = False
    auth_clawback_enabled: bool = False


class HorizonAssetRecord(BaseModel):
    """A single normalized record from the Horizon /assets endpoint."""
    asset_type: str          # credit_alphanum4 / credit_alphanum12
    num_accounts: int        # number of accounts holding the token
    total_supply: str        # total circulating amount (7 decimals)
    flags: AssetFlags


class StellarAssetInfo(BaseModel):
    """Return type of StellarService.get_asset_info()."""
    found_on_network: bool
    network: BlockchainNetwork
    horizon_url: str
    record: Optional[HorizonAssetRecord] = None
    retrieved_at: datetime


# ── Error type ────────────────────────────────────────────────────────────────


class StellarNetworkError(Exception):
    """The Horizon API could not be reached or returned an unexpected HTTP response."""


# ── Service ──────────────────────────────────────────────────────────────────


class StellarService:
    """
    Read-only Stellar Horizon client.

    Makes direct HTTP calls to the Horizon REST API:
      GET {horizon_url}/assets?asset_code={code}&asset_issuer={key}

    Requires no private key, wallet or signing operation.
    Raises StellarNetworkError on network errors; the caller returns 503.
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
        Queries Horizon for asset information.

        - Asset registered    → found_on_network=True, record populated
        - Asset not found     → found_on_network=False, record=None
        - Network error       → StellarNetworkError is raised
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
                f"Horizon connection timed out ({network.value})."
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise StellarNetworkError(
                f"Horizon HTTP error: {exc.response.status_code} ({network.value})."
            ) from exc
        except httpx.RequestError as exc:
            raise StellarNetworkError(
                f"Horizon network error: {type(exc).__name__} ({network.value})."
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
    """FastAPI dependency — replaced with a mock in tests."""
    return StellarService()
