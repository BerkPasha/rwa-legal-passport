"""
Deterministic source-evidence verification (Stage 9B-4, step 1).

Checks whether a claim's quotation (supporting_passage) appears on the cited page of the
extracted text of one specific document version. This is a pure module: no database, file,
network or AI access. The same input always gives the same result.

A positive result proves source-text presence only. It never establishes that the claim's
value or legal interpretation is correct; that remains a human review decision.

Matching rules:
- Exact substring matching on normalized text, one page at a time. The text of different
  pages is never combined, so a quotation spanning a page break never matches.
- A match must start and end on token boundaries: "legal title" does not match inside
  "illegal title", "transferable" not inside "non-transferable", "$1,000" not inside
  "$1,000,000".
- Letter case, punctuation and digits are preserved: no case folding, no fuzzy matching.
- A quotation qualifies only with at least MIN_QUOTATION_CHARS normalized characters and
  MIN_QUOTATION_WORDS words.
- A page without extractable text (e.g. a scanned page) can never be verified; OCR is not
  supported.

Normalization (NORMALIZATION_VERSION 1), applied to the page text and the quotation alike:
1. Unicode NFC (canonical composition only).
2. A fixed table of typographic equivalents: ligatures, quotation marks, dashes, ellipsis.
3. Removal of invisible formatting characters: soft hyphen, zero-width space, word joiner, BOM.
4. Every run of whitespace becomes one space; leading and trailing whitespace is removed.
NFKC is deliberately not used: it folds superscripts, fractions and other compatibility
characters into digits and letters (a superscript two after "10" would become "102").
Any change to these rules must increment NORMALIZATION_VERSION.

Used through app/evidence_records.py by the extraction API, the reviewer correction rules
(app/correction_rules.py), the approval gate and binding, and the passport (Stage 9B-4, steps
2-7); the version text endpoint uses SourceDocumentText for its page-text digest (step 8).
"""

import hashlib
import json
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from functools import cached_property
from typing import Any

from app.enums import EvidenceStatus

NORMALIZATION_VERSION = 1
MATCH_METHOD = "exact_substring_token_boundary"
# What a positive result means: the quotation is present in the source text, nothing more
EVIDENCE_SCOPE = "source_text_presence_only"
MIN_QUOTATION_CHARS = 20
MIN_QUOTATION_WORDS = 3

# Presentation variants mapped to one canonical form. Kept deliberately small: every entry
# is a typographic variant that never changes the meaning of the text.
_EQUIVALENTS = {
    # Ligatures (PDF fonts often emit these instead of the letter sequences)
    "ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
    "ﬅ": "st", "ﬆ": "st",
    # Single quotation marks and apostrophes
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "ʼ": "'",
    # Double quotation marks
    "“": '"', "”": '"', "„": '"', "‟": '"',
    # Hyphens, dashes and the minus sign
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-",
    "−": "-", "﹘": "-", "﹣": "-", "－": "-",
    # Horizontal ellipsis
    "…": "...",
}
# Invisible formatting characters: soft hyphen, zero-width space, word joiner, byte order mark.
# Removed rather than replaced by a space, so that they can never split one word into two.
_INVISIBLE = ("­", "​", "⁠", "﻿")
_TRANSLATION = str.maketrans({**_EQUIVALENTS, **{char: None for char in _INVISIBLE}})

# Characters that join their neighbours into one token
_WORD_JOINERS = frozenset("-'/")   # non-transferable, Trustee's, and/or
_NUMBER_JOINERS = frozenset(".,")  # 1,000,000 and 5.5 (between digits only)


# ── Normalization ────────────────────────────────────────────────────────────


def normalize_text(text: str) -> str:
    """Normalizes text for evidence matching (rules in the module docstring); deterministic."""
    normalized = unicodedata.normalize("NFC", text).translate(_TRANSLATION)
    return " ".join(normalized.split())


def count_words(normalized: str) -> int:
    """Number of space-separated tokens that contain at least one letter or digit."""
    return sum(1 for token in normalized.split(" ") if any(char.isalnum() for char in token))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_page_number(value: Any) -> bool:
    # bool is a subclass of int; True must never be read as page 1
    return isinstance(value, int) and not isinstance(value, bool)


# ── Token-boundary matching ──────────────────────────────────────────────────


def _joined(left: str, joiner: str, right: str) -> bool:
    if joiner in _WORD_JOINERS:
        return left.isalnum() and right.isalnum()
    if joiner in _NUMBER_JOINERS:
        return left.isdigit() and right.isdigit()
    return False


def _starts_inside_token(page: str, start: int, quotation: str) -> bool:
    if start == 0:
        return False
    before, first = page[start - 1], quotation[0]
    if before.isalnum() and first.isalnum():
        return True
    if start >= 2 and _joined(page[start - 2], before, first):
        return True
    return len(quotation) >= 2 and _joined(before, first, quotation[1])


