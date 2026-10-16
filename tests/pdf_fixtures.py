"""
PDF test fixtures generated in memory (no binary files are added to the repository).

make_text_pdf()   — multi-page PDF with Helvetica text and a valid xref table
make_source_pdf() — the standard two-page source document that contains the mock AI quotations
make_blank_pdf()  — PDF with blank pages, created with pypdf
make_zero_page_pdf(), make_encrypted_pdf() — valid structures that must be rejected

All content is fictional.
"""

import io

from pypdf import PdfWriter

from tests.mock_ai import MOCK_SOURCE_PAGES


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_text_pdf(pages: list[list[str]]) -> bytes:
    """
    Produces a PDF whose pages contain the given lines.
    pages: [["line 1", "line 2"], ["page 2 line"]]  (ASCII text)
    """
    font_obj = 3 + 2 * len(pages)
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        (
            "<< /Type /Pages /Kids ["
            + " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
            + f"] /Count {len(pages)} >>"
        ).encode(),
    ]
    for i, lines in enumerate(pages):
        content_obj = 4 + 2 * i
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 {font_obj} 0 R >> >> /Contents {content_obj} 0 R >>"
            ).encode()
        )
        ops = "BT /F1 12 Tf 14 TL 72 720 Td " + " T* ".join(f"({_escape(l)}) Tj" for l in lines) + " ET"
        stream = ops.encode("latin-1")
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(f"{number} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_at = out.tell()
    out.write(f"xref\n0 {len(objects) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for offset in offsets:
        out.write(f"{offset:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_at}\n%%EOF\n".encode()
    )
    return out.getvalue()


def make_source_pdf(*extra_lines: str) -> bytes:
    """
    MOCK_SOURCE_PAGES as a PDF: the mock AI's quotations verify on page 1. Extra lines are
    appended to page 2 (e.g. to give each document version its own content).
    """
    first, second = MOCK_SOURCE_PAGES
    return make_text_pdf([list(first), [*second, *extra_lines]])


def make_blank_pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def make_zero_page_pdf() -> bytes:
    buf = io.BytesIO()
    PdfWriter().write(buf)
    return buf.getvalue()


def make_encrypted_pdf() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="fictional-test-password", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()
