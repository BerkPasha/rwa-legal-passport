"""
Stage 10A-3 — the controlled live AI extraction check (scripts/ai_live_check.py, AI_LIVE_TEST.md).

Everything here is offline. The end-to-end runs are subprocesses, as an operator runs the script:
a fresh interpreter whose configuration comes only from the temporary environment. No subprocess
has an ANTHROPIC_API_KEY of its own, and the one live-path run replaces the network transport with
an in-process fake and makes the real transport impossible to construct.

Coverage:
 1. Disabled by default; every live prerequisite is required (fail-closed, nothing sent)
 2. Fixture verification (pinned digest and SHA256SUMS)
 3. Budget: verified prices, planning bound, input/output/cost limits
 4. The guard transport: one request to the Messages API at most, nothing else
 5. Dry run: every gate up to the confirmation request, zero requests
 6. Fake provider: every scenario, through the real route and gates
 7. The live code path, offline: exactly one guarded request, key never shown
 8. Reports contain no document text, quotation, claim value, key or credential
"""

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx2
import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "ai_live_check.py"
_spec = importlib.util.spec_from_file_location("ai_live_check", SCRIPT)
check = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check)

FIXTURE = ROOT / "tests" / "fixtures" / "real_pdfs" / "trust_prospectus.pdf"
# Text of the synthetic fixture that must never appear in any output
PAGE_TEXT = ["Harbour Point", "legal title remains vested", "Net rental income", "exclusive jurisdiction",
             "Digital Unit", "redemption facility"]
LIVE_KEY = "sk-ant-fictional-live-path-test-key-0000000000"   # format only; the transport is a fake


def _env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "AI_PROVIDER", "DATABASE_URL")}
    env.update(extra)
    return env


# Every run goes through this driver: the script's own main(), in a fresh interpreter, with the real
# network transport made impossible to construct, so that no test can ever reach the network
_DRIVER = """
import json, sys, httpx2, importlib.util
class NoNetwork:
    def __init__(self, *a, **k):
        raise AssertionError("a real network transport must not be created in this test")
httpx2.AsyncHTTPTransport = NoNetwork
spec = importlib.util.spec_from_file_location("ai_live_check", {script!r})
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)
sys.exit(check.main(json.loads({args!r})))
"""


