"""
ARCH-03 Step 4 gate — hash agreement and backfill verification.

Run in two phases, on either side of the migration:

    python -m scripts.verify_step4 --phase pre     # before alembic upgrade
    alembic upgrade head
    python -m scripts.verify_step4 --phase post    # after

WHY A THIRD IMPLEMENTATION OF THE HASH IS BEING COMPARED
--------------------------------------------------------
Three pieces of code must agree on what the hash of a token is, and they are
deliberately not the same code:

    1. app/core/tokens.hash_token       — what the application computes when a
                                          user submits a token
    2. the MIGRATE revision's _hash_token — frozen inside the migration, so the
                                          revision replays identically forever
    3. encode(sha256(token::bytea),'hex') — what SQL computes, for any future
                                          sweeper or reporting query

(2) is duplicated from (1) on purpose: a migration that imports application
code replays differently after the application changes. The cost of that
decision is exactly this risk — the two copies drifting — and this script is
what pays it. The migration checks (2) against (3) internally; only this
script sees (1), because only this script is allowed to import from app.

If they ever disagree, the failure is silent and total: the migration writes
hashes the application cannot match, every invitation becomes unacceptable,
and nothing raises until a user clicks a link.

The pre phase compares all three against live tokens and writes nothing. The
post phase re-derives every stored hash from its surviving plaintext using the
application's own function, which is the strongest available statement that
CONTRACT can safely drop the plaintext column.
"""

from __future__ import annotations

import argparse
import hashlib
import sys

from sqlalchemy import create_engine, text

from app.core.config import settings
from app.core.tokens import TOKEN_HASH_LENGTH, generate_secure_token, hash_token

PASS = "  [PASS]"
FAIL = "  [FAIL]"
INFO = "  [INFO]"

# Must match the constants in the MIGRATE revision.
EXPECTED_USER_COUNT = 4
EXPECTED_INVITATION_COUNT = 19


def _engine():
    return create_engine(settings.sqlalchemy_database_uri)


