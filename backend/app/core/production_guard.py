"""Refuse-to-start checks for production and staging (F-003).

Two jobs, both run once when `Settings` is built, so the API, the workers,
alembic and every script get them for free:

1. In `production` and `staging`, list EVERYTHING that is unsafe or missing and
   refuse to boot. A founder who has to fix a `.env` one boot failure at a time
   gives up, so every problem is reported together, by variable name and never
   by value.
2. In `development` and `test`, do not fall back to secrets committed in the
   repository. The three secrets that used to have public defaults (the API-key
   pepper, the Redis identity pepper and the Fernet email-encryption key) are
   derived from the developer's own `JWT_SECRET_KEY` when not set. They are
   stable across restarts and processes, so API keys and stored secrets keep
   working, and they are private to each checkout.

This module has no import of `app.core.config`; `Settings` passes itself in.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import TYPE_CHECKING, Iterable, Optional
from urllib.parse import urlparse

if TYPE_CHECKING:  # pragma: no cover
    from pydantic import SecretStr

    from app.core.config import Settings

HARDENED_ENVIRONMENTS = frozenset({"production", "staging"})
DEVELOPMENT_ENVIRONMENTS = frozenset({"development", "test"})
KNOWN_ENVIRONMENTS = HARDENED_ENVIRONMENTS | DEVELOPMENT_ENVIRONMENTS

#: Spellings that mean one of the four. Anything else is a typo, and a typo
#: must not quietly become "development" or skip a check that compares
#: against the literal "production".
_ENVIRONMENT_ALIASES = {
    "prod": "production",
    "stage": "staging",
    "dev": "development",
    "local": "development",
    "testing": "test",
}

#: Values that were committed to this public repository as defaults. Anyone can
#: read them, so they are refused for good, even though nothing else about
#: them is wrong.
PUBLIC_SECRETS = frozenset(
    {
        "flowpilot_default_api_key_pepper_secret_2026",
        "flowpilot_default_redis_identity_pepper_2026",
        "v3-Q90I2S6bXpL9_L3_0V8gJ0Z1P8yL1_L3_0V8gJ0Z=",
    }
)

_PLACEHOLDER_MARKERS = (
    "changeme", "change-me", "change_me", "replace", "example", "placeholder",
    "your_", "your-", "_here", "-here", "xxxx", "default", "secret_key",
    "letmein", "dev-secret", "test-secret",
)
_WEAK_DATABASE_PASSWORDS = frozenset(
    {"postgres", "password", "flowpilot", "changeme", "admin", "root", "secret", "123456", "12345678"}
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "0.0.0.0", "::1", "[::1]"})

_GENERATE_HEX = "Generate one with: openssl rand -hex 32"
# Only app/core/encryption.py may name the Fernet import (E15), so the command
# itself lives in the template rather than in this message.
_GENERATE_FERNET = "Generate one with the command in backend/.env.production.template, section 3."


class UnsafeConfigurationError(RuntimeError):
    """Raised (not wrapped in a pydantic ValidationError) so the message is
    only the list of problems. A ValidationError would append the whole input
    dictionary, which here holds the deployment's secrets."""


def normalise_environment(raw: object) -> str:
    """`Production`, ` prod ` -> `production`; a misspelling is an error."""
    cleaned = str(raw or "").strip().lower()
    cleaned = _ENVIRONMENT_ALIASES.get(cleaned, cleaned)
    if cleaned not in KNOWN_ENVIRONMENTS:
        raise ValueError(
            f"ENVIRONMENT={str(raw)!r} is not recognised. Use one of: "
            + ", ".join(sorted(KNOWN_ENVIRONMENTS))
            + ". A misspelling is refused because it would silently switch "
            "off every production safety check."
        )
    return cleaned


# ---------------------------------------------------------------------------
# Development and test: private derived secrets instead of public defaults
# ---------------------------------------------------------------------------


