import re
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Scheme + host[:port] only; must not contain a path, query or wildcard.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^https?://\[[0-9A-Fa-f:]+\](:[0-9]{1,5})?$")

# ── AI extraction provider (Stage 10A-2, AI_EXTRACTION.md) ────────────────────
# Models accepted with AI_PROVIDER=anthropic. Extend deliberately, after evaluating a model on the
# synthetic fixtures; an unknown or retired ID is refused at startup instead of failing per request.
SUPPORTED_AI_MODELS = frozenset({
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "claude-sonnet-4-6",   # legacy, active; the owner's choice for the first live test (Stage 10A-4)
    "claude-haiku-5-5",
    "claude-haiku-4-5",
    "claude-haiku-4-5-20251001",
})
DEFAULT_AI_MODEL = "claude-opus-5-5"
DEFAULT_AI_MAX_OUTPUT_TOKENS = 8192
DEFAULT_AI_MAX_INPUT_CHARS = 200_000
DEFAULT_AI_MAX_PAGES = 200
DEFAULT_AI_REQUEST_TIMEOUT_SECONDS = 300.0
# Format of an Anthropic API key; anything else (empty, a placeholder) counts as not configured,
# so that no document is ever sent with it
_ANTHROPIC_KEY_RE = re.compile(r"^sk-ant-[A-Za-z0-9_-]{20,}$")


