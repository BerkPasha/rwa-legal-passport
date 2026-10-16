"""
Stage 9B-4 step 1 — deterministic evidence verification (app/evidence.py).

Isolated unit tests: no database, no HTTP client, no AI call. Coverage:
 1. Valid quotation on the cited page
 2. Fabricated quotation
 3. Incorrect and invalid page numbers
 4. Quotation on another page only
 5. Missing page, missing page records
 6. Missing quotation
 7. Empty PDF text (no text layer)
 8. Short quotation (minimum characters and words)
 9. Unicode normalization (and what is deliberately not normalized)
10. Whitespace normalization
11. Document version mismatch
12. Repeated text and page ambiguity
13. False-positive resistance (token boundaries, negation, numbers, page breaks)
14. Real pypdf text from generated PDFs
15. Structured result, hashes and check order
16. Deterministic repeated execution
17. Randomized invariants (fixed seed)
18. Module purity and stable status codes
"""

import ast
import hashlib
import json
import random
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.enums import EvidenceStatus
from app.evidence import (
    EVIDENCE_SCOPE,
    MIN_QUOTATION_CHARS,
    MIN_QUOTATION_WORDS,
    NORMALIZATION_VERSION,
    EvidenceClaim,
    SourceDocumentText,
    SourcePage,
    count_words,
    normalize_text,
    verify_evidence,
)
from app.services.pdf import PdfLimits, extract_pages
from tests.mock_ai import MockAIService
from tests.pdf_fixtures import make_blank_pdf, make_text_pdf

V1 = "version-0001"
V2 = "version-0002"

PAGE_1 = "This Trust Agreement is governed by the laws of the State of New York, United States of America."
PAGE_2 = "Net rental income shall be distributed quarterly, pro-rata to token holdings."
QUOTE_1 = "governed by the laws of the State of New York"
QUOTE_2 = "Net rental income shall be distributed quarterly"


def source(*texts, version: str = V1) -> SourceDocumentText:
    return SourceDocumentText.from_page_records(
        version, [{"page_number": i + 1, "text_content": t} for i, t in enumerate(texts)]
    )


def check(quotation, page, *texts, version: str = V1, claim_version: str = V1, is_unknown: bool = False):
    claim = EvidenceClaim(document_version_id=claim_version, source_page=page, quotation=quotation,
                          is_unknown=is_unknown)
    return verify_evidence(claim, source(*texts, version=version))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ── 1. Valid quotation on the cited page ─────────────────────────────────────


