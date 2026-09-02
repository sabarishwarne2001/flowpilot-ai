"""ARCH-03 MIGRATE — grandfather verification, hash invitation tokens

Two backfills, both logged per row, both reversible.

  1. users.email_verified_at = users.created_at            (§B.4)
  2. workspace_invitations.token_hash = sha256(token)      (§B.3)

Neither source column is touched. created_at and token both survive this
revision intact, which is what makes downgrade() a genuine reversal rather
than a best effort: blanking the two target columns returns the database to
exactly the state EXPAND left it in.

WHY THE ROW COUNTS ARE ASSERTED EXACTLY, NOT AS A MINIMUM
---------------------------------------------------------
Registration does not yet write email_verified_at — that arrives in Step 8.
So every user row is NULL, and this revision grandfathers every user it finds.
The §B.4 decision was to grandfather the four accounts named in the Step 0
audit, not to grandfather whoever happens to exist when the migration runs. An
account registered between EXPAND and MIGRATE would be silently marked as
having proved control of an address it never proved.

The exact-count assertion is the only thing standing between that decision and
that outcome. If it fails, the baseline moved and the audit no longer describes
the database; stop and find out why before adjusting the constants.

Set ARCH03_ALLOW_BASELINE_DRIFT=1 to downgrade the mismatch to a warning. That
is for the case where you have confirmed the delta is legitimate, not for the
case where you want the migration to stop complaining.

HASH AGREEMENT
--------------
The hash is computed in Python, with hashlib, over the UTF-8 bytes of the
token — the same computation app/core/tokens.hash_token performs at
verification time. Computing it here in SQL instead would introduce a second
implementation of the one value that must match, and a token whose stored hash
disagrees with the application's hash is an invitation that can never be
accepted.

Postgres is then asked for its own answer, encode(sha256(token::bytea), 'hex'),
and the two are compared on every row before anything is written. That check is
not redundant: it is what licenses a future sweeper or reporting query to
compute the hash in SQL and get the same value. If it ever fails, the two
implementations have diverged and this migration must not run.

The hashing is inlined rather than imported from app.core.tokens. A migration
that calls into application code replays differently after the application
changes, and a migration that replays differently is not a migration.

Revision ID: b7d4e0f81a63
Revises: a1c7f39b4e2d
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d4e0f81a63"
down_revision: Union[str, None] = "a1c7f39b4e2d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


logger = logging.getLogger("alembic.arch03.migrate")


# ===========================================================================
# Baseline recorded by the ARCH-03 Step 0 pre-flight audit
# ===========================================================================

EXPECTED_USER_COUNT = 4
EXPECTED_INVITATION_COUNT = 19

# SHA-256 hex is always 64 characters. The column is String(64) and CONTRACT
# makes it NOT NULL; a short value here would pass the migration and fail an
# equality comparison at verification time, which is a much worse place to
# discover it.
SHA256_HEX_LENGTH = 64


def _hash_token(token: str) -> str:
    """
    SHA-256 of a token's UTF-8 bytes, hex encoded.

    Mirrors app/core/tokens.hash_token exactly and is deliberately duplicated
    here so this revision is frozen against future changes to that module.
    Not bcrypt: the input is a 256-bit random secret from
    secrets.token_urlsafe, not a password. It is not guessable, so a slow KDF
    buys nothing and costs latency on every verification click.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _drift_allowed() -> bool:
    return os.environ.get("ARCH03_ALLOW_BASELINE_DRIFT", "").strip() == "1"


def _check_count(label: str, observed: int, expected: int) -> None:
    if observed == expected:
        logger.info("ARCH-03 baseline %-14s %d rows, as audited.", label, observed)
        return

    message = (
        f"ARCH-03 baseline mismatch: {label} has {observed} rows, the Step 0 "
        f"audit recorded {expected}. The database has changed since the audit. "
        f"Confirm the delta is legitimate, update the constant in this "
        f"revision, and re-run. To proceed anyway set "
        f"ARCH03_ALLOW_BASELINE_DRIFT=1."
    )

    if _drift_allowed():
        logger.warning("%s (overridden)", message)
        return

    raise RuntimeError(message)


# ===========================================================================
# Pre-flight
# ===========================================================================

def _assert_sha256_available(bind: sa.engine.Connection) -> None:
    """
    Confirms the server can compute SHA-256 without pgcrypto.

    sha256() is built in from PostgreSQL 11. On an older server the agreement
    check below would fail with an undefined-function error that reads like a
    missing extension rather than an unsupported version.
    """
    try:
        bind.execute(sa.text("SELECT sha256('probe'::bytea)"))
    except Exception as exc:  # noqa: BLE001 — re-raised with context
        raise RuntimeError(
            "This server cannot evaluate sha256(). PostgreSQL 11 or later is "
            "required for the hash agreement check in this revision. "
            f"Underlying error: {exc}"
        ) from exc


