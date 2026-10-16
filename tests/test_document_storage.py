"""
Stage 9B-2 — Secure PDF upload, storage and download tests.

Coverage:
 1. Permanent regression tests for the Stage 9A exploits (P08, P08v2, P08b)
 2. Windows path attacks and filename sanitizing / Content-Disposition
 3. StorageService unit tests: path validation, O_EXCL, symlink / junction escape
 4. PDF validation: valid PDF with text, empty / corrupt / truncated / encrypted /
    page-less PDFs, page / text / time limits
 5. Request body size limit (Content-Length and chunked)
 6. Integrity: SHA-256 from the bytes on disk, verification on download, tamper detection
 7. Versions: history is preserved, concurrent uploads do not collide, unique indexes
 8. Access control: rejected uploads do not touch the disk; no internal path is exposed
"""

import asyncio
import hashlib
import logging
import os
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import settings
from app.db.base import Base
from app.db.schema_check import SchemaOutOfDateError, verify_schema_columns
from app.db.session import get_db
from app.main import app
from app.models.document import DocumentVersion
from app.services.storage import StorageError, StorageIntegrityError, StorageService
from tests.conftest import _HARBOR_ASSET, bearer, ISSUER_TOKEN, OTHER_ISSUER_TOKEN, LEGAL_TOKEN
from tests.pdf_fixtures import (
    make_blank_pdf,
    make_encrypted_pdf,
    make_source_pdf,
    make_text_pdf,
    make_zero_page_pdf,
)

_DOC_ID = "3f2b8c1e-5d4a-4e6f-9a7b-1c2d3e4f5a6b"
TEXT_PAGES = [
    ["Investors hold a fractional beneficial interest in the Trust.",
     "This Agreement is governed by the laws of the State of Delaware."],
    ["Transfers require prior written consent of the Trustee."],
]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _storage_root(tmp_path: Path) -> Path:
    # conftest.client → StorageService(tmp_path / "storage")
    return tmp_path / "storage"


def _files_under(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file()} if root.exists() else set()


async def _upload(client: AsyncClient, asset_id: str, content: bytes, filename: str = "doc.pdf",
                  headers: dict | None = None, title: str = "Prospectus"):
    return await client.post(
        f"/assets/{asset_id}/documents",
        headers=headers or {},
        data={"document_type": "prospectus", "title": title},
        files={"file": (filename, content, "application/pdf")},
    )


async def _stored_paths(db: AsyncSession) -> list[str]:
    return list((await db.execute(select(DocumentVersion.stored_path))).scalars())


# ── 1. Regression tests for the Stage 9A exploits ─────────────────────────────


@pytest.mark.parametrize("filename", [
    "../../../escaped_rel.pdf",
    "../" * 12 + "escaped_deep.pdf",
    "..\\..\\..\\escaped_backslash.pdf",
    "v1/../../escaped_mixed.pdf",
])
async def test_p08_relative_traversal_filename_cannot_escape_storage(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path, filename: str
):
    """P08: a filename containing ../ can no longer write a file outside the storage root."""
    before = _files_under(tmp_path)
    pdf = make_text_pdf(TEXT_PAGES)
    r = await _upload(issuer_client, created_asset["id"], pdf, filename=filename)
    assert r.status_code == 201
    new_files = _files_under(tmp_path) - before
    assert len(new_files) == 1
    (stored,) = new_files
    assert stored.resolve().is_relative_to(_storage_root(tmp_path).resolve())
    assert "escaped" not in stored.name, "The user's filename must not be used as the storage path"
    assert stored.read_bytes() == pdf
    # The display name must be stripped of path components
    assert r.json()["current_version"]["original_filename"].startswith("escaped_")
    assert "/" not in r.json()["current_version"]["original_filename"]
    assert "\\" not in r.json()["current_version"]["original_filename"]


