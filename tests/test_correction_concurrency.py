"""
Stage 9B-4 closure, issue A — concurrent correction integrity (optimistic concurrency).

A correction that omits page or quotation keeps the evidence stored when it is processed. Without
a precondition, evidence another reviewer saved in between was inherited silently (issue A). Every
correction must therefore carry `expected_claim`: the claim's value, unknown flag, page and
quotation exactly as the reviewer read them. It is compared with the stored claim under the
database write lock; any difference refuses the correction with 409 and changes nothing.

`expected_claim` is mandatory: a request without it, or with null, is invalid (422) and never
reaches the claim, so no client can inherit evidence it did not see.

Coverage:
 1. Legacy requests without a precondition are rejected (they can no longer inherit)
 2. Two reviewers read the same claim, one updates its evidence, the other's stale request → 409
 3. A refused request changes nothing (claims, corrections, approvals, audit history)
 4. A matching precondition is accepted; after reloading, the reviewer can correct
 5. Every state component is compared, for every kind of correction
 6. Invalid preconditions are rejected
 7. Approval and audit integrity after a refused stale request
 8. Cross-tenant isolation: a precondition reveals nothing
 9. Backward compatibility (legacy requests, OpenAPI)
10. Parallel reviewers never preserve evidence they did not see
"""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import LegalFieldName
from app.main import app
from tests.conftest import _MERIDIAN_ASSET
from tests.mock_ai import MOCK_SOURCE_PAGES
from tests.pdf_fixtures import make_source_pdf
from tests.test_approval_evidence_gate import (
    FEE_SIMPLE_QUOTE,
    OWNERSHIP_QUOTE,
    _approve,
    _extract,
    _run_before_lock,
    _snapshot,
    _upload,
    file_db,  # noqa: F401 — pytest fixture
)
from tests.test_evidence_binding import _oracle_check

F = LegalFieldName
STATE = ("value", "is_unknown", "source_page", "supporting_passage")
NEW_VALUE = "Fee simple title held by the Trustee."
INCOME_PASSAGE = MOCK_SOURCE_PAGES[0][2]
CLAIM_CHANGED = "claim_changed"


def _claim(extraction: dict, field: LegalFieldName) -> dict:
    return next(c for c in extraction["claims"] if c["field_name"] == field.value)


def observed(claim: dict) -> dict:
    """The precondition a client sends: the claim exactly as it read it."""
    return {key: claim[key] for key in STATE}


async def _patch(client: AsyncClient, extraction: dict, field: LegalFieldName, headers: dict, **body):
    """A correction stating the claim as read in `extraction`, unless the test passes its own precondition."""
    claim = _claim(extraction, field)
    body.setdefault("expected_claim", observed(claim))
    return await client.patch(f"/extractions/{extraction['id']}/claims/{claim['id']}", headers=headers, json=body)


async def _legacy_patch(client: AsyncClient, extraction: dict, field: LegalFieldName, headers: dict, **body):
    """A request as a pre-precondition client sends it: exactly the given body."""
    claim = _claim(extraction, field)
    return await client.patch(f"/extractions/{extraction['id']}/claims/{claim['id']}", headers=headers, json=body)


