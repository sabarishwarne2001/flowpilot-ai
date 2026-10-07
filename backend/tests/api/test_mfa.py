"""N-017 — two-factor sign-in with an authenticator app, end to end through the API.

Proves: enrolment needs the password and a real code; until it is confirmed nothing changes;
once on, the password alone opens no session; the challenge is not a session token; a code is
good once; a recovery code is good once; wrong codes run out; turning it off needs the
password and a code; every change lands in the organization's audit log.
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import select

from app.core import totp
from app.models.audit_log import AuditLog
from app.models.user_mfa import UserMfaFactor
from app.services import login_backoff_service

PASSWORD = "test-password"


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _login(client, email: str, password: str = PASSWORD):
    return client.post("/api/v1/auth/login", data={"username": email, "password": password})


@pytest.fixture()
def owner(tenant):
    email = tenant.owner.user.email
    for ip in ("testclient", "127.0.0.1"):
        login_backoff_service.clear_login_backoff(ip, email)
    login_backoff_service.clear_second_factor_failures(email)
    yield tenant.owner
    for ip in ("testclient", "127.0.0.1"):
        login_backoff_service.clear_login_backoff(ip, email)
    login_backoff_service.clear_second_factor_failures(email)


def _enable(client, owner) -> tuple[str, list[str]]:
    setup = client.post("/api/v1/me/mfa/setup", json={"password": PASSWORD}, headers=_bearer(owner.token))
    assert setup.status_code == 200, setup.text
    secret = setup.json()["secret"]
    confirm = client.post("/api/v1/me/mfa/confirm", json={"code": totp.code_at(secret)},
                          headers=_bearer(owner.token))
    assert confirm.status_code == 200, confirm.text
    return secret, confirm.json()["recovery_codes"]


def test_enrolment_needs_the_password_and_a_real_code(client, owner, db_session):
    wrong = client.post("/api/v1/me/mfa/setup", json={"password": "nope"}, headers=_bearer(owner.token))
    assert wrong.status_code == 400 and wrong.json()["detail"]["code"] == "PASSWORD_INCORRECT"

    setup = client.post("/api/v1/me/mfa/setup", json={"password": PASSWORD}, headers=_bearer(owner.token))
    assert setup.status_code == 200
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/") and body["secret"] in body["otpauth_uri"]

    stored = db_session.get(UserMfaFactor, owner.user.id)
    assert stored is not None and body["secret"] not in stored.secret_ciphertext

    # Started but not confirmed: the password still signs in on its own.
    assert client.get("/api/v1/me/mfa", headers=_bearer(owner.token)).json()["pending"] is True
    assert "access_token" in _login(client, owner.user.email).json()

    bad = client.post("/api/v1/me/mfa/confirm", json={"code": "000000"}, headers=_bearer(owner.token))
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "MFA_CODE_INVALID"

    good = client.post("/api/v1/me/mfa/confirm", json={"code": totp.code_at(body["secret"])},
                       headers=_bearer(owner.token))
    assert good.status_code == 200
    codes = good.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    db_session.refresh(stored)
    assert all(code not in stored.recovery_code_hashes for code in codes)

    status = client.get("/api/v1/me/mfa", headers=_bearer(owner.token)).json()
    assert status["enabled"] is True and status["recovery_codes_remaining"] == 10


def test_the_password_alone_no_longer_opens_a_session(client, owner):
    secret, _ = _enable(client, owner)

    first = _login(client, owner.user.email)
    assert first.status_code == 200
    body = first.json()
    assert body["mfa_required"] is True and "access_token" not in body
    challenge = body["mfa_token"]

    # The challenge is not a session token.
    assert client.get("/api/v1/me/mfa", headers=_bearer(challenge)).status_code == 401

    wrong = client.post("/api/v1/auth/login/mfa", json={"mfa_token": challenge, "code": "123456"})
    assert wrong.status_code == 401 and wrong.json()["detail"]["code"] == "MFA_CODE_INVALID"

    code = totp.code_at(secret, time.time() + 30)  # the confirm step is spent; the next one is not
    second = client.post("/api/v1/auth/login/mfa", json={"mfa_token": challenge, "code": code})
    assert second.status_code == 200, second.text
    session_token = second.json()["access_token"]
    assert client.get("/api/v1/me/mfa", headers=_bearer(session_token)).status_code == 200

    # The same code twice is refused (replay).
    again = _login(client, owner.user.email).json()["mfa_token"]
    replay = client.post("/api/v1/auth/login/mfa", json={"mfa_token": again, "code": code})
    assert replay.status_code == 401


def test_a_recovery_code_works_once(client, owner):
    _, codes = _enable(client, owner)

    challenge = _login(client, owner.user.email).json()["mfa_token"]
    used = client.post("/api/v1/auth/login/mfa", json={"mfa_token": challenge, "code": codes[0].lower()})
    assert used.status_code == 200, used.text

    challenge = _login(client, owner.user.email).json()["mfa_token"]
    reused = client.post("/api/v1/auth/login/mfa", json={"mfa_token": challenge, "code": codes[0]})
    assert reused.status_code == 401

    status = client.get("/api/v1/me/mfa", headers=_bearer(owner.token)).json()
    assert status["recovery_codes_remaining"] == 9


def test_wrong_codes_run_out(client, owner, monkeypatch):
    secret, _ = _enable(client, owner)
    # Prove the per-account cap on its own, without the (ip, email) ladder.
    monkeypatch.setattr("app.core.config.settings.LOGIN_BACKOFF_ENABLED", False)

    challenge = _login(client, owner.user.email).json()["mfa_token"]
    for _ in range(login_backoff_service.SECOND_FACTOR_MAX_FAILURES):
        assert client.post("/api/v1/auth/login/mfa",
                           json={"mfa_token": challenge, "code": "000000"}).status_code == 401

    locked = client.post("/api/v1/auth/login/mfa",
                         json={"mfa_token": challenge, "code": totp.code_at(secret, time.time() + 30)})
    assert locked.status_code == 429 and locked.json()["detail"]["code"] == "MFA_LOCKED"


def test_turning_it_off_needs_the_password_and_a_code(client, owner, db_session):
    _, codes = _enable(client, owner)

    no_password = client.post("/api/v1/me/mfa/disable", json={"password": "nope", "code": codes[1]},
                              headers=_bearer(owner.token))
    assert no_password.status_code == 400
    no_code = client.post("/api/v1/me/mfa/disable", json={"password": PASSWORD, "code": "000000"},
                          headers=_bearer(owner.token))
    assert no_code.status_code == 422

    off = client.post("/api/v1/me/mfa/disable", json={"password": PASSWORD, "code": codes[1]},
                      headers=_bearer(owner.token))
    assert off.status_code == 204
    assert "access_token" in _login(client, owner.user.email).json()

    events = db_session.execute(
        select(AuditLog.details).where(AuditLog.resource_id == owner.user.id)
    ).scalars().all()
    recorded = {details.get("mfa") for details in events if details}
    assert {"enabled", "disabled"} <= recorded


def test_an_unknown_or_forged_challenge_is_refused(client, owner):
    _enable(client, owner)
    for token in ("not-a-token", owner.token):  # a session token is not a challenge either
        refused = client.post("/api/v1/auth/login/mfa", json={"mfa_token": token, "code": "123456"})
        assert refused.status_code == 401
