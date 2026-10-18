"""
Stage 10C-1 — deployment policies of a public showcase (app/middleware.py, GRANT_DEMO.md).

Coverage:
 1. READ_ONLY_MODE refuses every write route of the application, for every credential, before
    authentication, and changes nothing; reads keep working
 2. Security headers on every response (also errors and preflights); no CSP on the HTML docs
 3. API_DOCS_ENABLED=false hides /docs, /redoc and /openapi.json and nothing else
 4. /health reports the policies, and an unhandled error reveals no detail
"""

import logging

import pytest
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.session import get_db
from app.main import app
from app.middleware import READ_ONLY_DETAIL
from tests.conftest import ADMIN_TOKEN, ISSUER_TOKEN, LEGAL_TOKEN, bearer
from tests.test_approval_evidence_gate import _passport_url

_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_TABLES = ("assets", "documents", "document_versions", "document_pages", "legal_extractions", "legal_claims",
           "review_corrections", "review_approvals", "stellar_anchors")
_FAKE_ID = "00000000-0000-4000-8000-000000000000"


def _write_routes() -> list[tuple[str, str]]:
    """Every write route of the application, with its path parameters filled in."""
    routes = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            path = route.path
            for name in route.param_convertors:
                path = path.replace("{" + name + "}", _FAKE_ID)
            routes.extend((method, path) for method in sorted(route.methods & _WRITE_METHODS))
    return routes


async def _snapshot(db: AsyncSession) -> dict:
    return {table: [tuple(r) for r in (await db.execute(text(f"SELECT * FROM {table} ORDER BY 1"))).all()]
            for table in _TABLES}


@pytest.fixture
def read_only(monkeypatch):
    monkeypatch.setattr(settings, "read_only_mode", True)


# ── 1. Read-only mode ─────────────────────────────────────────────────────────


def test_the_write_route_inventory_is_known():
    """A new write route must be added here, after checking that read-only mode covers it."""
    f = _FAKE_ID
    assert sorted(_write_routes()) == sorted([
        ("POST", "/assets"), ("PATCH", f"/assets/{f}"),
        ("POST", f"/assets/{f}/documents"), ("POST", f"/documents/{f}/versions"),
        ("POST", f"/documents/{f}/extractions"), ("POST", f"/documents/{f}/manual-extractions"),
        ("POST", f"/extractions/{f}/review"), ("PATCH", f"/extractions/{f}/claims/{f}"),
        ("POST", f"/extractions/{f}/approvals"),
        ("POST", f"/assets/{f}/stellar-anchors"), ("POST", f"/assets/{f}/stellar-anchors/{f}/refresh"),
    ])


@pytest.mark.parametrize("headers", [{}, bearer(ADMIN_TOKEN), bearer(ISSUER_TOKEN), bearer(LEGAL_TOKEN),
                                     bearer("not-a-token"), {"X-API-Key": "test-inst-key-alpha"}],
                         ids=["anonymous", "operator", "issuer", "legal", "invalid", "institution"])
async def test_every_write_route_is_refused_for_every_credential_and_nothing_changes(
    client: AsyncClient, approved_extraction: dict, async_db: AsyncSession, read_only, headers
):
    before = await _snapshot(async_db)
    for method, path in _write_routes():
        r = await client.request(method, path, headers=headers, json={})
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json() == {"detail": READ_ONLY_DETAIL, "read_only_mode": True}
    # Methods the application does not use are refused the same way
    for method in ("PUT", "DELETE"):
        assert (await client.request(method, "/assets", headers=headers)).status_code == 403
    assert await _snapshot(async_db) == before


