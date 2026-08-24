"""SEC-1 Gate 1 — `auth_time`, and the F6 hole it closes."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core import security
from app.core.config import settings
from app.core.security import AUTH_TIME_CLAIM, create_access_token
from app.models.user import User
from app.models.user_session import UserSession
from app.services import session_service
from app.services.billing import portal_service
from app.services.billing.portal_service import ReauthenticationRequiredError


@pytest.fixture()
def account(db) -> User:
    user = User(
        email=f"sec1-{uuid.uuid4().hex[:8]}@flowpilot.test",
        hashed_password=security.get_password_hash("a-perfectly-fine-password"),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture(autouse=True)
def reauth_enabled(monkeypatch):
    monkeypatch.setattr(settings, "BILLING_REAUTH_REQUIRED", True, raising=False)
    monkeypatch.setattr(settings, "BILLING_REAUTH_WINDOW_S", 300, raising=False)
    yield


def bearer(token: str) -> str:
    return f"Bearer {token}"


# ============================================================================
# The column and its carry-forward
# ============================================================================


class TestAuthenticatedAtCarryForward:
    def test_login_stamps_the_authentication_moment(self, db, account):
        before = datetime.now(timezone.utc) - timedelta(seconds=1)
        issued = session_service.create_session(db, user=account)
        after = datetime.now(timezone.utc) + timedelta(seconds=1)

        assert before <= issued.session.authenticated_at <= after

    def test_rotation_does_not_refresh_the_authentication_moment(self, db, account):
        issued = session_service.create_session(db, user=account)
        original_auth = issued.session.authenticated_at
        original_created = issued.session.created_at
        original_family = issued.session.family_id

        token = issued.plaintext_token
        for _ in range(5):
            rotated = session_service.rotate_session(db, refresh_token=token)
            token = rotated.plaintext_token
            db.flush()

            assert rotated.session.authenticated_at == original_auth
            assert rotated.session.family_id == original_family
            assert rotated.session.created_at >= original_created

    def test_a_new_login_starts_a_new_authentication_moment(self, db, account):
        first = session_service.create_session(db, user=account)
        db.flush()

        second = session_service.create_session(db, user=account)
        db.flush()

        assert second.family_id != first.family_id
        assert second.session.authenticated_at >= first.session.authenticated_at

    def test_column_is_not_nullable(self, db):
        column = UserSession.__table__.c.authenticated_at
        assert column.nullable is False
        assert column.server_default is not None


# ============================================================================
# The claim
# ============================================================================


class TestAuthTimeClaim:
    def test_token_carries_auth_time_when_supplied(self, account):
        moment = datetime.now(timezone.utc) - timedelta(minutes=42)
        token = create_access_token(
            subject=account.id, session_id=uuid.uuid4(), authenticated_at=moment
        )

        claims = security.decode_access_token_claims(token)
        assert claims is not None
        assert claims.auth_time is not None
        assert int(claims.auth_time.timestamp()) == int(moment.timestamp())

    def test_auth_time_is_independent_of_iat(self, account):
        long_ago = datetime.now(timezone.utc) - timedelta(days=270)
        token = create_access_token(
            subject=account.id, session_id=uuid.uuid4(), authenticated_at=long_ago
        )

        claims = security.decode_access_token_claims(token)
        assert claims is not None
        assert claims.issued_at - claims.auth_time > timedelta(days=269)

    def test_legacy_token_without_the_claim_still_decodes(self, account):
        token = create_access_token(subject=account.id, session_id=uuid.uuid4())

        claims = security.decode_access_token_claims(token)
        assert claims is not None
        assert claims.auth_time is None

    def test_unparseable_auth_time_rejects_the_whole_token(self, account):
        payload = {
            "sub": str(account.id),
            "jti": str(uuid.uuid4()),
            "iat": int(datetime.now(timezone.utc).timestamp()),
            "exp": int(
                (datetime.now(timezone.utc) + timedelta(minutes=5)).timestamp()
            ),
            "type": "access",
            AUTH_TIME_CLAIM: "not-a-timestamp",
        }
        assert security.AccessTokenClaims.from_payload(payload) is None


# ============================================================================
# F6 — the gate that was unreachable
# ============================================================================


class TestPortalReauthGate:
    def test_portal_gate_refuses_a_long_rotated_session(self, db, account):
        stale = datetime.now(timezone.utc) - timedelta(days=270)
        issued = session_service.create_session(
            db, user=account, authenticated_at=stale
        )
        db.flush()

        token = issued.plaintext_token
        for _ in range(5):
            rotated = session_service.rotate_session(db, refresh_token=token)
            token = rotated.plaintext_token
            db.flush()
            latest = rotated.session

        access_token = create_access_token(
            subject=account.id,
            session_id=latest.id,
            authenticated_at=latest.authenticated_at,
        )

        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header=bearer(access_token)
            )

    def test_fresh_login_passes_the_gate(self, db, account):
        issued = session_service.create_session(db, user=account)
        db.flush()

        access_token = create_access_token(
            subject=account.id,
            session_id=issued.session_id,
            authenticated_at=issued.session.authenticated_at,
        )

        resolved = portal_service.assert_recent_authentication(
            authorization_header=bearer(access_token)
        )
        assert resolved is not None

    def test_a_rotation_inside_the_window_still_passes(self, db, account):
        issued = session_service.create_session(db, user=account)
        db.flush()
        rotated = session_service.rotate_session(
            db, refresh_token=issued.plaintext_token
        )
        db.flush()

        access_token = create_access_token(
            subject=account.id,
            session_id=rotated.session_id,
            authenticated_at=rotated.session.authenticated_at,
        )

        assert portal_service.assert_recent_authentication(
            authorization_header=bearer(access_token)
        )

    def test_token_without_auth_time_is_refused(self, db, account):
        legacy_token = create_access_token(
            subject=account.id, session_id=uuid.uuid4()
        )

        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header=bearer(legacy_token)
            )

    def test_no_token_is_refused(self):
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(authorization_header=None)

    def test_garbage_token_is_refused(self):
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header=bearer("not.a.jwt")
            )

    def test_boundary_is_the_window_not_the_mint_time(self, db, account):
        window = int(settings.BILLING_REAUTH_WINDOW_S)

        inside = datetime.now(timezone.utc) - timedelta(seconds=window - 5)
        outside = datetime.now(timezone.utc) - timedelta(seconds=window + 5)

        ok_token = create_access_token(
            subject=account.id, session_id=uuid.uuid4(), authenticated_at=inside
        )
        assert portal_service.assert_recent_authentication(
            authorization_header=bearer(ok_token)
        )

        stale_token = create_access_token(
            subject=account.id, session_id=uuid.uuid4(), authenticated_at=outside
        )
        with pytest.raises(ReauthenticationRequiredError):
            portal_service.assert_recent_authentication(
                authorization_header=bearer(stale_token)
            )


# ============================================================================
# The choke point
# ============================================================================


class TestIssuanceChokePoint:
    def test_issue_helper_passes_the_authentication_moment(self):
        import inspect
        from app.api.v1 import auth as auth_router

        source = inspect.getsource(auth_router._issue)
        assert "authenticated_at=issued.session.authenticated_at" in source

    def test_no_other_call_site_mints_without_it(self):
        import inspect
        import re
        from app.api.v1 import auth as auth_router

        source = inspect.getsource(auth_router)
        calls = re.findall(r"create_access_token\((.*?)\)", source, re.S)
        for call in calls:
            assert "authenticated_at" in call, (
                "an access token is minted without authenticated_at; it will "
                "be refused by the F6 gate"
            )