def test_valid_quotation_on_cited_page_is_verified():
    r = check(QUOTE_1, 1, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.verified
    assert r.verified is True
    assert r.cited_page == 1 and r.page_count == 2
    assert r.found_on_pages == (1,)
    assert r.occurrences_on_cited_page == 1
    assert r.checked_version_id == r.expected_version_id == V1
    assert "presence only, not legal correctness" in r.explanation


def test_second_page_quotation_cited_on_second_page_is_verified():
    r = check(QUOTE_2, 2, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.verified
    assert r.found_on_pages == (2,)


@pytest.mark.parametrize("quotation", [
    "This Trust Agreement is governed",                     # at the start of the page
    "the State of New York, United States of America.",     # at the end, with the full stop
    "the laws of the State of New York",                    # followed by a comma
    "United States of America",                             # followed by a full stop
])
def test_quotations_on_token_boundaries_are_verified(quotation):
    assert check(quotation, 1, PAGE_1).status is EvidenceStatus.verified


def test_quotation_enclosed_in_quotation_marks_is_verified():
    page = 'The "Trust Property" means all assets held by the Trustee.'
    assert check('"Trust Property" means all assets', 1, page).verified
    assert check("all assets held by the Trustee", 1, page).verified


def test_percent_and_currency_values_match_exactly():
    page = "A fixed coupon of 5% per annum is payable on $1,000 units."
    assert check("A fixed coupon of 5% per annum", 1, page).verified
    assert check("is payable on $1,000 units", 1, page).verified


# ── 2. Fabricated quotation ──────────────────────────────────────────────────


def test_fabricated_quotation_is_not_found():
    r = check("Investors hold a fractional beneficial ownership interest", 1, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.not_found
    assert r.verified is False
    assert r.found_on_pages == ()
    assert r.occurrences_on_cited_page == 0


def test_altered_quotation_is_not_found():
    # One word changed: Delaware instead of New York
    r = check("governed by the laws of the State of Delaware", 1, PAGE_1)
    assert r.status is EvidenceStatus.not_found


# ── 3. Incorrect and invalid page numbers ────────────────────────────────────


def test_page_999_of_a_two_page_document_fails():
    r = check(QUOTE_1, 999, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.page_out_of_range
    assert r.verified is False
    assert r.cited_page == 999 and r.page_count == 2
    assert "pages 1 to 2" in r.explanation
    # The quotation exists, so the result says where: the reviewer can correct the page
    assert r.found_on_pages == (1,)
    assert r.cited_page_text_sha256 is None


@pytest.mark.parametrize("page", [0, -1, 3])
def test_pages_outside_the_document_fail(page):
    r = check(QUOTE_1, page, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.page_out_of_range
    assert r.cited_page == page


@pytest.mark.parametrize("page", [True, False, "1", 1.0, [1]])
def test_non_integer_pages_fail(page):
    r = check(QUOTE_1, page, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.page_out_of_range
    assert r.cited_page is None


# ── 4. Quotation on another page only ────────────────────────────────────────


def test_quotation_only_on_page_2_cited_as_page_1_is_not_verified():
    r = check(QUOTE_2, 1, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.found_on_other_page
    assert r.verified is False
    assert r.found_on_pages == (2,)
    assert r.occurrences_on_cited_page == 0
    assert "page 2" in r.explanation


def test_quotation_on_several_other_pages_lists_all_of_them():
    r = check(QUOTE_2, 1, PAGE_1, PAGE_2, PAGE_2)
    assert r.status is EvidenceStatus.found_on_other_page
    assert r.found_on_pages == (2, 3)
    assert "pages 2, 3" in r.explanation


# ── 5. Missing page, missing page records ────────────────────────────────────


def test_missing_page_fails_and_reports_where_the_quotation_is():
    r = check(QUOTE_2, None, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.page_missing
    assert r.verified is False
    assert r.cited_page is None
    assert r.found_on_pages == (2,)


def test_missing_page_with_fabricated_quotation():
    r = check("a passage that the document never contains", None, PAGE_1)
    assert r.status is EvidenceStatus.page_missing
    assert r.found_on_pages == ()


@pytest.mark.parametrize("records, problem", [
    ([(1, PAGE_1), (3, PAGE_2)], "without gaps"),       # page 2 record missing
    ([(2, PAGE_1), (3, PAGE_2)], "without gaps"),       # page 1 record missing
    ([(1, PAGE_1), (1, PAGE_2)], "duplicate"),
    ([(0, PAGE_1), (1, PAGE_2)], "without gaps"),
    ([("1", PAGE_1)], "integers"),
    ([(None, PAGE_1)], "integers"),
    ([(True, PAGE_1)], "integers"),
    ([(1, PAGE_1.encode())], "strings"),
])
def test_invalid_page_records_are_never_used(records, problem):
    src = SourceDocumentText(V1, tuple(SourcePage(n, t) for n, t in records))
    r = verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), src)
    assert r.status is EvidenceStatus.source_text_invalid
    assert r.verified is False
    assert problem in r.explanation


def test_page_records_from_orm_like_rows():
    rows = [SimpleNamespace(page_number=2, text_content=PAGE_2), SimpleNamespace(page_number=1, text_content=PAGE_1)]
    src = SourceDocumentText.from_page_records(V1, rows)
    assert verify_evidence(EvidenceClaim(V1, 2, QUOTE_2), src).verified


# ── 6. Missing quotation ─────────────────────────────────────────────────────


@pytest.mark.parametrize("quotation", [None, "", "   ", "\n\t \r\n", "​­﻿", 42])
def test_missing_quotation_fails(quotation):
    r = check(quotation, 1, PAGE_1)
    assert r.status is EvidenceStatus.quotation_missing
    assert r.verified is False
    assert r.quotation_sha256 is None


# ── 7. Empty PDF text (no text layer) ────────────────────────────────────────


def test_document_without_any_text_is_never_verified():
    r = check(QUOTE_1, 1, "", "")
    assert r.status is EvidenceStatus.no_text_layer
    assert r.verified is False
    assert "OCR is not supported" in r.explanation


@pytest.mark.parametrize("empty", ["", "   ", "\n\n", " ​"])
def test_cited_page_without_text_is_never_verified(empty):
    r = check(QUOTE_1, 1, empty, PAGE_2)
    assert r.status is EvidenceStatus.no_text_layer


def test_missing_page_text_is_a_page_without_text_layer():
    src = SourceDocumentText.from_page_records(V1, [{"page_number": 1, "text_content": None}])
    assert verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), src).status is EvidenceStatus.no_text_layer


def test_empty_cited_page_with_quotation_elsewhere_reports_the_other_page():
    r = check(QUOTE_2, 1, "", PAGE_2)
    assert r.status is EvidenceStatus.found_on_other_page
    assert r.found_on_pages == (2,)


def test_version_without_page_records_is_unavailable():
    r = verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), SourceDocumentText(V1, ()))
    assert r.status is EvidenceStatus.source_text_unavailable
    assert r.page_count == 0


# ── 8. Short quotation ───────────────────────────────────────────────────────


def test_minimum_quotation_rules():
    assert MIN_QUOTATION_CHARS == 20
    assert MIN_QUOTATION_WORDS == 3


def test_quotation_of_exactly_20_characters_and_3_words_qualifies():
    quotation = "Trustee shall decide"
    assert len(quotation) == 20 and count_words(quotation) == 3
    assert check(quotation, 1, "The Trustee shall decide on every transfer.").verified


@pytest.mark.parametrize("quotation, chars, words", [
    ("Trustee may decide.", 19, 3),                     # one character short
    ("indemnification obligations", 27, 2),             # long enough, but two words
    ("Trustee - - - - - - - consent", 29, 2),           # punctuation tokens are not words
    ("  a  b  c  d  e  f  g  ", 13, 7),                 # padding does not count
])
def test_short_quotations_are_rejected(quotation, chars, words):
    page = f"Text containing {quotation} somewhere."
    r = check(quotation, 1, page)
    assert r.status is EvidenceStatus.quotation_too_short
    assert r.verified is False
    assert (r.quotation_chars, r.quotation_words) == (chars, words)


def test_short_quotation_present_in_text_is_still_rejected():
    assert check("New York", 1, PAGE_1).status is EvidenceStatus.quotation_too_short


# ── 9. Unicode normalization ─────────────────────────────────────────────────


@pytest.mark.parametrize("page, quotation", [
    # Ligatures emitted by PDF fonts
    ("the beneﬁcial owner of the ﬂoating rate notes", "the beneficial owner of the floating rate notes"),
    ("the beneficial owner of the floating rate notes", "the beneﬁcial owner of the ﬂoating rate notes"),
    # Typographic quotation marks and apostrophes
    ("the “Trust Property” includes the Trustee’s rights", '"Trust Property" includes the Trustee\'s rights'),
    # En dash, em dash and minus sign
    ("payable on 1–5 March by the Issuer — in arrears", "payable on 1-5 March by the Issuer - in arrears"),
    ("a change of −5% in the reference rate", "a change of -5% in the reference rate"),
    # Composed and decomposed accents (NFC)
    ("the Société Générale acts as custodian", "the Société Générale acts as custodian"),
    # No-break and narrow no-break spaces
    ("EUR 100 000 minimum subscription amount", "EUR 100 000 minimum subscription amount"),
    # Soft hyphen and zero-width space inside words
    ("the agree­ment is bind​ing on all holders", "the agreement is binding on all holders"),
    # Horizontal ellipsis
    ("the following assets… including real estate", "the following assets... including real estate"),
])
def test_unicode_presentation_variants_are_equivalent(page, quotation):
    r = check(quotation, 1, page)
    assert r.status is EvidenceStatus.verified, r.explanation


def test_superscripts_are_not_folded_into_digits():
    page = "interest of 10² basis points per annum"
    # NFKC would make these equal; the verifier deliberately does not
    assert unicodedata.normalize("NFKC", page) == "interest of 102 basis points per annum"
    assert check("interest of 102 basis points per annum", 1, page).status is EvidenceStatus.not_found


def test_full_width_digits_are_not_folded():
    page = "a minimum term of ５ years for holders"
    assert check("a minimum term of 5 years for holders", 1, page).status is EvidenceStatus.not_found


def test_letter_case_is_preserved():
    page = "The Trustee shall approve every transfer of tokens."
    assert check("the trustee shall approve every transfer", 1, page).status is EvidenceStatus.not_found
    assert check("The Trustee shall approve every transfer", 1, page).verified


def test_punctuation_and_digits_are_preserved():
    page = "A fee of 5.0% per annum is payable quarterly."
    assert check("A fee of 50% per annum is payable", 1, page).status is EvidenceStatus.not_found
    assert check("A fee of 5.0 % per annum is payable", 1, page).status is EvidenceStatus.not_found
    assert check("A fee of 5.0% per annum is payable", 1, page).verified


def test_normalization_golden_output():
    raw = "  A b—c ﬁne\n“q” … x­y\tz  "
    assert normalize_text(raw) == 'A b-c fine "q" ... xy z'
    assert NORMALIZATION_VERSION == 1


# ── 10. Whitespace normalization ─────────────────────────────────────────────


@pytest.mark.parametrize("page", [
    "governed by the laws\nof the State of New York",           # pypdf line break
    "governed by the laws\r\nof the State of New York",
    "governed  by\tthe   laws of the\n\nState of New York",
    "governed by the laws of the State of New York",
])
def test_whitespace_variants_in_the_page_are_equivalent(page):
    assert check(QUOTE_1, 1, page).verified


def test_whitespace_in_the_quotation_is_normalized():
    r = check("  governed by the\nlaws of the State  of New York \n", 1, PAGE_1)
    assert r.verified
    assert r.quotation_chars == len(QUOTE_1)


def test_missing_whitespace_inside_a_word_is_not_bridged():
    page = "the Trust shall dis tribute income quarterly"
    assert check("the Trust shall distribute income quarterly", 1, page).status is EvidenceStatus.not_found


def test_line_break_hyphenation_is_not_repaired():
    page = "the agree-\nment is binding on all holders"
    assert check("the agreement is binding on all holders", 1, page).status is EvidenceStatus.not_found


# ── 11. Document version mismatch ────────────────────────────────────────────


def test_text_of_another_version_is_never_used():
    r = check(QUOTE_1, 1, PAGE_1, PAGE_2, version=V2, claim_version=V1)
    assert r.status is EvidenceStatus.document_version_mismatch
    assert r.verified is False
    assert (r.expected_version_id, r.checked_version_id) == (V1, V2)
    assert r.found_on_pages == ()


def test_same_text_under_the_claims_own_version_is_verified():
    assert check(QUOTE_1, 1, PAGE_1, version=V2, claim_version=V2).verified


def test_quotation_from_version_1_is_not_found_in_version_2():
    v1_text = "Version one: the investor holds fee simple ownership of the property."
    v2_text = "Version two: the investor holds a leasehold interest only."
    quotation = "the investor holds fee simple ownership"
    assert check(quotation, 1, v1_text, version=V1, claim_version=V1).verified
    assert check(quotation, 1, v2_text, version=V2, claim_version=V2).status is EvidenceStatus.not_found


# ── 12. Repeated text and page ambiguity ─────────────────────────────────────


def test_repeated_quotation_on_the_cited_page_is_counted():
    page = f"{QUOTE_2}. Again: {QUOTE_2}."
    r = check(QUOTE_2, 1, page)
    assert r.verified and r.occurrences_on_cited_page == 2


def test_overlapping_occurrences_are_counted():
    page = "token holders token holders token holders"
    r = check("token holders token holders", 1, page)
    assert r.verified and r.occurrences_on_cited_page == 2


def test_quotation_on_cited_and_other_pages_is_verified_and_all_pages_are_reported():
    r = check(QUOTE_2, 1, PAGE_2, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.verified
    assert r.found_on_pages == (1, 3)


def test_quotation_on_other_pages_but_not_the_cited_one_is_ambiguous_and_not_verified():
    r = check(QUOTE_2, 2, PAGE_2, PAGE_1, PAGE_2)
    assert r.status is EvidenceStatus.found_on_other_page
    assert r.found_on_pages == (1, 3)


# ── 13. False-positive resistance ────────────────────────────────────────────


@pytest.mark.parametrize("page, quotation", [
    ("Investors acquire illegal title to the property under this deed.", "legal title to the property"),
    ("These tokens are non-transferable shares of the fund.", "transferable shares of the fund"),
    ("The minimum investment is $1,000,000 per subscriber.", "minimum investment is $1,000"),
    ("The lock-up period ends after 15 years from issuance.", "5 years from issuance"),
    ("Interest accrues at 5.5% per annum on the principal.", "Interest accrues at 5"),
    ("The Trustee and/or the Manager may redeem tokens.", "or the Manager may redeem tokens"),
    ("Transfers are subject to the Trustee's prior written consent.", "Transfers are subject to the Trustee"),
    ("Net income shall be distributed quarterly.", "come shall be distributed quarterly"),
    ("The unlimited liability of the general partner applies.", "limited liability of the general partner"),
])
def test_quotation_never_matches_inside_another_token(page, quotation):
    assert quotation in page  # a naive substring search would accept it
    r = check(quotation, 1, page)
    assert r.status is EvidenceStatus.not_found
    assert r.verified is False


def test_removed_negation_is_not_found():
    page = "Tokens shall not be transferred without the consent of the Trustee."
    assert check("Tokens shall be transferred without the consent", 1, page).status is EvidenceStatus.not_found


def test_quotation_spanning_a_page_break_is_never_matched():
    page_1 = "Net rental income shall be distributed"
    page_2 = "quarterly to all token holders."
    quotation = "shall be distributed quarterly to all token holders"
    # The pages joined together would contain the quotation; the verifier never joins pages
    assert quotation in normalize_text(page_1 + " " + page_2)
    for cited in (1, 2):
        r = check(quotation, cited, page_1, page_2)
        assert r.status is EvidenceStatus.not_found
        assert r.found_on_pages == ()


def test_quotation_longer_than_the_page_is_not_found():
    assert check("Distributions are quarterly and audited annually", 1, "Distributions are quarterly.").status \
        is EvidenceStatus.not_found


def test_unknown_claim_is_not_applicable_and_never_verified():
    r = check("a passage that the document never contains", 999, PAGE_1, is_unknown=True)
    assert r.status is EvidenceStatus.not_applicable
    assert r.verified is False


# ── 14. Real pypdf text from generated PDFs ──────────────────────────────────

_LIMITS = PdfLimits(max_pages=500, max_text_chars=2_000_000, max_parse_seconds=30.0)


def _pdf_source(pdf: bytes, version: str = V1) -> SourceDocumentText:
    return SourceDocumentText.from_page_records(version, extract_pages(pdf, _LIMITS))


def test_extracted_two_page_pdf():
    src = _pdf_source(make_text_pdf([
        ["This Trust Agreement is governed by the laws", "of the State of New York, United States of America."],
        ["Net rental income shall be distributed quarterly,", "pro-rata to token holdings."],
    ]))
    assert len(src.pages) == 2
    # pypdf joins the two lines with a line break; whitespace normalization handles it
    assert "\n" in src.pages[0].text
    assert verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), src).verified
    assert verify_evidence(EvidenceClaim(V1, 2, "distributed quarterly, pro-rata to token holdings"), src).verified
    assert verify_evidence(EvidenceClaim(V1, 999, QUOTE_1), src).status is EvidenceStatus.page_out_of_range
    assert verify_evidence(EvidenceClaim(V1, 1, QUOTE_2), src).status is EvidenceStatus.found_on_other_page


async def test_mock_ai_quotations_on_a_blank_pdf_are_never_verified():
    src = _pdf_source(make_blank_pdf(2))
    result = await MockAIService().extract_legal_rights("doc", V1, [])
    known = [c for c in result.claims if not c.is_unknown]
    assert known
    for c in known:
        r = verify_evidence(EvidenceClaim(V1, c.source.page_number, c.source.supporting_passage), src)
        assert r.status is EvidenceStatus.no_text_layer


async def test_mock_ai_quotations_absent_from_the_text_are_not_found():
    src = _pdf_source(make_text_pdf([["The Fund invests in commercial real estate in Ohio."]]))
    result = await MockAIService().extract_legal_rights("doc", V1, [])
    for c in (c for c in result.claims if not c.is_unknown):
        r = verify_evidence(EvidenceClaim(V1, c.source.page_number, c.source.supporting_passage), src)
        assert r.status is EvidenceStatus.not_found


# ── 15. Structured result, hashes and check order ────────────────────────────


def _sample_results():
    return [
        check(QUOTE_1, 1, PAGE_1, PAGE_2),                       # verified
        check("a passage that the document never contains", 1, PAGE_1),
        check(QUOTE_2, 1, PAGE_1, PAGE_2),
        check(QUOTE_1, None, PAGE_1),
        check(QUOTE_1, 999, PAGE_1, PAGE_2),
        check(QUOTE_1, 1, ""),
        check(None, 1, PAGE_1),
        check("New York", 1, PAGE_1),
        check(QUOTE_1, 1, PAGE_1, version=V2),
        verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), SourceDocumentText(V1, ())),
        verify_evidence(EvidenceClaim(V1, 1, QUOTE_1), SourceDocumentText(V1, (SourcePage(1, PAGE_1),) * 2)),
        check(QUOTE_1, 1, PAGE_1, is_unknown=True),
    ]


