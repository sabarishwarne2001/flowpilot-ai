"""DNS_RESOLVERS accepts a resolver on another port, and the lookup speaks real DNS to it (session 4).

    pytest tests/services/test_dns_resolver_on_another_port.py -q

Domain verification asks the resolvers in DNS_RESOLVERS, always on port 53. A resolver on another
port (a local caching resolver or a sidecar, and the browser journeys' DNS stand-in, since a test
cannot bind port 53) could not be named: `127.0.0.1:5354` was handed to the resolver as an address
and every lookup failed as "unexpected". Here a real DNS server on a free port answers the query,
so the lookup is exercised from the socket up, and the egress gate is asked about that port.
"""

from __future__ import annotations

import pytest

from app.core import egress
from app.services.identity import dns_service
from scripts import e2e_dns

pytestmark = pytest.mark.no_db

TOKEN = "flowpilot-site-verification=2c624232cdd221771294dfbb310aca00"


@pytest.fixture
def resolver(monkeypatch: pytest.MonkeyPatch):
    records: dict[str, list[str]] = {}
    running, port = e2e_dns.start_in_thread(lambda: records)
    settings = dns_service.get_settings()
    monkeypatch.setattr(settings, "DNS_RESOLVERS", f"127.0.0.1:{port}")
    monkeypatch.setattr(settings, "DNS_TIMEOUT_S", 2.0)
    try:
        yield records, port
    finally:
        running.shutdown()


def test_a_published_record_is_read_from_a_resolver_on_another_port(resolver) -> None:
    records, _ = resolver
    records["acme.example.com"] = [TOKEN]
    result = dns_service.lookup_txt("acme.example.com")
    assert result.error is None
    assert result.resolved is True
    assert result.contains(TOKEN)


def test_the_flowpilot_subdomain_and_a_missing_name(resolver) -> None:
    records, _ = resolver
    records["_flowpilot.acme.example.com"] = [TOKEN]
    assert dns_service.lookup_txt("acme.example.com", subdomain="_flowpilot").contains(TOKEN)
    missing = dns_service.lookup_txt("nobody.example.com")
    assert (missing.resolved, missing.records, missing.error) == (True, [], None)


def test_the_egress_gate_is_asked_about_the_port_it_will_use(resolver, monkeypatch: pytest.MonkeyPatch) -> None:
    _, port = resolver
    asked: list[tuple[str, int]] = []
    monkeypatch.setattr(egress, "guard", lambda channel, host, p: asked.append((host, p)))
    dns_service.lookup_txt("acme.example.com")
    assert asked == [("127.0.0.1", port)]


@pytest.mark.parametrize(("entry", "expected"), [
    ("1.1.1.1", ("1.1.1.1", 53)),
    ("127.0.0.1:5354", ("127.0.0.1", 5354)),
    ("[2606:4700:4700::1111]:53", ("2606:4700:4700::1111", 53)),
    ("2606:4700:4700::1111", ("2606:4700:4700::1111", 53)),
])
def test_each_form_of_a_resolver_entry(entry: str, expected: tuple[str, int]) -> None:
    assert dns_service.resolver_address(entry) == expected
