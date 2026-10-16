"""The website's subprocessor list is derived from what the code calls (website session, B4).

`website/src/data/subprocessors.json` feeds the subprocessors and privacy pages. A provider the
product sends customer data to must be on it, and the providers on it must be the ones this
deployment is configured to use. Each operator connection that carries customer data
(`app.core.egress`) needs an entry; the configured providers must match the entries' names.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.core import egress
from app.core.platform_models import PLATFORM_SERVED_MODELS

pytestmark = pytest.mark.no_db

REPO = Path(__file__).resolve().parents[3]
DATA = json.loads((REPO / "website/src/data/subprocessors.json").read_text(encoding="utf-8"))
TEMPLATE = (REPO / "backend/.env.production.template").read_text(encoding="utf-8")

#: Operator channels that carry customer data to a third party on the hosted service.
CUSTOMER_DATA_CHANNELS = {egress.STORAGE, egress.BACKUP, egress.LLM_PROVIDER, egress.SMTP_PLATFORM, egress.BILLING}
#: Operator channels that do not: the operator's own machines, or DNS lookups of a domain name.
NOT_THIRD_PARTIES = {egress.LLM_LOCAL, egress.INTERNAL, egress.DNS}


def _entries_for(channel: str) -> list[dict]:
    return [s for s in DATA["subprocessors"] if s["channel"] == channel or channel in s.get("also_channels", [])]


def _template(name: str) -> str:
    match = re.search(rf"^{name}=(.*)$", TEMPLATE, re.M)
    return match.group(1).strip() if match else ""


def test_every_operator_channel_is_listed_or_explained() -> None:
    # LLM_PROVIDER is both an operator channel (the platform's own key) and a tenant one (BYOK).
    assert egress.OPERATOR_CHANNELS | {egress.LLM_PROVIDER} == CUSTOMER_DATA_CHANNELS | NOT_THIRD_PARTIES, (
        "a new operator connection: decide whether it sends customer data to a third party, "
        "and list the provider in website/src/data/subprocessors.json"
    )
    for channel in CUSTOMER_DATA_CHANNELS:
        assert _entries_for(channel), f"no subprocessor listed for {channel}"


def test_the_hosting_provider_is_listed() -> None:
    assert len(_entries_for("HOSTING")) == 1


def test_every_ai_provider_on_the_platforms_own_key_is_listed() -> None:
    listed = {s.get("provider_key") for s in _entries_for(egress.LLM_PROVIDER)}
    assert listed == set(PLATFORM_SERVED_MODELS), (listed, set(PLATFORM_SERVED_MODELS))


def test_the_mail_relay_is_the_configured_one() -> None:
    host = _template("PLATFORM_SMTP_HOST")
    names = " ".join(s["name"].lower() for s in _entries_for(egress.SMTP_PLATFORM))
    assert "postmark" in host and "postmark" in names, (host, names)


def test_object_storage_is_the_configured_one() -> None:
    backend = _template("STORAGE_BACKEND")
    names = " ".join(s["service"].lower() for s in _entries_for(egress.STORAGE))
    assert backend == "r2" and "r2" in names, (backend, names)


def test_the_payment_gateway_is_the_configured_one() -> None:
    from app.core.config import Settings

    gateway = _template("BILLING_GATEWAY") or Settings.model_fields["BILLING_GATEWAY"].default
    assert [s.get("gateway") for s in _entries_for(egress.BILLING)] == [gateway]


def test_the_website_and_caddy_send_the_same_headers() -> None:
    headers = (REPO / "website/public/_headers").read_text(encoding="utf-8")
    caddy = (REPO / "backend/deploy/Caddyfile.website").read_text(encoding="utf-8")
    csp = re.search(r"Content-Security-Policy: (.+)", headers).group(1).strip()
    assert f'Content-Security-Policy "{csp}"' in caddy
    for name in ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "X-Frame-Options"):
        value = re.search(rf"{name}: (.+)", headers).group(1).strip()
        assert f'{name} "{value}"' in caddy, name
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp and "frame-ancestors 'none'" in csp
