"""
Phase 3 — Legal Document Management tests.

Fictional test data is used.
All PDFs are minimal files created in memory with pypdf.
"""

import pytest
from httpx import AsyncClient


# ── Helper function ───────────────────────────────────────────────────────────


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, **kwargs) -> dict:
    """Shortens the repeated upload code."""
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


# ── Success scenarios ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_document_returns_201(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """A valid PDF upload must return 201 and the document record."""
    resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)

    assert resp.status_code == 201
    data = resp.json()
    assert data["asset_id"] == created_asset["id"]
    assert data["document_type"] == "prospectus"
    assert data["status"] == "active"
    assert data["current_version_number"] == 1
    assert "id" in data


@pytest.mark.asyncio
async def test_upload_document_stores_sha256_hash(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """The SHA-256 fingerprint of the uploaded document must be calculated and stored."""
    import hashlib
    expected_hash = hashlib.sha256(minimal_pdf).hexdigest()

    resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)
    data = resp.json()

    assert data["current_version"]["sha256_hash"] == expected_hash
    assert len(data["current_version"]["sha256_hash"]) == 64


@pytest.mark.asyncio
async def test_upload_document_extracts_pages(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """Uploading a single-page PDF must create a page record."""
    resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)
    doc_id = resp.json()["id"]

    text_resp = await issuer_client.get(f"/documents/{doc_id}/text")

    assert text_resp.status_code == 200
    data = text_resp.json()
    assert len(data["pages"]) == 1
    assert data["pages"][0]["page_number"] == 1
    assert "text_content" in data["pages"][0]


@pytest.mark.asyncio
async def test_upload_document_file_preserved(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """The downloaded file must match the original bytes exactly."""
    resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)
    doc_id = resp.json()["id"]

    download_resp = await issuer_client.get(f"/documents/{doc_id}/download")

    assert download_resp.status_code == 200
    assert "application/pdf" in download_resp.headers.get("content-type", "")
    assert download_resp.content == minimal_pdf


@pytest.mark.asyncio
async def test_list_documents_for_asset(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """A document uploaded under an asset must appear in the list."""
    asset_id = created_asset["id"]
    await _upload(issuer_client, asset_id, minimal_pdf, title="Tapu Senedi", document_type="title_deed")

    resp = await issuer_client.get(f"/assets/{asset_id}/documents")

    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 1
    assert items[0]["document_type"] == "title_deed"


@pytest.mark.asyncio
async def test_get_document_by_id(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """A document must be fetched correctly by ID."""
    upload_resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)
    doc_id = upload_resp.json()["id"]

    resp = await issuer_client.get(f"/documents/{doc_id}")

    assert resp.status_code == 200
    assert resp.json()["id"] == doc_id
    assert resp.json()["current_version"]["version_number"] == 1


@pytest.mark.asyncio
async def test_get_document_text_version_reference(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """The text response must state which version it belongs to."""
    upload_resp = await _upload(issuer_client, created_asset["id"], minimal_pdf)
    doc_id = upload_resp.json()["id"]

    resp = await issuer_client.get(f"/documents/{doc_id}/text")

    assert resp.status_code == 200
    data = resp.json()
    assert data["document_id"] == doc_id
    assert data["version_number"] == 1


@pytest.mark.asyncio
async def test_upload_new_version_increments_number(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """A second upload to the same document must set the version number to 2."""
    asset_id = created_asset["id"]
    first = await _upload(issuer_client, asset_id, minimal_pdf)
    doc_id = first.json()["id"]

    resp = await issuer_client.post(
        f"/documents/{doc_id}/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )

    assert resp.status_code == 201
    assert resp.json()["version_number"] == 2


@pytest.mark.asyncio
async def test_list_versions_shows_history(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """After two versions are uploaded, the history list must contain both."""
    asset_id = created_asset["id"]
    first = await _upload(issuer_client, asset_id, minimal_pdf)
    doc_id = first.json()["id"]

    await issuer_client.post(
        f"/documents/{doc_id}/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )

    resp = await issuer_client.get(f"/documents/{doc_id}/versions")

    assert resp.status_code == 200
    versions = resp.json()
    assert len(versions) == 2
    assert versions[0]["version_number"] == 1
    assert versions[1]["version_number"] == 2


@pytest.mark.asyncio
async def test_document_linked_to_asset(
    issuer_client: AsyncClient, created_asset: dict, minimal_pdf: bytes
):
    """A document must be linked correctly to the asset it was uploaded to."""
    asset_id = created_asset["id"]
    resp = await _upload(issuer_client, asset_id, minimal_pdf)

    assert resp.json()["asset_id"] == asset_id


# ── Error / validation scenarios ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upload_wrong_file_extension_returns_415(
    issuer_client: AsyncClient, created_asset: dict
):
    """A non-PDF extension (e.g. .txt) must be rejected."""
    fake_content = b"Bu bir metin dosyasidir."
    resp = await issuer_client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Yanlış Tip"},
        files={"file": ("belge.txt", fake_content, "text/plain")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_fake_pdf_magic_bytes_returns_415(
    issuer_client: AsyncClient, created_asset: dict
):
    """A file with a '.pdf' extension but invalid content must be rejected."""
    fake_pdf = b"Bu PDF degil ama uzantisi pdf."
    resp = await issuer_client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Sahte PDF"},
        files={"file": ("sahte.pdf", fake_pdf, "application/pdf")},
    )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_upload_exceeds_size_limit_returns_413(
    issuer_client: AsyncClient, created_asset: dict, monkeypatch
):
    """
    A file exceeding the size limit must return 413.
    We lower the limit to 0 MB so the test runs quickly with a small file.
    """
    from app.config import settings
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)

    # 0 MB limit → any PDF content exceeds the limit
    tiny_content = b"%PDF-1.4\n" + b"x" * 10
    resp = await issuer_client.post(
        f"/assets/{created_asset['id']}/documents",
        data={"document_type": "other", "title": "Büyük Dosya"},
        files={"file": ("buyuk.pdf", tiny_content, "application/pdf")},
    )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_upload_to_nonexistent_asset_returns_404(
    issuer_client: AsyncClient, minimal_pdf: bytes
):
    """An attempt to upload a document to a non-existent asset must return 404."""
    resp = await issuer_client.post(
        "/assets/nonexistent-asset-id-00000/documents",
        data={"document_type": "other", "title": "Test"},
        files={"file": ("test.pdf", minimal_pdf, "application/pdf")},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_document_not_found_returns_404(issuer_client: AsyncClient):
    """A non-existent document ID must return 404."""
    resp = await issuer_client.get("/documents/nonexistent-document-id-000")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_text_of_nonexistent_document_returns_404(issuer_client: AsyncClient):
    """The text endpoint of a non-existent document must return 404."""
    resp = await issuer_client.get("/documents/nonexistent-document-id-000/text")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_new_version_to_nonexistent_document_returns_404(
    issuer_client: AsyncClient, minimal_pdf: bytes
):
    """An attempt to upload a new version to a non-existent document must return 404."""
    resp = await issuer_client.post(
        "/documents/nonexistent-document-id-000/versions",
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )
    assert resp.status_code == 404
