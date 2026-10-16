"""
Stage 9B-1 — Authentication, authorization, tenant isolation and CORS tests.

Coverage:
 1. Route inventory: every route except /health requires authentication and is
    protected by the expected permission (the test fails if a new route is added).
 2. No / invalid / revoked credential → 403 / 401.
 3. Wrong credential type: an API key cannot access internal routes, a Bearer token cannot access the passport.
 4. Role matrix: wrong role → 403; correct role → success.
 5. Cross-tenant: another issuer's resources → 404 (as if they did not exist), no side effects.
 6. Institutional key scope: issuer / asset / operation scope.
 7. End-to-end authorized workflow (each role in its own step).
 8. AI extraction rate limit (per user, before the AI call).
 9. CORS allowlist.
10. /auth/me and the security log (no credential leaks).
"""

import logging

import pytest
from fastapi.routing import APIRoute
from httpx import AsyncClient

from app.access_registry import AccessRegistry, get_access_registry, hash_token
from app.api_keys import get_institutional_client
from app.auth import get_current_principal
from app.config import Settings, settings
from app.main import app
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter
from app.services.ai import get_ai_service
from tests.conftest import (
    _HARBOR_ASSET,
    _MERIDIAN_ASSET,
    ADMIN_TOKEN,
    DISABLED_TOKEN,
    INST_KEY_ALPHA,
    INST_KEY_DISABLED,
    ISSUER_TOKEN,
    LEGAL_TOKEN,
    MERIDIAN_ISSUER,
    TEST_REGISTRY,
    bearer,
    build_test_access_config,
)
from tests.mock_ai import MockAIService

_ZERO_ID = "00000000-0000-0000-0000-000000000000"

# (method, path) → the dependency that protects the route. When a new route is added,
# the test fails until this table is updated: every new endpoint needs a deliberate authorization decision.
EXPECTED_ROUTE_GUARDS: dict[tuple[str, str], str | None] = {
    ("GET", "/health"): None,  # the only public route: liveness check
    ("GET", "/auth/me"): "get_current_principal",
    ("POST", "/assets"): "require_asset_create",
    ("GET", "/assets"): "require_asset_read",
    ("GET", "/assets/{asset_id}"): "require_asset_read",
    ("PATCH", "/assets/{asset_id}"): "require_asset_update",
    ("GET", "/assets/{asset_id}/blockchain-status"): "require_asset_read",
    ("POST", "/assets/{asset_id}/documents"): "require_document_upload",
    ("GET", "/assets/{asset_id}/documents"): "require_document_read",
    ("GET", "/documents/{document_id}"): "require_document_read",
    ("POST", "/documents/{document_id}/versions"): "require_document_upload",
    ("GET", "/documents/{document_id}/versions"): "require_document_read",
    ("GET", "/documents/{document_id}/text"): "require_document_read",
    ("GET", "/documents/{document_id}/versions/{version_number}/text"): "require_document_read",
    ("GET", "/documents/{document_id}/download"): "require_document_read",
    ("POST", "/documents/{document_id}/extractions"): "require_extraction_run",
    ("GET", "/documents/{document_id}/extractions"): "require_extraction_read",
    ("GET", "/extractions/{extraction_id}"): "require_extraction_read",
    ("POST", "/extractions/{extraction_id}/review"): "require_review_start",
    ("PATCH", "/extractions/{extraction_id}/claims/{claim_id}"): "require_claim_correct",
    ("POST", "/extractions/{extraction_id}/approvals"): "require_approval_create",
    ("GET", "/extractions/{extraction_id}/corrections"): "require_audit_read",
    ("GET", "/extractions/{extraction_id}/approvals"): "require_audit_read",
    ("GET", "/passport/{network}/{asset_code}/{issuer_key}"): "require_passport_read",
}

BEARER_ROUTES = [
    route for route, guard in EXPECTED_ROUTE_GUARDS.items()
    if guard is not None and guard != "require_passport_read"
]


def _dependency_calls(dependant) -> list:
    calls = []
    for sub in dependant.dependencies:
        calls.append(sub.call)
        calls.extend(_dependency_calls(sub))
    return calls


