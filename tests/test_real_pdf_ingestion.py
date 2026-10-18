"""
Stage 10A-1 — real PDF ingestion and page-grounded evidence.

The documents in tests/fixtures/real_pdfs/ were printed by a real PDF producer (headless
Chrome, Skia/PDF; scripts/generate_real_pdf_fixtures.py): subset-embedded fonts, ToUnicode
maps, line wrapping, typographic quotation marks and dashes, an image-only "scanned" page. They
are synthetic and non-confidential. No test sends anything to an AI provider or the network: the
real AnthropicAIService runs against an in-process fake client.

Coverage:
 1. Text extraction of a real multi-page PDF: page numbers, exact raw text, determinism
 2. Upload: the stored page records are exactly the extracted text; text-layer summary
 3. Exact quote-to-page matching on the real text (line breaks, typographic equivalents)
 4. Incorrect page references and near misses (wrong page, impossible pages, page breaks,
    partial tokens, case, a fabricated term, the table-cell spacing artifact)
 5. API flow: verified claims can be approved; wrong or invented pages block the approval;
    the passport serves no page text
 6. Scanned PDFs: partial and missing text layers, requires_ocr, no_text_layer evidence
 7. The real provider is never called for a document without extractable text
 8. Invalid and empty documents: rejected safely, nothing stored
 9. Tenant isolation
10. Existing mock-mode compatibility
"""

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from httpx import AsyncClient
from pypdf import PdfReader, PdfWriter
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.enums import EvidenceStatus, LegalFieldName, TextLayerStatus
from app.evidence import EvidenceClaim, SourceDocumentText, verify_evidence
from app.main import app
from app.models.document import Document, DocumentPage, DocumentVersion
from app.models.extraction import LegalExtraction
from app.routers.documents import _text_layer
from app.schemas.extraction import ClaimSource, RawClaim
from app.services.ai import (
    EMPTY_PAGE_TEXT,
    NO_EXTRACTABLE_TEXT,
    AnthropicAIService,
    get_ai_service,
    has_extractable_text,
)
from app.services.pdf import PdfLimits, extract_pages
from tests.mock_ai import MockAIService
from tests.test_approval_evidence_gate import ScriptedAI

F = LegalFieldName
FIXTURES = Path(__file__).parent / "fixtures" / "real_pdfs"
# The reviewed synthetic files, pinned (tests/fixtures/real_pdfs/README.md)
FIXTURE_SHA256 = {
    name: digest
    for digest, name in (line.split() for line in (FIXTURES / "SHA256SUMS").read_text(encoding="utf-8").splitlines() if line)
}


def _fixture(name: str, directory: Path = FIXTURES) -> bytes:
    path = directory / name
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing: the synthetic real-producer fixtures are versioned in git "
            "(see tests/fixtures/real_pdfs/README.md)."
        )
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != FIXTURE_SHA256.get(name):
        raise ValueError(f"{path} is not the reviewed synthetic fixture (SHA-256 differs from SHA256SUMS).")
    return data


PROSPECTUS = _fixture("trust_prospectus.pdf")   # 4 text pages
MIXED = _fixture("mixed_scanned.pdf")           # text, image only, text
SCANNED = _fixture("scanned_only.pdf")          # 2 image-only pages
LIMITS = PdfLimits(max_pages=500, max_text_chars=2_000_000, max_parse_seconds=30.0)
FAKE_KEY = "fictional-key-for-tests-only"

