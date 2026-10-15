"""
Phase 6 — Stellar blockchain doğrulama testleri (8 test).

Tüm Horizon HTTP çağrıları MockStellarService ile izole edilir.
Gerçek ağ bağlantısı gerektirmez.

Kapsam:
 1. Varlık ağda bulundu — tam kayıt döner
 2. Varlık ağda bulunamadı — found_on_network=False
 3. Horizon ağ hatası — 503 Service Unavailable
 4. DB'de olmayan varlık — 404 Not Found
 5. Disclaimer her yanıtta zorunlu
 6. Yanıt hiçbir zaman özel anahtar içermez
 7. Testnet varlığı doğru Horizon URL'i kullanır
 8. Mainnet varlığı doğru Horizon URL'i kullanır
"""

import pytest
from httpx import AsyncClient

from app.main import app
from app.services.stellar import get_stellar_service
from tests.mock_stellar import MockStellarService

# Testnet varlığı için conftest'teki _MERIDIAN_ASSET stellar-mainnet kullanır.
# Testnet testi için ayrı bir varlık verisi tanımlıyoruz.
_TESTNET_ASSET = {
    "blockchain_network": "stellar-testnet",
    "stellar_asset_code": "TPROP",
    "stellar_issuer_public_key": "GDQOE23CFSUMSVQK4Y5JHPPYK73VYCNHZHA7ENKCV37P6SUEO6XQBKPP",
    "issuer_organization_name": "Testnet Property Fund",
    "asset_type": "real_estate",
    "jurisdiction": "US-CA",
}


# ── 1. Varlık ağda bulundu ────────────────────────────────────────────────────


async def test_blockchain_status_found_on_network(
    client: AsyncClient, created_asset: dict
):
    """
    MockStellarService "found" senaryosu:
    - found_on_network=True
    - record alanları dolu (asset_type, num_accounts, total_supply, flags)
    - asset_code ve issuer_key doğru aktarılır
    """
    asset_id = created_asset["id"]
    r = await client.get(f"/assets/{asset_id}/blockchain-status")
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


# ── 2. Varlık ağda bulunamadı ─────────────────────────────────────────────────


async def test_blockchain_status_not_found_on_network(
    client: AsyncClient, created_asset: dict
):
    """
    "not_found" senaryosunda found_on_network=False döner;
    record=None, durum kodu yine 200 (ağ erişimi başarılı ama varlık yok).
    """
    app.dependency_overrides[get_stellar_service] = lambda: MockStellarService(
        scenario="not_found"
    )

    asset_id = created_asset["id"]
    r = await client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert body["found_on_network"] is False
    assert body["record"] is None


# ── 3. Horizon ağ hatası ──────────────────────────────────────────────────────


async def test_blockchain_status_network_error_returns_503(
    client: AsyncClient, created_asset: dict
):
    """Horizon ulaşılamadığında endpoint 503 Service Unavailable döner."""
    app.dependency_overrides[get_stellar_service] = lambda: MockStellarService(
        scenario="error"
    )

    asset_id = created_asset["id"]
    r = await client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 503
    assert "Horizon" in r.json()["detail"]


# ── 4. DB'de olmayan varlık ───────────────────────────────────────────────────


async def test_blockchain_status_asset_not_in_db_returns_404(client: AsyncClient):
    """Veritabanında bulunmayan varlık ID'si → 404 Not Found."""
    r = await client.get(
        "/assets/00000000-0000-0000-0000-000000000000/blockchain-status"
    )
    assert r.status_code == 404


# ── 5. Disclaimer zorunlu ─────────────────────────────────────────────────────


async def test_blockchain_disclaimer_always_present(
    client: AsyncClient, created_asset: dict
):
    """
    Her yanıtta disclaimer alanı mevcut ve
    yasal mülkiyet iddiasında bulunmadığını açıkça belirtir.
    """
    r = await client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert "disclaimer" in body
    assert body["disclaimer"]  # boş değil
    assert "legal ownership" in body["disclaimer"].lower() or "yasal" in body["disclaimer"].lower()


# ── 6. Özel anahtar hiçbir zaman döndürülmez ──────────────────────────────────


async def test_blockchain_response_contains_no_private_key_fields(
    client: AsyncClient, created_asset: dict
):
    """
    Yanıt hiçbir zaman private_key, secret, seed veya signing bilgisi içermez.
    Bu test, endpoint tasarımının salt-okuma sözleşmesine uyduğunu doğrular.
    """
    r = await client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body_str = str(r.json())
    for forbidden in ("private_key", "secret_key", "seed", "signing"):
        assert forbidden not in body_str.lower(), (
            f"Yanıt yasak alan içeriyor: {forbidden}"
        )


# ── 7. Testnet varlığı doğru Horizon URL'i kullanır ──────────────────────────


async def test_blockchain_testnet_uses_correct_horizon_url(
    client: AsyncClient
):
    """
    stellar-testnet varlığı için yanıt,
    horizon-testnet.stellar.org URL'ini içerir.
    """
    # Testnet varlığı oluştur
    asset_resp = await client.post("/assets", json=_TESTNET_ASSET)
    assert asset_resp.status_code == 201
    asset_id = asset_resp.json()["id"]

    r = await client.get(f"/assets/{asset_id}/blockchain-status")
    assert r.status_code == 200
    assert "horizon-testnet.stellar.org" in r.json()["horizon_url"]
    assert r.json()["network"] == "stellar-testnet"


# ── 8. Mainnet varlığı doğru Horizon URL'i kullanır ──────────────────────────


async def test_blockchain_mainnet_uses_correct_horizon_url(
    client: AsyncClient, created_asset: dict
):
    """
    stellar-mainnet varlığı için yanıt,
    horizon.stellar.org URL'ini içerir (testnet değil).
    """
    r = await client.get(f"/assets/{created_asset['id']}/blockchain-status")
    assert r.status_code == 200
    body = r.json()
    assert body["network"] == "stellar-mainnet"
    assert body["horizon_url"] == "https://horizon.stellar.org"
    assert "testnet" not in body["horizon_url"]