class Settings(BaseSettings):
    app_name: str = "RWA Legal Passport"
    app_version: str = "0.1.0"
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./rwa_legal_passport.db"
    # ── SQLite (Stage 10C-3B, T4; DEPLOYMENT.md §9) ───────────────────────────
    # Seconds a connection waits for another connection's lock before the request fails with 503
    # (5 is the sqlite3 module's own default)
    sqlite_busy_timeout_seconds: float = Field(default=5.0, ge=0, le=60)
    # "unchanged" (default): the database keeps its journal mode (a new file uses the rollback
    # journal). "wal": write-ahead logging for a writable file database; stored in the file, it stays
    # after this setting is removed. Never applied in memory or with READ_ONLY_MODE.
    sqlite_journal_mode: Literal["unchanged", "wal"] = "unchanged"
    max_upload_size_mb: int = Field(default=10, ge=0, le=200)
    storage_path: str = "./storage"
    # Resource limits for parsing untrusted PDFs (Stage 9B-2)
    max_pdf_pages: int = Field(default=500, ge=1, le=10_000)
    max_pdf_text_chars: int = Field(default=2_000_000, ge=1_000, le=50_000_000)
    pdf_parse_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    # ── AI extraction (Stage 10A-2, AI_EXTRACTION.md) ─────────────────────────
    # "disabled" (default): no document text is ever sent to an AI service; extraction returns
    # 503 ai_provider_disabled. "anthropic": the text of a document version is sent to Anthropic,
    # only for issuers authorized in access_control.json and after the user's per-request
    # confirmation. The key is server-side only and is never logged or returned.
    ai_provider: Literal["disabled", "anthropic"] = "disabled"
    anthropic_api_key: SecretStr = SecretStr("")
    ai_model: str = DEFAULT_AI_MODEL
    # Output bound per request (16000 keeps a non-streaming request well inside the SDK's limit)
    ai_max_output_tokens: int = Field(default=DEFAULT_AI_MAX_OUTPUT_TOKENS, ge=1024, le=16_000)
    # Input bounds per request, checked before anything is sent: characters of the complete model
    # input (instructions + page markers + page text) and pages of the document version
    ai_max_input_chars: int = Field(default=DEFAULT_AI_MAX_INPUT_CHARS, ge=10_000, le=1_000_000)
    ai_max_pages: int = Field(default=DEFAULT_AI_MAX_PAGES, ge=1, le=10_000)
    # One attempt per request (the SDK's automatic retries are disabled), bounded in time
    ai_request_timeout_seconds: float = Field(default=DEFAULT_AI_REQUEST_TIMEOUT_SECONDS, ge=10, le=600)

    # ── Access control ───────────────────────────────────────────────────────
    # Users, roles, issuer scopes and institutional keys are defined in this JSON
    # file. The file contains only SHA-256 digests of tokens/keys; it contains no
    # plaintext credentials. If the file is missing, every protected
    # endpoint is denied (fail-closed). See ACCESS_CONTROL.md
    access_control_file: str = "./access_control.json"

    # CORS allowlist for browser clients (comma-separated origins).
    # "*" is not accepted. If left empty, no cross-origin requests are allowed.
    cors_allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # AI extraction trigger limit (per user, sliding 1-hour window).
    extraction_rate_limit_per_hour: int = Field(default=20, ge=1, le=10_000)

    # ── Stellar Testnet evidence anchoring (STELLAR_ANCHORING.md) ─────────────
    # JSON file of the Testnet-only signing account: {"network", "public_key", "secret_seed"}.
    # Keep it out of version control (secrets/ is gitignored). Missing file: anchoring is
    # disabled (503) and verification reports anchoring_not_configured.
    stellar_anchor_secret_file: str = "./secrets/stellar_testnet_anchor.json"
    # Fee per operation in stroops, and how long a signed anchoring transaction stays valid
    stellar_anchor_base_fee: int = Field(default=1000, ge=100, le=1_000_000)
    stellar_anchor_tx_timeout_seconds: int = Field(default=300, ge=30, le=3600)

    # ── Showcase deployment (Stage 10C-1, GRANT_DEMO.md) ──────────────────────
    # READ_ONLY_MODE: every request other than GET, HEAD and OPTIONS is refused with 403 before
    # it reaches authentication or a route, whatever the credential (app/middleware.py).
    read_only_mode: bool = False
    # API_DOCS_ENABLED: serve /docs, /redoc and /openapi.json. Turn off on public deployments.
    api_docs_enabled: bool = True
    # SYNTHETIC_DATA_NOTICE: the server declares that all its data is synthetic and fictional;
    # the interface labels every page accordingly. Set it only for demo datasets.
    synthetic_data_notice: bool = False
    # Per client address and minute (Stage 10C-2, DEPLOYMENT.md); 0 turns a limit off. Behind a
    # reverse proxy, run uvicorn with --proxy-headers and --forwarded-allow-ips set to the proxy, so
    # that the address is the visitor's, not the proxy's.
    client_rate_limit_per_minute: int = Field(default=0, ge=0, le=100_000)
    # Failed authentications (401) per client address and minute; reaching it refuses every request
    # from that address with 429 until the minute has passed.
    auth_failure_limit_per_minute: int = Field(default=0, ge=0, le=10_000)

    # DEPRECATED — single-token credentials from before Stage 9B-1.
    # They no longer authenticate any request; they are read only to warn at
    # startup if still set in .env. Migration: `python -m app.access_tool hash-env <NAME>`
    reviewer_issuer_token: str = ""
    reviewer_legal_token: str = ""
    institutional_api_key_alpha: str = ""
    institutional_api_key_beta: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    @field_validator("cors_allowed_origins")
    @classmethod
    def _validate_cors_origins(cls, value: str) -> str:
        origins = [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        for origin in origins:
            if "*" in origin:
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must not contain a wildcard ('*'); "
                    "list each allowed origin explicitly."
                )
            if not _ORIGIN_RE.match(origin):
                raise ValueError(
                    f"Invalid CORS origin: {origin!r}. Expected format: "
                    "http(s)://host[:port] (no path or query)."
                )
        return ",".join(origins)

    @model_validator(mode="after")
    def _validate_ai_model(self) -> "Settings":
        # Only checked when the provider is enabled: a stale AI_MODEL never blocks a disabled server
        if self.ai_provider == "anthropic" and self.ai_model not in SUPPORTED_AI_MODELS:
            raise ValueError(
                f"AI_MODEL {self.ai_model!r} is not supported with AI_PROVIDER=anthropic; "
                f"supported: {', '.join(sorted(SUPPORTED_AI_MODELS))}."
            )
        return self

    @property
    def anthropic_api_key_configured(self) -> bool:
        """True if ANTHROPIC_API_KEY has the format of an Anthropic key (the value is never exposed)."""
        return bool(_ANTHROPIC_KEY_RE.fullmatch(self.anthropic_api_key.get_secret_value()))

    @property
    def cors_origins(self) -> list[str]:
        return [o for o in self.cors_allowed_origins.split(",") if o]

    @property
    def legacy_credentials_configured(self) -> list[str]:
        """Names of the deprecated credential variables that are still set."""
        names = {
            "REVIEWER_ISSUER_TOKEN": self.reviewer_issuer_token,
            "REVIEWER_LEGAL_TOKEN": self.reviewer_legal_token,
            "INSTITUTIONAL_API_KEY_ALPHA": self.institutional_api_key_alpha,
            "INSTITUTIONAL_API_KEY_BETA": self.institutional_api_key_beta,
        }
        return [name for name, value in names.items() if value]


settings = Settings()
