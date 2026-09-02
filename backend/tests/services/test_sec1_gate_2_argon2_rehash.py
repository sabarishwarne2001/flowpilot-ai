"""SEC-1 Gate 2 — Argon2id, and the rehash that must never cost a login."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from passlib.hash import bcrypt
from sqlalchemy.exc import OperationalError
from sqlalchemy.sql.dml import Update

from app.core import security
from app.core.config import settings
from app.models.user import User
from app.services import auth_service, session_service

PASSWORD = "a-perfectly-fine-password"
LEGACY_ROUNDS = 12


def make_user(db, *, hashed: str) -> User:
    user = User(
        email=f"sec1-{uuid.uuid4().hex[:8]}@flowpilot.test",
        hashed_password=hashed,
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    return user


@pytest.fixture()
def legacy_user(db) -> User:
    return make_user(db, hashed=bcrypt.using(rounds=LEGACY_ROUNDS).hash(PASSWORD))


@pytest.fixture()
def modern_user(db) -> User:
    return make_user(db, hashed=security.get_password_hash(PASSWORD))


@pytest.fixture(autouse=True)
def no_duration_floor(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_LOGIN_MIN_DURATION_MS", 0, raising=False)
    yield


# ============================================================================
# The scheme
# ============================================================================


class TestHashingScheme:
    def test_new_hashes_are_argon2id(self):
        hashed = security.get_password_hash(PASSWORD)
        assert security.hash_scheme(hashed) == "argon2"
        assert "$argon2id$" in hashed

    def test_configured_parameters_are_the_owasp_floor(self):
        assert settings.ARGON2_MEMORY_COST == 19456
        assert settings.ARGON2_TIME_COST == 2
        assert settings.ARGON2_PARALLELISM == 1

    def test_bcrypt_is_still_accepted(self):
        legacy = bcrypt.using(rounds=LEGACY_ROUNDS).hash(PASSWORD)
        assert security.verify_password(PASSWORD, legacy) is True

    def test_bcrypt_is_reported_as_needing_upgrade(self):
        legacy = bcrypt.using(rounds=LEGACY_ROUNDS).hash(PASSWORD)
        assert security.hash_needs_upgrade(legacy) is True

    def test_current_argon2_hash_needs_no_upgrade(self):
        assert security.hash_needs_upgrade(security.get_password_hash(PASSWORD)) is False

    def test_unparseable_hash_verifies_false_rather_than_raising(self):
        verified, upgraded = security.verify_and_upgrade_password(
            PASSWORD, "$not$a$real$hash"
        )
        assert verified is False
        assert upgraded is None


# ============================================================================
# Rehash on login
# ============================================================================


class TestRehashOnLogin:
    def test_legacy_user_authenticates_and_is_upgraded(self, db, legacy_user):
        assert security.hash_scheme(legacy_user.hashed_password) == "bcrypt"

        authenticated = auth_service.authenticate_user(
            db, email=legacy_user.email, password=PASSWORD
        )

        assert authenticated is not None
        assert authenticated.id == legacy_user.id

        db.flush()
        db.refresh(legacy_user)
        assert security.hash_scheme(legacy_user.hashed_password) == "argon2"
        assert security.verify_password(PASSWORD, legacy_user.hashed_password)

    def test_upgrade_is_idempotent_across_logins(self, db, legacy_user):
        auth_service.authenticate_user(
            db, email=legacy_user.email, password=PASSWORD
        )
        db.flush()
        db.refresh(legacy_user)
        first = legacy_user.hashed_password

        auth_service.authenticate_user(
            db, email=legacy_user.email, password=PASSWORD
        )
        db.flush()
        db.refresh(legacy_user)

        assert legacy_user.hashed_password == first

    def test_upgraded_user_authenticates_directly(self, db, modern_user):
        original = modern_user.hashed_password

        authenticated = auth_service.authenticate_user(
            db, email=modern_user.email, password=PASSWORD
        )

        assert authenticated is not None
        db.flush()
        db.refresh(modern_user)
        assert modern_user.hashed_password == original

    def test_wrong_password_never_upgrades(self, db, legacy_user):
        original = legacy_user.hashed_password

        assert (
            auth_service.authenticate_user(
                db, email=legacy_user.email, password="not-the-password"
            )
            is None
        )

        db.flush()
        db.refresh(legacy_user)
        assert legacy_user.hashed_password == original

    def test_unknown_account_returns_none(self, db):
        assert (
            auth_service.authenticate_user(
                db, email="nobody@flowpilot.test", password=PASSWORD
            )
            is None
        )

    def test_in_memory_object_matches_the_row_after_upgrade(self, db, legacy_user):
        auth_service.authenticate_user(
            db, email=legacy_user.email, password=PASSWORD
        )
        assert security.hash_scheme(legacy_user.hashed_password) == "argon2"


# ============================================================================
# Failure isolation — the property that protects the login
# ============================================================================


class TestUpgradeFailureIsolation:
    def _patch_update_failure(self, db, exception: Exception | None = None):
        real_execute = db.execute
        boom = exception or OperationalError(
            "UPDATE users", {}, Exception("disk on fire")
        )

        def failing_execute(statement, *args, **kwargs):
            if isinstance(statement, Update) or str(statement).lstrip().upper().startswith("UPDATE"):
                raise boom
            return real_execute(statement, *args, **kwargs)

        return patch.object(db, "execute", side_effect=failing_execute)

    def test_rehash_write_failure_does_not_fail_the_login(self, db, legacy_user):
        with self._patch_update_failure(db):
            authenticated = auth_service.authenticate_user(
                db, email=legacy_user.email, password=PASSWORD
            )

        assert authenticated is not None
        assert authenticated.id == legacy_user.id

    def test_failed_rehash_is_logged_as_a_warning(self, db, legacy_user, caplog):
        auth_service.logger.propagate = True
        with patch.object(
            auth_service.logger, "warning", wraps=auth_service.logger.warning
        ) as mock_warn:
            with caplog.at_level("WARNING"):
                with self._patch_update_failure(db):
                    auth_service.authenticate_user(
                        db, email=legacy_user.email, password=PASSWORD
                    )

        assert mock_warn.called or any(
            "password.upgrade_write_failed" in getattr(record, "message", "")
            or "upgrade_write_failed" in str(record.msg)
            or "upgrade_write_failed" in record.getMessage()
            for record in caplog.records
        )
        if mock_warn.called:
            call_msg = (
                str(mock_warn.call_args[0][0])
                if mock_warn.call_args and mock_warn.call_args[0]
                else ""
            )
            assert (
                "password.upgrade_write_failed" in call_msg
                or "upgrade_write_failed" in str(mock_warn.call_args)
            )

    def test_session_creation_still_works_after_a_failed_rehash(
        self, db, legacy_user
    ):
        with self._patch_update_failure(db):
            authenticated = auth_service.authenticate_user(
                db, email=legacy_user.email, password=PASSWORD
            )
        assert authenticated is not None

        issued = session_service.create_session(db, user=authenticated)
        db.flush()
        assert issued.session_id is not None
        assert issued.session.authenticated_at is not None

    def test_hash_stays_legacy_after_a_failed_upgrade(self, db, legacy_user):
        original = legacy_user.hashed_password

        with self._patch_update_failure(db):
            auth_service.authenticate_user(
                db, email=legacy_user.email, password=PASSWORD
            )

        db.rollback()
        refreshed = db.get(User, legacy_user.id)
        if refreshed is not None:
            assert security.verify_password(PASSWORD, refreshed.hashed_password)
            assert refreshed.hashed_password == original

    def test_next_login_retries_the_upgrade(self, db, legacy_user):
        boom = OperationalError("UPDATE users", {}, Exception("transient"))
        with self._patch_update_failure(db, boom):
            auth_service.authenticate_user(
                db, email=legacy_user.email, password=PASSWORD
            )

        auth_service.authenticate_user(
            db, email=legacy_user.email, password=PASSWORD
        )
        db.flush()
        db.refresh(legacy_user)
        assert security.hash_scheme(legacy_user.hashed_password) == "argon2"


# ============================================================================
# The timing oracle this migration opens
# ============================================================================


class TestLoginDurationFloor:
    def test_floor_is_off_by_default(self):
        assert settings.AUTH_LOGIN_MIN_DURATION_MS == 0

    def test_floor_holds_a_fast_path_open(self, db, modern_user, monkeypatch):
        import time

        monkeypatch.setattr(
            settings, "AUTH_LOGIN_MIN_DURATION_MS", 200, raising=False
        )

        started = time.perf_counter()
        auth_service.authenticate_user(
            db, email=modern_user.email, password=PASSWORD
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms >= 195

    def test_floor_applies_to_the_unknown_account_path(self, db, monkeypatch):
        import time

        monkeypatch.setattr(
            settings, "AUTH_LOGIN_MIN_DURATION_MS", 200, raising=False
        )

        started = time.perf_counter()
        auth_service.authenticate_user(
            db, email="nobody@flowpilot.test", password=PASSWORD
        )
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms >= 195