def _derive(jwt_secret: str, label: str) -> bytes:
    return hmac.new(
        jwt_secret.encode("utf-8"),
        f"flowpilot/development-derived/{label}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def derive_development_hex(jwt_secret: str, label: str) -> str:
    return _derive(jwt_secret, label).hex()


def derive_development_fernet_key(jwt_secret: str) -> str:
    return base64.urlsafe_b64encode(_derive(jwt_secret, "email-encryption-key")).decode("ascii")


def _text(secret: "Optional[SecretStr] | str | None") -> str:
    if secret is None:
        return ""
    value = secret if isinstance(secret, str) else secret.get_secret_value()
    return value.strip()


def derive_missing_development_secrets(settings: "Settings") -> None:
    """Fill the three secrets that are unset, from this checkout's JWT secret."""
    from pydantic import SecretStr  # local: keep module import-light

    jwt_secret = _text(settings.JWT_SECRET_KEY)
    if not _text(settings.API_KEY_PEPPER):
        object.__setattr__(
            settings, "API_KEY_PEPPER", SecretStr(derive_development_hex(jwt_secret, "api-key-pepper"))
        )
    if not _text(settings.REDIS_IDENTITY_PEPPER):
        object.__setattr__(
            settings,
            "REDIS_IDENTITY_PEPPER",
            SecretStr(derive_development_hex(jwt_secret, "redis-identity-pepper")),
        )
    if not _text(settings.EMAIL_ENCRYPTION_KEYS):
        object.__setattr__(
            settings, "EMAIL_ENCRYPTION_KEYS", SecretStr(derive_development_fernet_key(jwt_secret))
        )


# ---------------------------------------------------------------------------
# Production and staging: everything that is unsafe or missing, at once
# ---------------------------------------------------------------------------


def _weakness(value: str, *, min_length: int, public: Iterable[str] = ()) -> Optional[str]:
    if value in set(public):
        return "is a public value: it was published in this repository, so anyone can read it"
    if len(value) < min_length:
        return f"is only {len(value)} characters; use at least {min_length}"
    if len(set(value)) < 10:
        return "has too little variety and looks repetitive"
    lowered = value.lower()
    if any(marker in lowered for marker in _PLACEHOLDER_MARKERS):
        return "looks like a placeholder, not a generated secret"
    return None


def _is_local(host: Optional[str]) -> bool:
    return (host or "").strip().lower() in _LOCAL_HOSTS


def _cors_problems(settings: "Settings", environment: str) -> list[str]:
    origins = settings.cors_origins
    if not origins:
        return ["CORS_ORIGINS: is empty. Set the exact https origin of the web app."]
    problems: list[str] = []
    for origin in origins:
        if "*" in origin:
            problems.append(
                f"CORS_ORIGINS: contains a wildcard ({origin!r}). With cookies and "
                "bearer tokens in play, list the exact origins instead."
            )
            continue
        parsed = urlparse(origin)
        if environment == "production":
            if parsed.scheme != "https":
                problems.append(f"CORS_ORIGINS: {origin!r} is not https.")
            elif _is_local(parsed.hostname):
                problems.append(f"CORS_ORIGINS: {origin!r} is a local address, not the public web app.")
        elif not parsed.scheme or not parsed.netloc:
            problems.append(f"CORS_ORIGINS: {origin!r} is not a full origin like https://app.example.com.")
    return problems


def _secret_problems(settings: "Settings") -> list[str]:
    problems: list[str] = []
    jwt_secret = _text(settings.JWT_SECRET_KEY)
    seen: dict[str, str] = {}

    def _check(name: str, value: str, *, hint: str) -> None:
        if not value:
            problems.append(f"{name}: is not set. {hint}")
            return
        weakness = _weakness(value, min_length=32, public=PUBLIC_SECRETS)
        if weakness:
            problems.append(f"{name}: {weakness}. {hint}")
            return
        if value == jwt_secret and name != "JWT_SECRET_KEY":
            problems.append(f"{name}: reuses JWT_SECRET_KEY. Every secret needs its own value. {hint}")
            return
        if value in seen:
            problems.append(
                f"{name}: has the same value as {seen[value]}. Every secret needs its own value. {hint}"
            )
            return
        seen[value] = name

    _check("JWT_SECRET_KEY", jwt_secret, hint=_GENERATE_HEX)
    _check("API_KEY_PEPPER", _text(settings.API_KEY_PEPPER), hint=_GENERATE_HEX)
    _check("REDIS_IDENTITY_PEPPER", _text(settings.REDIS_IDENTITY_PEPPER), hint=_GENERATE_HEX)

    raw_keys = _text(settings.EMAIL_ENCRYPTION_KEYS)
    if not raw_keys:
        problems.append(f"EMAIL_ENCRYPTION_KEYS: is not set. {_GENERATE_FERNET}")
    else:
        for index, key in enumerate(part.strip() for part in raw_keys.split(",") if part.strip()):
            if key in PUBLIC_SECRETS:
                problems.append(
                    f"EMAIL_ENCRYPTION_KEYS[{index}]: is a public value: it was published in "
                    f"this repository, so anyone can read it. {_GENERATE_FERNET}"
                )
                continue
            if not _looks_like_fernet_key(key):
                problems.append(f"EMAIL_ENCRYPTION_KEYS[{index}]: is not a valid Fernet key. {_GENERATE_FERNET}")

    password = _text_raw(settings.POSTGRES_PASSWORD)
    if password.lower() in _WEAK_DATABASE_PASSWORDS:
        problems.append(
            "POSTGRES_PASSWORD: is a default or trivial password. Generate one with: openssl rand -base64 32"
        )
    else:
        weakness = _weakness(password, min_length=12)
        if weakness:
            problems.append(f"POSTGRES_PASSWORD: {weakness}. Generate one with: openssl rand -base64 32")
    return problems


def _looks_like_fernet_key(key: str) -> bool:
    """32 url-safe base64 bytes: the same rule the Fernet constructor applies.

    Checked by hand because only app/core/encryption.py may import that
    library or build one of its objects (tests/test_encryption_boundary.py, E15).
    """
    try:
        return len(base64.urlsafe_b64decode(key.encode("ascii"))) == 32
    except Exception:  # noqa: BLE001
        return False


def _text_raw(value: object) -> str:
    return str(value or "").strip()


def _storage_problems(settings: "Settings", environment: str) -> list[str]:
    problems: list[str] = []
    backend = (settings.STORAGE_BACKEND or "").strip().lower()
    if backend == "local" and environment == "production":
        problems.append(
            "STORAGE_BACKEND: is 'local'. Uploaded documents would live on a container's own "
            "disk and vanish on the next deploy. Use s3, r2 or minio with a persistent volume."
        )
    if backend in {"s3", "r2", "minio"}:
        access = _text_raw(settings.S3_ACCESS_KEY_ID)
        secret = _text(settings.S3_SECRET_ACCESS_KEY)
        if access.lower() == "minioadmin" or secret.lower() == "minioadmin":
            problems.append(
                "S3_ACCESS_KEY_ID / S3_SECRET_ACCESS_KEY: are the public MinIO development "
                "credentials ('minioadmin'). Create real credentials scoped to the bucket."
            )
    return problems


def _billing_problems(settings: "Settings", environment: str) -> list[str]:
    problems: list[str] = []
    gateway = (settings.BILLING_GATEWAY or "").strip().upper()
    if gateway not in {"STRIPE", "DODO"}:
        problems.append(f"BILLING_GATEWAY: {settings.BILLING_GATEWAY!r} is not STRIPE or DODO.")

    stripe_key = _text(settings.STRIPE_SECRET_KEY)
    webhook_secrets = settings.stripe_webhook_secret_list
    if stripe_key and not webhook_secrets:
        problems.append(
            "STRIPE_WEBHOOK_SECRETS: is not set although STRIPE_SECRET_KEY is. Payments could "
            "never be confirmed, because unsigned events must be refused."
        )
    for index, secret in enumerate(webhook_secrets):
        if not secret.startswith("whsec_"):
            problems.append(
                f"STRIPE_WEBHOOK_SECRETS[{index}]: does not look like a Stripe signing secret "
                "(it starts with whsec_)."
            )
    publishable = _text_raw(settings.STRIPE_PUBLISHABLE_KEY)
    if publishable:
        if settings.STRIPE_LIVEMODE and publishable.startswith("pk_test_"):
            problems.append("STRIPE_PUBLISHABLE_KEY: is a test-mode key but STRIPE_LIVEMODE is true.")
        if not settings.STRIPE_LIVEMODE and publishable.startswith("pk_live_"):
            problems.append("STRIPE_PUBLISHABLE_KEY: is a live-mode key but STRIPE_LIVEMODE is false.")

    if _text(settings.DODO_API_KEY) and not _text(settings.DODO_WEBHOOK_SECRET):
        problems.append(
            "DODO_WEBHOOK_SECRET: is not set although DODO_API_KEY is. Payments could never be "
            "confirmed, because unsigned events must be refused."
        )
    if environment == "staging" and (settings.STRIPE_LIVEMODE or settings.DODO_LIVEMODE):
        problems.append(
            "STRIPE_LIVEMODE / DODO_LIVEMODE: staging must not run in live payment mode. "
            "Use test keys and set both to false."
        )
    return problems


def production_config_problems(settings: "Settings", environment: str) -> list[str]:
    """Every reason `settings` is unsafe for `environment`. Empty means safe."""
    problems: list[str] = []
    problems += _secret_problems(settings)
    problems += _cors_problems(settings, environment)

    frontend = urlparse(_text_raw(settings.FRONTEND_URL))
    if environment == "production" and (frontend.scheme != "https" or _is_local(frontend.hostname)):
        problems.append(
            "FRONTEND_URL: must be the public https address of the web app. It is put in "
            "invitation, verification and password-reset emails."
        )

    if (settings.LOG_LEVEL or "").strip().upper() in {"DEBUG", "TRACE", "NOTSET"}:
        problems.append(
            "LOG_LEVEL: debug logging is on. It writes request details that must not be "
            "retained in production. Use INFO."
        )

    for name in ("RATE_LIMIT_ENABLED", "LOGIN_BACKOFF_ENABLED", "SAML_XSW_DEFENCE_ENABLED", "LLM_METERING_ENABLED"):
        if not getattr(settings, name):
            problems.append(f"{name}: is switched off. It is a protection and stays on outside tests.")
    if (settings.RATE_LIMIT_BACKEND or "").strip().lower() != "redis":
        problems.append(
            "RATE_LIMIT_BACKEND: must be 'redis'. An in-memory limiter is per process and is "
            "reset by every restart."
        )

    problems += _storage_problems(settings, environment)

    if "REDIS_URL" not in settings.model_fields_set or not _text(settings.REDIS_URL):
        problems.append("REDIS_URL: is not set. Rate limits and idempotency need Redis, e.g. redis://redis:6379/0.")
    elif environment == "production" and _is_local(urlparse(_text(settings.REDIS_URL)).hostname):
        problems.append("REDIS_URL: points at localhost, which is not where Redis runs in production.")

    if not _text_raw(settings.PLATFORM_SMTP_HOST):
        problems.append(
            "PLATFORM_SMTP_HOST: is not set. Without mail, invitations, verification links and "
            "password resets are generated and never delivered, and the UI shows no error."
        )

    if settings.RERANKER_ENABLED:
        token = _text(settings.RERANKER_INTERNAL_TOKEN)
        if not token:
            problems.append(
                "RERANKER_INTERNAL_TOKEN: is not set while RERANKER_ENABLED is true. "
                f"{_GENERATE_HEX}, or set RERANKER_ENABLED=false."
            )
        elif len(token) < 32:
            problems.append("RERANKER_INTERNAL_TOKEN: is shorter than 32 characters.")

    problems += _billing_problems(settings, environment)
    return problems


def format_refusal(environment: str, problems: list[str]) -> str:
    bullets = "\n".join(f"  - {problem}" for problem in problems)
    return (
        f"Refusing to start: ENVIRONMENT={environment} has {len(problems)} unsafe or missing "
        f"setting(s). Fix them in your production .env (see backend/.env.production.template):\n"
        f"{bullets}"
    )
