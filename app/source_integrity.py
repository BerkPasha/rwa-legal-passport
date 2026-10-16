"""
Source-document consistency of an approval (Stage 9B-4, step 5).

Evidence is verified against the DocumentPage records of a document version. Before an approval
is bound to that text, the text must still be what the stored PDF of the version contains:
1. the bytes on disk match the version's recorded SHA-256 (StorageService.read_verified);
2. the page text re-extracted from those bytes with the upload method (app/services/pdf.py,
   extract_pages, the same limits as uploads) equals the DocumentPage records page by page:
   the same page count, pages numbered 1..n, and the identical text on every page.
No OCR or AI is used, missing text is never invented and no other version is substituted.
Any failure refuses the approval (fail closed).

The file is read and parsed before the approval takes the database write lock, because it can
be large; the decisive comparison with the version record and the DocumentPage records, both
loaded under the lock, happens inside the locked approval transaction. The file is checked at
approval time only; a later change of the file is detected on download and at the next approval.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import SourceIntegrityFailure
from app.evidence_records import ExtractionSourceText
from app.models.document import DocumentVersion
from app.services.pdf import PdfLimits, PdfValidationError, extract_pages
from app.services.storage import StorageError, StorageIntegrityError, StorageService


def _pdf_limits() -> PdfLimits:
    # The same limits as uploads (app/routers/documents.py, _pdf_limits)
    return PdfLimits(
        max_pages=settings.max_pdf_pages,
        max_text_chars=settings.max_pdf_text_chars,
        max_parse_seconds=settings.pdf_parse_timeout_seconds,
    )


@dataclass(frozen=True)
class StoredPdfText:
    """The page text of a version's stored PDF, or why it could not be established."""
    version_id: str
    stored_path: str | None = None
    sha256: str | None = None                  # the recorded digest the bytes were verified against
    pages: tuple[tuple[int, str], ...] = ()    # (page number, extracted text)
    failure: SourceIntegrityFailure | None = None


async def read_stored_pdf_text(db: AsyncSession, storage: StorageService, version_id: str) -> StoredPdfText:
    """Reads, verifies and re-extracts the stored PDF of one version (no write lock needed)."""
    version = (
        await db.execute(
            select(DocumentVersion).where(DocumentVersion.id == version_id).execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if version is None:
        return StoredPdfText(version_id, failure=SourceIntegrityFailure.version_unavailable)
    identity = {"version_id": version.id, "stored_path": version.stored_path, "sha256": version.sha256_hash}
    try:
        data = await asyncio.to_thread(storage.read_verified, version.stored_path, version.sha256_hash)
    except StorageIntegrityError:
        return StoredPdfText(**identity, failure=SourceIntegrityFailure.file_hash_mismatch)
    except StorageError:
        return StoredPdfText(**identity, failure=SourceIntegrityFailure.file_unavailable)
    limits = _pdf_limits()
    try:
        pages = await asyncio.wait_for(
            asyncio.to_thread(extract_pages, data, limits), timeout=limits.max_parse_seconds + 1.0
        )
    except (PdfValidationError, asyncio.TimeoutError):
        return StoredPdfText(**identity, failure=SourceIntegrityFailure.file_unreadable)
    return StoredPdfText(**identity, pages=tuple((p["page_number"], p["text_content"]) for p in pages))


def source_integrity_failure(
    stored: StoredPdfText, version: DocumentVersion, source: ExtractionSourceText
) -> SourceIntegrityFailure | None:
    """
    Compares the stored PDF's text with the version record and the page records loaded under
    the write lock. Returns None only if all of them agree.
    """
    if stored.failure is not None:
        return stored.failure
    if (
        (stored.version_id, stored.stored_path, stored.sha256) != (version.id, version.stored_path, version.sha256_hash)
        or source.text.document_version_id != version.id
    ):
        return SourceIntegrityFailure.version_changed
    if len(source.text.pages) != len(stored.pages):
        return SourceIntegrityFailure.page_count_mismatch
    if source.text.problem is not None:
        return SourceIntegrityFailure.page_numbering_mismatch
    records = {page.page_number: page.text for page in source.text.pages}
    if any(records.get(number) != text for number, text in stored.pages):
        return SourceIntegrityFailure.page_text_mismatch
    return None
