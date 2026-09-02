"""SEC-1 Gate 3 — distributed lockout and anti-enumeration."""

from __future__ import annotations

import ast
import inspect
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.core import security
from app.core.config import settings
from app.core.rate_limit.policy import (
    POLICY_LOGIN_ACCOUNT,
    POLICY_LOGIN_ACCOUNT_IP,
    LoginScopeBehaviour,
    RateLimitScope,
)
from app.models.user import User
from app.services import login_backoff_service as backoff

PASSWORD = "a-perfectly-fine-password"
LOGIN_URL = "/api/v1/auth/login"


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch):
    monkeypatch.setattr(settings, "LOGIN_BACKOFF_ENABLED", True, raising=False)
    store = backoff._MemoryStore()
    previous = backoff.set_store(store)
    yield store
    backoff.set_store(previous)


@pytest.fixture()
def account(db) -> User:
    user = User(
        email=f"sec1-lock-{uuid.uuid4().hex[:8]}@flowpilot.test",
        hashed_password=security.get_password_hash(PASSWORD),
        is_active=True,
        email_verified_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.flush()
    db.commit()
    return user


# ============================================================================
# Policy shape
# ============================================================================


class TestPolicyShape:
    def test_account_scope_exists(self):
        assert RateLimitScope.LOGIN_ACCOUNT.value == "login_account"
        assert RateLimitScope.LOGIN_ACCOUNT_IP.value == "login_account_ip"

    def test_account_scope_never_refuses(self):
        assert POLICY_LOGIN_ACCOUNT.behaviour is LoginScopeBehaviour.DELAY

    def test_pair_scope_refuses(self):
        assert POLICY_LOGIN_ACCOUNT_IP.behaviour is LoginScopeBehaviour.REFUSE

    def test_delay_ceiling_cannot_exhaust_the_threadpool(self):
        assert POLICY_LOGIN_ACCOUNT.ladder_ceiling <= 5000


# ============================================================================
# Coverage — the gap this tranche closes
# ============================================================================


class TestDistributedAttack:
    def test_distributed_attack_is_throttled_by_the_account_scope(self):
        email = "victim@flowpilot.test"

        for n in range(POLICY_LOGIN_ACCOUNT.threshold + 4):
            backoff.record_login_failure(f"203.0.113.{n}", email)

        fresh_ip_status = backoff.check_login_backoff("198.51.100.7", email)
        assert fresh_ip_status.is_backed_off is False
        assert fresh_ip_status.delay_ms > 0

    def test_pair_scope_still_stops_a_single_grinder(self):
        email = "victim@flowpilot.test"
        ip = "203.0.113.9"

        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 2):
            backoff.record_login_failure(ip, email)

        status = backoff.check_login_backoff(ip, email)
        assert status.is_backed_off is True
        assert status.retry_after_seconds > 0

    def test_a_refused_pair_does_not_refuse_other_addresses(self):
        email = "victim@flowpilot.test"
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 2):
            backoff.record_login_failure("203.0.113.9", email)

        assert (
            backoff.check_login_backoff("198.51.100.20", email).is_backed_off is False
        )

    def test_account_scope_never_refuses_however_many_failures(self):
        email = "victim@flowpilot.test"
        for n in range(200):
            backoff.record_login_failure(f"203.0.113.{n % 250}", email)

        assert backoff.check_login_backoff("198.51.100.1", email).is_backed_off is False

    def test_success_clears_both_scopes(self):
        email = "victim@flowpilot.test"
        ip = "203.0.113.9"
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 2):
            backoff.record_login_failure(ip, email)

        backoff.clear_login_backoff(ip, email)

        status = backoff.check_login_backoff(ip, email)
        assert status.is_backed_off is False
        assert status.delay_ms == 0


# ============================================================================
# Anti-enumeration
# ============================================================================


class TestAntiEnumeration:
    def test_ladder_climbs_for_identifiers_that_do_not_exist(self):
        unknown = f"nobody-{uuid.uuid4().hex[:8]}@flowpilot.test"

        for n in range(POLICY_LOGIN_ACCOUNT.threshold + 4):
            backoff.record_login_failure(f"203.0.113.{n}", unknown)

        assert backoff.check_login_backoff("198.51.100.7", unknown).delay_ms > 0

    def test_known_and_unknown_identifiers_ladder_identically(self, account):
        known = account.email
        unknown = f"nobody-{uuid.uuid4().hex[:8]}@flowpilot.test"

        for n in range(POLICY_LOGIN_ACCOUNT.threshold + 3):
            backoff.record_login_failure(f"203.0.113.{n}", known)
            backoff.record_login_failure(f"203.0.113.{n}", unknown)

        known_status = backoff.check_login_backoff("198.51.100.1", known)
        unknown_status = backoff.check_login_backoff("198.51.100.1", unknown)

        assert abs(known_status.delay_ms - unknown_status.delay_ms) <= 50
        assert known_status.is_backed_off == unknown_status.is_backed_off

    def test_counter_keys_do_not_contain_the_address(self, isolated_store):
        email = "someone@flowpilot.test"
        backoff.record_login_failure("203.0.113.1", email)

        keys = list(isolated_store._data.keys())
        assert keys
        for key in keys:
            assert "someone" not in key
            assert "flowpilot.test" not in key

    def test_redis_outage_does_not_refuse_every_login(self, monkeypatch):
        backoff.set_store(None)
        monkeypatch.setattr(
            "app.services.login_backoff_service.get_redis_client", lambda: None
        )

        status = backoff.check_login_backoff("203.0.113.1", "someone@flowpilot.test")
        assert status.is_backed_off is False