def _api_routes() -> dict[tuple[str, str], APIRoute]:
    routes = {}
    for route in app.routes:
        if isinstance(route, APIRoute) and route.include_in_schema:
            for method in route.methods:
                routes[(method, route.path)] = route
    return routes


def _concrete(path: str) -> str:
    return (
        path.replace("{network}", "stellar-mainnet")
        .replace("{asset_code}", "MRPROP")
        .replace("{issuer_key}", _MERIDIAN_ASSET["stellar_issuer_public_key"])
        .replace("{asset_id}", _ZERO_ID)
        .replace("{document_id}", _ZERO_ID)
        .replace("{extraction_id}", _ZERO_ID)
        .replace("{claim_id}", _ZERO_ID)
        .replace("{version_number}", "1")
    )


async def _call(client: AsyncClient, method: str, path: str, headers: dict | None = None):
    kwargs: dict = {"headers": headers or {}}
    if method in {"POST", "PATCH"} and not path.endswith(("/documents", "/versions")):
        kwargs["json"] = {}
    return await client.request(method, _concrete(path), **kwargs)


def _passport_url(asset: dict) -> str:
    return (
        f"/passport/{asset['blockchain_network']}/{asset['stellar_asset_code']}"
        f"/{asset['stellar_issuer_public_key']}"
    )


def _as_read(claim: dict) -> dict:
    """The mandatory correction precondition (Stage 9B-4 issue A): the claim exactly as read."""
    return {key: claim[key] for key in ("value", "is_unknown", "source_page", "supporting_passage")}


async def _upload(client: AsyncClient, asset_id: str, pdf: bytes, headers: dict):
    return await client.post(
        f"/assets/{asset_id}/documents",
        headers=headers,
        data={"document_type": "prospectus", "title": "Prospectus"},
        files={"file": ("p.pdf", pdf, "application/pdf")},
    )


@pytest.fixture
async def harbor_asset(admin_client: AsyncClient) -> dict:
    resp = await admin_client.post("/assets", json=_HARBOR_ASSET)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def harbor_extraction(
    client: AsyncClient, harbor_asset: dict, source_pdf: bytes, other_issuer_headers: dict
) -> dict:
    doc = await _upload(client, harbor_asset["id"], source_pdf, other_issuer_headers)
    assert doc.status_code == 201, doc.text
    ext = await client.post(
        f"/documents/{doc.json()['id']}/extractions", headers=other_issuer_headers
    )
    assert ext.status_code == 201, ext.text
    return ext.json()


# ── 1. Route inventory ────────────────────────────────────────────────────────


def test_route_inventory_matches_expected_guards():
    """Every route is protected by the expected dependency; there is no route outside the inventory."""
    routes = _api_routes()
    assert set(routes) == set(EXPECTED_ROUTE_GUARDS), (
        "The route inventory changed — make an authorization decision for the new route and "
        "update the EXPECTED_ROUTE_GUARDS table."
    )
    for key, guard in EXPECTED_ROUTE_GUARDS.items():
        names = {getattr(c, "__name__", "") for c in _dependency_calls(routes[key].dependant)}
        if guard is None:
            assert "get_current_principal" not in names
            assert "get_institutional_client" not in names
        else:
            assert guard in names, f"{key} is not protected by {guard} (found: {sorted(names)})"


def test_every_protected_route_authenticates():
    """The dependency tree of every route except /health contains an authenticator."""
    for key, route in _api_routes().items():
        calls = _dependency_calls(route.dependant)
        authenticated = get_current_principal in calls or get_institutional_client in calls
        assert authenticated or key == ("GET", "/health"), f"{key} has no authentication"


# ── 2. No / invalid / revoked credential ──────────────────────────────────────


@pytest.mark.parametrize(
    "method,path", [k for k, g in EXPECTED_ROUTE_GUARDS.items() if g is not None]
)
async def test_protected_route_without_credentials_is_rejected(
    client: AsyncClient, method: str, path: str
):
    """Every protected request sent without credentials receives 403 (no data is returned)."""
    r = await _call(client, method, path)
    assert r.status_code == 403, f"{method} {path} → {r.status_code}"
    assert set(r.json()) == {"detail"}


