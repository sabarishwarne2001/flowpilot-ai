"""The DNS TXT lookup behind domain verification (campaign session 3, coverage ledger).

Every domain-verification test stubbed `dns_service.lookup_txt` whole, so the function that
turns a resolver's answer into "verified", "not there yet" or "could not ask" was untested. The
network is out of reach (and CI cannot bind port 53 for a local server), so here the resolver is
handed real dnspython TXT records and raises real dnspython exceptions, and the function's own
work is checked: a record split into 255-byte strings is joined back; no record and no domain
both mean "asked, and it is not there" (which counts against a claim); a timeout or dead
resolvers mean "could not ask" (which must not); and a resolver the egress gate refuses is
never asked.
"""

from __future__ import annotations

import dns.exception
import dns.rdata
import dns.rdataclass
import dns.rdatatype
import dns.resolver
import pytest

from app.core import egress
from app.services.identity import dns_service

pytestmark = pytest.mark.no_db

TOKEN = "servabo-verify=8f14e45fceea167a5a36dedd4bea2543"


def _txt(*strings: str) -> dns.rdata.Rdata:
    text = " ".join(f'"{chunk}"' for chunk in strings)
    return dns.rdata.from_text(dns.rdataclass.IN, dns.rdatatype.TXT, text)


@pytest.fixture
def answer(monkeypatch: pytest.MonkeyPatch):
    asked: list[tuple[str, list[str]]] = []

    def install(outcome):
        def resolve(self, name, rdtype):  # type: ignore[no-untyped-def]
            asked.append((str(name), list(self.nameservers)))
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome

        monkeypatch.setattr(dns.resolver.Resolver, "resolve", resolve)
        return asked

    return install


def test_a_record_split_into_strings_is_read_whole(answer) -> None:
    asked = answer([_txt("v=spf1 -all"), _txt(TOKEN[:20], TOKEN[20:])])
    result = dns_service.lookup_txt("acme.example.com", subdomain="_servabo")
    assert result.resolved and result.error is None
    assert result.records == ["v=spf1 -all", TOKEN]
    assert result.contains(TOKEN)
    assert asked[0][0] == "_servabo.acme.example.com"


@pytest.mark.parametrize("missing", [dns.resolver.NXDOMAIN(), dns.resolver.NoAnswer()])
def test_no_domain_and_no_record_both_mean_asked_and_absent(answer, missing) -> None:
    answer(missing)
    result = dns_service.lookup_txt("acme.example.com")
    assert result.resolved is True and result.records == [] and result.error is None
    assert not result.contains(TOKEN)


@pytest.mark.parametrize("unreachable", [dns.exception.Timeout(), dns.resolver.NoNameservers()])
def test_a_resolver_that_cannot_answer_is_not_an_answer(answer, unreachable) -> None:
    answer(unreachable)
    result = dns_service.lookup_txt("acme.example.com")
    assert result.resolved is False, "a dead resolver must not count against the tenant's claim"
    assert result.error and result.error.startswith("resolver unavailable")


def test_a_resolver_the_egress_gate_refuses_is_never_asked(answer, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = answer([_txt(TOKEN)])

    def refuse(channel, host, port, **_):  # type: ignore[no-untyped-def]
        raise egress.EgressDenied(egress.Decision(allowed=False, channel=channel, host=host, port=port,
                                                  mode="sovereign", reason="NOT_ALLOWED", explanation="no rule"))

    monkeypatch.setattr(egress, "guard", refuse)
    result = dns_service.lookup_txt("acme.example.com")
    assert result.resolved is False and "refused by the egress gate" in (result.error or "")
    assert asked == []
