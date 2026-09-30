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
import yaml
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

#: Documented in the template but deliberately NOT given to the long-running containers:
#: IMAGE_TAG is read by Docker Compose itself, and the bootstrap administrator's
#: credentials go to the one-off seed command (docs/RUNBOOK.md, "First deploy") so a
#: password does not sit in the environment of every service.
NOT_FORWARDED_ON_PURPOSE = {"IMAGE_TAG", "SEED_ADMIN_EMAIL", "SEED_ADMIN_PASSWORD"}

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


def _passed_to_containers() -> set[str]:
    """Every variable name some service's `environment:` gives its container (the shared
    `x-app-env` block is merged into each service by YAML, so it is included)."""
    names: set[str] = set()
    for service in yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"].values():
        environment = service.get("environment") or {}
        if isinstance(environment, dict):
            names |= set(environment)
        else:  # the list form: ["NAME=value", "NAME"]
            names |= {str(item).split("=", 1)[0] for item in environment}
    return names


def test_every_setting_the_template_asks_for_reaches_the_containers() -> None:
    """F-047. Docker Compose hands a container ONLY the variables listed under its
    `environment:`. A value in `.env.production` that is not listed there is used to
    fill in the compose file and then thrown away: the app never sees it and runs on its
    code default. Twenty-three settings the template tells the operator to fill in were
    in that state, among them the LLM API keys, the Dodo Payments credentials and
    BILLING_GATEWAY, so a founder could follow the template exactly and get a stack with
    no AI provider and no way to select Dodo."""
    dropped = sorted(set(template_values()) - _passed_to_containers() - NOT_FORWARDED_ON_PURPOSE)
    assert not dropped, f"in the template but never passed to a container: {dropped}"


def test_the_exemptions_are_real_template_entries() -> None:
    """Control: the allow-list above cannot quietly grow to cover a real setting."""
    assert NOT_FORWARDED_ON_PURPOSE <= set(template_values())
    assert not (NOT_FORWARDED_ON_PURPOSE & _passed_to_containers())


def test_the_template_names_every_price_id_the_plan_seeder_reads() -> None:
    """F-047. `scripts/seed_quota_tiers.py` refuses to publish a paid plan whose gateway
    price id is not in its environment. The template listed two of the three (Enterprise
    is a self-serve paid plan there, though the template called it "sales-led"), so the
    documented first-deploy seed stopped on the third."""
    from scripts.seed_quota_tiers import COMMERCIALS

    read_by_seeder = {terms["gateway_price_id_env"] for terms in COMMERCIALS.values() if terms.get("gateway_price_id_env")}
    assert read_by_seeder, "expected the seeder to read at least one price id"
    missing = sorted(read_by_seeder - set(template_values()))
    assert not missing, f"read by scripts/seed_quota_tiers.py but absent from the template: {missing}"