async def test_p08v2_absolute_and_windows_paths_cannot_write_outside(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path
):
    """P08v2: absolute, drive, UNC and \\\\?\\ paths cannot write to their target."""
    outside = tmp_path / "outside"
    outside.mkdir()
    targets = {
        "posix_abs": outside / "posix_abs.pdf",
        "win_fwd": outside / "win_fwd.pdf",
        "win_back": outside / "win_back.pdf",
        "extended": outside / "extended.pdf",
    }
    filenames = [
        targets["posix_abs"].as_posix().split(":", 1)[-1],          # /Users/.../posix_abs.pdf
        targets["win_fwd"].as_posix(),                               # C:/Users/.../win_fwd.pdf
        str(targets["win_back"]),                                    # C:\Users\...\win_back.pdf
        "\\\\?\\" + str(targets["extended"]),                        # \\?\C:\...\extended.pdf
        "\\\\fileserver\\share\\unc.pdf",                            # UNC
        "C:relative_drive.pdf",                                      # drive-relative
    ]
    for filename in filenames:
        r = await _upload(issuer_client, created_asset["id"], make_blank_pdf(), filename=filename)
        assert r.status_code == 201, (filename, r.text)
    assert list(outside.iterdir()) == []
    for f in _files_under(tmp_path):
        assert f.resolve().is_relative_to(_storage_root(tmp_path).resolve()), f


async def test_p08b_upload_cannot_overwrite_another_documents_file(
    client: AsyncClient,
    issuer_client: AsyncClient,
    created_asset: dict,
    async_db: AsyncSession,
    issuer_headers: dict,
    legal_headers: dict,
    tmp_path: Path,
):
    """
    P08b: the stored file of an approved document cannot be replaced by another upload,
    not even by an attacker who knows the real storage path.
    """
    original = make_source_pdf()  # contains the mock AI quotations, so the approval verifies
    doc_a = (await _upload(issuer_client, created_asset["id"], original, filename="evidence.pdf")).json()
    ext = await client.post(f"/documents/{doc_a['id']}/extractions", headers=issuer_headers)
    assert (await client.post(f"/extractions/{ext.json()['id']}/approvals", headers=legal_headers, json={})).status_code == 201

    (real_path,) = await _stored_paths(async_db)
    attacks = [
        f"../../{doc_a['id']}/v1/evidence.pdf",   # the predictable old layout from 9A
        f"../../{real_path}",                       # the real internal path (strongest attacker)
        f"../../../storage/{real_path}",
    ]
    for filename in attacks:
        r = await _upload(issuer_client, created_asset["id"], make_blank_pdf(5), filename=filename, title="x")
        assert r.status_code == 201

    dl = await issuer_client.get(f"/documents/{doc_a['id']}/download")
    assert dl.status_code == 200
    assert dl.content == original
    assert _sha(dl.content) == doc_a["current_version"]["sha256_hash"]
    assert len(set(await _stored_paths(async_db))) == 1 + len(attacks)


# ── 2. Windows path names and filename sanitizing ─────────────────────────────


@pytest.mark.parametrize("filename,expected_status", [
    ("CON.pdf", 201),
    ("aux.PDF", 201),
    ("NUL.pdf", 201),
    ("report.pdf ", 201),            # trailing whitespace is stripped
    ("evil.pdf:hidden_stream", 415),  # NTFS alternate data stream → not .pdf
    ("trailing_dot.pdf.", 415),
    ("tab\tand\x01control.pdf", 201),
])
async def test_windows_special_filenames_are_metadata_only(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path, filename: str, expected_status: int
):
    before = _files_under(tmp_path)
    r = await _upload(issuer_client, created_asset["id"], make_blank_pdf(), filename=filename)
    assert r.status_code == expected_status
    new_files = _files_under(tmp_path) - before
    if expected_status == 201:
        assert len(new_files) == 1
        name = r.json()["current_version"]["original_filename"]
        assert not any(ord(ch) < 32 for ch in name)
        (stored,) = new_files
        assert stored.name != filename.strip() and len(stored.stem) == 32
    else:
        assert new_files == set()


async def test_download_content_disposition_is_safe(
    issuer_client: AsyncClient, created_asset: dict
):
    # httpx encodes quotes as %22; a name that reaches the server unchanged is chosen
    nasty = "Ünlü belge; x=1.pdf"
    doc = (await _upload(issuer_client, created_asset["id"], make_blank_pdf(), filename=nasty)).json()
    r = await issuer_client.get(f"/documents/{doc['id']}/download")
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    assert cd.startswith("attachment;")
    assert "\r" not in cd and "\n" not in cd
    assert 'filename="_nl__belge__x_1.pdf"' in cd
    assert "filename*=UTF-8''%C3%9Cnl%C3%BC%20belge%3B%20x%3D1.pdf" in cd
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["cache-control"] == "no-store"