async def _read(client: AsyncClient, extraction_id: str, headers: dict) -> dict:
    r = await client.get(f"/extractions/{extraction_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _assert_conflict(r, fields: list[str]) -> None:
    assert r.status_code == 409, r.text
    body = r.json()
    assert set(body) == {"detail", "correction_conflict", "changed_fields"}
    assert body["correction_conflict"] == CLAIM_CHANGED and body["changed_fields"] == fields
    assert body["detail"].startswith("Correction rejected — the claim changed after it was read")


async def _db_snapshot(factory) -> dict:
    async with factory() as s:
        return await _snapshot(s)


async def _fresh(client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict) -> dict:
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    return await _extract(client, doc["id"], issuer_headers)


# Reviewer 2 keeps the value but replaces the evidence with another authentic quotation (page 2)
NEW_EVIDENCE = {"corrected_source_page": 2, "corrected_passage": FEE_SIMPLE_QUOTE}


# ── 1. Legacy requests ───────────────────────────────────────────────────────


@pytest.mark.parametrize("precondition", ["missing", "null"])
async def test_a_legacy_request_without_a_precondition_is_rejected_and_inherits_nothing(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
    precondition: str,
):
    """
    The issue A scenario with a legacy client. Before the precondition was mandatory, this request
    was accepted and kept evidence its reviewer never saw (reproduced before the fix; see
    PRODUCT_READINESS.md §12.8). It is now invalid and never reaches the claim.
    """
    ex = await _fresh(client, admin_client, issuer_headers)
    seen = _claim(ex, F.ownership_nature)  # reviewer 1 reads page 1, the ownership quotation
    # Reviewer 2 replaces the evidence after reviewer 1 read the claim
    assert (await _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                         is_unknown=False, **NEW_EVIDENCE)).status_code == 200
    before = await _db_snapshot(file_db)
    lock_calls = _run_before_lock(monkeypatch, lambda: asyncio.sleep(0))
    body = {"corrected_value": seen["value"], "is_unknown": False}
    if precondition == "null":
        body["expected_claim"] = None
    r = await _legacy_patch(client, ex, F.ownership_nature, issuer_headers, **body)
    assert r.status_code == 422, r.text
    assert any(error["loc"][-1] == "expected_claim" for error in r.json()["detail"])
    assert lock_calls == []  # refused by validation, before the claim is read or locked
    assert await _db_snapshot(file_db) == before


# ── 2, 3. Stale preservation refused, nothing changed ───────────────────────


async def test_a_stale_evidence_preserving_request_is_refused_and_changes_nothing(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    ex = await _fresh(client, admin_client, issuer_headers)
    seen = _claim(ex, F.ownership_nature)
    after_other: dict = {}

    async def reviewer_2():
        r = await _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                         is_unknown=False, expected_claim=observed(seen), **NEW_EVIDENCE)
        after_other["snapshot"] = await _db_snapshot(file_db)
        return r

    other = _run_before_lock(monkeypatch, reviewer_2)
    r = await asyncio.wait_for(_patch(client, ex, F.ownership_nature, issuer_headers, corrected_value=seen["value"],
                                      is_unknown=False, expected_claim=observed(seen)), 30)
    assert [o.status_code for o in other] == [200]
    _assert_conflict(r, ["source_page", "supporting_passage"])
    assert await _db_snapshot(file_db) == after_other["snapshot"]
    current = _claim(await _read(client, ex["id"], issuer_headers), F.ownership_nature)
    assert (current["source_page"], current["supporting_passage"]) == (2, FEE_SIMPLE_QUOTE)
    history = (await client.get(f"/extractions/{ex['id']}/corrections", headers=issuer_headers)).json()
    assert [h["reviewer_id"] for h in history] == [other[0].json()["reviewer_id"]]


async def test_a_matching_precondition_is_accepted_and_after_reloading_the_reviewer_can_correct(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
):
    seen = _claim(created_extraction, F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                     is_unknown=False, expected_claim=observed(seen), **NEW_EVIDENCE)
    assert r.status_code == 200, r.text
    # The other reviewer's stale request is refused; after reloading, the current state can be kept knowingly
    stale = await _patch(client, created_extraction, F.ownership_nature, issuer_headers, corrected_value=seen["value"],
                         is_unknown=False, expected_claim=observed(seen))
    _assert_conflict(stale, ["source_page", "supporting_passage"])
    reloaded = _claim(await _read(client, created_extraction["id"], issuer_headers), F.ownership_nature)
    r = await _patch(client, created_extraction, F.ownership_nature, issuer_headers, corrected_value=reloaded["value"],
                     is_unknown=False, expected_claim=observed(reloaded), correction_note="Checked page 2.")
    assert r.status_code == 200, r.text
    assert (r.json()["original_passage"], r.json()["corrected_passage"]) == (FEE_SIMPLE_QUOTE, FEE_SIMPLE_QUOTE)
    count = (await async_db.execute(text("SELECT COUNT(*) FROM review_corrections"))).scalar_one()
    assert count == 2


