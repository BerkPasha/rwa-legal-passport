"""
Phase 7 — Kurumsal Yasal Pasaport entegrasyon ve güvenlik testleri (11 test).

Kapsam:
 1. Kimlik doğrulama — API anahtarı eksik veya geçersiz
 2. Varlık bulunamadı — 404
 3. Onaylı çıkarım yok — 404 (taslak gizli kalır)
 4. Taslak bilgi hiç gösterilmez
 5. Tüm 7 yasal alan döner, doğrulama durumu doğru
 6. Bilinmeyen alanlar açıkça etiketlenir
 7. Onay meta verisi yanıtta bulunur
 8. Disclaimer her yanıtta zorunlu
 9. Hız sınırı aşıldığında 429 döner
10. Pasaport isteği günlük kaydına işlenir
11. Kaynak belge PDF'i bu endpoint üzerinden indirilemez (link içermez)
"""

import logging

import pytest
from httpx import AsyncClient

from app.main import app
from app.rate_limit import InMemoryRateLimiter, get_rate_limiter


def _passport_url(asset: dict) -> str:
    """Varlık verisinden pasaport URL'i oluşturur."""
    return (
        f"/passport/{asset['blockchain_network']}"
        f"/{asset['stellar_asset_code']}"
        f"/{asset['stellar_issuer_public_key']}"
    )


# ── 1–2. Kimlik doğrulama ─────────────────────────────────────────────────────


