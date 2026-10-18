"""
Stage 10B-2 — GET /ai/status: whether AI extraction can run, without secrets.

The endpoint reports the same AI service the extraction route would use. No test reaches the
network: a configured provider is only constructed, never called.
"""

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.config import SUPPORTED_AI_MODELS, settings
from app.main import app
from app.services.ai import AI_PROVIDER_DISABLED, AI_PROVIDER_NOT_CONFIGURED, get_ai_service
from tests.conftest import INST_KEY_ALPHA, bearer

FAKE_KEY = "sk-ant-fictional-status-test-key-000000000000"


@pytest.fixture
def real_ai_dependency(monkeypatch):
    app.dependency_overrides.pop(get_ai_service, None)   # the real dependency, not the test mock
    monkeypatch.setattr(settings, "ai_provider", "disabled")
    monkeypatch.setattr(settings, "anthropic_api_key", SecretStr(""))
    return monkeypatch


async def test_a_disabled_provider_is_reported_as_unavailable(client: AsyncClient, issuer_headers: dict, real_ai_dependency):
    r = await client.get("/ai/status", headers=issuer_headers)
    assert r.status_code == 200
    assert r.json() == {"available": False, "status": "ai_provider_disabled", "external_processor": None, "model": None,
                        "supported_models": sorted(SUPPORTED_AI_MODELS), "message": AI_PROVIDER_DISABLED}


async def test_a_provider_without_a_valid_key_is_reported_as_not_configured(
    client: AsyncClient, legal_headers: dict, real_ai_dependency
):
    real_ai_dependency.setattr(settings, "ai_provider", "anthropic")
    real_ai_dependency.setattr(settings, "anthropic_api_key", SecretStr("your-anthropic-api-key-here"))
    body = (await client.get("/ai/status", headers=legal_headers)).json()
    assert (body["available"], body["status"], body["message"]) == (False, "ai_provider_not_configured", AI_PROVIDER_NOT_CONFIGURED)
    assert "your-anthropic-api-key-here" not in str(body)


async def test_a_configured_provider_names_the_processor_and_model_never_the_key(
    client: AsyncClient, admin_headers: dict, real_ai_dependency
):
    real_ai_dependency.setattr(settings, "ai_provider", "anthropic")
    real_ai_dependency.setattr(settings, "anthropic_api_key", SecretStr(FAKE_KEY))
    real_ai_dependency.setattr(settings, "ai_model", "claude-sonnet-4-6")
    r = await client.get("/ai/status", headers=admin_headers)
    body = r.json()
    assert (body["available"], body["status"], body["external_processor"], body["model"]) == (
        True, "available", "Anthropic", "claude-sonnet-4-6")
    assert "only after an explicit confirmation" in body["message"]
    assert FAKE_KEY not in r.text


async def test_the_test_double_is_reported_as_a_local_service(client: AsyncClient, issuer_headers: dict):
    body = (await client.get("/ai/status", headers=issuer_headers)).json()   # conftest: MockAIService
    assert (body["available"], body["external_processor"], body["model"]) == (True, None, None)


async def test_the_status_needs_a_user_credential(client: AsyncClient, real_ai_dependency):
    assert (await client.get("/ai/status")).status_code == 403
    assert (await client.get("/ai/status", headers=bearer("not-a-valid-token"))).status_code == 401
    assert (await client.get("/ai/status", headers={"X-API-Key": INST_KEY_ALPHA})).status_code == 403