# ── 5. Every component, every kind of correction ────────────────────────────


@pytest.mark.parametrize("field,stale_value", [
    ("value", "A value the reviewer never saw."),
    ("is_unknown", True),
    ("source_page", 2),
    ("supporting_passage", INCOME_PASSAGE),
])
async def test_every_state_component_is_compared(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, field: str, stale_value,
):
    seen = _claim(created_extraction, F.ownership_nature)
    expected = {**observed(seen), field: stale_value}
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                     is_unknown=False, expected_claim=expected)
    _assert_conflict(r, [field])
    assert await _snapshot(async_db) == before


@pytest.mark.parametrize("kind", ["keep", "replace_evidence", "change_value", "make_unknown", "page_only"])
async def test_the_precondition_protects_every_kind_of_correction(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
    kind: str,
):
    seen = _claim(created_extraction, F.ownership_nature)
    # Another reviewer saves new evidence first
    assert (await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                         is_unknown=False, **NEW_EVIDENCE)).status_code == 200
    body = {
        "keep": {"corrected_value": seen["value"], "is_unknown": False},
        "replace_evidence": {"corrected_value": seen["value"], "is_unknown": False,
                             "corrected_source_page": 1, "corrected_passage": OWNERSHIP_QUOTE},
        "change_value": {"corrected_value": NEW_VALUE, "is_unknown": False, **NEW_EVIDENCE},
        "make_unknown": {"corrected_value": "unknown", "is_unknown": True,
                         "corrected_source_page": None, "corrected_passage": None},
        "page_only": {"corrected_value": seen["value"], "is_unknown": False, "corrected_source_page": 1},
    }[kind]
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, issuer_headers, expected_claim=observed(seen), **body)
    _assert_conflict(r, ["source_page", "supporting_passage"])
    assert await _snapshot(async_db) == before


async def test_unknown_claims_use_null_in_the_precondition(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict,
):
    unknown = next(c for c in created_extraction["claims"] if c["is_unknown"])
    assert observed(unknown) == {"value": "unknown", "is_unknown": True, "source_page": None, "supporting_passage": None}
    field = LegalFieldName(unknown["field_name"])
    made_known = {"corrected_value": "Stated value.", "is_unknown": False,
                  "corrected_source_page": 2, "corrected_passage": FEE_SIMPLE_QUOTE}
    r = await _patch(client, created_extraction, field, legal_headers, expected_claim=observed(unknown), **made_known)
    assert r.status_code == 200, r.text
    # The other reviewer, who also read it as unknown, is told that it changed
    r = await _patch(client, created_extraction, field, issuer_headers, expected_claim=observed(unknown),
                     corrected_value="unknown", is_unknown=True)
    _assert_conflict(r, list(STATE))


# ── 6. Invalid preconditions ─────────────────────────────────────────────────


@pytest.mark.parametrize("mutation", [
    "missing_page", "missing_passage", "missing_value", "missing_flag", "extra_key", "page_as_text", "page_as_bool",
    "flag_as_text", "flag_as_int", "value_null", "value_number", "passage_number", "not_an_object", "list",
])
async def test_invalid_preconditions_are_rejected(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, mutation: str,
):
    seen = _claim(created_extraction, F.ownership_nature)
    expected = observed(seen)
    removed = {"missing_page": "source_page", "missing_passage": "supporting_passage", "missing_value": "value",
               "missing_flag": "is_unknown"}
    if mutation in removed:
        expected.pop(removed[mutation])
    expected = {
        "extra_key": {**expected, "evidence_status": "verified"},
        "page_as_text": {**expected, "source_page": "1"},
        "page_as_bool": {**expected, "source_page": True},
        "flag_as_text": {**expected, "is_unknown": "false"},
        "flag_as_int": {**expected, "is_unknown": 0},
        "value_null": {**expected, "value": None},
        "value_number": {**expected, "value": 5},
        "passage_number": {**expected, "supporting_passage": 7},
        "not_an_object": "stale",
        "list": [expected],
    }.get(mutation, expected)
    before = await _snapshot(async_db)
    r = await _patch(client, created_extraction, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                     is_unknown=False, expected_claim=expected)
    assert r.status_code == 422, (mutation, r.text)
    assert isinstance(r.json()["detail"], list)
    assert await _snapshot(async_db) == before


