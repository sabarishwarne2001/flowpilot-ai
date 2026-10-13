"""An ERP or warehouse destination cannot be pointed at the platform's own network (campaign
session 3, security review: SSRF).

Webhooks, tenant SMTP, BYOK endpoints and identity metadata already have SSRF tests
(`tests/security/test_ssrf_hostile_destinations.py`, `test_tenant_supplied_endpoints.py`,
`test_identity_console_setup_live.py`). The ERP connections (REST/OData base URL, SFTP host) and
the warehouse destinations (an S3-compatible endpoint, a Databricks host) take a destination an
administrator types too, and had none. Each is driven here through the real API with the
addresses an attacker uses: loopback, the cloud metadata service, private ranges, IPv6 forms. Every
one must be refused, at saving or at the connection test, and no socket may be opened to it.
"""

from __future__ import annotations

import socket
import uuid

import pytest

from tests.engines.conftest import Engines

HOSTILE_HOSTS = ["127.0.0.1", "169.254.169.254", "10.0.0.5", "192.168.1.10", "localhost", "[::1]", "[::ffff:127.0.0.1]"]


@pytest.fixture
def sockets(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Every outbound connect the process attempts on the ports these destinations use."""
    opened: list[tuple] = []
    real_connect = socket.socket.connect

    def spy(self, address):  # type: ignore[no-untyped-def]
        if isinstance(address, tuple) and len(address) >= 2 and address[1] in (22, 443, 80, 2222):
            opened.append(address)
        return real_connect(self, address)

    monkeypatch.setattr(socket.socket, "connect", spy)
    return opened


def _erp_target(engines: Engines, *, transport: str, config: dict) -> tuple[int, str]:
    body = {
        "name": f"Books {transport} {uuid.uuid4().hex[:8]}", "format": "JSON" if transport == "HTTP" else "CSV", "transport": transport,
        "preset": "GENERIC_REST" if transport == "HTTP" else "NONE",
        "auth_mode": "BEARER" if transport == "HTTP" else "SSH_PASSWORD", "config": config,
        "ack_mode": "SYNC" if transport == "HTTP" else "DELIVERY",
    }
    created = engines.post("/erp/targets", body)
    if created.status_code != 201:
        return created.status_code, created.text
    tested = engines.post(f"/erp/targets/{created.json()['id']}/test")
    return tested.status_code, tested.text


def test_an_erp_rest_target_cannot_reach_the_platform(engines: Engines, sockets: list) -> None:
    engines.plan("enterprise")
    for host in HOSTILE_HOSTS:
        status, text = _erp_target(engines, transport="HTTP", config={"http": {"base_url": f"https://{host}/odata"}})
        assert status == 422 and "URL_REFUSED" in text, (host, status, text[:300])
    assert sockets == [], sockets


def test_an_erp_sftp_target_cannot_reach_the_platform(engines: Engines, sockets: list) -> None:
    engines.plan("enterprise")
    for host in HOSTILE_HOSTS:
        bare = host.strip("[]")
        status, text = _erp_target(
            engines, transport="SFTP",
            config={"sftp": {"host": bare, "port": 22, "username": "flowpilot", "directory": "/inbox",
                             "host_key_sha256": "SHA256:" + "A" * 43}},
        )
        # An address literal is refused for the address; an IPv6 literal earlier, as not a host name.
        assert status == 422 and ("disallowed" in text or "needs sftp.host, a host name" in text), (
            host, status, text[:300])
    assert sockets == [], sockets


def _destination(engines: Engines, credential: dict) -> tuple[int, str]:
    created = engines.post("/analytics/destinations", {"label": f"Warehouse {uuid.uuid4().hex[:8]}", "credential": credential}, org=True)
    if created.status_code != 201:
        return created.status_code, created.text
    tested = engines.post(f"/analytics/destinations/{created.json()['id']}/test", org=True)
    return tested.status_code, tested.text


def test_a_warehouse_destination_cannot_reach_the_platform(engines: Engines, sockets: list) -> None:
    engines.plan("enterprise")
    for host in HOSTILE_HOSTS:
        s3 = {"kind": "S3", "bucket": "acme-analytics", "region": "eu-west-1", "prefix": "flowpilot/",
              "access_key_id": "AKIAEXAMPLE0000000000", "secret_access_key": "s" * 40,
              "endpoint_url": f"https://{host}"}
        status, text = _destination(engines, s3)
        assert status == 422 or ('"ok":false' in text.replace(" ", "") and "not reachable safely" in text), (
            host, status, text[:300])
        databricks = {"kind": "DATABRICKS", "host": host.strip("[]"), "warehouse_id": "abc123", "volume": "landing",
                      "access_token": "dapi" + "0" * 32}
        status, text = _destination(engines, databricks)
        assert status == 422 or ('"ok":false' in text.replace(" ", "") and "refusing to connect" in text), (
            host, status, text[:300])
    assert sockets == [], sockets
