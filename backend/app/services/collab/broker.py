"""ARCH48-S1:broker — fan-out across API workers, and presence every worker can read.

Each API process holds only its own WebSocket connections. A lock taken on
worker A must reach a console connected to worker B, so every live event goes
through a broker:

  RedisBroker   (production: `REDIS_URL` is set -- Redis already runs; no new
                service) PUBLISH to one channel per workspace; each process
                SUBSCRIBEs to the channels of the workspaces it has
                connections for and hands what arrives to its hub. Presence is
                one Redis hash per workspace (connection id -> who, which item,
                last heard), so any process can compute the snapshot; entries
                not refreshed within PRESENCE_TTL_SECONDS are pruned on read.
                One process per workspace is its LEADER (a Redis lease), and
                only the leader expires locks and polls the queue digest.
  MemoryBroker  (no REDIS_URL; single process; the offline gates) the same
                interface in process.

Publishing is SYNCHRONOUS and thread-safe (route handlers run in the thread
pool and publish after their commit); everything a hub awaits is async.

When Redis cannot be reached a publish is still delivered to this process's
own connections (a single-worker deployment keeps working), the failure is
logged, and the pub/sub reader reconnects with backoff. Events lost during an
outage are recovered by the consoles re-reading: after a reconnect the reader
tells every local connection `queue.changed` (reason "resync").
"""

from __future__ import annotations

import asyncio
import collections
import json
import logging
import threading
from typing import Any, Awaitable, Callable, Optional

from app.services.collab import vocabulary as v

logger = logging.getLogger("app.services.collab.broker")

Dispatch = Callable[[str, dict], None]


class Broker:
    """The interface both brokers implement."""

    kind = "abstract"

    def __init__(self) -> None:
        self._dispatch: Optional[Dispatch] = None
        #: The last messages this process published (the gates read it).
        self.recent: collections.deque = collections.deque(maxlen=500)
        self.degraded = False

    # -- wiring -------------------------------------------------------------
    def bind(self, dispatch: Dispatch) -> None:
        """The hub's dispatch function (thread-safe: it hands each message to its connection's loop)."""
        self._dispatch = dispatch

    def _deliver_local(self, workspace_id: str, message: dict) -> None:
        """Hand a message to this process's hub, from any thread."""
        if self._dispatch is not None:
            self._dispatch(workspace_id, message)

    # -- interface ----------------------------------------------------------
    def publish(self, workspace_id: str, message: dict) -> None:
        raise NotImplementedError

    async def subscribe(self, workspace_id: str) -> None:
        raise NotImplementedError

    async def unsubscribe(self, workspace_id: str) -> None:
        raise NotImplementedError

    async def presence_put(self, workspace_id: str, connection_id: str, entry: dict) -> None:
        raise NotImplementedError

    async def presence_drop(self, workspace_id: str, connection_id: str) -> None:
        raise NotImplementedError

    async def presence_all(self, workspace_id: str, now_epoch: float) -> tuple[list[dict], int]:
        """(live entries, number pruned)."""
        raise NotImplementedError

    async def lead(self, workspace_id: str, owner: str, ttl_seconds: int) -> bool:
        raise NotImplementedError

    async def swap_digest(self, workspace_id: str, digest: str) -> bool:
        """Store the queue digest; True when it differs from the one stored before."""
        raise NotImplementedError

    async def close(self) -> None:
        return None


def _fresh(entries: dict[str, dict], now_epoch: float) -> tuple[list[dict], list[str]]:
    live, stale = [], []
    for connection_id, entry in entries.items():
        if now_epoch - float(entry.get("t") or 0) > v.PRESENCE_TTL_SECONDS:
            stale.append(connection_id)
        else:
            live.append({**entry, "connection_id": connection_id})
    live.sort(key=lambda e: (float(e.get("since") or 0), e["connection_id"]))
    return live[-v.MAX_PRESENCE:], stale