def _assert_hash_agreement(bind: sa.engine.Connection) -> None:
    """
    Compares Python's hash against Postgres's, on every real token, before any
    write occurs.

    Checked against live tokens rather than a synthetic string because the
    thing being tested is the encoding boundary: secrets.token_urlsafe emits
    ASCII, so text::bytea and str.encode("utf-8") agree — but that is a
    property of the input, not a guarantee of the cast, and the input is what
    would change if token generation is ever revisited.
    """
    rows = bind.execute(
        sa.text(
            """
            SELECT id,
                   token,
                   encode(sha256(token::bytea), 'hex') AS sql_hash
              FROM workspace_invitations
             ORDER BY created_at
            """
        )
    ).fetchall()

    if not rows:
        logger.warning(
            "ARCH-03 hash agreement: no invitations present, nothing compared."
        )
        return

    mismatches: list[str] = []
    for row in rows:
        python_hash = _hash_token(row.token)

        if len(python_hash) != SHA256_HEX_LENGTH:
            mismatches.append(
                f"{row.id}: python hash is {len(python_hash)} chars, "
                f"expected {SHA256_HEX_LENGTH}"
            )
            continue

        if python_hash != row.sql_hash:
            # Neither hash is logged in full. They are derived from live
            # secrets, and a migration log is not a secret store.
            mismatches.append(
                f"{row.id}: python {python_hash[:12]}… != sql {row.sql_hash[:12]}…"
            )

    if mismatches:
        raise RuntimeError(
            "ARCH-03 hash agreement FAILED. Python and PostgreSQL produce "
            "different SHA-256 values for the same token, so any hash written "
            "by this migration would be unmatchable by the application. "
            "Nothing has been written. Mismatches: " + "; ".join(mismatches)
        )

    logger.info(
        "ARCH-03 hash agreement OK — %d tokens, hashlib and pg sha256 identical.",
        len(rows),
    )


# ===========================================================================
# Backfills
# ===========================================================================

def _backfill_email_verified_at(bind: sa.engine.Connection) -> int:
    """
    Grandfathers every existing account by trusting its creation timestamp.

    created_at rather than now(): it records that the account predates
    verification, which is the actual justification for trusting it. Stamping
    now() would assert that these four addresses were proved at migration
    time, which is not true of any of them.
    """
    rows = bind.execute(
        sa.text(
            """
            SELECT id, email, created_at
              FROM users
             WHERE email_verified_at IS NULL
             ORDER BY created_at
            """
        )
    ).fetchall()

    for row in rows:
        # Per-row, so the grandfathering decision is auditable afterwards
        # against a specific list of addresses rather than a count (§B.4).
        logger.info(
            "ARCH-03 grandfather user %s (created %s)",
            row.email,
            row.created_at.isoformat(),
        )

    result = bind.execute(
        sa.text(
            """
            UPDATE users
               SET email_verified_at = created_at
             WHERE email_verified_at IS NULL
            """
        )
    )

    logger.info("ARCH-03 grandfathered %d users.", result.rowcount)
    return result.rowcount


def _backfill_token_hash(bind: sa.engine.Connection) -> int:
    """
    Writes the SHA-256 of every invitation token.

    Every row, not only PENDING ones. A consumed invitation token is still a
    live secret until its row is gone; hashing one and leaving eighteen in
    plaintext removes nothing.

    Written row by row with a bound parameter rather than as one SQL-side
    UPDATE, so the value stored is exactly what Python computed. The SQL
    equivalent was already verified in _assert_hash_agreement; using it to
    write would mean the one value that must match the application is produced
    by something other than the application's algorithm.
    """
    rows = bind.execute(
        sa.text(
            """
            SELECT id, email, status, token
              FROM workspace_invitations
             WHERE token_hash IS NULL
             ORDER BY created_at
            """
        )
    ).fetchall()

    for row in rows:
        token_hash = _hash_token(row.token)

        bind.execute(
            sa.text(
                """
                UPDATE workspace_invitations
                   SET token_hash = :token_hash
                 WHERE id = :id
                """
            ),
            {"token_hash": token_hash, "id": row.id},
        )

        # Status and a hash prefix only. The plaintext token is never logged;
        # until CONTRACT drops the column it is still a working credential.
        logger.info(
            "ARCH-03 hashed invitation %s (%s, %s) -> %s…",
            row.id,
            row.email,
            row.status,
            token_hash[:12],
        )

    logger.info("ARCH-03 hashed %d invitation tokens.", len(rows))
    return len(rows)


# ===========================================================================
# Post-conditions
# ===========================================================================

