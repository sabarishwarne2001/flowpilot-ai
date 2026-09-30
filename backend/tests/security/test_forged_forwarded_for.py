"""F-023 — a forged X-Forwarded-For cannot choose the client IP.

    pytest tests/security/test_forged_forwarded_for.py -q

Phase 1's gate listed five modules that mention `X-Forwarded-For`. Reading them
shows one parser and four consumers: `app/core/client_ip.py` applies
`TRUSTED_PROXY_HOPS` and takes the entry the trusted proxy appended (the LAST
one), the SAML route hands the raw header to the same resolver, and BYOK, SCIM,
warehouse sync and custom domains call `client_ip(request)`. Nothing reads
`request.client.host` directly, which behind an ingress would make every caller
look like the proxy.

These tests pin the behaviour with forged headers, so the parser cannot be
"simplified" into reading the first entry (the one a client controls).
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.api.v1 import saml
from app.core import client_ip as trusted

pytestmark = pytest.mark.no_db

REAL_CLIENT = "203.0.113.9"
FORGED = "6.6.6.6"


def _request(forwarded_for: str | None, socket_ip: str = "172.18.0.5") -> Request:
    headers = [(b"x-forwarded-for", forwarded_for.encode())] if forwarded_for else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "client": (socket_ip, 4242)})


@pytest.fixture()
def hops(monkeypatch: pytest.MonkeyPatch):
    def _set(value: int) -> None:
        monkeypatch.setattr(trusted.settings, "TRUSTED_PROXY_HOPS", value)

    return _set


def test_the_first_entry_is_client_controlled_and_never_used(hops) -> None:
    hops(1)
    # The proxy appends the real peer. Everything before it is whatever the client sent.
    assert trusted.client_ip(_request(f"{FORGED}, {REAL_CLIENT}")) == REAL_CLIENT
    assert trusted.client_ip(_request(f"{FORGED}, 10.0.0.1, {REAL_CLIENT}")) == REAL_CLIENT


def test_many_forged_values_all_resolve_to_the_same_real_client(hops) -> None:
    """A per-IP rate limit cannot be evaded by rotating the header."""
    hops(1)
    seen = {trusted.client_ip(_request(f"198.51.100.{n}, {REAL_CLIENT}")) for n in range(1, 40)}
    assert seen == {REAL_CLIENT}


def test_with_no_trusted_proxy_the_header_is_ignored_entirely(hops) -> None:
    hops(0)
    assert trusted.client_ip(_request(f"{FORGED}, {REAL_CLIENT}", socket_ip="192.0.2.77")) == "192.0.2.77"


def test_a_chain_shorter_than_the_trusted_hops_never_yields_the_forged_address(hops) -> None:
    """Rate limiting falls back to the socket peer (the proxy); IP pinning
    (strict) refuses. Neither ever returns the address the client wrote."""
    hops(2)
    assert trusted.client_ip(_request(FORGED)) != FORGED
    assert saml._client_ip(_request(FORGED)) is None


def test_a_garbage_entry_never_becomes_the_client_ip(hops) -> None:
    hops(1)
    assert trusted.client_ip(_request("not-an-ip")) != "not-an-ip"
    assert saml._client_ip(_request("not-an-ip")) is None


def test_the_saml_route_uses_the_same_resolver(hops) -> None:
    hops(1)
    assert saml._client_ip(_request(f"{FORGED}, {REAL_CLIENT}")) == REAL_CLIENT
    hops(0)
    assert saml._client_ip(_request(f"{FORGED}", socket_ip="192.0.2.77")) == "192.0.2.77"


def test_no_module_reads_the_socket_peer_or_parses_the_header_itself() -> None:
    """Structural: only `client_ip.py` splits the header, and the only code that
    touches `request.client.host` hands it to the resolver as `socket_ip=`."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "app"
    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "client_ip.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        allowed_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.keyword) and node.arg == "socket_ip":
                allowed_lines.update(range(node.value.lineno, getattr(node.value, "end_lineno", node.value.lineno) + 1))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "host"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "client"
                and node.lineno not in allowed_lines
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}: reads client.host directly")
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "split"
                and isinstance(node.func.value, ast.Name)
                and "forward" in node.func.value.id.lower()
            ):
                offenders.append(f"{path.relative_to(root)}:{node.lineno}: splits a forwarded header itself")
    assert not offenders, offenders
