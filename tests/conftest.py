"""
Test fixtures.

Her test için:
- Ayrı in-memory SQLite veritabanı (tam izolasyon)
- Ayrı geçici klasör (dosya depolama izolasyonu)
- Mock AI servisi (Anthropic API'ye gerçek istek gönderilmez)
- Kurgusal test token'ları / API anahtarları (.env'deki gerçek değerler kullanılmaz)
"""

import io

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api_keys import InstitutionalClient, get_client_registry
from app.auth import ReviewerInfo, get_reviewer_registry
from app.db.base import Base
from app.db.session import get_db
from app.enums import ReviewerRole
from app.main import app
from app.rate_limit import InMemoryRateLimiter, get_rate_limiter
from app.services.ai import get_ai_service
from app.services.stellar import get_stellar_service
from app.services.storage import StorageService, get_storage
from tests.mock_ai import MockAIService
from tests.mock_stellar import MockStellarService

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

# Phase 2 ve Phase 3 testlerinde kullanılan kurgusal varlık
_MERIDIAN_ASSET = {
    "blockchain_network": "stellar-mainnet",
    "stellar_asset_code": "MRPROP",
    "stellar_issuer_public_key": "GCEZFBCKNOXYELQZFQ7JKPWQABYOPZKVIPQLNOILMWMEXGWAF7RQJCUQ",
    "issuer_organization_name": "Meridian Property Holdings LLC",
    "asset_type": "real_estate",
    "jurisdiction": "US-NY",
}

# ── KURGUSAL TEST KİMLİK BİLGİLERİ — GİZLİ DEĞİLDİR ───────────────────────────
# Yalnızca bu test paketinde, bağımlılık geçersiz kılma ile geçerlidir.
# Çalışan uygulama bunları tanımaz; gerçek değerler yalnızca .env dosyasındadır.
_TEST_REVIEWERS: dict[str, ReviewerInfo] = {
    "test-issuer-token": ReviewerInfo(
        user_id="alice-chen-01",
        display_name="Alice Chen",
        role=ReviewerRole.issuer_reviewer,
    ),
    "test-legal-token": ReviewerInfo(
        user_id="bob-martinez-01",
        display_name="Bob Martinez",
        role=ReviewerRole.independent_legal_reviewer,
    ),
    # Kod katmanı testine özgü: user_id "ai-" ile başlıyor → onay 403 ile reddedilir
    "test-ai-token": ReviewerInfo(
        user_id="ai-extraction-service",
        display_name="AI Extraction Service",
        role=ReviewerRole.issuer_reviewer,
    ),
}

_TEST_INSTITUTIONAL_CLIENTS: dict[str, InstitutionalClient] = {
    "test-inst-key-alpha": InstitutionalClient(
        client_id="meridian-capital-001",
        organization="Meridian Capital Partners",
        tier="read",
    ),
    "test-inst-key-beta": InstitutionalClient(
        client_id="apex-legal-research-001",
        organization="Apex Legal Research Inc.",
        tier="read",
    ),
}


@pytest.fixture
async def async_db() -> AsyncSession:
    """Her test için taze bir in-memory SQLite DB ve session döndürür."""
    engine = create_async_engine(_TEST_DB_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture
async def client(async_db: AsyncSession, tmp_path) -> AsyncClient:
    """
    Tüm bağımlılıkları test versiyonlarıyla değiştirmiş HTTP istemcisi:
    - get_db   → in-memory SQLite
    - get_storage → geçici klasör
    - get_ai_service → MockAIService (API çağrısı yok)
    - get_reviewer_registry / get_client_registry → kurgusal test kimlik bilgileri
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

    # Rate limiter: her test için taze yüksek limitli örnek (normal testler sınıra çarpmaz)
    test_limiter = InMemoryRateLimiter(max_requests=1000, window_seconds=60)

    def _override_get_rate_limiter():
        return test_limiter

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_storage] = _override_get_storage
    app.dependency_overrides[get_ai_service] = _override_get_ai_service
    app.dependency_overrides[get_stellar_service] = _override_get_stellar_service
    app.dependency_overrides[get_rate_limiter] = _override_get_rate_limiter
    app.dependency_overrides[get_reviewer_registry] = lambda: _TEST_REVIEWERS
    app.dependency_overrides[get_client_registry] = lambda: _TEST_INSTITUTIONAL_CLIENTS

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest.fixture
async def created_asset(client: AsyncClient) -> dict:
    """Kurgusal bir varlık kaydı oluşturur."""
    resp = await client.post("/assets", json=_MERIDIAN_ASSET)
    assert resp.status_code == 201, f"Varlık oluşturulamadı: {resp.text}"
    return resp.json()


@pytest.fixture
def minimal_pdf() -> bytes:
    """
    pypdf ile oluşturulmuş geçerli, tek sayfalık boş bir PDF.
    Metin içermez — taramalı belgeler gibi davranır.
    """
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
async def created_document(client: AsyncClient, created_asset: dict, minimal_pdf: bytes) -> dict:
    """Kurgusal bir belge yükler ve meta verilerini döndürür."""
    asset_id = created_asset["id"]
    resp = await client.post(
        f"/assets/{asset_id}/documents",
        data={"document_type": "prospectus", "title": "Q4 2024 Prospectus"},
        files={"file": ("prospectus.pdf", minimal_pdf, "application/pdf")},
    )
    assert resp.status_code == 201, f"Belge yüklenemedi: {resp.text}"
    return resp.json()


@pytest.fixture
async def created_extraction(client: AsyncClient, created_document: dict) -> dict:
    """Mock AI ile bir çıkarım oluşturur (taslak, 7 iddia)."""
    doc_id = created_document["id"]
    resp = await client.post(f"/documents/{doc_id}/extractions")
    assert resp.status_code == 201, f"Çıkarım oluşturulamadı: {resp.text}"
    return resp.json()


@pytest.fixture
def issuer_headers() -> dict:
    """İhraç eden inceleyici (Alice Chen) Bearer token başlığı."""
    return {"Authorization": "Bearer test-issuer-token"}


@pytest.fixture
def legal_headers() -> dict:
    """Bağımsız hukuk uzmanı (Bob Martinez) Bearer token başlığı."""
    return {"Authorization": "Bearer test-legal-token"}


@pytest.fixture
def ai_headers() -> dict:
    """AI kullanıcısı token başlığı — user_id 'ai-' ile başlar, onay 403 ile reddedilir."""
    return {"Authorization": "Bearer test-ai-token"}


@pytest.fixture
async def approved_extraction(
    client: AsyncClient,
    created_extraction: dict,
    issuer_headers: dict,
) -> dict:
    """Bir çıkarımı onaylar ve çıkarım verisini döndürür."""
    eid = created_extraction["id"]
    resp = await client.post(
        f"/extractions/{eid}/approvals", headers=issuer_headers, json={}
    )
    assert resp.status_code == 201, f"Onay başarısız: {resp.text}"
    return created_extraction


@pytest.fixture
def inst_headers() -> dict:
    """Kurumsal istemci API anahtarı başlığı (Meridian Capital Partners)."""
    return {"X-API-Key": "test-inst-key-alpha"}
