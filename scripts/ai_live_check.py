"""
Controlled live AI extraction check (Stage 10A-3). Operator guide: AI_LIVE_TEST.md.

Drives the real extraction route (POST /documents/{id}/extractions) of the real application, with
its real gates, in an isolated temporary environment: a new SQLite database, storage directory and
access registry in the system temp directory. The development database, storage, .env and
access_control.json are never read or written; the working directory is the temporary directory, so
.env is not loaded at all.

    python scripts/ai_live_check.py --dry-run
    python scripts/ai_live_check.py --fake-provider all
    python scripts/ai_live_check.py --live --authorize-paid-request --authorize-external-transfer --max-cost-usd 0.25

Disabled by default: without one of these modes nothing happens (exit 2).
- --dry-run: registers a fictional asset, uploads the verified synthetic fixture, passes the issuer
  authorization and receives the confirmation request (428) with the exact input size. It checks the
  budget and stops before confirming. Every transport is blocked: zero AI requests.
- --fake-provider SCENARIO|all: the whole path, including the confirmed request, against an
  in-process fake of the Messages API (offline). Every scenario states its expected outcome.
- --live: exactly one real request to Anthropic with the synthetic fixture only. It requires both
  authorization flags, an explicit --max-cost-usd and ANTHROPIC_API_KEY in this process's
  environment.

Fail-closed:
- Every prerequisite is checked before anything is sent:
  - the fixture SHA-256 (pinned here and in SHA256SUMS);
  - a supported model with a known price;
  - the input, output and cost budget;
  - the flags and the key format;
  - the isolation of the database.
- One request at most: the SDK retries are disabled, and a guard transport allows a single
  POST https://api.anthropic.com/v1/messages and blocks anything else.
- Nothing is approved: the harness never calls an approval endpoint, and the result is a draft
  that needs human review.

Output: model, token usage, cost, claim counts, evidence statuses, page numbers, codes and timings.
It never contains the API key, the fictional credentials or document text. Quotations and claim
values appear only with --include-claim-values, and then only in --report-file, never on the
console. Exit codes: 0 completed (and, with fake providers, every scenario as expected), 1 an
unexpected outcome or error, 2 refused (nothing was sent).
"""

import argparse
import asyncio
import hashlib
import json
import logging
import os
import secrets
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx2

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "real_pdfs"
FIXTURE_NAME = "trust_prospectus.pdf"
# Pinned here as well as in SHA256SUMS: both must agree with the file (tests/fixtures/real_pdfs/README.md)
FIXTURE_SHA256 = "a2ee44089c7ce48c3042a6ef88200fc5c349867745daf4ecd1e5a214ef017772"
FIXTURE_PAGES = 4
DEV_DATABASE = ROOT / "rwa_legal_passport.db"

# USD per million tokens (input, output) from https://platform.claude.com/docs/en/about-claude/pricing,
# fetched 2026-10-18. Verify again before a live run; a model without a price here is refused.
PRICES_SOURCE = "platform.claude.com/docs/en/about-claude/pricing (fetched 2026-10-18)"
PRICES_USD_PER_MTOK = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),     # also its model page (models/sonnet-4-6), 2026-10-18; no long-context tier
    "claude-haiku-5-5": (0.10, 0.50),       # prompts up to 100,000 tokens
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-haiku-4-5-20251001": (1.00, 5.00),
}
HAIKU_5_5_LONG_PROMPT = (100_000, (0.50, 2.50))   # above 100,000 prompt tokens

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_MAX_OUTPUT_TOKENS = 8192
DEFAULT_MAX_INPUT_TOKENS = 10_000
DEFAULT_MAX_COST_USD = 0.25        # dry-run and fake modes only; --live needs it explicitly
LIVE_TIMEOUT_SECONDS = 120

# What the fixture states, for the evaluation: the page of each legal field, and one verbatim quotation
# per field (used only by the fake provider; never printed)
GROUND_TRUTH = {
    "ownership_nature": (1, "Unitholders do not acquire legal title to the underlying real estate; "
                            "legal title remains vested in the Trustee."),
    "income_rights": (2, "Net rental income shall be distributed quarterly, pro rata to the number of "
                         "Digital Units held"),
    "voting_rights": (2, "each Digital Unit carries one vote on matters reserved to unitholders"),
    "transfer_restrictions": (3, "approved by the Trustee — any other transfer is void"),
    "redemption_rights": (3, "The Trust does not offer a redemption facility"),
    "insolvency_provisions": (4, "the Trust property shall not form part of the Trustee’s estate"),
    "jurisdiction": (4, "governed by the laws of England and Wales"),
}
AUTHORIZED_ISSUER = "ai-live-check-issuer"
UNAUTHORIZED_ISSUER = "ai-live-check-no-ai-consent"
FAKE_KEY = "sk-ant-offline-fictional-key-for-the-fake-provider-000000"   # never valid, never sent


class Refusal(Exception):
    """A prerequisite is missing; nothing has been or will be sent (exit 2)."""