def test_every_status_is_reachable_and_only_verified_is_verified():
    results = _sample_results()
    assert {r.status for r in results} == set(EvidenceStatus)
    for r in results:
        assert r.verified is (r.status is EvidenceStatus.verified)
        assert r.explanation and r.explanation.isascii()
        assert r.scope == EVIDENCE_SCOPE == "source_text_presence_only"
        assert r.normalization_version == NORMALIZATION_VERSION


def test_result_dictionary_is_machine_readable():
    d = check(QUOTE_2, 1, PAGE_1, PAGE_2).to_dict()
    assert json.loads(json.dumps(d)) == d
    assert d["status"] == "found_on_other_page" and d["verified"] is False
    assert d["found_on_pages"] == [2]
    assert list(d) == [
        "status", "verified", "explanation", "expected_version_id", "checked_version_id", "cited_page",
        "page_count", "found_on_pages", "occurrences_on_cited_page", "quotation_chars", "quotation_words",
        "quotation_sha256", "cited_page_text_sha256", "source_text_sha256", "normalization_version",
        "method", "scope",
    ]


def test_hashes_identify_the_exact_text_that_was_checked():
    r = check("  governed by the\nlaws of the State of New York ", 1, PAGE_1, PAGE_2)
    assert r.quotation_sha256 == sha256(QUOTE_1)               # the normalized quotation
    assert r.cited_page_text_sha256 == sha256(PAGE_1)          # the raw stored page text
    assert r.source_text_sha256 == check(QUOTE_1, 1, PAGE_1, PAGE_2).source_text_sha256


