"""F-003 / F-011 — `.env.production.template` must be complete and safe to copy.

    pytest tests/infra/test_production_env_template.py -q

The template is the only document a solo founder follows to deploy. It has to
(1) name every variable that docker-compose.prod.yml or the application's boot
guard requires, (2) never ship a usable secret, (3) be REFUSED if copied without
filling the secrets in (a blank pepper used to mean an empty pepper), and (4)
boot the application once the secrets are filled in.
"""

from __future__ import annotations

import base64
import re
import secrets
from pathlib import Path

import pytest
from dotenv import dotenv_values
from pydantic import ValidationError

from app.core.config import Settings
from app.core.production_guard import UnsafeConfigurationError

BACKEND = Path(__file__).resolve().parents[2]
TEMPLATE = BACKEND / ".env.production.template"
COMPOSE = BACKEND / "docker-compose.prod.yml"

#: Required by the boot guard (app/core/production_guard.py) in addition to
#: whatever the compose file marks with ${VAR:?}.
GUARD_REQUIRED = {
    "JWT_SECRET_KEY", "API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "EMAIL_ENCRYPTION_KEYS",
    "POSTGRES_PASSWORD", "CORS_ORIGINS", "FRONTEND_URL", "REDIS_URL", "PLATFORM_SMTP_HOST",
    "RERANKER_INTERNAL_TOKEN", "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
}

#: Must be empty in the template: a value here would be a committed secret.
MUST_BE_BLANK = {
    "JWT_SECRET_KEY", "API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "EMAIL_ENCRYPTION_KEYS",
    "POSTGRES_PASSWORD", "RERANKER_INTERNAL_TOKEN", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY",
    "STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRETS", "DODO_API_KEY", "DODO_WEBHOOK_SECRET",
    "PLATFORM_SMTP_PASSWORD", "GROQ_API_KEY", "GEMINI_API_KEY", "SEED_ADMIN_PASSWORD",
}

pytestmark = pytest.mark.no_db


def template_values() -> dict[str, str]:
    return {key: (value or "") for key, value in dotenv_values(TEMPLATE).items()}


def secrets_filled_in() -> dict[str, str]:
    """What an operator does after `cp .env.production.template .env.production`."""
    values = template_values()
    values.update(
        JWT_SECRET_KEY=secrets.token_hex(32),
        API_KEY_PEPPER=secrets.token_hex(32),
        REDIS_IDENTITY_PEPPER=secrets.token_hex(32),
        EMAIL_ENCRYPTION_KEYS=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
        POSTGRES_PASSWORD=secrets.token_urlsafe(24),
        RERANKER_INTERNAL_TOKEN=secrets.token_hex(32),
        S3_ACCESS_KEY_ID="AKIA" + secrets.token_hex(8).upper(),
        S3_SECRET_ACCESS_KEY=secrets.token_urlsafe(30),
    )
    return values


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch):
    # pytest.ini exports RATE_LIMIT_ENABLED=False; nothing ambient may leak in.
    ambient = {
        "EMAIL_ENCRYPTION_KEY", "ML_STUBS", "RATE_LIMIT_ENABLED", "RATE_LIMIT_BACKEND",
        "LLM_METERING_ENABLED", "LOGIN_BACKOFF_ENABLED", "SAML_XSW_DEFENCE_ENABLED",
        "STORAGE_BACKEND", "LOG_LEVEL", "POSTGRES_HOST", "SERVICE_ROLE",
    }
    for name in set(template_values()) | GUARD_REQUIRED | ambient:
        monkeypatch.delenv(name, raising=False)

    def apply(values: dict[str, str]) -> Settings:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return apply


def test_the_template_names_everything_the_compose_file_requires() -> None:
    required = set(re.findall(r"\$\{([A-Z][A-Z0-9_]*):\?", COMPOSE.read_text(encoding="utf-8")))
    assert required, "expected docker-compose.prod.yml to use the ${VAR:?} form"
    assert required <= set(template_values()), sorted(required - set(template_values()))


def test_the_template_names_everything_the_boot_guard_requires() -> None:
    assert GUARD_REQUIRED <= set(template_values()), sorted(GUARD_REQUIRED - set(template_values()))


def test_the_template_declares_the_production_environment() -> None:
    assert template_values()["ENVIRONMENT"] == "production"


@pytest.mark.parametrize("name", sorted(MUST_BE_BLANK))
def test_the_template_ships_no_usable_secret(name: str) -> None:
    assert template_values().get(name, "") == "", f"{name} must be blank in the template"


def test_the_template_as_copied_without_secrets_is_refused(clean_env) -> None:
    """A blank pepper used to override the default and boot with an empty pepper."""
    copied = template_values()
    copied["JWT_SECRET_KEY"] = secrets.token_hex(32)  # the one secret with no default
    with pytest.raises((UnsafeConfigurationError, ValidationError)) as failure:
        clean_env(copied)
    text = str(failure.value)
    for name in ("API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "EMAIL_ENCRYPTION_KEYS", "POSTGRES_PASSWORD"):
        assert name in text


def test_the_template_with_secrets_filled_in_boots(clean_env) -> None:
    settings = clean_env(secrets_filled_in())
    assert settings.ENVIRONMENT == "production"
    assert settings.cors_origins == ["https://app.example.com"]
    assert settings.RATE_LIMIT_ENABLED is True