# ── Pure checks (no application import) ──────────────────────────────────────


def verify_fixture(directory: Path = FIXTURES, name: str = FIXTURE_NAME, pinned: str = FIXTURE_SHA256) -> bytes:
    """The fixture bytes, only if they match both the pinned digest and SHA256SUMS."""
    path, manifest = directory / name, directory / "SHA256SUMS"
    if not path.is_file() or not manifest.is_file():
        raise Refusal(f"fixture or manifest missing: {path.name}, {manifest.name}")
    listed = {
        parts[1]: parts[0]
        for parts in (line.split() for line in manifest.read_text(encoding="utf-8").splitlines())
        if len(parts) == 2
    }
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != pinned or listed.get(name) != pinned:
        raise Refusal(f"{name} is not the reviewed synthetic fixture (SHA-256 mismatch)")
    return data


def prices(model: str, input_tokens: int) -> tuple[float, float]:
    if model not in PRICES_USD_PER_MTOK:
        raise Refusal(f"no verified price for model {model!r}; the cost budget cannot be enforced")
    if model == "claude-haiku-5-5" and input_tokens > HAIKU_5_5_LONG_PROMPT[0]:
        return HAIKU_5_5_LONG_PROMPT[1]
    return PRICES_USD_PER_MTOK[model]


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    price_in, price_out = prices(model, input_tokens)
    return (input_tokens * price_in + output_tokens * price_out) / 1_000_000


@dataclass(frozen=True)
class Budget:
    model: str
    max_cost_usd: float
    max_input_tokens: int
    max_output_tokens: int

    def worst_case(self, input_chars: int) -> dict:
        """
        Planning bound: at most one token per character of the model input (typical English text is
        3-4 characters per token), and the full output allowance (thinking included).
        """
        input_bound = input_chars
        worst = cost_usd(self.model, input_bound, self.max_output_tokens)
        problems = []
        if input_bound > self.max_input_tokens:
            problems.append(f"input planning bound {input_bound} tokens exceeds --max-input-tokens {self.max_input_tokens}")
        if worst > self.max_cost_usd:
            problems.append(f"worst-case cost ${worst:.4f} exceeds --max-cost-usd ${self.max_cost_usd:.4f}")
        return {
            "model": self.model, "input_chars": input_chars, "input_tokens_planning_bound": input_bound,
            "max_output_tokens": self.max_output_tokens, "max_input_tokens": self.max_input_tokens,
            "price_usd_per_mtok": dict(zip(("input", "output"), prices(self.model, input_bound))),
            "price_source": PRICES_SOURCE, "worst_case_cost_usd": round(worst, 6),
            "max_cost_usd": self.max_cost_usd, "within_budget": not problems, "problems": problems,
        }


def live_preflight(args: argparse.Namespace, environ: dict) -> list[str]:
    """Every live prerequisite that can be checked before any import or file access."""
    problems = []
    if not args.authorize_paid_request:
        problems.append("--authorize-paid-request is required for --live")
    if not args.authorize_external_transfer:
        problems.append("--authorize-external-transfer is required for --live (the synthetic document text is sent to Anthropic)")
    if args.max_cost_usd is None:
        problems.append("--max-cost-usd must be given explicitly for --live")
    if args.keep_temp:
        problems.append("--keep-temp is not allowed with --live (the temporary data is always destroyed)")
    key = environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        problems.append("ANTHROPIC_API_KEY is not set in this process's environment (.env is never read)")
    elif not key.startswith("sk-ant-") or key != key.strip() or len(key) < 27:
        problems.append("ANTHROPIC_API_KEY does not have the format of an Anthropic key")
    return problems


class GuardedTransport(httpx2.AsyncBaseTransport):
    """
    Allows at most `max_requests` POSTs to https://api.anthropic.com/v1/messages through `inner`; any
    other request, a request beyond the limit, or any request while `inner` is None (dry run) is
    blocked before it leaves the process.
    """

    TARGET = ("https", "api.anthropic.com", "POST", "/v1/messages")

    def __init__(self, inner: Optional[httpx2.AsyncBaseTransport], max_requests: int = 1) -> None:
        self.inner = inner
        self.max_requests = max_requests
        self.sent = 0
        self.blocked: list[str] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        target = (request.url.scheme, request.url.host, request.method, request.url.path)
        reason = (
            "unexpected_target" if target != self.TARGET
            else "dry_run" if self.inner is None
            else "request_limit" if self.sent >= self.max_requests
            else None
        )
        if reason is not None:
            self.blocked.append(reason)
            raise httpx2.ConnectError(f"blocked by the live-check guard ({reason})", request=request)
        self.sent += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        if self.inner is not None:
            await self.inner.aclose()


def _live_transport() -> httpx2.AsyncBaseTransport:
    """The real network transport of a live run (no connection retries); always wrapped in the guard."""
    return httpx2.AsyncHTTPTransport(retries=0)


# ── Fake Messages API (offline) ──────────────────────────────────────────────


