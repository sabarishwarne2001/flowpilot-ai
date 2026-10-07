"""F-123 — SSO must advertise the deployment's public address, and the settings
that say what that address is must be settings.

    pytest tests/core/test_sso_public_addresses.py -q

`app/api/v1/saml.py` built the SAML entity ID, ACS and SLO URLs from
`getattr(settings, "PUBLIC_API_URL", "http://localhost:8000")` and sent
`getattr(settings, "OIDC_REDIRECT_URI", "")` as the OIDC redirect_uri. None of
those names was a declared field, and `Settings` ignores undeclared variables,
so on every deployment the SP metadata pointed identity providers at
http://localhost:8000 and every OIDC sign-in sent an empty redirect_uri. Setting
the variables changed nothing. The same was true of the identity settings the
env templates document (SCIM token lifetime and rotation overlap, the domain
re-verification grace period).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.config import Settings
from tests.core.test_production_config_guard import GUARDED, good_production_env

pytestmark = pytest.mark.no_db

SSO_SETTINGS = (
    "PUBLIC_API_URL", "SAML_SP_ENTITY_ID", "OIDC_REDIRECT_URI", "OIDC_DEFAULT_SCOPES",
    "OIDC_DISCOVERY_TIMEOUT_S", "SAML_SP_SIGNING_CERT_PEM",
)
IDENTITY_SETTINGS = (
    "SCIM_TOKEN_TTL_DAYS", "SCIM_TOKEN_ROTATION_OVERLAP_DAYS", "SCIM_MAX_PAGE_SIZE",
    "DOMAIN_VERIFICATION_GRACE_DAYS", "DOMAIN_VERIFICATION_RECHECK_INTERVAL_HOURS",
)


@pytest.fixture()
def build(monkeypatch: pytest.MonkeyPatch):
    for name in (*GUARDED, *SSO_SETTINGS, *IDENTITY_SETTINGS, "API_V1_STR"):
        monkeypatch.delenv(name, raising=False)

    def apply(values: dict[str, str]) -> Settings:
        for key, value in values.items():
            monkeypatch.setenv(key, value)
        return Settings(_env_file=None)

    return apply


def _sp_urls_for(monkeypatch: pytest.MonkeyPatch, settings: Settings) -> tuple[str, str, str]:
    from app.api.v1 import saml

    monkeypatch.setattr(saml, "get_settings", lambda: settings)
    return saml._sp_urls()


def test_production_sso_uses_the_public_web_address(build, monkeypatch) -> None:
    """Caddy serves the API on the web app's own host, so with nothing else set the
    public API address is FRONTEND_URL, never localhost."""
    settings = build(good_production_env())

    entity_id, acs, slo = _sp_urls_for(monkeypatch, settings)

    assert entity_id == "https://app.example.com/api/v1/saml/metadata"
    assert acs == "https://app.example.com/api/v1/saml/acs"
    assert slo == "https://app.example.com/api/v1/saml/slo"
    assert settings.oidc_redirect_uri == "https://app.example.com/api/v1/oidc/callback"


def test_public_api_url_is_honoured(build, monkeypatch) -> None:
    settings = build({**good_production_env(), "PUBLIC_API_URL": "https://api.example.com/"})

    entity_id, acs, _ = _sp_urls_for(monkeypatch, settings)

    assert acs == "https://api.example.com/api/v1/saml/acs"
    assert entity_id == "https://api.example.com/api/v1/saml/metadata"
    assert settings.oidc_redirect_uri == "https://api.example.com/api/v1/oidc/callback"


def test_explicit_entity_id_and_redirect_uri_are_honoured(build, monkeypatch) -> None:
    settings = build({
        **good_production_env(),
        "SAML_SP_ENTITY_ID": "urn:flowpilot:sp",
        "OIDC_REDIRECT_URI": "https://sso.example.com/callback",
    })

    entity_id, acs, _ = _sp_urls_for(monkeypatch, settings)

    assert entity_id == "urn:flowpilot:sp"
    assert acs == "https://app.example.com/api/v1/saml/acs"
    assert settings.oidc_redirect_uri == "https://sso.example.com/callback"


def test_development_keeps_the_local_api_address(build, monkeypatch) -> None:
    """Unchanged for a developer: the API runs on uvicorn's port, the web app on Vite's."""
    settings = build({
        "ENVIRONMENT": "development",
        "JWT_SECRET_KEY": "d" * 8 + "0123456789abcdef0123456789abcdef",
        "FRONTEND_URL": "http://localhost:5173",
    })

    _, acs, _ = _sp_urls_for(monkeypatch, settings)

    assert acs == "http://localhost:8000/api/v1/saml/acs"
    assert settings.oidc_redirect_uri == "http://localhost:8000/api/v1/oidc/callback"


def test_a_public_api_url_without_a_scheme_is_refused(build) -> None:
    with pytest.raises(ValueError, match="PUBLIC_API_URL"):
        build({**good_production_env(), "PUBLIC_API_URL": "api.example.com"})


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("SCIM_TOKEN_TTL_DAYS", "90", 90),
        ("SCIM_TOKEN_ROTATION_OVERLAP_DAYS", "3", 3),
        ("SCIM_MAX_PAGE_SIZE", "100", 100),
        ("DOMAIN_VERIFICATION_GRACE_DAYS", "21", 21),
        ("DOMAIN_VERIFICATION_RECHECK_INTERVAL_HOURS", "12", 12),
    ],
)
def test_documented_identity_settings_take_effect(build, name: str, value: str, expected: int) -> None:
    settings = build({**good_production_env(), name: value})
    assert getattr(settings, name) == expected


#: Read through getattr() but deliberately not settings: FLOWPILOT_DEPLOYMENT_ID is read
#: from the process environment first (sovereign licence binding), and SCIM_TOKEN_PEPPER
#: is F-124 (a separate fix, because changing it invalidates issued SCIM tokens).
NOT_DECLARED_ON_PURPOSE = {"FLOWPILOT_DEPLOYMENT_ID", "SCIM_TOKEN_PEPPER"}

_GETATTR = re.compile(
    r"getattr\(\s*(?:settings|_settings\(\)|get_settings\(\)|self\._settings)\s*,\s*[\"']([A-Z][A-Z0-9_]+)[\"']"
)


def test_every_setting_read_through_getattr_is_declared() -> None:
    """Ratchet. An undeclared name read with getattr() returns its literal default on
    every deployment, whatever the environment says."""
    app_dir = Path(__file__).resolve().parents[2] / "app"
    read: dict[str, str] = {}
    for path in app_dir.rglob("*.py"):
        if path.name == "config.py" and path.parent.name == "core":
            continue  # its comments quote the idiom, e.g. getattr(settings, "NAME", default)
        for name in _GETATTR.findall(path.read_text(encoding="utf-8")):
            read.setdefault(name, str(path.relative_to(app_dir.parent)))
    undeclared = {
        name: where for name, where in read.items()
        if name not in Settings.model_fields and name not in NOT_DECLARED_ON_PURPOSE
    }
    assert not undeclared, f"read with getattr() but not declared in Settings: {undeclared}"
