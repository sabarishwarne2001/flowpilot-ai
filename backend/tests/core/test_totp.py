"""RFC 6238 / RFC 4226 conformance of app.core.totp (two-factor sign-in)."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest

from app.core import totp

pytestmark = pytest.mark.no_db

# RFC 6238 Appendix B: SHA-1 key "12345678901234567890", 8-digit codes.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")
RFC_VECTORS = [(59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
               (1234567890, "89005924"), (2000000000, "69279037"), (20000000000, "65353130")]


@pytest.mark.parametrize(("moment", "expected"), RFC_VECTORS)
def test_rfc_6238_sha1_vectors(moment, expected):
    key = base64.b32decode(RFC_SECRET + "=" * (-len(RFC_SECRET) % 8))
    assert totp.hotp(key, int(moment // 30), digits=8) == expected


def test_rfc_4226_hotp_vectors():
    key = b"12345678901234567890"
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [totp.hotp(key, counter) for counter in range(10)] == expected


def test_verify_accepts_one_step_of_clock_drift_and_nothing_more():
    secret = totp.new_secret()
    now = 1_800_000_000.0
    assert totp.verify(secret, totp.code_at(secret, now), moment=now) == totp.step_at(now)
    assert totp.verify(secret, totp.code_at(secret, now - 30), moment=now) is not None
    assert totp.verify(secret, totp.code_at(secret, now + 30), moment=now) is not None
    assert totp.verify(secret, totp.code_at(secret, now - 90), moment=now) is None


def test_a_used_step_is_refused():
    secret = totp.new_secret()
    now = 1_800_000_000.0
    code = totp.code_at(secret, now)
    step = totp.verify(secret, code, moment=now)
    assert totp.verify(secret, code, moment=now, not_before_step=step) is None


def test_malformed_codes_are_refused():
    secret = totp.new_secret()
    for code in ("", "12345", "1234567", "abcdef", None):
        assert totp.verify(secret, code) is None  # type: ignore[arg-type]
    assert totp.verify(secret, " " + totp.code_at(secret)[:3] + " " + totp.code_at(secret)[3:]) is not None


def test_provisioning_uri_is_what_authenticator_apps_read():
    uri = totp.provisioning_uri("ABCDEFGH", account="ana@example.com", issuer="FlowPilot AI")
    parsed = urlparse(uri)
    assert parsed.scheme == "otpauth" and parsed.netloc == "totp"
    assert parsed.path == "/FlowPilot%20AI:ana@example.com"
    query = parse_qs(parsed.query)
    assert query["secret"] == ["ABCDEFGH"] and query["issuer"] == ["FlowPilot AI"]
    assert query["digits"] == ["6"] and query["period"] == ["30"]
