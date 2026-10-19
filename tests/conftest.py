"""
Test fixtures.

For every test:
- A separate in-memory SQLite database (full isolation)
- A separate temporary folder (file storage isolation)
- A mock AI service (no real request is sent to the Anthropic API)
- A fictional access registry: two issuers (tenants), users of every role and
  scoped institutional keys. The registry goes through the application's own
  validator (AccessRegistry.from_dict); tokens are kept only as digests.
  Real values from .env or access_control.json are not used.

The standard document (created_document) is a two-page text PDF containing every quotation of
the mock AI, so approvals of its extractions pass the source-evidence gate (Stage 9B-4).
minimal_pdf stays a blank, scanned-like PDF on which no quotation can ever be verified.
"""

import io
from contextlib import asynccontextmanager

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.access_registry import AccessRegistry, get_access_registry, hash_token
from app.db.base import Base
from app.db.session import create_database_engine, get_db
from app.main import app
from app.rate_limit import InMemoryRateLimiter, get_extraction_rate_limiter, get_rate_limiter
from app.services.ai import get_ai_service
from app.services.stellar import get_stellar_service
from app.services.stellar_anchor import StellarAnchorService, get_anchor_service
from app.services.storage import StorageService, get_storage
from tests.mock_ai import MockAIService
from tests.mock_stellar import MockStellarService
from tests.pdf_fixtures import make_source_pdf

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# ── Fictional issuers (tenants) ───────────────────────────────────────────────
MERIDIAN_ISSUER = "meridian-property"
HARBOR_ISSUER = "harbor-bonds"
AGROCOMM_ISSUER = "agrocomm-finance"
TESTNET_ISSUER = "testnet-property"

# Fictional asset used in the Phase 2 and Phase 3 tests
_MERIDIAN_ASSET = {
    "issuer_id": MERIDIAN_ISSUER,
    "blockchain_network": "stellar-mainnet",
    "stellar_asset_code": "MRPROP",
    "stellar_issuer_public_key": "GCEXZSDIMN7TYVO5FITNJNNEJTRLWV4YRXMVOLF6Q2CAZBMBDZUJY2YV",
    "issuer_organization_name": "Meridian Property Holdings LLC",
    "asset_type": "real_estate",
    "jurisdiction": "US-NY",
}

# Fictional asset of the second tenant (cross-tenant tests)
_HARBOR_ASSET = {
    "issuer_id": HARBOR_ISSUER,
    "blockchain_network": "stellar-mainnet",
    "stellar_asset_code": "HBBOND",
    "stellar_issuer_public_key": "GCTZS7UU3ELEP4YJH37FC2PFYSWNXHTTUROVHLEFBHHF4IUNGVLL7Y4B",
    "issuer_organization_name": "Harbor Municipal Bonds Ltd",
    "asset_type": "bond",
    "jurisdiction": "GB",
}

# ── FICTIONAL TEST CREDENTIALS — NOT SECRET ───────────────────────────────────
# Valid only within this test suite, through dependency overrides.
# The running application does not recognize them; the real registry is in access_control.json.
ADMIN_TOKEN = "test-admin-token"
ISSUER_TOKEN = "test-issuer-token"
LEGAL_TOKEN = "test-legal-token"
SECOND_LEGAL_TOKEN = "test-second-legal-token"
AI_TOKEN = "test-ai-token"
OTHER_ISSUER_TOKEN = "test-other-issuer-token"
OTHER_LEGAL_TOKEN = "test-other-legal-token"
DISABLED_TOKEN = "test-disabled-token"

INST_KEY_ALPHA = "test-inst-key-alpha"   # Meridian issuer, passport:read
INST_KEY_BETA = "test-inst-key-beta"     # Harbor issuer, passport:read
INST_KEY_DISABLED = "test-inst-key-off"  # Meridian scope, disabled (revoked)


