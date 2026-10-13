"""A verified custom domain, end to end (campaign session 3, console routes).

`Organization > Branding & custom domains` claims a hostname, verifies it by a DNS TXT record,
and can make one verified hostname the primary (the one links are built with). Three routes had
no test of their effect:

* `PUT .../custom-domains/{id}/primary` moves the primary flag: one primary at a time, and only a
  verified hostname can hold it.
* `GET /internal/tls/authorize` is the question the TLS terminator asks before requesting a
  certificate: yes for a verified tenant hostname (however its case or trailing dot is written),
  no for a pending one, a stranger, or nothing.
* `GET /branding/logo` and `/branding/favicon` serve the organization's own files on its
  verified hostname, as plain PNG that varies by Host, and nothing on the platform's address.

DNS is the one thing a test cannot reach, so the TXT lookup answers with the challenge token.
"""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services.branding import branding_service, domain_service
from app.services.identity.dns_service import TxtLookupResult
from tests.engines.conftest import Engines

API = "/api/v1"


@pytest.fixture(autouse=True)
def _custom_domains_on(monkeypatch, engines: Engines):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CUSTOM_DOMAINS_ENABLED", True, raising=False)
    monkeypatch.setattr(settings, "PLATFORM_RESERVED_HOSTS", ["flowpilot.ai", "app.flowpilot.ai"], raising=False)
    engines.plan("enterprise")  # custom domains and branding files are plan features


def _verified(engines: Engines, monkeypatch, hostname: str) -> dict:
    claimed = engines.post("/custom-domains", {"hostname": hostname}, org=True)
    assert claimed.status_code == 201, claimed.text
    body = claimed.json()

    def _answer(domain: str, *, subdomain: str | None = None) -> TxtLookupResult:
        return TxtLookupResult(domain=domain, records=[body["challenge_token"]], resolved=True)

    monkeypatch.setattr(domain_service.dns_service, "lookup_txt", _answer)
    monkeypatch.setattr(branding_service.dns_service, "lookup_txt", _answer)
    verified = engines.post(f"/custom-domains/{body['id']}/verify", org=True)
    assert verified.status_code == 200 and verified.json()["verified"] is True, verified.text
    return body


def _primary(engines: Engines) -> set[str]:
    listed = engines.get("/custom-domains", org=True)
    assert listed.status_code == 200, listed.text
    return {row["hostname"] for row in listed.json() if row["is_primary"]}


def test_one_verified_hostname_is_primary_at_a_time(engines: Engines, monkeypatch) -> None:
    first = _verified(engines, monkeypatch, "docs.acme-primary.com")
    second = _verified(engines, monkeypatch, "portal.acme-primary.com")

    made = engines.put(f"/custom-domains/{first['id']}/primary", {"is_primary": True}, org=True)
    assert made.status_code == 200 and made.json()["is_primary"] is True, made.text
    assert _primary(engines) == {"docs.acme-primary.com"}

    moved = engines.put(f"/custom-domains/{second['id']}/primary", {"is_primary": True}, org=True)
    assert moved.status_code == 200, moved.text
    assert _primary(engines) == {"portal.acme-primary.com"}, "the flag moves; it is never on two hostnames"

    cleared = engines.put(f"/custom-domains/{second['id']}/primary", {"is_primary": False}, org=True)
    assert cleared.status_code == 200 and _primary(engines) == set()


def test_a_pending_hostname_cannot_be_primary_and_only_the_owner_may_choose(engines: Engines, monkeypatch) -> None:
    pending = engines.post("/custom-domains", {"hostname": "pending.acme-primary.com"}, org=True).json()
    refused = engines.put(f"/custom-domains/{pending['id']}/primary", {"is_primary": True}, org=True)
    assert refused.status_code == 409, refused.text
    assert "verified" in refused.text.lower()

    verified = _verified(engines, monkeypatch, "ok.acme-primary.com")
    admin = engines.put(
        f"/custom-domains/{verified['id']}/primary", {"is_primary": True}, org=True, as_user=engines.tenant.org_admin
    )
    assert admin.status_code == 403, admin.text
    assert _primary(engines) == set()


def _tls(engines: Engines, domain: str):
    return engines.client.get(f"{API}/internal/tls/authorize", params={"domain": domain})


def test_a_certificate_is_authorized_for_a_verified_hostname_only(engines: Engines, monkeypatch) -> None:
    pending = engines.post("/custom-domains", {"hostname": "waiting.acme-tls.com"}, org=True)
    assert pending.status_code == 201, pending.text
    _verified(engines, monkeypatch, "secure.acme-tls.com")

    for spelling in ("secure.acme-tls.com", "SECURE.Acme-TLS.com", "secure.acme-tls.com."):
        answer = _tls(engines, spelling)
        assert answer.status_code == 200, (spelling, answer.text)
        assert answer.json() == {"authorized": True, "hostname": "secure.acme-tls.com"}

    for refused, reason in (
        ("waiting.acme-tls.com", "NOT_VERIFIED"),       # claimed, DNS not proven yet
        ("evil.acme-tls.com", "NOT_VERIFIED"),          # a neighbour of a verified name
        ("secure.acme-tls.com.evil.net", "NOT_VERIFIED"),
        ("", "EMPTY_HOSTNAME"),
    ):
        answer = _tls(engines, refused)
        assert answer.status_code == 404, (refused, answer.text)
        assert answer.json()["authorized"] is False and answer.json()["reason"] == reason


def _png(colour: tuple[int, int, int]) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def test_the_logo_and_favicon_are_served_on_the_verified_hostname(engines: Engines, monkeypatch) -> None:
    _verified(engines, monkeypatch, "brand.acme-assets.com")
    for kind, colour in (("logo", (200, 30, 30)), ("favicon", (30, 200, 30))):
        uploaded = engines.client.post(
            f"{engines.org_url('/branding')}/{kind}",
            files={"file": (f"{kind}.png", _png(colour), "image/png")},
            headers=engines.tenant.owner.headers,
        )
        assert uploaded.status_code in (200, 201), (kind, uploaded.text)

    for kind, colour in (("logo", (200, 30, 30)), ("favicon", (30, 200, 30))):
        served = engines.client.get(f"{API}/branding/{kind}", headers={"Host": "brand.acme-assets.com"})
        assert served.status_code == 200, (kind, served.text[:200])
        assert served.headers["content-type"].startswith("image/png")
        assert served.headers.get("x-content-type-options") == "nosniff"
        assert "host" in served.headers.get("vary", "").lower(), "a shared cache must not hand one tenant's file to another"
        assert Image.open(io.BytesIO(served.content)).convert("RGB").getpixel((5, 5)) == colour

        on_the_platform = engines.client.get(f"{API}/branding/{kind}")
        assert on_the_platform.status_code == 404, (kind, "the platform's own address has no tenant files")

    cleared = engines.delete("/branding/favicon", org=True)
    assert cleared.status_code == 200, cleared.text
    gone = engines.client.get(f"{API}/branding/favicon", headers={"Host": "brand.acme-assets.com"})
    assert gone.status_code == 404, "a cleared favicon is not served from the old file"