async def test_the_real_identifiers_change_nothing_either(
    client: AsyncClient, created_asset: dict, created_extraction: dict, issuer_headers: dict,
    async_db: AsyncSession, read_only,
):
    before = await _snapshot(async_db)
    doc_id, eid = created_extraction["document_id"], created_extraction["id"]
    claim_id = created_extraction["claims"][0]["id"]
    requests = [
        ("POST", "/assets", {"issuer_id": "meridian-property"}),
        ("PATCH", f"/assets/{created_asset['id']}", {"jurisdiction": "GB"}),
        ("POST", f"/documents/{doc_id}/extractions", None),
        ("POST", f"/documents/{doc_id}/manual-extractions", {}),
        ("POST", f"/extractions/{eid}/review", None),
        ("PATCH", f"/extractions/{eid}/claims/{claim_id}", {"corrected_value": "x", "is_unknown": False}),
        ("POST", f"/extractions/{eid}/approvals", {}),
        ("POST", f"/assets/{created_asset['id']}/stellar-anchors", None),
    ]
    for method, path, payload in requests:
        r = await client.request(method, path, headers=issuer_headers, json=payload)
        assert r.status_code == 403 and r.json()["read_only_mode"] is True, (method, path)
    upload = await client.post(f"/assets/{created_asset['id']}/documents", headers=issuer_headers,
                               data={"document_type": "prospectus", "title": "x"},
                               files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert upload.status_code == 403
    assert await _snapshot(async_db) == before


async def test_reads_keep_working_in_read_only_mode(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, read_only,
):
    assert (await client.get("/assets", headers=issuer_headers)).status_code == 200
    assert (await client.get(f"/extractions/{approved_extraction['id']}", headers=issuer_headers)).status_code == 200
    assert (await client.get(f"/documents/{approved_extraction['document_id']}/download",
                             headers=issuer_headers)).status_code == 200
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200
    assert (await client.get(f"/assets/{created_asset['id']}/stellar-anchors/verification",
                             headers=issuer_headers)).status_code == 200
    health = (await client.get("/health")).json()
    assert health["read_only"] is True


async def test_a_refused_write_is_logged_and_carries_cors_headers(client: AsyncClient, read_only, caplog):
    origin = settings.cors_origins[0]
    with caplog.at_level(logging.INFO, logger="rwa_security"):
        r = await client.post("/assets", headers={**bearer(ADMIN_TOKEN), "Origin": origin}, json={})
    assert r.status_code == 403
    assert r.headers["access-control-allow-origin"] == origin
    logged = [rec.getMessage() for rec in caplog.records if "request.refused_read_only" in rec.getMessage()]
    assert logged == ["request.refused_read_only method=POST path=/assets"]
    assert ADMIN_TOKEN not in caplog.text


async def test_preflight_requests_are_still_answered(client: AsyncClient, read_only):
    origin = settings.cors_origins[0]
    r = await client.options("/assets", headers={"Origin": origin, "Access-Control-Request-Method": "POST"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin


async def test_writes_work_when_read_only_mode_is_off(client: AsyncClient, created_asset: dict, admin_headers: dict):
    assert settings.read_only_mode is False
    r = await client.patch(f"/assets/{created_asset['id']}", headers=admin_headers, json={"jurisdiction": "GB"})
    assert r.status_code == 200


# ── 2. Security headers ───────────────────────────────────────────────────────


@pytest.mark.parametrize("method, path, headers, expected", [
    ("GET", "/health", {}, 200),
    ("GET", "/assets", {}, 403),
    ("GET", "/assets", bearer("not-a-token"), 401),
    ("GET", "/no-such-route", {}, 404),
    ("GET", f"/documents/{_FAKE_ID}/download", bearer(ISSUER_TOKEN), 404),
])
async def test_every_api_response_carries_the_security_headers(client: AsyncClient, method, path, headers, expected):
    r = await client.request(method, path, headers=headers)
    assert r.status_code == expected
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert "camera=()" in r.headers["permissions-policy"]


async def test_the_pdf_download_keeps_its_own_headers_and_gains_the_security_headers(
    client: AsyncClient, created_document: dict, issuer_headers: dict
):
    r = await client.get(f"/documents/{created_document['id']}/download", headers=issuer_headers)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert "attachment" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_the_html_docs_have_no_content_security_policy_but_the_other_headers(client: AsyncClient):
    r = await client.get("/docs")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert r.headers["x-frame-options"] == "DENY"
    # A route whose path merely starts like the docs is an API route
    r = await client.get(f"/documents/{_FAKE_ID}", headers=bearer(ISSUER_TOKEN))
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


# ── 3. API documentation ──────────────────────────────────────────────────────


async def test_the_api_docs_can_be_turned_off(client: AsyncClient, created_document: dict, issuer_headers: dict, monkeypatch):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert (await client.get(path)).status_code == 200
    monkeypatch.setattr(settings, "api_docs_enabled", False)
    for path in ("/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"):
        r = await client.get(path)
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, path
    assert (await client.get(f"/documents/{created_document['id']}", headers=issuer_headers)).status_code == 200
    assert "/docs" not in (await client.get("/")).json()["message"]
    assert (await client.get("/health")).json()["api_docs"] is False


# ── 4. Health and error responses ─────────────────────────────────────────────


async def test_health_reports_the_policies_only(client: AsyncClient, monkeypatch):
    body = (await client.get("/health")).json()
    assert set(body) == {"status", "version", "environment", "read_only", "synthetic_data", "api_docs"}
    assert [body["read_only"], body["synthetic_data"], body["api_docs"]] == [False, False, True]
    monkeypatch.setattr(settings, "synthetic_data_notice", True)
    assert (await client.get("/health")).json()["synthetic_data"] is True


async def test_an_unhandled_error_reveals_no_detail(client: AsyncClient):
    # `client` installs the test overrides (registry, storage); only the database is replaced
    async def _broken_db():
        raise RuntimeError("internal detail: /srv/private/database.db")
        yield  # pragma: no cover

    working_db = app.dependency_overrides[get_db]
    app.dependency_overrides[get_db] = _broken_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False),
                               base_url="http://test") as ac:
            r = await ac.get("/assets", headers=bearer(ISSUER_TOKEN))
    finally:
        app.dependency_overrides[get_db] = working_db
    assert r.status_code == 500
    assert "/srv/private" not in r.text and "RuntimeError" not in r.text and "Traceback" not in r.text


# ── 5. No PDF parsing on a read-only server (Stage 10C-2) ─────────────────────


async def test_a_read_only_server_never_parses_a_pdf(
    client: AsyncClient, created_asset: dict, approved_extraction: dict, issuer_headers: dict,
    inst_headers: dict, monkeypatch, read_only,
):
    """pypdf (with unfixed advisories, DEPLOYMENT.md) only runs on uploads and approvals: refused here."""
    from app.services import pdf

    # Counted, not only raised: the application turns any parser error into a validation error
    calls: list[int] = []

    def _recording(*args, **kwargs):
        calls.append(1)
        raise AssertionError("a read-only server must not parse a PDF")

    monkeypatch.setattr(pdf, "PdfReader", _recording)
    from app.source_integrity import _pdf_limits
    with pytest.raises(pdf.PdfValidationError):
        pdf.extract_pages(b"%PDF-1.4", _pdf_limits())
    assert calls == [1]                           # the patch reaches the application's PDF path
    calls.clear()
    doc_id, eid = approved_extraction["document_id"], approved_extraction["id"]
    for path in (f"/assets/{created_asset['id']}", f"/assets/{created_asset['id']}/documents", f"/documents/{doc_id}",
                 f"/documents/{doc_id}/versions", f"/documents/{doc_id}/text", f"/documents/{doc_id}/versions/1/text",
                 f"/documents/{doc_id}/download", f"/documents/{doc_id}/extractions", f"/extractions/{eid}",
                 f"/extractions/{eid}/approvals", f"/extractions/{eid}/corrections",
                 f"/assets/{created_asset['id']}/stellar-anchors/verification"):
        assert (await client.get(path, headers=issuer_headers)).status_code == 200, path
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200
    upload = await client.post(f"/assets/{created_asset['id']}/documents", headers=issuer_headers,
                               data={"document_type": "prospectus", "title": "x"},
                               files={"file": ("x.pdf", b"%PDF-1.4 not parsed", "application/pdf")})
    assert upload.status_code == 403
    assert calls == []