def _claim(field: str, page: Optional[int], quote: Optional[str], value: str = "Stated in the document.") -> dict:
    source = None if page is None and quote is None else {"page_number": page, "supporting_passage": quote}
    return {"field": field, "value": value, "is_unknown": False, "source": source}


def _grounded(**changes: tuple) -> list[dict]:
    claims = []
    for field, (page, quote) in GROUND_TRUTH.items():
        page, quote = changes.get(field, (page, quote))
        claims.append(_claim(field, page, quote))
    return claims


def _message(text: Optional[str], stop_reason: str = "end_turn", input_tokens: int = 1200,
             output_tokens: int = 900, stop_details: Optional[dict] = None) -> httpx2.Response:
    content = [{"type": "thinking", "thinking": "(fake)", "signature": "ZmFrZQ=="}]
    if text is not None:
        content.append({"type": "text", "text": text})
    body = {"id": "msg_fake_offline", "type": "message", "role": "assistant", "model": "fake-offline-model",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}}
    if stop_details is not None:
        body["stop_details"] = stop_details
    return httpx2.Response(200, json=body)


def _json(claims: list[dict]) -> str:
    return json.dumps({"claims": claims})


@dataclass(frozen=True)
class Scenario:
    name: str
    description: str
    expect: dict                       # expected outcome, compared with the report
    response: Any = None               # fake provider outcome (Response or exception)
    issuer: str = AUTHORIZED_ISSUER
    confirm: bool = True
    max_cost_usd: Optional[float] = None
    stale_confirmation: bool = False
    expected_pages: int = FIXTURE_PAGES   # what the harness verified; the server's summary must agree


SCENARIOS = {s.name: s for s in [
    Scenario("valid", "7 grounded claims, each quoted verbatim from its page",
             {"http_status": 201, "provider_requests": 1, "known_claims": 7, "page_grounded": 7, "unverified": 0},
             response=_message(_json(_grounded()))),
    Scenario("wrong_page", "a verbatim quotation cited on the wrong page",
             {"http_status": 201, "provider_requests": 1, "page_grounded": 6, "unverified": 1,
              "statuses": {"jurisdiction": "found_on_other_page"}},
             response=_message(_json(_grounded(jurisdiction=(2, GROUND_TRUTH["jurisdiction"][1]))))),
    Scenario("fabricated", "a legal claim whose quotation is not in the document",
             {"http_status": 201, "provider_requests": 1, "page_grounded": 6, "unverified": 1,
              "statuses": {"redemption_rights": "not_found"}},
             response=_message(_json(_grounded(redemption_rights=(3, "Unitholders may redeem their units monthly at net asset value."))))),
    Scenario("invented_page", "a claim citing page 9 of a 4-page document",
             {"http_status": 503, "ai_error": "ungrounded_output", "provider_requests": 1, "known_claims": 0},
             response=_message(_json(_grounded(jurisdiction=(9, GROUND_TRUTH["jurisdiction"][1]))))),
    Scenario("refusal", "the model declines (stop_reason refusal)",
             {"http_status": 503, "ai_error": "refusal", "provider_requests": 1, "known_claims": 0},
             response=_message(None, "refusal", output_tokens=0, stop_details={"type": "refusal", "category": None})),
    Scenario("malformed_json", "the response text is not JSON",
             {"http_status": 503, "ai_error": "invalid_output", "provider_requests": 1, "known_claims": 0},
             response=_message("Here are the claims: {not json")),
    Scenario("truncated", "the response hit max_tokens",
             {"http_status": 503, "ai_error": "truncated", "provider_requests": 1, "known_claims": 0},
             response=_message('{"claims": [', "max_tokens", output_tokens=8192)),
    Scenario("timeout", "no response within the timeout",
             {"http_status": 503, "ai_error": "timeout", "provider_requests": 1, "known_claims": 0},
             response=httpx2.ReadTimeout("fake timeout")),
    Scenario("invalid_credentials", "the provider rejects the API key (401)",
             {"http_status": 503, "ai_error": "authentication_failed", "provider_requests": 1, "known_claims": 0},
             response=httpx2.Response(401, json={"type": "error", "error": {"type": "authentication_error", "message": "fake"}})),
    Scenario("cost_limit", "the worst-case cost exceeds the budget: refused before confirming",
             {"http_status": 428, "refused_by_harness": "budget", "provider_requests": 0},
             response=_message(_json(_grounded())), max_cost_usd=0.001),
    Scenario("missing_consent", "the request is not confirmed",
             {"http_status": 428, "extraction_refusal": "external_processing_confirmation_required", "provider_requests": 0},
             response=_message(_json(_grounded())), confirm=False),
    Scenario("stale_confirmation", "a confirmation for version 1 after version 2 was uploaded",
             {"http_status": 409, "extraction_refusal": "confirmation_version_mismatch", "provider_requests": 0},
             response=_message(_json(_grounded())), stale_confirmation=True),
    Scenario("summary_mismatch", "the confirmation request does not describe the verified fixture",
             {"http_status": 428, "refused_by_harness": "summary_mismatch", "provider_requests": 0},
             response=_message(_json(_grounded())), expected_pages=FIXTURE_PAGES + 1),
    Scenario("missing_issuer_authorization", "the issuer has not authorized external AI processing",
             {"http_status": 403, "extraction_refusal": "external_processing_not_authorized", "provider_requests": 0},
             response=_message(_json(_grounded())), issuer=UNAUTHORIZED_ISSUER),
]}