def test_any_change_to_any_page_changes_the_source_hash():
    base = check(QUOTE_1, 1, PAGE_1, PAGE_2).source_text_sha256
    assert check(QUOTE_1, 1, PAGE_1, PAGE_2 + " ").source_text_sha256 != base   # uncited page changed
    assert check(QUOTE_1, 1, PAGE_1, PAGE_2.replace("quarterly", "annually")).source_text_sha256 != base


@pytest.mark.parametrize("quotation, page, expected", [
    (None, None, EvidenceStatus.quotation_missing),             # quotation is checked before the page
    ("New York", 999, EvidenceStatus.quotation_too_short),
    ("a passage that the document never contains", 999, EvidenceStatus.page_out_of_range),
])
def test_checks_run_in_a_fixed_order(quotation, page, expected):
    assert check(quotation, page, PAGE_1, PAGE_2).status is expected


def test_version_mismatch_takes_precedence_over_matching():
    r = check(QUOTE_1, 1, PAGE_1, version=V2, claim_version=V1)
    assert r.status is EvidenceStatus.document_version_mismatch
    assert r.source_text_sha256 is None


# ── 16. Deterministic repeated execution ─────────────────────────────────────


def test_repeated_execution_gives_identical_results():
    first = [r.to_dict() for r in _sample_results()]
    for _ in range(50):
        assert [r.to_dict() for r in _sample_results()] == first


