"""
Phase 3 — Yasal Belge Yönetimi testleri.

Kurgusal test verisi kullanılmaktadır.
Tüm PDF'ler pypdf ile bellekte oluşturulan minimal dosyalardır.
"""

import pytest
from httpx import AsyncClient


# ── Yardımcı fonksiyon ────────────────────────────────────────────────────────


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, **kwargs) -> dict:
    """Tekrar eden yükleme kodunu kısaltır."""
    doc_type = kwargs.get("document_type", "prospectus")
    title = kwargs.get("title", "Q4 2024 Prospectus — Meridian Property Holdings LLC")
    filename = kwargs.get("filename", "test.pdf")
    content_type = kwargs.get("content_type", "application/pdf")

    resp = await client.post(
        f"/assets/{asset_id}/documents",
        data={"document_type": doc_type, "title": title},
        files={"file": (filename, pdf, content_type)},
    )
    return resp


# ── Başarılı senaryolar ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_document_returns_201(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Geçerli PDF yüklemesi 201 ve belge kaydı döndürmeli."""
    resp = await _upload(client, created_asset["id"], minimal_pdf)

    assert resp.status_code == 201
    data = resp.json()
    assert data["asset_id"] == created_asset["id"]
    assert data["document_type"] == "prospectus"
    assert data["status"] == "active"
    assert data["current_version_number"] == 1
    assert "id" in data