class MemoryBroker(Broker):
    """One process: the same semantics without Redis."""

    kind = "memory"

    def __init__(self) -> None:
        super().__init__()
        self._presence: dict[str, dict[str, dict]] = {}
        self._digests: dict[str, str] = {}
        self._lock = threading.Lock()

    def publish(self, workspace_id: str, message: dict) -> None:
        self.recent.append((workspace_id, dict(message)))
        self._deliver_local(workspace_id, dict(message))

    async def subscribe(self, workspace_id: str) -> None:
        return None

    async def unsubscribe(self, workspace_id: str) -> None:
        return None

    async def presence_put(self, workspace_id: str, connection_id: str, entry: dict) -> None:
        with self._lock:
            self._presence.setdefault(workspace_id, {})[connection_id] = dict(entry)

    async def presence_drop(self, workspace_id: str, connection_id: str) -> None:
        with self._lock:
            self._presence.get(workspace_id, {}).pop(connection_id, None)

    async def presence_all(self, workspace_id: str, now_epoch: float) -> tuple[list[dict], int]:
        with self._lock:
            entries = self._presence.get(workspace_id, {})
            live, stale = _fresh(entries, now_epoch)
            for connection_id in stale:
                entries.pop(connection_id, None)
        return live, len(stale)

    async def lead(self, workspace_id: str, owner: str, ttl_seconds: int) -> bool:
        return True

    async def swap_digest(self, workspace_id: str, digest: str) -> bool:
        with self._lock:
            before = self._digests.get(workspace_id)
            self._digests[workspace_id] = digest
        return before is not None and before != digest


