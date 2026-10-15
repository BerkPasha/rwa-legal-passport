"""
PDF doğrulama ve metin çıkarma servisi.

Dış AI servislerine gönderim yapılmaz.
Metin taramalı (scanned) PDF'lerde boş string döner — bu normaldir.
"""

import hashlib
import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

# PDF dosyalarının ilk 4 byte'ı bu imzayla başlar
_PDF_MAGIC = b"%PDF"


def calculate_sha256(data: bytes) -> str:
    """SHA-256 parmak izi döner (hex string, 64 karakter)."""
    return hashlib.sha256(data).hexdigest()


def is_valid_pdf_content(data: bytes) -> bool:
    """
    Dosyanın gerçekten PDF olup olmadığını magic byte kontrolüyle doğrular.
    İçerik-türü başlığına güvenmez — bu başlık manipüle edilebilir.
    """
    return len(data) >= 4 and data[:4] == _PDF_MAGIC


def extract_pages(data: bytes) -> list[dict]:
    """
    PDF'in her sayfasından metin çıkarır.
    Dönen yapı: [{"page_number": int, "text_content": str}, ...]

    Taramalı (görüntü tabanlı) sayfalarda text_content boş string olur.
    Raises ValueError: PDF ayrıştırılamıyorsa.
    """
    try:
        reader = PdfReader(io.BytesIO(data))
        return [
            {
                "page_number": i + 1,
                "text_content": (page.extract_text() or "").strip(),
            }
            for i, page in enumerate(reader.pages)
        ]
    except PdfReadError as exc:
        raise ValueError(f"PDF ayrıştırılamadı: {exc}") from exc
