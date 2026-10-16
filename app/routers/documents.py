"""
Legal document management endpoints.

POST /assets/{asset_id}/documents          — Upload a new document
GET  /assets/{asset_id}/documents          — List the documents of an asset
GET  /documents/{document_id}              — Document metadata
POST /documents/{document_id}/versions     — Upload a new version
GET  /documents/{document_id}/versions     — Version history
GET  /documents/{document_id}/text         — Extracted page texts of the CURRENT version
GET  /documents/{document_id}/versions/{n}/text — Extracted page texts of exactly version n
                                              (read-only, no fallback; Stage 9B-4, step 8)
GET  /documents/{document_id}/download     — Download the file

Secure upload / download (Stage 9B-2):
- The user's filename is only sanitized display metadata; the storage path is
  generated on the server (app/services/storage.py).
- PDF content is untrusted: size, signature, %%EOF, parsing, encryption, page /
  text / time limits are checked (app/services/pdf.py).
- SHA-256 is verified from the bytes written to disk; it is re-verified on download,
  and the file is not served if it does not match.
- The version number is allocated atomically; unique indexes prevent collisions
  between concurrent uploads. A failed request leaves no file on disk.
- Responses contain no internal filesystem paths.
"""

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.access_registry import Principal, security_logger
from app.auth import require_permission
from app.authorization import get_asset_for_principal, get_document_for_principal
from app.config import settings
from app.db.session import get_db
from app.enums import DocumentType
from app.evidence import SourceDocumentText
from app.models.document import Document, DocumentPage, DocumentVersion
from app.permissions import Permission
from app.schemas.document import (
    DocumentPageResponse,
    DocumentResponse,
    DocumentTextResponse,
    DocumentVersionResponse,
    DocumentVersionTextResponse,
)
from app.services.pdf import (
    PdfLimits,
    PdfValidationError,
    calculate_sha256,
    extract_pages,
    has_eof_marker,
    is_valid_pdf_content,
)
from app.services.storage import StorageError, StorageIntegrityError, StorageService, get_storage

router = APIRouter(tags=["Documents"])

_ALLOWED_EXTENSION = ".pdf"
_ASSET_NOT_FOUND = "Asset not found."
_DOC_NOT_FOUND = "Document not found."
_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")
_ASCII_FILENAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")
_MAX_FILENAME_LENGTH = 255


@dataclass(frozen=True)
class _ValidatedUpload:
    content: bytes
    sha256: str
    pages: list[dict]
    display_filename: str


# ── Helper functions ──────────────────────────────────────────────────────────


def _display_filename(raw: str | None) -> str:
    """
    Reduces the user's filename to display metadata: path components
    (/, \\) and control characters are removed and the length is limited. Never
    used as a path.
    """
    name = (raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _CONTROL_CHARS.sub("", name).strip()
    if len(name) > _MAX_FILENAME_LENGTH:
        name = name[: _MAX_FILENAME_LENGTH - len(_ALLOWED_EXTENSION)] + _ALLOWED_EXTENSION
    return name


def _pdf_limits() -> PdfLimits:
    return PdfLimits(
        max_pages=settings.max_pdf_pages,
        max_text_chars=settings.max_pdf_text_chars,
        max_parse_seconds=settings.pdf_parse_timeout_seconds,
    )


async def _process_upload(file: UploadFile, max_mb: int) -> _ValidatedUpload:
    """
    Validates and processes the uploaded file as untrusted input.
    Raises HTTPException on error; nothing is written to disk.
    """
    # 1. Extension check (on the sanitized name) — before the content is read
    display_filename = _display_filename(file.filename)
    if not display_filename.lower().endswith(_ALLOWED_EXTENSION):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF files (.pdf) are accepted.",
        )

    # 2. Read the content (limit + 1 byte: to detect overflow)
    limit_bytes = max_mb * 1024 * 1024
    content = await file.read(limit_bytes + 1)

    # 3. Size check
    if len(content) > limit_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {max_mb} MB limit.",
        )
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="File is empty."
        )

    # 4. Magic byte check (the content-type header is not trusted)
    if not is_valid_pdf_content(content):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File does not appear to be a valid PDF (magic byte mismatch).",
        )

    # 5. Truncated file check
    if not has_eof_marker(content):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PDF appears incomplete or truncated (no %%EOF marker).",
        )

    # 6. Parsing — in a separate thread so the event loop is not blocked,
    #    with an outer time limit (the inner budget is checked between pages)
    limits = _pdf_limits()
    try:
        pages = await asyncio.wait_for(
            asyncio.to_thread(extract_pages, content, limits),
            timeout=limits.max_parse_seconds + 1.0,
        )
    except PdfValidationError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=exc.reason)
    except asyncio.TimeoutError:
        security_logger.warning("upload.pdf_parse_timeout")
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="PDF processing exceeded the time limit.",
        )

    return _ValidatedUpload(
        content=content,
        sha256=calculate_sha256(content),
        pages=pages,
        display_filename=display_filename,
    )


