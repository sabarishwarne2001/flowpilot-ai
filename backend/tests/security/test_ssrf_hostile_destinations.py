"""SSRF — a tenant-supplied destination can never reach the platform's own network.

    pytest tests/security/test_ssrf_hostile_destinations.py -q

Webhooks, ERP HTTP targets, analytics connectors, identity (OIDC/SAML metadata),
tenant SMTP and provider endpoints all send requests to an address a tenant
typed. Each goes through `app.core.ssrf_client` (resolve, refuse forbidden
ranges, connect to the VALIDATED address so DNS cannot change it afterwards,
never follow redirects). These tests hit the resolver with every notation an
attacker uses for the addresses that matter: loopback, the cloud metadata
service, private ranges, and forms that embed an IPv4 address inside an IPv6 one.
"""

from __future__ import annotations

import ipaddress
import socket

import pytest

from app.core import ssrf_client
from app.core.ssrf_client import ForbiddenAddressError, InvalidURLError, SSRFClientError


#: Every one of these reaches something private if the client believes it.
FORBIDDEN_LITERALS = [
    "127.0.0.1", "127.1", "0.0.0.0", "0", "2130706433", "0x7f000001", "017700000001", "0177.0.0.1", "0x7f.1",
    "169.254.169.254", "2852039166", "0xa9fea9fe", "10.0.0.5", "172.16.5.4", "172.31.255.255", "192.168.1.1",
    "100.64.0.1", "198.18.0.1", "192.0.0.1", "224.0.0.1", "255.255.255.255",
    "::1", "::", "[::1]", "fe80::1", "fc00::1", "fd12:3456::1", "ff02::1",
    "::ffff:127.0.0.1", "::ffff:7f00:1", "::ffff:169.254.169.254", "::ffff:10.0.0.1",
]

#: Forms that carry an IPv4 address inside an IPv6 one. They only matter behind a
#: gateway that translates them, which some clouds and container networks have.
EMBEDDED_LITERALS = [
    "64:ff9b::7f00:1",       # NAT64 -> 127.0.0.1
    "64:ff9b::a9fe:a9fe",    # NAT64 -> 169.254.169.254
    "64:ff9b::a00:5",        # NAT64 -> 10.0.0.5
    "2002:7f00:1::",         # 6to4  -> 127.0.0.1
    "2002:a9fe:a9fe::1",     # 6to4  -> 169.254.169.254
    "2002:c0a8:101::1",      # 6to4  -> 192.168.1.1
]


def _refused(host: str) -> bool:
    try:
        ssrf_client.resolve_and_validate(host.strip("[]"), 443, timeout=2.0)
    except (ForbiddenAddressError, InvalidURLError):
        return True
    except SSRFClientError:
        return True  # unresolvable is also not a connection
    return False


@pytest.mark.parametrize("host", FORBIDDEN_LITERALS)
@pytest.mark.no_db
def test_forbidden_addresses_are_refused_in_every_notation(host: str) -> None:
    assert _refused(host), f"{host} was accepted as a public destination"


@pytest.mark.parametrize("host", EMBEDDED_LITERALS)
@pytest.mark.no_db
def test_ipv4_embedded_in_ipv6_is_judged_by_the_embedded_address(host: str) -> None:
    assert _refused(host), f"{host} smuggles a private IPv4 address past the check"


@pytest.mark.parametrize("address", ["93.184.216.34", "8.8.8.8", "1.1.1.1", "2606:4700:4700::1111", "2001:4860:4860::8888"])
def test_ordinary_public_addresses_are_allowed(address: str) -> None:
    """Control: the check refuses private space, not everything."""
    assert not ssrf_client._is_forbidden_ip(ipaddress.ip_address(address))


def test_a_hostname_with_one_private_answer_among_public_ones_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """DNS rebinding's simplest form: a name that resolves to a public AND a private address."""
    def fake(host, port, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.169.254", port)),
        ]

    monkeypatch.setattr(ssrf_client.socket, "getaddrinfo", fake)
    with pytest.raises(ForbiddenAddressError):
        ssrf_client.resolve_and_validate("rebind.attacker.example", 443, timeout=2.0)


def test_a_hostname_that_resolves_to_loopback_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssrf_client.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", port))],
    )
    with pytest.raises(ForbiddenAddressError):
        ssrf_client.resolve_and_validate("localtest.attacker.example", 443, timeout=2.0)


def test_only_validated_addresses_are_returned_for_connecting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ssrf_client.socket, "getaddrinfo",
        lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))],
    )
    assert ssrf_client.resolve_and_validate("ok.example", 443, timeout=2.0) == ["93.184.216.34"]


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/hook",
        "ftp://example.com/x",
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_FLUSHALL",
        "https://",
        "https:///path-only",
        "javascript:alert(1)",
    ],
)
def test_non_https_urls_are_refused_before_any_connection(url: str) -> None:
    client = ssrf_client.SSRFSafeHTTPClient()
    with pytest.raises((InvalidURLError, ForbiddenAddressError, SSRFClientError)):
        client.request("POST", url, body=b"{}", headers={})


# ---------------------------------------------------------------------------
# The tenant-facing seams: what an ADMIN can type into the console
# ---------------------------------------------------------------------------

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from tests.conftest import Fixture  # noqa: E402
from tests.security.plans import put_on_plan  # noqa: E402

HOSTILE_WEBHOOK_URLS = [
    "https://127.0.0.1/hook",
    "https://localhost/hook",
    "https://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "https://[::1]/hook",
    "https://10.0.0.5:8443/hook",
    "https://192.168.1.10/hook",
    "https://2130706433/hook",
    "https://0x7f.0.0.1/hook",
    "https://[::ffff:169.254.169.254]/hook",
    "https://metadata.google.internal/computeMetadata/v1/",
]


@pytest.mark.parametrize("url", HOSTILE_WEBHOOK_URLS)
def test_a_webhook_endpoint_cannot_be_pointed_at_the_platforms_own_network(
    client: TestClient, db_session: Session, tenant: Fixture, url: str
) -> None:
    """Creation must refuse it, or at the very least a delivery would (see the
    resolver tests above). A 201 for one of these is a stored SSRF target."""
    put_on_plan(db_session, tenant.organization, "business")
    response = client.post(
        f"/api/v1/organizations/{tenant.organization.id}/webhooks/endpoints",
        headers=tenant.owner.headers,
        json={"url": url, "event_types": ["document.completed"]},
    )
    assert response.status_code in (400, 409, 422), (url, response.status_code, response.text[:200])