# ── 7. Approval and audit integrity ──────────────────────────────────────────


async def test_the_approval_binds_the_state_that_was_actually_saved(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict, monkeypatch,
):
    ex = await _fresh(client, admin_client, issuer_headers)
    seen = _claim(ex, F.ownership_nature)
    other = _run_before_lock(monkeypatch, lambda: _patch(
        client, ex, F.ownership_nature, legal_headers, corrected_value=seen["value"], is_unknown=False,
        expected_claim=observed(seen), **NEW_EVIDENCE))
    stale = await asyncio.wait_for(_patch(client, ex, F.ownership_nature, issuer_headers, corrected_value=seen["value"],
                                          is_unknown=False, expected_claim=observed(seen)), 30)
    _assert_conflict(stale, ["source_page", "supporting_passage"])
    monkeypatch.undo()
    approval = await _approve(client, ex["id"], issuer_headers)
    assert approval.status_code == 201, approval.text
    record = next(c for c in approval.json()["evidence_record"]["claims"] if c["field_name"] == "ownership_nature")
    assert record["correction_id"] == other[0].json()["id"]
    assert (record["source_page"], record["supporting_passage"], record["evidence_status"]) == (2, FEE_SIMPLE_QUOTE, "verified")
    assert approval.json()["currency"] == {"is_current": True, "reasons": []}
    await _oracle_check(file_db, approval.json()["id"])


async def test_an_approved_extraction_keeps_its_existing_conflict_answer(
    client: AsyncClient, created_extraction: dict, issuer_headers: dict, legal_headers: dict, async_db: AsyncSession,
):
    seen = _claim(created_extraction, F.ownership_nature)
    assert (await _approve(client, created_extraction["id"], legal_headers)).status_code == 201
    before = await _snapshot(async_db)
    for expected in (observed(seen), {**observed(seen), "source_page": 2}):
        r = await _patch(client, created_extraction, F.ownership_nature, issuer_headers, corrected_value=seen["value"],
                         is_unknown=False, expected_claim=expected)
        assert r.status_code == 409 and r.json() == {"detail": "An approved extraction cannot be corrected."}
    assert await _snapshot(async_db) == before


# ── 8. Cross-tenant isolation ────────────────────────────────────────────────


async def test_another_tenant_learns_nothing_from_a_precondition(
    client: AsyncClient, created_extraction: dict, other_legal_headers: dict, async_db: AsyncSession,
):
    seen = _claim(created_extraction, F.ownership_nature)
    before = await _snapshot(async_db)
    for expected in (observed(seen), {**observed(seen), "value": "guess"}):
        r = await _patch(client, created_extraction, F.ownership_nature, other_legal_headers,
                         corrected_value=seen["value"], is_unknown=False, expected_claim=expected)
        assert r.status_code == 404 and r.json() == {"detail": "Extraction not found."}
    # Without a precondition the answer is the same 422 for a foreign and for a non-existent extraction
    foreign = await _legacy_patch(client, created_extraction, F.ownership_nature, other_legal_headers,
                                  corrected_value=seen["value"], is_unknown=False)
    missing = await client.patch(f"/extractions/00000000-0000-0000-0000-000000000000/claims/{seen['id']}",
                                 headers=other_legal_headers, json={"corrected_value": seen["value"], "is_unknown": False})
    assert foreign.status_code == missing.status_code == 422 and foreign.json() == missing.json()
    assert await _snapshot(async_db) == before


# ── 9. Backward compatibility ────────────────────────────────────────────────


