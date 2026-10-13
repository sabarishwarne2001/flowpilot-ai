"""An organization's egress lockdown decides where its data may go (campaign session 3, ledger).

`Organization > Egress lockdown` (Enterprise) switches on a deny-by-default policy for the
channels that carry an organization's data to destinations it chose (webhooks, warehouses, ERP,
its own SMTP, identity metadata, AI providers), with allow rules per channel and host. The
console only had a test that flips the switch. This proves the effect, through the API and on a
real outbound path: with the lockdown on, a destination no rule allows is refused before any
connection is made; a rule opens exactly its host on its channel; the platform's own channels are
not the organization's to close; switching it off opens the organization's channels again.
"""

from __future__ import annotations

import socket

import pytest

from app.core import egress
from tests.engines.conftest import Engines
from tests.engines.test_webhooks_live import _deliver

HOOK = "hooks.acme.example.com"


def _decision(engines: Engines, channel: str, destination: str) -> dict:
    tested = engines.post("/egress/test", {"channel": channel, "destination": destination}, org=True)
    assert tested.status_code == 200, tested.text
    return tested.json()


@pytest.fixture
def sockets(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    opened: list[tuple] = []
    real_connect = socket.socket.connect

    def spy(self, address):  # type: ignore[no-untyped-def]
        if isinstance(address, tuple) and len(address) >= 2 and address[1] == 443:
            opened.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    return opened


def test_lockdown_refuses_what_no_rule_allows_and_a_rule_opens_its_host(engines: Engines) -> None:
    engines.plan("enterprise")
    assert _decision(engines, egress.WEBHOOK, f"https://{HOOK}/in")["allowed"] is True

    assert engines.put("/egress", {"lockdown_enabled": True}, org=True).status_code == 200
    refused = _decision(engines, egress.WEBHOOK, f"https://{HOOK}/in")
    assert refused["allowed"] is False and refused["reason"] == egress.REASON_TENANT_LOCKDOWN, refused

    rule = engines.post("/egress/rules", {"channel": egress.WEBHOOK, "host_pattern": HOOK}, org=True)
    assert rule.status_code in (200, 201), rule.text
    assert _decision(engines, egress.WEBHOOK, f"https://{HOOK}/in")["allowed"] is True
    assert _decision(engines, egress.WEBHOOK, "https://elsewhere.example.org/in")["allowed"] is False
    assert _decision(engines, egress.WAREHOUSE, f"https://{HOOK}/in")["allowed"] is False, \
        "a rule opens its own channel only"

    assert engines.delete(f"/egress/rules/{rule.json()['id']}", org=True).status_code == 204
    assert _decision(engines, egress.WEBHOOK, f"https://{HOOK}/in")["allowed"] is False

    assert engines.put("/egress", {"lockdown_enabled": False}, org=True).status_code == 200
    assert _decision(engines, egress.WEBHOOK, "https://elsewhere.example.org/in")["allowed"] is True


def test_the_platforms_own_channels_are_not_the_organizations_to_close(engines: Engines) -> None:
    engines.plan("enterprise")
    assert engines.put("/egress", {"lockdown_enabled": True}, org=True).status_code == 200
    decision = egress.decide(egress.SMTP_PLATFORM, "smtp.platform.example", 587, organization_id=engines.org)
    assert decision.allowed, decision


def test_a_webhook_to_a_locked_down_destination_is_refused_before_connecting(
    engines: Engines, sockets: list
) -> None:
    engines.plan("enterprise")
    created = engines.post("/webhooks/endpoints", {"url": f"https://{HOOK}/in", "event_types": ["document.completed"]},
                           org=True)
    assert created.status_code == 201, created.text
    endpoint_id = created.json()["endpoint"]["id"]
    assert engines.put("/egress", {"lockdown_enabled": True}, org=True).status_code == 200

    assert engines.post(f"/webhooks/endpoints/{endpoint_id}/test", org=True).status_code == 202
    _deliver()
    delivery = engines.get(f"/webhooks/endpoints/{endpoint_id}/deliveries", org=True).json()[0]
    assert delivery["status"] != "DELIVERED", delivery
    attempts = engines.get(f"/webhooks/deliveries/{delivery['id']}/attempts", org=True).json()
    assert attempts and "lockdown" in (attempts[0].get("error") or "").lower(), attempts
    assert sockets == [], sockets
