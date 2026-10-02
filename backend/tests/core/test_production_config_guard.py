"""F-003 — the app must refuse to start on unsafe or missing production config.

    pytest tests/core/test_production_config_guard.py -q

Before this guard, three secrets had public defaults committed in
`app/core/config.py` (the API-key pepper, the Redis identity pepper and a
Fernet email-encryption key), a forgotten variable meant the app quietly used
them, and nothing checked CORS, the database password, debug logging, local
disk storage or half-configured billing. `ENVIRONMENT=Production` (capital P)
also turned off every check that compares against the literal "production".

These tests build `Settings` from a clean environment, exactly as the API, the
worker and alembic do at import time, so a refusal here is a refusal at boot.
"""

from __future__ import annotations

import base64
import secrets

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.core.production_guard import UnsafeConfigurationError

#: Every variable the guard reads. Cleared first so nothing leaks in from the
#: developer's shell or CI.
GUARDED = (
    "ENVIRONMENT", "JWT_SECRET_KEY", "API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER",
    "EMAIL_ENCRYPTION_KEYS", "EMAIL_ENCRYPTION_KEY", "POSTGRES_PASSWORD",
    "POSTGRES_USER", "POSTGRES_HOST", "CORS_ORIGINS", "FRONTEND_URL", "LOG_LEVEL",
    "RATE_LIMIT_ENABLED", "RATE_LIMIT_BACKEND", "LLM_METERING_ENABLED",
    "LOGIN_BACKOFF_ENABLED", "SAML_XSW_DEFENCE_ENABLED", "STORAGE_BACKEND",
    "S3_BUCKET", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY", "S3_ENDPOINT_URL", "REDIS_URL", "PLATFORM_SMTP_HOST",
    "RERANKER_ENABLED", "RERANKER_INTERNAL_TOKEN", "STRIPE_SECRET_KEY",
    "STRIPE_PUBLISHABLE_KEY", "STRIPE_WEBHOOK_SECRETS", "STRIPE_LIVEMODE",
    "DODO_API_KEY", "DODO_WEBHOOK_SECRET", "DODO_LIVEMODE", "DODO_API_BASE",
    "BILLING_GATEWAY", "ML_STUBS", "SERVICE_ROLE",
)

#: The values that used to be committed as defaults. They are public.
OLD_PUBLIC_API_KEY_PEPPER = "flowpilot_default_api_key_pepper_secret_2026"
OLD_PUBLIC_REDIS_PEPPER = "flowpilot_default_redis_identity_pepper_2026"
OLD_PUBLIC_EMAIL_KEY = "v3-Q90I2S6bXpL9_L3_0V8gJ0Z1P8yL1_L3_0V8gJ0Z="


def _fernet_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def good_production_env() -> dict[str, str]:
    return {
        "ENVIRONMENT": "production",
        "JWT_SECRET_KEY": secrets.token_hex(32),
        "API_KEY_PEPPER": secrets.token_hex(32),
        "REDIS_IDENTITY_PEPPER": secrets.token_hex(32),
        "EMAIL_ENCRYPTION_KEYS": _fernet_key(),
        "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
        "POSTGRES_HOST": "db",
        "CORS_ORIGINS": "https://app.example.com",
        "FRONTEND_URL": "https://app.example.com",
        "LOG_LEVEL": "INFO",
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "flowpilot-prod",
        "S3_ACCESS_KEY_ID": "AKIA" + secrets.token_hex(8).upper(),
        "S3_SECRET_ACCESS_KEY": secrets.token_urlsafe(30),
        "REDIS_URL": "redis://redis:6379/0",
        "PLATFORM_SMTP_HOST": "smtp.postmarkapp.com",
        "RERANKER_ENABLED": "true",
        "RERANKER_INTERNAL_TOKEN": secrets.token_hex(32),
    }


@pytest.fixture()
def env(monkeypatch: pytest.MonkeyPatch):
    for name in GUARDED:
        monkeypatch.delenv(name, raising=False)

    def apply(values: dict[str, str], **changes: str | None) -> None:
        merged = {**values, **changes}
        for key, value in merged.items():
            if value is None:
                monkeypatch.delenv(key, raising=False)
            else:
                monkeypatch.setenv(key, value)

    return apply


def build() -> Settings:
    return Settings(_env_file=None)


def refusal(env, **changes: str | None) -> str:
    """Settings text of the refusal for a production env with `changes`."""
    env(good_production_env(), **changes)
    with pytest.raises((ValidationError, UnsafeConfigurationError)) as failure:
        build()
    return str(failure.value)


# ---------------------------------------------------------------------------
# The good path
# ---------------------------------------------------------------------------


