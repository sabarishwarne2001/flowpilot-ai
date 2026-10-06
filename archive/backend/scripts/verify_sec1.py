#!/usr/bin/env python3
"""SEC-1 — the release gate.

    python scripts/verify_sec1.py            # static + database
    python scripts/verify_sec1.py --static   # no database needed
"""

from __future__ import annotations

import argparse
import ast
import os
import sys
from pathlib import Path
from typing import Iterable, Optional

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BACKEND = Path(__file__).resolve().parents[1]
APP = BACKEND / "app"

FAILURES: list[str] = []
CHECKS_RUN: list[str] = []


def fail(check: str, message: str) -> None:
    FAILURES.append(f"[{check}] {message}")


def ok(check: str) -> None:
    CHECKS_RUN.append(check)


def python_files(root: Path) -> Iterable[Path]:
    for path in root.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        yield path


def parse(path: Path) -> Optional[ast.Module]:
    try:
        return ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except (SyntaxError, UnicodeDecodeError):
        return None


def source(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


# ===========================================================================
# Static checks
# ===========================================================================


def check_no_iat_freshness_reads() -> None:
    check = "no_iat_freshness_reads"
    sensitive = [
        APP / "services" / "billing" / "portal_service.py",
    ]

    for path in sensitive:
        if not path.exists():
            continue
        text = source(path)
        if "claims.issued_at" in text or "payload['iat']" in text:
            fail(
                check,
                f"{path.relative_to(BACKEND)} reads `iat`. Freshness must come "
                "from `auth_time`; `iat` is refreshed by every rotation and a "
                "window measured against it can never be exceeded.",
            )
        if "claims.auth_time" not in text:
            fail(
                check,
                f"{path.relative_to(BACKEND)} no longer reads `auth_time`.",
            )

    ok(check)


def check_auth_time_is_not_defaulted_from_iat() -> None:
    check = "auth_time_fails_closed"
    path = APP / "services" / "billing" / "portal_service.py"
    if not path.exists():
        ok(check)
        return

    text = source(path)
    for pattern in (
        "auth_time or claims.issued_at",
        "claims.auth_time or ",
        "getattr(claims, \"auth_time\", claims.issued_at)",
    ):
        if pattern in text:
            fail(
                check,
                f"a fallback from auth_time to iat was reintroduced ({pattern!r}). "
                "Legacy tokens are exactly what an attacker with a stolen "
                "pre-SEC-1 refresh token can produce.",
            )
    ok(check)


def check_login_has_one_failure_shape() -> None:
    check = "login_single_failure_shape"
    path = APP / "api" / "v1" / "auth.py"
    text = source(path)

    try:
        start = text.index("async def login(")
        end = text.index("@router.post", start)
    except ValueError:
        fail(check, "could not locate the login handler")
        ok(check)
        return

    body = text[start:end]

    if "HTTP_429" in body:
        fail(
            check,
            "login answers 429 on a failure path. A rate-limit status "
            "distinguishes a throttled attempt from a wrong password.",
        )
    if '"Retry-After"' in body or "'Retry-After'" in body:
        fail(
            check,
            "login sets a Retry-After header on a failure. Its presence "
            "reports the state of a counter keyed to the identifier being "
            "probed.",
        )
    if "inactive" in body.lower() and "HTTP_400" in body:
        fail(
            check,
            "login answers 400 for an inactive account, which confirms the "
            "address is registered.",
        )

    refusals = body.count("_login_refused()")
    if refusals < 2:
        fail(
            check,
            f"only {refusals} failure path(s) route through _login_refused(); "
            "every refusal must share one shape.",
        )
    ok(check)


def check_recovery_path_is_not_throttled_by_lockout() -> None:
    check = "recovery_exempt_from_lockout"
    path = APP / "api" / "v1" / "auth.py"
    text = source(path)

    try:
        start = text.index("async def login(")
        end = text.index("@router.post", start)
    except ValueError:
        fail(check, "could not locate the login handler")
        ok(check)
        return

    tree = parse(path)
    if tree is None:
        fail(check, "could not parse the auth router")
        ok(check)
        return

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

    stray = guarded - {"login"}
    if stray:
        fail(
            check,
            f"check_login_backoff is called from {sorted(stray)} as well as "
            "login. An attacker who can throttle password reset keeps a "
            "compromised account compromised.",
        )
    if "login" not in guarded:
        fail(check, "login does not consult the backoff at all")
    ok(check)


def check_account_scope_never_refuses() -> None:
    check = "account_scope_delays_only"
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

    try:
        from app.core.rate_limit.policy import (
            POLICY_LOGIN_ACCOUNT,
            POLICY_LOGIN_ACCOUNT_IP,
            LoginScopeBehaviour,
        )
    except ImportError as exc:
        fail(check, f"login guard policies are missing: {exc}")
        ok(check)
        return

    if POLICY_LOGIN_ACCOUNT.behaviour is not LoginScopeBehaviour.DELAY:
        fail(
            check,
            "POLICY_LOGIN_ACCOUNT refuses. Anyone who knows an address could "
            "then disable it by failing logins.",
        )
    if POLICY_LOGIN_ACCOUNT_IP.behaviour is not LoginScopeBehaviour.REFUSE:
        fail(check, "POLICY_LOGIN_ACCOUNT_IP no longer refuses.")
    if POLICY_LOGIN_ACCOUNT.ladder_ceiling > 5000:
        fail(
            check,
            f"the account delay ceiling is {POLICY_LOGIN_ACCOUNT.ladder_ceiling}ms; "
            "a long ladder is a lever on the request threadpool.",
        )
    ok(check)


def check_failures_counted_for_unknown_identifiers() -> None:
    check = "ladder_ignores_account_existence"
    path = APP / "api" / "v1" / "auth.py"
    text = source(path)

    try:
        start = text.index("async def login(")
        end = text.index("@router.post", start)
    except ValueError:
        ok(check)
        return

    body = text[start:end]
    if "record_login_failure" not in body:
        fail(check, "login never records a failure")
    if body.count("record_login_failure") < 2:
        fail(
            check,
            "record_login_failure is called on fewer than two failure paths; "
            "an unrecorded path is one an attacker can probe for free.",
        )
    ok(check)


def check_argon2_is_the_default_scheme() -> None:
    check = "argon2_scheme_priority"
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

    try:
        from app.core.security import pwd_context
    except ImportError as exc:
        fail(check, f"could not import the password context: {exc}")
        ok(check)
        return

    schemes = list(pwd_context.schemes())
    if not schemes or schemes[0] != "argon2":
        fail(
            check,
            f"scheme order is {schemes}; argon2 must be first or new hashes "
            "silently revert to bcrypt.",
        )
    if "bcrypt" not in schemes:
        fail(
            check,
            "bcrypt was removed. Rehashing only happens during a successful "
            "login, so dormant accounts still hold bcrypt hashes and removing "
            "it locks them out permanently.",
        )
    ok(check)


def check_argon2_parameters_are_within_bounds() -> None:
    check = "argon2_parameter_bounds"
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

    from app.core.config import settings

    memory = settings.ARGON2_MEMORY_COST
    if memory < 19456:
        fail(
            check,
            f"ARGON2_MEMORY_COST={memory} is below the OWASP floor of 19456 KiB.",
        )
    if memory > 131072:
        fail(
            check,
            f"ARGON2_MEMORY_COST={memory} KiB is {memory * 20 / 1024:.0f} MB at "
            "20 concurrent logins and will OOM a 1-2 GB container.",
        )
    if settings.ARGON2_TIME_COST < 2:
        fail(check, f"ARGON2_TIME_COST={settings.ARGON2_TIME_COST} is below 2.")
    if settings.ARGON2_PARALLELISM > 4:
        fail(
            check,
            f"ARGON2_PARALLELISM={settings.ARGON2_PARALLELISM} exceeds the "
            "vCPU allocation of an API container.",
        )
    ok(check)


def check_rehash_is_savepoint_isolated() -> None:
    check = "rehash_savepoint_isolated"
    path = APP / "services" / "auth_service.py"
    text = source(path)

    if "begin_nested" not in text:
        fail(
            check,
            "the password upgrade is not SAVEPOINT-isolated. A bare try/except "
            "leaves the transaction aborted, so creating the login session "
            "fails afterwards with an error naming the wrong statement.",
        )
    ok(check)


def check_no_plaintext_identifier_in_counter_keys() -> None:
    check = "counter_keys_are_hashed"
    path = APP / "services" / "login_backoff_service.py"
    text = source(path)

    if "_hmac" not in text:
        fail(check, "counter keys are not HMAC-derived")
    if 'f"bo:v2:{policy.name}:{email' in text:
        fail(check, "an email address is interpolated directly into a key")
    ok(check)


def check_session_carries_authentication_moment() -> None:
    check = "session_authenticated_at"
    sys.path.insert(0, str(BACKEND))
    os.environ.setdefault("ENVIRONMENT", "test")
    os.environ.setdefault("JWT_SECRET_KEY", "x" * 64)

    from app.models.user_session import UserSession

    column = UserSession.__table__.c.get("authenticated_at")
    if column is None:
        fail(check, "user session model has no authenticated_at column")
        ok(check)
        return
    if column.nullable:
        fail(check, "authenticated_at is nullable; a missing moment reads as absent")

    rotation = source(APP / "services" / "session_service.py")
    if "authenticated_at=session.authenticated_at" not in rotation:
        fail(
            check,
            "rotation does not carry authenticated_at forward. Re-stamping it "
            "on refresh restores the F6 hole exactly.",
        )
    ok(check)


def check_migration_chain_has_one_head() -> None:
    check = "migration_chain_single_head"
    sys.path.insert(0, str(BACKEND))
    try:
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        script = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
        heads = script.get_heads()
    except Exception as exc:  # noqa: BLE001
        fail(check, f"could not resolve the migration chain: {exc}")
        ok(check)
        return

    if len(heads) != 1:
        fail(check, f"{len(heads)} heads: {heads}")
    ok(check)


# ===========================================================================
# Database invariants
# ===========================================================================


def check_database() -> None:
    check = "database_invariants"
    sys.path.insert(0, str(BACKEND))

    try:
        from sqlalchemy import text as sql

        from app.db.session import SessionLocal
        from app.models.user_session import UserSession
    except ImportError as exc:
        fail(check, f"could not import the database layer: {exc}")
        ok(check)
        return

    table = UserSession.__tablename__

    with SessionLocal() as db:
        column = db.execute(
            sql(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = 'authenticated_at'"
            ),
            {"t": table},
        ).scalar_one_or_none()

        if column is None:
            fail(check, f"{table}.authenticated_at does not exist")
        elif column != "NO":
            fail(check, f"{table}.authenticated_at is nullable")

        orphans = db.execute(
            sql(f"SELECT count(*) FROM {table} WHERE authenticated_at IS NULL")
        ).scalar_one()
        if orphans:
            fail(check, f"{orphans} session rows have no authentication moment")

        diverged = db.execute(
            sql(
                f"SELECT count(*) FROM (SELECT family_id FROM {table} "
                "GROUP BY family_id "
                "HAVING count(DISTINCT authenticated_at) > 1) AS d"
            )
        ).scalar_one()
        if diverged:
            fail(
                check,
                f"{diverged} session families carry more than one "
                "authenticated_at; rotation is re-stamping it somewhere.",
            )

        legacy = db.execute(
            sql("SELECT count(*) FROM users WHERE hashed_password LIKE '$2%'")
        ).scalar_one()
        total = db.execute(sql("SELECT count(*) FROM users")).scalar_one()
        if total:
            print(
                f"  note: {legacy}/{total} users still hold bcrypt hashes "
                f"({legacy / total * 100:.1f}%). Not a failure — the migration "
                "is asymptotic and completes only as users return."
            )

    ok(check)


# ===========================================================================
# Entry point
# ===========================================================================

STATIC_CHECKS = (
    check_no_iat_freshness_reads,
    check_auth_time_is_not_defaulted_from_iat,
    check_login_has_one_failure_shape,
    check_recovery_path_is_not_throttled_by_lockout,
    check_account_scope_never_refuses,
    check_failures_counted_for_unknown_identifiers,
    check_argon2_is_the_default_scheme,
    check_argon2_parameters_are_within_bounds,
    check_rehash_is_savepoint_isolated,
    check_no_plaintext_identifier_in_counter_keys,
    check_session_carries_authentication_moment,
    check_migration_chain_has_one_head,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="SEC-1 release gate")
    parser.add_argument(
        "--static",
        action="store_true",
        help="skip the database invariants",
    )
    args = parser.parse_args(argv)

    for check in STATIC_CHECKS:
        check()

    if not args.static:
        check_database()

    print(f"SEC-1 gate: {len(CHECKS_RUN)} checks run")
    if FAILURES:
        print(f"  {len(FAILURES)} FAILED")
        for failure in FAILURES:
            print(f"    {failure}")
        return 1

    print("  all checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
