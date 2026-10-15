"""
Phase 5 — İnceleme ve onay iş akışı testleri (15 test).

Kapsam:
 1. Kimlik doğrulama — yetkisiz ve geçersiz token
 2. start_review — başarılı geçiş, zaten onaylanmış çakışma, bulunamadı
 3. correct_claim — denetim kaydı, otomatik under_review, onaylı çıkarım engeli, bulunamadı
 4. approve_extraction — ihraç eden ve hukuki onay türleri, kaynak kanıt eksikliği, AI yasağı, bulunamadı
 5. list_corrections / list_approvals — kayıt sonrası liste doğrulaması
"""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.extraction import LegalClaim
from sqlalchemy import select


# ── 1. Kimlik doğrulama ───────────────────────────────────────────────────────


async def test_start_review_unauthenticated(client: AsyncClient, created_extraction: dict):
    """
    Authorization başlığı hiç gönderilmediğinde HTTPBearer 403 döner.
    (FastAPI varsayılan davranışı: başlık eksikse 403, geçersiz token ise 401.)
    """
    eid = created_extraction["id"]
    r = await client.post(f"/extractions/{eid}/review")
    assert r.status_code == 403


async def test_start_review_invalid_token(client: AsyncClient, created_extraction: dict):
    """Geçersiz token → 401."""
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/review",
        headers={"Authorization": "Bearer gecersiz-token"},
    )
    assert r.status_code == 401


# ── 2. start_review ───────────────────────────────────────────────────────────