def test_a_complete_production_environment_boots(env) -> None:
    env(good_production_env())
    settings = build()
    assert settings.ENVIRONMENT == "production"
    assert settings.API_KEY_PEPPER.get_secret_value() != OLD_PUBLIC_API_KEY_PEPPER


def test_staging_gets_the_same_secret_checks(env) -> None:
    text = refusal(env, ENVIRONMENT="staging", API_KEY_PEPPER=None)
    assert "API_KEY_PEPPER" in text


# ---------------------------------------------------------------------------
# ENVIRONMENT itself
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("spelling", ["Production", "PRODUCTION", " production ", "prod"])
def test_environment_is_normalised_so_no_check_is_skipped_by_spelling(env, spelling) -> None:
    env(good_production_env(), ENVIRONMENT=spelling)
    assert build().ENVIRONMENT == "production"


def test_a_misspelt_environment_is_refused_not_treated_as_development(env) -> None:
    text = refusal(env, ENVIRONMENT="prodction")
    assert "ENVIRONMENT" in text and "prodction" in text


def test_a_misspelt_production_cannot_bypass_the_guard_by_spelling_alone(env) -> None:
    text = refusal(env, ENVIRONMENT="Production", API_KEY_PEPPER=None)
    assert "API_KEY_PEPPER" in text


# ---------------------------------------------------------------------------
# Secrets: missing, public, weak, reused
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "EMAIL_ENCRYPTION_KEYS"])
def test_a_missing_secret_is_refused_by_name(env, name) -> None:
    assert name in refusal(env, **{name: None})


@pytest.mark.parametrize("name", ["API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "EMAIL_ENCRYPTION_KEYS"])
def test_a_blank_secret_is_refused_not_used_as_an_empty_key(env, name) -> None:
    """The template used to ship `API_KEY_PEPPER=` blank. Blank must not boot."""
    assert name in refusal(env, **{name: ""})


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("API_KEY_PEPPER", OLD_PUBLIC_API_KEY_PEPPER),
        ("REDIS_IDENTITY_PEPPER", OLD_PUBLIC_REDIS_PEPPER),
        ("EMAIL_ENCRYPTION_KEYS", OLD_PUBLIC_EMAIL_KEY),
    ],
)
def test_the_values_once_committed_as_defaults_are_permanently_refused(env, name, value) -> None:
    text = refusal(env, **{name: value})
    assert name in text and "public" in text.lower()


@pytest.mark.parametrize("weak", ["short", "a" * 64, "changeme" * 5, "your_pepper_here_" * 3])
def test_weak_or_placeholder_secrets_are_refused(env, weak) -> None:
    assert "API_KEY_PEPPER" in refusal(env, API_KEY_PEPPER=weak)


def test_a_weak_jwt_secret_is_refused_by_the_existing_validator_too(env) -> None:
    assert "JWT_SECRET_KEY" in refusal(env, JWT_SECRET_KEY="a" * 64)


def test_two_secrets_may_not_share_a_value(env) -> None:
    shared = secrets.token_hex(32)
    text = refusal(env, API_KEY_PEPPER=shared, REDIS_IDENTITY_PEPPER=shared)
    assert "share" in text.lower() or "same" in text.lower()


def test_the_pepper_may_not_reuse_the_jwt_secret(env) -> None:
    shared = secrets.token_hex(32)
    text = refusal(env, JWT_SECRET_KEY=shared, API_KEY_PEPPER=shared)
    assert "API_KEY_PEPPER" in text


@pytest.mark.parametrize("weak", ["postgres", "password", "flowpilot", "changeme123", "short"])
def test_a_default_or_weak_database_password_is_refused(env, weak) -> None:
    assert "POSTGRES_PASSWORD" in refusal(env, POSTGRES_PASSWORD=weak)


def test_the_committed_default_database_password_is_refused_when_unset(env) -> None:
    assert "POSTGRES_PASSWORD" in refusal(env, POSTGRES_PASSWORD=None)


# ---------------------------------------------------------------------------
# Browser-facing settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("origins", ["*", "https://*.example.com", "https://app.example.com,*"])
def test_wildcard_cors_is_refused(env, origins) -> None:
    assert "CORS_ORIGINS" in refusal(env, CORS_ORIGINS=origins)


@pytest.mark.parametrize("origins", ["http://app.example.com", "http://localhost:3000", "https://localhost:5173", ""])
def test_production_cors_must_be_real_https_origins(env, origins) -> None:
    assert "CORS_ORIGINS" in refusal(env, CORS_ORIGINS=origins)


def test_the_default_cors_origin_is_refused_when_left_unset(env) -> None:
    assert "CORS_ORIGINS" in refusal(env, CORS_ORIGINS=None)