def _fake_transport(outcome: Any) -> httpx2.MockTransport:
    def handle(request: httpx2.Request) -> httpx2.Response:
        if isinstance(outcome, Exception):
            raise outcome
        return httpx2.Response(outcome.status_code, headers={"content-type": "application/json"}, content=outcome.content)
    return httpx2.MockTransport(handle)


# ── Report helpers ───────────────────────────────────────────────────────────


def summarize_claims(extraction: Optional[dict]) -> dict:
    """Counts and statuses only: no value, no quotation, no page text."""
    if extraction is None:
        return {"total": 0, "known": 0, "unknown": 0, "page_grounded": 0, "unverified": 0,
                "on_expected_page": 0, "by_field": []}
    by_field = []
    for claim in sorted(extraction["claims"], key=lambda c: c["field_name"]):
        evidence = claim.get("evidence") or {}
        expected_page = GROUND_TRUTH.get(claim["field_name"], (None, None))[0]
        passage = claim.get("supporting_passage") or ""
        by_field.append({
            "field": claim["field_name"],
            "known": not claim["is_unknown"],
            "cited_page": claim.get("source_page"),
            "expected_page": expected_page,
            "evidence_status": evidence.get("status"),
            "found_on_pages": evidence.get("found_on_pages", []),
            "on_expected_page": (not claim["is_unknown"] and evidence.get("status") == "verified"
                                 and claim.get("source_page") == expected_page),
            "quotation_chars": len(passage),
            "quotation_sha256_prefix": hashlib.sha256(passage.encode("utf-8")).hexdigest()[:12] if passage else None,
        })
    known = [f for f in by_field if f["known"]]
    grounded = [f for f in known if f["evidence_status"] == "verified"]
    return {"total": len(by_field), "known": len(known), "unknown": len(by_field) - len(known),
            "page_grounded": len(grounded), "unverified": len(known) - len(grounded),
            "on_expected_page": sum(f["on_expected_page"] for f in by_field), "by_field": by_field}


def scenario_check(report: dict, expect: dict) -> list[str]:
    observed = {
        "http_status": report["outcome"]["http_status"],
        "ai_error": report["outcome"].get("ai_error"),
        "extraction_refusal": report["outcome"].get("extraction_refusal"),
        "refused_by_harness": report["outcome"].get("refused_by_harness"),
        "provider_requests": report["provider"]["requests_sent"],
        "known_claims": report["claims"]["known"],
        "page_grounded": report["claims"]["page_grounded"],
        "unverified": report["claims"]["unverified"],
    }
    mismatches = [f"{key}: expected {value!r}, observed {observed[key]!r}"
                  for key, value in expect.items() if key != "statuses" and observed[key] != value]
    statuses = {f["field"]: f["evidence_status"] for f in report["claims"]["by_field"]}
    mismatches += [f"{field}: expected {status}, observed {statuses.get(field)}"
                   for field, status in expect.get("statuses", {}).items() if statuses.get(field) != status]
    if report["approval"]["approved_automatically"]:
        mismatches.append("an extraction was approved automatically")
    if report["provider"]["blocked_requests"] and report["mode"] != "dry_run":
        mismatches.append(f"blocked requests: {report['provider']['blocked_requests']}")
    return mismatches


# ── Isolated environment ─────────────────────────────────────────────────────


@dataclass
class Environment:
    root: Path
    admin_token: str
    reviewer_token: str