def build_test_access_config(extra_clients: list[dict] | None = None) -> dict:
    """Access configuration dictionary (same schema as access_control.json)."""
    return {
        "issuers": [
            # Meridian has authorized external AI processing (Stage 10A-2); the other issuers have not
            {"id": MERIDIAN_ISSUER, "name": "Meridian Property Holdings LLC", "external_ai_processing": True},
            {"id": HARBOR_ISSUER, "name": "Harbor Municipal Bonds Ltd"},
            {"id": AGROCOMM_ISSUER, "name": "AgroComm Finance SA"},
            {"id": TESTNET_ISSUER, "name": "Testnet Property Fund"},
        ],
        "users": [
            {"user_id": "ops-admin-01", "display_name": "Platform Operator",
             "role": "platform_admin", "token_sha256": hash_token(ADMIN_TOKEN)},
            {"user_id": "alice-chen-01", "display_name": "Alice Chen",
             "role": "issuer_reviewer", "issuer_ids": [MERIDIAN_ISSUER, TESTNET_ISSUER],
             "token_sha256": hash_token(ISSUER_TOKEN)},
            {"user_id": "bob-martinez-01", "display_name": "Bob Martinez",
             "role": "independent_legal_reviewer", "issuer_ids": [MERIDIAN_ISSUER],
             "token_sha256": hash_token(LEGAL_TOKEN)},
            # A second legal reviewer of Meridian: approves what Bob corrected (Stage 9B-5C, self-review)
            {"user_id": "grace-osei-01", "display_name": "Grace Osei",
             "role": "independent_legal_reviewer", "issuer_ids": [MERIDIAN_ISSUER],
             "token_sha256": hash_token(SECOND_LEGAL_TOKEN)},
            # Specific to the code-layer test: user_id starts with "ai-" → approval is rejected with 403
            {"user_id": "ai-extraction-service", "display_name": "AI Extraction Service",
             "role": "issuer_reviewer", "issuer_ids": [MERIDIAN_ISSUER],
             "token_sha256": hash_token(AI_TOKEN)},
            {"user_id": "carol-ng-01", "display_name": "Carol Ng",
             "role": "issuer_reviewer", "issuer_ids": [HARBOR_ISSUER],
             "token_sha256": hash_token(OTHER_ISSUER_TOKEN)},
            {"user_id": "dave-okafor-01", "display_name": "Dave Okafor",
             "role": "independent_legal_reviewer", "issuer_ids": [HARBOR_ISSUER],
             "token_sha256": hash_token(OTHER_LEGAL_TOKEN)},
            {"user_id": "erin-former-01", "display_name": "Erin (revoked)",
             "role": "issuer_reviewer", "issuer_ids": [MERIDIAN_ISSUER],
             "token_sha256": hash_token(DISABLED_TOKEN), "enabled": False},
        ],
        "institutional_clients": [
            {"client_id": "meridian-capital-001", "organization": "Meridian Capital Partners",
             "key_sha256": hash_token(INST_KEY_ALPHA), "issuer_ids": [MERIDIAN_ISSUER],
             "operations": ["passport:read"]},
            {"client_id": "apex-legal-research-001", "organization": "Apex Legal Research Inc.",
             "key_sha256": hash_token(INST_KEY_BETA), "issuer_ids": [HARBOR_ISSUER],
             "operations": ["passport:read"]},
            {"client_id": "revoked-fund-001", "organization": "Revoked Fund",
             "key_sha256": hash_token(INST_KEY_DISABLED), "issuer_ids": [MERIDIAN_ISSUER],
             "operations": ["passport:read"], "enabled": False},
            *(extra_clients or []),
        ],
    }


TEST_REGISTRY = AccessRegistry.from_dict(build_test_access_config())


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
async def async_db() -> AsyncSession:
    """
    Returns a fresh in-memory SQLite DB and session for every test, made like the application's
    engine: foreign keys are enforced (Stage 10C-3B, T4).
    """
    engine = create_database_engine(_TEST_DB_URL, busy_timeout_seconds=5.0)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@asynccontextmanager
async def foreign_keys_unenforced(db: AsyncSession):
    """
    Plants a corrupt reference the way a database written before foreign keys were enforced
    (Stage 10C-3B, T4), or by another tool, can still contain one: for the tests proving that the
    application fails closed on such a record. Enforcement is off only inside the block, on the
    test's own connection, and is always restored.
    """
    await db.commit()                                   # PRAGMA foreign_keys is ignored in a transaction
    await db.execute(text("PRAGMA foreign_keys=OFF"))
    assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 0
    try:
        yield
        await db.commit()
    finally:
        await db.rollback()
        await db.execute(text("PRAGMA foreign_keys=ON"))
        assert (await db.execute(text("PRAGMA foreign_keys"))).scalar() == 1


@pytest.fixture
async def client(async_db: AsyncSession, tmp_path) -> AsyncClient:
    """
    HTTP client that sends NO credentials; every dependency uses its test version:
    - get_db   → in-memory SQLite
    - get_storage → temporary folder
    - get_ai_service → MockAIService (no API call)
    - get_access_registry → fictional test access registry
    Calls that need credentials use the header fixtures (e.g. issuer_headers)
    or the role clients (e.g. admin_client).
    """
    storage_svc = StorageService(str(tmp_path / "storage"))

    async def _override_get_db():
        yield async_db

    def _override_get_storage():
        return storage_svc

    def _override_get_ai_service():
        return MockAIService()

    def _override_get_stellar_service():
        return MockStellarService(scenario="found")

    # Rate limiters: a fresh high-limit instance for every test
    test_limiter = InMemoryRateLimiter(max_requests=1000, window_seconds=60)
    test_extraction_limiter = InMemoryRateLimiter(max_requests=1000, window_seconds=3600)

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    app.dependency_overrides[get_ai_service] = _override_get_ai_service
    app.dependency_overrides[get_stellar_service] = _override_get_stellar_service
    app.dependency_overrides[get_rate_limiter] = lambda: test_limiter
    app.dependency_overrides[get_extraction_rate_limiter] = lambda: test_extraction_limiter
    app.dependency_overrides[get_access_registry] = lambda: TEST_REGISTRY
    # Stellar anchoring: never the real account file and never the real network in tests
    # (tests/test_stellar_anchoring.py overrides this with a fake Horizon)
    unconfigured_anchoring = StellarAnchorService(
        str(tmp_path / "no-anchor-account.json"), base_fee=1000, tx_timeout_seconds=300,
        transport=httpx.MockTransport(_no_network),
    )
    app.dependency_overrides[get_anchor_service] = lambda: unconfigured_anchoring

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