@pytest.mark.parametrize("url", ["http://app.example.com", "http://localhost:3000", "https://127.0.0.1"])
def test_frontend_url_must_be_a_real_https_url(env, url) -> None:
    assert "FRONTEND_URL" in refusal(env, FRONTEND_URL=url)


# ---------------------------------------------------------------------------
# "Debug on" and protections that must not be off
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level", ["DEBUG", "debug", "TRACE"])
def test_debug_logging_is_refused(env, level) -> None:
    assert "LOG_LEVEL" in refusal(env, LOG_LEVEL=level)


@pytest.mark.parametrize(
    "switch",
    ["RATE_LIMIT_ENABLED", "LOGIN_BACKOFF_ENABLED", "SAML_XSW_DEFENCE_ENABLED", "LLM_METERING_ENABLED"],
)
def test_a_protection_switched_off_is_refused(env, switch) -> None:
    assert switch in refusal(env, **{switch: "false"})


def test_an_in_memory_rate_limiter_is_refused(env) -> None:
    assert "RATE_LIMIT_BACKEND" in refusal(env, RATE_LIMIT_BACKEND="memory")


# ---------------------------------------------------------------------------
# Services and storage
# ---------------------------------------------------------------------------


def test_local_disk_storage_is_refused_in_production(env) -> None:
    text = refusal(env, STORAGE_BACKEND="local")
    assert "STORAGE_BACKEND" in text


def test_local_disk_storage_is_allowed_in_staging(env) -> None:
    env(good_production_env(), ENVIRONMENT="staging", STORAGE_BACKEND="local")
    assert build().ENVIRONMENT == "staging"


def test_the_minio_dev_credentials_are_refused(env) -> None:
    text = refusal(env, S3_ACCESS_KEY_ID="minioadmin", S3_SECRET_ACCESS_KEY="minioadmin")
    assert "S3_" in text and "minioadmin" in text


def test_redis_must_be_set_explicitly(env) -> None:
    assert "REDIS_URL" in refusal(env, REDIS_URL=None)


def test_redis_may_not_point_at_localhost_in_production(env) -> None:
    assert "REDIS_URL" in refusal(env, REDIS_URL="redis://localhost:6379/0")


def test_mail_must_be_configured_or_users_silently_never_get_links(env) -> None:
    assert "PLATFORM_SMTP_HOST" in refusal(env, PLATFORM_SMTP_HOST=None)


def test_the_reranker_needs_its_token_when_enabled(env) -> None:
    assert "RERANKER_INTERNAL_TOKEN" in refusal(env, RERANKER_INTERNAL_TOKEN=None)


def test_the_reranker_token_is_not_needed_when_it_is_off(env) -> None:
    env(good_production_env(), RERANKER_ENABLED="false", RERANKER_INTERNAL_TOKEN=None)
    assert build().RERANKER_ENABLED is False


# ---------------------------------------------------------------------------
# Billing: half-configured or mismatched keys
# ---------------------------------------------------------------------------


def test_a_stripe_key_without_webhook_secrets_is_refused(env) -> None:
    text = refusal(env, STRIPE_SECRET_KEY="sk_test_" + "a1B2c3D4" * 4)
    assert "STRIPE_WEBHOOK_SECRETS" in text


def test_a_stripe_webhook_secret_must_look_like_one(env) -> None:
    text = refusal(
        env,
        STRIPE_SECRET_KEY="sk_test_" + "a1B2c3D4" * 4,
        STRIPE_WEBHOOK_SECRETS="not-a-webhook-secret",
    )
    assert "STRIPE_WEBHOOK_SECRETS" in text


def test_a_test_publishable_key_with_live_mode_is_refused(env) -> None:
    text = refusal(
        env,
        STRIPE_SECRET_KEY="sk_live_" + "a1B2c3D4" * 4,
        STRIPE_WEBHOOK_SECRETS="whsec_" + "a1B2c3D4" * 4,
        STRIPE_LIVEMODE="true",
        STRIPE_PUBLISHABLE_KEY="pk_test_" + "a1B2c3D4" * 4,
    )
    assert "STRIPE_PUBLISHABLE_KEY" in text


def test_a_dodo_key_without_a_webhook_secret_is_refused(env) -> None:
    text = refusal(env, BILLING_GATEWAY="DODO", DODO_API_KEY="dodo_" + "a1B2c3D4" * 4)
    assert "DODO_WEBHOOK_SECRET" in text


