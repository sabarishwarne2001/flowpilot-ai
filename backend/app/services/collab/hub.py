"""ARCH48-S1:hub — the per-process live review connection manager.

One `Hub` per API process. It holds this process's WebSocket connections by
workspace, subscribes the broker to exactly the workspaces it has connections
for, and runs, per workspace, a LEADER loop that only the broker-elected
process executes (expire lapsed locks, poll the queue digest, heal presence).

A CONNECTION
============

    handshake     gate.authenticate (in a worker thread: it reads the database)
                  -> refused: close 1008 BEFORE accepting (endpoint)
    accept        the subprotocol "flowpilot.review.v1" when offered (never
                  the token entry)
    hello         who you are, the heartbeat and lock intervals, presence and
                  the workspace's live locks
    receive loop  view / ping / lock / unlock, each at most MAX_MESSAGE_BYTES
                  (else 1009) and RATE_MESSAGES per RATE_WINDOW_SECONDS
                  (else 4429)
    send loop     a bounded queue per connection; a console too slow to drain
                  it is closed (1013) rather than letting memory grow
    watchdog      at the token's expiry: 4401 (the console refreshes its token
                  through the REST client and reconnects); every
                  SESSION_RECHECK_SECONDS: gate.recheck -> 4401 (session
                  revoked, signed out everywhere, account disabled) or 4403
                  (workspace access or the plan's capability gone)
    close         presence dropped and re-announced; on a CLEAN close (1000 /
                  1001: the tab closed) the connection's leases are released
                  at once, on an abnormal one (network) they are kept until
                  they lapse, so a reconnect within LOCK_TTL keeps the lock

Every database step runs in a worker thread with its own session and commits
before anything is announced (claim, commit, then publish -- ARCH-47's shape).
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.services.collab import broker as broker_module
from app.services.collab import gate
from app.services.collab import service
from app.services.collab import vocabulary as v

logger = logging.getLogger("app.services.collab.hub")

CLOSE_TRY_AGAIN = 1013
SEND_QUEUE = 256


def _session_factory() -> Any:
    from app.db import session as session_module

    return session_module.SessionLocal


async def in_thread(fn: Callable[..., Any], *args: Any) -> Any:
    """Run blocking database work off the event loop."""
    import anyio

    return await anyio.to_thread.run_sync(lambda: fn(*args))


@dataclass(eq=False)  # identity: a connection is a set member
class Connection:
    principal: gate.LivePrincipal
    websocket: Any
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=SEND_QUEUE))
    view: Optional[tuple[str, str]] = None
    since: float = field(default_factory=time.time)
    leases: dict[tuple[str, str], uuid.UUID] = field(default_factory=dict)
    stamps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=v.RATE_MESSAGES + 1))
    close_code: Optional[int] = None
    #: The event loop serving this connection: events published from a worker thread
    #: (a REST route after its commit) are handed to it with call_soon_threadsafe.
    loop: Optional[asyncio.AbstractEventLoop] = None
    #: Events that arrived before the hello was sent, delivered right after it (in order).
    early: Optional[list] = field(default_factory=list)

    @property
    def workspace(self) -> str:
        return str(self.principal.workspace_id)

    def presence_entry(self) -> dict:
        p = self.principal
        return {"user_id": str(p.user_id), "name": p.name, "email": p.email,
                "kind": self.view[0] if self.view else None, "item_id": self.view[1] if self.view else None,
                "since": self.since, "t": service.now().timestamp()}


class Hub:
    def __init__(self) -> None:
        self.owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self.connections: dict[str, set[Connection]] = {}
        self.leaders: dict[str, asyncio.Task] = {}
        self._broker: Optional[broker_module.Broker] = None
        self._presence_digest: dict[str, str] = {}
        self._mutex = threading.Lock()

    # ------------------------------------------------------------------ wiring
    @property
    def broker(self) -> broker_module.Broker:
        current = broker_module.get_broker()
        if current is not self._broker:
            current.bind(self.dispatch)
            self._broker = current
        return current

    def dispatch(self, workspace_id: str, message: dict) -> None:
        """Deliver one event to every local connection of the workspace. Callable from any thread."""
        with self._mutex:
            targets = list(self.connections.get(workspace_id, ()))
        try:
            running: Optional[asyncio.AbstractEventLoop] = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        for connection in targets:
            loop = connection.loop
            if loop is None or loop.is_closed():
                continue
            if loop is running:
                self._enqueue(connection, dict(message))
            else:
                loop.call_soon_threadsafe(self._enqueue, connection, dict(message))

    def _enqueue(self, connection: Connection, message: dict) -> None:
        if connection.early is not None and message.get("type") != v.SERVER_HELLO:
            connection.early.append(message)
            return
        try:
            connection.queue.put_nowait(message)
        except asyncio.QueueFull:
            # A console too slow to drain its queue is closed (it reconnects and re-reads) rather
            # than letting this process's memory grow with its backlog.
            self._close(connection, CLOSE_TRY_AGAIN)

    def local_count(self, user_id: uuid.UUID) -> int:
        with self._mutex:
            return sum(1 for conns in self.connections.values() for c in conns if c.principal.user_id == user_id)

    # ---------------------------------------------------------------- presence
    async def presence_snapshot(self, workspace_id: str) -> list[dict]:
        viewers, _ = await self.broker.presence_all(workspace_id, service.now().timestamp())
        return [{k: e.get(k) for k in ("connection_id", "user_id", "name", "email", "kind", "item_id", "since")}
                for e in viewers]

    async def announce_presence(self, workspace_id: str) -> None:
        viewers = await self.presence_snapshot(workspace_id)
        # F-218. A Redis publish blocks for up to its 2 s socket timeout: off the loop.
        await in_thread(self.broker.publish, workspace_id, {"type": v.EVENT_PRESENCE, "viewers": viewers})
        self._presence_digest[workspace_id] = json.dumps(viewers, sort_keys=True, default=str)

    # ------------------------------------------------------------------- serve
    async def serve(self, websocket: Any, principal: gate.LivePrincipal, subprotocol: Optional[str]) -> None:
        broker = self.broker
        if self.local_count(principal.user_id) >= v.MAX_CONNECTIONS_PER_USER:
            await websocket.close(code=v.CLOSE_POLICY)
            return
        await websocket.accept(subprotocol=subprotocol)
        connection = Connection(principal, websocket, loop=asyncio.get_running_loop())
        workspace_id = connection.workspace
        with self._mutex:
            first = workspace_id not in self.connections
            self.connections.setdefault(workspace_id, set()).add(connection)
        clean = False
        try:
            if first:
                try:
                    await broker.subscribe(workspace_id)
                except Exception:  # noqa: BLE001 - local delivery still works
                    broker.degraded = True
                    logger.warning("collab.subscribe_failed", exc_info=True)
                self._start_leader(workspace_id)
            await broker.presence_put(workspace_id, connection.id, connection.presence_entry())
            locks = await in_thread(self._locks_snapshot, principal.workspace_id)
            viewers = await self.presence_snapshot(workspace_id)
            # The hello is always the first frame; whatever was fanned out while it was
            # being assembled follows it, in order (nothing is lost between the snapshot
            # and the registration, because the connection was registered first).
            self._enqueue(connection, {
                "type": v.SERVER_HELLO, "connection_id": connection.id, "you": principal.person(),
                "heartbeat_seconds": v.HEARTBEAT_SECONDS, "lock_ttl_seconds": v.LOCK_TTL_SECONDS,
                "presence": viewers, "locks": locks, "degraded": bool(broker.degraded),
                "allowed_kinds": list(principal.allowed_kinds)})
            early, connection.early = connection.early or [], None
            for message in early:
                self._enqueue(connection, message)
            await self.announce_presence(workspace_id)
            clean = await self._run(connection)
        finally:
            # Shielded: a cancelled handler (a server shutting down) still gives its leases back
            # and takes its avatar down, rather than leaving both to lapse.
            import anyio

            with anyio.CancelScope(shield=True):
                await self._leave(connection, workspace_id, clean)

    async def _leave(self, connection: Connection, workspace_id: str, clean: bool) -> None:
        broker = self.broker
        if clean and connection.leases:
            try:
                await in_thread(self._release_all, connection, v.RELEASE_DISCONNECTED)
            except Exception:  # noqa: BLE001 - the leases lapse by themselves
                logger.warning("collab.release_on_close_failed", exc_info=True)
        with self._mutex:
            conns = self.connections.get(workspace_id)
            last = False
            if conns is not None:
                conns.discard(connection)
                if not conns:
                    self.connections.pop(workspace_id, None)
                    last = True
        if last:
            task = self.leaders.pop(workspace_id, None)
            if task is not None:
                task.cancel()
            try:
                await broker.unsubscribe(workspace_id)
            except Exception:  # noqa: BLE001
                pass
        try:
            await broker.presence_drop(workspace_id, connection.id)
            await self.announce_presence(workspace_id)
        except Exception:  # noqa: BLE001
            logger.warning("collab.presence_drop_failed", exc_info=True)

    async def _run(self, connection: Connection) -> bool:
        """Run the loops until one ends; True when the client closed cleanly."""
        outcome: dict[str, Any] = {"clean": False}
        tasks = [asyncio.create_task(self._receive(connection, outcome)),
                 asyncio.create_task(self._send(connection)),
                 asyncio.create_task(self._watch(connection))]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        if connection.close_code is not None:
            try:
                await connection.websocket.close(code=connection.close_code)
            except Exception:  # noqa: BLE001
                pass
        return bool(outcome["clean"])

    def _close(self, connection: Connection, code: int) -> None:
        if connection.close_code is None:
            connection.close_code = code
        self._enqueue_close(connection, code)

    def _enqueue_close(self, connection: Connection, code: int) -> None:
        """Drop the backlog (nothing more is worth sending) and wake the sender with the close."""
        connection.early = None
        while True:
            try:
                connection.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        connection.queue.put_nowait({"__close__": code})

    async def _send(self, connection: Connection) -> None:
        while True:
            message = await connection.queue.get()
            if "__close__" in message:
                connection.close_code = connection.close_code or int(message["__close__"])
                return
            await connection.websocket.send_text(json.dumps(message, separators=(",", ":"), default=str))

    async def _receive(self, connection: Connection, outcome: dict) -> None:
        from starlette.websockets import WebSocketDisconnect

        while True:
            try:
                raw = await connection.websocket.receive_text()
            except WebSocketDisconnect as exc:
                outcome["clean"] = exc.code in (1000, 1001)
                return
            except RuntimeError:  # the socket is already closed
                return
            if len(raw.encode("utf-8")) > v.MAX_MESSAGE_BYTES:
                self._close(connection, v.CLOSE_TOO_BIG)
                return
            moment = time.monotonic()
            connection.stamps.append(moment)
            if len(connection.stamps) > v.RATE_MESSAGES and moment - connection.stamps[0] < v.RATE_WINDOW_SECONDS:
                self._close(connection, v.CLOSE_RATE_LIMITED)
                return
            try:
                message = json.loads(raw)
            except ValueError:
                message = None
            if not isinstance(message, dict) or message.get("type") not in v.CLIENT_TYPES:
                self._enqueue(connection, {"type": v.SERVER_ERROR, "code": "BAD_MESSAGE",
                                           "detail": f"Expected a JSON object with type one of {list(v.CLIENT_TYPES)}."})
                continue
            try:
                await self._handle(connection, message)
            except gate.LiveRefused as exc:
                self._close(connection, exc.close_code)
                return
            except Exception:  # noqa: BLE001 - one bad step never takes the others' connections down
                logger.exception("collab.message_failed", extra={"type": message.get("type")})
                self._enqueue(connection, {"type": v.SERVER_ERROR, "code": "FAILED",
                                           "detail": "That could not be done. Try again."})

    async def _watch(self, connection: Connection) -> None:
        principal = connection.principal
        while True:
            left = principal.token_expires_at.timestamp() - service.now().timestamp()
            if left <= 0:
                self._close(connection, v.CLOSE_SESSION_ENDED)
                return
            await asyncio.sleep(max(0.05, min(float(v.SESSION_RECHECK_SECONDS), left)))
            if principal.token_expires_at.timestamp() <= service.now().timestamp():
                self._close(connection, v.CLOSE_SESSION_ENDED)
                return
            try:
                await in_thread(self._recheck, principal)
            except gate.LiveRefused as exc:
                logger.info("collab.connection_closed", extra={"reason": exc.reason, "code": exc.close_code,
                                                               "user_id": str(principal.user_id)})
                self._close(connection, exc.close_code)
                return

    # --------------------------------------------------------------- messages
    async def _handle(self, connection: Connection, message: dict) -> None:
        kind = message.get("type")
        workspace_id = connection.workspace
        if kind == v.CLIENT_PING:
            result = await in_thread(self._heartbeat_all, connection)
            await self.broker.presence_put(workspace_id, connection.id, connection.presence_entry())
            self._enqueue(connection, {"type": v.SERVER_PONG, **result})
            return
        if kind == v.CLIENT_VIEW:
            target = self._target(connection, message, required=False)
            if target is not None:
                visible = await in_thread(self._visible, connection, target)
                if not visible:
                    self._enqueue(connection, {"type": v.SERVER_ERROR, "code": "NOT_FOUND",
                                               "detail": "No such review item in this workspace."})
                    return
            if target != connection.view:
                connection.view = target
                await self.broker.presence_put(workspace_id, connection.id, connection.presence_entry())
                await self.announce_presence(workspace_id)
            return
        target = self._target(connection, message, required=True)
        if target is None:
            return
        if kind == v.CLIENT_LOCK:
            reply = await in_thread(self._lock, connection, target)
        else:
            reply = await in_thread(self._unlock, connection, target)
        self._enqueue(connection, {"type": v.SERVER_LOCK_RESULT, "kind": target[0], "item_id": target[1], **reply})

    def _target(self, connection: Connection, message: dict, *, required: bool) -> Optional[tuple[str, str]]:
        kind, item_id = message.get("kind"), message.get("item_id")
        if not kind and not item_id and not required:
            return None
        try:
            parsed = str(uuid.UUID(str(item_id)))
        except (TypeError, ValueError):
            parsed = ""
        if kind not in connection.principal.allowed_kinds or not parsed:
            self._enqueue(connection, {"type": v.SERVER_ERROR, "code": "BAD_TARGET",
                                       "detail": "kind must be a review kind your plan includes, item_id a UUID."})
            return None
        return str(kind), parsed

    # -------------------------------------------------- database steps (threads)
    def _recheck(self, principal: gate.LivePrincipal) -> None:
        with _session_factory()() as db:
            gate.recheck(db, principal)

    def _locks_snapshot(self, workspace_id: uuid.UUID) -> list[dict]:
        with _session_factory()() as db:
            locks = service.workspace_locks(db, workspace_id=workspace_id)
            people = service.people(db, [lock.holder_user_id for lock in locks])
        return [{**lock.as_event(), "holder": people.get(str(lock.holder_user_id))} for lock in locks]

    def _visible(self, connection: Connection, target: tuple[str, str]) -> bool:
        from app.services.review import projection

        with _session_factory()() as db:
            return projection.load_item(db, workspace_id=connection.principal.workspace_id, kind=target[0],
                                        item_id=uuid.UUID(target[1])) is not None

    def _lock(self, connection: Connection, target: tuple[str, str]) -> dict:
        from app.services.billing import dunning_service
        from app.services.collab import events
        from app.services.review import projection
        from app.services.review import vocabulary as review_vocab

        principal = connection.principal
        kind, item_id = target[0], uuid.UUID(target[1])
        with _session_factory()() as db:
            if not dunning_service.access_state(db, organization_id=principal.organization_id).writes_allowed:
                return {"ok": False, "code": "READ_ONLY", "detail": "The organization is read-only."}
            item = projection.load_item(db, workspace_id=principal.workspace_id, kind=kind, item_id=item_id)
            if item is None:
                return {"ok": False, "code": "NOT_FOUND", "detail": "No such review item in this workspace."}
            if item.status == review_vocab.STATUS_RESOLVED:
                return {"ok": False, "code": v.CODE_ALREADY_RESOLVED, "detail": "This item is already resolved."}
            outcome = service.acquire_lock(db, workspace_id=principal.workspace_id, kind=kind, item_id=item_id,
                                           user_id=principal.user_id)
            holder = outcome.lock
            people = service.people(db, [holder.holder_user_id] if holder else [])
            db.commit()
        if not outcome.acquired or holder is None:
            return {"ok": False, "code": v.CODE_LOCKED,
                    "holder": people.get(str(holder.holder_user_id)) if holder else None,
                    "expires_at": holder.expires_at.isoformat() if holder else None}
        connection.leases[target] = holder.lease_token  # type: ignore[assignment]
        if not outcome.reentrant:
            events.publish_now(principal.workspace_id, {"type": v.EVENT_LOCK_ACQUIRED, **holder.as_event(),
                                                         "holder": people.get(str(holder.holder_user_id))})
        return {"ok": True, "expires_at": holder.expires_at.isoformat(), "reentrant": outcome.reentrant,
                "holder": people.get(str(holder.holder_user_id))}

    def _unlock(self, connection: Connection, target: tuple[str, str]) -> dict:
        from app.services.collab import events

        token = connection.leases.pop(target, None)
        if token is None:
            return {"ok": False, "code": v.CODE_LEASE_LOST, "detail": "This connection does not hold that lock."}
        with _session_factory()() as db:
            holder = service.release(db, kind=target[0], item_id=uuid.UUID(target[1]), lease_token=token)
            db.commit()
        if holder is None:
            return {"ok": False, "code": v.CODE_LEASE_LOST, "detail": "The lock had already lapsed."}
        events.publish_now(connection.principal.workspace_id, {
            "type": v.EVENT_LOCK_RELEASED, "kind": target[0], "item_id": target[1],
            "holder_user_id": str(holder), "reason": v.RELEASE_RELEASED})
        return {"ok": True, "released": True}

    def _heartbeat_all(self, connection: Connection) -> dict:
        """Heartbeat = extend every lease this connection holds. Lost ones are dropped and reported."""
        if not connection.leases:
            return {"leases": [], "lost": []}
        kept, lost = [], []
        with _session_factory()() as db:
            for (kind, item_id), token in list(connection.leases.items()):
                until = service.heartbeat(db, kind=kind, item_id=uuid.UUID(item_id), lease_token=token)
                if until is None:
                    lost.append({"kind": kind, "item_id": item_id})
                    connection.leases.pop((kind, item_id), None)
                else:
                    kept.append({"kind": kind, "item_id": item_id, "expires_at": until.isoformat()})
            db.commit()
        return {"leases": kept, "lost": lost}

    def _release_all(self, connection: Connection, reason: str) -> None:
        from app.services.collab import events

        with _session_factory()() as db:
            released = []
            for (kind, item_id), token in list(connection.leases.items()):
                holder = service.release(db, kind=kind, item_id=uuid.UUID(item_id), lease_token=token)
                if holder is not None:
                    released.append((kind, item_id, holder))
            db.commit()
        connection.leases.clear()
        for kind, item_id, holder in released:
            events.publish_now(connection.principal.workspace_id, {
                "type": v.EVENT_LOCK_RELEASED, "kind": kind, "item_id": item_id, "holder_user_id": str(holder),
                "reason": reason})

    # ---------------------------------------------------------------- leader
    def _start_leader(self, workspace_id: str) -> None:
        task = self.leaders.get(workspace_id)
        if task is None or task.done():
            self.leaders[workspace_id] = asyncio.get_running_loop().create_task(
                self._lead(workspace_id), name=f"collab-leader-{workspace_id}")

    async def _lead(self, workspace_id: str) -> None:
        while True:
            try:
                await self.leader_tick(workspace_id)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.warning("collab.leader_tick_failed", extra={"workspace_id": workspace_id}, exc_info=True)
            await asyncio.sleep(v.WATERMARK_SECONDS)

    async def leader_tick(self, workspace_id: str) -> dict:
        """One leader pass: lapsed locks, the queue digest, stale presence. Returns what it announced."""
        broker = self.broker
        if not await broker.lead(workspace_id, self.owner, v.WATERMARK_SECONDS * 3):
            return {"leader": False}
        expired, digest = await in_thread(self._leader_db, uuid.UUID(workspace_id))
        # F-218. Every publish below runs off the loop (a Redis publish blocks for up to 2 s).
        for lock in expired:
            await in_thread(broker.publish, workspace_id,
                            {"type": v.EVENT_LOCK_RELEASED, "kind": lock.kind, "item_id": str(lock.item_id),
                             "holder_user_id": str(lock.holder_user_id), "reason": v.RELEASE_EXPIRED})
        changed = await broker.swap_digest(workspace_id, digest)
        if changed:
            await in_thread(broker.publish, workspace_id, {"type": v.EVENT_QUEUE_CHANGED, "reason": "watermark"})
        _, pruned = await broker.presence_all(workspace_id, service.now().timestamp())
        viewers = await self.presence_snapshot(workspace_id)
        snapshot = json.dumps(viewers, sort_keys=True, default=str)
        healed = pruned > 0 or self._presence_digest.get(workspace_id) != snapshot
        if healed:
            await in_thread(broker.publish, workspace_id, {"type": v.EVENT_PRESENCE, "viewers": viewers})
            self._presence_digest[workspace_id] = snapshot
        return {"leader": True, "expired": len(expired), "queue_changed": changed, "presence": healed}

    def _leader_db(self, workspace_id: uuid.UUID) -> tuple[list[service.LockState], str]:
        from sqlalchemy import text

        with _session_factory()() as db:
            expired = service.expire_locks(db, workspace_id=workspace_id)
            row = db.execute(text(
                "SELECT count(*), COALESCE(sum(hashtext(kind || ':' || item_id::text)::bigint), 0) "
                "FROM review_queue_items WHERE workspace_id = :w AND status = 'OPEN'"), {"w": workspace_id}).first()
            db.commit()
        return expired, f"{int(row[0])}:{int(row[1])}"


_hub: Optional[Hub] = None


def get_hub() -> Hub:
    global _hub
    if _hub is None:
        _hub = Hub()
    return _hub


def set_hub(hub: Optional[Hub]) -> Optional[Hub]:
    global _hub
    previous, _hub = _hub, hub
    return previous


__all__ = ["Connection", "Hub", "get_hub", "in_thread", "set_hub"]
