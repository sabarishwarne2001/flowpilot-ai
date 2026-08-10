"""
ARCH-03 Step 10 — the R1–R9 regression matrix.

Most of these properties already hold and are already covered somewhere in this
suite. That is not the same as being *asserted*. A property that holds as a
side effect of how something was written disappears the day someone rewrites it
for an unrelated reason, and nothing goes red.

So each risk gets a named test that fails loudly and says which risk it was.
The name is the point: when one of these breaks in six months, the failure
should read as "R6 regressed", not as "test_decode_returns_none failed".

    R1  grandfathering missed -> existing users locked out
    R2  concurrent refresh misread as replay -> random sign-outs
    R3  cookie SameSite/Domain/Secure wrong in production
    R4  token leaks via Referer or access logs
    R5  reset does not revoke sessions
    R6  refresh token accepted as an access token
    R7  platform SMTP unavailable -> registration fails
    R8  sessions grows unbounded
    R9  verification gating breaks the ARCH-02 suite
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from jose import jwt
from sqlalchemy import inspect, text

from app.core import security
from app.core.config import settings
from app.core.cookies import REFRESH_COOKIE_NAME, refresh_cookie_path
from app.core.tokens import hash_token
from app.models.auth_token import AuthToken, AuthTokenPurpose
from app.models.user import User
from app.models.user_session import SessionRevokedReason, UserSession
from app.services import (
    auth_token_service,
    password_service,
    session_service,
    verification_service,
)

PASSWORD = "correct-horse-battery-staple"
VERIFY = AuthTokenPurpose.EMAIL_VERIFICATION
RESET = AuthTokenPurpose.PASSWORD_RESET


# ===========================================================================
# R1 — grandfathering
# ===========================================================================

def test_R1_email_verified_at_is_permanently_nullable(engine):
    """
    R1, and the correction to the plan that came out of it.

    Step 5 as originally written called for NOT NULL on this column. That would
    have made an unverified account unrepresentable and registration
    impossible — NULL is the encoding of "not yet verified", not a gap in the
    backfill.

    The migration-time assertion (zero NULLs across the four audited rows)
    cannot be re-run; it fired once, inside a transaction that has long since
    committed. What can be asserted forever is the schema it was allowed to
    produce, which is this.
    """
    columns = {
        c["name"]: c for c in inspect(engine).get_columns("users")
    }
    assert columns["email_verified_at"]["nullable"] is True
    assert columns["sessions_revoked_at"]["nullable"] is True


def test_R1_a_new_account_starts_unverified(db):
    """A registration path that produced a verified account would silently
    grandfather everyone, which is the same failure from the other side."""
    row = User(
        email=f"r1-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="!x",
        is_active=True,
    )
    db.add(row)
    db.flush()
    assert row.email_verified_at is None


def test_R1_invitation_plaintext_column_is_gone(engine):
    """
    The CONTRACT half of the same migration.

    If this column ever reappears, something has re-added plaintext token
    storage and §A.2.2 is open again.
    """
    columns = {
        c["name"] for c in inspect(engine).get_columns("workspace_invitations")
    }
    assert "token" not in columns
    assert "token_hash" in columns


# ===========================================================================
# R2 — concurrent refresh
# ===========================================================================

def test_R2_two_tabs_refreshing_together_do_not_sign_the_user_out(
    client, registered
):
    """
    The highest-likelihood operational failure in the phase.

    Both tabs present the same cookie because they share one cookie jar. If the
    second is read as a replay, the family is revoked and the user is signed
    out of a device they are actively using, with nothing in the logs that
    looks like an error.
    """
    _login(client, registered)
    original = client.cookies.get(REFRESH_COOKIE_NAME)

    first = client.post("/api/v1/auth/refresh")
    client.cookies.set(
        REFRESH_COOKIE_NAME, original, path=refresh_cookie_path()
    )
    second = client.post("/api/v1/auth/refresh")

    assert first.status_code == 200
    assert second.status_code == 200
    assert client.post("/api/v1/auth/refresh").status_code == 200


def test_R2_many_tabs_converge_on_one_session(client, registered, db):
    """
    Rotating the tip rather than the presented row is what keeps this at one
    live session instead of four forked branches.
    """
    _login(client, registered)
    original = client.cookies.get(REFRESH_COOKIE_NAME)

    for _ in range(4):
        client.cookies.set(
            REFRESH_COOKIE_NAME, original, path=refresh_cookie_path()
        )
        assert client.post("/api/v1/auth/refresh").status_code == 200

    live = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == registered.id,
            UserSession.revoked_at.is_(None),
        )
        .count()
    )
    assert live == 1


def test_R2_genuine_replay_is_still_caught(client, registered, db):
    """The other edge. A grace window wide enough to never false-positive
    would also never catch a theft."""
    _login(client, registered)
    stolen = client.cookies.get(REFRESH_COOKIE_NAME)
    client.post("/api/v1/auth/refresh")

    row = (
        db.query(UserSession)
        .filter(UserSession.token_hash == hash_token(stolen))
        .one()
    )
    row.rotated_at = datetime.now(UTC) - timedelta(
        seconds=settings.SESSION_REUSE_GRACE_SECONDS + 5
    )
    db.commit()

    client.cookies.set(
        REFRESH_COOKIE_NAME, stolen, path=refresh_cookie_path()
    )
    assert client.post("/api/v1/auth/refresh").status_code == 401


# ===========================================================================
# R3 — cookie attributes
# ===========================================================================

def test_R3_refresh_cookie_attributes(client, registered):
    """
    The failure mode here is silent: the browser discards or withholds the
    cookie, login looks fine, and refresh 401s forever with nothing logged.
    """
    response = _login_response(client, registered)
    cookie = response.headers.get("set-cookie", "")

    assert REFRESH_COOKIE_NAME in cookie
    assert "HttpOnly" in cookie
    assert "samesite=lax" in cookie.lower()
    assert f"Path={refresh_cookie_path()}" in cookie


def test_R3_no_domain_attribute(client, registered):
    """
    Host-only, deliberately.

    Domain=.flowpilot.ai would attach a fourteen-day session credential to
    every present and future subdomain — docs, marketing, staging — and
    HttpOnly does not help, because the browser sends it unprompted.
    """
    cookie = _login_response(client, registered).headers.get("set-cookie", "")
    assert "domain=" not in cookie.lower()


def test_R3_secure_follows_the_environment(client, registered, monkeypatch):
    """
    Off in development because Safari rejects Secure over http://localhost
    where Chrome accepts it; on everywhere else, or the cookie never comes back
    over HTTPS.
    """
    from app.core import cookies

    assert settings.ENVIRONMENT == "development"
    cookie = _login_response(client, registered).headers.get("set-cookie", "")
    assert "secure" not in cookie.lower()

    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    assert cookies._secure() is True


def test_R3_clearing_matches_what_set_it(client, registered):
    """
    A browser matches a deletion on name, domain and path. A mismatch leaves
    the original cookie in place and the sign-out silently fails.
    """
    _login(client, registered)
    response = client.post("/api/v1/auth/logout")
    cookie = response.headers.get("set-cookie", "")

    assert f"Path={refresh_cookie_path()}" in cookie
    assert "Max-Age=0" in cookie or "01 Jan 1970" in cookie


# ===========================================================================
# R4 — token leakage
# ===========================================================================

@pytest.mark.parametrize(
    "builder",
    [
        verification_service.build_verification_link,
        password_service.build_reset_link,
    ],
)
def test_R4_identity_links_carry_tokens_only_in_the_fragment(builder):
    """
    §B.9. A fragment is not sent to any server, so it cannot leak through
    Referer to third-party assets on the landing page.
    """
    link = builder("sample-token-value")
    before_fragment = link.split("#", 1)[0]

    assert "?" not in before_fragment
    assert "token" not in before_fragment
    assert "#token=sample-token-value" in link


def test_R4_no_plaintext_token_reaches_the_logs(db, user):
    """
    Every log line in the identity services names ids, never secrets. This
    catches the well-meaning debug line that prints the whole object.

    Uses its own handler rather than pytest's caplog, because caplog needs the
    logging plugin and this suite is routinely run with -p no:logging to keep
    the output readable. A regression test that silently errors out under the
    flag everyone uses is not a regression test.
    """
    with _captured_logs() as emitted:
        issued = auth_token_service.issue_token(
            db, user=user, purpose=AuthTokenPurpose.EMAIL_VERIFICATION
        )
        db.flush()
        auth_token_service.consume_token(
            db,
            token=issued.plaintext_token,
            purpose=AuthTokenPurpose.EMAIL_VERIFICATION,
        )

    assert issued.plaintext_token not in emitted.text
    assert issued.auth_token.token_hash not in emitted.text


def test_R4_no_plaintext_refresh_token_reaches_the_logs(db, user):
    with _captured_logs() as emitted:
        issued = session_service.create_session(db, user=user)
        session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )

    assert issued.plaintext_token not in emitted.text


def test_R4_no_token_column_stores_plaintext(db, user):
    """
    The storage half. Every token in the system is persisted as a hash; a read
    of any of these tables yields nothing replayable.
    """
    issued = auth_token_service.issue_token(
        db, user=user, purpose=AuthTokenPurpose.PASSWORD_RESET
    )
    session = session_service.create_session(db, user=user)
    db.flush()

    for row, plaintext in (
        (issued.auth_token, issued.plaintext_token),
        (session.session, session.plaintext_token),
    ):
        stored = [
            getattr(row, column.name)
            for column in row.__table__.columns
        ]
        assert plaintext not in [v for v in stored if isinstance(v, str)]


# ===========================================================================
# R5 — reset revokes sessions
# ===========================================================================

def test_R5_reset_revokes_every_session_row(
    client, second_client, registered, db
):
    _login(client, registered)
    _login(second_client, registered)

    plaintext = _issue_reset(db, registered)
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": "a-brand-new-passphrase"},
    )

    live = (
        db.query(UserSession)
        .filter(
            UserSession.user_id == registered.id,
            UserSession.revoked_at.is_(None),
        )
        .count()
    )
    assert live == 0
    assert second_client.post("/api/v1/auth/refresh").status_code == 401


def test_R5_reset_stamps_the_global_cutoff(client, registered, db):
    """
    Revoking rows alone is not enough. Access tokens are stateless cutoff.
    """
    plaintext = _issue_reset(db, registered)
    client.post(
        "/api/v1/auth/reset-password",
        json={"token": plaintext, "new_password": "a-brand-new-passphrase"},
    )

    db.refresh(registered)
    assert registered.sessions_revoked_at is not None


def test_R5_reset_invalidates_other_outstanding_links(client, registered, db):
    attacker_link = _issue_reset(db, registered)
    owner_link = _issue_reset(db, registered)

    client.post(
        "/api/v1/auth/reset-password",
        json={"token": owner_link, "new_password": "a-brand-new-passphrase"},
    )

    assert client.post(
        "/api/v1/auth/reset-password",
        json={"token": attacker_link, "new_password": "third-passphrase-x"},
    ).status_code == 400


# ===========================================================================
# R6 — token type confusion
# ===========================================================================

def test_R6_a_token_without_a_type_claim_is_rejected():
    """The pre-ARCH-03 shape."""
    legacy = jwt.encode(
        _claims(),
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    assert security.decode_access_token(legacy) is None


def test_R6_a_token_of_another_type_is_rejected():
    """
    A validly signed JWT of some other kind must not authenticate.
    """
    other = jwt.encode(
        {**_claims(), "type": "download"},
        settings.JWT_SECRET_KEY.get_secret_value(),
        algorithm=settings.JWT_ALGORITHM,
    )
    assert security.decode_access_token(other) is None


def test_R6_a_refresh_token_is_not_a_jwt_at_all(db, user):
    """
    The structural reason R6 is closed rather than merely guarded.
    """
    issued = session_service.create_session(db, user=user)
    db.flush()
    assert security.decode_access_token(issued.plaintext_token) is None


def test_R6_an_access_token_is_not_a_refresh_token(client, registered, db):
    """And the converse: the JWT cannot be spent as a refresh credential."""
    token = _login(client, registered)
    with pytest.raises(session_service.InvalidRefreshTokenError):
        session_service.rotate_session(db, refresh_token=token)


# ===========================================================================
# R7 — SMTP outage must not break sign-up
# ===========================================================================

def test_R7_registration_succeeds_when_the_mail_server_is_down(
    client, db, monkeypatch
):
    """
    A registration that 500s because SMTP is unreachable converts a mail outage
    into an inability to sign up. The account must exist either way; resend is
    one click.
    """
    import app.core.platform_email as platform_email

    def _explode(**_kwargs):
        raise ConnectionRefusedError("simulated relay outage")

    monkeypatch.setattr(platform_email, "send_platform_email", _explode)

    email = f"r7-{uuid.uuid4().hex[:8]}@example.com"
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": PASSWORD}
    )

    assert response.status_code == 202
    assert db.query(User).filter(User.email == email).one_or_none() is not None


def test_R7_resend_reports_failure_without_failing(
    client, unverified, monkeypatch
):
    """202 with delivered=False. The token is valid; only the delivery failed,
    and the account is not broken."""
    import app.core.platform_email as platform_email

    monkeypatch.setattr(
        platform_email,
        "send_platform_email",
        lambda **_kwargs: (False, "simulated relay outage"),
    )

    token = _login(client, unverified)
    response = client.post(
        "/api/v1/auth/resend-verification",
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 202
    assert response.json()["delivered"] is False


# ===========================================================================
# R8 — unbounded growth
# ===========================================================================

def test_R8_sweeper_removes_long_expired_rows(db, user):
    fresh = session_service.create_session(db, user=user)
    stale = session_service.create_session(db, user=user)
    stale.session.expires_at = datetime.now(UTC) - timedelta(days=40)

    token = auth_token_service.issue_token(
        db, user=user, purpose=AuthTokenPurpose.PASSWORD_RESET
    )
    token.auth_token.expires_at = datetime.now(UTC) - timedelta(days=40)
    db.flush()

    assert session_service.sweep_expired_sessions(db, retain_days=30) == 1
    assert auth_token_service.sweep_expired_tokens(db, retain_days=30) == 1
    assert db.get(UserSession, fresh.session.id) is not None


def test_R8_sweeper_retains_the_window(db, user):
    """
    A revoked row is the evidence of a reuse incident and a consumed token is
    the proof a reset completed. Deleting at expiry would destroy both while
    someone is still asking about them.
    """
    recent = session_service.create_session(db, user=user)
    recent.session.expires_at = datetime.now(UTC) - timedelta(days=2)
    db.flush()

    assert session_service.sweep_expired_sessions(db, retain_days=30) == 0
    assert db.get(UserSession, recent.session.id) is not None


def test_R8_sweeping_a_successor_spares_its_ancestor(db, user):
    """replaced_by_id is ON DELETE SET NULL, not CASCADE."""
    first = session_service.create_session(db, user=user)
    second = session_service.rotate_session(
        db, refresh_token=first.plaintext_token
    )
    second.session.expires_at = datetime.now(UTC) - timedelta(days=40)
    db.flush()

    session_service.sweep_expired_sessions(db, retain_days=30)

    ancestor = db.get(UserSession, first.session.id)
    assert ancestor is not None
    db.refresh(ancestor)
    assert ancestor.replaced_by_id is None


def test_R8_the_sweep_indexes_exist(engine):
    """Without these the sweep is a sequential scan that gets slower exactly
    as the table gets big enough to need sweeping."""
    inspector = inspect(engine)
    session_indexes = {i["name"] for i in inspector.get_indexes("sessions")}
    token_indexes = {i["name"] for i in inspector.get_indexes("auth_tokens")}

    assert "ix_sessions_expires_at" in session_indexes
    assert "ix_auth_tokens_expires_at" in token_indexes


# ===========================================================================
# R9 — the gate must not break tenancy
# ===========================================================================

def test_R9_verified_users_reach_the_tenancy_layer(client, registered):
    """
    Past the gate.
    """
    token = _login(client, registered)
    response = client.get(
        f"/api/v1/organizations/{uuid.uuid4()}/invitations",  # <-- Swapped workspaces to organizations
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 404


def test_R9_unverified_users_are_stopped_before_tenancy(client, unverified):
    token = _login(client, unverified)
    response = client.get(
        f"/api/v1/organizations/{uuid.uuid4()}/invitations",  # <-- Swapped workspaces to organizations
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 403


def test_R9_the_gate_reads_current_state(client, unverified, db):
    """
    Verification is not a token claim. As one it would require signing out and
    back in before access changed.
    """
    token = _login(client, unverified)
    assert client.get(
        f"/api/v1/organizations/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 403

    unverified.email_verified_at = datetime.now(UTC)
    db.commit()

    # Same token, no re-login.
    assert client.get(
        f"/api/v1/organizations/{uuid.uuid4()}",
        headers={"Authorization": f"Bearer {token}"},
    ).status_code == 404


# ===========================================================================
# Step 10 additions — registration enumeration
# ===========================================================================

def test_registration_is_indistinguishable_for_a_taken_address(
    client, registered
):
    """
    The oracle that sat edge-to-edge with /auth/forgot-password until Step 10.
    """
    taken = client.post(
        "/api/v1/auth/register",
        json={"email": registered.email, "password": PASSWORD},
    )
    free = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"free-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
        },
    )

    assert taken.status_code == free.status_code == 202
    assert taken.json() == free.json()


def test_registration_does_not_duplicate_an_existing_account(
    client, registered, db
):
    client.post(
        "/api/v1/auth/register",
        json={"email": registered.email, "password": "a-different-password"},
    )

    assert (
        db.query(User).filter(User.email == registered.email).count() == 1
    )
    # And the existing password is untouched — otherwise the endpoint would be
    # an unauthenticated password reset.
    db.refresh(registered)
    assert security.verify_password(PASSWORD, registered.hashed_password)


def test_registration_response_carries_no_account_identifier(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"shape-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
        },
    )

    body = response.json()
    assert set(body) == {"detail"}


def test_login_does_not_distinguish_unknown_account_from_wrong_password(
    client, registered
):
    unknown = client.post(
        "/api/v1/auth/login",
        data={
            "username": f"nobody-{uuid.uuid4().hex[:8]}@example.com",
            "password": PASSWORD,
        },
    )
    wrong = client.post(
        "/api/v1/auth/login",
        data={"username": registered.email, "password": "not-the-password"},
    )

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


# ===========================================================================
# Helpers
# ===========================================================================

class _LogBuffer(logging.Handler):
    """Collects formatted log messages for the R4 assertions."""

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())

    @property
    def text(self) -> str:
        return "\n".join(self.records)


@contextmanager
def _captured_logs():
    buffer = _LogBuffer()
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(buffer)
    root.setLevel(logging.DEBUG)
    try:
        yield buffer
    finally:
        root.removeHandler(buffer)
        root.setLevel(previous_level)


def _claims() -> dict:
    now = datetime.now(UTC)
    return {
        "sub": str(uuid.uuid4()),
        "jti": str(uuid.uuid4()),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }


def _login_response(client, user):
    return client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": PASSWORD},
    )


def _login(client, user) -> str:
    return _login_response(client, user).json()["access_token"]


def _issue_reset(db, user) -> str:
    issued = auth_token_service.issue_token(
        db,
        user=user,
        purpose=RESET,
        enforce_rate_limit=False,
    )
    db.commit()
    return issued.plaintext_token