@pytest.mark.parametrize("kind", ["keep", "replace_evidence", "make_unknown"])
async def test_legacy_clients_are_rejected_for_every_kind_of_correction(
    client: AsyncClient, created_extraction: dict, legal_headers: dict, async_db: AsyncSession, kind: str,
):
    seen = _claim(created_extraction, F.ownership_nature)
    body = {
        "keep": {"corrected_value": seen["value"], "is_unknown": False},
        "replace_evidence": {"corrected_value": seen["value"], "is_unknown": False, **NEW_EVIDENCE},
        "make_unknown": {"corrected_value": "unknown", "is_unknown": True},
    }[kind]
    before = await _snapshot(async_db)
    r = await _legacy_patch(client, created_extraction, F.ownership_nature, legal_headers, **body)
    assert r.status_code == 422, r.text
    assert [error["loc"] for error in r.json()["detail"]] == [["body", "expected_claim"]]
    assert await _snapshot(async_db) == before


def test_openapi_documents_the_mandatory_precondition_and_the_409():
    schema = app.openapi()
    request = schema["components"]["schemas"]["ClaimCorrectionRequest"]
    assert "expected_claim" in request["properties"] and "expected_claim" in request["required"]
    assert request["properties"]["expected_claim"] == {
        "$ref": "#/components/schemas/ExpectedClaimState", "description": request["properties"]["expected_claim"]["description"]}
    assert all("expected_claim" in example for example in request["examples"])
    expected = schema["components"]["schemas"]["ExpectedClaimState"]
    assert set(expected["required"]) == set(STATE) and expected.get("additionalProperties") is False
    operation = schema["paths"]["/extractions/{extraction_id}/claims/{claim_id}"]["patch"]
    assert "409" in operation["responses"] and "`expected_claim` is **required**" in operation["description"]


# ── 10. Parallel reviewers ───────────────────────────────────────────────────


async def test_parallel_reviewers_never_preserve_evidence_they_did_not_see(
    file_db, client: AsyncClient, admin_client: AsyncClient, issuer_headers: dict, legal_headers: dict,
):
    asset = (await admin_client.post("/assets", json=_MERIDIAN_ASSET)).json()
    doc = await _upload(client, asset["id"], make_source_pdf(), issuer_headers)
    outcomes = []
    for delay in (0.0, 0.02, 0.05, 0.1, 0.0, 0.2):
        ex = await _extract(client, doc["id"], issuer_headers)
        seen = _claim(ex, F.ownership_nature)

        async def keep():
            await asyncio.sleep(delay)
            return await _patch(client, ex, F.ownership_nature, issuer_headers, corrected_value=seen["value"],
                                is_unknown=False, expected_claim=observed(seen))

        async def replace():
            return await _patch(client, ex, F.ownership_nature, legal_headers, corrected_value=seen["value"],
                                is_unknown=False, expected_claim=observed(seen), **NEW_EVIDENCE)

        kept, replaced = await asyncio.wait_for(asyncio.gather(keep(), replace()), 60)
        assert kept.status_code in (200, 409, 503) and replaced.status_code in (200, 409, 503), (kept.text, replaced.text)
        if kept.status_code == 200:  # the keep ran first: it kept exactly what its reviewer saw
            assert (kept.json()["original_source_page"], kept.json()["original_passage"]) == (1, OWNERSHIP_QUOTE)
        if kept.status_code == 409:  # the replacement ran first
            assert replaced.status_code == 200 and kept.json()["changed_fields"] == ["source_page", "supporting_passage"]
        # The stored claim always equals the last accepted correction (or the AI's claim)
        async with file_db() as s:
            row = (await s.execute(text("SELECT source_page, supporting_passage FROM legal_claims WHERE id = :c"),
                                   {"c": seen["id"]})).one()
            last = (await s.execute(text(
                "SELECT corrected_source_page, corrected_passage FROM review_corrections WHERE claim_id = :c "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1"), {"c": seen["id"]})).one_or_none()
        assert tuple(row) == (tuple(last) if last else (1, OWNERSHIP_QUOTE))
        outcomes.append((kept.status_code, replaced.status_code))
    print(f"parallel correction outcomes: {outcomes}")