async def _build_document_response(
    db: AsyncSession, document: Document
) -> DocumentResponse:
    """Loads the current version from the database and builds a DocumentResponse."""
    result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document.id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = result.scalar_one_or_none()

    return DocumentResponse(
        id=document.id,
        asset_id=document.asset_id,
        document_type=document.document_type,
        title=document.title,
        status=document.status,
        current_version_number=document.current_version_number,
        created_at=document.created_at,
        updated_at=document.updated_at,
        current_version=(
            DocumentVersionResponse.model_validate(current_version)
            if current_version
            else None
        ),
    )


async def _store_version(
    db: AsyncSession,
    storage: StorageService,
    document_id: str,
    version_number: int,
    upload: _ValidatedUpload,
) -> DocumentVersion:
    """
    Writes the file to a unique server-generated path (without overwriting, verifying
    the bytes) and adds the DocumentVersion + DocumentPage records.
    On error the caller must clean up with storage.discard(version.stored_path);
    the file write cleans up after itself when it fails.
    """
    stored_path = storage.build_stored_path(document_id, version_number)
    await asyncio.to_thread(storage.save_new, stored_path, upload.content, upload.sha256)

    version = DocumentVersion(
        id=str(uuid.uuid4()),
        document_id=document_id,
        version_number=version_number,
        original_filename=upload.display_filename,
        stored_path=stored_path,
        file_size=len(upload.content),
        sha256_hash=upload.sha256,
    )
    db.add(version)
    for page in upload.pages:
        db.add(
            DocumentPage(
                id=str(uuid.uuid4()),
                version_id=version.id,
                page_number=page["page_number"],
                text_content=page["text_content"],
            )
        )
    return version


async def _commit_or_cleanup(
    db: AsyncSession, storage: StorageService, stored_path: str | None
) -> None:
    """Commits the record; on failure rolls back and deletes the file written by this request."""
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        if stored_path:
            storage.discard(stored_path)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A concurrent upload created the same version; please retry.",
        )
    except OperationalError:
        await db.rollback()
        if stored_path:
            storage.discard(stored_path)
        security_logger.warning("upload.database_busy_on_commit")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is busy; please retry the upload.",
        )
    except BaseException:
        await db.rollback()
        if stored_path:
            storage.discard(stored_path)
        raise


def _storage_failure(exc: StorageError) -> HTTPException:
    # No internal path or operating-system detail is passed to the client
    security_logger.error("storage.failure type=%s", type(exc).__name__)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Document could not be stored; please retry.",
    )