def test_page_order_of_the_input_does_not_matter():
    records = [{"page_number": n, "text_content": t} for n, t in ((1, PAGE_1), (2, PAGE_2), (3, "Page three text."))]
    forward = SourceDocumentText.from_page_records(V1, records)
    backward = SourceDocumentText.from_page_records(V1, list(reversed(records)))
    for claim in (EvidenceClaim(V1, 2, QUOTE_2), EvidenceClaim(V1, 1, QUOTE_2), EvidenceClaim(V1, 9, QUOTE_1)):
        assert verify_evidence(claim, forward).to_dict() == verify_evidence(claim, backward).to_dict()


def test_inputs_are_not_modified():
    src = source(PAGE_1, PAGE_2)
    pages_before = src.pages
    claim = EvidenceClaim(V1, 1, QUOTE_1)
    verify_evidence(claim, src)
    assert src.pages == pages_before and claim == EvidenceClaim(V1, 1, QUOTE_1)


# ── 17. Randomized invariants (fixed seed) ───────────────────────────────────

_VOCABULARY = ["trustee", "holder", "income", "quarterly", "transfer", "consent", "asset", "fund",
               "clause", "notice", "redemption", "title", "Issuer", "Agreement", "5%", "2030"]
_FOREIGN = ["meteor", "violin", "glacier", "orchid", "harbor", "lantern", "saffron", "pebble"]