def _no_network(request):
    raise AssertionError(f"tests must not reach the network: {request.url}")


async def _role_client(token: str):
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test", headers=bearer(token)
    ) as ac:
        yield ac


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    """Client that sends requests as platform_admin (global scope)."""
    async for ac in _role_client(ADMIN_TOKEN):
        yield ac


@pytest.fixture
async def issuer_client(client: AsyncClient) -> AsyncClient:
    """Client that sends requests as the Meridian issuer reviewer (Alice Chen)."""
    async for ac in _role_client(ISSUER_TOKEN):
        yield ac


@pytest.fixture
async def created_asset(admin_client: AsyncClient) -> dict:
    """Fictional asset registered to the Meridian issuer by the platform operator."""
    resp = await admin_client.post("/assets", json=_MERIDIAN_ASSET)
    assert resp.status_code == 201, f"Asset could not be created: {resp.text}"
    return resp.json()


@pytest.fixture
def minimal_pdf() -> bytes:
    """
    A valid, single-page blank PDF created with pypdf.
    Contains no text — behaves like a scanned document.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def source_pdf() -> bytes:
    """Two-page text PDF (tests/mock_ai.py MOCK_SOURCE_PAGES): the mock AI quotations verify."""
    return make_source_pdf()


@pytest.fixture
async def created_document(
    issuer_client: AsyncClient, created_asset: dict, source_pdf: bytes
) -> dict:
    """The Meridian issuer reviewer uploads a fictional document (the standard source PDF)."""
    asset_id = created_asset["id"]
    resp = await issuer_client.post(
        f"/assets/{asset_id}/documents",
        data={"document_type": "prospectus", "title": "Q4 2024 Prospectus"},
        files={"file": ("prospectus.pdf", source_pdf, "application/pdf")},
    )
    assert resp.status_code == 201, f"Document could not be uploaded: {resp.text}"
    return resp.json()


@pytest.fixture
async def created_extraction(issuer_client: AsyncClient, created_document: dict) -> dict:
    """Creates an extraction with the mock AI (draft, 7 claims)."""
    doc_id = created_document["id"]
    resp = await issuer_client.post(f"/documents/{doc_id}/extractions")
    assert resp.status_code == 201, f"Extraction could not be created: {resp.text}"
    return resp.json()


@pytest.fixture
def admin_headers() -> dict:
    """Bearer token header of the platform operator."""
    return bearer(ADMIN_TOKEN)


@pytest.fixture
def issuer_headers() -> dict:
    """Bearer token header of the issuer reviewer (Alice Chen, Meridian)."""
    return bearer(ISSUER_TOKEN)


@pytest.fixture
def legal_headers() -> dict:
    """Bearer token header of the independent legal expert (Bob Martinez, Meridian)."""
    return bearer(LEGAL_TOKEN)


@pytest.fixture
def second_legal_headers() -> dict:
    """A second independent legal expert of Meridian (Grace Osei)."""
    return bearer(SECOND_LEGAL_TOKEN)


@pytest.fixture
def ai_headers() -> dict:
    """Token header of the AI user — user_id starts with 'ai-', approval is rejected with 403."""
    return bearer(AI_TOKEN)


@pytest.fixture
def other_issuer_headers() -> dict:
    """Reviewer of another issuer (Harbor) — cannot access Meridian resources."""
    return bearer(OTHER_ISSUER_TOKEN)


@pytest.fixture
def other_legal_headers() -> dict:
    """Independent legal expert assigned only to Harbor."""
    return bearer(OTHER_LEGAL_TOKEN)


@pytest.fixture
async def approved_extraction(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
) -> dict:
    """Approves an extraction and returns the extraction data."""
    eid = created_extraction["id"]
    resp = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert resp.status_code == 201, f"Approval failed: {resp.text}"
    return created_extraction


@pytest.fixture
def inst_headers() -> dict:
    """Institutional client API key header (Meridian Capital Partners, Meridian scope)."""
    return {"X-API-Key": INST_KEY_ALPHA}


@pytest.fixture
def inst_beta_headers() -> dict:
    """Institutional client API key header (Apex Legal Research, Harbor scope only)."""
    return {"X-API-Key": INST_KEY_BETA}