# ── 3. StorageService unit tests ──────────────────────────────────────────────


@pytest.mark.parametrize("stored_path", [
    "../outside.pdf",
    f"{_DOC_ID}/v1/../../../outside.pdf",
    "/etc/passwd",
    "C:/Windows/win.ini",
    "C:\\Windows\\win.ini",
    "\\\\server\\share\\x.pdf",
    f"{_DOC_ID}\\v1\\{'a' * 32}.pdf",
    f"{_DOC_ID}/v1/evidence.pdf",           # does not match the server-generated format
    f"{_DOC_ID}/v0/{'a' * 32}.pdf",
    f"{_DOC_ID}/v1/{'a' * 32}.pdf\x00.txt",
    "",
])
def test_storage_rejects_unsafe_stored_paths(tmp_path: Path, stored_path: str):
    storage = StorageService(str(tmp_path / "root"))
    with pytest.raises(StorageError):
        storage.full_path(stored_path)
    with pytest.raises(StorageError):
        storage.read_verified(stored_path, "0" * 64)


@pytest.mark.parametrize("document_id,version", [
    ("../../etc", 1), ("not-a-uuid", 1), (_DOC_ID.upper(), 1), (_DOC_ID, 0), (_DOC_ID, -3), (_DOC_ID, True),
])
def test_storage_rejects_invalid_path_components(tmp_path: Path, document_id, version):
    with pytest.raises(StorageError):
        StorageService(str(tmp_path / "root")).build_stored_path(document_id, version)


def test_storage_paths_are_unique_and_never_overwrite(tmp_path: Path):
    storage = StorageService(str(tmp_path / "root"))
    paths = {storage.build_stored_path(_DOC_ID, 1) for _ in range(200)}
    assert len(paths) == 200
    path = paths.pop()
    storage.save_new(path, b"%PDF-first", _sha(b"%PDF-first"))
    with pytest.raises(StorageError, match="already exists"):
        storage.save_new(path, b"%PDF-second", _sha(b"%PDF-second"))
    assert storage.read_verified(path, _sha(b"%PDF-first")) == b"%PDF-first"


def test_storage_write_verification_failure_leaves_no_file(tmp_path: Path):
    storage = StorageService(str(tmp_path / "root"))
    path = storage.build_stored_path(_DOC_ID, 1)
    with pytest.raises(StorageIntegrityError):
        storage.save_new(path, b"%PDF-data", "0" * 64)
    assert not storage.exists(path)
    assert _files_under(tmp_path / "root") == set()


def _make_dir_link(link: Path, target: Path) -> str:
    """Tries a symlink first, otherwise (Windows privileges) a directory junction."""
    try:
        os.symlink(target, link, target_is_directory=True)
        return "symlink"
    except (OSError, NotImplementedError):
        if sys.platform == "win32":
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
            return "junction"
        pytest.skip("Directory links cannot be created on this platform")


@pytest.mark.parametrize("link_level", ["document_dir", "version_dir"])
def test_storage_blocks_link_escape_on_write(tmp_path: Path, link_level: str):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    outside.mkdir()
    storage = StorageService(str(root))
    if link_level == "document_dir":
        kind = _make_dir_link(root / _DOC_ID, outside)
    else:
        (root / _DOC_ID).mkdir(parents=True)
        kind = _make_dir_link(root / _DOC_ID / "v1", outside)
    path = storage.build_stored_path(_DOC_ID, 1)
    with pytest.raises(StorageError):
        storage.save_new(path, b"%PDF-x", _sha(b"%PDF-x"))
    assert list(outside.iterdir()) == [], f"{kind} allowed a write outside the root"


def test_storage_blocks_link_escape_on_read(tmp_path: Path):
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (outside / "v1").mkdir(parents=True)
    secret_name = "a" * 32 + ".pdf"
    (outside / "v1" / secret_name).write_bytes(b"%PDF-outside")
    storage = StorageService(str(root))
    _make_dir_link(root / _DOC_ID, outside)
    with pytest.raises(StorageError):
        storage.read_verified(f"{_DOC_ID}/v1/{secret_name}", _sha(b"%PDF-outside"))


# ── 4. PDF validation ─────────────────────────────────────────────────────────