# Exactly what pypdf 4.3.1 extracts from pages 3 and 4 of the prospectus: line breaks where the
# producer wrapped the lines, the em dash (U+2014) and right single quotation marks (U+2019) as
# printed. Nothing is normalized at extraction; normalization happens only inside the check.
PROSPECTUS_PAGE_3 = (
    "4. Transfer Restrictions\n"
    "Digital Units may be transferred only to verified accredited investors whose wallet\n"
    "addresses have been approved by the Trustee — any other transfer is void.\n"
    "5. Redemption\n"
    "The Trust does not offer a redemption facility; unitholders may exit only by transfer on an\n"
    "approved venue."
)
PROSPECTUS_PAGE_4 = (
    "6. Insolvency\n"
    "In the event of the Trustee’s insolvency, the Trust property shall not form part of the\n"
    "Trustee’s estate and remains held for the benefit of the unitholders.\n"
    "7. Governing Law\n"
    "This Prospectus and the Trust Deed are governed by the laws of England and Wales, and\n"
    "the courts of England have exclusive jurisdiction."
)

# One verbatim quotation per legal field, copied from the page it is cited on (several of them
# run across a line break of the extracted text)
GROUNDED = {
    F.ownership_nature: (1, "Unitholders do not acquire legal title to the underlying real estate; "
                            "legal title remains vested in the Trustee."),
    F.income_rights: (2, "Net rental income shall be distributed quarterly, pro rata to the number "
                         "of Digital Units held"),
    F.voting_rights: (2, "each Digital Unit carries one vote on matters reserved to unitholders"),
    F.transfer_restrictions: (3, "approved by the Trustee — any other transfer is void"),
    F.redemption_rights: (3, "The Trust does not offer a redemption facility"),
    F.insolvency_provisions: (4, "the Trust property shall not form part of the Trustee’s estate"),
    F.jurisdiction: (4, "governed by the laws of England and Wales"),
}


def _pages(pdf: bytes) -> list[dict]:
    return extract_pages(pdf, LIMITS)


def _source(pdf: bytes, version_id: str = "v1") -> SourceDocumentText:
    return SourceDocumentText.from_page_records(version_id, _pages(pdf))


def _check(page, quotation: str, pdf: bytes = PROSPECTUS):
    return verify_evidence(EvidenceClaim(document_version_id="v1", source_page=page, quotation=quotation),
                           _source(pdf))


def _claim(field: LegalFieldName, page: int, passage: str) -> RawClaim:
    return RawClaim(field=field, value="Stated value.", is_unknown=False,
                    source=ClaimSource(page_number=page, supporting_passage=passage))


def _grounded_claims(**wrong_pages: int) -> list[RawClaim]:
    """The seven grounded claims; `wrong_pages` cites some of them on another page."""
    return [_claim(field, wrong_pages.get(field.value, page), quote) for field, (page, quote) in GROUNDED.items()]


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, headers: dict, filename: str = "prospectus.pdf"):
    return await client.post(
        f"/assets/{asset_id}/documents", headers=headers,
        data={"document_type": "prospectus", "title": "Series A Prospectus"},
        files={"file": (filename, pdf, "application/pdf")},
    )


async def _uploaded(client: AsyncClient, asset_id: str, pdf: bytes, headers: dict) -> dict:
    r = await _upload(client, asset_id, pdf, headers)
    assert r.status_code == 201, r.text
    return r.json()


async def _extract(client: AsyncClient, document_id: str, headers: dict, service):
    app.dependency_overrides[get_ai_service] = lambda: service
    try:
        # The real service is an external processor: the request confirms it (Stage 10A-2)
        return await client.post(f"/documents/{document_id}/extractions", headers=headers,
                                 json={"confirm_external_processing": True})
    finally:
        app.dependency_overrides[get_ai_service] = lambda: MockAIService()


async def _counts(db: AsyncSession) -> dict[str, int]:
    async def count(model) -> int:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()
    return {"documents": await count(Document), "versions": await count(DocumentVersion),
            "pages": await count(DocumentPage), "extractions": await count(LegalExtraction)}


def _stored_files(tmp_path: Path) -> list[Path]:
    root = tmp_path / "storage"   # conftest.client → StorageService(tmp_path / "storage")
    return [p for p in root.rglob("*") if p.is_file()] if root.exists() else []


