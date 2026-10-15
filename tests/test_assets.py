"""
Phase 2 — Tokenized Asset Registry testleri.

Kurgusal test verileri kullanılmaktadır; gerçek bir şirketi veya varlığı temsil etmez.
Stellar public key'ler format doğrulaması için manuel olarak oluşturulmuştur
(56 karakter, G ile başlar, yalnızca RFC 4648 base32 karakterleri: A-Z ve 2-7).
"""

import pytest
from httpx import AsyncClient

# ── Kurgusal test verisi ──────────────────────────────────────────────────────

# Geçerli bir Stellar public key: 56 karakter, G ile başlar, A-Z ve 2-7 içerir
_KEY_MERIDIAN = "GCEZFBCKNOXYELQZFQ7JKPWQABYOPZKVIPQLNOILMWMEXGWAF7RQJCUQ"
_KEY_AGROCOMM = "GBVERHXLKQNF5ZPAW7MXJTDYRVCSUIQENBAHAGKFZ3PLMWT2YDRUQQXY"

MERIDIAN_ASSET = {
    "blockchain_network": "stellar-mainnet",
    "stellar_asset_code": "MRPROP",
    "stellar_issuer_public_key": _KEY_MERIDIAN,
    "issuer_organization_name": "Meridian Property Holdings LLC",
    "asset_type": "real_estate",
    "jurisdiction": "US-NY",
}

AGROCOMM_ASSET = {
    "blockchain_network": "stellar-testnet",
    "stellar_asset_code": "AGRCOM",
    "stellar_issuer_public_key": _KEY_AGROCOMM,
    "issuer_organization_name": "AgroComm Finance SA",
    "asset_type": "commodity",
    "jurisdiction": "BR",
}


# ── Başarılı senaryolar ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_asset_returns_201(client: AsyncClient):
    """Geçerli varlık kaydı 201 ve doğru alanlarla dönmeli."""
    response = await client.post("/assets", json=MERIDIAN_ASSET)

    assert response.status_code == 201
    data = response.json()
    assert data["stellar_asset_code"] == "MRPROP"
    assert data["issuer_organization_name"] == "Meridian Property Holdings LLC"
    assert data["status"] == "active"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_list_assets_empty_on_start(client: AsyncClient):
    """Henüz varlık eklenmemişken liste boş dönmeli."""
    response = await client.get("/assets")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_assets_after_creation(client: AsyncClient):
    """İki varlık eklendikten sonra liste ikisini de içermeli."""
    await client.post("/assets", json=MERIDIAN_ASSET)
    await client.post("/assets", json=AGROCOMM_ASSET)

    response = await client.get("/assets")

    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_asset_by_id(client: AsyncClient):
    """Var olan bir varlık ID ile doğru şekilde getirilmeli."""
    create_resp = await client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]

    response = await client.get(f"/assets/{asset_id}")

    assert response.status_code == 200
    assert response.json()["id"] == asset_id
    assert response.json()["blockchain_network"] == "stellar-mainnet"


@pytest.mark.asyncio
async def test_update_asset_status(client: AsyncClient):
    """Varlık durumu 'suspended' olarak güncellenebilmeli."""
    create_resp = await client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]

    response = await client.patch(f"/assets/{asset_id}", json={"status": "suspended"})

    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    # Blockchain kimlik alanları değişmemeli
    assert response.json()["stellar_asset_code"] == "MRPROP"


@pytest.mark.asyncio
async def test_update_asset_organization_name(client: AsyncClient):
    """Organizasyon adı güncellenebilmeli, diğer alanlar korunmalı."""
    create_resp = await client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]
    new_name = "Meridian Property Holdings International LLC"

    response = await client.patch(
        f"/assets/{asset_id}", json={"issuer_organization_name": new_name}
    )

    assert response.status_code == 200
    assert response.json()["issuer_organization_name"] == new_name
    assert response.json()["jurisdiction"] == "US-NY"  # değişmemeli


@pytest.mark.asyncio
async def test_create_response_separates_blockchain_and_legal_fields(client: AsyncClient):
    """
    Yanıt hem blockchain metadata hem de yasal metadata içermeli —
    bu iki kavramın ayrı tutulduğunu doğrular.
    """
    response = await client.post("/assets", json=MERIDIAN_ASSET)
    data = response.json()

    blockchain_fields = {"blockchain_network", "stellar_asset_code", "stellar_issuer_public_key"}
    legal_fields = {"issuer_organization_name", "asset_type", "jurisdiction", "status"}

    for field in blockchain_fields | legal_fields:
        assert field in data, f"Eksik alan: {field}"


# ── Hata / doğrulama senaryoları ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_asset_invalid_code_too_long(client: AsyncClient):
    """13 karakterli asset code 422 döndürmeli (maksimum 12)."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "TOOLONGCODE13"}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_code_xlm(client: AsyncClient):
    """'XLM' native asset olduğu için kabul edilmemeli."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "XLM"}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_code_lowercase(client: AsyncClient):
    """Küçük harfli asset code kabul edilmemeli."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "mrprop"}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_too_short(client: AsyncClient):
    """56 karakterden kısa public key 422 döndürmeli."""
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": "GSHORTKEY"}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_wrong_start_char(client: AsyncClient):
    """'G' yerine 'A' ile başlayan 56 karakterli key kabul edilmemeli."""
    # GCEZFB... → ACEZFB... (G → A)
    bad_key = "A" + _KEY_MERIDIAN[1:]
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": bad_key}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_contains_zero(client: AsyncClient):
    """'0' (sıfır) base32 alfabesinde geçersizdir; key reddedilmeli."""
    # İkinci karakteri '0' ile değiştir: GC... → G0...
    bad_key = "G0" + _KEY_MERIDIAN[2:]
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": bad_key}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_asset_not_found(client: AsyncClient):
    """Var olmayan ID ile sorgu 404 döndürmeli."""
    response = await client.get("/assets/nonexistent-id-00000000")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_asset_not_found(client: AsyncClient):
    """Var olmayan varlığı güncelleme girişimi 404 döndürmeli."""
    response = await client.patch(
        "/assets/nonexistent-id-00000000", json={"status": "retired"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_asset_returns_409(client: AsyncClient):
    """Aynı asset code + issuer key kombinasyonu tekrar eklenemez."""
    await client.post("/assets", json=MERIDIAN_ASSET)
    response = await client.post("/assets", json=MERIDIAN_ASSET)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_asset_missing_required_field(client: AsyncClient):
    """Zorunlu alan eksikse 422 döndürmeli."""
    payload = {k: v for k, v in MERIDIAN_ASSET.items() if k != "jurisdiction"}
    response = await client.post("/assets", json=payload)

    assert response.status_code == 422