async def test_text_pdf_upload_extracts_text_and_round_trips(
    issuer_client: AsyncClient, created_asset: dict
):
    """Real PDF with text: the page texts are extracted and the downloaded bytes are identical."""
    pdf = make_text_pdf(TEXT_PAGES)
    r = await _upload(issuer_client, created_asset["id"], pdf, filename="trust_agreement.pdf")
    assert r.status_code == 201
    doc = r.json()
    assert doc["current_version"]["sha256_hash"] == _sha(pdf)
    assert doc["current_version"]["file_size"] == len(pdf)

    pages = (await issuer_client.get(f"/documents/{doc['id']}/text")).json()["pages"]
    assert [p["page_number"] for p in pages] == [1, 2]
    assert "governed by the laws of the State of Delaware" in pages[0]["text_content"]
    assert pages[1]["text_content"] == "Transfers require prior written consent of the Trustee."

    dl = await issuer_client.get(f"/documents/{doc['id']}/download")
    assert dl.status_code == 200
    assert dl.headers["content-type"] == "application/pdf"
    assert dl.content == pdf
    assert dl.headers["x-content-sha256"] == _sha(pdf)


@pytest.mark.parametrize("label,content,status_code", [
    ("empty", b"", 422),
    ("header_only", b"%PDF-1.7", 422),
    ("truncated", make_text_pdf(TEXT_PAGES)[: len(make_text_pdf(TEXT_PAGES)) // 2], 422),
    ("garbage_body", b"%PDF-1.4\n" + os.urandom(2048) + b"\n%%EOF\n", 422),
    ("no_magic", b"<html>%%EOF</html>", 415),
    ("encrypted", make_encrypted_pdf(), 422),
    ("zero_pages", make_zero_page_pdf(), 422),
])
async def test_invalid_pdfs_are_rejected_without_side_effects(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path,
    label: str, content: bytes, status_code: int,
):
    r = await _upload(issuer_client, created_asset["id"], content, filename=f"{label}.pdf")
    assert r.status_code == status_code, (label, r.text)
    assert "Traceback" not in r.text and "pypdf" not in r.text.lower()
    assert _files_under(_storage_root(tmp_path)) == set()
    docs = await issuer_client.get(f"/assets/{created_asset['id']}/documents")
    assert docs.json() == []


async def test_page_limit_is_enforced(issuer_client: AsyncClient, created_asset: dict, monkeypatch, tmp_path: Path):
    monkeypatch.setattr(settings, "max_pdf_pages", 2)
    assert (await _upload(issuer_client, created_asset["id"], make_blank_pdf(2))).status_code == 201
    r = await _upload(issuer_client, created_asset["id"], make_blank_pdf(3))
    assert r.status_code == 422 and "page limit" in r.json()["detail"]
    assert len(_files_under(_storage_root(tmp_path))) == 1


async def test_text_limit_is_enforced(issuer_client: AsyncClient, created_asset: dict, monkeypatch):
    monkeypatch.setattr(settings, "max_pdf_text_chars", 50)
    r = await _upload(issuer_client, created_asset["id"], make_text_pdf(TEXT_PAGES))
    assert r.status_code == 422 and "character limit" in r.json()["detail"]


async def test_parse_time_budget_is_enforced(issuer_client: AsyncClient, created_asset: dict, monkeypatch):
    monkeypatch.setattr(settings, "pdf_parse_timeout_seconds", 1e-9)
    r = await _upload(issuer_client, created_asset["id"], make_text_pdf(TEXT_PAGES * 3))
    assert r.status_code == 422 and "time limit" in r.json()["detail"]


def test_resource_limit_settings_are_bounded():
    from app.config import Settings
    for bad in ({"max_pdf_pages": 0}, {"max_pdf_text_chars": 10}, {"pdf_parse_timeout_seconds": 0},
                {"max_upload_size_mb": -1}):
        with pytest.raises(ValueError):
            Settings(**bad)


# ── 5. Request body size limit ────────────────────────────────────────────────


async def test_oversized_request_rejected_by_content_length_before_auth(
    client: AsyncClient, created_asset: dict, monkeypatch, tmp_path: Path
):
    """A large body gets 413 before authentication and multipart parsing."""
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)  # limit = envelope allowance only (64 KiB)
    big = b"%PDF-1.4\n" + b"0" * (200 * 1024)
    r = await _upload(client, created_asset["id"], big)  # no credentials
    assert r.status_code == 413
    assert _files_under(_storage_root(tmp_path)) == set()


async def test_grossly_oversized_declared_body_is_not_read_at_all(
    client: AsyncClient, created_asset: dict, monkeypatch
):
    """A body declared far above the limit is not drained; 413 is returned without reading a single byte."""
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)  # limit 64 KiB, drain cap 256 KiB
    consumed = 0

    async def body():
        nonlocal consumed
        for _ in range(64):
            consumed += 16 * 1024
            yield b"0" * (16 * 1024)

    r = await client.post(
        f"/assets/{created_asset['id']}/documents",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": str(1024 * 1024)},
    )
    assert r.status_code == 413
    assert consumed <= 16 * 1024