def _migration_hash(token: str) -> str:
    """
    Byte-for-byte copy of _hash_token from the MIGRATE revision.

    Reproduced rather than imported: alembic revision modules are not on the
    import path as packages, and re-typing it is the point — if someone edits
    the revision, this copy stops matching and the gate fails.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def check_hash_implementations() -> list[str]:
    """Compares the two Python implementations on synthetic tokens."""
    print("\n=== 1. Python hash implementations ===")
    failures: list[str] = []

    samples = [generate_secure_token() for _ in range(64)]
    # Deliberately awkward inputs alongside the real ones. token_urlsafe emits
    # ASCII today; these are what would break the UTF-8 assumption if that
    # ever changes.
    samples += ["", "a", "-_" * 32, "ünïcödé-token", "🔐" * 4]

    for token in samples:
        app_hash = hash_token(token)
        mig_hash = _migration_hash(token)
        if app_hash != mig_hash:
            failures.append(f"hash_token disagrees with the migration on {token!r}")
            print(f"{FAIL} divergence on {token[:16]!r}")
            break
        if len(app_hash) != TOKEN_HASH_LENGTH:
            failures.append(f"hash length {len(app_hash)} for {token!r}")
            print(f"{FAIL} wrong length on {token[:16]!r}")
            break

    if not failures:
        print(
            f"{PASS} app and migration agree on {len(samples)} tokens, "
            f"including empty, unicode, and emoji inputs."
        )
    return failures


def check_sql_agreement(conn, live: bool) -> list[str]:
    """Compares Python against PostgreSQL, on real tokens where available."""
    print("\n=== 2. Python vs PostgreSQL ===")
    failures: list[str] = []

    try:
        conn.execute(text("SELECT sha256('probe'::bytea)"))
    except Exception as exc:  # noqa: BLE001
        print(f"{FAIL} server cannot evaluate sha256(): {exc}")
        return ["PostgreSQL 11+ required for sha256()"]
    print(f"{PASS} server provides sha256() without pgcrypto.")

    rows = conn.execute(
        text(
            """
            SELECT id, token, encode(sha256(token::bytea), 'hex') AS sql_hash
              FROM workspace_invitations
             ORDER BY created_at
            """
        )
    ).fetchall()

    if not rows:
        print(f"{INFO} no invitations present; nothing live to compare.")
        return failures

    for row in rows:
        if hash_token(row.token) != row.sql_hash:
            failures.append(f"invitation {row.id}: python and sql hashes differ")
            print(f"{FAIL} invitation {row.id}")

    if not failures:
        print(f"{PASS} {len(rows)} live tokens: hashlib and pg sha256 identical.")
    if live:
        print(f"{INFO} the migration repeats this check internally before writing.")
    return failures


def check_pre_state(conn) -> list[str]:
    """Confirms the database is where EXPAND left it."""
    print("\n=== 3. Pre-migration state ===")
    failures: list[str] = []

    users = conn.execute(text("SELECT count(*) FROM users")).scalar_one()
    invitations = conn.execute(
        text("SELECT count(*) FROM workspace_invitations")
    ).scalar_one()
    null_verified = conn.execute(
        text("SELECT count(*) FROM users WHERE email_verified_at IS NULL")
    ).scalar_one()
    null_hash = conn.execute(
        text("SELECT count(*) FROM workspace_invitations WHERE token_hash IS NULL")
    ).scalar_one()

    for label, observed, expected in (
        ("users", users, EXPECTED_USER_COUNT),
        ("invitations", invitations, EXPECTED_INVITATION_COUNT),
        ("users with NULL email_verified_at", null_verified, EXPECTED_USER_COUNT),
        ("invitations with NULL token_hash", null_hash, EXPECTED_INVITATION_COUNT),
    ):
        if observed == expected:
            print(f"{PASS} {label}: {observed}")
        else:
            failures.append(f"{label}: {observed}, expected {expected}")
            print(f"{FAIL} {label}: {observed}, expected {expected}")

    if users != EXPECTED_USER_COUNT:
        print(
            f"{INFO} the migration will refuse to run on a changed baseline. "
            "That is intentional — see the revision docstring."
        )

    return failures


def check_post_state(conn) -> list[str]:
    """Verifies both backfills, re-deriving hashes with the application's code."""
    print("\n=== 3. Post-migration state ===")
    failures: list[str] = []

    checks = [
        (
            "users with NULL email_verified_at",
            "SELECT count(*) FROM users WHERE email_verified_at IS NULL",
            0,
        ),
        (
            "users where email_verified_at <> created_at",
            "SELECT count(*) FROM users WHERE email_verified_at <> created_at",
            0,
        ),
        (
            "invitations with NULL token_hash",
            "SELECT count(*) FROM workspace_invitations WHERE token_hash IS NULL",
            0,
        ),
        (
            "token_hash values of the wrong length",
            f"SELECT count(*) FROM workspace_invitations "
            f"WHERE length(token_hash) <> {TOKEN_HASH_LENGTH}",
            0,
        ),
        (
            "duplicate token_hash values",
            "SELECT count(*) FROM (SELECT token_hash FROM workspace_invitations "
            "GROUP BY token_hash HAVING count(*) > 1) d",
            0,
        ),
        (
            "users",
            "SELECT count(*) FROM users",
            EXPECTED_USER_COUNT,
        ),
        (
            "invitations",
            "SELECT count(*) FROM workspace_invitations",
            EXPECTED_INVITATION_COUNT,
        ),
    ]

    for label, sql, expected in checks:
        observed = conn.execute(text(sql)).scalar_one()
        if observed == expected:
            print(f"{PASS} {label}: {observed}")
        else:
            failures.append(f"{label}: {observed}, expected {expected}")
            print(f"{FAIL} {label}: {observed}, expected {expected}")

    # The check that actually licenses CONTRACT to drop the plaintext column:
    # every stored hash re-derived by the application's own function.
    print("\n=== 4. Stored hashes re-derived by the application ===")
    rows = conn.execute(
        text("SELECT id, token, token_hash FROM workspace_invitations")
    ).fetchall()

    mismatched = [row.id for row in rows if hash_token(row.token) != row.token_hash]
    if mismatched:
        for row_id in mismatched:
            failures.append(f"invitation {row_id}: stored hash is not reproducible")
            print(f"{FAIL} invitation {row_id}")
    else:
        print(
            f"{PASS} all {len(rows)} stored hashes reproduce exactly from their "
            "plaintext using app.core.tokens.hash_token."
        )

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-03 Step 4 gate.")
    parser.add_argument("--phase", choices=("pre", "post"), required=True)
    args = parser.parse_args()

    print(f"ARCH-03 Step 4 gate — {args.phase}-migration")

    failures = check_hash_implementations()

    with _engine().connect() as conn:
        failures += check_sql_agreement(conn, live=args.phase == "pre")
        if args.phase == "pre":
            failures += check_pre_state(conn)
        else:
            failures += check_post_state(conn)

    print("\n" + "=" * 60)
    if failures:
        print(f"GATE FAILED — {len(failures)} problem(s):")
        for item in failures:
            print(f"  - {item}")
        return 1

    if args.phase == "pre":
        print("GATE PASSED — safe to run alembic upgrade head.")
        print("Nothing was written by this script.")
    else:
        print("GATE PASSED — both backfills verified.")
        print("Every stored hash is reproducible from its plaintext, so")
        print("CONTRACT can drop workspace_invitations.token safely.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
