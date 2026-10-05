"""Brute-force protection on login, live.

Past POLICY_LOGIN_ACCOUNT_IP.threshold wrong passwords for one account from
one address, the account is refused from that address for an escalating
window (1 s, 2 s, 4 s ... up to 15 minutes; every refused attempt counts as
another failure) - with the CORRECT password too, and with the same answer a wrong password gets (nothing tells
an attacker the guess was right). Clearing the backoff (what a successful
login or the window's expiry does) lets the owner back in.

Whether that refusal should instead be 429 with Retry-After (what
tests/api/test_login_backoff.py expects) is logged for the owner (NEEDS-OWNER).
"""

from __future__ import annotations

from app.core.rate_limit.policy import POLICY_LOGIN_ACCOUNT_IP
from app.services.login_backoff_service import clear_login_backoff
from tests.engines.conftest import Engines

PASSWORD = "test-password"  # tests/conftest.py _make_user


def _login(engines: Engines, email: str, password: str):
    return engines.client.post("/api/v1/auth/login", data={"username": email, "password": password})


def test_repeated_wrong_passwords_lock_the_account_from_that_address(engines: Engines) -> None:
    email = engines.tenant.contributor.user.email
    for ip in ("unknown", "testclient", "127.0.0.1"):
        clear_login_backoff(ip, email)
    assert _login(engines, email, PASSWORD).status_code == 200

    for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 3):  # the window is now at least 4 s
        assert _login(engines, email, "wrong-password").status_code == 401

    refused = _login(engines, email, PASSWORD)
    assert refused.status_code == 401, "the correct password got through a locked account"
    assert refused.json() == _login(engines, email, "wrong-password").json()

    for ip in ("unknown", "testclient", "127.0.0.1"):
        clear_login_backoff(ip, email)
    assert _login(engines, email, PASSWORD).status_code == 200