def _ends_inside_token(page: str, end: int, quotation: str) -> bool:
    if end >= len(page):
        return False
    after, last = page[end], quotation[-1]
    if after.isalnum() and last.isalnum():
        return True
    if end + 1 < len(page) and _joined(last, after, page[end + 1]):
        return True
    return len(quotation) >= 2 and _joined(quotation[-2], last, after)


def _count_matches(page: str, quotation: str, limit: int | None = None) -> int:
    """Occurrences of the quotation in the page that lie on token boundaries (overlaps included)."""
    count = 0
    start = page.find(quotation)
    while start != -1:
        end = start + len(quotation)
        if not _starts_inside_token(page, start, quotation) and not _ends_inside_token(page, end, quotation):
            count += 1
            if limit is not None and count >= limit:
                break
        start = page.find(quotation, start + 1)
    return count


# ── Input and result types ───────────────────────────────────────────────────


@dataclass(frozen=True)
class SourcePage:
    """Extracted text of one page, exactly as stored ("" if the page has no text layer)."""
    page_number: int
    text: str


@dataclass(frozen=True)
class SourceDocumentText:
    """The extracted page texts of exactly one document version."""
    document_version_id: str
    pages: tuple[SourcePage, ...]

    @classmethod
    def from_page_records(cls, document_version_id: str, records: Iterable[Any]) -> "SourceDocumentText":
        """
        Builds the source from {"page_number", "text_content"} mappings (the format of
        app/services/pdf.py) or from objects with those attributes (DocumentPage rows).
        A missing (None) text is treated as a page without a text layer.
        """
        pages = []
        for record in records:
            if isinstance(record, Mapping):
                number, text = record.get("page_number"), record.get("text_content")
            else:
                number, text = getattr(record, "page_number", None), getattr(record, "text_content", None)
            pages.append(SourcePage(page_number=number, text="" if text is None else text))
        return cls(document_version_id=document_version_id, pages=tuple(pages))

    @cached_property
    def problem(self) -> str | None:
        """Why the page records cannot be used for verification, or None if they are valid."""
        numbers = [page.page_number for page in self.pages]
        if not all(_is_page_number(number) for number in numbers):
            return "page numbers must be integers"
        if not all(isinstance(page.text, str) for page in self.pages):
            return "page texts must be strings"
        if len(set(numbers)) != len(numbers):
            return "duplicate page numbers"
        if sorted(numbers) != list(range(1, len(numbers) + 1)):
            return "page numbers must run from 1 without gaps"
        return None

    @cached_property
    def _texts(self) -> dict[int, str]:
        return {page.page_number: page.text for page in sorted(self.pages, key=lambda p: p.page_number)}

    @cached_property
    def normalized_pages(self) -> dict[int, str]:
        """Normalized text per page number, in page order (valid sources only)."""
        return {number: normalize_text(text) for number, text in self._texts.items()}

    @cached_property
    def text_sha256(self) -> str:
        """SHA-256 over all page numbers and raw texts in page order (valid sources only)."""
        canonical = json.dumps(
            [[number, text] for number, text in self._texts.items()],
            ensure_ascii=False, separators=(",", ":"),
        )
        return _sha256(canonical)

    def page_text(self, page_number: int) -> str:
        return self._texts[page_number]


@dataclass(frozen=True)
class EvidenceClaim:
    """The evidence a claim cites. Nothing here is trusted; every field is validated."""
    document_version_id: str     # the document version the claim (its extraction) belongs to
    source_page: Any             # cited page number
    quotation: Any               # supporting_passage
    is_unknown: bool = False


@dataclass(frozen=True)
class EvidenceVerification:
    """Structured, machine-readable result of one verification."""
    status: EvidenceStatus
    explanation: str
    expected_version_id: str            # version the claim belongs to
    checked_version_id: str             # version whose page text was supplied
    cited_page: int | None              # the cited page if it is an integer, even when out of range
    page_count: int
    found_on_pages: tuple[int, ...] = ()        # every page on which the quotation appears
    occurrences_on_cited_page: int = 0
    quotation_chars: int = 0                    # after normalization
    quotation_words: int = 0
    quotation_sha256: str | None = None         # of the normalized quotation
    cited_page_text_sha256: str | None = None   # of the raw stored text of the cited page
    source_text_sha256: str | None = None       # of all raw page texts of the checked version
    normalization_version: int = NORMALIZATION_VERSION
    method: str = MATCH_METHOD
    scope: str = EVIDENCE_SCOPE

    @property
    def verified(self) -> bool:
        return self.status is EvidenceStatus.verified

    def to_dict(self) -> dict:
        """JSON-compatible representation with a fixed key order."""
        return {
            "status": self.status.value,
            "verified": self.verified,
            "explanation": self.explanation,
            "expected_version_id": self.expected_version_id,
            "checked_version_id": self.checked_version_id,
            "cited_page": self.cited_page,
            "page_count": self.page_count,
            "found_on_pages": list(self.found_on_pages),
            "occurrences_on_cited_page": self.occurrences_on_cited_page,
            "quotation_chars": self.quotation_chars,
            "quotation_words": self.quotation_words,
            "quotation_sha256": self.quotation_sha256,
            "cited_page_text_sha256": self.cited_page_text_sha256,
            "source_text_sha256": self.source_text_sha256,
            "normalization_version": self.normalization_version,
            "method": self.method,
            "scope": self.scope,
        }