@pytest.mark.parametrize("method,path", BEARER_ROUTES)
async def test_bearer_route_with_invalid_token_returns_401(
    client: AsyncClient, method: str, path: str
):
    r = await _call(client, method, path, bearer("tamamen-gecersiz-token"))
    assert r.status_code == 401, f"{method} {path} → {r.status_code}"
    assert r.headers.get("www-authenticate") == "Bearer"


async def test_health_stays_public(client: AsyncClient):
    r = await client.get("/health")
    assert r.status_code == 200


async def test_disabled_user_token_is_rejected(client: AsyncClient):
    """A user with enabled=false in the access file → 401 (revoked credential)."""
    r = await client.get("/assets", headers=bearer(DISABLED_TOKEN))
    assert r.status_code == 401


async def test_disabled_institutional_key_is_rejected(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    r = await client.get(_passport_url(created_asset), headers={"X-API-Key": INST_KEY_DISABLED})
    assert r.status_code == 401


async def test_legacy_env_tokens_no_longer_authenticate(client: AsyncClient, monkeypatch):
    """Deprecated .env tokens no longer authenticate any request."""
    legacy = "legacy-reviewer-token-value-0123456789abcdef"
    monkeypatch.setattr(settings, "reviewer_issuer_token", legacy)
    monkeypatch.setattr(settings, "institutional_api_key_alpha", legacy + "-inst")
    assert (await client.get("/assets", headers=bearer(legacy))).status_code == 401
    r = await client.get(
        "/passport/stellar-mainnet/MRPROP/" + _MERIDIAN_ASSET["stellar_issuer_public_key"],
        headers={"X-API-Key": legacy + "-inst"},
    )
    assert r.status_code == 401


# ── 3. Wrong credential type ──────────────────────────────────────────────────


@pytest.mark.parametrize("method,path", BEARER_ROUTES)
async def test_institutional_key_cannot_use_internal_routes(
    client: AsyncClient, method: str, path: str
):
    """An institutional API key is not accepted as a credential on internal routes (drafts included)."""
    r = await _call(client, method, path, {"X-API-Key": INST_KEY_ALPHA})
    assert r.status_code == 403


async def test_institutional_key_cannot_read_drafts_or_download(
    client: AsyncClient, created_extraction: dict, created_document: dict
):
    key = {"X-API-Key": INST_KEY_ALPHA}
    assert (await client.get(f"/extractions/{created_extraction['id']}", headers=key)).status_code == 403
    assert (await client.get(f"/documents/{created_document['id']}/download", headers=key)).status_code == 403
    assert (await client.get(f"/documents/{created_document['id']}/text", headers=key)).status_code == 403


async def test_user_token_cannot_read_passport(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """A Bearer token cannot replace the passport key (internal user ≠ institutional client)."""
    r = await client.get(_passport_url(created_asset), headers=bearer(ADMIN_TOKEN))
    assert r.status_code == 403


# ── 4. Role matrix ────────────────────────────────────────────────────────────


async def test_issuer_reviewer_cannot_register_or_modify_assets(
    client: AsyncClient, created_asset: dict, issuer_headers: dict
):
    r = await client.post("/assets", headers=issuer_headers, json=_HARBOR_ASSET)
    assert r.status_code == 403
    r = await client.patch(
        f"/assets/{created_asset['id']}", headers=issuer_headers, json={"jurisdiction": "KY"}
    )
    assert r.status_code == 403
    # The change must not have been applied
    r = await client.get(f"/assets/{created_asset['id']}", headers=issuer_headers)
    assert r.json()["jurisdiction"] == "US-NY"


async def test_legal_reviewer_cannot_upload_or_run_ai(
    client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    minimal_pdf: bytes,
    legal_headers: dict,
):
    assert (await _upload(client, created_asset["id"], minimal_pdf, legal_headers)).status_code == 403
    r = await client.post(
        f"/documents/{created_document['id']}/versions",
        headers=legal_headers,
        files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
    )
    assert r.status_code == 403
    r = await client.post(f"/documents/{created_document['id']}/extractions", headers=legal_headers)
    assert r.status_code == 403


async def test_platform_admin_cannot_review_correct_or_approve(
    client: AsyncClient, created_extraction: dict, admin_headers: dict
):
    eid = created_extraction["id"]
    claim_id = created_extraction["claims"][0]["id"]
    assert (await client.post(f"/extractions/{eid}/review", headers=admin_headers)).status_code == 403
    r = await client.patch(
        f"/extractions/{eid}/claims/{claim_id}",
        headers=admin_headers,
        json={"corrected_value": "x", "is_unknown": False, "expected_claim": _as_read(created_extraction["claims"][0])},
    )
    assert r.status_code == 403
    r = await client.post(f"/extractions/{eid}/approvals", headers=admin_headers, json={})
    assert r.status_code == 403
    # The status must not have changed
    r = await client.get(f"/extractions/{eid}", headers=admin_headers)
    assert r.json()["status"] == "draft"


async def test_platform_admin_can_read_audit_history(
    client: AsyncClient, approved_extraction: dict, admin_headers: dict
):
    """Authorized audit access: the operator can read the approval history (cannot change it)."""
    eid = approved_extraction["id"]
    r = await client.get(f"/extractions/{eid}/approvals", headers=admin_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert (await client.get(f"/extractions/{eid}/corrections", headers=admin_headers)).status_code == 200


async def test_legal_reviewer_can_read_assigned_issuer_documents(
    client: AsyncClient, created_document: dict, source_pdf: bytes, legal_headers: dict
):
    r = await client.get(f"/documents/{created_document['id']}/download", headers=legal_headers)
    assert r.status_code == 200
    assert r.content == source_pdf


# ── 5. Cross-tenant isolation ─────────────────────────────────────────────────


async def test_asset_list_is_scoped_to_issuer(
    client: AsyncClient,
    created_asset: dict,
    harbor_asset: dict,
    issuer_headers: dict,
    other_issuer_headers: dict,
    admin_headers: dict,
):
    meridian_view = {a["id"] for a in (await client.get("/assets", headers=issuer_headers)).json()}
    harbor_view = {a["id"] for a in (await client.get("/assets", headers=other_issuer_headers)).json()}
    admin_view = {a["id"] for a in (await client.get("/assets", headers=admin_headers)).json()}
    assert meridian_view == {created_asset["id"]}
    assert harbor_view == {harbor_asset["id"]}
    assert admin_view == {created_asset["id"], harbor_asset["id"]}


async def test_cross_tenant_resource_access_returns_404(
    client: AsyncClient,
    created_asset: dict,
    created_document: dict,
    created_extraction: dict,
    minimal_pdf: bytes,
    other_issuer_headers: dict,
):
    """The Harbor reviewer cannot access any Meridian resource; the response is 404."""
    h = other_issuer_headers
    aid, did, eid = created_asset["id"], created_document["id"], created_extraction["id"]
    cid = created_extraction["claims"][0]["id"]
    attempts = {
        "GET asset": await client.get(f"/assets/{aid}", headers=h),
        "GET blockchain": await client.get(f"/assets/{aid}/blockchain-status", headers=h),
        "GET documents": await client.get(f"/assets/{aid}/documents", headers=h),
        "POST document": await _upload(client, aid, minimal_pdf, h),
        "GET document": await client.get(f"/documents/{did}", headers=h),
        "POST version": await client.post(
            f"/documents/{did}/versions", headers=h,
            files={"file": ("v2.pdf", minimal_pdf, "application/pdf")},
        ),
        "GET versions": await client.get(f"/documents/{did}/versions", headers=h),
        "GET text": await client.get(f"/documents/{did}/text", headers=h),
        "GET version text": await client.get(f"/documents/{did}/versions/1/text", headers=h),
        "GET download": await client.get(f"/documents/{did}/download", headers=h),
        "POST extraction": await client.post(f"/documents/{did}/extractions", headers=h),
        "GET extractions": await client.get(f"/documents/{did}/extractions", headers=h),
        "GET extraction": await client.get(f"/extractions/{eid}", headers=h),
        "POST review": await client.post(f"/extractions/{eid}/review", headers=h),
        # A well-formed request (with the mandatory precondition), so that the scope check is reached
        "PATCH claim": await client.patch(
            f"/extractions/{eid}/claims/{cid}", headers=h,
            json={"corrected_value": "tampered", "is_unknown": False,
                  "expected_claim": _as_read(created_extraction["claims"][0])},
        ),
        "POST approval": await client.post(f"/extractions/{eid}/approvals", headers=h, json={}),
        "GET corrections": await client.get(f"/extractions/{eid}/corrections", headers=h),
        "GET approvals": await client.get(f"/extractions/{eid}/approvals", headers=h),
    }
    leaked = {name: r.status_code for name, r in attempts.items() if r.status_code != 404}
    assert leaked == {}, f"Cross-tenant access did not return 404: {leaked}"


async def test_cross_tenant_attempts_have_no_side_effects(
    client: AsyncClient,
    created_document: dict,
    created_extraction: dict,
    issuer_headers: dict,
    other_issuer_headers: dict,
    other_legal_headers: dict,
):
    eid = created_extraction["id"]
    claim = created_extraction["claims"][0]
    r = await client.patch(
        f"/extractions/{eid}/claims/{claim['id']}", headers=other_issuer_headers,
        json={"corrected_value": "tampered", "is_unknown": False, "expected_claim": _as_read(claim)},
    )
    assert r.status_code == 404
    await client.post(f"/extractions/{eid}/approvals", headers=other_legal_headers, json={})
    await client.post(f"/documents/{created_document['id']}/extractions", headers=other_issuer_headers)

    ext = (await client.get(f"/extractions/{eid}", headers=issuer_headers)).json()
    assert ext["status"] == "draft"
    assert next(c for c in ext["claims"] if c["id"] == claim["id"])["value"] == claim["value"]
    assert (await client.get(f"/extractions/{eid}/approvals", headers=issuer_headers)).json() == []
    assert (await client.get(f"/extractions/{eid}/corrections", headers=issuer_headers)).json() == []
    listed = (await client.get(f"/documents/{created_document['id']}/extractions", headers=issuer_headers)).json()
    assert len(listed) == 1


async def test_cross_tenant_404_is_indistinguishable_from_missing(
    client: AsyncClient, created_asset: dict, created_extraction: dict, other_issuer_headers: dict
):
    """An out-of-scope resource and a non-existent resource receive the same response (IDs cannot be confirmed)."""
    real = await client.get(f"/assets/{created_asset['id']}", headers=other_issuer_headers)
    ghost = await client.get(f"/assets/{_ZERO_ID}", headers=other_issuer_headers)
    assert (real.status_code, real.json()) == (ghost.status_code, ghost.json())
    real = await client.get(f"/extractions/{created_extraction['id']}", headers=other_issuer_headers)
    ghost = await client.get(f"/extractions/{_ZERO_ID}", headers=other_issuer_headers)
    assert (real.status_code, real.json()) == (ghost.status_code, ghost.json())


async def test_admin_cannot_register_asset_for_unknown_issuer(
    client: AsyncClient, admin_headers: dict
):
    r = await client.post("/assets", headers=admin_headers, json={**_MERIDIAN_ASSET, "issuer_id": "ghost-issuer"})
    assert r.status_code == 422
    r = await client.post("/assets", headers=admin_headers, json={**_MERIDIAN_ASSET, "issuer_id": "*"})
    assert r.status_code == 422
    r = await client.post(
        "/assets", headers=admin_headers,
        json={k: v for k, v in _MERIDIAN_ASSET.items() if k != "issuer_id"},
    )
    assert r.status_code == 422


async def test_asset_issuer_id_is_immutable(
    client: AsyncClient, created_asset: dict, admin_headers: dict, issuer_headers: dict
):
    """The tenant cannot be changed with PATCH; the asset stays in the scope of its original issuer."""
    r = await client.patch(
        f"/assets/{created_asset['id']}", headers=admin_headers,
        json={"issuer_id": "harbor-bonds", "jurisdiction": "US-DE"},
    )
    assert r.status_code == 200
    assert r.json()["issuer_id"] == MERIDIAN_ISSUER
    assert r.json()["jurisdiction"] == "US-DE"
    assert (await client.get(f"/assets/{created_asset['id']}", headers=issuer_headers)).status_code == 200


# ── 6. Institutional key scope ────────────────────────────────────────────────


async def test_institutional_key_cannot_read_other_issuer_passport(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_headers: dict,
    inst_beta_headers: dict,
):
    """A Harbor-scoped key cannot see the approved Meridian passport (treated like an unknown asset)."""
    assert (await client.get(_passport_url(created_asset), headers=inst_headers)).status_code == 200
    out_of_scope = await client.get(_passport_url(created_asset), headers=inst_beta_headers)
    unknown = await client.get(
        "/passport/stellar-mainnet/GHOST/" + _MERIDIAN_ASSET["stellar_issuer_public_key"],
        headers=inst_beta_headers,
    )
    assert out_of_scope.status_code == 404
    assert out_of_scope.json() == unknown.json()


async def test_institutional_scope_applies_in_both_directions(
    client: AsyncClient,
    harbor_asset: dict,
    harbor_extraction: dict,
    other_legal_headers: dict,
    inst_headers: dict,
    inst_beta_headers: dict,
):
    r = await client.post(
        f"/extractions/{harbor_extraction['id']}/approvals", headers=other_legal_headers, json={}
    )
    assert r.status_code == 201
    assert (await client.get(_passport_url(harbor_asset), headers=inst_beta_headers)).status_code == 200
    assert (await client.get(_passport_url(harbor_asset), headers=inst_headers)).status_code == 404


async def test_asset_scoped_institutional_key(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    admin_headers: dict,
    issuer_headers: dict,
    source_pdf: bytes,
):
    """A key scoped with asset_ids reads only that asset; it cannot read another asset of the same issuer."""
    second = await client.post(
        "/assets", headers=admin_headers,
        json={**_MERIDIAN_ASSET, "stellar_asset_code": "MRPROP2"},
    )
    assert second.status_code == 201
    second_asset = second.json()
    doc = await _upload(client, second_asset["id"], source_pdf, issuer_headers)
    ext = await client.post(f"/documents/{doc.json()['id']}/extractions", headers=issuer_headers)
    r = await client.post(f"/extractions/{ext.json()['id']}/approvals", headers=issuer_headers, json={})
    assert r.status_code == 201, r.text  # the second asset has a current passport of its own

    scoped_key = "test-inst-key-single-asset"
    registry = AccessRegistry.from_dict(build_test_access_config(extra_clients=[{
        "client_id": "single-asset-fund-001",
        "organization": "Single Asset Fund",
        "key_sha256": hash_token(scoped_key),
        "asset_ids": [created_asset["id"]],
        "operations": ["passport:read"],
    }]))
    app.dependency_overrides[get_access_registry] = lambda: registry

    headers = {"X-API-Key": scoped_key}
    assert (await client.get(_passport_url(created_asset), headers=headers)).status_code == 200
    assert (await client.get(_passport_url(second_asset), headers=headers)).status_code == 404


async def test_institutional_key_without_passport_operation_is_forbidden(
    client: AsyncClient, approved_extraction: dict, created_asset: dict
):
    """Operation scope: a key without the passport:read permission → 403."""
    key = INST_KEY_ALPHA
    client_entry = TEST_REGISTRY.clients_by_key_hash[hash_token(key)]
    restricted = AccessRegistry(
        issuers=TEST_REGISTRY.issuers,
        users_by_token_hash=TEST_REGISTRY.users_by_token_hash,
        clients_by_key_hash={
            hash_token(key): client_entry.model_copy(update={"operations": frozenset()})
        },
    )
    app.dependency_overrides[get_access_registry] = lambda: restricted
    r = await client.get(_passport_url(created_asset), headers={"X-API-Key": key})
    assert r.status_code == 403
    assert r.json() == {"detail": "This API key is not authorized for this operation."}


async def test_out_of_scope_passport_attempt_is_logged_without_key(
    client: AsyncClient,
    approved_extraction: dict,
    created_asset: dict,
    inst_beta_headers: dict,
    caplog,
):
    with caplog.at_level(logging.WARNING, logger="rwa_passport"):
        r = await client.get(_passport_url(created_asset), headers=inst_beta_headers)
    assert r.status_code == 404
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("passport.out_of_scope" in m and "apex-legal-research-001" in m for m in messages)
    assert not any(inst_beta_headers["X-API-Key"] in m for m in messages)


# ── 7. End-to-end authorized workflow ─────────────────────────────────────────


async def test_full_authorized_workflow_across_roles(
    client: AsyncClient,
    source_pdf: bytes,
    admin_headers: dict,
    issuer_headers: dict,
    legal_headers: dict,
    inst_headers: dict,
    inst_beta_headers: dict,
):
    """
    The operator registers the asset → the issuer uploads a document and runs the AI →
    the issuer starts the review → the legal expert corrects and verifies →
    the issuer gives the attestation → the in-scope institutional client reads the passport.
    """
    asset = await client.post("/assets", headers=admin_headers, json=_MERIDIAN_ASSET)
    assert asset.status_code == 201
    asset = asset.json()
    assert asset["issuer_id"] == MERIDIAN_ISSUER

    doc = await _upload(client, asset["id"], source_pdf, issuer_headers)
    assert doc.status_code == 201
    ext = await client.post(f"/documents/{doc.json()['id']}/extractions", headers=issuer_headers)
    assert ext.status_code == 201
    ext = ext.json()
    assert ext["status"] == "draft"

    # Before approval, the institutional client cannot see anything
    assert (await client.get(_passport_url(asset), headers=inst_headers)).status_code == 404

    r = await client.post(f"/extractions/{ext['id']}/review", headers=issuer_headers)
    assert r.status_code == 200 and r.json()["status"] == "under_review"

    known = next(c for c in ext["claims"] if not c["is_unknown"])
    r = await client.patch(
        f"/extractions/{ext['id']}/claims/{known['id']}", headers=legal_headers,
        # A changed value needs explicit evidence (Stage 9B-4 step 7): the claim's own, re-cited
        json={"corrected_value": known["value"] + " (reviewed)", "is_unknown": False,
              "corrected_source_page": known["source_page"], "corrected_passage": known["supporting_passage"],
              "expected_claim": _as_read(known)},
    )
    assert r.status_code == 200 and r.json()["reviewer_id"] == "bob-martinez-01"

    r = await client.post(f"/extractions/{ext['id']}/approvals", headers=legal_headers, json={})
    assert r.status_code == 201 and r.json()["approval_type"] == "legal_verification"
    r = await client.post(f"/extractions/{ext['id']}/approvals", headers=issuer_headers, json={})
    assert r.status_code == 201 and r.json()["approval_type"] == "issuer_attestation"

    passport = await client.get(_passport_url(asset), headers=inst_headers)
    assert passport.status_code == 200
    types = {a["approval_type"] for a in passport.json()["approvals"]}
    assert types == {"legal_verification", "issuer_attestation"}
    assert all("reviewer_id" not in a for a in passport.json()["approvals"])

    assert (await client.get(_passport_url(asset), headers=inst_beta_headers)).status_code == 404


# ── 8. AI extraction rate limit ───────────────────────────────────────────────


class _CountingAI(MockAIService):
    calls = 0

    async def extract_legal_rights(self, **kwargs):
        type(self).calls += 1
        return await super().extract_legal_rights(**kwargs)


async def test_extraction_trigger_is_rate_limited_per_user(
    client: AsyncClient, created_document: dict, issuer_headers: dict, admin_headers: dict
):
    _CountingAI.calls = 0
    app.dependency_overrides[get_ai_service] = lambda: _CountingAI()
    limiter = InMemoryRateLimiter(max_requests=2, window_seconds=3600)
    app.dependency_overrides[get_extraction_rate_limiter] = lambda: limiter
    url = f"/documents/{created_document['id']}/extractions"

    codes = [(await client.post(url, headers=issuer_headers)).status_code for _ in range(4)]
    assert codes == [201, 201, 429, 429]
    assert _CountingAI.calls == 2, "The AI service must not be called once the limit is exceeded"
    # The limit is per user: another authorized user is not affected
    assert (await client.post(url, headers=admin_headers)).status_code == 201


async def test_extraction_rate_limit_setting_is_bounded():
    with pytest.raises(ValueError):
        Settings(extraction_rate_limit_per_hour=0)


# ── 9. CORS allowlist ─────────────────────────────────────────────────────────


async def test_cors_preflight_allowed_origin(client: AsyncClient):
    assert settings.cors_origins, "The default allowlist must not be empty"
    origin = settings.cors_origins[0]
    r = await client.options(
        "/assets",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == origin
    assert "access-control-allow-credentials" not in r.headers


async def test_cors_rejects_unlisted_origin(client: AsyncClient, admin_headers: dict):
    evil = "https://evil.example"
    assert evil not in settings.cors_origins
    pre = await client.options(
        "/assets",
        headers={"Origin": evil, "Access-Control-Request-Method": "POST",
                 "Access-Control-Request-Headers": "authorization,content-type"},
    )
    assert pre.status_code == 400
    assert "access-control-allow-origin" not in pre.headers
    simple = await client.get("/health", headers={"Origin": evil})
    assert "access-control-allow-origin" not in simple.headers


@pytest.mark.parametrize(
    "value",
    ["*", "http://localhost:5173,*", "https://*.example.com", "localhost:5173",
     "http://localhost:5173/path", "javascript:alert(1)"],
)
def test_cors_wildcard_and_malformed_origins_rejected(value: str):
    with pytest.raises(ValueError):
        Settings(cors_allowed_origins=value)


def test_cors_explicit_origins_accepted():
    s = Settings(cors_allowed_origins=" https://app.example.com , http://127.0.0.1:5173/ ")
    assert s.cors_origins == ["https://app.example.com", "http://127.0.0.1:5173"]
    assert Settings(cors_allowed_origins="").cors_origins == []


# ── 10. /auth/me and the security log ─────────────────────────────────────────


async def test_auth_me_reports_role_and_scope_without_secrets(client: AsyncClient):
    r = await client.get("/auth/me", headers=bearer(LEGAL_TOKEN))
    assert r.status_code == 200
    body = r.json()
    assert body["user_id"] == "bob-martinez-01"
    assert body["role"] == "independent_legal_reviewer"
    assert body["scope"] == "issuers"
    assert body["issuer_ids"] == [MERIDIAN_ISSUER]
    assert "approval:create" in body["permissions"]
    assert "document:upload" not in body["permissions"]
    assert LEGAL_TOKEN not in r.text and hash_token(LEGAL_TOKEN) not in r.text

    admin = (await client.get("/auth/me", headers=bearer(ADMIN_TOKEN))).json()
    assert admin["scope"] == "global" and admin["issuer_ids"] == []
    assert "approval:create" not in admin["permissions"]


async def test_denials_are_logged_without_tokens(
    client: AsyncClient, created_asset: dict, other_issuer_headers: dict, issuer_headers: dict, caplog
):
    with caplog.at_level(logging.WARNING, logger="rwa_security"):
        await client.get(f"/assets/{created_asset['id']}", headers=other_issuer_headers)
        await client.post("/assets", headers=issuer_headers, json=_HARBOR_ASSET)
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("authz.out_of_scope" in m and "carol-ng-01" in m for m in messages)
    assert any("authz.permission_denied" in m and "asset:create" in m for m in messages)
    for token in (ISSUER_TOKEN, "test-other-issuer-token"):
        assert not any(token in m for m in messages)