async def test_oversized_chunked_request_rejected_while_streaming(
    issuer_client: AsyncClient, created_asset: dict, monkeypatch, tmp_path: Path
):
    """A body streamed without Content-Length is cut off as soon as the limit is exceeded."""
    monkeypatch.setattr(settings, "max_upload_size_mb", 0)
    boundary = "testboundary9b2"
    sent = 0

    async def body():
        nonlocal sent
        yield (
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"document_type\"\r\n\r\nother\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"title\"\r\n\r\nx\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"big.pdf\"\r\n"
            f"Content-Type: application/pdf\r\n\r\n%PDF-1.4\n"
        ).encode()
        for _ in range(64):  # 64 × 16 KiB = 1 MiB
            sent += 16 * 1024
            yield b"0" * (16 * 1024)
        yield f"\r\n--{boundary}--\r\n".encode()

    r = await issuer_client.post(
        f"/assets/{created_asset['id']}/documents",
        content=body(),
        headers={"content-type": f"multipart/form-data; boundary={boundary}"},
    )
    assert r.status_code == 413
    # The body must stop being read as soon as the limit is exceeded (the full 1 MiB must not be consumed)
    assert sent < 256 * 1024, f"middleware did not cut the stream: {sent} bytes read"
    assert _files_under(_storage_root(tmp_path)) == set()


async def test_normal_upload_within_limit_passes_middleware(
    issuer_client: AsyncClient, created_asset: dict
):
    assert (await _upload(issuer_client, created_asset["id"], make_text_pdf(TEXT_PAGES))).status_code == 201


# ── 6. Integrity ──────────────────────────────────────────────────────────────


async def test_stored_hash_matches_bytes_on_disk(
    issuer_client: AsyncClient, created_asset: dict, async_db: AsyncSession, tmp_path: Path
):
    pdf = make_text_pdf(TEXT_PAGES)
    doc = (await _upload(issuer_client, created_asset["id"], pdf)).json()
    (stored,) = _files_under(_storage_root(tmp_path))
    assert _sha(stored.read_bytes()) == doc["current_version"]["sha256_hash"] == _sha(pdf)
    row = (await async_db.execute(select(DocumentVersion))).scalar_one()
    assert row.sha256_hash == _sha(stored.read_bytes())


async def test_tampered_file_is_not_served(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path, caplog
):
    doc = (await _upload(issuer_client, created_asset["id"], make_text_pdf(TEXT_PAGES))).json()
    (stored,) = _files_under(_storage_root(tmp_path))
    stored.write_bytes(make_blank_pdf(5))  # tampering on disk

    with caplog.at_level(logging.ERROR, logger="rwa_security"):
        r = await issuer_client.get(f"/documents/{doc['id']}/download")
    assert r.status_code == 500
    assert r.json() == {"detail": "Document integrity check failed; the file was not served."}
    assert str(tmp_path) not in r.text and stored.name not in r.text
    assert any("download.integrity_mismatch" in rec.getMessage() for rec in caplog.records)


async def test_missing_file_error_does_not_expose_paths(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path
):
    doc = (await _upload(issuer_client, created_asset["id"], make_blank_pdf())).json()
    (stored,) = _files_under(_storage_root(tmp_path))
    stored.unlink()
    r = await issuer_client.get(f"/documents/{doc['id']}/download")
    assert r.status_code == 500
    assert r.json() == {"detail": "Stored file not found."}