class RedisBroker(Broker):
    """Redis pub/sub for events, a hash per workspace for presence, a lease per workspace for the leader."""

    kind = "redis"

    def __init__(self, url: str) -> None:
        super().__init__()
        self._url = url
        self._sync: Any = None
        self._async: Any = None
        self._pubsub: Any = None
        self._reader: Optional[asyncio.Task] = None
        self._channels: set[str] = set()
        self._sync_lock = threading.Lock()

    # -- publishing (sync, any thread) ---------------------------------------
    def _sync_client(self) -> Any:
        with self._sync_lock:
            if self._sync is None:
                import redis

                # Not the shared 0.25 s client: a publish is one round trip, but a
                # busy Redis must not turn a committed resolution into a timeout.
                self._sync = redis.Redis.from_url(self._url, socket_timeout=2.0, socket_connect_timeout=2.0,
                                                  decode_responses=True)
            return self._sync

    def publish(self, workspace_id: str, message: dict) -> None:
        self.recent.append((workspace_id, dict(message)))
        payload = json.dumps(message, separators=(",", ":"), default=str)
        try:
            self._sync_client().publish(v.channel(workspace_id), payload)
            self.degraded = False
        except Exception:  # noqa: BLE001
            self.degraded = True
            logger.warning("collab.redis_publish_failed", extra={"workspace_id": workspace_id}, exc_info=True)
            # This process's own reviewers still hear it.
            self._deliver_local(workspace_id, dict(message))

    # -- the async side (the hub's loop) --------------------------------------
    def _client(self) -> Any:
        if self._async is None:
            import redis.asyncio as aioredis

            self._async = aioredis.Redis.from_url(self._url, socket_connect_timeout=2.0, decode_responses=True,
                                                  health_check_interval=30)
        return self._async

    async def subscribe(self, workspace_id: str) -> None:
        channel = v.channel(workspace_id)
        if self._pubsub is None:
            self._pubsub = self._client().pubsub(ignore_subscribe_messages=True)
        await self._pubsub.subscribe(channel)
        self._channels.add(channel)
        if self._reader is None or self._reader.done():
            self._reader = asyncio.get_running_loop().create_task(self._read(), name="collab-redis-reader")

    async def unsubscribe(self, workspace_id: str) -> None:
        channel = v.channel(workspace_id)
        self._channels.discard(channel)
        if self._pubsub is not None:
            try:
                await self._pubsub.unsubscribe(channel)
            except Exception:  # noqa: BLE001
                logger.warning("collab.redis_unsubscribe_failed", exc_info=True)

    async def _read(self) -> None:
        backoff = 0.5
        while True:
            try:
                message = await self._pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if self.degraded:
                    self.degraded = False
                    for channel in list(self._channels):
                        self._deliver_local(channel[len(v.CHANNEL_PREFIX):],
                                            {"type": v.EVENT_QUEUE_CHANGED, "reason": "resync"})
                backoff = 0.5
                if message is None or message.get("type") != "message":
                    continue
                channel = str(message.get("channel") or "")
                if not channel.startswith(v.CHANNEL_PREFIX):
                    continue
                try:
                    data = json.loads(message.get("data") or "{}")
                except ValueError:
                    continue
                if isinstance(data, dict):
                    self._deliver_local(channel[len(v.CHANNEL_PREFIX):], data)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - reconnect: redis-py re-subscribes its channels
                self.degraded = True
                logger.warning("collab.redis_reader_error", exc_info=True)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 15.0)
                if self._channels and self._pubsub is not None:
                    try:
                        await self._pubsub.subscribe(*sorted(self._channels))
                    except Exception:  # noqa: BLE001
                        pass

    async def presence_put(self, workspace_id: str, connection_id: str, entry: dict) -> None:
        client = self._client()
        key = v.presence_key(workspace_id)
        await client.hset(key, connection_id, json.dumps(entry, separators=(",", ":"), default=str))
        await client.expire(key, 3600)

    async def presence_drop(self, workspace_id: str, connection_id: str) -> None:
        await self._client().hdel(v.presence_key(workspace_id), connection_id)

    async def presence_all(self, workspace_id: str, now_epoch: float) -> tuple[list[dict], int]:
        client = self._client()
        key = v.presence_key(workspace_id)
        raw = await client.hgetall(key)
        entries: dict[str, dict] = {}
        for connection_id, value in (raw or {}).items():
            try:
                parsed = json.loads(value)
            except ValueError:
                parsed = {}
            if isinstance(parsed, dict):
                entries[connection_id] = parsed
        live, stale = _fresh(entries, now_epoch)
        if stale:
            await client.hdel(key, *stale)
        return live, len(stale)

    async def lead(self, workspace_id: str, owner: str, ttl_seconds: int) -> bool:
        client = self._client()
        key = v.leader_key(workspace_id)
        if await client.set(key, owner, nx=True, px=int(ttl_seconds * 1000)):
            return True
        if await client.get(key) == owner:
            await client.pexpire(key, int(ttl_seconds * 1000))
            return True
        return False

    async def swap_digest(self, workspace_id: str, digest: str) -> bool:
        before = await self._client().set(v.digest_key(workspace_id), digest, ex=3600, get=True)
        return before is not None and before != digest

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:  # noqa: BLE001
                pass
        if self._async is not None:
            try:
                await self._async.aclose()
            except Exception:  # noqa: BLE001
                pass


_broker: Optional[Broker] = None
_broker_lock = threading.Lock()


def get_broker() -> Broker:
    """The process's broker: Redis when REDIS_URL is configured, else in process."""
    global _broker
    with _broker_lock:
        if _broker is None:
            from app.core.config import settings

            url = settings.REDIS_URL.get_secret_value() if settings.REDIS_URL else ""
            _broker = RedisBroker(url) if url else MemoryBroker()
        return _broker


def set_broker(broker: Optional[Broker]) -> Optional[Broker]:
    """Replace the process's broker (the gates); returns the previous one."""
    global _broker
    with _broker_lock:
        previous, _broker = _broker, broker
        return previous


__all__ = ["Broker", "MemoryBroker", "RedisBroker", "get_broker", "set_broker"]