def _random_pages(rng: random.Random) -> list[str]:
    pages = []
    for _ in range(3):
        lines = [" ".join(rng.choice(_VOCABULARY) for _ in range(rng.randint(5, 9))) for _ in range(6)]
        pages.append("\n".join(lines))
    return pages


def test_randomized_quotations_obey_the_invariants():
    rng = random.Random(9_204)
    for _ in range(300):
        pages = _random_pages(rng)
        k = rng.randint(1, 3)
        words = pages[k - 1].split()
        start = rng.randint(0, len(words) - 6)
        quotation = " ".join(words[start:start + rng.randint(4, 6)])
        if len(quotation) < MIN_QUOTATION_CHARS:
            continue
        # A whole-word window of the cited page is always verified on that page
        assert check(quotation, k, *pages).verified
        # Whatever page is cited, "verified" implies the normalized quotation is on that page
        for cited in (1, 2, 3):
            r = check(quotation, cited, *pages)
            if r.verified:
                assert normalize_text(quotation) in normalize_text(pages[cited - 1])
            assert k in r.found_on_pages
        # Words that never occur in the document are never verified anywhere
        fabricated = " ".join(rng.choice(_FOREIGN) for _ in range(5))
        assert check(fabricated, k, *pages).status is EvidenceStatus.not_found


# ── 18. Module purity and stable status codes ────────────────────────────────


def test_evidence_module_is_pure():
    tree = ast.parse(Path(__file__).resolve().parents[1].joinpath("app", "evidence.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module)
    assert imported <= {"hashlib", "json", "unicodedata", "collections.abc", "dataclasses", "functools",
                        "typing", "app.enums"}


def test_status_codes_are_stable():
    assert [s.value for s in EvidenceStatus] == [
        "verified", "not_found", "found_on_other_page", "page_missing", "page_out_of_range", "no_text_layer",
        "quotation_missing", "quotation_too_short", "document_version_mismatch", "source_text_unavailable",
        "source_text_invalid", "not_applicable",
    ]
