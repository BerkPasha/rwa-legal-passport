"""
Deployment profiles and the startup checks of staging and production (Stage 10C-3B, T3;
DEPLOYMENT.md §8).

APP_ENV selects a profile, optionally followed by a label that identifies the server:
"<profile>" or "<profile>-<label>", e.g. "production-showcase" or "test-e2e-synthetic".
- development, test: this computer only. Nothing is checked; the development defaults apply
  (API docs on, the local frontend origins, no per-client limits).
- staging (a private server) and production (a publicly reachable one): the application refuses to
  start unless the settings below are safe. READ_ONLY_MODE changes none of these rules.
- Any other value refuses to start, so that a typo ("prod", "Production") can never fall back to
  the development behavior.

The checks run first thing at startup (the lifespan in app/main.py), before any file is read. A
refusal names the settings to correct and the rule each one breaks, never a configured value:
no origin, path, token or registry content.

The ceilings of the per-client limits follow from the limiter (app/rate_limit.py,
ClientRateLimiter): it keeps one timestamp per counted event and address, and each request scans
the address's timestamps of the last minute. Memory and work per request therefore grow with the
limit, and a limit far above any client's real rate restricts nothing. The ceilings are about 3x
(requests) and 10x (failed authentications) the values rehearsed for the showcase (300 and 10).
"""

import ipaddress
import re
from urllib.parse import urlsplit

from app.config import Settings

LOCAL_PROFILES = ("development", "test")
DEPLOYMENT_PROFILES = ("staging", "production")
_APP_ENV_RE = re.compile(r"(development|test|staging|production)(?:-[a-z0-9][a-z0-9-]{0,62})?")

# Accepted values of the per-client limits in staging and production (0 turns a limit off)
CLIENT_RATE_LIMIT_RANGE = (1, 1000)       # requests per address and minute
AUTH_FAILURE_LIMIT_RANGE = (1, 100)       # failed authentications (401) per address and minute


class UnsafeDeploymentConfiguration(RuntimeError):
    """The settings of a staging or production server are unsafe; the message names the settings."""

    def __init__(self, profile: str, problems: list[str]) -> None:
        self.profile = profile
        self.settings = sorted({problem.split(":", 1)[0] for problem in problems})
        super().__init__(
            f"Refusing to start: unsafe configuration for the {profile} profile. Correct these settings "
            "(DEPLOYMENT.md, section 8):\n" + "\n".join(f"  - {problem}" for problem in problems)
        )


def deployment_profile(app_env: str) -> str | None:
    """The profile selected by APP_ENV, or None if the value is not accepted."""
    match = _APP_ENV_RE.fullmatch(app_env)
    return match.group(1) if match else None


def _is_loopback(host: str) -> bool:
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _cors_problems(settings: Settings, profile: str) -> list[str]:
    if "cors_allowed_origins" not in settings.model_fields_set:
        return ["CORS_ALLOWED_ORIGINS: must be set explicitly; the development default (the local frontend "
                "origins) is not accepted. An empty value allows no cross-origin access (same-origin deployment)."]
    problems = []
    for position, origin in enumerate(settings.cors_origins, start=1):
        parts = urlsplit(origin)
        loopback = _is_loopback(parts.hostname or "")
        if profile == "production" and (parts.scheme != "https" or loopback):
            problems.append(f"CORS_ALLOWED_ORIGINS: origin {position} must be an https origin of a "
                            "non-loopback host in production.")
        elif profile == "staging" and parts.scheme != "https" and not loopback:
            problems.append(f"CORS_ALLOWED_ORIGINS: origin {position} must use https; plain http is "
                            "accepted in staging only for a loopback host (credentials would cross the "
                            "network unencrypted).")
    return problems


def _limit_problem(name: str, value: int, accepted: tuple[int, int]) -> list[str]:
    low, high = accepted
    if low <= value <= high:
        return []
    return [f"{name}: must be between {low} and {high} in staging and production (0 turns the limit off; "
            "a higher value would not restrict a client)."]


def configuration_problems(settings: Settings) -> list[str]:
    """The settings to correct before this server may start; empty if it may start."""
    profile = deployment_profile(settings.app_env)
    if profile is None:
        return ["APP_ENV: not an accepted value. Use development, test, staging or production, optionally "
                "followed by -<label> (lowercase letters, digits and hyphens), e.g. production-showcase."]
    if profile in LOCAL_PROFILES:
        return []
    problems = []
    if profile == "production" and settings.api_docs_enabled:
        problems.append("API_DOCS_ENABLED: must be false in production (the interactive documentation "
                        "would be public).")
    elif profile == "staging" and "api_docs_enabled" not in settings.model_fields_set:
        problems.append("API_DOCS_ENABLED: must be set explicitly in staging: false, or true to show the "
                        "interactive documentation on a private staging server.")
    problems += _cors_problems(settings, profile)
    problems += _limit_problem("CLIENT_RATE_LIMIT_PER_MINUTE", settings.client_rate_limit_per_minute,
                               CLIENT_RATE_LIMIT_RANGE)
    problems += _limit_problem("AUTH_FAILURE_LIMIT_PER_MINUTE", settings.auth_failure_limit_per_minute,
                               AUTH_FAILURE_LIMIT_RANGE)
    return problems


def ensure_safe_configuration(settings: Settings) -> str:
    """Returns the profile; raises UnsafeDeploymentConfiguration if the server must not start."""
    problems = configuration_problems(settings)
    if problems:
        raise UnsafeDeploymentConfiguration(deployment_profile(settings.app_env) or "unrecognized", problems)
    return deployment_profile(settings.app_env)
