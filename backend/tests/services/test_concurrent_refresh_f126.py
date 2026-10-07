"""F-126 — two tabs (or a reload during a refresh) must never sign each other out.

What happened (browser run, 2026-10-07): a page was reloaded while its refresh was
in flight. The new page's refresh rotated A -> B and the page got B's access token.
The cut-off request then presented A inside the grace window, and the grace path
rotated the chain tip B -> C, which revoked B. The page's next request carried B's
access token and was refused (`AUTH_REJECTED reason=session_revoked`); C's cookie
never reached the browser, which still held B.

The same thing happens without any reload: every refresh in one tab revoked the
session whose access token the other tab was still using, so each tab's next
request after the other refreshed was a 401.

The fix keeps the two properties the rotation tests protect (one live session per
sign-in, and a refresh token presented twice outside the grace window ends the
sign-in), and adds:

* an access token whose session was retired only by ROTATION stays valid until it
  expires, as long as its sign-in is still alive (no live session left = refused);
* a token retired by a racing tab before its own holder ever presented it (B above)
  is served the first time its holder presents it, at any age; the second time is
  ordinary reuse;
* signing out, or revoking a device, ends the whole sign-in, so the first point
  cannot keep an older access token alive after the user asked to be signed out.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import settings
from app.core.cookies import REFRESH_COOKIE_NAME, refresh_cookie_path
from app.core.security import decode_access_token_claims
from app.core.tokens import hash_token
from app.models.user_session import UserSession
from app.services import session_service as svc

PASSWORD = "correct-horse-battery-staple"
ME = "/api/v1/auth/me"


def _login(client, user):
    response = client.post("/api/v1/auth/login", data={"username": user.email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def _refresh(client):
    response = client.post("/api/v1/auth/refresh")
    return response


def _me(client, token) -> int:
    return client.get(ME, headers={"Authorization": f"Bearer {token}"}).status_code


def _use_cookie(client, value: str) -> None:
    client.cookies.clear()
    client.cookies.set(REFRESH_COOKIE_NAME, value, path=refresh_cookie_path())


def _age(db, plaintext: str, seconds: int) -> None:
    row = db.query(UserSession).filter(UserSession.token_hash == hash_token(plaintext)).one()
    row.rotated_at = datetime.now(UTC) - timedelta(seconds=seconds)
    db.commit()


PAST_GRACE = settings.SESSION_REUSE_GRACE_SECONDS + 5


# ===========================================================================
# Over the wire
# ===========================================================================

def test_the_other_tabs_access_token_survives_a_refresh(client, registered):
    tab_b = _login(client, registered)
    response = _refresh(client)  # tab A refreshes
    assert response.status_code == 200

    # Tab B has not refreshed yet; its token has minutes left and its sign-in is alive.
    assert _me(client, tab_b) == 200
    assert _me(client, response.json()["access_token"]) == 200


def test_the_f126_trace_reload_during_a_refresh(client, registered, db):
    _login(client, registered)
    cookie_a = client.cookies.get(REFRESH_COOKIE_NAME)

    # The new page's refresh: A -> B. The browser keeps B's cookie and access token.
    page = _refresh(client)
    assert page.status_code == 200
    token_b = page.json()["access_token"]
    cookie_b = client.cookies.get(REFRESH_COOKIE_NAME)

    # The cut-off request presents A inside the grace window. Its answer never arrives.
    _use_cookie(client, cookie_a)
    assert _refresh(client).status_code == 200
    _use_cookie(client, cookie_b)

    # The page's next request: B's access token.
    assert _me(client, token_b) == 200

    # Minutes later the page refreshes with the cookie it holds (B), long past the grace.
    _age(db, cookie_b, PAST_GRACE)
    later = _refresh(client)
    assert later.status_code == 200, later.text
    assert _me(client, later.json()["access_token"]) == 200


def test_a_superseded_token_presented_twice_is_still_reuse(client, registered, db):
    _login(client, registered)
    cookie_a = client.cookies.get(REFRESH_COOKIE_NAME)
    _refresh(client)
    cookie_b = client.cookies.get(REFRESH_COOKIE_NAME)
    _use_cookie(client, cookie_a)
    _refresh(client)  # supersedes B

    _use_cookie(client, cookie_b)
    first = _refresh(client)  # B's holder, first presentation
    assert first.status_code == 200
    token = first.json()["access_token"]

    _age(db, cookie_b, PAST_GRACE)
    _use_cookie(client, cookie_b)
    stolen = _refresh(client)  # the same token again, outside the grace: theft
    assert stolen.status_code == 401
    assert "reused" in stolen.json()["detail"].lower()
    assert _me(client, token) == 401


def test_sign_out_ends_every_access_token_of_the_sign_in(client, registered):
    first = _login(client, registered)
    second = _refresh(client).json()["access_token"]

    assert client.post("/api/v1/auth/logout").status_code == 204
    assert _me(client, first) == 401
    assert _me(client, second) == 401


def test_sign_out_with_a_stale_cookie_still_signs_the_device_out(client, registered):
    first = _login(client, registered)
    stale = client.cookies.get(REFRESH_COOKIE_NAME)
    second = _refresh(client).json()["access_token"]
    current = client.cookies.get(REFRESH_COOKIE_NAME)

    _use_cookie(client, stale)
    assert client.post("/api/v1/auth/logout").status_code == 204

    _use_cookie(client, current)
    assert _refresh(client).status_code == 401
    assert _me(client, first) == 401
    assert _me(client, second) == 401


def test_revoking_a_device_ends_its_older_access_tokens_too(client, second_client, registered):
    laptop = _login(client, registered)
    phone_old = _login(second_client, registered)
    phone_new = _refresh(second_client).json()["access_token"]
    phone_session = str(decode_access_token_claims(phone_new).session_id)

    assert client.delete(
        f"/api/v1/auth/sessions/{phone_session}", headers={"Authorization": f"Bearer {laptop}"}
    ).status_code == 204

    assert _me(second_client, phone_new) == 401
    assert _me(second_client, phone_old) == 401
    assert _me(client, laptop) == 200


def test_reuse_detection_ends_older_access_tokens(client, registered, db):
    first = _login(client, registered)
    stolen = client.cookies.get(REFRESH_COOKIE_NAME)
    second = _refresh(client).json()["access_token"]

    _age(db, stolen, PAST_GRACE)
    _use_cookie(client, stolen)
    assert _refresh(client).status_code == 401

    assert _me(client, first) == 401
    assert _me(client, second) == 401


# ===========================================================================
# The service
# ===========================================================================

def test_racing_tabs_still_converge_on_one_live_session(db, registered):
    first = svc.create_session(db, user=registered)
    svc.rotate_session(db, refresh_token=first.plaintext_token)
    svc.rotate_session(db, refresh_token=first.plaintext_token)

    assert len(svc.list_active_sessions(db, user=registered)) == 1


def test_an_ordinary_rotated_token_is_still_reuse_outside_the_grace(db, registered):
    first = svc.create_session(db, user=registered)
    svc.rotate_session(db, refresh_token=first.plaintext_token)
    db.refresh(first.session)
    first.session.rotated_at = datetime.now(UTC) - timedelta(seconds=PAST_GRACE)
    db.flush()

    with pytest.raises(svc.SessionReuseDetectedError):
        svc.rotate_session(db, refresh_token=first.plaintext_token)