async def test_passport_no_api_key_returns_403(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """X-API-Key başlığı olmadan 403 Forbidden döner."""
    r = await client.get(_passport_url(created_asset))
    assert r.status_code == 403


async def test_passport_invalid_api_key_returns_401(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """Geçersiz API anahtarı ile 401 Unauthorized döner."""
    r = await client.get(
        _passport_url(created_asset), headers={"X-API-Key": "yanlis-anahtar"}
    )
    assert r.status_code == 401


# ── 3. Varlık bulunamadı ──────────────────────────────────────────────────────


async def test_passport_unknown_asset_returns_404(
    client: AsyncClient, inst_headers: dict
):
    """Stellar ağında tanımlı olmayan varlık → 404 Not Found."""
    r = await client.get(
        "/passport/stellar-testnet/GHOST/GDQOE23CFSUMSVQK4Y5JHPPYK73VYCNHZHA7ENKCV37P6SUEO6XQBKPP",
        headers=inst_headers,
    )
    assert r.status_code == 404


# ── 4. Taslak bilgi gizli kalır ───────────────────────────────────────────────


async def test_passport_draft_extraction_returns_404(
    client: AsyncClient, created_extraction: dict, created_asset: dict, inst_headers: dict
):
    """
    Çıkarım onaylanmadan pasaport isteği 404 döner.
    Taslak bilgi hiçbir zaman dış API üzerinden gösterilmez.
    """
    # created_extraction var (taslak) ama approved_extraction yok
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 404
    assert "onaylanmış" in r.json()["detail"].lower()


# ── 5. Tüm yasal alanlar döner ────────────────────────────────────────────────


async def test_passport_returns_all_seven_legal_rights(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    Onaylı pasaport tüm 7 yasal alanı içerir.
    Mock AI 4 bilinen + 3 bilinmeyen alan çıkarır.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    assert body["asset"]["asset_code"] == "MRPROP"
    assert body["asset"]["network"] == "stellar-mainnet"

    rights = body["legal_rights"]
    assert len(rights) == 7

    field_names = {right["field_name"] for right in rights}
    assert field_names == {
        "income_rights",
        "insolvency_provisions",
        "jurisdiction",
        "ownership_nature",
        "redemption_rights",
        "transfer_restrictions",
        "voting_rights",
    }


# ── 6. Bilinmeyen alanlar açıkça etiketlenir ─────────────────────────────────


async def test_passport_unknown_fields_clearly_labeled(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    is_unknown=True olan alanlarda:
    - value == "unknown"
    - verified == False
    - supporting_passage == None
    is_unknown=False olan alanlarda verified==True (kaynak kanıtı var).
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    rights = r.json()["legal_rights"]

    unknown_rights = [r for r in rights if r["is_unknown"]]
    known_rights = [r for r in rights if not r["is_unknown"]]

    assert len(unknown_rights) == 3  # voting_rights, redemption_rights, insolvency_provisions
    assert len(known_rights) == 4

    for u in unknown_rights:
        assert u["verified"] is False
        assert u["supporting_passage"] is None

    for k in known_rights:
        assert k["verified"] is True
        assert k["supporting_passage"] is not None


# ── 7. Onay meta verisi yanıtta bulunur ───────────────────────────────────────


async def test_passport_includes_approval_metadata(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    Pasaport yanıtı:
    - approvals listesi dolu (en az 1 onay)
    - extraction_metadata: passport_version, effective_date, ai_model
    - source_document: belge başlığı ve versiyon numarası
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    approvals = body["approvals"]
    assert len(approvals) >= 1
    assert approvals[0]["approval_type"] == "issuer_attestation"
    assert approvals[0]["approved_at"] is not None

    meta = body["extraction_metadata"]
    assert meta["passport_version"] == 1
    assert meta["ai_model"] == "mock-v1"
    assert meta["effective_date"] is not None

    doc_ref = body["source_document"]
    assert doc_ref["title"] == "Q4 2024 Prospectus"
    assert doc_ref["version_number"] == 1


# ── 8. Disclaimer her yanıtta zorunlu ─────────────────────────────────────────


async def test_passport_disclaimer_always_present(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    Disclaimer alanı her pasaport yanıtında mevcuttur ve
    hukuki bağlayıcılık olmadığını açıkça belirtir.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()
    disclaimer = body["disclaimer"]
    assert disclaimer
    # Yasal tavsiye olmadığını belirten anahtar ifade
    assert "legal advice" in disclaimer.lower() or "legal counsel" in disclaimer.lower()


# ── 9. Hız sınırı aşıldığında 429 döner ──────────────────────────────────────


async def test_passport_rate_limit_exceeded_returns_429(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    Düşük limitli hız sınırlayıcı ile sınır aşıldığında 429 Too Many Requests döner.
    """
    low_limiter = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    app.dependency_overrides[get_rate_limiter] = lambda: low_limiter

    url = _passport_url(created_asset)
    r1 = await client.get(url, headers=inst_headers)
    assert r1.status_code == 200
    r2 = await client.get(url, headers=inst_headers)
    assert r2.status_code == 200
    # Üçüncü istek sınırı aşıyor
    r3 = await client.get(url, headers=inst_headers)
    assert r3.status_code == 429


# ── 10. Pasaport isteği günlük kaydına işlenir ────────────────────────────────


async def test_passport_request_is_logged(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
    caplog,
):
    """
    Başarılı pasaport isteği 'rwa_passport' logger'ına INFO kaydı bırakır.
    Kayıt istemci kimliğini ve varlık kodunu içerir.
    """
    with caplog.at_level(logging.INFO, logger="rwa_passport"):
        r = await client.get(_passport_url(created_asset), headers=inst_headers)

    assert r.status_code == 200
    log_messages = [record.message for record in caplog.records]
    # En az bir kayıtta client_id geçmeli
    assert any("meridian-capital-001" in msg for msg in log_messages)


# ── 11. PDF içeriği bu endpoint üzerinden indirilemez ────────────────────────


async def test_passport_does_not_expose_document_download_url(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
):
    """
    Pasaport yanıtı yalnızca belge meta verisini (başlık, tür, versiyon) içerir.
    Ham PDF içeriği, indirme bağlantısı veya belge metni gösterilmez.
    """
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    body = r.json()

    doc_ref = body["source_document"]
    # Yalnızca meta veri alanları — indirme bağlantısı veya içerik yok
    assert set(doc_ref.keys()) == {"document_id", "title", "document_type", "version_number"}
    assert "download_url" not in doc_ref
    assert "content" not in doc_ref
    assert "text" not in doc_ref
