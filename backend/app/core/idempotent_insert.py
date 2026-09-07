"""Savepoint-guarded insert for retry-path producers.

The problem
-----------
A background handler that inserts into a UNIQUE-constrained table and then
calls `db.flush()` has two failure modes, and the second is worse than it
looks:

1.  The obvious one: a replay of the same logical operation collides and
    raises IntegrityError.

2.  The one that costs you data: SQLAlchemy marks the whole transaction
    rolled-back-only when a flush raises. So the collision does not just
    fail the duplicate insert, it discards every write the handler had
    accumulated in that unit of work. A handler that wrote six rows and
    then hit a duplicate on the seventh loses all six.

`insert_or_get` closes both. The SELECT handles the common case cheaply.
The SAVEPOINT handles the race between that SELECT and the INSERT, which is
the case that only appears once two workers run concurrently — which is to
say, in production and not on the founder's laptop.

When NOT to use this
--------------------
Only for retry paths. A deliberate create from an HTTP route must keep
raising, because there the UniqueViolation carries meaning: the caller asked
for something that already exists and the honest answer is 409.

Turning a user-facing create into insert_or_get is not a hardening, it is a
vulnerability. `create_user` inserts into `users`, unique on `email`. Making
it get-or-return means a signup with an existing address silently returns the
existing account, and the attacker is now holding a session for someone else's
user. The same reasoning applies to anything keyed on a `token_hash` or `secret_hash`:
returning the existing row gives the caller a credential they did not generate.

`scripts/scan_idempotency_seams.py` enforces that split. Every insert on a
unique-constrained model must be classified RETRY_PATH or USER_INTENT there,
and only the first population belongs here.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypeVar

from sqlalchemy import Select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger("app.core.idempotent_insert")

T = TypeVar("T")


class ConflictResolutionFailed(RuntimeError):
    """An IntegrityError fired but the lookup found no conflicting row.

    That combination means the violated constraint was not the one the
    lookup targets, so the error is unrelated to idempotency and must not be
    swallowed. Raised only after re-raising has been ruled out.
    """


def insert_or_get(
    db: Session,
    *,
    instance: T,
    lookup: Callable[[], Optional[T]],
    label: str,
    log_extra: Optional[dict[str, Any]] = None,
) -> tuple[T, bool]:
    """Insert `instance`, or return the row a prior attempt already wrote.

    Returns `(row, created)`. `created` is False when an existing row was
    returned, which lets the caller skip side effects it has already
    performed — emitting a second outbox event for a delivery that was
    already dispatched, for instance.

    `lookup` must query on exactly the constraint tuple that would collide.
    A broader predicate returns the wrong row; a narrower one lets the
    IntegrityError through and defeats the point.

    `instance` must not be in the session when this is called. The caller
    constructs it and hands it over; this function owns the add.
    """
    extra = dict(log_extra or {})

    existing = lookup()
    if existing is not None:
        logger.info(f"{label}.deduplicated", extra=extra)
        return existing, False

    db.add(instance)
    try:
        # SAVEPOINT, not a bare flush. Without the nested block an
        # IntegrityError here poisons the caller's entire transaction.
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        db.expunge(instance)
        winner = lookup()
        if winner is None:
            # A different constraint was violated. Not ours to absorb.
            logger.warning(
                f"{label}.integrity_error_unrelated",
                extra=extra,
                exc_info=True,
            )
            raise
        logger.info(f"{label}.raced", extra=extra)
        return winner, False

    return instance, True


def scalar_lookup(db: Session, statement: Select) -> Callable[[], Any]:
    """Build a `lookup` callable from a SELECT. Convenience for the common case.

    The statement is re-executed on each call, which is deliberate: the whole
    point of the second lookup is to see a row written by a concurrent
    transaction that was not visible during the first.
    """

    def _lookup() -> Any:
        return db.execute(statement.limit(1)).scalar_one_or_none()

    return _lookup


__all__ = ["ConflictResolutionFailed", "insert_or_get", "scalar_lookup"]
