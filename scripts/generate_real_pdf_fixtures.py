"""
Generate the real-producer PDF test fixtures (tests/fixtures/real_pdfs/) with headless Chrome.

    python scripts/generate_real_pdf_fixtures.py [--chrome <path to chrome.exe or msedge.exe>]

The documents are SYNTHETIC and non-confidential (a fictional trust prospectus). They are printed
by Chromium's real PDF backend (Skia: subset-embedded fonts, ToUnicode maps, text positioning),
not by the minimal generator of tests/pdf_fixtures.py, so that ingestion is tested against the
kind of file a real user uploads:

- trust_prospectus.pdf  four text pages (sections, a table, ligatures, curly quotes, dashes)
- mixed_scanned.pdf     two text pages and one image-only page in between (a scanned page)
- scanned_only.pdf      two image-only pages: no text layer at all (OCR required)

A throwaway browser profile is used; nothing is sent over the network.
"""

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "real_pdfs"
DEFAULT_BROWSERS = (
    Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
    Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
)

STYLE = """
<style>
  @page { size: A4; margin: 22mm; }
  body { font-family: Georgia, 'Times New Roman', serif; font-size: 11.5pt; line-height: 1.45; }
  h1 { font-size: 17pt; margin: 0 0 6pt; } h2 { font-size: 13pt; margin: 14pt 0 4pt; }
  .page { page-break-after: always; } .page:last-child { page-break-after: auto; }
  table { border-collapse: collapse; margin-top: 6pt; } td, th { border: 1px solid #444; padding: 3pt 6pt; }
  .scan { width: 100%; height: 230mm; display: block; }
</style>
"""

PAGES = [
    # Page 1
    """<h1>Meridian Harbour Property Trust</h1>
    <p><strong>Prospectus — Series A Digital Units</strong> (fictional test document)</p>
    <h2>1. Nature of the Investment</h2>
    <p>Each Digital Unit represents a fractional beneficial interest in the Meridian Harbour Property Trust.
    Unitholders do not acquire legal title to the underlying real estate; legal title remains vested in the Trustee.</p>
    <p>The Trust’s sole asset is the office building known as “Harbour Point”, 14 Quay Street.</p>""",
    # Page 2
    """<h2>2. Income and Distributions</h2>
    <p>Net rental income shall be distributed quarterly, pro rata to the number of Digital Units held,
    within thirty days after the end of each calendar quarter.</p>
    <table><tr><th>Item</th><th>Amount</th></tr>
    <tr><td>Minimum subscription</td><td>$1,000</td></tr><tr><td>Total offering</td><td>$12,500,000</td></tr></table>
    <h2>3. Voting</h2>
    <p>Unitholders holding at least 25% of the outstanding Digital Units may requisition a general meeting;
    each Digital Unit carries one vote on matters reserved to unitholders.</p>""",
    # Page 3
    """<h2>4. Transfer Restrictions</h2>
    <p>Digital Units may be transferred only to verified accredited investors whose wallet addresses have been
    approved by the Trustee — any other transfer is void.</p>
    <h2>5. Redemption</h2>
    <p>The Trust does not offer a redemption facility; unitholders may exit only by transfer on an approved venue.</p>""",
    # Page 4
    """<h2>6. Insolvency</h2>
    <p>In the event of the Trustee’s insolvency, the Trust property shall not form part of the Trustee’s estate
    and remains held for the benefit of the unitholders.</p>
    <h2>7. Governing Law</h2>
    <p>This Prospectus and the Trust Deed are governed by the laws of England and Wales, and the courts of
    England have exclusive jurisdiction.</p>""",
]


def _png(width: int = 600, height: int = 800) -> bytes:
    """A grayscale raster that looks like a scanned page (stripes), with no text layer."""
    rows = []
    for y in range(height):
        shade = 235 if (y // 18) % 3 else 120
        rows.append(b"\x00" + bytes([shade if 40 < x < width - 40 else 250 for x in range(width)]))
    raw = zlib.compress(b"".join(rows), 9)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _html(pages: list[str]) -> str:
    body = "".join(f'<div class="page">{p}</div>' for p in pages)
    return f"<!doctype html><html><head><meta charset='utf-8'>{STYLE}</head><body>{body}</body></html>"


def _print(browser: Path, html: Path, pdf: Path, profile: Path) -> None:
    if pdf.exists():
        pdf.unlink()
    subprocess.run([
        str(browser), "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
        "--disable-extensions", f"--user-data-dir={profile}", "--no-pdf-header-footer",
        f"--print-to-pdf={pdf}", html.as_uri(),
    ], check=True, timeout=120, capture_output=True)
    # On Windows the launcher can return before the browser has written the file
    deadline, last_size = time.monotonic() + 60, -1
    while time.monotonic() < deadline:
        size = pdf.stat().st_size if pdf.exists() else -1
        if size > 0 and size == last_size and pdf.read_bytes().rstrip().endswith(b"%%EOF"):
            return
        last_size = size
        time.sleep(1)
    raise RuntimeError(f"{browser.name} produced no complete PDF for {html.name}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate real-producer PDF fixtures with headless Chrome")
    parser.add_argument("--chrome", type=Path, default=next((p for p in DEFAULT_BROWSERS if p.exists()), None))
    args = parser.parse_args()
    if args.chrome is None or not args.chrome.exists():
        print("No Chrome or Edge executable found; pass --chrome.")
        return 2
    OUT.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="lp-pdf-fixtures-"))
    try:
        scan = work / "scan.png"
        scan.write_bytes(_png())
        image_page = '<img class="scan" src="scan.png" alt="">'
        documents = {
            "trust_prospectus.pdf": PAGES,
            "mixed_scanned.pdf": [PAGES[0], image_page, PAGES[3]],
            "scanned_only.pdf": [image_page, image_page],
        }
        for name, pages in documents.items():
            html = work / f"{Path(name).stem}.html"
            html.write_text(_html(pages), encoding="utf-8")
            _print(args.chrome, html, OUT / name, work / "profile")
            print(f"{name}: {(OUT / name).stat().st_size} bytes")
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