def prepare_environment(root: Path, mode: str, args: argparse.Namespace) -> Environment:
    """
    Fills the temporary directory `root` and points this process at it: environment variables are
    set before any application module is imported, and the working directory becomes the temporary
    directory, so that the repository's .env is never loaded.
    """
    env = Environment(root=root, admin_token=secrets.token_urlsafe(32), reviewer_token=secrets.token_urlsafe(32))
    os.environ.update({
        "APP_ENV": "ai-live-check",
        "DATABASE_URL": f"sqlite+aiosqlite:///{(root / 'live_check.db').as_posix()}",
        "STORAGE_PATH": str(root / "storage"),
        "ACCESS_CONTROL_FILE": str(root / "access_control.json"),
        "STELLAR_ANCHOR_SECRET_FILE": str(root / "no-anchor-account.json"),   # anchoring stays disabled
        "AI_PROVIDER": "anthropic",
        "AI_MODEL": args.model,
        "AI_MAX_OUTPUT_TOKENS": str(args.max_output_tokens),
        "AI_MAX_INPUT_CHARS": str(max(10_000, args.max_input_tokens)),
        "AI_MAX_PAGES": "10",
        "AI_REQUEST_TIMEOUT_SECONDS": str(LIVE_TIMEOUT_SECONDS),
    })
    if mode != "live":
        os.environ["ANTHROPIC_API_KEY"] = FAKE_KEY   # an operator's real key is never used offline
    os.chdir(root)
    sys.path.insert(0, str(ROOT))
    from app.access_registry import hash_token  # noqa: E402 — after the environment is set
    (root / "access_control.json").write_text(json.dumps({
        "issuers": [
            {"id": AUTHORIZED_ISSUER, "name": "AI Live Check Fictional Issuer", "external_ai_processing": True},
            {"id": UNAUTHORIZED_ISSUER, "name": "AI Live Check Issuer Without AI Consent"},
        ],
        "users": [
            {"user_id": "ai-check-operator", "display_name": "Live Check Operator", "role": "platform_admin",
             "token_sha256": hash_token(env.admin_token)},
            {"user_id": "ai-check-reviewer", "display_name": "Live Check Reviewer", "role": "issuer_reviewer",
             "issuer_ids": [AUTHORIZED_ISSUER, UNAUTHORIZED_ISSUER], "token_sha256": hash_token(env.reviewer_token)},
        ],
    }), encoding="utf-8")
    return env


def _redact(text: str) -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    return text.replace(key, "[redacted]") if key else text


def _sha256_file(path: Path) -> Optional[str]:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


# ── The check ────────────────────────────────────────────────────────────────


class _AuditEvents(logging.Handler):
    """Collects the names of security log events (the first word of each message; content-free)."""

    def __init__(self) -> None:
        super().__init__(level=logging.INFO)
        self.events: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(record.getMessage().split(" ", 1)[0])


