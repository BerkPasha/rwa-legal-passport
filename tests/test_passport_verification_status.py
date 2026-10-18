"""
Stage 9B-5B step 1 — truthful verification metadata (mitigation of issue B).

Five things are kept apart: issuer attestation, legal reviewer approval, source-field
verification, actual institutional independence (never verified by this system) and
institutional approval-policy satisfaction (not implemented). verification_status reports
source-field coverage regardless of approval roles; issuer_attestation_recorded and
legal_reviewer_approval_recorded report the current approvals of the selected extractions only.
The legacy verification_level is unchanged and can still mislead when read alone.

Coverage:
 1. Issuer approval present, no verified source fields
 2. Legal reviewer approval only (no issuer approval), no verified source fields
 3. No approvals and no verified fields
 4. Issuer and legal approvals, incomplete source verification
 5. Complete source-field verification
 6. Current versus stale approvals
 7. Multiple extractions and source selection
 8. Changed document or evidence bindings
 9. Cross-tenant isolation
10. No reviewer identity leakage
11. Stable legacy verification_level
12. Passport API compatibility
"""

import itertools

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.routers.passport as passport_router
from app.access_registry import hash_token
from app.approval_integrity import load_extraction_context, summarize_extraction
from app.enums import EvidenceStatus, LegalFieldName, ReviewerAttestationStatus, VerificationLevel
from app.evidence import MATCH_METHOD
from app.main import app
from app.schemas.passport import LegalRightEntry
from tests.conftest import (
    _HARBOR_ASSET,
    INST_KEY_ALPHA,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    MERIDIAN_ISSUER,
    OTHER_LEGAL_TOKEN,
)
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    FABRICATED,
    FEE_SIMPLE_QUOTE,
    JURISDICTION_QUOTE,
    _new_version,
    _passport_url,
    _upload,
    known,
)
from tests.test_evidence_binding import _exec
from tests.test_passport_verification import (
    DISTRIBUTIONS_QUOTE,
    LEGACY_LEVELS,
    LEGACY_TOP_KEYS,
    MOCK_KNOWN,
    STEP_9B5B1_SUMMARY_KEYS,
    STEP_9B5B1_TOP_KEYS,
    SUMMARY_KEYS,
    _approved,
    _extract_exactly,
    _passport,
    _rights,
    _sources_with,
    _two_document_passport,
)

F = LegalFieldName

STATUSES = {"no_source_verified_fields", "partially_source_verified", "all_fields_source_verified"}
ALL_FIELDS = {f.value for f in LegalFieldName}
# Every legal field known, each quotation on its cited page of the standard test document
ALL_FIELDS_VERIFIED = (
    known(F.ownership_nature, 1, "Investors shall hold a fractional beneficial ownership interest",
          value="Fractional beneficial ownership interest."),
    known(F.income_rights, 1, "Net rental income shall be distributed quarterly", value="Quarterly net rental income."),
    known(F.voting_rights, 1, "requires prior written consent of the Trustee", value="Trustee consent required."),
    known(F.redemption_rights, 2, DISTRIBUTIONS_QUOTE, value="Quarterly distribution window."),
    known(F.transfer_restrictions, 1, "Transfer of ownership interests is restricted to accredited investors",
          value="Accredited investors only."),
    known(F.jurisdiction, 1, JURISDICTION_QUOTE, value="State of New York."),
    known(F.insolvency_provisions, 2, FEE_SIMPLE_QUOTE, value="Trustee holds fee simple title."),
)


def _facts(body: dict) -> tuple:
    """(verification_status, issuer_attestation_recorded, legal_reviewer_approval_recorded, verification_level)."""
    summary = body["verification_summary"]
    # The legacy flag is the same fact under its old name, in every response
    assert summary["independent_reviewer_attestation_recorded"] is summary["legal_reviewer_approval_recorded"]
    assert body["verification_status"] in STATUSES
    return (body["verification_status"], summary["issuer_attestation_recorded"],
            summary["legal_reviewer_approval_recorded"], body["verification_level"])


