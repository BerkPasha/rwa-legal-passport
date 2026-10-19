from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError

from app.access_registry import describe_registry, load_access_registry, security_logger
from app.config import settings
from app.db.base import Base
from app.db.schema_check import verify_schema_columns
from app.db.session import check_sqlite_state, engine, sqlite_connection_state, sqlite_wal_applies
from app.deployment_checks import DEPLOYMENT_PROFILES, UnsafeDeploymentConfiguration, ensure_safe_configuration
from app.middleware import (
    ApiDocsGateMiddleware,
    BodySizeLimitMiddleware,
    ClientRateLimitMiddleware,
    ReadOnlyModeMiddleware,
    SecurityHeadersMiddleware,
)
from app.routers import (
    ai_status, assets, auth, blockchain, documents, extractions, health, passport, review, stellar_anchors,
)
from app.logging_config import configure_logging

# The application's INFO security and audit events reach the server log (Stage 10C-3B, T1;
# app/logging_config.py). Idempotent; an existing logging configuration is kept.
configure_logging()

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
    # Staging and production refuse to start with unsafe settings, before anything is read or
    # created (Stage 10C-3B, T3; app/deployment_checks.py). The log names settings, never values.
    try:
        profile = ensure_safe_configuration(settings)
    except UnsafeDeploymentConfiguration as exc:
        security_logger.error("deployment.configuration_refused profile=%s settings=%s",
                              exc.profile, ",".join(exc.settings))
        raise
    security_logger.info("deployment.profile profile=%s checks=%s", profile,
                         "passed" if profile in DEPLOYMENT_PROFILES else "not_applicable")
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
    # AI extraction provider (Stage 10A-2): the state only, never the key
    if settings.ai_provider == "anthropic":
        if settings.anthropic_api_key_configured:
            security_logger.info(
                "ai_provider.enabled provider=anthropic model=%s max_output_tokens=%d max_input_chars=%d "
                "max_pages=%d — document text is sent only for issuers with external_ai_processing and "
                "after per-request confirmation",
                settings.ai_model, settings.ai_max_output_tokens, settings.ai_max_input_chars, settings.ai_max_pages,
            )
        else:
            security_logger.warning(
                "ai_provider.not_configured provider=anthropic — ANTHROPIC_API_KEY is missing or malformed; "
                "extraction requests are refused and nothing is sent"
            )
    else:
        security_logger.info("ai_provider.disabled — no document text is sent to any AI service")
    # Showcase deployment policies (Stage 10C-1, GRANT_DEMO.md)
    security_logger.info(
        "deployment.policies read_only_mode=%s api_docs_enabled=%s synthetic_data_notice=%s "
        "client_rate_limit_per_minute=%d auth_failure_limit_per_minute=%d",
        settings.read_only_mode, settings.api_docs_enabled, settings.synthetic_data_notice,
        settings.client_rate_limit_per_minute, settings.auth_failure_limit_per_minute,
    )
    legacy = settings.legacy_credentials_configured
    if legacy:
        security_logger.warning(
            "legacy_credentials.ignored vars=%s — these variables no longer authenticate anything; "
            "follow the migration steps in ACCESS_CONTROL.md.", ",".join(legacy),
        )

    # On startup, check the existing tables for missing columns FIRST (an out-of-date database is
    # refused before anything is created in it), then create the tables that are missing (e.g.
    # the empty stellar_anchors table on an up-to-date database)
    async with engine.begin() as conn:
        # Before that, the connection itself (Stage 10C-3B, T4; app/db/session.py): foreign keys
        # enforced, and WAL if requested and applicable
        sqlite_state = await conn.run_sync(sqlite_connection_state)
        if sqlite_state is not None:
            check_sqlite_state(sqlite_state, wal_expected=sqlite_wal_applies(
                engine.url, settings.sqlite_journal_mode, settings.read_only_mode))
            security_logger.info(
                "database.sqlite foreign_keys=on journal_mode=%s busy_timeout_ms=%d wal_setting=%s",
                sqlite_state["journal_mode"], sqlite_state["busy_timeout_ms"], settings.sqlite_journal_mode,
            )
        await conn.run_sync(verify_schema_columns)
        await conn.run_sync(Base.metadata.create_all)
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

# Middleware order, innermost first (each one added wraps the ones before it):
# - read-only mode and the API docs gate (Stage 10C-1) refuse before authentication or a route;
# - the body size limit cuts off oversized requests with 413 before the multipart body is
#   buffered (and also before authentication);
# - the per-client rate limits (Stage 10C-2) refuse with 429 before any of them;
# - CORS wraps them, so that their 403/404/413 responses carry the CORS headers too;
# - the security headers are outermost and reach every response, preflights included.
app.add_middleware(ReadOnlyModeMiddleware)
app.add_middleware(ApiDocsGateMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(ClientRateLimitMiddleware)

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
app.add_middleware(SecurityHeadersMiddleware)

@app.exception_handler(OperationalError)
async def database_unavailable(request: Request, exc: OperationalError) -> JSONResponse:
    """
    A lock held longer than the busy timeout, or a database that cannot be used, on a route that
    does not answer it itself (Stage 10C-3B, T4): 503, never the statement, its parameters or a
    path. The request's session is closed by get_db, which rolls its transaction back. Constraint
    violations (IntegrityError) and programming errors are not handled here.
    """
    route = request.scope.get("route")
    reason = "locked" if "locked" in str(getattr(exc, "orig", "")).lower() else "unavailable"
    security_logger.warning("database.unavailable route=%s reason=%s", getattr(route, "path", "-"), reason)
    return JSONResponse(status_code=503, content={"detail": "The database is busy or unavailable; please retry."})


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(assets.router)
app.include_router(documents.router)
app.include_router(extractions.router)
app.include_router(ai_status.router)
app.include_router(review.router)
app.include_router(blockchain.router)
app.include_router(passport.router)
app.include_router(stellar_anchors.router)


@app.get("/", include_in_schema=False)
async def root():
    if not settings.api_docs_enabled:
        return {"message": f"{settings.app_name} v{settings.app_version}."}
    return {"message": f"{settings.app_name} v{settings.app_version} — visit /docs."}