# ── Verification ─────────────────────────────────────────────────────────────


def _pages_phrase(pages: tuple[int, ...]) -> str:
    return ("page " if len(pages) == 1 else "pages ") + ", ".join(str(n) for n in pages)


def _found_elsewhere_note(pages: tuple[int, ...]) -> str:
    return f" The quotation appears on {_pages_phrase(pages)}." if pages else ""


def verify_evidence(claim: EvidenceClaim, source: SourceDocumentText) -> EvidenceVerification:
    """
    Verifies the claim's quotation against the cited page of the given document version.
    The checks run in a fixed order and the first failing check determines the status:
    not_applicable, document_version_mismatch, source_text_invalid, source_text_unavailable,
    quotation_missing, quotation_too_short, page_missing, page_out_of_range, then the match
    itself: verified, found_on_other_page, no_text_layer or not_found.
    """
    page = claim.source_page
    page_count = len(source.pages)
    context: dict[str, Any] = {
        "expected_version_id": claim.document_version_id,
        "checked_version_id": source.document_version_id,
        "cited_page": page if _is_page_number(page) else None,
        "page_count": page_count,
    }

    def result(status: EvidenceStatus, explanation: str) -> EvidenceVerification:
        return EvidenceVerification(status=status, explanation=explanation, **context)

    if claim.is_unknown:
        return result(EvidenceStatus.not_applicable,
                      "The claim is marked unknown; no source evidence is required.")
    if claim.document_version_id != source.document_version_id:
        return result(EvidenceStatus.document_version_mismatch,
                      "The supplied page text belongs to another document version than the claim; "
                      "evidence is only checked against the claim's own version.")
    if source.problem is not None:
        return result(EvidenceStatus.source_text_invalid,
                      f"The page records of this document version are invalid: {source.problem}.")
    if page_count == 0:
        return result(EvidenceStatus.source_text_unavailable,
                      "No extracted page text exists for this document version.")
    context["source_text_sha256"] = source.text_sha256

    quotation = normalize_text(claim.quotation) if isinstance(claim.quotation, str) else ""
    if not quotation:
        return result(EvidenceStatus.quotation_missing, "No supporting quotation is given.")
    chars, words = len(quotation), count_words(quotation)
    context.update(quotation_chars=chars, quotation_words=words, quotation_sha256=_sha256(quotation))
    if chars < MIN_QUOTATION_CHARS or words < MIN_QUOTATION_WORDS:
        return result(EvidenceStatus.quotation_too_short,
                      f"The quotation has {chars} characters and {words} words after normalization; "
                      f"at least {MIN_QUOTATION_CHARS} characters and {MIN_QUOTATION_WORDS} words "
                      "are required.")

    found = tuple(
        number for number, text in source.normalized_pages.items()
        if _count_matches(text, quotation, limit=1)
    )
    context["found_on_pages"] = found
    if page is None:
        return result(EvidenceStatus.page_missing,
                      "No source page is cited." + _found_elsewhere_note(found))
    if not _is_page_number(page) or not 1 <= page <= page_count:
        return result(EvidenceStatus.page_out_of_range,
                      f"The cited page {page!r} is not a page of this document version "
                      f"(pages 1 to {page_count})." + _found_elsewhere_note(found))

    context["cited_page_text_sha256"] = _sha256(source.page_text(page))
    occurrences = _count_matches(source.normalized_pages[page], quotation)
    context["occurrences_on_cited_page"] = occurrences
    if occurrences:
        return result(EvidenceStatus.verified,
                      f"The quotation appears on the cited page {page}. This proves source-text "
                      "presence only, not legal correctness.")
    if found:
        return result(EvidenceStatus.found_on_other_page,
                      f"The quotation does not appear on the cited page {page}; it appears on "
                      f"{_pages_phrase(found)}.")
    if not source.normalized_pages[page]:
        return result(EvidenceStatus.no_text_layer,
                      f"The cited page {page} has no extractable text, so its evidence cannot be "
                      "verified (OCR is not supported).")
    return result(EvidenceStatus.not_found,
                  "The quotation does not appear on any page of this document version.")