async def test_corrupted_stored_path_in_db_cannot_read_outside(
    issuer_client: AsyncClient, created_asset: dict, async_db: AsyncSession, tmp_path: Path
):
    """Even if the path in the database is corrupted / tampered with, nothing is read from outside the root."""
    secret = tmp_path / "secret.pdf"
    secret.write_bytes(make_blank_pdf())
    doc = (await _upload(issuer_client, created_asset["id"], make_blank_pdf())).json()
    for bad in ["../secret.pdf", str(secret), secret.as_posix()]:
        await async_db.execute(
            update(DocumentVersion).values(stored_path=bad, sha256_hash=_sha(secret.read_bytes()))
        )
        await async_db.commit()
        r = await issuer_client.get(f"/documents/{doc['id']}/download")
        assert r.status_code == 500, bad
        assert r.content != secret.read_bytes()


# ── 7. Versions and concurrency ───────────────────────────────────────────────


async def test_previous_versions_are_preserved(
    issuer_client: AsyncClient, created_asset: dict, tmp_path: Path
):
    v1 = make_text_pdf([["Version one terms."]])
    v2 = make_text_pdf([["Version two amended terms."]])
    doc = (await _upload(issuer_client, created_asset["id"], v1)).json()
    r = await issuer_client.post(f"/documents/{doc['id']}/versions",
                                 files={"file": ("amended.pdf", v2, "application/pdf")})
    assert r.status_code == 201 and r.json()["version_number"] == 2

    versions = (await issuer_client.get(f"/documents/{doc['id']}/versions")).json()
    assert [(v["version_number"], v["sha256_hash"]) for v in versions] == [(1, _sha(v1)), (2, _sha(v2))]
    on_disk = {_sha(p.read_bytes()) for p in _files_under(_storage_root(tmp_path))}
    assert on_disk == {_sha(v1), _sha(v2)}
    assert (await issuer_client.get(f"/documents/{doc['id']}/download")).content == v2
    text = (await issuer_client.get(f"/documents/{doc['id']}/text")).json()
    assert text["version_number"] == 2 and "amended" in text["pages"][0]["text_content"]


