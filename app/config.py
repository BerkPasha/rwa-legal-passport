import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Scheme + host[:port] only; must not contain a path, query or wildcard.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:[0-9]{1,5})?$|^https?://\[[0-9A-Fa-f:]+\](:[0-9]{1,5})?$")


class Settings(BaseSettings):
    app_name: str = "RWA Legal Passport"
    app_version: str = "0.1.0"
    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./rwa_legal_passport.db"
    max_upload_size_mb: int = Field(default=10, ge=0, le=200)
    storage_path: str = "./storage"
    # Resource limits for parsing untrusted PDFs (Stage 9B-2)
    max_pdf_pages: int = Field(default=500, ge=1, le=10_000)
    max_pdf_text_chars: int = Field(default=2_000_000, ge=1_000, le=50_000_000)
    pdf_parse_timeout_seconds: float = Field(default=30.0, gt=0, le=600)
    # AI service — add to the .env file for real use
    anthropic_api_key: str = ""
    ai_model: str = "claude-haiku-4-5-20251001"

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