class _RecordingMessages:
    """Fake Messages API: records every request, returns a fixed response, never uses the network."""

    def __init__(self, text: str):
        self.requests: list[dict] = []
        self._response = SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn")

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return self._response


def _real_service(claims: list[RawClaim]) -> AnthropicAIService:
    service = AnthropicAIService(api_key=FAKE_KEY, model="fake-model")
    service._client = SimpleNamespace(
        messages=_RecordingMessages(json.dumps({"claims": [c.model_dump(mode="json") for c in claims]}))
    )
    return service


def _evidence(extraction: dict) -> dict[str, str]:
    return {c["field_name"]: c["evidence"]["status"] for c in extraction["claims"]}


# ── 1. Text extraction of a real multi-page PDF ──────────────────────────────


def test_fixtures_are_exactly_the_reviewed_synthetic_files():
    assert sorted(FIXTURE_SHA256) == ["mixed_scanned.pdf", "scanned_only.pdf", "trust_prospectus.pdf"]
    assert sorted(p.name for p in FIXTURES.glob("*.pdf")) == sorted(FIXTURE_SHA256)   # nothing else is here
    for name, pdf in (("trust_prospectus.pdf", PROSPECTUS), ("mixed_scanned.pdf", MIXED), ("scanned_only.pdf", SCANNED)):
        assert hashlib.sha256(pdf).hexdigest() == FIXTURE_SHA256[name]
        metadata = dict(PdfReader(io.BytesIO(pdf)).metadata or {})
        assert metadata.get("/Title") == name.replace(".pdf", ".html")    # no local path or user name
        assert set(metadata) <= {"/Title", "/Creator", "/Producer", "/CreationDate", "/ModDate"}
    assert "(fictional test document)" in _pages(PROSPECTUS)[0]["text_content"]
    assert "(fictional test document)" in _pages(MIXED)[0]["text_content"]


def test_a_replaced_or_missing_fixture_is_refused(tmp_path: Path):
    (tmp_path / "trust_prospectus.pdf").write_bytes(PROSPECTUS + b"\n")   # any other content
    with pytest.raises(ValueError, match="not the reviewed synthetic fixture"):
        _fixture("trust_prospectus.pdf", tmp_path)
    with pytest.raises(FileNotFoundError, match="versioned in git"):
        _fixture("scanned_only.pdf", tmp_path)
    assert _fixture("trust_prospectus.pdf") == PROSPECTUS


