"""
Reviewer correction evidence rules (Stage 9B-4, step 7).

A correction decides the claim's effective value AND its effective evidence. The rules are
deterministic and are applied under the approval write lock, to the claim as stored now:

1. Corrected to unknown: the claim states nothing. Its value becomes "unknown" and its page
   and quotation are cleared. Sending a page or quotation with is_unknown=true is rejected
   (unknown_with_evidence), because silently dropping it would hide a contradiction.
2. Known, value and unknown flag unchanged: an omitted corrected_source_page / corrected_passage
   keeps the current one; a field sent as null clears it.
3. Known, value changed (or an unknown claim made known): the previous page and quotation are
   never reused. Both must be sent for the new value (evidence_required); sending the same
   quotation again is an explicit act of the reviewer, recorded as such.
4. A known claim whose value states "unknown" is contradictory (unknown_value_for_known_claim).
5. The effective quotation of a known claim must be verified on its cited page of the
   extraction's own document version (app/evidence.py), otherwise evidence_not_verified with
   the evidence status (missing, too short, wrong or impossible page, not found, ...).

A verified quotation proves only that the text is on the cited page; whether it supports the
corrected value is the reviewer's attestation, never established by this check. A refused
correction changes nothing.

Optimistic concurrency (Stage 9B-4, issue A): every request states the claim as its reviewer read it
(expected_claim, required). Before the rules above are applied, under the same write lock, every
component of that state is compared with the stored claim; any difference refuses the correction
(409), so an omitted page or quotation never keeps evidence its reviewer did not see.
"""

from dataclasses import dataclass

from app.enums import CorrectionRejection, EvidenceStatus
from app.evidence import EvidenceClaim, verify_evidence
from app.evidence_records import ExtractionSourceText
from app.models.extraction import LegalClaim
from app.schemas.review import ClaimCorrectionRequest, ExpectedClaimState

UNKNOWN_VALUE = "unknown"
_PAGE, _PASSAGE = "corrected_source_page", "corrected_passage"
# The complete evidence state of a claim, in the order reported to the client
CLAIM_STATE_FIELDS = ("value", "is_unknown", "source_page", "supporting_passage")


def changed_since_read(claim: LegalClaim, expected: ExpectedClaimState) -> list[str]:
    """The state fields whose stored value differs from what the reviewer read (empty: unchanged)."""
    stored = {
        "value": claim.value,
        "is_unknown": bool(claim.is_unknown),
        "source_page": claim.source_page,
        "supporting_passage": claim.supporting_passage,
    }
    return [name for name in CLAIM_STATE_FIELDS if stored[name] != getattr(expected, name)]


@dataclass(frozen=True)
class CorrectedClaim:
    """The effective claim after an accepted correction."""
    value: str
    is_unknown: bool
    source_page: int | None
    supporting_passage: str | None


@dataclass(frozen=True)
class RefusedCorrection:
    rule: CorrectionRejection
    evidence_status: EvidenceStatus | None = None


def _states_unknown(value: str) -> bool:
    return value.strip().casefold() in ("", UNKNOWN_VALUE)


def resolve_correction(
    claim: LegalClaim, request: ClaimCorrectionRequest, version_id: str, source: ExtractionSourceText
) -> CorrectedClaim | RefusedCorrection:
    """Applies the rules above; explicit null is told apart from omission via model_fields_set."""
    sent = request.model_fields_set
    page_sent, passage_sent = _PAGE in sent, _PASSAGE in sent

    if request.is_unknown:
        if (page_sent and request.corrected_source_page is not None) or (
            passage_sent and request.corrected_passage is not None
        ):
            return RefusedCorrection(CorrectionRejection.unknown_with_evidence)
        return CorrectedClaim(UNKNOWN_VALUE, True, None, None)

    if _states_unknown(request.corrected_value):
        return RefusedCorrection(CorrectionRejection.unknown_value_for_known_claim)
    value_changed = bool(claim.is_unknown) or request.corrected_value != claim.value
    if value_changed and not (page_sent and passage_sent):
        return RefusedCorrection(CorrectionRejection.evidence_required)

    page = request.corrected_source_page if page_sent else claim.source_page
    passage = request.corrected_passage if passage_sent else claim.supporting_passage
    check = verify_evidence(EvidenceClaim(version_id, page, passage, False), source.text)
    if check.status is not EvidenceStatus.verified:
        return RefusedCorrection(CorrectionRejection.evidence_not_verified, check.status)
    return CorrectedClaim(request.corrected_value, False, page, passage)