@pytest.mark.asyncio
async def test_upload_document_stores_sha256_hash(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Yüklenen belgenin SHA-256 parmak izi hesaplanmalı ve saklanmalı."""
    import hashlib
    expected_hash = hashlib.sha256(minimal_pdf).hexdigest()

    resp = await _upload(client, created_asset["id"], minimal_pdf)
    data = resp.json()

    assert data["current_version"]["sha256_hash"] == expected_hash
    assert len(data["current_version"]["sha256_hash"]) == 64


@pytest.mark.asyncio
async def test_upload_document_extracts_pages(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Tek sayfalık PDF yüklendiğinde sayfa kaydı oluşturulmalı."""
    resp = await _upload(client, created_asset["id"], minimal_pdf)
    doc_id = resp.json()["id"]

    text_resp = await client.get(f"/documents/{doc_id}/text")

    assert text_resp.status_code == 200
    data = text_resp.json()
    assert len(data["pages"]) == 1
    assert data["pages"][0]["page_number"] == 1
    assert "text_content" in data["pages"][0]


@pytest.mark.asyncio
async def test_upload_document_file_preserved(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """İndirilen dosya orijinal baytlarla birebir eşleşmeli."""
    resp = await _upload(client, created_asset["id"], minimal_pdf)
    doc_id = resp.json()["id"]

    download_resp = await client.get(f"/documents/{doc_id}/download")

    assert download_resp.status_code == 200
    assert "application/pdf" in download_resp.headers.get("content-type", "")
    assert download_resp.content == minimal_pdf


@pytest.mark.asyncio
async def test_list_documents_for_asset(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Varlık altına yüklenen belge listede görünmeli."""
    asset_id = created_asset["id"]
    await _upload(client, asset_id, minimal_pdf, title="Tapu Senedi", document_type="title_deed")

    resp = await client.get(f"/assets/{asset_id}/documents")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["document_type"] == "title_deed"


@pytest.mark.asyncio
async def test_get_document_by_id(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Belge ID ile doğru şekilde getirilmeli."""
    upload_resp = await _upload(client, created_asset["id"], minimal_pdf)
    doc_id = upload_resp.json()["id"]

    resp = await client.get(f"/documents/{doc_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id
    assert resp.json()["current_version"]["version_number"] == 1


@pytest.mark.asyncio
async def test_get_document_text_version_reference(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Metin yanıtı hangi versiyona ait olduğunu bildirmeli."""
    upload_resp = await _upload(client, created_asset["id"], minimal_pdf)
    doc_id = upload_resp.json()["id"]

    resp = await client.get(f"/documents/{doc_id}/text")

    assert resp.status_code == 200
    data = resp.json()
    assert data["document_id"] == doc_id
    assert data["version_number"] == 1


@pytest.mark.asyncio
async def test_upload_new_version_increments_number(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Aynı belgeye ikinci yükleme versiyon numarasını 2 yapmalı."""
    asset_id = created_asset["id"]
    first = await _upload(client, asset_id, minimal_pdf)
    doc_id = first.json()["id"]

    resp = await client.post(
        f"/documents/{doc_id}/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )

    assert resp.status_code == 201
    assert resp.json()["version_number"] == 2


@pytest.mark.asyncio
async def test_list_versions_shows_history(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """İki versiyon yüklendikten sonra geçmiş listesi ikisini de içermeli."""
    asset_id = created_asset["id"]
    first = await _upload(client, asset_id, minimal_pdf)
    doc_id = first.json()["id"]

    await client.post(
        f"/documents/{doc_id}/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )

    resp = await client.get(f"/documents/{doc_id}/versions")

    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[1]["version_number"] == 2


@pytest.mark.asyncio
async def test_document_linked_to_asset(
    client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Belge, yüklendiği varlığa doğru şekilde bağlanmalı."""
    asset_id = created_asset["id"]
    resp = await _upload(client, asset_id, minimal_pdf)

    assert resp.json()["asset_id"] == asset_id


# ── Hata / doğrulama senaryoları ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_wrong_file_extension_returns_415(
    client: AsyncClient, created_asset: dict
):
    """PDF olmayan uzantı (örn. .txt) reddedilmeli."""
    fake_content = b"Bu bir metin dosyasidir."
    resp = await client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Yanlış Tip"},
        files={"file": ("belge.txt", fake_content, "text/plain")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_fake_pdf_magic_bytes_returns_415(
    client: AsyncClient, created_asset: dict
):
    """'.pdf' uzantılı ama geçersiz içerikli dosya reddedilmeli."""
    fake_pdf = b"Bu PDF degil ama uzantisi pdf."
    resp = await client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Sahte PDF"},
        files={"file": ("sahte.pdf", fake_pdf, "application/pdf")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_exceeds_size_limit_returns_413(
    client: AsyncClient, created_asset: dict, monkeypatch
):
    """
    Boyut sınırını aşan dosya 413 döndürmeli.
    Sınırı 0 MB'a çekerek küçük bir dosyayla testi hızlı çalıştırırız.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)

    # 0 MB limit → herhangi bir PDF içeriği sınırı aşar
    tiny_content = b"%PDF-1.4\n" + b"x" * 10
    resp = await client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Büyük Dosya"},
        files={"file": ("buyuk.pdf", tiny_content, "application/pdf")},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_to_nonexistent_asset_returns_404(
    client: AsyncClient, minimal_pdf: bytes
):
    """Var olmayan varlığa belge yükleme girişimi 404 döndürmeli."""
    resp = await client.post(
        "/assets/nonexistent-asset-id-00000/documents",
        data={"document_type": "other", "title": "Test"},
        files={"file": ("test.pdf", minimal_pdf, "application/pdf")},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_document_not_found_returns_404(client: AsyncClient):
    """Var olmayan belge ID'si 404 döndürmeli."""
    resp = await client.get("/documents/nonexistent-document-id-000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_text_of_nonexistent_document_returns_404(client: AsyncClient):
    """Var olmayan belgenin metin endpoint'i 404 döndürmeli."""
    resp = await client.get("/documents/nonexistent-document-id-000/text")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_new_version_to_nonexistent_document_returns_404(
    client: AsyncClient, minimal_pdf: bytes
):
    """Var olmayan belgeye yeni versiyon yükleme girişimi 404 döndürmeli."""
    resp = await client.post(
        "/documents/nonexistent-document-id-000/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )
    assert resp.status_code == 404