async def _run_scenario(client, headers: dict, app, build_ai_service, get_ai_service, settings, index: int,
                        fixture: bytes, budget: Budget, mode: str, scenario: Optional[Scenario],
                        live_inner: Optional[httpx2.AsyncBaseTransport], include_claim_values: bool = False) -> dict:
    from stellar_sdk import Keypair  # offline key generation; no network

    started = time.monotonic()
    issuer = scenario.issuer if scenario else AUTHORIZED_ISSUER
    report: dict = {
        "mode": mode if scenario is None else f"fake:{scenario.name}",
        "scenario": None if scenario is None else {"name": scenario.name, "description": scenario.description},
        "fixture": {"name": FIXTURE_NAME, "sha256": FIXTURE_SHA256, "verified": True},
        "gates": {}, "outcome": {"http_status": None}, "claims": summarize_claims(None),
        "approval": {"status": None, "approved_automatically": False, "approvals": 0},
        "human_review_required": True,
        "human_review_reasons": [
            "claim values are AI-generated and may be wrong or incomplete",
            "a verified quotation proves only that the text appears on the cited page",
            "approval is a separate, human decision; this harness never approves",
        ],
    }

    asset = await client.post("/assets", headers=headers["admin"], json={
        "issuer_id": issuer, "blockchain_network": "stellar-testnet", "stellar_asset_code": f"AILC{index:02d}",
        "stellar_issuer_public_key": Keypair.random().public_key, "issuer_organization_name": "Fictional Issuer",
        "asset_type": "real_estate", "jurisdiction": "GB",
    })
    if asset.status_code != 201:
        raise RuntimeError(f"asset registration failed (HTTP {asset.status_code})")
    upload = await client.post(f"/assets/{asset.json()['id']}/documents", headers=headers["reviewer"],
                               data={"document_type": "prospectus", "title": "Synthetic prospectus"},
                               files={"file": (FIXTURE_NAME, fixture, "application/pdf")})
    if upload.status_code != 201:
        raise RuntimeError(f"fixture upload failed (HTTP {upload.status_code})")
    document = upload.json()
    version = document["current_version"]
    layer = version.get("text_layer") or {}
    report["fixture"].update(stored_sha256_matches=version["sha256_hash"] == FIXTURE_SHA256,
                             pages=layer.get("page_count"), pages_without_text=layer.get("pages_without_text"))

    outcome = scenario.response if scenario else None
    inner = _fake_transport(outcome) if scenario else live_inner
    guard = GuardedTransport(inner, max_requests=1)
    service = build_ai_service(settings, http_client=httpx2.AsyncClient(transport=guard))
    if not getattr(service, "available", False):
        raise Refusal(f"the AI service is not available in the test environment ({getattr(service, 'code', '?')})")
    app.dependency_overrides[get_ai_service] = lambda: service
    url = f"/documents/{document['id']}/extractions"

    try:
        # 1. Without confirmation: the issuer authorization is checked, then the confirmation requested
        first = await client.post(url, headers=headers["reviewer"])
        body = first.json()
        report["gates"]["issuer_authorization"] = "refused" if first.status_code == 403 else "passed"
        report["outcome"].update(http_status=first.status_code, extraction_refusal=body.get("extraction_refusal"))
        summary = body.get("external_processing")
        if first.status_code == 428 and summary:
            report["gates"]["confirmation_requested"] = True
            expected = {"processor": "Anthropic", "model": budget.model, "version_id": version["id"],
                        "page_count": scenario.expected_pages if scenario else FIXTURE_PAGES,
                        "pages_without_text": []}
            mismatched = sorted(k for k, v in expected.items() if summary.get(k) != v)
            report["gates"]["summary_matches_fixture"] = not mismatched
            report["budget"] = budget_report = (
                Budget(budget.model, scenario.max_cost_usd, budget.max_input_tokens, budget.max_output_tokens)
                if scenario and scenario.max_cost_usd is not None else budget
            ).worst_case(summary["input_chars"])
            if summary.get("max_output_tokens") != budget.max_output_tokens:
                mismatched.append("max_output_tokens")
            if mismatched:
                report["outcome"]["refused_by_harness"] = "summary_mismatch"
                report["outcome"]["mismatched"] = mismatched
            elif not budget_report["within_budget"]:
                report["outcome"]["refused_by_harness"] = "budget"
            elif mode == "dry_run":
                report["outcome"]["stopped"] = "dry run: stopped before confirming; nothing was sent"
            elif scenario is None or scenario.confirm:
                # 2. The explicit confirmation of exactly this version: the one request
                if scenario and scenario.stale_confirmation:
                    newer = await client.post(f"/documents/{document['id']}/versions", headers=headers["reviewer"],
                                              files={"file": (FIXTURE_NAME, fixture, "application/pdf")})
                    if newer.status_code != 201:
                        raise RuntimeError(f"second version upload failed (HTTP {newer.status_code})")
                report["gates"]["confirmed_version_id"] = version["id"]
                second = await client.post(url, headers=headers["reviewer"],
                                           json={"confirm_external_processing": True, "version_id": version["id"]})
                body = second.json()
                report["outcome"].update(http_status=second.status_code, ai_error=body.get("ai_error"),
                                         extraction_refusal=body.get("extraction_refusal"))
                if second.status_code == 201:
                    extraction = (await client.get(f"/extractions/{body['id']}", headers=headers["reviewer"])).json()
                    report["outcome"]["extraction_id_present"] = True
                    report["claims"] = summarize_claims(extraction)
                    if include_claim_values:
                        # For the human evaluation of the values; written to the report file only
                        report["claim_text"] = [
                            {"field": c["field_name"], "value": c["value"], "supporting_passage": c["supporting_passage"]}
                            for c in sorted(extraction["claims"], key=lambda c: c["field_name"])
                        ]
                    report["approval"].update(
                        status=extraction["status"],
                        approvals=len(extraction["approval_state"]["current_approval_types"]),
                        approved_automatically=extraction["status"] != "draft"
                        or bool(extraction["approval_state"]["current_approval_types"]),
                    )
    finally:
        app.dependency_overrides.pop(get_ai_service, None)
        await service._client.close()

    exchange = getattr(service, "last_exchange", None)
    usage = None if exchange is None else {"input_tokens": exchange.input_tokens, "output_tokens": exchange.output_tokens}
    report["provider"] = {
        "kind": "fake (offline)" if scenario else ("none (dry run)" if mode == "dry_run" else "anthropic (live)"),
        "model_requested": budget.model,
        "model_reported": None if scenario or exchange is None else exchange.model,
        "request_limit": guard.max_requests, "requests_sent": guard.sent, "blocked_requests": guard.blocked,
        "service_attempts": service.requests_attempted, "stop_reason": None if exchange is None else exchange.stop_reason,
        "usage": usage,
    }
    # No fallback is requested, so the provider must answer with exactly the requested model
    reported = report["provider"]["model_reported"]
    report["provider"]["model_matches_request"] = None if reported is None else reported == budget.model
    if usage and usage["input_tokens"] is not None and usage["output_tokens"] is not None:
        report["cost_usd"] = {
            "basis": "fake usage (offline, nothing billed)" if scenario else "provider-reported usage",
            "amount": round(cost_usd(budget.model, usage["input_tokens"], usage["output_tokens"]), 6),
        }
    else:
        report["cost_usd"] = {"basis": "no provider response", "amount": 0.0}
    # Rejections of the model output and unverified claims; gate refusals are reported in `outcome`
    report["validation_errors"] = sorted({
        *([report["outcome"]["ai_error"]] if report["outcome"].get("ai_error") else []),
        *(["model_reported_differs_from_request"] if report["provider"]["model_matches_request"] is False else []),
        *(f"{f['field']}:{f['evidence_status']}" for f in report["claims"]["by_field"]
          if f["known"] and f["evidence_status"] != "verified"),
    })
    report["duration_seconds"] = round(time.monotonic() - started, 3)
    if scenario:
        report["expected"] = scenario.expect
        report["mismatches"] = scenario_check(report, scenario.expect)
        report["passed"] = not report["mismatches"]
    return report


