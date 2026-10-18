"""
Access-control registry: issuers (tenants), Bearer users and institutional
API clients.

Source: settings.access_control_file (JSON). Example: access_control.example.json

PRIVACY:
- The file contains no plaintext tokens/keys; only SHA-256 digests are stored.
  An incoming credential is hashed and matched by its digest.
- Validation error messages do not include input values (digests included).

FAIL-CLOSED:
- If the file is missing, the registry is empty → every protected endpoint returns 401/403.
- If the file is invalid, the application does not start; if it becomes corrupt at
  runtime, requests receive 503.
- When the file changes (mtime/size) it is reloaded; credentials can be revoked
  without a restart.
"""

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Annotated, Any, Mapping, Optional

from fastapi import HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StringConstraints, ValidationError, model_validator

from app.config import settings
from app.enums import ReviewerRole, UserRole
from app.permissions import ROLE_PERMISSIONS, InstitutionalOperation, Permission

security_logger = logging.getLogger("rwa_security")

ISSUER_ID_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
_SHA256_PATTERN = r"^[0-9a-f]{64}$"
_PRINCIPAL_ID_PATTERN = r"^[a-z0-9][a-z0-9._-]{2,63}$"

IssuerId = Annotated[str, StringConstraints(pattern=ISSUER_ID_PATTERN)]
Sha256Hex = Annotated[str, StringConstraints(pattern=_SHA256_PATTERN)]
PrincipalId = Annotated[str, StringConstraints(pattern=_PRINCIPAL_ID_PATTERN)]