def _assert_postconditions(bind: sa.engine.Connection) -> None:
    """
    Every check runs inside the migration transaction, so a failure rolls the
    whole revision back rather than leaving a half-backfilled table.
    """
    null_verified = bind.execute(
        sa.text("SELECT count(*) FROM users WHERE email_verified_at IS NULL")
    ).scalar_one()
    if null_verified:
        raise RuntimeError(
            f"ARCH-03 MIGRATE: {null_verified} users still have a NULL "
            "email_verified_at after the backfill."
        )

    null_hash = bind.execute(
        sa.text(
            "SELECT count(*) FROM workspace_invitations WHERE token_hash IS NULL"
        )
    ).scalar_one()
    if null_hash:
        raise RuntimeError(
            f"ARCH-03 MIGRATE: {null_hash} invitations still have a NULL "
            "token_hash after the backfill. CONTRACT's NOT NULL would fail."
        )

    bad_length = bind.execute(
        sa.text(
            """
            SELECT count(*)
              FROM workspace_invitations
             WHERE length(token_hash) <> :expected
            """
        ),
        {"expected": SHA256_HEX_LENGTH},
    ).scalar_one()
    if bad_length:
        raise RuntimeError(
            f"ARCH-03 MIGRATE: {bad_length} token_hash values are not "
            f"{SHA256_HEX_LENGTH} characters."
        )

    # CONTRACT adds UNIQUE(token_hash). Asserting distinctness here means that
    # constraint cannot be the thing that discovers a collision, which would
    # otherwise happen two revisions later with no obvious cause.
    duplicate_hashes = bind.execute(
        sa.text(
            """
            SELECT count(*) FROM (
                SELECT token_hash
                  FROM workspace_invitations
                 GROUP BY token_hash
                HAVING count(*) > 1
            ) AS duplicates
            """
        )
    ).scalar_one()
    if duplicate_hashes:
        raise RuntimeError(
            f"ARCH-03 MIGRATE: {duplicate_hashes} token_hash values are not "
            "unique. CONTRACT's UNIQUE constraint would fail."
        )

    # The hash must be recomputable from the surviving plaintext, or CONTRACT
    # drops a column whose replacement does not correspond to it.
    inconsistent = bind.execute(
        sa.text(
            """
            SELECT count(*)
              FROM workspace_invitations
             WHERE token_hash <> encode(sha256(token::bytea), 'hex')
            """
        )
    ).scalar_one()
    if inconsistent:
        raise RuntimeError(
            f"ARCH-03 MIGRATE: {inconsistent} stored hashes do not match "
            "their plaintext token."
        )

    logger.info(
        "ARCH-03 post-conditions OK — no NULLs, all hashes 64 chars, "
        "distinct, and consistent with their plaintext."
    )


# ===========================================================================
# Revision entry points
# ===========================================================================

def upgrade() -> None:
    bind = op.get_bind()

    user_count = bind.execute(sa.text("SELECT count(*) FROM users")).scalar_one()
    invitation_count = bind.execute(
        sa.text("SELECT count(*) FROM workspace_invitations")
    ).scalar_one()

    # If this is a completely empty database (such as during test suite execution
    # or a fresh application installation), we have no legacy baseline to backfill.
    # Bypass the grandfathering and hashing logic safely.
    if user_count == 0 and invitation_count == 0:
        logger.info(
            "ARCH-03: Fresh/empty database detected. Skipping legacy baseline checks and backfills."
        )
        return

    _check_count("users", user_count, EXPECTED_USER_COUNT)
    _check_count("invitations", invitation_count, EXPECTED_INVITATION_COUNT)

    # Both checks precede every write. A failure here leaves the database
    # exactly as EXPAND left it.
    _assert_sha256_available(bind)
    _assert_hash_agreement(bind)

    grandfathered = _backfill_email_verified_at(bind)
    hashed = _backfill_token_hash(bind)

    _assert_postconditions(bind)

    logger.info(
        "ARCH-03 MIGRATE complete — %d users grandfathered, %d tokens hashed.",
        grandfathered,
        hashed,
    )


def downgrade() -> None:
    bind = op.get_bind()

    # Reversible because neither source column was touched: created_at and
    # token are both still present and unmodified, so upgrade() can be replayed
    # and will produce identical values.
    verified = bind.execute(
        sa.text(
            "UPDATE users SET email_verified_at = NULL "
            "WHERE email_verified_at IS NOT NULL"
        )
    ).rowcount

    hashed = bind.execute(
        sa.text(
            "UPDATE workspace_invitations SET token_hash = NULL "
            "WHERE token_hash IS NOT NULL"
        )
    ).rowcount

    logger.info(
        "ARCH-03 MIGRATE reversed — %d users un-verified, %d token hashes cleared.",
        verified,
        hashed,
    )