# ============================================================================
# The endpoint's answer
# ============================================================================


class TestUnifiedFailureResponse:
    def _login(self, client, email: str, password: str):
        return client.post(
            LOGIN_URL, data={"username": email, "password": password}
        )

    def test_wrong_password_and_unknown_account_are_indistinguishable(
        self, client, account
    ):
        wrong = self._login(client, account.email, "not-the-password")
        unknown = self._login(
            client, f"nobody-{uuid.uuid4().hex[:8]}@flowpilot.test", PASSWORD
        )

        assert wrong.status_code == unknown.status_code == 401
        assert wrong.json() == unknown.json()
        assert set(wrong.headers) - {"date", "content-length"} == (
            set(unknown.headers) - {"date", "content-length"}
        )

    def test_inactive_account_is_indistinguishable_from_a_bad_password(
        self, client, db, account
    ):
        account.is_active = False
        db.flush()
        db.commit()

        inactive = self._login(client, account.email, PASSWORD)
        wrong = self._login(client, account.email, "not-the-password")

        assert inactive.status_code == 401
        assert inactive.json() == wrong.json()

    def test_no_retry_after_header_on_a_failed_login(self, client, account):
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 2):
            response = self._login(client, account.email, "not-the-password")

        assert response.status_code == 401
        assert "retry-after" not in {k.lower() for k in response.headers}

    def test_backed_off_pair_returns_the_same_401(self, client, account):
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 3):
            self._login(client, account.email, "not-the-password")

        refused = self._login(client, account.email, PASSWORD)
        assert refused.status_code == 401

    def test_successful_login_still_works(self, client, account):
        response = self._login(client, account.email, PASSWORD)
        assert response.status_code == 200
        assert response.json()["token_type"] == "bearer"


# ============================================================================
# Recovery must never be locked
# ============================================================================


class TestRecoveryPathExemption:
    def test_backoff_is_not_consulted_by_the_reset_path(self):
        """Locking recovery locks the way out of the lock.

        Asserted structurally via AST: an attacker who can throttle password reset
        can keep a compromised account compromised, so the exemption is verified
        by inspecting all function definitions in the router.
        """
        from app.api.v1 import auth as auth_router

        source = inspect.getsource(auth_router)
        tree = ast.parse(source)

        guarded: set[str] = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for inner in ast.walk(node):
                if (
                    isinstance(inner, ast.Call)
                    and isinstance(inner.func, ast.Name)
                    and inner.func.id == "check_login_backoff"
                ):
                    guarded.add(node.name)

        assert "login" in guarded
        assert guarded == {"login"}, (
            f"check_login_backoff is called from {guarded - {'login'}}; "
            "recovery endpoints must not be throttled by login lockout"
        )

    def test_reset_password_endpoint_is_reachable_while_backed_off(
        self, client, account
    ):
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold + 3):
            client.post(
                LOGIN_URL,
                data={"username": account.email, "password": "not-the-password"},
            )

        response = client.post(
            "/api/v1/auth/forgot-password", json={"email": account.email}
        )
        assert response.status_code != 429


# ============================================================================
# The ladder
# ============================================================================


class TestLadder:
    def test_ladder_is_flat_below_the_threshold(self):
        email = "quiet@flowpilot.test"
        for _ in range(POLICY_LOGIN_ACCOUNT_IP.threshold - 1):
            backoff.record_login_failure("203.0.113.1", email)

        assert backoff.check_login_backoff("203.0.113.1", email).is_backed_off is False

    def test_ladder_is_capped(self):
        email = "ground@flowpilot.test"
        for n in range(300):
            backoff.record_login_failure(f"203.0.113.{n % 250}", email)

        status = backoff.check_login_backoff("198.51.100.1", email)
        assert status.delay_ms <= POLICY_LOGIN_ACCOUNT.ladder_ceiling

    def test_apply_delay_respects_the_ceiling(self):
        started = time.perf_counter()
        backoff.apply_delay(10_000_000)
        elapsed_ms = (time.perf_counter() - started) * 1000

        assert elapsed_ms <= POLICY_LOGIN_ACCOUNT.ladder_ceiling + 250
