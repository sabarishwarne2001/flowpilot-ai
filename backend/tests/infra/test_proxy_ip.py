"""ARCH-19 §3.4 — X-Forwarded-For parsing, spoof resistance, and IP pinning.

The spoofing tests are the reason this file exists. X-Forwarded-For is
append-only and the leftmost entry is whatever the client typed, so every
assertion below is really the same assertion: a value the client controls must
never be promoted to "the client address".
"""

from __future__ import annotations

import pytest
from starlette.requests import Request

from app.core import client_ip as ip
from app.core.config import settings
from app.services.identity import session_policy_service as policy

pytestmark = pytest.mark.no_db


def _request(headers: dict[str, str], host: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in headers.items()
            ],
            "client": (host, 41234),
        }
    )


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("203.0.113.7", "203.0.113.7"),
        ("  203.0.113.7  ", "203.0.113.7"),
        ("203.0.113.7:41234", "203.0.113.7"),
        ("[2001:db8::1]:41234", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
        # A bare IPv6 has more than one colon, so "split on the last colon"
        # would truncate it. This is the case that breaks naive parsers.
        ("2001:db8::1", "2001:db8::1"),
        ("2001:0db8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
        ("fe80::1%eth0", "fe80::1"),
    ],
)
def test_normalise_accepts_the_forms_proxies_emit(raw: str, expected: str) -> None:
    assert ip.normalise_ip(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "   ",
        "not-an-address",
        "999.999.999.999",
        "testclient",
        "<script>alert(1)</script>",
        "203.0.113.7 203.0.113.8",
        "[2001:db8::1",
    ],
)
def test_normalise_rejects_everything_else(raw) -> None:
    assert ip.normalise_ip(raw) is None


def test_normalised_address_fits_the_storage_column() -> None:
    longest = ip.normalise_ip("2001:0db8:85a3:0000:0000:8a2e:0370:7334")
    assert longest is not None
    assert len(longest) <= ip.MAX_IP_LENGTH


# ---------------------------------------------------------------------------
# Chain selection
# ---------------------------------------------------------------------------


def test_hops_zero_means_no_proxy_and_the_header_is_ignored() -> None:
    address, outcome = ip.parse_forwarded_for("1.2.3.4, 5.6.7.8", hops=0)
    assert (address, outcome) == (None, ip.HOPS_DISABLED)


@pytest.mark.parametrize(
    "chain,hops,expected",
    [
        ("198.51.100.1", 1, "198.51.100.1"),
        ("203.0.113.9, 198.51.100.1", 1, "198.51.100.1"),
        ("203.0.113.9, 198.51.100.1", 2, "203.0.113.9"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3", 1, "3.3.3.3"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3", 2, "2.2.2.2"),
        ("1.1.1.1, 2.2.2.2, 3.3.3.3", 3, "1.1.1.1"),
    ],
)
def test_rightmost_trusted_entry_is_selected(chain, hops, expected) -> None:
    address, outcome = ip.parse_forwarded_for(chain, hops=hops)
    assert outcome == ip.TRUSTED
    assert address == expected


def test_a_spoofed_prefix_cannot_reach_the_selection() -> None:
    """The classic attack, and the one thing this module must get right.

    One real proxy in front. The client sends a forged chain; the proxy
    appends the address it actually observed. With hops=1 the selection is the
    proxy's observation, and none of the forged entries can be reached no
    matter how many the client sends.
    """
    forged = ", ".join(["10.0.0.99"] * 50)
    observed = "198.51.100.1"

    address, outcome = ip.parse_forwarded_for(f"{forged}, {observed}", hops=1)

    assert outcome == ip.TRUSTED
    assert address == observed
    assert address != "10.0.0.99"


def test_short_chain_is_refused_rather_than_promoted() -> None:
    """Two proxies configured, one entry present.

    Selecting chain[0] here IS the spoof: a client that sends its own
    X-Forwarded-For against a two-hop deployment would have that value
    promoted to the client address. Refusing is the only safe answer.
    """
    address, outcome = ip.parse_forwarded_for("10.0.0.99", hops=2)
    assert (address, outcome) == (None, ip.CHAIN_TOO_SHORT)


