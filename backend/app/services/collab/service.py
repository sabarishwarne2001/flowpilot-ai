"""ARCH48-S1:service — THE clock, soft review locks and resolution versions.

ONE CLOCK
=========

`now()` is the only source of time for leases, heartbeats and presence: every
function here takes `at=` and falls back to `now()`, and the hub and the broker
call `service.now()` (looked up at call time), so a gate pins time by patching
this one function -- the way ARCH-46's `obligations.service.now()` and
ARCH-47's `erp.service.now()` work. Lease times are written from it, never from
the database's `now()`, so a pinned clock and the rows agree.

SOFT LOCKS: THE CLAIM SHAPE
===========================

ARCH-47 claims a work-queue row with `SELECT ... FOR UPDATE SKIP LOCKED`, a
lease token and an expiry. A review lock names ONE item, so there is nothing
to skip to: the claim is one statement,

    INSERT ... ON CONFLICT (kind, item_id) DO UPDATE ... WHERE
        the existing lease has expired OR it is already the caller's

which the unique index serialises. Two people racing for a free item: the
second INSERT waits on the first's uncommitted row, then evaluates the WHERE
against the committed lease -- held, not theirs -- and gets no row back.
A heartbeat extends only the caller's own UNEXPIRED lease (the token is the
proof), so a lease that lapsed and was taken by someone else is never revived;
the old holder's heartbeat reports the lease lost. The caller commits before
announcing anything (events are published after commit).

A lock is SOFT: it never blocks reading, commenting or assigning, and it
expires by itself. What it does refuse is a resolution by someone else while
it is live (`resolution.resolve_item`), because that is the wasted work the
lock exists to prevent. A workspace admin can break it.

VERSIONS: OPTIMISTIC CONCURRENCY
================================

`claim_version` makes sure the item's row exists (INSERT ... DO NOTHING, which
waits on a concurrent first insert), then increments it with an UPDATE that
takes the row lock -- guarded by `version = :expected` when the caller read a
version. A concurrent resolver blocks on that row lock until the first commits,
then PostgreSQL re-evaluates the guard against the committed row: the version
moved, no row comes back, and the caller is told what the version is now.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.collab import vocabulary as v


def now() -> datetime:
    """ARCH48-S1:clock. The one clock (gates patch this)."""
    return datetime.now(timezone.utc)


def _at(at: Optional[datetime]) -> datetime:
    return at if at is not None else now()


# ---------------------------------------------------------------------------
# Versions
# ---------------------------------------------------------------------------


def claim_version(
    db: Session,
    *,
    kind: str,
    item_id: uuid.UUID,
    workspace_id: uuid.UUID,
    expected: Optional[int],
    actor_user_id: Optional[uuid.UUID],
    at: Optional[datetime] = None,
) -> tuple[bool, int]:
    """(claimed, version): the new version when claimed, else the current one.

    Holds the version row's lock until the caller's transaction ends.
    """
    moment = _at(at)
    params = {"k": kind, "i": item_id, "w": workspace_id, "u": actor_user_id, "now": moment}
    db.execute(
        text(
            "INSERT INTO review_item_versions (kind, item_id, workspace_id, version, updated_at) "
            "VALUES (:k, :i, :w, 0, :now) ON CONFLICT (kind, item_id) DO NOTHING"
        ),
        params,
    )
    # ARCH48-S1:version-guard. The guard is the whole of optimistic concurrency.
    guard = "" if expected is None else " AND version = :e"
    row = db.execute(
        text(
            "UPDATE review_item_versions SET version = version + 1, updated_at = :now, updated_by_user_id = :u "
            f"WHERE kind = :k AND item_id = :i AND workspace_id = :w{guard} RETURNING version"
        ),
        {**params, "e": expected},
    ).first()
    if row is not None:
        return True, int(row[0])
    return False, current_version(db, kind=kind, item_id=item_id)


def current_version(db: Session, *, kind: str, item_id: uuid.UUID) -> int:
    value = db.execute(
        text("SELECT version FROM review_item_versions WHERE kind = :k AND item_id = :i"),
        {"k": kind, "i": item_id},
    ).scalar_one_or_none()
    return int(value or 0)


# ---------------------------------------------------------------------------
# Locks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LockState:
    """A lease as a caller may see it (the token only when it is the caller's)."""

    kind: str
    item_id: uuid.UUID
    holder_user_id: uuid.UUID
    acquired_at: datetime
    expires_at: datetime
    lease_token: Optional[uuid.UUID] = None

    def as_event(self) -> dict:
        return {"kind": self.kind, "item_id": str(self.item_id), "holder_user_id": str(self.holder_user_id),
                "acquired_at": self.acquired_at.isoformat(), "expires_at": self.expires_at.isoformat()}


@dataclass(frozen=True)
class LockOutcome:
    acquired: bool
    lock: Optional[LockState]
    #: True when the caller already held the lease (a second tab, a reconnect).
    reentrant: bool = False


def _lease_end(moment: datetime) -> datetime:
    return moment + timedelta(seconds=v.LOCK_TTL_SECONDS)


def acquire_lock(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    kind: str,
    item_id: uuid.UUID,
    user_id: uuid.UUID,
    at: Optional[datetime] = None,
) -> LockOutcome:
    """Take the item's lock, extend the caller's own, or report who holds it."""
    moment = _at(at)
    # ARCH48-S1:lock-claim. Free (expired) or already mine; anything else returns no row.
    row = db.execute(
        text(
            """
            INSERT INTO review_locks (id, workspace_id, kind, item_id, holder_user_id, lease_token,
                                      acquired_at, heartbeat_at, expires_at)
            VALUES (:id, :w, :k, :i, :u, :t, :now, :now, :exp)
            ON CONFLICT (kind, item_id) DO UPDATE SET
                workspace_id = EXCLUDED.workspace_id,
                holder_user_id = EXCLUDED.holder_user_id,
                lease_token = CASE WHEN review_locks.holder_user_id = EXCLUDED.holder_user_id
                                        AND review_locks.expires_at > :now
                                   THEN review_locks.lease_token ELSE EXCLUDED.lease_token END,
                acquired_at = CASE WHEN review_locks.holder_user_id = EXCLUDED.holder_user_id
                                        AND review_locks.expires_at > :now
                                   THEN review_locks.acquired_at ELSE EXCLUDED.acquired_at END,
                -- ARCH48-S1:lease-monotonic. The same person's second tab can reach the row with a
                -- timestamp taken a moment BEFORE the first tab's: a lease never moves backwards
                -- (heartbeat_at below acquired_at would violate ck_review_locks_lease_order).
                heartbeat_at = CASE WHEN review_locks.holder_user_id = EXCLUDED.holder_user_id
                                         AND review_locks.expires_at > :now
                                    THEN GREATEST(review_locks.heartbeat_at, EXCLUDED.heartbeat_at)
                                    ELSE EXCLUDED.heartbeat_at END,
                expires_at = CASE WHEN review_locks.holder_user_id = EXCLUDED.holder_user_id
                                       AND review_locks.expires_at > :now
                                  THEN GREATEST(review_locks.expires_at, EXCLUDED.expires_at)
                                  ELSE EXCLUDED.expires_at END
            WHERE review_locks.expires_at <= :now OR review_locks.holder_user_id = EXCLUDED.holder_user_id
            RETURNING holder_user_id, lease_token, acquired_at, expires_at, (lease_token <> :t) AS reentrant
            """
        ),
        {"id": uuid.uuid4(), "w": workspace_id, "k": kind, "i": item_id, "u": user_id, "t": uuid.uuid4(),
         "now": moment, "exp": _lease_end(moment)},
    ).first()
    if row is not None:
        return LockOutcome(True, LockState(kind, item_id, row.holder_user_id, row.acquired_at, row.expires_at,
                                           row.lease_token), bool(row.reentrant))
    return LockOutcome(False, live_lock(db, kind=kind, item_id=item_id, at=moment))


def heartbeat(
    db: Session,
    *,
    kind: str,
    item_id: uuid.UUID,
    lease_token: uuid.UUID,
    at: Optional[datetime] = None,
) -> Optional[datetime]:
    """Extend the caller's own unexpired lease; None when it is lost."""
    moment = _at(at)
    # ARCH48-S1:lock-heartbeat. The token AND an unexpired lease: a lapsed lease is never revived.
    return db.execute(
        text(
            "UPDATE review_locks SET heartbeat_at = GREATEST(heartbeat_at, :now), expires_at = GREATEST(expires_at, :exp) "
            "WHERE kind = :k AND item_id = :i AND lease_token = :t AND expires_at > :now RETURNING expires_at"
        ),
        {"k": kind, "i": item_id, "t": lease_token, "now": moment, "exp": _lease_end(moment)},
    ).scalar_one_or_none()


def release(db: Session, *, kind: str, item_id: uuid.UUID, lease_token: uuid.UUID) -> Optional[uuid.UUID]:
    """Give the lease back; the holder when it was released, None when it was not the caller's."""
    return db.execute(
        text("DELETE FROM review_locks WHERE kind = :k AND item_id = :i AND lease_token = :t RETURNING holder_user_id"),
        {"k": kind, "i": item_id, "t": lease_token},
    ).scalar_one_or_none()


def clear_item_lock(db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID) -> Optional[uuid.UUID]:
    """Remove the item's lock whoever holds it (a resolution, or an admin breaking it)."""
    return db.execute(
        text("DELETE FROM review_locks WHERE kind = :k AND item_id = :i AND workspace_id = :w RETURNING holder_user_id"),
        {"k": kind, "i": item_id, "w": workspace_id},
    ).scalar_one_or_none()


def live_lock(db: Session, *, kind: str, item_id: uuid.UUID, at: Optional[datetime] = None) -> Optional[LockState]:
    """The item's unexpired lease (without its token), or None."""
    row = db.execute(
        text(
            "SELECT holder_user_id, acquired_at, expires_at FROM review_locks "
            "WHERE kind = :k AND item_id = :i AND expires_at > :now"
        ),
        {"k": kind, "i": item_id, "now": _at(at)},
    ).first()
    if row is None:
        return None
    return LockState(kind, item_id, row.holder_user_id, row.acquired_at, row.expires_at)


def workspace_locks(db: Session, *, workspace_id: uuid.UUID, at: Optional[datetime] = None) -> list[LockState]:
    rows = db.execute(
        text(
            "SELECT kind, item_id, holder_user_id, acquired_at, expires_at FROM review_locks "
            "WHERE workspace_id = :w AND expires_at > :now ORDER BY acquired_at"
        ),
        {"w": workspace_id, "now": _at(at)},
    ).all()
    return [LockState(r.kind, r.item_id, r.holder_user_id, r.acquired_at, r.expires_at) for r in rows]


def expire_locks(db: Session, *, workspace_id: uuid.UUID, at: Optional[datetime] = None) -> list[LockState]:
    """Delete the workspace's lapsed leases and return them (so their badges can be taken down)."""
    rows = db.execute(
        text(
            "DELETE FROM review_locks WHERE workspace_id = :w AND expires_at <= :now "
            "RETURNING kind, item_id, holder_user_id, acquired_at, expires_at"
        ),
        {"w": workspace_id, "now": _at(at)},
    ).all()
    return [LockState(r.kind, r.item_id, r.holder_user_id, r.acquired_at, r.expires_at) for r in rows]


def release_user_locks(db: Session, *, user_id: uuid.UUID) -> int:
    """Every lease a person holds (erasure: their sessions are revoked with it)."""
    return int(db.execute(text("DELETE FROM review_locks WHERE holder_user_id = :u"), {"u": user_id}).rowcount or 0)


# ---------------------------------------------------------------------------
# People, as a live channel shows them
# ---------------------------------------------------------------------------


def people(db: Session, user_ids: list[uuid.UUID]) -> dict[str, dict]:
    """{user id: {user_id, name, email}} -- workspace members already see each other's emails (assignees)."""
    ids = sorted({u for u in user_ids if u is not None}, key=str)
    if not ids:
        return {}
    rows = db.execute(
        text("SELECT id, email, display_name FROM users WHERE id = ANY(:ids)"), {"ids": ids}
    ).all()
    return {str(r.id): {"user_id": str(r.id), "email": r.email, "name": (r.display_name or r.email or "").strip()}
            for r in rows}


__all__ = [
    "LockOutcome",
    "LockState",
    "acquire_lock",
    "claim_version",
    "clear_item_lock",
    "current_version",
    "expire_locks",
    "heartbeat",
    "live_lock",
    "now",
    "people",
    "release",
    "release_user_locks",
    "workspace_locks",
]
