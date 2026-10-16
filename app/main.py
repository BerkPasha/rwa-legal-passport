from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.access_registry import describe_registry, load_access_registry, security_logger
from app.config import settings
from app.db.base import Base
from app.db.schema_check import verify_schema_columns
from app.db.session import engine
from app.middleware import BodySizeLimitMiddleware
from app.routers import assets, auth, blockchain, documents, extractions, health, passport, review

# ── OpenAPI tag metadata ──────────────────────────────────────────────────────
_tags_metadata = [
    {
        "name": "Health",
        "description": "System health check.",
    },
    {
        "name": "Auth",
        "description": "The user, role and issuer scope that a Bearer token is bound to.",
    },
    {
        "name": "Assets",
        "description": (
            "Tokenized asset registry — create, list, update. "
            "Requires a Bearer token; results are limited to the user's issuer scope."
        ),
    },
    {
        "name": "Documents",
        "description": "Legal document management — PDF upload, SHA-256 verification, versioning.",
    },
    {
        "name": "Legal Extractions",
        "description": (
            "AI legal-rights extraction engine. "
            "Returned results are always in 'draft' status; automatic approval is never applied."
        ),
    },
    {
        "name": "Legal Review",
        "description": (
            "Review and approval workflow. "
            "Only authorized reviewers can make corrections and approvals (Bearer token)."
        ),
    },
    {
        "name": "Blockchain Verification",
        "description": (
            "Read-only Stellar network verification. "
            "Requires a Bearer token; a blockchain record does not prove legal ownership."
        ),
    },
    {
        "name": "Legal Passport",
        "description": (
            "**Institutional client API** — approved legal rights and restrictions. "
            "Requires authentication with the `X-API-Key` header; a key can only read "
            "passports of the issuers / assets within its scope. "
            "Only approved information is shown; draft information is never exposed."
        ),
    },
]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Refuse to start if the access configuration is invalid (fail-closed); warn if the file is missing
    access_path = Path(settings.access_control_file)
    if access_path.exists():
        registry = load_access_registry(access_path)
        security_logger.info("access_config.loaded path=%s %s", access_path, describe_registry(registry))
    else:
        security_logger.warning(
            "access_config.missing path=%s — all protected endpoints will be denied. "
            "See ACCESS_CONTROL.md", access_path,
        )
    legacy = settings.legacy_credentials_configured
    if legacy:
        security_logger.warning(
            "legacy_credentials.ignored vars=%s — these variables no longer authenticate anything; "
            "follow the migration steps in ACCESS_CONTROL.md.", ",".join(legacy),
        )

    # On startup, create the SQLite tables (if missing) and check for missing columns
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(verify_schema_columns)
    yield


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Institutional legal transparency API for tokenized real-world assets. "
        "Combines AI-assisted legal-rights extraction, human review, an approval workflow and "
        "blockchain verification in a single passport API."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=_tags_metadata,
    lifespan=lifespan,
)

# Body size limit: cuts off oversized requests with 413 before the multipart body is
# buffered (and also before authentication). Added before CORS, so that CORS stays the
# outermost layer and the 413 response also carries the CORS headers.
app.add_middleware(BodySizeLimitMiddleware)

# CORS: configured origins only (wildcards are rejected in Settings).
# Credentials travel in headers; cookies are not used, hence allow_credentials=False.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-API-Key"],
    allow_credentials=False,
    max_age=600,
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(documents.router)
app.include_router(extractions.router)
app.include_router(review.router)
app.include_router(blockchain.router)
app.include_router(passport.router)


@app.get("/", include_in_schema=False)
async def root():
    return {"message": f"{settings.app_name} v{settings.app_version} — visit /docs."}