def _headers(names, issuer_headers: dict, legal_headers: dict) -> list[dict]:
    return [{"issuer": issuer_headers, "legal": legal_headers}[n] for n in names]


async def _approval_ids(client: AsyncClient, extraction_id: str, headers: dict) -> dict[str, str]:
    r = await client.get(f"/extractions/{extraction_id}/approvals", headers=headers)
    assert r.status_code == 200, r.text
    return {a["approval_type"]: a["id"] for a in r.json()}


# ── 1, 2. Approvals without any verified source field ────────────────────────


@pytest.mark.parametrize("approvers,issuer,legal", [
    (("issuer",), True, False),
    (("legal",), False, True),
    (("issuer", "legal"), True, True),
], ids=["issuer_only", "legal_only_no_issuer", "issuer_and_legal"])
async def test_no_verified_field_is_reported_whatever_the_approval_roles(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, approvers: tuple, issuer: bool, legal: bool,
):
    ex = await _extract_exactly(client, created_document["id"], issuer_headers)  # every field unknown
    await _approved(client, ex, *_headers(approvers, issuer_headers, legal_headers))

    body = await _passport(client, created_asset, inst_headers)
    assert _facts(body) == ("no_source_verified_fields", issuer, legal, "issuer_attested")
    assert body["verification_summary"]["source_verified_field_count"] == 0
    assert not any(e["verified"] or e["independently_verified"] for e in body["legal_rights"])


async def test_legal_only_approval_never_reports_an_issuer_attestation(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict,
):
    """Issue B: the legacy floor reads issuer_attested although no issuer attestation exists."""
    ex = await _extract_exactly(client, created_document["id"], issuer_headers)
    await _approved(client, ex, legal_headers)

    body = await _passport(client, created_asset, inst_headers)
    assert body["verification_level"] == "issuer_attested"  # legacy behaviour, unchanged
    assert body["verification_summary"]["issuer_attestation_recorded"] is False
    assert body["verification_summary"]["legal_reviewer_approval_recorded"] is True
    assert body["verification_status"] == "no_source_verified_fields"
    assert [a["approval_type"] for a in body["approvals"]] == ["legal_verification"]


# ── 3. No approvals ──────────────────────────────────────────────────────────


async def test_without_approvals_no_passport_and_no_metadata_is_served(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, inst_headers: dict,
):
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 404 and set(r.json()) == {"detail"}
    # A review in progress is not an approval either
    assert (await client.post(f"/extractions/{created_extraction['id']}/review", headers=issuer_headers)).status_code == 200
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 404 and set(r.json()) == {"detail"}
    for name in STEP_9B5B1_TOP_KEYS | STEP_9B5B1_SUMMARY_KEYS:
        assert name not in r.text


def test_derivation_without_sources_or_fields_asserts_nothing():
    assert passport_router._verification_status([]).value == "no_source_verified_fields"
    summary = passport_router._verification_summary([], [])
    assert (summary.issuer_attestation_recorded, summary.legal_reviewer_approval_recorded,
            summary.independent_reviewer_attestation_recorded) == (False, False, False)


# ── 4. Incomplete source verification ────────────────────────────────────────


async def test_unknown_fields_keep_the_status_partial_under_both_approvals(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict,
):
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    body = await _passport(client, created_asset, inst_headers)
    # Every verified field is independently attested, yet three legal fields are unknown
    assert _facts(body) == ("partially_source_verified", True, True, "independently_verified")
    summary = body["verification_summary"]
    assert (summary["source_verified_field_count"], summary["unknown_field_count"]) == (4, 3)
    assert {e["field_name"] for e in body["legal_rights"] if e["verified"]} == MOCK_KNOWN


