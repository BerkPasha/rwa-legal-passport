"""
Stage 9B-4 step 3 — AI extraction output integrity and duplicate claim protection.

No paid API call is made: the real AnthropicAIService runs against an in-process fake client.
Coverage:
 1. validate_ai_claims: valid output, duplicates, unknown integrity, missing fields, malformed claims
 2. _parse_and_complete: strict JSON parsing
 3. Real service path: response shapes and API errors (fake client)
 4. API: rejection without partial persistence, real and mock consistency
 5. Retry and fallback behaviour
 6. Transaction safety
 7. Tenant isolation and version binding
 8. Interaction with the Step 2 evidence results
"""

import json
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import anthropic
import httpx
import pytest
from httpx import AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import LegalFieldName
from app.main import app
from app.models.extraction import LegalClaim, LegalExtraction
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter
from app.schemas.extraction import AIExtractionResult, ClaimSource, RawClaim
from app.services.ai import (
    UNVERIFIED_DISCLAIMER,
    AIOutputValidationError,
    AnthropicAIService,
    _parse_and_complete,
    get_ai_service,
    validate_ai_claims,
)
from tests.mock_ai import MockAIService
from tests.pdf_fixtures import make_text_pdf

F = LegalFieldName
FAKE_KEY = "fictional-key-for-tests-only"
PDF = make_text_pdf([["This Trust Agreement is governed by the laws", "of the State of New York, United States of America."]])
QUOTE = "governed by the laws of the State of New York"


def known(field: LegalFieldName, value: str = "Stated value.", page=1, passage: str | None = QUOTE) -> RawClaim:
    source = ClaimSource(page_number=page, supporting_passage=passage) if passage is not None else None
    return RawClaim(field=field, value=value, is_unknown=False, source=source)


def unknown(field: LegalFieldName, value: str = "unknown", page=None, passage: str | None = None) -> RawClaim:
    source = ClaimSource(page_number=page, supporting_passage=passage) if passage is not None else None
    return RawClaim(field=field, value=value, is_unknown=True, source=source)


def as_json(*claims: RawClaim) -> str:
    return json.dumps({"claims": [c.model_dump(mode="json") for c in claims]})


def stored(claims: list[RawClaim]) -> list[tuple]:
    return [(c.field.value, c.value, c.is_unknown, c.source.page_number if c.source else None,
             c.source.supporting_passage if c.source else None) for c in claims]


# ── Fakes (no network) ───────────────────────────────────────────────────────


class ScriptedAI:
    """A mocked AI service returning exactly the given claims."""

    def __init__(self, *claims):
        self._claims = list(claims)

    async def extract_legal_rights(self, document_id: str, version_id: str, pages: list[dict]) -> AIExtractionResult:
        return AIExtractionResult.model_construct(
            claims=self._claims, ai_model="scripted-test",
            extraction_timestamp=datetime.now(timezone.utc), disclaimer=UNVERIFIED_DISCLAIMER,
        )