def hash_token(token: str) -> str:
    """SHA-256 digest of a token / API key (64 characters, lowercase hex)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class AccessConfigError(Exception):
    """The access configuration file could not be read or is invalid."""


# ── Authenticated principals ─────────────────────────────────────────────────


class Principal(BaseModel):
    """User authenticated with a Bearer token."""
    model_config = ConfigDict(frozen=True)

    user_id: str
    display_name: str
    role: UserRole
    # Accessible issuers; empty for platform_admin (global scope)
    issuer_ids: frozenset[str] = frozenset()

    @property
    def has_global_scope(self) -> bool:
        return self.role == UserRole.platform_admin

    @property
    def permissions(self) -> frozenset[Permission]:
        return ROLE_PERMISSIONS[self.role]

    @property
    def reviewer_role(self) -> Optional[ReviewerRole]:
        """Role used in review records; None if the user is not a reviewer."""
        try:
            return ReviewerRole(self.role.value)
        except ValueError:
            return None

    def has_permission(self, permission: Permission) -> bool:
        return permission in self.permissions

    def can_access_issuer(self, issuer_id: Optional[str]) -> bool:
        if self.has_global_scope:
            return True
        return issuer_id is not None and issuer_id in self.issuer_ids


class InstitutionalClient(BaseModel):
    """Institutional client authenticated with X-API-Key."""
    model_config = ConfigDict(frozen=True)

    client_id: str
    organization: str
    issuer_ids: frozenset[str] = frozenset()
    asset_ids: frozenset[str] = frozenset()
    operations: frozenset[InstitutionalOperation] = frozenset()

    def allows(self, operation: InstitutionalOperation) -> bool:
        return operation in self.operations

    def can_access_asset(self, asset_id: str, issuer_id: Optional[str]) -> bool:
        """True if the asset belongs to a permitted issuer or is listed explicitly."""
        return asset_id in self.asset_ids or (
            issuer_id is not None and issuer_id in self.issuer_ids
        )


# ── Configuration file schema ────────────────────────────────────────────────


class _StrictModel(BaseModel):
    # Unknown fields are rejected: fields such as an accidentally added plaintext
    # "token" are not silently ignored.
    model_config = ConfigDict(extra="forbid")


class IssuerConfig(_StrictModel):
    id: IssuerId
    name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    # The issuer has authorized sending the text of its documents to an external AI provider
    # (Stage 10A-2, AI_EXTRACTION.md). Off unless set to true explicitly.
    external_ai_processing: StrictBool = False


class UserConfig(_StrictModel):
    user_id: PrincipalId
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    role: UserRole
    issuer_ids: list[IssuerId] = Field(default_factory=list)
    token_sha256: Sha256Hex
    enabled: bool = True

    @model_validator(mode="after")
    def _check_scope(self) -> "UserConfig":
        if self.role == UserRole.platform_admin and self.issuer_ids:
            raise ValueError(
                "platform_admin has global scope; issuer_ids must not be set"
            )
        if self.role != UserRole.platform_admin and not self.issuer_ids:
            raise ValueError(f"{self.role.value} role requires at least one issuer_ids entry")
        return self


class InstitutionalClientConfig(_StrictModel):
    client_id: PrincipalId
    organization: Annotated[str, StringConstraints(min_length=1, max_length=255)]
    key_sha256: Sha256Hex
    issuer_ids: list[IssuerId] = Field(default_factory=list)
    asset_ids: list[Annotated[str, StringConstraints(min_length=1, max_length=36)]] = Field(
        default_factory=list
    )
    operations: list[InstitutionalOperation] = Field(min_length=1)
    enabled: bool = True

    @model_validator(mode="after")
    def _check_scope(self) -> "InstitutionalClientConfig":
        if not self.issuer_ids and not self.asset_ids:
            raise ValueError("institutional client requires at least one issuer_ids or asset_ids entry")
        return self


class AccessConfig(_StrictModel):
    issuers: list[IssuerConfig] = Field(default_factory=list)
    users: list[UserConfig] = Field(default_factory=list)
    institutional_clients: list[InstitutionalClientConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_references(self) -> "AccessConfig":
        issuer_ids = [i.id for i in self.issuers]
        _require_unique(issuer_ids, "issuers[].id")
        _require_unique([u.user_id for u in self.users], "users[].user_id")
        _require_unique(
            [c.client_id for c in self.institutional_clients], "institutional_clients[].client_id"
        )
        # The same digest cannot be used by both a user and a client (no identity mix-up)
        _require_unique(
            [u.token_sha256 for u in self.users]
            + [c.key_sha256 for c in self.institutional_clients],
            "token_sha256 / key_sha256",
        )
        known = set(issuer_ids)
        for user in self.users:
            unknown = sorted(set(user.issuer_ids) - known)
            if unknown:
                raise ValueError(f"users[{user.user_id}] references unknown issuer(s): {unknown}")
        for client in self.institutional_clients:
            unknown = sorted(set(client.issuer_ids) - known)
            if unknown:
                raise ValueError(
                    f"institutional_clients[{client.client_id}] references unknown issuer(s): {unknown}"
                )
        return self


def _require_unique(values: list[str], label: str) -> None:
    if len(values) != len(set(values)):
        # The values themselves (which may be digests) are not added to the message
        raise ValueError(f"{label} must be unique")


# ── Registry ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AccessRegistry:
    issuers: Mapping[str, IssuerConfig] = field(default_factory=dict)
    users_by_token_hash: Mapping[str, Principal] = field(default_factory=dict)
    clients_by_key_hash: Mapping[str, InstitutionalClient] = field(default_factory=dict)

    @classmethod
    def from_config(cls, config: AccessConfig) -> "AccessRegistry":
        users = {
            u.token_sha256: Principal(
                user_id=u.user_id,
                display_name=u.display_name,
                role=u.role,
                issuer_ids=frozenset(u.issuer_ids),
            )
            for u in config.users
            if u.enabled
        }
        clients = {
            c.key_sha256: InstitutionalClient(
                client_id=c.client_id,
                organization=c.organization,
                issuer_ids=frozenset(c.issuer_ids),
                asset_ids=frozenset(c.asset_ids),
                operations=frozenset(c.operations),
            )
            for c in config.institutional_clients
            if c.enabled
        }
        return cls(
            issuers={i.id: i for i in config.issuers},
            users_by_token_hash=users,
            clients_by_key_hash=clients,
        )

    @classmethod
    def from_dict(cls, data: Any) -> "AccessRegistry":
        return cls.from_config(parse_access_config(data))

    def allows_external_ai(self, issuer_id: Optional[str]) -> bool:
        """True only if the issuer is registered and has set external_ai_processing (fail-closed)."""
        issuer = self.issuers.get(issuer_id) if issuer_id else None
        return issuer is not None and issuer.external_ai_processing

    def authenticate_user(self, token: str) -> Optional[Principal]:
        if not token:
            return None
        return self.users_by_token_hash.get(hash_token(token))

    def authenticate_client(self, api_key: str) -> Optional[InstitutionalClient]:
        if not api_key:
            return None
        return self.clients_by_key_hash.get(hash_token(api_key))


EMPTY_REGISTRY = AccessRegistry()


def parse_access_config(data: Any) -> AccessConfig:
    try:
        return AccessConfig.model_validate(data)
    except ValidationError as exc:
        # include_input=False: digest/key values do not leak into the error text
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
            for err in exc.errors(include_input=False, include_url=False)
        )
        raise AccessConfigError(f"access configuration is invalid — {problems}") from None


def load_access_registry(path: Path) -> AccessRegistry:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AccessConfigError(
            f"access configuration is not valid JSON (line {exc.lineno}, column {exc.colno})"
        ) from None
    except OSError as exc:
        raise AccessConfigError(f"access configuration could not be read: {type(exc).__name__}") from None
    return AccessRegistry.from_dict(raw)


# ── FastAPI dependency ───────────────────────────────────────────────────────

_cache_lock = Lock()
_cache: dict[str, tuple[tuple[int, int], AccessRegistry]] = {}
_missing_warned: set[str] = set()


def get_access_registry() -> AccessRegistry:
    """
    Returns the current access registry (reloads it if the file has changed).
    Replaced with fictional credentials in tests.
    """
    path = Path(settings.access_control_file)
    try:
        stat = path.stat()
    except FileNotFoundError:
        key = str(path)
        if key not in _missing_warned:
            _missing_warned.add(key)
            security_logger.warning(
                "access_config.missing path=%s — all protected endpoints will be denied", path
            )
        return EMPTY_REGISTRY

    key = str(path.resolve())
    fingerprint = (stat.st_mtime_ns, stat.st_size)
    with _cache_lock:
        cached = _cache.get(key)
        if cached and cached[0] == fingerprint:
            return cached[1]
        try:
            registry = load_access_registry(path)
        except AccessConfigError as exc:
            security_logger.error("access_config.invalid path=%s error=%s", path, exc)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Access configuration is invalid; contact the system administrator.",
            ) from None
        _cache[key] = (fingerprint, registry)
        _missing_warned.discard(str(path))
        return registry


def describe_registry(registry: AccessRegistry) -> str:
    """Summary for logging (contains no credentials)."""
    roles: dict[str, int] = {}
    for principal in registry.users_by_token_hash.values():
        roles[principal.role.value] = roles.get(principal.role.value, 0) + 1
    role_text = ", ".join(f"{k}={v}" for k, v in sorted(roles.items())) or "none"
    external_ai = sum(1 for issuer in registry.issuers.values() if issuer.external_ai_processing)
    return (
        f"issuers={len(registry.issuers)} users=[{role_text}] "
        f"institutional_clients={len(registry.clients_by_key_hash)} "
        f"issuers_authorizing_external_ai={external_ai}"
    )


_SAFE_ID = re.compile(r"[^A-Za-z0-9._:-]")


def safe_log_id(value: str) -> str:
    """Sanitizes user-supplied identifiers before they are written to the log (log injection)."""
    return _SAFE_ID.sub("?", value)[:64]
