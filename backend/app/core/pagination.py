"""Opaque keyset cursors (ARCH-08 §B.9).

THE ONLY MODULE IN app/ THAT ENCODES OR DECODES A PAGINATION CURSOR.

Why a compound (created_at, id) cursor and not `id < last_id`:
audit_logs.id is a random UUIDv4 with no ordering relationship to insertion time.
`id < last_id` compares two random 128-bit values and returns an arbitrary subset.
The correct predicate is the tuple comparison matching ORDER BY created_at DESC, id DESC.

Why the tuple and not `created_at` alone: TimestampMixin.created_at defaults
to func.now(), which PostgreSQL resolves as transaction_timestamp(). Every
audit row written by one request shares a created_at to the microsecond, so a
`created_at <` predicate drops every tied row.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

CURSOR_VERSION = 1
_DIGEST_BYTES = 8


class InvalidCursorError(ValueError):
    """The cursor is malformed, truncated, or of an unknown version."""


class CursorFilterMismatchError(ValueError):
    """The cursor was issued against a different filter set."""


@dataclass(frozen=True)
class KeysetCursor:
    created_at: datetime
    id: uuid.UUID
    filter_digest: str


def filter_digest(scope: Mapping[str, Any]) -> str:
    """Stable digest over a canonicalised filter mapping."""
    canonical = json.dumps(
        {key: _normalise(value) for key, value in sorted(scope.items())},
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.blake2b(
        canonical.encode("utf-8"), digest_size=_DIGEST_BYTES
    ).hexdigest()


def _normalise(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _to_utc(value).isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if hasattr(value, "value") and hasattr(value, "name"):
        return value.value
    return str(value)


def _to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidCursorError("Naive datetime in cursor scope.")
    return value.astimezone(timezone.utc)


def encode_cursor(*, created_at: datetime, id: uuid.UUID, digest: str) -> str:
    payload = {
        "v": CURSOR_VERSION,
        "c": _to_utc(created_at).isoformat(),
        "i": str(id),
        "f": digest,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode_cursor(token: str, *, expected_digest: str) -> KeysetCursor:
    padded = token + "=" * (-len(token) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("Cursor is not decodable.") from exc

    if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
        raise InvalidCursorError("Unsupported cursor version.")

    try:
        created_at = datetime.fromisoformat(payload["c"])
        entry_id = uuid.UUID(payload["i"])
        digest = str(payload["f"])
    except (KeyError, TypeError, ValueError) as exc:
        raise InvalidCursorError("Cursor payload is malformed.") from exc

    if created_at.tzinfo is None:
        raise InvalidCursorError("Cursor timestamp lost its timezone.")

    if digest != expected_digest:
        raise CursorFilterMismatchError(
            "Cursor was issued against a different filter set."
        )

    return KeysetCursor(
        created_at=created_at.astimezone(timezone.utc),
        id=entry_id,
        filter_digest=digest,
    )