class _FakeMessages:
    def __init__(self, outcome):
        self.outcome = outcome
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def text_response(text: str, stop_reason: str = "end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)


def real_service(outcome) -> AnthropicAIService:
    """The real service class with its HTTP client replaced by an in-process fake."""
    service = AnthropicAIService(api_key=FAKE_KEY, model="fake-model")
    service._client = SimpleNamespace(messages=_FakeMessages(outcome))
    return service


_REQUEST = httpx.Request("POST", "https://api.anthropic.invalid/v1/messages")


# ── 1. validate_ai_claims ────────────────────────────────────────────────────


async def test_valid_mock_output_passes_unchanged():
    claims = (await MockAIService().extract_legal_rights("d", "v", [])).claims
    validated = validate_ai_claims(claims)
    assert [c.field for c in validated] == list(F)
    assert sorted(stored(validated)) == sorted(stored(claims))


@pytest.mark.parametrize("duplicate", [
    known(F.ownership_nature),                                   # identical duplicate
    known(F.ownership_nature, value="Leasehold interest only"),  # contradictory duplicate
    unknown(F.ownership_nature),                                 # known and unknown for one field
])
def test_duplicate_field_rejects_the_whole_response(duplicate):
    with pytest.raises(AIOutputValidationError, match=r"duplicate legal fields \(ownership_nature\)"):
        validate_ai_claims([known(F.ownership_nature), known(F.jurisdiction), duplicate])


def test_all_duplicate_fields_are_reported_in_a_fixed_order():
    claims = [known(F.voting_rights), known(F.income_rights), known(F.voting_rights), known(F.income_rights)]
    with pytest.raises(AIOutputValidationError, match=r"\(income_rights, voting_rights\)"):
        validate_ai_claims(claims)


def test_duplicate_detection_does_not_depend_on_claim_order():
    claims = [known(F.jurisdiction, "A"), known(F.jurisdiction, "B")]
    for ordered in (claims, list(reversed(claims))):
        with pytest.raises(AIOutputValidationError):
            validate_ai_claims(ordered)


@pytest.mark.parametrize("claim", [
    unknown(F.transfer_restrictions, value="Freely transferable"),                     # fabricated value
    unknown(F.transfer_restrictions, passage="Transfers require prior consent.", page=1),  # unsupported quote
    unknown(F.transfer_restrictions, value="Restricted", passage=QUOTE, page=999),
    known(F.transfer_restrictions, value="unknown"),                                    # flag contradicts value
    known(F.transfer_restrictions, value="  Unknown. "),
    known(F.transfer_restrictions, value=""),
    known(F.transfer_restrictions, value="   ", passage=QUOTE),
])
def test_unknown_claims_are_canonical(claim):
    (result,) = [c for c in validate_ai_claims([claim]) if c.field is F.transfer_restrictions]
    assert (result.value, result.is_unknown, result.source) == ("unknown", True, None)


def test_known_claims_keep_their_value_and_source():
    claim = known(F.voting_rights, value="No voting rights are granted to token holders.", page=7)
    (result,) = [c for c in validate_ai_claims([claim]) if c.field is F.voting_rights]
    assert result == claim


def test_missing_fields_are_completed_as_unknown():
    validated = validate_ai_claims([known(F.jurisdiction)])
    assert [c.field for c in validated] == list(F)
    assert [c.field for c in validated if not c.is_unknown] == [F.jurisdiction]
    assert all(c.value == "unknown" and c.source is None for c in validated if c.is_unknown)
    assert all(c.is_unknown for c in validate_ai_claims([]))


@pytest.mark.parametrize("claim", [
    {"field": "jurisdiction", "value": "New York"},                                   # is_unknown missing
    {"value": "New York", "is_unknown": False},                                       # field missing
    {"field": "jurisdiction", "is_unknown": False},                                   # value missing
    {"field": "governing_law", "value": "New York", "is_unknown": False},             # not a legal field
    {"field": "jurisdiction", "value": "New York", "is_unknown": "yes"},              # not a boolean
    {"field": "jurisdiction", "value": "New York", "is_unknown": 0},
    {"field": "jurisdiction", "value": 5, "is_unknown": False},
    {"field": "jurisdiction", "value": "NY", "is_unknown": False,
     "source": {"page_number": True, "supporting_passage": QUOTE}},                    # true would be page 1
    {"field": "jurisdiction", "value": "NY", "is_unknown": False,
     "source": {"page_number": "1", "supporting_passage": QUOTE}},
    {"field": "jurisdiction", "value": "NY", "is_unknown": False,
     "source": {"page_number": 1.5, "supporting_passage": QUOTE}},
    {"field": "jurisdiction", "value": "NY", "is_unknown": False, "source": {"page_number": 1}},
    "not a claim",
    None,
])
def test_malformed_claims_reject_the_response(claim):
    with pytest.raises(AIOutputValidationError, match="AI returned an invalid response format"):
        validate_ai_claims([known(F.income_rights), claim])


def test_bypassing_model_validation_does_not_bypass_the_gate():
    forged = RawClaim.model_construct(
        field=F.jurisdiction, value="NY", is_unknown=False,
        source=ClaimSource.model_construct(page_number=True, supporting_passage=QUOTE),
    )
    with pytest.raises(AIOutputValidationError):
        validate_ai_claims([forged])


@pytest.mark.parametrize("claims", [None, 42, "claims"])
def test_non_list_claims_are_rejected(claims):
    with pytest.raises(AIOutputValidationError):
        validate_ai_claims(claims)


def test_validation_is_deterministic_and_idempotent():
    claims = [unknown(F.voting_rights, value="x"), known(F.jurisdiction), known(F.income_rights, page=2)]
    first = validate_ai_claims(claims)
    assert validate_ai_claims(list(reversed(claims))) == first
    assert validate_ai_claims(first) == first
    assert validate_ai_claims(claims) == first


def test_validation_error_follows_the_existing_runtime_error_path():
    assert issubclass(AIOutputValidationError, RuntimeError)


# ── 2. _parse_and_complete: strict JSON parsing ──────────────────────────────


def test_valid_json_is_parsed_plain_and_fenced():
    text = as_json(known(F.jurisdiction))
    for raw in (text, f"```json\n{text}\n```", f"```\n{text}\n```"):
        result = _parse_and_complete(raw, "m")
        assert len(result.claims) == 7
        assert result.disclaimer == UNVERIFIED_DISCLAIMER


@pytest.mark.parametrize("raw", [
    "not json at all",
    "",
    "[]",
    '{"items": []}',
    '{"claims": null}',
    '{"claims": {"field": "jurisdiction"}}',
    # A repeated key would otherwise silently keep the last value
    '{"claims": [], "claims": [{"field": "jurisdiction", "value": "NY", "is_unknown": false}]}',
    '{"claims": [{"field": "jurisdiction", "value": "New York", "value": "Delaware", "is_unknown": false}]}',
    '{"claims": [{"field": "jurisdiction", "value": "NY", "is_unknown": false,'
    ' "source": {"page_number": NaN, "supporting_passage": "x"}}]}',
    '{"claims": [{"field": "jurisdiction", "value": "NY", "is_unknown": false,'
    ' "source": {"page_number": true, "supporting_passage": "x"}}]}',
    '{"claims": [{"field": "jurisdiction", "value": "NY", "is_unknown": "false"}]}',
    '{"claims": [{"field": "jurisdiction", "value": "A", "is_unknown": false},'
    ' {"field": "jurisdiction", "value": "B", "is_unknown": false}]}',
])
def test_malformed_ai_json_is_rejected(raw):
    with pytest.raises(AIOutputValidationError, match="AI returned an invalid response format"):
        _parse_and_complete(raw, "m")


def test_duplicate_json_keys_are_named():
    raw = '{"claims": [{"field": "jurisdiction", "value": "A", "value": "B", "is_unknown": false}]}'
    with pytest.raises(AIOutputValidationError, match="duplicate JSON keys: value"):
        _parse_and_complete(raw, "m")


# ── 3. Real service path (fake client, no network) ───────────────────────────


async def test_real_service_valid_response():
    service = real_service(text_response(as_json(known(F.jurisdiction))))
    result = await service.extract_legal_rights("d", "v", [{"page_number": 1, "text_content": "x"}])
    assert [c.field for c in result.claims] == list(F)
    assert service._client.messages.calls == 1


@pytest.mark.parametrize("response, message", [
    (text_response(as_json(known(F.jurisdiction)), stop_reason="max_tokens"), "truncated"),
    (SimpleNamespace(content=[], stop_reason="end_turn"), "no text"),
    (SimpleNamespace(content=None, stop_reason="end_turn"), "no text"),
    (SimpleNamespace(content=[SimpleNamespace(type="tool_use", input={})], stop_reason="tool_use"), "no text"),
    (text_response(as_json(known(F.jurisdiction), known(F.jurisdiction, "B"))), "duplicate legal fields"),
])
async def test_real_service_rejects_invalid_responses(response, message):
    with pytest.raises(AIOutputValidationError, match=message):
        await real_service(response).extract_legal_rights("d", "v", [])


@pytest.mark.parametrize("error, message", [
    (anthropic.APIConnectionError(request=_REQUEST), "connection failed"),
    (anthropic.APITimeoutError(request=_REQUEST), "connection failed"),
    (anthropic.RateLimitError("limited", response=httpx.Response(429, request=_REQUEST), body=None), "rate limit"),
    (anthropic.AuthenticationError("denied", response=httpx.Response(401, request=_REQUEST), body=None),
     "authentication failed"),
    (anthropic.InternalServerError("boom", response=httpx.Response(500, request=_REQUEST), body=None), "HTTP 500"),
])
async def test_real_service_errors_are_controlled(error, message):
    with pytest.raises(RuntimeError, match=message) as info:
        await real_service(error).extract_legal_rights("d", "v", [])
    assert not isinstance(info.value, AIOutputValidationError)
    assert FAKE_KEY not in str(info.value)


# ── 4. API: rejection without partial persistence, real and mock consistency ─


async def _counts(db: AsyncSession) -> tuple[int, int]:
    extractions = (await db.execute(select(func.count()).select_from(LegalExtraction))).scalar_one()
    claims = (await db.execute(select(func.count()).select_from(LegalClaim))).scalar_one()
    return extractions, claims


async def _upload(client: AsyncClient, asset_id: str, headers: dict) -> dict:
    r = await client.post(f"/assets/{asset_id}/documents", headers=headers,
                          data={"document_type": "prospectus", "title": "Prospectus"},
                          files={"file": ("doc.pdf", PDF, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()


async def _trigger(client: AsyncClient, document_id: str, headers: dict, service):
    app.dependency_overrides[get_ai_service] = lambda: service
    return await client.post(f"/documents/{document_id}/extractions", headers=headers)


DUPLICATE_SETS = {
    "identical": (known(F.ownership_nature), known(F.ownership_nature)),
    "contradictory": (known(F.ownership_nature, "Fee simple"), known(F.ownership_nature, "Leasehold only")),
    "known_and_unknown": (known(F.ownership_nature), unknown(F.ownership_nature)),
}


@pytest.mark.parametrize("name", sorted(DUPLICATE_SETS))
@pytest.mark.parametrize("path", ["mock", "real"])
async def test_duplicate_fields_are_rejected_and_nothing_is_stored(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict,
    caplog, name: str, path: str,
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    claims = DUPLICATE_SETS[name]
    service = ScriptedAI(*claims) if path == "mock" else real_service(text_response(as_json(*claims)))
    before = await _counts(async_db)
    with caplog.at_level(logging.WARNING, logger="rwa_security"):
        r = await _trigger(client, doc["id"], issuer_headers, service)
    assert r.status_code == 503
    assert r.json()["detail"] == (
        "AI returned an invalid response format: duplicate legal fields (ownership_nature). "
        "No claim of this response was stored."
    )
    assert await _counts(async_db) == before
    listed = await client.get(f"/documents/{doc['id']}/extractions", headers=issuer_headers)
    assert listed.json() == []
    assert any("extraction.ai_output_rejected" in rec.getMessage() for rec in caplog.records)


@pytest.mark.parametrize("raw", [
    "This is not JSON.",
    '{"claims": [{"field": "jurisdiction", "value": "A", "value": "B", "is_unknown": false}]}',
    '{"claims": [{"field": "jurisdiction", "value": "NY", "is_unknown": false,'
    ' "source": {"page_number": true, "supporting_passage": "x"}}]}',
])
async def test_malformed_real_output_stores_nothing(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict, raw: str
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    before = await _counts(async_db)
    r = await _trigger(client, doc["id"], issuer_headers, real_service(text_response(raw)))
    assert r.status_code == 503
    assert r.json()["detail"].startswith("AI returned an invalid response format")
    assert await _counts(async_db) == before


async def test_forged_mock_output_cannot_bypass_validation(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    forged = RawClaim.model_construct(
        field=F.jurisdiction, value="NY", is_unknown=False,
        source=ClaimSource.model_construct(page_number=True, supporting_passage=QUOTE),
    )
    before = await _counts(async_db)
    r = await _trigger(client, doc["id"], issuer_headers, ScriptedAI(forged))
    assert r.status_code == 503
    assert await _counts(async_db) == before


CONSISTENCY_SETS = {
    "unknown_with_fabricated_value_and_quote": (
        known(F.jurisdiction),
        unknown(F.transfer_restrictions, value="Freely transferable", passage="Transfers are free.", page=1),
    ),
    "known_flag_with_unknown_value": (known(F.jurisdiction), known(F.voting_rights, value="Unknown")),
    "missing_fields": (known(F.jurisdiction),),
}


@pytest.mark.parametrize("name", sorted(CONSISTENCY_SETS))
async def test_real_and_mock_outputs_are_stored_identically(
    client: AsyncClient, created_asset: dict, issuer_headers: dict, name: str
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    claims = CONSISTENCY_SETS[name]
    bodies = []
    for service in (ScriptedAI(*claims), real_service(text_response(as_json(*claims)))):
        r = await _trigger(client, doc["id"], issuer_headers, service)
        assert r.status_code == 201, r.text
        bodies.append(r.json())
    shape = [
        [(c["field_name"], c["value"], c["is_unknown"], c["source_page"], c["supporting_passage"], c["evidence"]["status"])
         for c in body["claims"]]
        for body in bodies
    ]
    assert shape[0] == shape[1]
    assert len(shape[0]) == 7


async def test_valid_extraction_behaviour_is_preserved(
    client: AsyncClient, created_document: dict, issuer_headers: dict
):
    r = await _trigger(client, created_document["id"], issuer_headers, MockAIService())
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "draft" and body["disclaimer"] == UNVERIFIED_DISCLAIMER
    expected = sorted(stored((await MockAIService().extract_legal_rights("d", "v", [])).claims))
    assert sorted((c["field_name"], c["value"], c["is_unknown"], c["source_page"], c["supporting_passage"])
                  for c in body["claims"]) == expected


# ── 5. Retry and fallback behaviour ──────────────────────────────────────────


async def test_retry_after_a_rejected_response_creates_exactly_one_extraction(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    rejected = await _trigger(client, doc["id"], issuer_headers, ScriptedAI(known(F.jurisdiction), known(F.jurisdiction)))
    assert rejected.status_code == 503
    retried = await _trigger(client, doc["id"], issuer_headers, ScriptedAI(known(F.jurisdiction)))
    assert retried.status_code == 201
    listed = (await client.get(f"/documents/{doc['id']}/extractions", headers=issuer_headers)).json()
    assert [e["id"] for e in listed] == [retried.json()["id"]]
    assert await _counts(async_db) == (1, 7)


async def test_ai_failure_has_no_fallback_and_stores_nothing(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    service = real_service(anthropic.APIConnectionError(request=_REQUEST))
    r = await _trigger(client, doc["id"], issuer_headers, service)
    assert r.status_code == 503
    assert r.json()["detail"] == "AI service: connection failed. Please try again later."
    assert service._client.messages.calls == 1   # no application-level retry
    assert await _counts(async_db) == (0, 0)


async def test_rejected_responses_count_towards_the_rate_limit(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    limiter = InMemoryRateLimiter(max_requests=1, window_seconds=3600)
    app.dependency_overrides[get_extraction_rate_limiter] = lambda: limiter
    duplicate = ScriptedAI(known(F.jurisdiction), known(F.jurisdiction))
    assert (await _trigger(client, doc["id"], issuer_headers, duplicate)).status_code == 503
    assert (await _trigger(client, doc["id"], issuer_headers, ScriptedAI())).status_code == 429


# ── 6. Transaction safety ────────────────────────────────────────────────────


async def test_failure_while_saving_claims_leaves_nothing_behind(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)

    def fail_on_claims(session, flush_context, instances):
        if any(isinstance(obj, LegalClaim) for obj in session.new):
            raise RuntimeError("simulated failure while storing claims")

    event.listen(async_db.sync_session, "before_flush", fail_on_claims)
    try:
        with pytest.raises(RuntimeError, match="simulated failure"):
            await _trigger(client, doc["id"], issuer_headers, MockAIService())
    finally:
        event.remove(async_db.sync_session, "before_flush", fail_on_claims)

    # The extraction row was flushed before the failure; it must have been rolled back too
    assert await _counts(async_db) == (0, 0)
    assert (await _trigger(client, doc["id"], issuer_headers, MockAIService())).status_code == 201
    assert await _counts(async_db) == (1, 7)


# ── 7. Tenant isolation and version binding ──────────────────────────────────


async def test_other_tenant_cannot_trigger_and_no_ai_call_is_made(
    client: AsyncClient, async_db: AsyncSession, created_asset: dict, issuer_headers: dict,
    other_issuer_headers: dict,
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    service = real_service(text_response(as_json(known(F.jurisdiction))))
    r = await _trigger(client, doc["id"], other_issuer_headers, service)
    assert r.status_code == 404
    assert service._client.messages.calls == 0
    assert await _counts(async_db) == (0, 0)


async def test_validated_extraction_is_bound_to_the_current_version(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    r = await _trigger(client, doc["id"], issuer_headers, ScriptedAI(known(F.jurisdiction)))
    assert r.json()["version_id"] == doc["current_version"]["id"]


# ── 8. Interaction with the Step 2 evidence results ──────────────────────────


async def test_canonical_unknown_claims_are_never_evidence(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    doc = await _upload(client, created_asset["id"], issuer_headers)
    claims = (known(F.jurisdiction),
              unknown(F.ownership_nature, value="Full legal title", passage=QUOTE, page=1))  # a real quotation
    r = await _trigger(client, doc["id"], issuer_headers, real_service(text_response(as_json(*claims))))
    assert r.status_code == 201
    by_field = {c["field_name"]: c for c in r.json()["claims"]}
    assert by_field["jurisdiction"]["evidence"]["status"] == "verified"
    ownership = by_field["ownership_nature"]
    assert (ownership["value"], ownership["is_unknown"], ownership["source_page"], ownership["supporting_passage"]) \
        == ("unknown", True, None, None)
    assert ownership["evidence"]["status"] == "not_applicable"
    assert all(c["evidence"]["status"] == "not_applicable" for c in r.json()["claims"] if c["is_unknown"])