async def test_concurrent_version_uploads_get_distinct_versions(
    client: AsyncClient, issuer_client: AsyncClient, admin_client: AsyncClient, tmp_path: Path
):
    """
    Real concurrency: file-based SQLite and a separate session per request.
    Every successful upload receives a different version number and its own file;
    no file is overwritten and no orphan file remains.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'concurrency.db').as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def per_request_session():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = per_request_session
    try:
        from tests.conftest import _MERIDIAN_ASSET
        asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
        doc = (await _upload(issuer_client, asset["id"], make_text_pdf([["Base version."]]))).json()

        payloads = [make_text_pdf([[f"Concurrent amendment number {i}."]]) for i in range(8)]
        responses = await asyncio.gather(*[
            issuer_client.post(f"/documents/{doc['id']}/versions",
                               files={"file": (f"v{i}.pdf", pdf, "application/pdf")})
            for i, pdf in enumerate(payloads)
        ])
        codes = [r.status_code for r in responses]
        assert set(codes) <= {201, 409, 503}, codes
        ok = [r.json() for r in responses if r.status_code == 201]
        assert ok, codes
        numbers = [v["version_number"] for v in ok]
        assert len(numbers) == len(set(numbers)), "The same version number was assigned twice"

        versions = (await issuer_client.get(f"/documents/{doc['id']}/versions")).json()
        # The increment of failed uploads is rolled back: the numbers are gapless and unique
        assert [v["version_number"] for v in versions] == list(range(1, len(versions) + 1))
        assert len(versions) == 1 + len(ok)
        files = _files_under(_storage_root(tmp_path))
        assert len(files) == len(versions), "Orphaned or missing file"
        assert {_sha(f.read_bytes()) for f in files} == {v["sha256_hash"] for v in versions}
        current = (await issuer_client.get(f"/documents/{doc['id']}")).json()
        assert current["current_version_number"] == max(v["version_number"] for v in versions)
        print(f"concurrent upload status codes: {codes}")
    finally:
        await engine.dispose()


async def test_unique_indexes_block_duplicate_versions_and_shared_files(
    issuer_client: AsyncClient, created_asset: dict, async_db: AsyncSession
):
    doc = (await _upload(issuer_client, created_asset["id"], make_blank_pdf())).json()
    existing = (await async_db.execute(select(DocumentVersion))).scalar_one()
    for duplicate in (
        {"version_number": 1, "stored_path": existing.stored_path[:-10] + "0" * 6 + ".pdf"},
        {"version_number": 2, "stored_path": existing.stored_path},
    ):
        async_db.add(DocumentVersion(
            id=f"dup-{duplicate['version_number']}", document_id=doc["id"], original_filename="x.pdf",
            file_size=1, sha256_hash="0" * 64, **duplicate,
        ))
        with pytest.raises(IntegrityError):
            await async_db.commit()
        await async_db.rollback()


async def test_schema_check_requires_9b2_unique_indexes():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP INDEX uq_document_versions_doc_version"))
        with pytest.raises(SchemaOutOfDateError, match=r"document_versions index\(uq_document_versions_doc_version\)"):
            await conn.run_sync(verify_schema_columns)
    await engine.dispose()


# ── 8. Access control and internal-path privacy ───────────────────────────────


async def test_rejected_uploads_never_touch_storage(
    client: AsyncClient, created_asset: dict, created_document: dict, tmp_path: Path, admin_client: AsyncClient
):
    """Unauthorized, wrong-role, cross-tenant and institutional-key uploads do not write to disk."""
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    before = _files_under(tmp_path)
    pdf = make_text_pdf(TEXT_PAGES)
    aid, did = created_asset["id"], created_document["id"]
    attempts = [
        (await _upload(client, aid, pdf), 403),                                        # anonymous
        (await _upload(client, aid, pdf, headers=bearer("invalid")), 401),            # invalid
        (await _upload(client, aid, pdf, headers={"X-API-Key": "test-inst-key-alpha"}), 403),
        (await _upload(client, aid, pdf, headers=bearer(LEGAL_TOKEN)), 403),          # wrong role
        (await _upload(client, aid, pdf, headers=bearer(OTHER_ISSUER_TOKEN)), 404),   # cross-tenant
        (await _upload(client, harbor["id"], pdf, headers=bearer(ISSUER_TOKEN)), 404),
        (await client.post(f"/documents/{did}/versions", headers=bearer(OTHER_ISSUER_TOKEN),
                           files={"file": ("v2.pdf", pdf, "application/pdf")}), 404),
        (await client.post(f"/documents/{did}/versions", headers=bearer(LEGAL_TOKEN),
                           files={"file": ("v2.pdf", pdf, "application/pdf")}), 403),
    ]
    assert [r.status_code for r, _ in attempts] == [code for _, code in attempts]
    assert _files_under(tmp_path) == before


async def test_cross_tenant_and_institutional_download_blocked(
    client: AsyncClient, created_document: dict, other_issuer_headers: dict, inst_headers: dict
):
    did = created_document["id"]
    assert (await client.get(f"/documents/{did}/download", headers=other_issuer_headers)).status_code == 404
    assert (await client.get(f"/documents/{did}/download", headers=inst_headers)).status_code == 403
    assert (await client.get(f"/documents/{did}/download")).status_code == 403


async def test_responses_never_expose_internal_paths(
    issuer_client: AsyncClient, created_asset: dict, async_db: AsyncSession, tmp_path: Path
):
    upload = await _upload(issuer_client, created_asset["id"], make_text_pdf(TEXT_PAGES))
    doc_id = upload.json()["id"]
    (stored_path,) = await _stored_paths(async_db)
    stored_name = stored_path.rsplit("/", 1)[-1]
    responses = [
        upload,
        await issuer_client.get(f"/documents/{doc_id}"),
        await issuer_client.get(f"/documents/{doc_id}/versions"),
        await issuer_client.get(f"/assets/{created_asset['id']}/documents"),
        await issuer_client.post(f"/documents/{doc_id}/versions",
                                 files={"file": ("v2.pdf", make_blank_pdf(), "application/pdf")}),
    ]
    for r in responses:
        assert r.status_code in (200, 201)
        assert "stored_path" not in r.text
        assert stored_name not in r.text
        assert str(tmp_path) not in r.text and tmp_path.as_posix() not in r.text
    dl = await issuer_client.get(f"/documents/{doc_id}/download")
    assert stored_name not in dl.headers.get("content-disposition", "")
