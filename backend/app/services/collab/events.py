"""ARCH48-S1:events — live events, published only after the transaction that caused them commits.

A reviewer's console that is told "item X was resolved" re-reads the queue at
once. Told BEFORE the commit, it would read the old row and show an item that
is about to vanish; told about a transaction that then rolls back, it would
hide an item that is still open. So an event is QUEUED on the SQLAlchemy
session with the transaction that was innermost when it was queued, and:

    the outermost transaction commits    every queued event is published
    it rolls back                        every queued event is dropped
    a SAVEPOINT rolls back               the events queued inside it are dropped
                                         (bulk resolution: one refused item
                                         never announces the others' events,
                                         and never its own)

`resolution.resolve_item` queues `item.resolved` this way whichever endpoint
called it -- the hub, bulk, or a source screen (verifications, assertions,
anomalies) -- so every resolution is live without those screens knowing.

Publishing is best effort and never raises into the caller: the database is
the truth, and a console that misses an event re-reads on reconnect and on the
leader's queue digest (hub.py).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import event as sa_event
from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.collab.events")

_PENDING = "arch48_collab_events"
_ROLLED_BACK = "arch48_collab_rolled_back"
_HOOKED = "arch48_collab_hooked"
_OUTCOME = "arch48_collab_outcome"


def _innermost(db: Session) -> Any:
    getter = getattr(db, "get_nested_transaction", None)
    nested = getter() if getter else None
    return nested or db.get_transaction()


def _chain(transaction: Any):
    while transaction is not None:
        yield transaction
        transaction = getattr(transaction, "parent", None)


def _after_commit(session: Session) -> None:
    session.info[_OUTCOME] = "commit"


def _after_rollback(session: Session) -> None:
    session.info[_OUTCOME] = "rollback"


def _after_transaction_end(session: Session, transaction: Any) -> None:
    """How each transaction ended, and at the ROOT's end, the publication.

    SQLAlchemy (2.0) fires `after_commit` / `after_rollback` for a SAVEPOINT
    release / rollback as well as for the root, each immediately followed by
    `after_transaction_end` for that transaction -- so the outcome recorded by
    the first tells this handler how THIS transaction ended. A transaction that
    ends with neither (a close) counts as rolled back. The transaction objects
    are kept, never their id(): a later one can be allocated at the same address.
    """
    outcome = session.info.pop(_OUTCOME, "rollback")
    if outcome != "commit":
        session.info.setdefault(_ROLLED_BACK, []).append(transaction)
    if getattr(transaction, "parent", None) is not None:
        return
    pending = session.info.pop(_PENDING, [])
    rolled_back = session.info.pop(_ROLLED_BACK, [])
    if outcome != "commit":
        return
    for chain, workspace_id, message in pending:
        if any(link is dead for link in chain for dead in rolled_back):
            continue
        publish_now(workspace_id, message)


def _hook(db: Session) -> None:
    if db.info.get(_HOOKED):
        return
    sa_event.listen(db, "after_commit", _after_commit)
    sa_event.listen(db, "after_rollback", _after_rollback)
    sa_event.listen(db, "after_transaction_end", _after_transaction_end)
    db.info[_HOOKED] = True


def publish_after_commit(db: Session, workspace_id: uuid.UUID | str, message: dict) -> None:
    """Queue `message` for the workspace's live channel; it goes out when the transaction commits."""
    if db.get_transaction() is None:
        publish_now(workspace_id, message)
        return
    _hook(db)
    # The chain is captured NOW: SQLAlchemy clears a closed transaction's parent link.
    db.info.setdefault(_PENDING, []).append((tuple(_chain(_innermost(db))), str(workspace_id), dict(message)))


def publish_now(workspace_id: uuid.UUID | str, message: dict) -> None:
    """Hand the message to the broker (Redis pub/sub, or the in-process broker)."""
    from app.services.collab import broker

    try:
        broker.get_broker().publish(str(workspace_id), dict(message))
    except Exception:  # noqa: BLE001 - the live channel never fails a write
        logger.warning("collab.publish_failed", extra={"workspace_id": str(workspace_id),
                                                        "event": message.get("type")}, exc_info=True)


def pending(db: Session) -> list[tuple[str, dict]]:
    """What is queued on this session (gates read it)."""
    return [(w, m) for _, w, m in db.info.get(_PENDING, [])]


def item_resolved(db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID, version: int,
                  actor_user_id: Optional[uuid.UUID], resolution: str) -> None:
    from app.services.collab import vocabulary as v

    publish_after_commit(db, workspace_id, {
        "type": v.EVENT_ITEM_RESOLVED, "kind": kind, "item_id": str(item_id), "version": int(version),
        "by_user_id": str(actor_user_id) if actor_user_id else None, "resolution": str(resolution)[:64]})


def lock_released(db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID,
                  holder_user_id: Optional[uuid.UUID], reason: str) -> None:
    from app.services.collab import vocabulary as v

    publish_after_commit(db, workspace_id, {
        "type": v.EVENT_LOCK_RELEASED, "kind": kind, "item_id": str(item_id),
        "holder_user_id": str(holder_user_id) if holder_user_id else None, "reason": reason})


def item_assigned(db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID,
                  assignee_user_id: Optional[uuid.UUID]) -> None:
    from app.services.collab import vocabulary as v

    publish_after_commit(db, workspace_id, {
        "type": v.EVENT_ITEM_ASSIGNED, "kind": kind, "item_id": str(item_id),
        "assignee_user_id": str(assignee_user_id) if assignee_user_id else None})


def thread_changed(db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID, thread_id: uuid.UUID,
                   action: str, open_threads: int) -> None:
    from app.services.collab import vocabulary as v

    publish_after_commit(db, workspace_id, {
        "type": v.EVENT_THREAD_CHANGED, "kind": kind, "item_id": str(item_id), "thread_id": str(thread_id),
        "action": action, "open_threads": int(open_threads)})


__all__ = ["item_assigned", "item_resolved", "lock_released", "pending", "publish_after_commit", "publish_now",
           "thread_changed"]