def _content_disposition(filename: str) -> str:
    ascii_name = _ASCII_FILENAME_UNSAFE.sub("_", filename) or "document.pdf"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post(
    "/assets/{asset_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new legal document to an asset",
)
async def upload_document(
    asset_id: str,
    file: UploadFile,
    document_type: DocumentType = Form(...),
    title: str = Form(..., min_length=1, max_length=255),
    principal: Principal = Depends(require_permission(Permission.document_upload)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> DocumentResponse:
    # Asset and issuer scope check
    await get_asset_for_principal(db, asset_id, principal, _ASSET_NOT_FOUND)

    upload = await _process_upload(file, settings.max_upload_size_mb)

    doc_id = str(uuid.uuid4())
    document = Document(
        id=doc_id,
        asset_id=asset_id,
        document_type=document_type,
        title=title,
        status="active",
        current_version_number=1,
    )
    db.add(document)

    version: DocumentVersion | None = None
    try:
        version = await _store_version(db, storage, doc_id, 1, upload)
    except StorageError as exc:
        await db.rollback()
        raise _storage_failure(exc)
    await _commit_or_cleanup(db, storage, version.stored_path)
    await db.refresh(document)

    return await _build_document_response(db, document)


@router.get(
    "/assets/{asset_id}/documents",
    response_model=list[DocumentResponse],
    summary="List all documents of an asset",
)
async def list_documents(
    asset_id: str,
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentResponse]:
    await get_asset_for_principal(db, asset_id, principal, _ASSET_NOT_FOUND)

    result = await db.execute(
        select(Document).where(Document.asset_id == asset_id).order_by(Document.created_at.desc())
    )
    documents = result.scalars().all()
    return [await _build_document_response(db, doc) for doc in documents]


@router.get(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    summary="Get document metadata",
)
async def get_document(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
) -> DocumentResponse:
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)
    return await _build_document_response(db, document)


@router.post(
    "/documents/{document_id}/versions",
    response_model=DocumentVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a new version of an existing document",
)
async def upload_new_version(
    document_id: str,
    file: UploadFile,
    principal: Principal = Depends(require_permission(Permission.document_upload)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> DocumentVersionResponse:
    await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    upload = await _process_upload(file, settings.max_upload_size_mb)

    # Close the read transaction: the write transaction starts clean (SQLite lock upgrade)
    await db.commit()

    version: DocumentVersion | None = None
    try:
        # Atomic version allocation: concurrent uploads receive different numbers
        result = await db.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                current_version_number=Document.current_version_number + 1,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(Document.current_version_number)
            .execution_options(synchronize_session=False)
        )
        new_version_number = result.scalar_one()
        version = await _store_version(db, storage, document_id, new_version_number, upload)
    except StorageError as exc:
        await db.rollback()
        raise _storage_failure(exc)
    except OperationalError:
        await db.rollback()
        if version is not None:
            storage.discard(version.stored_path)
        security_logger.warning("upload.database_busy document=%s", document_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is busy; please retry the upload.",
        )
    await _commit_or_cleanup(db, storage, version.stored_path)
    await db.refresh(version)
    return version


@router.get(
    "/documents/{document_id}/versions",
    response_model=list[DocumentVersionResponse],
    summary="List all versions of a document",
)
async def list_versions(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
) -> list[DocumentVersionResponse]:
    await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    result = await db.execute(
        select(DocumentVersion)
        .where(DocumentVersion.document_id == document_id)
        .order_by(DocumentVersion.version_number)
    )
    return result.scalars().all()


@router.get(
    "/documents/{document_id}/text",
    response_model=DocumentTextResponse,
    summary="Get the text extracted from the current version of a document",
    description=(
        "Returns the pages of whatever version is current at the time of the request; `version_id` "
        "names it. For the text of a specific version (for example the version an extraction or an "
        "approval belongs to) use GET /documents/{document_id}/versions/{version_number}/text."
    ),
)
async def get_document_text(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
) -> DocumentTextResponse:
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found.")

    pages_result = await db.execute(
        select(DocumentPage)
        .where(DocumentPage.version_id == current_version.id)
        .order_by(DocumentPage.page_number)
    )
    pages = pages_result.scalars().all()

    return DocumentTextResponse(
        document_id=document_id,
        version_number=current_version.version_number,
        version_id=current_version.id,
        pages=[DocumentPageResponse.model_validate(p) for p in pages],
    )


_VERSION_NOT_FOUND = "Document version not found."
_VERSION_MISMATCH = (
    "Version reference mismatch: version_id does not identify version {n} of this document; "
    "no text is served for a different version."
)
_TEXT_UNAVAILABLE = (
    "The stored source text of this document version is unavailable or inconsistent; it is not served."
)


@router.get(
    "/documents/{document_id}/versions/{version_number}/text",
    response_model=DocumentVersionTextResponse,
    summary="Get the text extracted from one specific version of a document",
    description=(
        "Returns the page texts recorded for exactly the requested version (Stage 9B-4, step 8). "
        "The version is required: there is no fallback to the current or any other version, and a "
        "newer upload never changes what an older version returns. An optional `version_id` (for "
        "example an extraction's `version_id`) must identify that same version, otherwise 409. "
        "The text is evidence material extracted from the PDF text layer at upload (no OCR); it "
        "does not establish legal validity. `source_text_sha256` equals the value an approval's "
        "evidence record binds for this version. Read-only."
    ),
    responses={
        404: {"description": "Document not found or outside the caller's scope, or no such version."},
        409: {"description": "version_id mismatch, or the stored text of the version is unavailable or inconsistent."},
    },
)
async def get_document_version_text(
    document_id: str,
    version_number: int = Path(..., ge=1, le=2_147_483_647, description="Version number (1 = first upload)."),
    version_id: Optional[str] = Query(
        None, min_length=1, max_length=64, description="Optional expected version id; a different version gives 409."
    ),
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
) -> DocumentVersionTextResponse:
    # Scope first: an out-of-scope document is indistinguishable from a missing one
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    version = (
        await db.execute(
            select(DocumentVersion).where(
                DocumentVersion.document_id == document_id,
                DocumentVersion.version_number == version_number,
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_VERSION_NOT_FOUND)
    if version_id is not None and version_id != version.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_VERSION_MISMATCH.format(n=version_number))

    # Exactly the page records of this version id, in page order
    pages = (
        await db.execute(
            select(DocumentPage)
            .where(DocumentPage.version_id == version.id)
            .order_by(DocumentPage.page_number, DocumentPage.id)
        )
    ).scalars().all()
    source = SourceDocumentText.from_page_records(version.id, pages)
    if not pages or source.problem is not None:
        reason = "no_page_records" if not pages else "invalid_page_records"
        security_logger.error(
            "document_text.unavailable document=%s version=%d reason=%s", document_id, version_number, reason
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": _TEXT_UNAVAILABLE, "source_text_unavailable_reason": reason},
        )

    return DocumentVersionTextResponse(
        document_id=document.id,
        document_status=document.status,
        version_id=version.id,
        version_number=version.version_number,
        version_created_at=version.created_at,
        sha256_hash=version.sha256_hash,
        is_current_version=version.version_number == document.current_version_number,
        current_version_number=document.current_version_number,
        page_count=len(pages),
        source_text_sha256=source.text_sha256,
        pages=[DocumentPageResponse.model_validate(p) for p in pages],
    )


@router.get(
    "/documents/{document_id}/download",
    summary="Download the current version of a document (PDF, SHA-256 verified)",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}}, "description": "Verified PDF bytes."},
        500: {"description": "File not found or integrity check failed; the file is not served."},
    },
)
async def download_document(
    document_id: str,
    principal: Principal = Depends(require_permission(Permission.document_read)),
    db: AsyncSession = Depends(get_db),
    storage: StorageService = Depends(get_storage),
) -> Response:
    document = await get_document_for_principal(db, document_id, principal, _DOC_NOT_FOUND)

    version_result = await db.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.version_number == document.current_version_number,
        )
    )
    current_version = version_result.scalar_one_or_none()
    if current_version is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document version not found.")

    # The bytes are read once, verified, and the same bytes are served (no TOCTOU)
    try:
        data = await asyncio.to_thread(
            storage.read_verified, current_version.stored_path, current_version.sha256_hash
        )
    except StorageIntegrityError:
        security_logger.error(
            "download.integrity_mismatch document=%s version=%d user=%s",
            document_id, current_version.version_number, principal.user_id,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document integrity check failed; the file was not served.",
        )
    except StorageError:
        security_logger.error(
            "download.file_unavailable document=%s version=%d",
            document_id, current_version.version_number,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Stored file not found.",
        )

    return Response(
        content=data,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(current_version.original_filename),
            "X-Content-SHA256": current_version.sha256_hash,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