def test_missing_header_behind_a_proxy_is_reported_distinctly() -> None:
    address, outcome = ip.parse_forwarded_for(None, hops=1)
    assert (address, outcome) == (None, ip.NO_HEADER)


def test_garbage_in_the_trusted_position_is_refused() -> None:
    address, outcome = ip.parse_forwarded_for(
        "203.0.113.9, definitely-not-an-ip", hops=1
    )
    assert (address, outcome) == (None, ip.INVALID_ADDRESS)


def test_every_outcome_is_in_the_closed_vocabulary() -> None:
    for chain, hops in (
        ("1.1.1.1", 1), ("1.1.1.1", 0), (None, 1), ("1.1.1.1", 3), ("junk", 1)
    ):
        _, outcome = ip.parse_forwarded_for(chain, hops=hops)
        assert outcome in ip.PARSE_OUTCOMES


# ---------------------------------------------------------------------------
# The two postures
# ---------------------------------------------------------------------------


def test_observability_flavour_falls_back_to_the_peer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    request = _request({"X-Forwarded-For": "10.0.0.99"}, host="192.0.2.5")
    assert ip.client_ip(request) == "192.0.2.5"


def test_security_flavour_refuses_the_same_request(monkeypatch) -> None:
    """The whole point of two exits.

    A log line with the load balancer's address is a mild annoyance. An IP
    allowlist that matches the load balancer is a bypass.
    """
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 2)
    request = _request({"X-Forwarded-For": "10.0.0.99"}, host="192.0.2.5")
    assert ip.trusted_client_ip(request) is None


def test_client_ip_never_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 0)
    assert ip.client_ip(None) == "unknown"
    assert ip.client_ip(_request({}, host="testclient")) == "testclient"


def test_both_flavours_agree_when_the_chain_is_trustworthy(monkeypatch) -> None:
    monkeypatch.setattr(settings, "TRUSTED_PROXY_HOPS", 1)
    request = _request({"X-Forwarded-For": "10.0.0.99, 198.51.100.1"})
    assert ip.client_ip(request) == "198.51.100.1"
    assert ip.trusted_client_ip(request) == "198.51.100.1"


# ---------------------------------------------------------------------------
# One parser, not three
# ---------------------------------------------------------------------------


def test_session_policy_delegates_to_the_shared_parser(monkeypatch) -> None:
    """ARCH-16's resolver and this module used to disagree on short chains.

    They must now give the same answer for the same input, or the SAML
    gateway and the rate limiter are once again deciding on different
    addresses for the same request.
    """
    class Stub:
        TRUSTED_PROXY_HOPS = 2

    monkeypatch.setattr(policy, "get_settings", lambda: Stub())

    assert policy.resolve_client_ip(
        socket_ip="10.0.0.1", forwarded_for="1.2.3.4"
    ) is None
    assert policy.resolve_client_ip(
        socket_ip="10.0.0.1", forwarded_for="203.0.113.9, 198.51.100.1"
    ) == "203.0.113.9"


