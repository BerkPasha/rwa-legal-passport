"""
PDF validation and text extraction service.

PDF content is treated as UNTRUSTED input (Stage 9B-2):
- An empty file, a file without the PDF signature (%PDF-) and a file without
  the %%EOF marker (truncated) are rejected.
- Parsing is done with pypdf; every exception raised by the parser is reported
  as "could not be parsed" (internal details are not passed to the client).
- Encrypted PDFs, PDFs without pages, PDFs exceeding the page or text limit,
  and parsing that exceeds the time budget are rejected. The limits are configurable.
- Nothing is sent to external AI services. On scanned (image-based) pages the
  text is an empty string — this is normal.

LIMITATION: The time budget is checked between pages; a pathological stream
inside a single page (e.g. a compression bomb) cannot be interrupted in
pypdf 4.3.1. See PRODUCT_READINESS.md §10.
"""

import hashlib
import io
import time
from dataclasses import dataclass

from pypdf import PdfReader

# PDF files start with this signature (followed by the version number)
_PDF_MAGIC = b"%PDF-"
_EOF_MARKER = b"%%EOF"
# PDF readers look for the %%EOF marker in the last 1024 bytes of the file
_EOF_SEARCH_WINDOW = 1024


class PdfValidationError(ValueError):
    """PDF rejected. `reason` may be shown to the client; it contains no internal details."""

    def __init__(self, reason: str, limit_exceeded: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.limit_exceeded = limit_exceeded


@dataclass(frozen=True)
class PdfLimits:
    max_pages: int
    max_text_chars: int
    max_parse_seconds: float


def calculate_sha256(data: bytes) -> str:
    """Returns the SHA-256 fingerprint (hex string, 64 characters)."""
    return hashlib.sha256(data).hexdigest()


def is_valid_pdf_content(data: bytes) -> bool:
    """
    Checks whether the file starts with the PDF signature.
    Does not trust the content-type header — that header can be manipulated.
    """
    return len(data) >= len(_PDF_MAGIC) and data[: len(_PDF_MAGIC)] == _PDF_MAGIC


def has_eof_marker(data: bytes) -> bool:
    """Looks for %%EOF in the last 1024 bytes to catch truncated files."""
    return _EOF_MARKER in data[-_EOF_SEARCH_WINDOW:]


def extract_pages(data: bytes, limits: PdfLimits) -> list[dict]:
    """
    Parses an untrusted PDF and extracts the page texts.
    Returned structure: [{"page_number": int, "text_content": str}, ...]

    Raises PdfValidationError: for a PDF that cannot be parsed, is encrypted,
    has no pages or exceeds the limits.
    """
    deadline = time.monotonic() + limits.max_parse_seconds
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise PdfValidationError("Encrypted PDF files are not supported.")
        page_count = len(reader.pages)
    except PdfValidationError:
        raise
    except Exception:  # noqa: BLE001 — untrusted input; the parser can raise any kind of error
        raise PdfValidationError("PDF could not be parsed: the file is corrupt or invalid.") from None

    if page_count < 1:
        raise PdfValidationError("PDF contains no pages.")
    if page_count > limits.max_pages:
        raise PdfValidationError(
            f"PDF exceeds the {limits.max_pages}-page limit.", limit_exceeded=True
        )

    pages: list[dict] = []
    total_chars = 0
    for index in range(page_count):
        if time.monotonic() > deadline:
            raise PdfValidationError("PDF processing exceeded the time limit.", limit_exceeded=True)
        try:
            text = (reader.pages[index].extract_text() or "").strip()
        except Exception:  # noqa: BLE001
            raise PdfValidationError(
                f"PDF could not be parsed: page {index + 1} is unreadable."
            ) from None
        # The NUL character causes problems in text columns; it is removed
        text = text.replace("\x00", "")
        total_chars += len(text)
        if total_chars > limits.max_text_chars:
            raise PdfValidationError(
                f"PDF text exceeds the {limits.max_text_chars}-character limit.", limit_exceeded=True
            )
        pages.append({"page_number": index + 1, "text_content": text})
    if time.monotonic() > deadline:
        raise PdfValidationError("PDF processing exceeded the time limit.", limit_exceeded=True)
    return pages