def test_staging_may_not_use_live_payment_mode(env) -> None:
    text = refusal(
        env,
        ENVIRONMENT="staging",
        STRIPE_SECRET_KEY="sk_live_" + "a1B2c3D4" * 4,
        STRIPE_WEBHOOK_SECRETS="whsec_" + "a1B2c3D4" * 4,
        STRIPE_LIVEMODE="true",
    )
    assert "staging" in text.lower() and "live" in text.lower()


def test_an_unknown_billing_gateway_is_refused(env) -> None:
    assert "BILLING_GATEWAY" in refusal(env, BILLING_GATEWAY="PAYPAL")


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def test_every_problem_is_reported_at_once(env) -> None:
    """A founder fixing a .env one boot-failure at a time gives up."""
    text = refusal(
        env,
        API_KEY_PEPPER=None,
        REDIS_IDENTITY_PEPPER=None,
        CORS_ORIGINS="*",
        LOG_LEVEL="DEBUG",
        POSTGRES_PASSWORD="postgres",
    )
    for name in ("API_KEY_PEPPER", "REDIS_IDENTITY_PEPPER", "CORS_ORIGINS", "LOG_LEVEL", "POSTGRES_PASSWORD"):
        assert name in text


def test_the_refusal_never_prints_a_secret_value_or_the_input_dictionary(env) -> None:
    """A pydantic ValidationError appends the whole input dict, which holds
    every secret in the deployment; the guard raises its own error instead."""
    jwt_secret = secrets.token_hex(32)
    pepper = "supersecretvalue-" + secrets.token_hex(16)
    text = refusal(env, JWT_SECRET_KEY=jwt_secret, API_KEY_PEPPER=pepper, POSTGRES_PASSWORD="postgres")
    assert jwt_secret[:20] not in text
    assert pepper[:20] not in text
    assert "input_value" not in text


# ---------------------------------------------------------------------------
# Development and test keep working, without public defaults
# ---------------------------------------------------------------------------


@pytest.fixture()
def dev(env):
    def _build(**changes: str | None) -> Settings:
        env({"ENVIRONMENT": "development", "JWT_SECRET_KEY": secrets.token_hex(32)}, **changes)
        return build()

    return _build


def test_development_boots_with_only_a_jwt_secret(dev) -> None:
    settings = dev()
    assert settings.API_KEY_PEPPER.get_secret_value()
    assert settings.REDIS_IDENTITY_PEPPER.get_secret_value()
    assert settings.encryption_key_list


def test_development_secrets_are_not_the_old_public_values(dev) -> None:
    settings = dev()
    assert settings.API_KEY_PEPPER.get_secret_value() != OLD_PUBLIC_API_KEY_PEPPER
    assert settings.REDIS_IDENTITY_PEPPER.get_secret_value() != OLD_PUBLIC_REDIS_PEPPER
    assert OLD_PUBLIC_EMAIL_KEY not in settings.encryption_key_list


def test_development_secrets_are_stable_for_one_jwt_secret_and_differ_between_two(env) -> None:
    """Stable, so API keys and stored secrets survive a restart; private,
    because each checkout derives them from its own JWT secret."""
    first = secrets.token_hex(32)
    env({"ENVIRONMENT": "development", "JWT_SECRET_KEY": first})
    a, b = build(), build()
    assert a.API_KEY_PEPPER.get_secret_value() == b.API_KEY_PEPPER.get_secret_value()
    assert a.encryption_key_list == b.encryption_key_list

    env({"ENVIRONMENT": "development", "JWT_SECRET_KEY": secrets.token_hex(32)})
    other = build()
    assert other.API_KEY_PEPPER.get_secret_value() != a.API_KEY_PEPPER.get_secret_value()
    assert other.encryption_key_list != a.encryption_key_list


def test_development_secrets_differ_from_each_other(dev) -> None:
    settings = dev()
    assert settings.API_KEY_PEPPER.get_secret_value() != settings.REDIS_IDENTITY_PEPPER.get_secret_value()


def test_an_explicit_development_secret_wins_over_derivation(dev) -> None:
    explicit = secrets.token_hex(32)
    assert dev(API_KEY_PEPPER=explicit).API_KEY_PEPPER.get_secret_value() == explicit


def test_the_test_environment_gets_derived_secrets_too(env) -> None:
    env({"ENVIRONMENT": "test", "JWT_SECRET_KEY": secrets.token_hex(32)})
    assert build().encryption_key_list


def test_the_old_public_values_are_gone_from_the_source() -> None:
    from pathlib import Path

    source = (Path(__file__).parents[2] / "app" / "core" / "config.py").read_text(encoding="utf-8")
    for public in (OLD_PUBLIC_API_KEY_PEPPER, OLD_PUBLIC_REDIS_PEPPER, OLD_PUBLIC_EMAIL_KEY):
        assert public not in source, "config.py must not carry a public default"
