"""
Mock Stellar service — for automated tests.

Sends no request to the real Horizon API.
Supports three scenarios:
  "found"     — asset registered on the network (standard successful response)
  "not_found" — asset does not exist on the Stellar network
  "error"     — Horizon unreachable (network error simulation)
"""

from datetime import datetime, timezone

from app.enums import BlockchainNetwork
from app.services.stellar import (
    AssetFlags,
    HorizonAssetRecord,
    HORIZON_URLS,
    StellarAssetInfo,
    StellarNetworkError,
)


class MockStellarService:
    """Implements the same interface as StellarService; makes no HTTP call."""

    def __init__(self, scenario: str = "found") -> None:
        assert scenario in {"found", "not_found", "error"}
        self._scenario = scenario

    async def get_asset_info(
        self,
        asset_code: str,
        issuer_key: str,
        network: BlockchainNetwork,
    ) -> StellarAssetInfo:
        horizon_url = HORIZON_URLS[network]

        if self._scenario == "error":
            raise StellarNetworkError(
                f"Horizon connection timed out ({network.value})."
            )

        if self._scenario == "not_found":
            return StellarAssetInfo(
                found_on_network=False,
                network=network,
                horizon_url=horizon_url,
                retrieved_at=datetime.now(timezone.utc),
            )

        # "found": imitates a realistic Horizon record
        asset_type = (
            "credit_alphanum4" if len(asset_code) <= 4 else "credit_alphanum12"
        )
        return StellarAssetInfo(
            found_on_network=True,
            network=network,
            horizon_url=horizon_url,
            record=HorizonAssetRecord(
                asset_type=asset_type,
                num_accounts=1_523,
                total_supply="10000000.0000000",
                flags=AssetFlags(
                    auth_required=False,
                    auth_revocable=True,
                    auth_immutable=False,
                    auth_clawback_enabled=False,
                ),
            ),
            retrieved_at=datetime.now(timezone.utc),
        )