def _run(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    driver = _DRIVER.format(script=str(SCRIPT), args=json.dumps(list(args)))
    return subprocess.run([sys.executable, "-c", driver], cwd=ROOT, env=env or _env(),
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def _assert_no_content(text: str) -> None:
    for phrase in PAGE_TEXT:
        assert phrase not in text, phrase
    for quote in (q for _, q in check.GROUND_TRUTH.values()):
        assert quote not in text
    assert check.FAKE_KEY not in text and LIVE_KEY not in text
    assert "Stated in the document." not in text            # the fake claim value
    assert "token_sha256" not in text and "Bearer" not in text


def _dev_db_hash() -> str | None:
    db = ROOT / "rwa_legal_passport.db"
    return check._sha256_file(db)


# ── 1. Disabled by default; live prerequisites ───────────────────────────────


def test_without_a_mode_nothing_happens():
    r = _run()
    assert r.returncode == 2
    assert "disabled by default" in r.stderr and "Nothing was sent" in r.stderr
    assert r.stdout == ""


@pytest.mark.parametrize("args, env, message", [
    (["--live"], {}, "--authorize-paid-request is required"),
    (["--live", "--authorize-paid-request", "--max-cost-usd", "0.25"], {"ANTHROPIC_API_KEY": LIVE_KEY},
     "--authorize-external-transfer is required"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer"], {"ANTHROPIC_API_KEY": LIVE_KEY},
     "--max-cost-usd must be given explicitly"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25"], {},
     "ANTHROPIC_API_KEY is not set"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25"],
     {"ANTHROPIC_API_KEY": "your-anthropic-api-key-here"}, "does not have the format"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25", "--keep-temp"],
     {"ANTHROPIC_API_KEY": LIVE_KEY}, "--keep-temp is not allowed"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25",
      "--model", "claude-fable-5-1"], {"ANTHROPIC_API_KEY": LIVE_KEY}, "no verified price"),
    (["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25",
      "--max-output-tokens", "32000"], {"ANTHROPIC_API_KEY": LIVE_KEY}, "--max-output-tokens must be between"),
    (["--dry-run", "--authorize-paid-request"], {}, "only valid with --live"),
])
def test_live_prerequisites_are_all_required(args, env, message):
    """Each run is refused before any import of the application, so nothing can be sent."""
    r = _run(*args, env=_env(**env))
    assert r.returncode == 2, r.stderr
    assert message in r.stderr and "Nothing was sent" in r.stderr
    assert LIVE_KEY not in r.stderr + r.stdout


def test_the_preflight_lists_every_missing_live_prerequisite():
    args = check.build_parser().parse_args(["--live"])
    problems = check.live_preflight(args, {})
    assert len(problems) == 4   # both flags, the explicit budget and the key
    ok = check.build_parser().parse_args(
        ["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.1"])
    assert check.live_preflight(ok, {"ANTHROPIC_API_KEY": LIVE_KEY}) == []
    assert check.live_preflight(ok, {"ANTHROPIC_API_KEY": f" {LIVE_KEY}"}) != []


# ── 2. Fixture verification ──────────────────────────────────────────────────


def test_only_the_reviewed_synthetic_fixture_is_accepted(tmp_path: Path):
    assert check.verify_fixture() == FIXTURE.read_bytes()
    manifest = (FIXTURE.parent / "SHA256SUMS").read_text(encoding="utf-8")
    (tmp_path / "SHA256SUMS").write_text(manifest, encoding="utf-8")
    (tmp_path / check.FIXTURE_NAME).write_bytes(FIXTURE.read_bytes() + b"\n")          # altered file
    with pytest.raises(check.Refusal, match="SHA-256 mismatch"):
        check.verify_fixture(tmp_path)
    (tmp_path / check.FIXTURE_NAME).write_bytes(FIXTURE.read_bytes())
    (tmp_path / "SHA256SUMS").write_text(manifest.replace(check.FIXTURE_SHA256, "0" * 64), encoding="utf-8")
    with pytest.raises(check.Refusal, match="SHA-256 mismatch"):                         # altered manifest
        check.verify_fixture(tmp_path)
    with pytest.raises(check.Refusal, match="SHA-256 mismatch"):                         # another pin
        check.verify_fixture(FIXTURE.parent, pinned="1" * 64)
    with pytest.raises(check.Refusal, match="missing"):
        check.verify_fixture(tmp_path / "absent")


# ── 3. Budget ────────────────────────────────────────────────────────────────


def test_prices_are_known_for_every_supported_model_and_refused_otherwise():
    from app.config import SUPPORTED_AI_MODELS
    assert set(check.PRICES_USD_PER_MTOK) == set(SUPPORTED_AI_MODELS)
    assert check.prices("claude-opus-5-5", 0) == (4.00, 20.00)
    assert check.prices("claude-sonnet-4-6", 0) == check.prices("claude-sonnet-4-6", 900_000) == (3.00, 15.00)
    assert check.prices("claude-haiku-5-5", 100_000) == (0.10, 0.50)
    assert check.prices("claude-haiku-5-5", 100_001) == (0.50, 2.50)
    with pytest.raises(check.Refusal, match="no verified price"):
        check.prices("claude-fable-5-1", 0)


def test_the_worst_case_is_the_full_input_bound_plus_the_full_output_allowance():
    budget = check.Budget("claude-opus-5-5", max_cost_usd=0.25, max_input_tokens=10_000, max_output_tokens=8192)
    plan = budget.worst_case(3562)
    assert plan["input_tokens_planning_bound"] == 3562
    assert plan["worst_case_cost_usd"] == pytest.approx((3562 * 4 + 8192 * 20) / 1_000_000)
    assert plan["within_budget"] is True and plan["problems"] == []
    assert check.Budget("claude-opus-5-5", 0.10, 10_000, 8192).worst_case(3562)["within_budget"] is False
    over_input = check.Budget("claude-opus-5-5", 5.0, 3000, 8192).worst_case(3562)
    assert over_input["within_budget"] is False and "exceeds --max-input-tokens" in over_input["problems"][0]
    assert check.cost_usd("claude-sonnet-5-5", 1000, 1000) == pytest.approx(0.012)


# ── 4. Guard transport ───────────────────────────────────────────────────────


async def test_the_guard_allows_one_messages_request_and_blocks_everything_else():
    seen = []
    inner = httpx2.MockTransport(lambda request: seen.append(request.url) or httpx2.Response(200, json={}))
    guard = check.GuardedTransport(inner, max_requests=1)
    async with httpx2.AsyncClient(transport=guard) as client:
        for url in ("https://api.anthropic.com/v1/models/claude-opus-5-5", "http://api.anthropic.com/v1/messages",
                    "https://example.com/v1/messages"):
            with pytest.raises(httpx2.ConnectError, match="unexpected_target"):
                await client.post(url) if "messages" in url else await client.get(url)
        assert (await client.post("https://api.anthropic.com/v1/messages", json={})).status_code == 200
        with pytest.raises(httpx2.ConnectError, match="request_limit"):
            await client.post("https://api.anthropic.com/v1/messages", json={})
    assert guard.sent == 1 and len(seen) == 1
    assert guard.blocked == ["unexpected_target"] * 3 + ["request_limit"]

    dry = check.GuardedTransport(None)
    async with httpx2.AsyncClient(transport=dry) as client:
        with pytest.raises(httpx2.ConnectError, match="dry_run"):
            await client.post("https://api.anthropic.com/v1/messages", json={})
    assert dry.sent == 0 and dry.blocked == ["dry_run"]


# ── 5. Dry run ───────────────────────────────────────────────────────────────


def test_the_dry_run_passes_every_gate_up_to_the_confirmation_and_sends_nothing():
    before = _dev_db_hash()
    r = _run("--dry-run", "--json")
    assert r.returncode == 0, r.stderr
    report = json.loads(r.stdout)
    run = report["runs"][0]
    assert run["provider"]["requests_sent"] == 0 and run["provider"]["blocked_requests"] == []
    assert run["provider"]["service_attempts"] == 0 and run["provider"]["request_limit"] == 1
    assert run["gates"] == {"issuer_authorization": "passed", "confirmation_requested": True, "summary_matches_fixture": True}
    assert run["outcome"]["http_status"] == 428 and "stopped" in run["outcome"]
    assert run["fixture"] == {"name": "trust_prospectus.pdf", "sha256": check.FIXTURE_SHA256, "verified": True,
                              "stored_sha256_matches": True, "pages": 4, "pages_without_text": []}
    assert run["budget"]["input_chars"] == 3562 and run["budget"]["within_budget"] is True
    assert report["development_database_unchanged"] is True and report["temporary_environment_destroyed"] is True
    assert report["real_ai_extraction_verified"] is False and run["human_review_required"] is True
    assert "extraction.external_processing_confirmed" not in report["audit_events"]
    assert _dev_db_hash() == before
    _assert_no_content(r.stdout + r.stderr)


def test_a_budget_below_the_worst_case_refuses_the_dry_run():
    r = _run("--dry-run", "--max-cost-usd", "0.05", "--json")
    assert r.returncode == 2
    run = json.loads(r.stdout)["runs"][0]
    assert run["outcome"]["refused_by_harness"] == "budget" and run["provider"]["requests_sent"] == 0


# ── 6. Fake provider ─────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def fake_report() -> dict:
    before = _dev_db_hash()
    r = _run("--fake-provider", "all", "--json")
    assert r.returncode == 0, r.stderr + r.stdout[-2000:]
    _assert_no_content(r.stdout + r.stderr)
    report = json.loads(r.stdout)
    assert _dev_db_hash() == before and report["development_database_unchanged"] is True
    return report


def test_every_scenario_has_its_expected_outcome(fake_report: dict):
    runs = {run["scenario"]["name"]: run for run in fake_report["runs"]}
    assert set(runs) == set(check.SCENARIOS)
    assert all(run["passed"] and run["mismatches"] == [] for run in runs.values())
    assert all(run["provider"]["request_limit"] == 1 for run in runs.values())
    assert fake_report["temporary_environment_destroyed"] is True
    assert fake_report["real_ai_extraction_verified"] is False


def test_the_valid_scenario_reports_a_grounded_draft(fake_report: dict):
    run = next(r for r in fake_report["runs"] if r["scenario"]["name"] == "valid")
    assert run["claims"]["known"] == 7 and run["claims"]["page_grounded"] == 7 and run["claims"]["on_expected_page"] == 7
    assert run["approval"] == {"status": "draft", "approved_automatically": False, "approvals": 0}
    assert run["provider"]["usage"] == {"input_tokens": 1200, "output_tokens": 900}
    assert run["cost_usd"]["amount"] == pytest.approx((1200 * 4 + 900 * 20) / 1_000_000)
    assert run["validation_errors"] == [] and run["human_review_required"] is True
    for field in run["claims"]["by_field"]:
        assert set(field) == {"field", "known", "cited_page", "expected_page", "evidence_status", "found_on_pages",
                              "on_expected_page", "quotation_chars", "quotation_sha256_prefix"}   # no value, no quote


@pytest.mark.parametrize("name, errors", [
    ("wrong_page", ["jurisdiction:found_on_other_page"]),
    ("fabricated", ["redemption_rights:not_found"]),
    ("invented_page", ["ungrounded_output"]),
    ("refusal", ["refusal"]),
    ("malformed_json", ["invalid_output"]),
    ("truncated", ["truncated"]),
    ("timeout", ["timeout"]),
    ("invalid_credentials", ["authentication_failed"]),
])
def test_rejections_and_unverified_claims_are_reported(fake_report: dict, name: str, errors: list):
    run = next(r for r in fake_report["runs"] if r["scenario"]["name"] == name)
    assert run["validation_errors"] == errors
    assert run["provider"]["requests_sent"] == 1 and run["provider"]["service_attempts"] == 1
    assert run["approval"]["approved_automatically"] is False


@pytest.mark.parametrize("name, status, code", [
    ("cost_limit", 428, None),
    ("summary_mismatch", 428, None),
    ("missing_consent", 428, "external_processing_confirmation_required"),
    ("stale_confirmation", 409, "confirmation_version_mismatch"),
    ("missing_issuer_authorization", 403, "external_processing_not_authorized"),
])
def test_refused_runs_send_nothing(fake_report: dict, name: str, status: int, code):
    run = next(r for r in fake_report["runs"] if r["scenario"]["name"] == name)
    assert run["outcome"]["http_status"] == status
    if code:
        assert run["outcome"]["extraction_refusal"] == code
    assert run["provider"]["requests_sent"] == 0 and run["provider"]["service_attempts"] == 0
    assert run["cost_usd"] == {"basis": "no provider response", "amount": 0.0}


def test_audit_events_are_recorded(fake_report: dict):
    assert {"access_config.loaded", "ai_provider.enabled", "extraction.external_processing_confirmed",
            "extraction.ai_output_rejected", "extraction.refused_external_not_authorized"} <= set(fake_report["audit_events"])


def test_claim_values_go_only_to_the_report_file_and_only_on_request(tmp_path: Path):
    assert _run("--fake-provider", "valid", "--include-claim-values").returncode == 2   # needs a file
    target = tmp_path / "with-values.json"
    r = _run("--fake-provider", "valid", "--include-claim-values", "--report-file", str(target), "--json")
    assert r.returncode == 0, r.stderr
    _assert_no_content(r.stdout + r.stderr)
    assert "claim_text" not in json.loads(r.stdout)["runs"][0]
    claim_text = json.loads(target.read_text(encoding="utf-8"))["runs"][0]["claim_text"]
    assert [c["field"] for c in claim_text] == sorted(check.GROUND_TRUTH)
    assert {c["supporting_passage"] for c in claim_text} == {q for _, q in check.GROUND_TRUTH.values()}


def _leftover_environments() -> set[str]:
    return {p.name for p in Path(tempfile.gettempdir()).glob("rwa-ai-live-check-*")}


def test_no_temporary_environment_is_left_behind_even_when_the_configuration_fails():
    before = _leftover_environments()
    # An invalid setting makes the application configuration fail to load inside the environment
    r = _run("--dry-run", env=_env(CORS_ALLOWED_ORIGINS="*"))
    assert r.returncode == 1 and "FAILED" in r.stderr
    assert _leftover_environments() == before
    assert _run("--fake-provider", "valid").returncode == 0
    assert _leftover_environments() == before


def test_a_report_file_is_written_without_content(tmp_path: Path):
    target = tmp_path / "reports" / "fake-valid.json"
    r = _run("--fake-provider", "valid", "--report-file", str(target))
    assert r.returncode == 0, r.stderr
    written = target.read_text(encoding="utf-8")
    assert json.loads(written)["runs"][0]["passed"] is True
    _assert_no_content(written + r.stdout)


# ── 7. The live code path, offline ───────────────────────────────────────────


_LIVE_PATH_DRIVER = """
import json, sys, httpx2
sys.path.insert(0, {root!r})
import asyncio
import importlib.util
spec = importlib.util.spec_from_file_location("ai_live_check", {script!r})
check = importlib.util.module_from_spec(spec); spec.loader.exec_module(check)

class NoNetwork:
    def __init__(self, *a, **k):
        raise AssertionError("a real network transport must not be created in this test")
httpx2.AsyncHTTPTransport = NoNetwork
requests = []
def handle(request):
    requests.append({{"url": str(request.url), "method": request.method,
                      "key_matches": request.headers.get("x-api-key") == {key!r},
                      "body_keys": sorted(json.loads(request.content))}})
    body = json.loads(check._message(check._json(check._grounded()), input_tokens=1100, output_tokens=1300).content)
    body["model"] = {served!r} or json.loads(request.content)["model"]
    return httpx2.Response(200, json=body)
check._live_transport = lambda: httpx2.MockTransport(handle)
code = check.main(json.loads({args!r}))
print("@@REQUESTS@@" + json.dumps(requests))
sys.exit(code)
"""
LIVE_ARGS = ["--live", "--authorize-paid-request", "--authorize-external-transfer", "--max-cost-usd", "0.25", "--json"]


def _run_live_path(args: list[str], served: str | None = None):
    """The live mode with a fake transport; `served` overrides the model the fake provider reports."""
    driver = _LIVE_PATH_DRIVER.format(root=str(ROOT), script=str(SCRIPT), key=LIVE_KEY, args=json.dumps(args),
                                      served=served)
    r = subprocess.run([sys.executable, "-c", driver], cwd=ROOT, env=_env(ANTHROPIC_API_KEY=LIVE_KEY),
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)
    output, _, requests = r.stdout.partition("@@REQUESTS@@")
    return r, output, json.loads(requests) if requests else None


def test_the_live_path_sends_exactly_one_guarded_request_and_never_shows_the_key():
    before = _dev_db_hash()
    r, output, requests = _run_live_path(LIVE_ARGS)
    assert r.returncode == 0, r.stderr
    assert requests == [{"url": "https://api.anthropic.com/v1/messages", "method": "POST", "key_matches": True,
                         "body_keys": ["max_tokens", "messages", "model", "system"]}]
    report = json.loads(output)
    run = report["runs"][0]
    assert report["mode"] == "live" and run["provider"]["kind"] == "anthropic (live)"
    assert run["provider"]["requests_sent"] == 1 and run["provider"]["request_limit"] == 1
    assert run["provider"]["model_reported"] == "claude-opus-5-5"
    assert run["provider"]["usage"] == {"input_tokens": 1100, "output_tokens": 1300}
    assert run["cost_usd"] == {"basis": "provider-reported usage",
                               "amount": pytest.approx((1100 * 4 + 1300 * 20) / 1_000_000)}
    assert run["claims"]["page_grounded"] == 7 and run["approval"]["approved_automatically"] is False
    assert report["temporary_environment_destroyed"] is True and report["development_database_unchanged"] is True
    assert report["real_ai_extraction_verified"] is False
    assert _dev_db_hash() == before
    _assert_no_content(r.stdout + r.stderr)


def test_the_operator_command_for_claude_sonnet_4_6_works_offline(tmp_path: Path):
    """Exactly the flags of the operator procedure (AI_LIVE_TEST.md §6), with a fake transport."""
    before = _dev_db_hash()
    target = tmp_path / "live-report.json"
    args = ["--live", "--authorize-paid-request", "--authorize-external-transfer", "--model", "claude-sonnet-4-6",
            "--max-cost-usd", "0.25", "--report-file", str(target)]
    r, output, requests = _run_live_path(args)
    assert r.returncode == 0, r.stderr
    assert [(q["url"], q["method"], q["key_matches"], q["body_keys"]) for q in requests] == [
        ("https://api.anthropic.com/v1/messages", "POST", True, ["max_tokens", "messages", "model", "system"])]
    report = json.loads(target.read_text(encoding="utf-8"))
    run = report["runs"][0]
    assert report["model"] == "claude-sonnet-4-6"
    assert run["provider"]["model_requested"] == run["provider"]["model_reported"] == "claude-sonnet-4-6"
    assert run["provider"]["model_matches_request"] is True and run["provider"]["requests_sent"] == 1
    assert run["budget"]["worst_case_cost_usd"] == pytest.approx((3562 * 3 + 8192 * 15) / 1_000_000)
    assert run["budget"]["within_budget"] is True and run["budget"]["max_cost_usd"] == 0.25
    assert run["cost_usd"]["amount"] == pytest.approx((1100 * 3 + 1300 * 15) / 1_000_000)
    assert "claim_text" not in run and run["approval"]["approved_automatically"] is False
    assert "model reported claude-sonnet-4-6 (matches request: True)" in output
    assert _dev_db_hash() == before
    _assert_no_content(output + r.stderr + target.read_text(encoding="utf-8"))


def test_a_different_model_in_the_response_is_flagged():
    r, output, requests = _run_live_path([*LIVE_ARGS[:-1], "--model", "claude-sonnet-4-6", "--json"],
                                         served="claude-sonnet-5-5")
    assert r.returncode == 0, r.stderr
    run = json.loads(output)["runs"][0]
    assert run["provider"]["model_matches_request"] is False
    assert "model_reported_differs_from_request" in run["validation_errors"]


def test_the_dry_run_with_claude_sonnet_4_6_fits_the_budget():
    r = _run("--dry-run", "--model", "claude-sonnet-4-6", "--max-cost-usd", "0.25", "--json")
    assert r.returncode == 0, r.stderr
    run = json.loads(r.stdout)["runs"][0]
    assert run["gates"]["summary_matches_fixture"] is True and run["provider"]["requests_sent"] == 0
    assert run["budget"]["model"] == "claude-sonnet-4-6"
    assert run["budget"]["price_usd_per_mtok"] == {"input": 3.0, "output": 15.0}
    assert run["budget"]["worst_case_cost_usd"] == pytest.approx(0.133566) and run["budget"]["within_budget"] is True


def _in_process_args():
    return check.build_parser().parse_args(["--dry-run"])


def test_the_check_refuses_an_application_not_configured_for_the_temporary_environment(tmp_path: Path):
    """In this test process the application is configured for tests, not for the temporary environment."""
    env = check.Environment(root=tmp_path, admin_token="x", reviewer_token="y")
    with pytest.raises(check.Refusal, match="not inside the temporary environment"):
        asyncio.run(check.run_check("dry_run", _in_process_args(), env, b"", []))


def test_the_check_refuses_without_a_well_formed_key_in_the_loaded_configuration(tmp_path: Path, monkeypatch):
    from pydantic import SecretStr

    from app.config import settings
    monkeypatch.setattr(settings, "database_url", f"sqlite+aiosqlite:///{(tmp_path / 'x.db').as_posix()}")
    monkeypatch.setattr(settings, "ai_provider", "anthropic")
    monkeypatch.setattr(settings, "ai_model", check.DEFAULT_MODEL)
    monkeypatch.setattr(settings, "ai_max_output_tokens", check.DEFAULT_MAX_OUTPUT_TOKENS)
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr(""))
    env = check.Environment(root=tmp_path, admin_token="x", reviewer_token="y")
    with pytest.raises(check.Refusal, match="not a well-formed Anthropic key"):
        asyncio.run(check.run_check("dry_run", _in_process_args(), env, b"", []))
    assert not (tmp_path / "x.db").exists()   # refused before the database was created


# ── 8. Report helpers ────────────────────────────────────────────────────────


def test_an_automatic_approval_would_be_flagged():
    report = {"mode": "fake", "outcome": {"http_status": 201}, "provider": {"requests_sent": 1, "blocked_requests": []},
              "claims": check.summarize_claims(None), "approval": {"approved_automatically": True}}
    assert "an extraction was approved automatically" in check.scenario_check(report, {"http_status": 201})
