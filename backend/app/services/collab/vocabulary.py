"""ARCH48-S1:vocabulary — the live review protocol, its limits and its names. Pure.

`verify_arch48.py` loads this module without a database, a session or the app
settings, and compares it with the migration (anchor kinds, statuses, limits),
the console (`types/collab.ts`: event types, close codes, message types) and the
Caddyfile (the WebSocket path).

THE TIMES
=========

  LOCK_TTL_SECONDS         a lease lasts this long past its last heartbeat
  HEARTBEAT_SECONDS        the console pings this often; each ping extends
                           every lease the connection holds (heartbeat =
                           extend the lease) and refreshes its presence
  PRESENCE_TTL_SECONDS     a viewer not heard from for this long is gone
  SESSION_RECHECK_SECONDS  a live connection re-checks its session, its
                           workspace access and the capability this often;
                           a revoked device or a downgraded plan is closed

LOCK_TTL is three heartbeats, so one lost ping never costs a reviewer the lock,
and a closed laptop frees it within ninety seconds. The migration bounds any
lease to ten minutes past its heartbeat, so no bug here can make a lock outlive
its holder by more than that.
"""

from __future__ import annotations

from typing import Final

LOCK_TTL_SECONDS: Final[int] = 90
HEARTBEAT_SECONDS: Final[int] = 25
PRESENCE_TTL_SECONDS: Final[int] = 75
SESSION_RECHECK_SECONDS: Final[int] = 60
#: How often the per-workspace leader re-reads the queue digest and expires locks.
WATERMARK_SECONDS: Final[int] = 5
#: The lease bound the migration's CHECK enforces (minutes).
MAX_LEASE_MINUTES: Final[int] = 10

ANCHOR_ITEM: Final[str] = "ITEM"
ANCHOR_FIELD: Final[str] = "FIELD"
ANCHOR_PARAGRAPH: Final[str] = "PARAGRAPH"
ANCHOR_KINDS: Final[tuple[str, ...]] = (ANCHOR_ITEM, ANCHOR_FIELD, ANCHOR_PARAGRAPH)

THREAD_OPEN: Final[str] = "OPEN"
THREAD_RESOLVED: Final[str] = "RESOLVED"
THREAD_STATUSES: Final[tuple[str, ...]] = (THREAD_OPEN, THREAD_RESOLVED)

#: Where an anchored paragraph is now, relative to where it was written.
ANCHOR_CURRENT: Final[str] = "CURRENT"      # same page and index, same text
ANCHOR_MOVED: Final[str] = "MOVED"          # same text, found elsewhere
ANCHOR_OUTDATED: Final[str] = "OUTDATED"    # the text is no longer in the document
ANCHOR_STATES: Final[tuple[str, ...]] = (ANCHOR_CURRENT, ANCHOR_MOVED, ANCHOR_OUTDATED)

MAX_COMMENT_CHARS: Final[int] = 4000
MAX_QUOTE_CHARS: Final[int] = 500
MAX_MENTIONS: Final[int] = 20
MAX_FIELD_CHARS: Final[int] = 200
MAX_PARAGRAPHS: Final[int] = 2000
MAX_PARAGRAPH_CHARS: Final[int] = 2000
MAX_THREADS_PER_ITEM: Final[int] = 200

# ---------------------------------------------------------------------------
# The WebSocket
# ---------------------------------------------------------------------------

#: The subprotocol the console offers and the server selects. The bearer token
#: rides beside it as `bearer.<access token>`: a browser can set no
#: Authorization header on a WebSocket, a query string lands in every access
#: log, and the subprotocol list is the one header the browser lets a page set.
#: The server never echoes the token entry back; Caddy's access log deletes the
#: header (deploy/Caddyfile, ARCH48-S1:ws-log-redaction).
SUBPROTOCOL: Final[str] = "flowpilot.review.v1"
TOKEN_PREFIX: Final[str] = "bearer."
#: Mounted under /api/v1/workspaces/{workspace_id}/review.
WS_SUFFIX: Final[str] = "/collab/live"

MAX_MESSAGE_BYTES: Final[int] = 4096
#: Inbound messages per connection per RATE_WINDOW_SECONDS before it is closed.
RATE_MESSAGES: Final[int] = 40
RATE_WINDOW_SECONDS: Final[int] = 10
#: Live connections one person may hold per API process.
MAX_CONNECTIONS_PER_USER: Final[int] = 12
#: Viewers carried in one presence snapshot (a workspace with more is truncated, oldest first).
MAX_PRESENCE: Final[int] = 200

#: Close codes. 1008 is sent BEFORE accepting (the handshake is refused: no
#: session, no access, no capability, a host that is not ours); the 44xx codes
#: close a connection that WAS accepted.
CLOSE_POLICY: Final[int] = 1008
CLOSE_TOO_BIG: Final[int] = 1009
CLOSE_GOING_AWAY: Final[int] = 1001
CLOSE_SERVER_ERROR: Final[int] = 1011
CLOSE_SESSION_ENDED: Final[int] = 4401      # the token expired or the session was revoked: refresh, reconnect
CLOSE_ACCESS_LOST: Final[int] = 4403        # workspace access or the capability went away
CLOSE_RATE_LIMITED: Final[int] = 4429
CLOSE_CODES: Final[tuple[int, ...]] = (CLOSE_POLICY, CLOSE_TOO_BIG, CLOSE_GOING_AWAY, CLOSE_SERVER_ERROR,
                                       CLOSE_SESSION_ENDED, CLOSE_ACCESS_LOST, CLOSE_RATE_LIMITED)

