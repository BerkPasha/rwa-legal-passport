"""
Phase 4 — AI Yasal Hak Çıkarım Motoru testleri.

Mock AI servisi kullanılır; Anthropic API'ye gerçek istek gönderilmez.
Mock, 4 bilinen + 3 bilinmeyen alan döndürür.
"""

import pytest
from httpx import AsyncClient

from app.enums import LegalFieldName
from app.services.ai import UNVERIFIED_DISCLAIMER


# ── Başarılı senaryolar ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_extraction_returns_201(
    client: AsyncClient, created_document: dict
):
    """Geçerli bir belge için çıkarım tetiklenebilmeli ve 201 dönmeli."""
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")

    assert resp.status_code == 201
    data = resp.json()
    assert data["document_id"] == doc_id
    assert "id" in data
    assert "claims" in data


@pytest.mark.asyncio
async def test_extraction_contains_all_seven_fields(
    client: AsyncClient, created_document: dict
):
    """Çıkarım her zaman yedi yasal hak alanının tamamını içermeli."""
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    assert len(claims) == 7
    extracted_fields = {c["field_name"] for c in claims}
    expected_fields = {f.value for f in LegalFieldName}
    assert extracted_fields == expected_fields


@pytest.mark.asyncio
async def test_extraction_status_is_always_draft(
    client: AsyncClient, created_document: dict
):
    """
    Status yalnızca 'draft' olabilir.
    Otomatik onay kesinlikle uygulanmaz.
    """
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")

    assert resp.json()["status"] == "draft"


@pytest.mark.asyncio
async def test_extraction_disclaimer_is_present(
    client: AsyncClient, created_document: dict
):
    """
    Her çıkarım yanıtında sorumluluk reddi metni bulunmalı.
    AI, yasal geçerliliği onaylayamaz.
    """
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    data = resp.json()

    assert "disclaimer" in data
    assert len(data["disclaimer"]) > 0
    # Sorumluluk reddi metninin değişmeden iletildiğini doğrula
    assert data["disclaimer"] == UNVERIFIED_DISCLAIMER


@pytest.mark.asyncio
async def test_known_claims_have_source_references(
    client: AsyncClient, created_document: dict
):
    """
    is_unknown=False olan iddiaların kaynak sayfa ve alıntı içermesi gerekir.
    Bu şart, 'her iddia kaynağını göstermelidir' gereksinimini karşılar.
    """
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    known = [c for c in claims if not c["is_unknown"]]
    assert len(known) > 0, "Mock en az bir bilinen iddia döndürmeli"

    for claim in known:
        assert claim["supporting_passage"] is not None, (
            f"{claim['field_name']} alanı is_unknown=False ama kaynak alıntı yok"
        )
        assert len(claim["supporting_passage"]) > 0


@pytest.mark.asyncio
async def test_unknown_claims_are_flagged_correctly(
    client: AsyncClient, created_document: dict
):
    """
    Kanıt yetersiz alanlarda is_unknown=True ve value='unknown' olmalı.
    AI, eksik bilgiyi uydurmaz.
    """
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    claims = resp.json()["claims"]

    unknown = [c for c in claims if c["is_unknown"]]
    assert len(unknown) > 0, "Mock en az bir bilinmeyen alan döndürmeli"

    for claim in unknown:
        assert claim["value"] == "unknown", (
            f"{claim['field_name']} is_unknown=True ama value='unknown' değil"
        )
        assert claim["supporting_passage"] is None


@pytest.mark.asyncio
async def test_ai_model_is_recorded(
    client: AsyncClient, created_document: dict
):
    """Hangi modelin kullanıldığı çıkarım kaydında saklanmalı."""
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    data = resp.json()

    assert "ai_model" in data
    assert len(data["ai_model"]) > 0


@pytest.mark.asyncio
async def test_get_extraction_by_id(
    client: AsyncClient, created_document: dict
):
    """Oluşturulan çıkarım ID ile getirilebilmeli."""
    doc_id = created_document["id"]
    create_resp = await client.post(f"/documents/{doc_id}/extractions")
    extraction_id = create_resp.json()["id"]

    get_resp = await client.get(f"/extractions/{extraction_id}")

    assert get_resp.status_code == 200
    assert get_resp.json()["id"] == extraction_id
    assert len(get_resp.json()["claims"]) == 7


@pytest.mark.asyncio
async def test_list_extractions_for_document(
    client: AsyncClient, created_document: dict
):
    """İki çıkarım tetiklendikten sonra liste her ikisini de içermeli."""
    doc_id = created_document["id"]
    await client.post(f"/documents/{doc_id}/extractions")
    await client.post(f"/documents/{doc_id}/extractions")

    resp = await client.get(f"/documents/{doc_id}/extractions")

    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_extractions_stored_separately_from_assets(
    client: AsyncClient, created_document: dict
):
    """
    Çıkarım sonuçları varlık/belge kayıtlarından ayrı tutulmalı.
    Belge meta verisi çıkarım tetiklendikten sonra değişmemeli.
    """
    doc_id = created_document["id"]

    # Çıkarım öncesi belge durumu
    before = await client.get(f"/documents/{doc_id}")
    before_status = before.json()["status"]

    # Çıkarım tetikle
    await client.post(f"/documents/{doc_id}/extractions")

    # Çıkarım sonrası belge durumu — değişmemeli
    after = await client.get(f"/documents/{doc_id}")
    assert after.json()["status"] == before_status
    assert after.json()["current_version_number"] == before.json()["current_version_number"]


# ── Hata senaryoları ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_trigger_extraction_nonexistent_document_returns_404(
    client: AsyncClient,
):
    """Var olmayan belge için çıkarım tetikleme 404 döndürmeli."""
    resp = await client.post("/documents/nonexistent-doc-id-000/extractions")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_extraction_not_found_returns_404(client: AsyncClient):
    """Var olmayan çıkarım ID'si 404 döndürmeli."""
    resp = await client.get("/extractions/nonexistent-extraction-id")

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_extractions_nonexistent_document_returns_404(client: AsyncClient):
    """Var olmayan belge için çıkarım listeleme 404 döndürmeli."""
    resp = await client.get("/documents/nonexistent-doc-id-000/extractions")

    assert resp.status_code == 404
