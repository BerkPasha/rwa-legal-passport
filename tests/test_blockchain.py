"""
Phase 6 — Stellar blockchain verification tests (8 tests).

All Horizon HTTP calls are isolated with MockStellarService.
No real network connection is required.

Coverage:
 1. Asset found on the network — full record returned
 2. Asset not found on the network — found_on_network=False
 3. Horizon network error — 503 Service Unavailable
 4. Asset not in the DB — 404 Not Found
 5. Disclaimer mandatory in every response
 6. The response never contains a private key
 7. A testnet asset uses the correct Horizon URL
 8. A mainnet asset uses the correct Horizon URL
"""

import pytest
from httpx import AsyncClient

from app.main import app
from app.services.stellar import get_stellar_service
from tests.conftest import TESTNET_ISSUER
from tests.mock_stellar import MockStellarService

# The _MERIDIAN_ASSET in conftest uses stellar-mainnet, so for the testnet test
# we define separate asset data.
_TESTNET_ASSET = {
    "issuer_id": TESTNET_ISSUER,
    "blockchain_network": "stellar-testnet",
    "stellar_asset_code": "TPROP",
    "stellar_issuer_public_key": "GDQOE23CFSUMSVQK4Y5JHPPYK73VYCNHZHA7ENKCV37P6SUEO6XQBKPP",
    "issuer_organization_name": "Testnet Property Fund",
    "asset_type": "real_estate",
    "jurisdiction": "US-CA",
}


# ── 1. Asset found on the network ─────────────────────────────────────────────


async def test_blockchain_status_found_on_network(
    admin_client: AsyncClient, created_asset: dict
):
    """
    MockStellarService "found" scenario:
    - found_on_network=True
    - record fields populated (asset_type, num_accounts, total_supply, flags)
    - asset_code and issuer_key passed through correctly
    """
    asset_id = created_asset["id"]
    r = await admin_client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 200
    body = r.json()

    assert body["found_on_network"] is True
    assert body["asset_id"] == asset_id
    assert body["asset_code"] == created_asset["stellar_asset_code"]
    assert body["issuer_key"] == created_asset["stellar_issuer_public_key"]

    record = body["record"]
    assert record is not None
    assert record["asset_type"] in {"credit_alphanum4", "credit_alphanum12"}
    assert record["num_accounts"] == 1523
    assert record["total_supply"] == "10000000.0000000"
    assert "auth_required" in record["flags"]
    assert "auth_revocable" in record["flags"]


# ── 2. Asset not found on the network ─────────────────────────────────────────


async def test_blockchain_status_not_found_on_network(
    admin_client: AsyncClient, created_asset: dict
):
    """
    In the "not_found" scenario found_on_network=False is returned;
    record=None, and the status code is still 200 (network access succeeded but the asset does not exist).
    """
    app.dependency_overrides[get_stellar_service] = lambda: MockStellarService(
        scenario="not_found"
    )

    asset_id = created_asset["id"]
    r = await admin_client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert body["found_on_network"] is False
    assert body["record"] is None


# ── 3. Horizon network error ──────────────────────────────────────────────────


async def test_blockchain_status_network_error_returns_503(
    admin_client: AsyncClient, created_asset: dict
):
    """When Horizon is unreachable the endpoint returns 503 Service Unavailable."""
    app.dependency_overrides[get_stellar_service] = lambda: MockStellarService(
        scenario="error"
    )

    asset_id = created_asset["id"]
    r = await admin_client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 503
    assert "Horizon" in r.json()["detail"]


# ── 4. Asset not in the DB ────────────────────────────────────────────────────


async def test_blockchain_status_asset_not_in_db_returns_404(admin_client: AsyncClient):
    """An asset ID that does not exist in the database → 404 Not Found."""
    r = await admin_client.get(
        "/assets/00000000-0000-0000-0000-000000000000/blockchain-status"
    )
    assert r.status_code == 404


# ── 5. Disclaimer mandatory ───────────────────────────────────────────────────


async def test_blockchain_disclaimer_always_present(
    admin_client: AsyncClient, created_asset: dict
):
    """
    The disclaimer field is present in every response and
    states explicitly that it makes no claim of legal ownership.
    """
    r = await admin_client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert "disclaimer" in body
    assert body["disclaimer"]  # not empty
    assert "legal ownership" in body["disclaimer"].lower()


# ── 6. A private key is never returned ────────────────────────────────────────


async def test_blockchain_response_contains_no_private_key_fields(
    admin_client: AsyncClient, created_asset: dict
):
    """
    The response never contains private_key, secret, seed or signing information.
    This test verifies that the endpoint design follows the read-only contract.
    """
    r = await admin_client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body_str = str(r.json())
    for forbidden in ("private_key", "secret_key", "seed", "signing"):
        assert forbidden not in body_str.lower(), (
            f"Response contains a forbidden field: {forbidden}"
        )


# ── 7. A testnet asset uses the correct Horizon URL ──────────────────────────


async def test_blockchain_testnet_uses_correct_horizon_url(
    admin_client: AsyncClient
):
    """
    For a stellar-testnet asset the response
    contains the horizon-testnet.stellar.org URL.
    """
    # Create a testnet asset
    asset_resp = await admin_client.post("/assets", json=_TESTNET_ASSET)
    assert asset_resp.status_code == 201
    asset_id = asset_resp.json()["id"]

    r = await admin_client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 200
    assert "horizon-testnet.stellar.org" in r.json()["horizon_url"]
    assert r.json()["network"] == "stellar-testnet"


# ── 8. A mainnet asset uses the correct Horizon URL ──────────────────────────


async def test_blockchain_mainnet_uses_correct_horizon_url(
    admin_client: AsyncClient, created_asset: dict
):
    """
    For a stellar-mainnet asset the response
    contains the horizon.stellar.org URL (not testnet).
    """
    r = await admin_client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert body["network"] == "stellar-mainnet"
    assert body["horizon_url"] == "https://horizon.stellar.org"
    assert "testnet" not in body["horizon_url"]
