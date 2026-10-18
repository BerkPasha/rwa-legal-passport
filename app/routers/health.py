"""
Liveness and readiness.

- GET /health (liveness): the process is up and answers. It checks nothing else, so a load balancer
  or supervisor does not restart a server only because, say, its database is briefly unavailable.
- GET /ready (readiness, Stage 10C-3B, T2): the server can safely serve its workload now. It checks
  the required components: the database answers SELECT 1, the access registry is present and valid,
  and the storage root of the documents is usable. 200 {"status": "ready"} or
  503 {"status": "not_ready"}.

Both are public. /ready therefore answers with its status only: no path, issuer, registry content,
component name or error detail. Which component failed is logged on the server
(readiness.not_ready, with fixed reason codes or an exception type, never an exception message). Readiness is not an integrity check: it verifies no document
digest, no evidence manifest and no Stellar anchor, and it reaches no external service. Every check
is read-only and bounded in time; nothing is created, migrated or written.
"""

import asyncio
from pathlib import Path
from typing import Awaitable, Literal, Optional

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from app.access_registry import EMPTY_REGISTRY, get_access_registry, security_logger
from app.db import session as db_session
from app.services.storage import storage_root_problem

router = APIRouter(tags=["Health"])

# Per component; the components are checked concurrently, so a probe is answered within this bound
READINESS_TIMEOUT_SECONDS = 3.0


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    # Showcase deployment policies (Stage 10C-1), for the interface's labels; no configuration value
    read_only: bool = Field(description="Every write request is refused (READ_ONLY_MODE).")
    synthetic_data: bool = Field(description="The server declares all its data synthetic (SYNTHETIC_DATA_NOTICE).")
    api_docs: bool = Field(description="/docs, /redoc and /openapi.json are served (API_DOCS_ENABLED).")


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]


@router.get("/health", response_model=HealthResponse, summary="System health check")
async def health_check() -> HealthResponse:
    """
    Returns whether the server is up, together with basic information.
    """
    from app.config import settings

    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.app_env,
        read_only=settings.read_only_mode,
        synthetic_data=settings.synthetic_data_notice,
        api_docs=settings.api_docs_enabled,
    )


# ── Readiness ─────────────────────────────────────────────────────────────────


class _NotReady(Exception):
    """A required component is not usable; the reason is a fixed code."""


async def _check_database() -> None:
    engine = db_session.engine
    url = engine.url
    if url.get_backend_name() == "sqlite" and url.database not in (None, "", ":memory:"):
        # Connecting to a missing SQLite file would create an empty database (and report it ready)
        if not await run_in_threadpool(Path(url.database).is_file):
            raise _NotReady("database_file_missing")
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def _check_access_registry() -> None:
    # The registry every request uses: cached, parsed again only after the file has changed
    try:
        registry = get_access_registry()
    except HTTPException:
        raise _NotReady("invalid") from None        # logged as access_config.invalid by the registry
    if registry is EMPTY_REGISTRY:
        raise _NotReady("missing")                  # every protected request would be refused


def _check_storage() -> None:
    from app.config import settings

    # A read-only server (READ_ONLY_MODE) only reads documents; its storage may be mounted read-only
    problem = storage_root_problem(settings.storage_path, writable=not settings.read_only_mode)
    if problem:
        raise _NotReady(problem)


async def _failure(component: str, check: Awaitable[None]) -> Optional[str]:
    """None if the check passed, otherwise "<component>:<reason>" for the log (never a message)."""
    try:
        await asyncio.wait_for(check, READINESS_TIMEOUT_SECONDS)
    except TimeoutError:
        return f"{component}:timeout"
    except _NotReady as exc:
        return f"{component}:{exc}"
    except Exception as exc:                        # an unexpected failure is a 503 too, never a 500
        return f"{component}:{type(exc).__name__}"
    return None


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    summary="Readiness check",
    responses={503: {"model": ReadinessResponse, "description": "A required component is not usable."}},
)
async def readiness_check(response: Response) -> ReadinessResponse:
    """
    Returns whether the server can serve requests now: database, access registry and document
    storage. Unlike /health, a 503 here means "do not send traffic yet", not "restart".
    """
    results = await asyncio.gather(
        _failure("database", _check_database()),
        _failure("access_registry", run_in_threadpool(_check_access_registry)),
        _failure("storage", run_in_threadpool(_check_storage)),
    )
    failed = [result for result in results if result]
    if failed:
        security_logger.warning("readiness.not_ready failed=%s", ",".join(failed))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return ReadinessResponse(status="not_ready")
    return ReadinessResponse(status="ready")