async def test_a_conflicting_field_keeps_the_status_partial(
    client: AsyncClient, created_asset: dict, created_document: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict,
):
    await _approved(client, await _extract_exactly(client, created_document["id"], issuer_headers,
                                                   *ALL_FIELDS_VERIFIED), issuer_headers, legal_headers)
    other = await _upload(client, created_asset["id"], source_pdf, issuer_headers, title="Opinion")
    differing = [known(F.ownership_nature, 2, FEE_SIMPLE_QUOTE, value="Fee simple title.")
                 if c.field == F.ownership_nature else c for c in ALL_FIELDS_VERIFIED]
    await _approved(client, await _extract_exactly(client, other["id"], issuer_headers, *differing),
                    issuer_headers, legal_headers)

    body = await _passport(client, created_asset, inst_headers)
    assert _rights(body)["ownership_nature"]["conflict"] is True
    assert _facts(body) == ("partially_source_verified", True, True, "independently_verified")
    summary = body["verification_summary"]
    assert (summary["source_verified_field_count"], summary["conflicting_field_count"]) == (6, 1)


@pytest.mark.parametrize("fabricated,expected", [
    (set(), "partially_source_verified"),
    ({"ownership_nature"}, "partially_source_verified"),
    (MOCK_KNOWN, "no_source_verified_fields"),
], ids=["none", "one_known", "every_known"])
async def test_unverified_evidence_under_a_current_approval_is_never_counted(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
    fabricated: set, expected: str,
):
    """A state the API cannot produce (claims altered in memory under a current approval)."""
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    sources = await _sources_with(async_db, created_extraction["id"],
                                  **{name: {"supporting_passage": FABRICATED} for name in fabricated})
    entries = passport_router._legal_rights(sources)
    assert passport_router._verification_status(entries).value == expected
    assert sum(e.verified for e in entries) == len(MOCK_KNOWN) - len(fabricated)


# ── 5. Complete source-field verification ────────────────────────────────────


@pytest.mark.parametrize("approvers,issuer,legal,level", [
    (("issuer",), True, False, "issuer_attested"),
    (("legal",), False, True, "independently_verified"),
    (("issuer", "legal"), True, True, "independently_verified"),
], ids=["issuer_only", "legal_only", "issuer_and_legal"])
async def test_every_field_source_verified_is_complete_whatever_the_approval_roles(
    client: AsyncClient, created_asset: dict, created_document: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, approvers: tuple, issuer: bool, legal: bool, level: str,
):
    ex = await _extract_exactly(client, created_document["id"], issuer_headers, *ALL_FIELDS_VERIFIED)
    await _approved(client, ex, *_headers(approvers, issuer_headers, legal_headers))

    body = await _passport(client, created_asset, inst_headers)
    assert _facts(body) == ("all_fields_source_verified", issuer, legal, level)
    assert {e["field_name"] for e in body["legal_rights"] if e["verified"]} == ALL_FIELDS
    assert all(e["evidence_status"] == "verified" for e in body["legal_rights"])
    summary = body["verification_summary"]
    assert (summary["source_verified_field_count"], summary["unknown_field_count"],
            summary["conflicting_field_count"], summary["unverified_field_count"]) == (7, 0, 0, 0)


async def test_an_absent_legal_field_is_never_complete(
    client: AsyncClient, created_document: dict, issuer_headers: dict, async_db: AsyncSession,
):
    ex = await _extract_exactly(client, created_document["id"], issuer_headers, *ALL_FIELDS_VERIFIED)
    await _approved(client, ex, issuer_headers)
    entries = passport_router._legal_rights(await _sources_with(async_db, ex["id"]))
    assert passport_router._verification_status(entries).value == "all_fields_source_verified"
    # e.g. a legacy extraction without one of the seven fields: verified as far as it goes, not complete
    for missing in range(len(entries)):
        rest = entries[:missing] + entries[missing + 1:]
        assert all(e.verified for e in rest)
        assert passport_router._verification_status(rest).value == "partially_source_verified"
    # Any further field that is not verified (e.g. a non-canonical legacy field) keeps it partial
    for state in ("unknown", "conflict", "unverified"):
        extra = entries + [_entry("legacy_field", state)]
        assert passport_router._verification_status(extra).value == "partially_source_verified"


# ── 6. Current versus stale approvals ────────────────────────────────────────