#: What the console sends.
CLIENT_VIEW: Final[str] = "view"
CLIENT_PING: Final[str] = "ping"
CLIENT_LOCK: Final[str] = "lock"
CLIENT_UNLOCK: Final[str] = "unlock"
CLIENT_TYPES: Final[tuple[str, ...]] = (CLIENT_VIEW, CLIENT_PING, CLIENT_LOCK, CLIENT_UNLOCK)

#: What the server sends: replies to one connection ...
SERVER_HELLO: Final[str] = "hello"
SERVER_PONG: Final[str] = "pong"
SERVER_LOCK_RESULT: Final[str] = "lock.result"
SERVER_ERROR: Final[str] = "error"
#: ... and events fanned out to every connection in the workspace. Events carry
#: ids, never document content or comment text: a client re-reads through the
#: REST routes, which are authorised and gated. Redis carries no tenant data.
EVENT_PRESENCE: Final[str] = "presence"
EVENT_LOCK_ACQUIRED: Final[str] = "lock.acquired"
EVENT_LOCK_RELEASED: Final[str] = "lock.released"
EVENT_ITEM_RESOLVED: Final[str] = "item.resolved"
EVENT_ITEM_ASSIGNED: Final[str] = "item.assigned"
EVENT_QUEUE_CHANGED: Final[str] = "queue.changed"
EVENT_THREAD_CHANGED: Final[str] = "thread.changed"
#: ARCH49-S1:event-proposal-changed. The exception agent proposed, scheduled, applied or retired a
#: suggestion for an item (ids and a status only; the console re-reads the gated REST route).
EVENT_PROPOSAL_CHANGED: Final[str] = "proposal.changed"
EVENT_TYPES: Final[tuple[str, ...]] = (EVENT_PRESENCE, EVENT_LOCK_ACQUIRED, EVENT_LOCK_RELEASED,
                                       EVENT_ITEM_RESOLVED, EVENT_ITEM_ASSIGNED, EVENT_QUEUE_CHANGED,
                                       EVENT_THREAD_CHANGED, EVENT_PROPOSAL_CHANGED)
SERVER_TYPES: Final[tuple[str, ...]] = (SERVER_HELLO, SERVER_PONG, SERVER_LOCK_RESULT, SERVER_ERROR) + EVENT_TYPES

#: Why a lock went away.
RELEASE_RELEASED: Final[str] = "RELEASED"
RELEASE_EXPIRED: Final[str] = "EXPIRED"
RELEASE_RESOLVED: Final[str] = "RESOLVED"
RELEASE_FORCED: Final[str] = "FORCED"
RELEASE_DISCONNECTED: Final[str] = "DISCONNECTED"
RELEASE_REASONS: Final[tuple[str, ...]] = (RELEASE_RELEASED, RELEASE_EXPIRED, RELEASE_RESOLVED, RELEASE_FORCED,
                                           RELEASE_DISCONNECTED)

#: What happened to a thread.
THREAD_CREATED: Final[str] = "CREATED"
THREAD_COMMENTED: Final[str] = "COMMENTED"
THREAD_EDITED: Final[str] = "EDITED"
THREAD_DELETED_COMMENT: Final[str] = "COMMENT_DELETED"
THREAD_RESOLVED_ACTION: Final[str] = "RESOLVED"
THREAD_REOPENED: Final[str] = "REOPENED"
THREAD_ACTIONS: Final[tuple[str, ...]] = (THREAD_CREATED, THREAD_COMMENTED, THREAD_EDITED, THREAD_DELETED_COMMENT,
                                          THREAD_RESOLVED_ACTION, THREAD_REOPENED)

#: Refusal codes (409 bodies, bulk results, lock results).
CODE_STALE_VERSION: Final[str] = "STALE_VERSION"
CODE_LOCKED: Final[str] = "LOCKED"
CODE_LEASE_LOST: Final[str] = "LEASE_LOST"
CODE_ALREADY_RESOLVED: Final[str] = "ALREADY_RESOLVED"

#: Redis names. One channel per workspace; presence is one hash per workspace.
CHANNEL_PREFIX: Final[str] = "fp:collab:ws:"
PRESENCE_PREFIX: Final[str] = "fp:collab:presence:"
LEADER_PREFIX: Final[str] = "fp:collab:leader:"
DIGEST_PREFIX: Final[str] = "fp:collab:digest:"


def channel(workspace_id: object) -> str:
    return f"{CHANNEL_PREFIX}{workspace_id}"


def presence_key(workspace_id: object) -> str:
    return f"{PRESENCE_PREFIX}{workspace_id}"


def leader_key(workspace_id: object) -> str:
    return f"{LEADER_PREFIX}{workspace_id}"


def digest_key(workspace_id: object) -> str:
    return f"{DIGEST_PREFIX}{workspace_id}"


__all__ = [name for name in dir() if name.isupper() or name in ("channel", "presence_key", "leader_key", "digest_key")]
