"""
Manual legal claims (Stage 10B-3, EVIDENCE_VERIFICATION.md).

An authorized user of the issuer (extraction:run) types legal claims from one uploaded document
version: per claim its legal field (the claim category), the claim, the cited page and the exact
quotation. They are stored as an ordinary extraction record of that version, so every existing
rule applies unchanged: the draft status, the evidence check on every read, corrections, the
approval gate with its evidence, source-integrity and separation-of-duty checks, the approval
binding, the passport and the evidence manifest.

What makes a record manual:
- ai_model is MANUAL_ENTRY_MODEL. No AI service is called, whatever AI_PROVIDER says, and no AI
  service can produce this marker (the AI extraction endpoint refuses a result that carries it);
- the disclaimer is MANUAL_DISCLAIMER;
- the API reports source "manual", and the passport reports value_origin "manual_entry".

Before anything is stored, every quotation must be `verified` on its cited page of exactly the
named version (app/evidence.py, the same check as approvals and corrections): missing,
fabricated, wrong-page and too-short quotations, and pages without a text layer, are refused.
The server never generates, completes or alters a quotation. Legal fields that are not given are
stored as canonical unknown claims: they state nothing, and can later be filled in through a
correction, which has its own evidence check.

Nothing is approved here: the record starts as a draft, its author is recorded in the security
log (extraction.manual_created), and approvals follow the existing review rules.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from app.correction_rules import UNKNOWN_VALUE, _states_unknown
from app.enums import EvidenceStatus, ExtractionSource, LegalFieldName, ManualClaimRefusal
from app.evidence import EvidenceClaim, verify_evidence
from app.evidence_records import ExtractionSourceText
from app.schemas.extraction import ManualClaimInput

# Stored as the extraction's ai_model: names the record for what it is, never an AI model
MANUAL_ENTRY_MODEL = "manual-entry (no AI model)"

# Stored with every manual record (legal_extractions.disclaimer). Like the AI disclaimer, it keeps
# the entered value, the quotation check, reviewer attestation and legal validity apart.
MANUAL_DISCLAIMER = (
    "MANUALLY ENTERED DRAFT — The legal-rights values in this record were typed by a user from "
    "the text of one uploaded document version; no AI model was used. They are not legal advice "
    "and may be incomplete or incorrect. A quotation check confirms only that the quoted text "
    "appears on the cited page of that document version; it does not confirm that the entered "
    "value is correct. Reviewer corrections and approvals are internal attestations, not legal "
    "opinions. None of these establishes legal validity, ownership or enforceability. Do not use "
    "this record for any legal, investment or compliance decision without review by qualified, "
    "independent legal counsel."
)


def extraction_source(ai_model: str) -> ExtractionSource:
    return ExtractionSource.manual if ai_model == MANUAL_ENTRY_MODEL else ExtractionSource.ai_service


@dataclass(frozen=True)
class ManualEvidenceFailure:
    """A manual claim whose quotation is not verified. Carries no page text."""
    field_name: str
    status: EvidenceStatus
    explanation: str


@dataclass(frozen=True)
class ManualClaimProblem:
    refusal: ManualClaimRefusal
    fields: list[str]
    evidence_failures: list[ManualEvidenceFailure]


@dataclass(frozen=True)
class StoredManualClaim:
    field: LegalFieldName
    value: str
    is_unknown: bool
    source_page: int | None
    supporting_passage: str | None


def manual_claim_problem(
    claims: Sequence[ManualClaimInput], version_id: str, source: ExtractionSourceText
) -> ManualClaimProblem | None:
    """
    The first rule the claims break, or None. Every quotation is checked against the page text of
    exactly version_id, as stored now; the request's own statements are never trusted.
    """
    counts = Counter(claim.field_name for claim in claims)
    duplicates = sorted(field.value for field, count in counts.items() if count > 1)
    if duplicates:
        return ManualClaimProblem(ManualClaimRefusal.duplicate_field, duplicates, [])
    # The same rule as corrections: a known claim cannot state "unknown"
    unknown = sorted(claim.field_name.value for claim in claims if _states_unknown(claim.value))
    if unknown:
        return ManualClaimProblem(ManualClaimRefusal.unknown_value, unknown, [])

    failures = []
    for claim in sorted(claims, key=lambda c: c.field_name.value):
        check = verify_evidence(
            EvidenceClaim(version_id, claim.source_page, claim.supporting_passage, False), source.text
        )
        if check.status is not EvidenceStatus.verified:
            failures.append(ManualEvidenceFailure(claim.field_name.value, check.status, check.explanation))
    if failures:
        return ManualClaimProblem(
            ManualClaimRefusal.evidence_not_verified, [f.field_name for f in failures], failures
        )
    return None


def complete_claim_set(claims: Sequence[ManualClaimInput]) -> list[StoredManualClaim]:
    """One claim per legal field, in LegalFieldName order; the fields not given are unknown."""
    given = {claim.field_name: claim for claim in claims}
    completed = []
    for field in LegalFieldName:
        claim = given.get(field)
        if claim is None:
            completed.append(StoredManualClaim(field, UNKNOWN_VALUE, True, None, None))
        else:
            # Stored exactly as entered: the server never rewrites a claim or its quotation
            completed.append(
                StoredManualClaim(field, claim.value, False, claim.source_page, claim.supporting_passage)
            )
    return completed