async def test_start_review_success(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Taslak çıkarım incelemeye alınır; dönen yanıt under_review durumunu yansıtır."""
    eid = created_extraction["id"]
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["status"] == "under_review"
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"


async def test_start_review_already_approved_conflict(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Onaylanmış çıkarım yeniden incelemeye alınamaz — 409 Conflict."""
    eid = created_extraction["id"]
    # Önce onayla
    approve_r = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert approve_r.status_code == 201
    # Sonra incelemeye almayı dene
    r = await client.post(f"/extractions/{eid}/review", headers=issuer_headers)
    assert r.status_code == 409


async def test_start_review_not_found(client: AsyncClient, issuer_headers: dict):
    """Var olmayan çıkarım → 404."""
    r = await client.post(
        "/extractions/00000000-0000-0000-0000-000000000000/review",
        headers=issuer_headers,
    )
    assert r.status_code == 404


# ── 3. correct_claim ──────────────────────────────────────────────────────────


async def test_correct_claim_success_audit_record(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    Düzeltme yapıldığında:
    - Denetim kaydı orijinal ve düzeltilmiş değerleri içerir.
    - Yanıt tüm alanları doğru döndürür.
    """
    eid = created_extraction["id"]
    # İlk iddia alfabetik sırayla 'income_rights' (is_unknown=False, orijinal değer var)
    claim = created_extraction["claims"][0]
    cid = claim["id"]
    original_value = claim["value"]

    payload = {
        "corrected_value": "Revised: quarterly distributions at fixed 5% yield.",
        "is_unknown": False,
        "corrected_passage": "Token holders shall receive a fixed 5% annual yield.",
        "correction_note": "Prospektüs güncellemesine göre sabit getiri oranı belirlendi.",
    }
    r = await client.patch(
        f"/extractions/{eid}/claims/{cid}", headers=issuer_headers, json=payload
    )
    assert r.status_code == 200
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["claim_id"] == cid
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"
    assert body["original_value"] == original_value
    assert body["corrected_value"] == payload["corrected_value"]
    assert body["corrected_passage"] == payload["corrected_passage"]
    assert body["correction_note"] == payload["correction_note"]


async def test_correct_claim_auto_sets_under_review(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    Taslak çıkarımda düzeltme yapıldığında durum otomatik olarak 'under_review' olur.
    Çıkarım getirme endpoint'i güncellenmiş durumu döndürür.
    """
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]
    assert created_extraction["status"] == "draft"

    await client.patch(
        f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Updated value.", "is_unknown": False},
    )

    get_r = await client.get(f"/extractions/{eid}")
    assert get_r.status_code == 200
    assert get_r.json()["status"] == "under_review"


async def test_correct_claim_approved_rejected(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Onaylanmış çıkarımda iddia düzeltilemez — 409 Conflict."""
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]
    # Önce onayla
    await client.post(f"/extractions/{eid}/approvals", headers=issuer_headers, json={})

    r = await client.patch(
        f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Değişiklik denemesi.", "is_unknown": False},
    )
    assert r.status_code == 409


async def test_correct_claim_not_found(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Var olmayan iddia ID'si → 404."""
    eid = created_extraction["id"]
    r = await client.patch(
        f"/extractions/{eid}/claims/00000000-0000-0000-0000-000000000000",
        headers=issuer_headers,
        json={"corrected_value": "Deneme.", "is_unknown": False},
    )
    assert r.status_code == 404


# ── 4. approve_extraction ─────────────────────────────────────────────────────


async def test_approve_issuer_success(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    İhraç eden inceleyicinin onayı:
    - approval_type = issuer_attestation
    - status = approved
    - disclaimer alanı dolu
    """
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals",
        headers=issuer_headers,
        json={"notes": "Tüm alanlar ihraçcı belgesiyle uyumlu."},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["extraction_id"] == eid
    assert body["approval_type"] == "issuer_attestation"
    assert body["reviewer_id"] == "alice-chen-01"
    assert body["reviewer_role"] == "issuer_reviewer"
    assert "INTERNAL REVIEW ONLY" in body["disclaimer"]
    assert body["notes"] == "Tüm alanlar ihraçcı belgesiyle uyumlu."
    assert body["approved_at"] is not None

    # Çıkarım durumu approved olmalı
    get_r = await client.get(f"/extractions/{eid}")
    assert get_r.json()["status"] == "approved"


async def test_approve_legal_reviewer_success(
    client: AsyncClient,
    created_extraction: dict,
    legal_headers: dict,
):
    """
    Bağımsız hukuk uzmanının onayı:
    - approval_type = legal_verification
    """
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals",
        headers=legal_headers,
        json={},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["approval_type"] == "legal_verification"
    assert body["reviewer_id"] == "bob-martinez-01"
    assert body["reviewer_role"] == "independent_legal_reviewer"


async def test_approve_missing_source_evidence(
    client: AsyncClient,
    async_db: AsyncSession,
    created_extraction: dict,
    issuer_headers: dict,
):
    """
    Bilinen bir iddianın supporting_passage değeri boşaltıldığında
    onay 422 Unprocessable Entity döner.
    """
    eid = created_extraction["id"]

    # Doğrudan DB'de bilinen bir iddianın kanıt alanını sil
    result = await async_db.execute(
        select(LegalClaim).where(
            LegalClaim.extraction_id == eid,
            LegalClaim.is_unknown == False,  # noqa: E712
        ).limit(1)
    )
    claim = result.scalar_one()
    claim.supporting_passage = None
    await async_db.commit()

    r = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert r.status_code == 422
    assert "kaynak kanıt eksik" in r.json()["detail"]


async def test_approve_ai_forbidden(
    client: AsyncClient,
    created_extraction: dict,
    ai_headers: dict,
):
    """user_id 'ai-' ile başlayan kullanıcı onay yapamaz — 403 Forbidden."""
    eid = created_extraction["id"]
    r = await client.post(
        f"/extractions/{eid}/approvals", headers=ai_headers, json={}
    )
    assert r.status_code == 403


async def test_approve_not_found(client: AsyncClient, issuer_headers: dict):
    """Var olmayan çıkarım → 404."""
    r = await client.post(
        "/extractions/00000000-0000-0000-0000-000000000000/approvals",
        headers=issuer_headers,
        json={},
    )
    assert r.status_code == 404


# ── 5. list_corrections / list_approvals ─────────────────────────────────────


async def test_list_corrections_after_correction(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
):
    """Düzeltme yapıldıktan sonra liste endpoint'i kayıtları döndürür."""
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]

    # Düzelt
    await client.patch(
        f"/extractions/{eid}/claims/{claim['id']}",
        headers=issuer_headers,
        json={"corrected_value": "Güncellenmiş değer.", "is_unknown": False},
    )

    r = await client.get(f"/extractions/{eid}/corrections", headers=issuer_headers)
    assert r.status_code == 200
    corrections = r.json()
    assert len(corrections) == 1
    assert corrections[0]["claim_id"] == claim["id"]
    assert corrections[0]["corrected_value"] == "Güncellenmiş değer."


async def test_list_approvals_after_approval(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
    legal_headers: dict,
):
    """
    İki farklı inceleyicinin onayından sonra liste endpoint'i her iki kaydı da döndürür.
    Onay türleri birbirinden ayrışır.
    """
    eid = created_extraction["id"]

    await client.post(f"/extractions/{eid}/approvals", headers=issuer_headers, json={})
    await client.post(f"/extractions/{eid}/approvals", headers=legal_headers, json={})

    r = await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)
    assert r.status_code == 200
    approvals = r.json()
    assert len(approvals) == 2
    types = {a["approval_type"] for a in approvals}
    assert types == {"issuer_attestation", "legal_verification"}
