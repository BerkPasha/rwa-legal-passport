"""
Phase 2 — Tokenized Asset Registry tests.

Fictional test data is used; it does not represent a real company or asset.
The Stellar public keys were created manually for format validation
(56 characters, starting with G, only RFC 4648 base32 characters: A-Z and 2-7).
"""

import pytest
from httpx import AsyncClient

from tests.conftest import AGROCOMM_ISSUER, MERIDIAN_ISSUER

# ── Fictional test data ───────────────────────────────────────────────────────

# A valid Stellar public key: 56 characters, starts with G, contains A-Z and 2-7
_KEY_MERIDIAN = "GCEZFBCKNOXYELQZFQ7JKPWQABYOPZKVIPQLNOILMWMEXGWAF7RQJCUQ"
_KEY_AGROCOMM = "GBVERHXLKQNF5ZPAW7MXJTDYRVCSUIQENBAHAGKFZ3PLMWT2YDRUQQXY"

MERIDIAN_ASSET = {
    "issuer_id": MERIDIAN_ISSUER,
    "blockchain_network": "stellar-mainnet",
    "stellar_asset_code": "MRPROP",
    "stellar_issuer_public_key": _KEY_MERIDIAN,
    "issuer_organization_name": "Meridian Property Holdings LLC",
    "asset_type": "real_estate",
    "jurisdiction": "US-NY",
}

AGROCOMM_ASSET = {
    "issuer_id": AGROCOMM_ISSUER,
    "blockchain_network": "stellar-testnet",
    "stellar_asset_code": "AGRCOM",
    "stellar_issuer_public_key": _KEY_AGROCOMM,
    "issuer_organization_name": "AgroComm Finance SA",
    "asset_type": "commodity",
    "jurisdiction": "BR",
}


# ── Success scenarios ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_asset_returns_201(admin_client: AsyncClient):
    """A valid asset registration must return 201 with the correct fields."""
    response = await admin_client.post("/assets", json=MERIDIAN_ASSET)

    assert response.status_code == 201
    data = response.json()
    assert data["stellar_asset_code"] == "MRPROP"
    assert data["issuer_organization_name"] == "Meridian Property Holdings LLC"
    assert data["status"] == "active"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data


@pytest.mark.asyncio
async def test_list_assets_empty_on_start(admin_client: AsyncClient):
    """The list must be empty while no asset has been added yet."""
    response = await admin_client.get("/assets")

    assert response.status_code == 200
    assert response.json() == []


@pytest.mark.asyncio
async def test_list_assets_after_creation(admin_client: AsyncClient):
    """After two assets are added, the list must contain both."""
    await admin_client.post("/assets", json=MERIDIAN_ASSET)
    await admin_client.post("/assets", json=AGROCOMM_ASSET)

    response = await admin_client.get("/assets")

    assert response.status_code == 200
    assert len(response.json()) == 2


@pytest.mark.asyncio
async def test_get_asset_by_id(admin_client: AsyncClient):
    """An existing asset must be fetched correctly by ID."""
    create_resp = await admin_client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]

    response = await admin_client.get(f"/assets/{asset_id}")

    assert response.status_code == 200
    assert response.json()["id"] == asset_id
    assert response.json()["blockchain_network"] == "stellar-mainnet"


@pytest.mark.asyncio
async def test_update_asset_status(admin_client: AsyncClient):
    """The asset status must be updatable to 'suspended'."""
    create_resp = await admin_client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]

    response = await admin_client.patch(f"/assets/{asset_id}", json={"status": "suspended"})

    assert response.status_code == 200
    assert response.json()["status"] == "suspended"
    # The blockchain identity fields must not change
    assert response.json()["stellar_asset_code"] == "MRPROP"


@pytest.mark.asyncio
async def test_update_asset_organization_name(admin_client: AsyncClient):
    """The organization name must be updatable while the other fields are preserved."""
    create_resp = await admin_client.post("/assets", json=MERIDIAN_ASSET)
    asset_id = create_resp.json()["id"]
    new_name = "Meridian Property Holdings International LLC"

    response = await admin_client.patch(
        f"/assets/{asset_id}", json={"issuer_organization_name": new_name}
    )

    assert response.status_code == 200
    assert response.json()["issuer_organization_name"] == new_name
    assert response.json()["jurisdiction"] == "US-NY"  # must not change


@pytest.mark.asyncio
async def test_create_response_separates_blockchain_and_legal_fields(admin_client: AsyncClient):
    """
    The response must include both blockchain metadata and legal metadata —
    verifies that these two concepts are kept separate.
    """
    response = await admin_client.post("/assets", json=MERIDIAN_ASSET)
    data = response.json()

    blockchain_fields = {"blockchain_network", "stellar_asset_code", "stellar_issuer_public_key"}
    legal_fields = {"issuer_organization_name", "asset_type", "jurisdiction", "status"}

    for field in blockchain_fields | legal_fields:
        assert field in data, f"Missing field: {field}"


# ── Error / validation scenarios ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_asset_invalid_code_too_long(admin_client: AsyncClient):
    """A 13-character asset code must return 422 (maximum 12)."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "TOOLONGCODE13"}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_code_xlm(admin_client: AsyncClient):
    """'XLM' must not be accepted because it is the native asset."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "XLM"}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_code_lowercase(admin_client: AsyncClient):
    """A lowercase asset code must not be accepted."""
    payload = {**MERIDIAN_ASSET, "stellar_asset_code": "mrprop"}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_too_short(admin_client: AsyncClient):
    """A public key shorter than 56 characters must return 422."""
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": "GSHORTKEY"}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_wrong_start_char(admin_client: AsyncClient):
    """A 56-character key starting with 'A' instead of 'G' must not be accepted."""
    # GCEZFB... → ACEZFB... (G → A)
    bad_key = "A" + _KEY_MERIDIAN[1:]
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": bad_key}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_asset_invalid_key_contains_zero(admin_client: AsyncClient):
    """'0' (zero) is invalid in the base32 alphabet; the key must be rejected."""
    # Replace the second character with '0': GC... → G0...
    bad_key = "G0" + _KEY_MERIDIAN[2:]
    payload = {**MERIDIAN_ASSET, "stellar_issuer_public_key": bad_key}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_get_asset_not_found(admin_client: AsyncClient):
    """A query with a non-existent ID must return 404."""
    response = await admin_client.get("/assets/nonexistent-id-00000000")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_asset_not_found(admin_client: AsyncClient):
    """An attempt to update a non-existent asset must return 404."""
    response = await admin_client.patch(
        "/assets/nonexistent-id-00000000", json={"status": "retired"}
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_duplicate_asset_returns_409(admin_client: AsyncClient):
    """The same asset code + issuer key combination cannot be added again."""
    await admin_client.post("/assets", json=MERIDIAN_ASSET)
    response = await admin_client.post("/assets", json=MERIDIAN_ASSET)

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_create_asset_missing_required_field(admin_client: AsyncClient):
    """A missing required field must return 422."""
    payload = {k: v for k, v in MERIDIAN_ASSET.items() if k != "jurisdiction"}
    response = await admin_client.post("/assets", json=payload)

    assert response.status_code == 422