async def test_stale_approvals_are_never_counted(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    eid = created_extraction["id"]
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    assert _facts(await _passport(client, created_asset, inst_headers))[1:3] == (True, True)

    # A material asset change makes both approvals stale: no passport, no metadata
    assert (await admin_client.patch(f"/assets/{created_asset['id']}", json={"jurisdiction": "KY-CAYMAN"})
            ).status_code == 200
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 409 and set(r.json()) == {"detail"}

    # Re-review and a new issuer attestation only: the stale legal approval is not counted
    assert (await client.post(f"/extractions/{eid}/review", headers=issuer_headers)).status_code == 200
    await _approved(client, created_extraction, issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert _facts(body) == ("partially_source_verified", True, False, "issuer_attested")
    assert [a["approval_type"] for a in body["approvals"]] == ["issuer_attestation"]
    history = (await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)).json()
    stale_legal = [a for a in history if a["approval_type"] == "legal_verification"]
    assert len(stale_legal) == 1 and stale_legal[0]["currency"]["is_current"] is False
    assert "asset_information_changed" in stale_legal[0]["currency"]["reasons"]


# ── 7. Multiple extractions and source selection ─────────────────────────────


async def test_flags_follow_the_approved_extraction_and_a_competitor_cannot_replace_it(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    # Extraction X: a current legal reviewer approval of four verified fields
    await _approved(client, created_extraction, legal_headers)
    # Extraction Y of the same document version: every field unknown; the issuer's attestation of it
    # is refused (Stage 9B-5C, issue B5) instead of silently replacing X
    y = await _extract_exactly(client, created_document["id"], issuer_headers)
    r = await client.post(f"/extractions/{y['id']}/approvals", headers=issuer_headers, json={})
    assert r.status_code == 409 and r.json()["approval_refusal"] == "competing_extraction"

    body = await _passport(client, created_asset, inst_headers)
    assert body["extraction_metadata"]["extraction_id"] == created_extraction["id"]
    (document,) = body["source_documents"]
    assert document["approval_types"] == ["legal_verification"]
    assert not summarize_extraction(await load_extraction_context(async_db, y["id"])).current_approvals
    assert _facts(body) == ("partially_source_verified", False, True, "independently_verified")


async def test_flags_cover_every_selected_document(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers,
                                         legal_headers)
    body = await _passport(client, created_asset, inst_headers)
    by_document = {d["document_id"]: d["approval_types"] for d in body["source_documents"]}
    assert by_document == {created_document["id"]: ["legal_verification"], other["id"]: ["issuer_attestation"]}
    assert _facts(body) == ("partially_source_verified", True, True, "partially_independently_verified")


# ── 8. Changed document or evidence bindings ─────────────────────────────────


async def test_a_superseded_document_version_withdraws_its_approval_flag(
    client: AsyncClient, created_asset: dict, created_document: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, inst_headers: dict,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers,
                                         legal_headers)
    await _new_version(client, created_document["id"], make_source_pdf("Prospectus v2."), issuer_headers)
    body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [other["id"]]
    assert body["excluded_documents"][0]["reasons"] == ["document_version_superseded"]
    assert _facts(body) == ("partially_source_verified", True, False, "issuer_attested")


async def test_changed_evidence_withdraws_its_approval_flag(
    client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes, issuer_headers: dict,
    legal_headers: dict, inst_headers: dict, async_db: AsyncSession,
):
    other = await _two_document_passport(client, created_asset, created_extraction, source_pdf, issuer_headers,
                                         legal_headers)
    await _exec(async_db, "UPDATE legal_claims SET value = 'Tampered value.' "
                          "WHERE extraction_id = :e AND field_name = 'jurisdiction'", e=created_extraction["id"])
    body = await _passport(client, created_asset, inst_headers)
    assert [d["document_id"] for d in body["source_documents"]] == [other["id"]]
    assert "evidence_changed" in body["excluded_documents"][0]["reasons"]
    assert _facts(body) == ("partially_source_verified", True, False, "issuer_attested")


@pytest.mark.parametrize("column,forged", [
    ("approval_type", "legal_verification"),
    ("reviewer_role", "independent_legal_reviewer"),
    ("document_sha256", "0" * 64),
])
async def test_a_tampered_approval_binding_earns_no_flag(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict, async_db: AsyncSession, column: str, forged: str,
):
    eid = created_extraction["id"]
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    issuer_approval = (await _approval_ids(client, eid, issuer_headers))["issuer_attestation"]
    await _exec(async_db, f"UPDATE review_approvals SET {column} = :v WHERE id = :a", v=forged, a=issuer_approval)

    body = await _passport(client, created_asset, inst_headers)
    # The forged record fails its binding: it counts neither as issuer nor as legal reviewer approval
    assert _facts(body) == ("partially_source_verified", False, True, "independently_verified")
    assert [a["approval_type"] for a in body["approvals"]] == ["legal_verification"]


# ── 9. Cross-tenant isolation ────────────────────────────────────────────────


async def test_flags_never_cross_tenants(
    client: AsyncClient, admin_client: AsyncClient, created_asset: dict, created_extraction: dict, source_pdf: bytes,
    issuer_headers: dict, legal_headers: dict, other_issuer_headers: dict, other_legal_headers: dict,
    inst_headers: dict, inst_beta_headers: dict,
):
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    harbor = (await admin_client.post("/assets", json=_HARBOR_ASSET)).json()
    doc = await _upload(client, harbor["id"], source_pdf, other_issuer_headers, title="Bond terms")
    await _approved(client, await _extract_exactly(client, doc["id"], other_issuer_headers), other_legal_headers)

    # Harbor: only its own legal reviewer approval; Meridian's issuer attestation never counts
    assert _facts(await _passport(client, harbor, inst_beta_headers)) == (
        "no_source_verified_fields", False, True, "issuer_attested")
    assert _facts(await _passport(client, created_asset, inst_headers)) == (
        "partially_source_verified", True, True, "independently_verified")
    # Out of scope: the same 404 as a missing asset, carrying no verification metadata
    meridian = await client.get(_passport_url(created_asset), headers=inst_beta_headers)
    missing = await client.get(_passport_url({**created_asset, "stellar_asset_code": "NOPE"}), headers=inst_beta_headers)
    assert meridian.status_code == missing.status_code == 404 and meridian.json() == missing.json()
    assert set(meridian.json()) == {"detail"}


# ── 10. No reviewer identity leakage ─────────────────────────────────────────


def _keys(node) -> set[str]:
    if isinstance(node, dict):
        return set(node) | {k for v in node.values() for k in _keys(v)}
    if isinstance(node, list):
        return {k for v in node for k in _keys(v)}
    return set()


async def test_no_reviewer_identity_or_credential_is_exposed(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
    inst_headers: dict,
):
    eid = created_extraction["id"]
    await _approved(client, created_extraction, issuer_headers, legal_headers)
    approval_ids = (await _approval_ids(client, eid, issuer_headers)).values()
    r = await client.get(_passport_url(created_asset), headers=inst_headers)
    assert r.status_code == 200
    secrets = {"alice-chen-01", "bob-martinez-01", "Alice Chen", "Bob Martinez", "meridian-capital-001",
               MERIDIAN_ISSUER, ISSUER_TOKEN, LEGAL_TOKEN, INST_KEY_ALPHA, OTHER_LEGAL_TOKEN,
               hash_token(ISSUER_TOKEN), hash_token(LEGAL_TOKEN), hash_token(INST_KEY_ALPHA), *approval_ids}
    for secret in secrets:
        assert secret not in r.text, secret
    assert not _keys(r.json()) & {"reviewer_id", "user_id", "display_name", "token", "token_sha256", "issuer_id",
                                  "client_id", "key_sha256", "id"}


# ── 11. Stable legacy verification_level ─────────────────────────────────────


def _legacy_verification_level(entries: list[LegalRightEntry]) -> VerificationLevel:
    """The calculation of the accepted Stage 9B-4 checkpoint (3aa67d6), copied verbatim."""
    verified = [e for e in entries if e.verified]
    if verified and all(e.independently_verified for e in verified):
        return VerificationLevel.independently_verified
    if any(e.independently_verified for e in verified):
        return VerificationLevel.partially_independently_verified
    return VerificationLevel.issuer_attested


def _entry(name: str, state: str) -> LegalRightEntry:
    verified = state in ("verified", "independent")
    return LegalRightEntry(
        field_name=name, value="unknown" if state == "unknown" else "Stated value.", is_unknown=state == "unknown",
        verified=verified, independently_verified=state == "independent", conflict=state == "conflict",
        evidence_status=EvidenceStatus.verified if verified else EvidenceStatus.not_applicable,
        verification_method=MATCH_METHOD,
        reviewer_attestation_status=ReviewerAttestationStatus.not_applicable,
    )


def test_legacy_level_is_unchanged_and_independent_of_the_new_status():
    states = ("unknown", "conflict", "unverified", "verified", "independent")
    names = [f.value for f in LegalFieldName][:3]
    seen = set()
    for combo in itertools.product(states, repeat=len(names)):
        entries = [_entry(n, s) for n, s in zip(names, combo)]
        assert passport_router._verification_level(entries) == _legacy_verification_level(entries), combo
        seen.add((passport_router._verification_level(entries).value,
                  passport_router._verification_status(entries).value))
    # The legacy floor covers both "no verified field" and "verified fields without independent backing"
    assert ("issuer_attested", "no_source_verified_fields") in seen
    assert ("issuer_attested", "partially_source_verified") in seen
    assert {level for level, _ in seen} == LEGACY_LEVELS


# ── 12. Passport API compatibility ───────────────────────────────────────────


async def test_existing_fields_are_unchanged_and_the_new_ones_are_additive(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, inst_headers: dict,
):
    body = await _passport(client, created_asset, inst_headers)
    assert set(body) == LEGACY_TOP_KEYS | {"verification_summary"} | STEP_9B5B1_TOP_KEYS
    assert set(body["verification_summary"]) == SUMMARY_KEYS | STEP_9B5B1_SUMMARY_KEYS
    assert body["verification_level"] == "issuer_attested"
    assert (body["verification_summary"]["reviewer_attestation_recorded"],
            body["verification_summary"]["independent_reviewer_attestation_recorded"]) == (True, False)
    assert _facts(body) == ("partially_source_verified", True, False, "issuer_attested")
    # The request contract is unchanged: no new parameter, header or body
    assert (await client.get(_passport_url(created_asset), headers=inst_headers,
                             params={"verification_status": "x"})).status_code == 200


def test_openapi_documents_the_new_fields_without_new_claims():
    spec = app.openapi()
    schemas = spec["components"]["schemas"]
    assert set(schemas["VerificationLevel"]["enum"]) == LEGACY_LEVELS  # unchanged
    assert set(schemas["SourceVerificationStatus"]["enum"]) == STATUSES
    passport = schemas["LegalPassportResponse"]
    summary = schemas["VerificationSummary"]
    assert "verification_status" in passport["required"]
    assert STEP_9B5B1_SUMMARY_KEYS <= set(summary["required"])
    assert "misleading when read alone" in passport["properties"]["verification_level"]["description"]
    status_text = passport["properties"]["verification_status"]["description"]
    assert "not legal validity" in status_text and "not institutional independence" in status_text
    assert "not approval-policy satisfaction" in status_text
    legal_text = summary["properties"]["legal_reviewer_approval_recorded"]["description"]
    assert "does not prove organizational independence" in legal_text
    assert "not independent" in summary["properties"]["issuer_attestation_recorded"]["description"]
    assert "legal_reviewer_approval_recorded" in summary["properties"][
        "independent_reviewer_attestation_recorded"]["description"]
    operation = spec["paths"]["/passport/{network}/{asset_code}/{issuer_key}"]["get"]
    assert [p["name"] for p in operation["parameters"] if p["in"] != "header"] == ["network", "asset_code", "issuer_key"]
    assert "requestBody" not in operation
    assert "legacy indicator that can mislead" in operation["description"]
