"""Authentication — forged, stale and confused tokens are refused, never a 500.

    pytest tests/security/test_auth_token_attacks.py -q

The session lifecycle (rotation, reuse detection, logout, revocation) has
extensive tests in tests/services/test_session_service.py and
test_auth_endpoints.py. These cover what an ATTACKER sends instead: unsigned and
wrongly signed tokens, algorithm confusion, tokens of the wrong type or for a
missing user, malformed headers. Every one must answer 401 with the same body,
and none may crash the server or reveal why.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy.orm import Session

from app.core import security
from app.core.config import settings
from tests.conftest import Fixture, Persona, _make_user

ME = "/api/v1/auth/me"


def _b64(raw: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(raw).encode()).rstrip(b"=").decode()


def _claims(subject, *, exp_in: int = 600, **extra) -> dict:
    now = int(time.time())
    return {
        "sub": str(subject),
        "jti": str(uuid.uuid4()),
        "iat": now,
        "exp": now + exp_in,
        "type": "access",
        **extra,
    }


def _signed(claims: dict, *, key: str | None = None, algorithm: str = "HS256") -> str:
    return jwt.encode(claims, key or settings.JWT_SECRET_KEY.get_secret_value(), algorithm=algorithm)


def _get(client: TestClient, token: str):
    return client.get(ME, headers={"Authorization": f"Bearer {token}"})


def test_a_genuine_token_is_accepted(client: TestClient, tenant: Fixture) -> None:
    """Control: the requests below are refused because they are forged."""
    assert client.get(ME, headers=tenant.owner.headers).status_code == 200


def test_an_unsigned_alg_none_token_is_refused(client: TestClient, tenant: Fixture) -> None:
    header = _b64({"alg": "none", "typ": "JWT"})
    body = _b64(_claims(tenant.owner.user.id))
    for token in (f"{header}.{body}.", f"{header}.{body}.AAAA"):
        assert _get(client, token).status_code == 401


def test_a_token_signed_with_another_secret_is_refused(client: TestClient, tenant: Fixture) -> None:
    forged = _signed(_claims(tenant.owner.user.id), key="a-different-secret-" + "x" * 40)
    assert _get(client, forged).status_code == 401


def test_a_tampered_payload_keeps_the_old_signature_and_is_refused(client: TestClient, tenant: Fixture) -> None:
    genuine = tenant.owner.token
    head, _payload, signature = genuine.split(".")
    other_body = _b64(_claims(tenant.org_admin.user.id))
    assert _get(client, f"{head}.{other_body}.{signature}").status_code == 401


@pytest.mark.parametrize("algorithm", ["HS384", "HS512"])
def test_only_the_configured_algorithm_is_accepted(client: TestClient, tenant: Fixture, algorithm: str) -> None:
    """Signed with the RIGHT secret but a different HMAC: still refused."""
    token = _signed(_claims(tenant.owner.user.id), algorithm=algorithm)
    assert _get(client, token).status_code == 401


def test_an_expired_token_is_refused(client: TestClient, tenant: Fixture) -> None:
    assert _get(client, _signed(_claims(tenant.owner.user.id, exp_in=-5))).status_code == 401


def test_a_token_of_the_wrong_type_is_refused(client: TestClient, tenant: Fixture) -> None:
    for kind in ("refresh", "verify", "reset", ""):
        token = _signed(_claims(tenant.owner.user.id, type=kind))
        assert _get(client, token).status_code == 401, kind


def test_a_token_with_no_type_or_no_expiry_is_refused(client: TestClient, tenant: Fixture) -> None:
    no_type = _claims(tenant.owner.user.id)
    del no_type["type"]
    no_exp = _claims(tenant.owner.user.id)
    del no_exp["exp"]
    for claims in (no_type, no_exp):
        assert _get(client, _signed(claims)).status_code == 401


def test_a_token_for_a_user_that_does_not_exist_is_refused(client: TestClient, tenant: Fixture) -> None:
    assert _get(client, _signed(_claims(uuid.uuid4()))).status_code == 401


@pytest.mark.parametrize("subject", ["not-a-uuid", "", "1; DROP TABLE users", "00000000-0000-0000-0000-000000000000"])
def test_a_malformed_subject_is_a_401_not_a_500(client: TestClient, subject: str) -> None:
    assert _get(client, _signed(_claims(subject))).status_code == 401


@pytest.mark.parametrize(
    "header",
    ["", "Bearer", "Bearer ", "Bearer a.b", "Bearer " + "A" * 20000, "Basic dXNlcjpwYXNz", "bearer\x00token", "Bearer null"],
)
def test_malformed_authorization_headers_are_a_401_not_a_500(client: TestClient, header: str) -> None:
    try:
        response = client.get(ME, headers={"Authorization": header})
    except Exception as error:  # noqa: BLE001 - e.g. an illegal header value the client rejects
        pytest.skip(f"the HTTP client refused to send this header: {error}")
    assert response.status_code in (401, 403), response.status_code


def test_every_refusal_gives_the_same_body_so_the_reason_is_not_revealed(client: TestClient, tenant: Fixture) -> None:
    bodies = {
        _get(client, _signed(_claims(tenant.owner.user.id), key="x" * 40)).text,  # bad signature
        _get(client, _signed(_claims(tenant.owner.user.id, exp_in=-5))).text,  # expired
        _get(client, _signed(_claims(uuid.uuid4()))).text,  # unknown user
        _get(client, "garbage").text,  # not a token
    }
    assert len(bodies) == 1, bodies


def test_an_unverified_users_valid_token_reaches_no_tenant_data(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    """Even a genuine MEMBER whose address is unverified is refused on tenant
    routes (`get_verified_user`). Control: verified, the same call succeeds."""
    tenant.viewer.user.email_verified_at = None
    db_session.commit()
    org = f"/api/v1/organizations/{tenant.organization.id}"
    ws = f"/api/v1/workspaces/{tenant.workspace.id}"
    for path in (org, f"{org}/members", ws, f"{ws}/work-items"):
        assert client.get(path, headers=tenant.viewer.headers).status_code == 403, path

    tenant.viewer.user.email_verified_at = datetime.now(timezone.utc)
    db_session.commit()
    assert client.get(ws, headers=tenant.viewer.headers).status_code == 200


def test_a_deactivated_users_valid_token_is_refused(client: TestClient, db_session: Session, tenant: Fixture) -> None:
    tenant.viewer.user.is_active = False
    db_session.commit()
    assert client.get(ME, headers=tenant.viewer.headers).status_code in (400, 401, 403)


def test_a_token_issued_before_the_users_revocation_cutoff_is_refused(
    client: TestClient, db_session: Session, tenant: Fixture
) -> None:
    """`logout-all` and a password change stamp `sessions_revoked_at`; tokens
    minted earlier must stop working immediately, not at their expiry."""
    old = _signed(_claims(tenant.owner.user.id, iat=int(time.time()) - 120))
    tenant.owner.user.sessions_revoked_at = datetime.now(timezone.utc) - timedelta(seconds=30)
    db_session.commit()
    assert _get(client, old).status_code == 401


def test_an_access_token_cannot_be_used_where_a_refresh_cookie_is_expected(client: TestClient, tenant: Fixture) -> None:
    client.cookies.set("flowpilot_refresh", tenant.owner.token, path="/api/v1/auth/refresh")
    assert client.post("/api/v1/auth/refresh").status_code == 401


def test_the_access_token_lifetime_is_short_in_the_shipped_configuration() -> None:
    """A stolen access token works until it expires, so the lifetime is the
    exposure window. The code default and the production template say 10."""
    from pathlib import Path

    assert settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 60
    template = (Path(__file__).resolve().parents[2] / ".env.production.template").read_text(encoding="utf-8")
    assert "ACCESS_TOKEN_EXPIRE_MINUTES=10" in template
