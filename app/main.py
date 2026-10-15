from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.db.base import Base
from app.db.session import engine
from app.routers import assets, blockchain, documents, extractions, health, passport, review

# ── OpenAPI etiket meta verileri ──────────────────────────────────────────────
_tags_metadata = [
    {
        "name": "Health",
        "description": "Sistem sağlık kontrolü.",
    },
    {
        "name": "Assets",
        "description": "Tokenize edilmiş varlık kaydı — oluşturma, listeleme, güncelleme.",
    },
    {
        "name": "Documents",
        "description": "Yasal belge yönetimi — PDF yükleme, SHA-256 doğrulama, versiyonlama.",
    },
    {
        "name": "Legal Extractions",
        "description": (
            "AI yasal hak çıkarım motoru. "
            "Dönen sonuçlar her zaman 'draft' durumundadır; otomatik onay uygulanmaz."
        ),
    },
    {
        "name": "Legal Review",
        "description": (
            "İnceleme ve onay iş akışı. "
            "Yalnızca yetkili inceleyiciler düzeltme ve onay yapabilir (Bearer token)."
        ),
    },
    {
        "name": "Blockchain Verification",
        "description": (
            "Salt okunur Stellar ağ doğrulaması. "
            "Kimlik doğrulama gerekmez; blockchain kaydı yasal mülkiyeti kanıtlamaz."
        ),
    },
    {
        "name": "Legal Passport",
        "description": (
            "**Kurumsal müşteri API'si** — onaylanmış yasal haklar ve kısıtlamalar. "
            "`X-API-Key` başlığı ile kimlik doğrulama gerektirir. "
            "Yalnızca onaylanmış bilgi gösterilir; taslak bilgi asla açığa çıkmaz."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uygulama başlarken SQLite tablolarını oluştur (yoksa)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Tokenize edilmiş gerçek dünya varlıkları için kurumsal yasal şeffaflık API'si. "
        "AI destekli yasal hak çıkarımı, insan incelemesi, onay iş akışı ve "
        "blockchain doğrulamasını tek bir pasaport API'sinde birleştirir."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=_tags_metadata,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(assets.router)
app.include_router(documents.router)
app.include_router(extractions.router)
app.include_router(review.router)
app.include_router(blockchain.router)
app.include_router(passport.router)


@app.get("/", include_in_schema=False)
async def root():
    return {"message": f"{settings.app_name} v{settings.app_version} — /docs adresini ziyaret edin."}