async def run_check(mode: str, args: argparse.Namespace, env: Environment, fixture: bytes,
                    scenarios: list[Scenario]) -> dict:
    from httpx import ASGITransport, AsyncClient  # noqa: E402

    from app.config import settings  # noqa: E402
    from app.db.session import engine  # noqa: E402
    from app.main import app, lifespan  # noqa: E402
    from app.services.ai import build_ai_service, get_ai_service  # noqa: E402

    # Isolation, checked on the loaded configuration itself (fail-closed)
    database = Path(settings.database_url.split("///", 1)[-1]).resolve()
    if env.root.resolve() not in database.parents or database == DEV_DATABASE.resolve():
        raise Refusal("the database is not inside the temporary environment")
    if (settings.ai_provider, settings.ai_model, settings.ai_max_output_tokens) != (
            "anthropic", args.model, args.max_output_tokens):
        raise Refusal("the AI configuration of the temporary environment did not load as requested")
    if not settings.anthropic_api_key_configured:
        raise Refusal("ANTHROPIC_API_KEY is not a well-formed Anthropic key")

    audit = _AuditEvents()
    security = logging.getLogger("rwa_security")
    security.addHandler(audit)
    security.setLevel(logging.INFO)
    security.propagate = False
    logging.getLogger("python_multipart").setLevel(logging.ERROR)   # upload parser chatter
    budget = Budget(args.model, args.max_cost_usd if args.max_cost_usd is not None else DEFAULT_MAX_COST_USD,
                    args.max_input_tokens, args.max_output_tokens)
    started = time.monotonic()
    reports = []
    live_inner = _live_transport() if mode == "live" else None
    try:
        async with lifespan(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://ai-live-check") as client:
                headers = {"admin": {"Authorization": f"Bearer {env.admin_token}"},
                           "reviewer": {"Authorization": f"Bearer {env.reviewer_token}"}}
                for index, scenario in enumerate(scenarios or [None]):
                    reports.append(await _run_scenario(
                        client, headers, app, build_ai_service, get_ai_service, settings, index, fixture, budget,
                        mode, scenario, live_inner, args.include_claim_values))
    finally:
        if live_inner is not None:
            await live_inner.aclose()
        await engine.dispose()
    return {
        "harness": "scripts/ai_live_check.py", "report_version": 1, "mode": mode,
        "model": args.model, "duration_seconds": round(time.monotonic() - started, 3),
        "runs": reports, "audit_events": sorted(set(audit.events)),
        "real_ai_extraction_verified": False,
    }


# ── CLI ──────────────────────────────────────────────────────────────────────


def _format(result: dict) -> str:
    lines = [f"AI live check - mode {result['mode']}, model {result['model']}, {result['duration_seconds']} s"]
    for run in result["runs"]:
        budget = run.get("budget") or {}
        claims, provider, outcome = run["claims"], run["provider"], run["outcome"]
        status = "" if "passed" not in run else ("  [as expected]" if run["passed"] else "  [UNEXPECTED]")
        lines += [
            f"- {run['mode']}{status}",
            f"  fixture {run['fixture']['name']} sha256 {run['fixture']['sha256'][:16]}... verified, "
            f"{run['fixture'].get('pages')} pages, pages without text {run['fixture'].get('pages_without_text')}",
            f"  gates: issuer {run['gates'].get('issuer_authorization')}, confirmation requested "
            f"{run['gates'].get('confirmation_requested', False)}, confirmed {bool(run['gates'].get('confirmed_version_id'))}",
        ]
        if budget:
            lines.append(f"  budget: input {budget['input_chars']} chars (<= {budget['input_tokens_planning_bound']} tokens "
                         f"planned), output <= {budget['max_output_tokens']} tokens, worst case "
                         f"${budget['worst_case_cost_usd']:.4f} vs limit ${budget['max_cost_usd']:.4f}"
                         f" -> {'within' if budget['within_budget'] else 'EXCEEDS'} budget")
        lines += [
            f"  provider: {provider['kind']}, requests sent {provider['requests_sent']}, blocked {provider['blocked_requests']}, "
            f"usage {provider['usage']}, cost ${run['cost_usd']['amount']:.4f} ({run['cost_usd']['basis']})"
            + (f", model reported {provider['model_reported']} (matches request: {provider['model_matches_request']})"
               if provider["model_reported"] is not None else ""),
            f"  outcome: HTTP {outcome['http_status']}"
            + "".join(f", {k} {outcome[k]}" for k in ("ai_error", "extraction_refusal", "refused_by_harness", "stopped")
                      if outcome.get(k)),
            f"  claims: {claims['known']} known, {claims['page_grounded']} page-grounded, {claims['unverified']} unverified, "
            f"{claims['on_expected_page']} on the expected page; approval {run['approval']['status']}, "
            f"approved automatically {run['approval']['approved_automatically']}",
            f"  validation errors: {', '.join(run['validation_errors']) or 'none'}; human review required: yes",
        ]
        for mismatch in run.get("mismatches", []):
            lines.append(f"  MISMATCH {mismatch}")
    lines.append(f"development database unchanged: {result.get('development_database_unchanged')}; "
                 f"temporary environment destroyed: {result.get('temporary_environment_destroyed')}")
    lines.append("real AI extraction verified: no (a live result needs human evaluation)")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled live AI extraction check (AI_LIVE_TEST.md)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="everything up to the confirmation; no AI request")
    mode.add_argument("--fake-provider", choices=[*SCENARIOS, "all"], help="offline fake Messages API")
    mode.add_argument("--live", action="store_true", help="exactly one real request to Anthropic")
    parser.add_argument("--authorize-paid-request", action="store_true")
    parser.add_argument("--authorize-external-transfer", action="store_true")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--max-cost-usd", type=float, default=None)
    parser.add_argument("--max-input-tokens", type=int, default=DEFAULT_MAX_INPUT_TOKENS)
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS)
    parser.add_argument("--json", action="store_true", help="print the JSON report instead of the summary")
    parser.add_argument("--report-file", type=Path, help="also write the JSON report to this file")
    parser.add_argument("--keep-temp", action="store_true", help="keep the temporary environment (offline modes)")
    parser.add_argument("--include-claim-values", action="store_true",
                        help="write the claim values and quotations (synthetic fixture only) to --report-file, never to the console")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if args.report_file is not None:
        args.report_file = args.report_file.resolve()   # before the working directory changes
    mode = "dry_run" if args.dry_run else "fake" if args.fake_provider else "live" if args.live else None
    try:
        if mode is None:
            raise Refusal("disabled by default: choose --dry-run, --fake-provider SCENARIO|all or --live")
        if mode != "live" and (args.authorize_paid_request or args.authorize_external_transfer):
            raise Refusal("the authorization flags are only valid with --live")
        if args.include_claim_values and args.report_file is None:
            raise Refusal("--include-claim-values needs --report-file (claim text is never printed)")
        if mode == "live":
            problems = live_preflight(args, dict(os.environ))
            if problems:
                raise Refusal("; ".join(problems))
        if not 1024 <= args.max_output_tokens <= 16_000:
            raise Refusal("--max-output-tokens must be between 1024 and 16000")
        if args.max_input_tokens < 1 or (args.max_cost_usd is not None and args.max_cost_usd <= 0):
            raise Refusal("--max-input-tokens and --max-cost-usd must be positive")
        prices(args.model, 0)   # a model without a verified price is refused
        fixture = verify_fixture()
        # The cost budget is checked against the server's own input size, before confirming
        scenarios = [] if mode != "fake" else (
            list(SCENARIOS.values()) if args.fake_provider == "all" else [SCENARIOS[args.fake_provider]])
    except Refusal as exc:
        print(f"REFUSED: {exc}. Nothing was sent.", file=sys.stderr)
        return 2

    dev_before = _sha256_file(DEV_DATABASE)
    # Created here, so that it is removed whatever fails afterwards (even loading the configuration)
    root = Path(tempfile.mkdtemp(prefix="rwa-ai-live-check-"))
    result = None
    exit_code = 0
    try:
        env = prepare_environment(root, mode, args)
        result = asyncio.run(run_check(mode, args, env, fixture, scenarios))
    except Refusal as exc:
        print(f"REFUSED: {exc}. Nothing was sent.", file=sys.stderr)
        result, exit_code = None, 2
    except Exception as exc:  # noqa: BLE001 — the failure, with any key redacted
        print(f"FAILED: {type(exc).__name__}: {_redact(str(exc))}", file=sys.stderr)
        result, exit_code = None, 1
    finally:
        os.chdir(ROOT)
        destroyed = False
        if not args.keep_temp:
            shutil.rmtree(root, ignore_errors=True)
            destroyed = not root.exists()
        if not destroyed and result is None:
            print(f"temporary environment not removed: {root}", file=sys.stderr)
    dev_unchanged = _sha256_file(DEV_DATABASE) == dev_before
    if result is None:
        return exit_code
    result.update(development_database_unchanged=dev_unchanged, temporary_environment_destroyed=destroyed,
                  temporary_environment=None if destroyed else str(root))
    if args.report_file:
        args.report_file.parent.mkdir(parents=True, exist_ok=True)
        args.report_file.write_text(json.dumps(result, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")
    for run in result["runs"]:
        run.pop("claim_text", None)   # the console never shows claim text
    print(json.dumps(result, indent=2, ensure_ascii=True) if args.json else _format(result))
    if not dev_unchanged:
        return 1
    if mode == "fake":
        return 0 if all(run["passed"] for run in result["runs"]) else 1
    run = result["runs"][0]
    if run["outcome"].get("refused_by_harness"):
        return 2
    if mode == "dry_run":
        ok = (run["provider"]["requests_sent"] == 0 and run["gates"].get("confirmation_requested")
              and "stopped" in run["outcome"])
        return 0 if ok else 1
    return 0 if run["provider"]["requests_sent"] == 1 else 1


if __name__ == "__main__":
    sys.exit(main())