def test_fixtures_are_tracked_by_git_despite_the_pdf_ignore_rule():
    gitignore = (Path(__file__).parent.parent / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "*.pdf" in gitignore
    for name in FIXTURE_SHA256:
        assert f"!tests/fixtures/real_pdfs/{name}" in gitignore


def test_fixtures_come_from_a_real_pdf_producer():
    for pdf, page_count in ((PROSPECTUS, 4), (MIXED, 3), (SCANNED, 2)):
        reader = PdfReader(io.BytesIO(pdf))
        assert "Skia/PDF" in (reader.metadata.producer or "")
        assert len(reader.pages) == page_count and not reader.is_encrypted


def test_multi_page_text_is_extracted_per_page_in_order():
    pages = _pages(PROSPECTUS)
    assert [p["page_number"] for p in pages] == [1, 2, 3, 4]
    headings = {1: "1. Nature of the Investment", 2: "3. Voting", 3: "5. Redemption", 4: "7. Governing Law"}
    for page in pages:
        for number, heading in headings.items():
            # Each section heading is on its own page and on no other
            assert (heading in page["text_content"]) is (number == page["page_number"])


def test_extracted_text_is_kept_exactly_as_printed():
    pages = {p["page_number"]: p["text_content"] for p in _pages(PROSPECTUS)}
    assert pages[3] == PROSPECTUS_PAGE_3
    assert pages[4] == PROSPECTUS_PAGE_4
    # Typographic characters are not replaced at extraction
    assert "Prospectus — Series A" in pages[1]
    assert "known as “Harbour Point”" in pages[1]
    assert "The Trust’s sole asset" in pages[1]


def test_extraction_is_deterministic():
    assert _pages(PROSPECTUS) == _pages(PROSPECTUS)
    assert _source(PROSPECTUS).text_sha256 == _source(PROSPECTUS).text_sha256


# ── 2. Upload: stored page records and the text-layer summary ────────────────


async def test_uploaded_version_stores_exactly_the_extracted_pages(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    version = doc["current_version"]
    assert version["sha256_hash"] == hashlib.sha256(PROSPECTUS).hexdigest()
    assert version["file_size"] == len(PROSPECTUS)
    assert version["text_layer"]["status"] == TextLayerStatus.complete.value
    assert version["text_layer"]["pages_without_text"] == [] and version["text_layer"]["requires_ocr"] is False

    r = await client.get(f"/documents/{doc['id']}/versions/1/text", headers=issuer_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["page_count"] == 4
    assert body["pages"] == [{"page_number": p["page_number"], "text_content": p["text_content"]}
                             for p in _pages(PROSPECTUS)]
    assert body["source_text_sha256"] == _source(PROSPECTUS).text_sha256

    downloaded = await client.get(f"/documents/{doc['id']}/download", headers=issuer_headers)
    assert downloaded.status_code == 200 and downloaded.content == PROSPECTUS


async def test_text_layer_is_reported_for_every_version(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    r = await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                          files={"file": ("v2.pdf", MIXED, "application/pdf")})
    assert r.status_code == 201, r.text
    assert r.json()["text_layer"]["status"] == TextLayerStatus.partial.value

    versions = (await client.get(f"/documents/{doc['id']}/versions", headers=issuer_headers)).json()
    assert [(v["version_number"], v["text_layer"]["status"], v["text_layer"]["pages_without_text"])
            for v in versions] == [(1, "complete", []), (2, "partial", [2])]
    detail = (await client.get(f"/documents/{doc['id']}", headers=issuer_headers)).json()
    assert detail["current_version"]["version_number"] == 2
    assert detail["current_version"]["text_layer"]["requires_ocr"] is True
    listed = (await client.get(f"/assets/{created_asset['id']}/documents", headers=issuer_headers)).json()
    assert listed[0]["current_version"]["text_layer"] == detail["current_version"]["text_layer"]


def test_text_layer_uses_the_evidence_rule_for_pages_without_text():
    # Whitespace and invisible formatting characters are no text layer, exactly as for no_text_layer
    records = [{"page_number": 1, "text_content": "Legal title remains vested in the Trustee."},
               {"page_number": 2, "text_content": " \n​­﻿ "},
               {"page_number": 3, "text_content": ""}]
    source = SourceDocumentText.from_page_records("v1", records)
    layer = _text_layer(source)
    assert (layer.status, layer.page_count, layer.pages_with_text, layer.pages_without_text, layer.requires_ocr) == (
        TextLayerStatus.partial, 3, 1, [2, 3], True)
    for page in (2, 3):
        check = verify_evidence(EvidenceClaim("v1", page, "The Trustee holds fee simple title to the Property."), source)
        assert check.status is EvidenceStatus.no_text_layer


def test_text_layer_is_not_reported_for_missing_or_invalid_page_records():
    assert _text_layer(SourceDocumentText.from_page_records("v1", [])) is None
    duplicate = [{"page_number": 1, "text_content": "a"}, {"page_number": 1, "text_content": "b"}]
    assert _text_layer(SourceDocumentText.from_page_records("v1", duplicate)) is None


# ── 3. Exact quote-to-page matching on the real text ─────────────────────────


@pytest.mark.parametrize("field", list(GROUNDED), ids=lambda f: f.value)
def test_verbatim_quotation_verifies_on_its_page_only(field: LegalFieldName):
    page, quote = GROUNDED[field]
    check = _check(page, quote)
    assert check.status is EvidenceStatus.verified, check.explanation
    assert check.found_on_pages == (page,) and check.occurrences_on_cited_page == 1


@pytest.mark.parametrize("page, quotation", [
    # Straight quotation marks for the printed curly ones (U+2019, U+201C, U+201D)
    (1, "The Trust's sole asset is the office building known as \"Harbour Point\""),
    # A hyphen for the printed em dash
    (3, "approved by the Trustee - any other transfer is void"),
    # Spaces for the line breaks of the extracted text
    (4, "governed by the laws of England and Wales, and the courts of England have exclusive jurisdiction."),
])
def test_typographic_variants_and_line_breaks_are_equivalent(page: int, quotation: str):
    assert _check(page, quotation).status is EvidenceStatus.verified


# ── 4. Incorrect page references and near misses ─────────────────────────────


def test_quotation_cited_on_the_wrong_page_is_not_verified():
    page, quote = GROUNDED[F.jurisdiction]
    check = _check(2, quote)
    assert check.status is EvidenceStatus.found_on_other_page
    assert check.found_on_pages == (page,) and "appears on page 4" in check.explanation


@pytest.mark.parametrize("page", [0, 5, 99, -1])
def test_page_outside_the_document_is_never_verified(page: int):
    check = _check(page, GROUNDED[F.jurisdiction][1])
    assert check.status is EvidenceStatus.page_out_of_range
    assert check.page_count == 4 and check.found_on_pages == (4,)


def test_missing_page_reference_is_never_verified():
    assert _check(None, GROUNDED[F.jurisdiction][1]).status is EvidenceStatus.page_missing


@pytest.mark.parametrize("page", [1, 2])
def test_quotation_spanning_a_page_break_never_matches(page: int):
    # The end of page 1 followed by the start of page 2: pages are never joined
    assert _check(page, "14 Quay Street. 2. Income and Distributions").status is EvidenceStatus.not_found


@pytest.mark.parametrize("quotation", [
    "ental income shall be distributed quarterly",      # starts inside the token "rental"
    "net rental income shall be distributed quarterly",  # letter case differs
    "Net rental income shall be distributed monthly",    # a fabricated term
    "Unitholders acquire legal title to the underlying real estate",  # a word removed
])
def test_near_misses_are_not_found(quotation: str):
    assert _check(2 if "rental" in quotation else 1, quotation).status is EvidenceStatus.not_found


def test_table_cell_spacing_artifact_is_not_repaired():
    # The producer prints "Minimum subscription" and "$1,000" in two table cells; the text layer
    # has no space between them. The check never guesses the missing space.
    assert "Minimum subscription$1,000" in _pages(PROSPECTUS)[1]["text_content"]
    assert _check(2, "Minimum subscription $1,000 Total offering").status is EvidenceStatus.not_found


# ── 5. API flow on the real PDF ──────────────────────────────────────────────


async def test_grounded_claims_on_a_real_pdf_verify_and_can_be_approved(
    client: AsyncClient, created_asset: dict, issuer_headers: dict, inst_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    service = _real_service(_grounded_claims())
    r = await _extract(client, doc["id"], issuer_headers, service)
    assert r.status_code == 201, r.text
    extraction = r.json()
    assert extraction["status"] == "draft"                       # never approved automatically
    assert extraction["approval_state"]["current_approval_types"] == []
    assert set(_evidence(extraction).values()) == {"verified"}
    assert {c["field_name"]: c["evidence"]["cited_page"] for c in extraction["claims"]} == {
        field.value: page for field, (page, _) in GROUNDED.items()}

    # The real service received the stored page text with its page markers, unchanged
    sent = service._client.messages.requests[0]["messages"][0]["content"]
    for page in _pages(PROSPECTUS):
        assert f"=== PAGE {page['page_number']} ===\n{page['text_content']}" in sent

    approval = await client.post(f"/extractions/{extraction['id']}/approvals", headers=issuer_headers, json={})
    assert approval.status_code == 201, approval.text

    passport = await client.get(
        f"/passport/{created_asset['blockchain_network']}/{created_asset['stellar_asset_code']}"
        f"/{created_asset['stellar_issuer_public_key']}", headers=inst_headers)
    assert passport.status_code == 200, passport.text
    served = passport.text
    assert len(passport.json()["legal_rights"]) == 7
    # Quotations only: no page text, and nothing of the pages that no claim quotes
    assert "text_content" not in served and "Total offering" not in served and "Harbour Point" not in served


async def test_wrong_and_invented_page_references_block_the_approval(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    claims = _grounded_claims(jurisdiction=2, voting_rights=7)   # page 7 does not exist
    r = await _extract(client, doc["id"], issuer_headers, ScriptedAI(*claims))
    assert r.status_code == 201, r.text
    extraction = r.json()
    evidence = _evidence(extraction)
    assert evidence.pop("jurisdiction") == "found_on_other_page"
    assert evidence.pop("voting_rights") == "page_out_of_range"
    assert set(evidence.values()) == {"verified"}

    approval = await client.post(f"/extractions/{extraction['id']}/approvals", headers=issuer_headers, json={})
    assert approval.status_code == 422
    failures = {f["field_name"]: f["status"] for f in approval.json()["evidence_failures"]}
    assert failures == {"jurisdiction": "found_on_other_page", "voting_rights": "page_out_of_range"}
    refreshed = (await client.get(f"/extractions/{extraction['id']}", headers=issuer_headers)).json()
    assert refreshed["status"] == "draft" and refreshed["approval_state"]["current_approval_types"] == []


# ── 6. Scanned PDFs ──────────────────────────────────────────────────────────


async def test_mixed_pdf_identifies_the_scanned_page(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], MIXED, issuer_headers)
    layer = doc["current_version"]["text_layer"]
    assert (layer["status"], layer["page_count"], layer["pages_with_text"], layer["pages_without_text"],
            layer["requires_ocr"]) == ("partial", 3, 2, [2], True)
    assert "OCR is not supported" in layer["notice"] and "physical positions" in layer["notice"]

    text = (await client.get(f"/documents/{doc['id']}/versions/1/text", headers=issuer_headers)).json()
    assert [p["text_content"] == "" for p in text["pages"]] == [False, True, False]   # nothing invented

    claims = [_claim(F.ownership_nature, 2, GROUNDED[F.ownership_nature][1]),   # on page 1, cited on the scan
              _claim(F.jurisdiction, 3, GROUNDED[F.jurisdiction][1]),
              _claim(F.redemption_rights, 2, "The Trust does not offer a redemption facility")]
    extraction = (await _extract(client, doc["id"], issuer_headers, ScriptedAI(*claims))).json()
    assert {k: v for k, v in _evidence(extraction).items() if v != "not_applicable"} == {
        "ownership_nature": "found_on_other_page", "jurisdiction": "verified", "redemption_rights": "no_text_layer"}


async def test_scanned_only_pdf_requires_ocr(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], SCANNED, issuer_headers)
    layer = doc["current_version"]["text_layer"]
    assert (layer["status"], layer["pages_with_text"], layer["pages_without_text"], layer["requires_ocr"]) == (
        "none", 0, [1, 2], True)


# ── 7. The real provider is never called without extractable text ────────────


def test_has_extractable_text():
    assert has_extractable_text(_pages(PROSPECTUS)) and has_extractable_text(_pages(MIXED))
    assert not has_extractable_text(_pages(SCANNED))
    assert not has_extractable_text([])
    assert not has_extractable_text([{"page_number": 1, "text_content": "​ \n­"}])
    assert not has_extractable_text([{"page_number": 1, "text_content": None}])


async def test_real_provider_is_not_called_for_a_scanned_pdf(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], SCANNED, issuer_headers)
    service = _real_service(_grounded_claims())
    r = await _extract(client, doc["id"], issuer_headers, service)
    assert r.status_code == 422
    assert r.json() == {"detail": NO_EXTRACTABLE_TEXT, "extraction_refusal": "no_extractable_text"}
    assert service._client.messages.requests == []          # nothing was sent
    assert (await _counts(async_db))["extractions"] == 0


async def test_real_provider_gets_marked_empty_pages_of_a_mixed_pdf(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], MIXED, issuer_headers)
    service = _real_service([_claim(F.jurisdiction, 3, GROUNDED[F.jurisdiction][1])])
    r = await _extract(client, doc["id"], issuer_headers, service)
    assert r.status_code == 201, r.text
    assert len(service._client.messages.requests) == 1
    sent = service._client.messages.requests[0]["messages"][0]["content"]
    assert f"=== PAGE 2 ===\n{EMPTY_PAGE_TEXT}\n\n=== PAGE 3 ===" in sent
    assert _evidence(r.json())["jurisdiction"] == "verified"


# ── 8. Invalid and empty documents ───────────────────────────────────────────


def _encrypted_prospectus() -> bytes:
    writer = PdfWriter(clone_from=PdfReader(io.BytesIO(PROSPECTUS)))
    writer.encrypt(user_password="fictional-test-password", algorithm="RC4-128")
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def _zero_page_pdf() -> bytes:
    buf = io.BytesIO()
    PdfWriter().write(buf)
    return buf.getvalue()


_N = len(PROSPECTUS)
INVALID = {
    "truncated": (PROSPECTUS[: _N // 2], 422, "truncated"),
    "corrupted_body": (PROSPECTUS[: _N // 4] + b"\x00" * (_N // 2) + PROSPECTUS[_N // 4 + _N // 2:], 422,
                       "could not be parsed"),
    "header_and_trailer_only": (PROSPECTUS[:1024] + PROSPECTUS[-1024:], 422, "could not be parsed"),
    "encrypted": (_encrypted_prospectus(), 422, "Encrypted"),
    "zero_bytes": (b"", 422, "File is empty"),
    "zero_pages": (_zero_page_pdf(), 422, "no pages"),
    "html_named_pdf": (b"<!doctype html><html><body>Prospectus</body></html>", 415, "magic byte"),
}


@pytest.mark.parametrize("name", sorted(INVALID))
async def test_invalid_documents_are_rejected_and_nothing_is_stored(
    name: str, client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict,
    tmp_path: Path,
):
    content, status_code, message = INVALID[name]
    r = await _upload(client, created_asset["id"], content, issuer_headers)
    assert r.status_code == status_code, r.text
    assert message in r.json()["detail"]
    assert await _counts(async_db) == {"documents": 0, "versions": 0, "pages": 0, "extractions": 0}
    assert _stored_files(tmp_path) == []


async def test_unsupported_file_type_is_rejected(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    r = await _upload(client, created_asset["id"], PROSPECTUS, issuer_headers, filename="prospectus.docx")
    assert r.status_code == 415
    assert (await _counts(async_db))["documents"] == 0


@pytest.mark.parametrize("setting, value, status_code, message", [
    ("max_pdf_pages", 3, 422, "3-page limit"),
    ("max_pdf_text_chars", 1000, 422, "character limit"),
    ("max_upload_size_mb", 0, 413, "MB limit"),
])
async def test_oversized_documents_are_rejected_and_nothing_is_stored(
    setting: str, value: int, status_code: int, message: str, client: AsyncClient, async_db: AsyncSession,
    created_asset: dict, issuer_headers: dict, monkeypatch, tmp_path: Path,
):
    monkeypatch.setattr(settings, setting, value)
    r = await _upload(client, created_asset["id"], PROSPECTUS, issuer_headers)
    assert r.status_code == status_code and message in r.json()["detail"]
    assert await _counts(async_db) == {"documents": 0, "versions": 0, "pages": 0, "extractions": 0}
    assert _stored_files(tmp_path) == []


async def test_invalid_new_version_leaves_the_document_unchanged(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    r = await client.post(f"/documents/{doc['id']}/versions", headers=issuer_headers,
                          files={"file": ("v2.pdf", INVALID["truncated"][0], "application/pdf")})
    assert r.status_code == 422
    versions = (await client.get(f"/documents/{doc['id']}/versions", headers=issuer_headers)).json()
    assert [v["version_number"] for v in versions] == [1]


# ── 9. Tenant isolation ──────────────────────────────────────────────────────


async def test_other_tenant_cannot_reach_the_document_or_its_text(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict,
    other_issuer_headers: dict, other_legal_headers: dict, tmp_path: Path,
):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    missing = "00000000-0000-4000-8000-000000000000"
    for headers in (other_issuer_headers, other_legal_headers):
        for path in ("", "/versions", "/versions/1/text", "/text", "/download", "/extractions"):
            r = await client.get(f"/documents/{doc['id']}{path}", headers=headers)
            absent = await client.get(f"/documents/{missing}{path}", headers=headers)
            assert r.status_code == 404 and r.json() == absent.json(), path   # indistinguishable from missing
            assert "Meridian" not in r.text

    service = _real_service(_grounded_claims())
    r = await _extract(client, doc["id"], other_issuer_headers, service)
    assert r.status_code == 404 and service._client.messages.requests == []
    upload = await client.post(f"/documents/{doc['id']}/versions", headers=other_issuer_headers,
                               files={"file": ("v2.pdf", MIXED, "application/pdf")})
    assert upload.status_code == 404
    assert (await _upload(client, created_asset["id"], MIXED, other_issuer_headers)).status_code == 404
    assert await _counts(async_db) == {"documents": 1, "versions": 1, "pages": 4, "extractions": 0}
    assert len(_stored_files(tmp_path)) == 1


async def test_text_endpoints_require_credentials(client: AsyncClient, created_asset: dict, issuer_headers: dict):
    doc = await _uploaded(client, created_asset["id"], PROSPECTUS, issuer_headers)
    for path in ("/versions/1/text", "/text", "/download"):
        missing = await client.get(f"/documents/{doc['id']}{path}")
        invalid = await client.get(f"/documents/{doc['id']}{path}", headers={"Authorization": "Bearer not-a-token"})
        assert (missing.status_code, invalid.status_code) == (403, 401)   # the project's contract
        assert set(missing.json()) == set(invalid.json()) == {"detail"}


# ── 10. Existing mock-mode compatibility ─────────────────────────────────────


async def test_mock_service_still_runs_on_documents_without_text(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    # The mock AI is not a real provider: it keeps working on a scanned PDF, and its claims are
    # never verified there (the evidence tests of pages without text depend on this)
    doc = await _uploaded(client, created_asset["id"], SCANNED, issuer_headers)
    r = await _extract(client, doc["id"], issuer_headers, MockAIService())
    assert r.status_code == 201, r.text
    assert {v for v in _evidence(r.json()).values() if v != "not_applicable"} == {"no_text_layer"}
    approval = await client.post(f"/extractions/{r.json()['id']}/approvals", headers=issuer_headers, json={})
    assert approval.status_code == 422


async def test_standard_mock_flow_is_unchanged(
    client: AsyncClient, created_document: dict, created_extraction: dict, issuer_headers: dict
):
    assert created_document["current_version"]["text_layer"]["status"] == "complete"
    evidence = {v for v in _evidence(created_extraction).values() if v != "not_applicable"}
    assert evidence == {"verified"}
    approval = await client.post(f"/extractions/{created_extraction['id']}/approvals", headers=issuer_headers, json={})
    assert approval.status_code == 201, approval.text