def test_no_router_reads_the_socket_address_directly() -> None:
    """SCIM used `request.client.host` and never parsed X-Forwarded-For, so
    behind ingress every SCIM auth event recorded the load balancer's address
    rather than the IdP's.

    Asserted against the parsed AST rather than the raw text, because the
    source now contains that expression inside a comment explaining why it was
    removed — and a test that a comment can fail is a test nobody keeps.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders: list[str] = []

    def is_socket_peer(node: ast.AST) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "host"
            and isinstance(node.value, ast.Attribute)
            and node.value.attr == "client"
        )

    for path in (root / "app" / "api").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))

        # Passing the peer in as `socket_ip=` is the sanctioned path: the
        # resolver needs the peer as an INPUT and decides whether to use it.
        # saml.py does exactly that, and flagging it would train people to
        # ignore this test.
        sanctioned = {
            id(kw.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            for kw in node.keywords
            if kw.arg == "socket_ip"
        }
        # The peer may also appear inside a conditional: `x if request.client
        # else None`. Follow one level into IfExp bodies.
        for node in ast.walk(tree):
            if isinstance(node, ast.IfExp) and id(node) in sanctioned:
                sanctioned.add(id(node.body))

        for node in ast.walk(tree):
            if is_socket_peer(node) and id(node) not in sanctioned:
                offenders.append(f"{path.relative_to(root)}:{node.lineno}")

    assert not offenders, (
        "Routers reading the socket peer directly instead of through "
        "app.core.client_ip: "
        + ", ".join(offenders)
        + ". Behind ingress these record the load balancer's address, which "
        "defeats source-IP audit and any tenant IP allowlist."
    )


# ---------------------------------------------------------------------------
# IP pinning — recorded since ARCH-16, enforced since ARCH-19
# ---------------------------------------------------------------------------


def test_unpinned_session_is_never_refused() -> None:
    """A misconfigured hop count must not lock out a tenant who never asked
    for pinning."""
    policy.enforce_session_pin(pinned_ip=None, pinned_prefix=None, client_ip=None)


def test_address_inside_the_pin_passes() -> None:
    policy.enforce_session_pin(
        pinned_ip="198.51.100.0", pinned_prefix=24, client_ip="198.51.100.77"
    )


def test_address_outside_the_pin_is_refused() -> None:
    with pytest.raises(policy.SessionPinViolation) as exc:
        policy.enforce_session_pin(
            pinned_ip="198.51.100.0", pinned_prefix=24, client_ip="203.0.113.7"
        )
    assert exc.value.reason == "IP_OUTSIDE_PIN"


def test_pinned_session_with_an_unverifiable_address_fails_closed() -> None:
    """"We cannot tell" is not an acceptable answer about a session the tenant
    asked us to be strict about."""
    with pytest.raises(policy.SessionPinViolation) as exc:
        policy.enforce_session_pin(
            pinned_ip="198.51.100.0", pinned_prefix=24, client_ip=None
        )
    assert exc.value.reason == "CLIENT_IP_UNVERIFIABLE"


def test_violation_reasons_are_a_closed_vocabulary() -> None:
    for pinned, client in (
        ("198.51.100.0", "203.0.113.7"),
        ("198.51.100.0", None),
    ):
        with pytest.raises(policy.SessionPinViolation) as exc:
            policy.enforce_session_pin(
                pinned_ip=pinned, pinned_prefix=24, client_ip=client
            )
        assert exc.value.reason in policy.PIN_VIOLATION_REASONS


def test_strict_pinning_is_a_single_host() -> None:
    policy.enforce_session_pin(
        pinned_ip="198.51.100.7", pinned_prefix=32, client_ip="198.51.100.7"
    )
    with pytest.raises(policy.SessionPinViolation):
        policy.enforce_session_pin(
            pinned_ip="198.51.100.7", pinned_prefix=32, client_ip="198.51.100.8"
        )


def test_ipv6_pins_are_honoured() -> None:
    policy.enforce_session_pin(
        pinned_ip="2001:db8::", pinned_prefix=64, client_ip="2001:db8::dead:beef"
    )
    with pytest.raises(policy.SessionPinViolation):
        policy.enforce_session_pin(
            pinned_ip="2001:db8::", pinned_prefix=64, client_ip="2001:db9::1"
        )


def test_rotation_enforces_the_pin() -> None:
    """The defect ARCH-19 closes: ip_matches_pin() had no call sites.

    Asserted against the source rather than through a live rotation, because
    the failure mode being guarded against is somebody deleting the call, not
    the comparison returning the wrong answer — that is covered above.
    """
    import pathlib

    source = (
        pathlib.Path(__file__).resolve().parents[2]
        / "app/services/session_service.py"
    ).read_text(encoding="utf-8-sig")

    assert "_enforce_ip_pin" in source
    assert source.count("_enforce_ip_pin(") >= 3, (
        "expected the helper plus both rotation paths"
    )
    assert "trusted_ip" in source