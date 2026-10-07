"""ASVS V2.1 — a new password must be 12+ characters and not a common or easy one.

Before this release the server took any 8-character password, "Password123!" included, at
sign-up, reset and change. Each path now refuses with 422 PASSWORD_TOO_WEAK and a plain-words
reason that never repeats the password; a refused reset leaves the emailed link usable.
"""

from __future__ import annotations

import uuid

import pytest

from app.core import security
from app.core.config import settings
from app.models.user import User
from app.services import auth_token_service
from app.models.auth_token import AuthTokenPurpose

STRONG = "maple-orbit-quietly-lantern"
WEAK = ("Password123!", "qwertyuiop12", "flowpilot2026", "iloveyou12345")


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch):
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)


def _register(client, email: str, password: str):
    return client.post("/api/v1/auth/register", json={"email": email, "password": password})


@pytest.mark.parametrize("password", WEAK)
def test_sign_up_refuses_a_weak_password(client, password):
    response = _register(client, f"weak-{uuid.uuid4().hex[:8]}@example.com", password)
    assert response.status_code == 422, response.text
    body = response.json()
    assert body["code"] == "PASSWORD_TOO_WEAK"
    assert password not in response.text


def test_sign_up_refuses_fewer_than_12_characters(client):
    response = _register(client, f"short-{uuid.uuid4().hex[:8]}@example.com", "Tr0ub4dor&3")
    assert response.status_code == 422
    assert "Tr0ub4dor&3" not in response.text


def test_sign_up_accepts_a_long_phrase(client):
    response = _register(client, f"strong-{uuid.uuid4().hex[:8]}@example.com", STRONG)
    assert response.status_code in (200, 201, 202), response.text


def test_the_answer_is_the_same_for_an_existing_address(client, tenant):
    """No account enumeration: a weak password is refused before the address is looked up."""
    existing = _register(client, tenant.owner.user.email, "Password123!")
    fresh = _register(client, f"nobody-{uuid.uuid4().hex[:8]}@example.com", "Password123!")
    assert existing.status_code == fresh.status_code == 422
    assert existing.json() == fresh.json()


def test_change_password_refuses_a_weak_one(client, tenant, db_session):
    owner = tenant.owner
    response = client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "test-password", "new_password": "Password123!"},
        headers={"Authorization": f"Bearer {owner.token}"},
    )
    assert response.status_code == 422 and response.json()["code"] == "PASSWORD_TOO_WEAK"
    db_session.refresh(owner.user)
    assert security.verify_password("test-password", owner.user.hashed_password)


def test_a_refused_reset_leaves_the_link_usable(client, db_session):
    user = User(
        email=f"reset-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=security.get_password_hash("an-older-long-passphrase"),
        is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    issued = auth_token_service.issue_token(
        db_session, user=user, purpose=AuthTokenPurpose.PASSWORD_RESET, enforce_rate_limit=False
    )
    db_session.commit()
    token = issued.plaintext_token

    weak = client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": "Password123!"})
    assert weak.status_code == 422 and weak.json()["code"] == "PASSWORD_TOO_WEAK"

    strong = client.post("/api/v1/auth/reset-password", json={"token": token, "new_password": STRONG})
    assert strong.status_code == 200, strong.text
