"""Storage driver interface (ARCH-07 §B.8 Option A).
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import BinaryIO

MAX_KEY_LENGTH = 512
_SEGMENT = re.compile(r"^(?=.*[^.])[A-Za-z0-9._-]+$")


class StorageError(RuntimeError):
    """Base class for every storage fault."""


class InvalidStorageKeyError(StorageError, ValueError):
    """The key does not satisfy the key grammar."""


class ObjectNotFoundError(StorageError, KeyError):
    """No object exists at the given key."""


def sanitize_key(key: str) -> str:
    """Validate a storage key and return it normalised."""
    if not isinstance(key, str):
        raise InvalidStorageKeyError(f"Storage key must be str, got {type(key)!r}")
    if not key:
        raise InvalidStorageKeyError("Storage key must not be empty")
    if len(key) > MAX_KEY_LENGTH:
        raise InvalidStorageKeyError(
            f"Storage key exceeds {MAX_KEY_LENGTH} characters"
        )
    if "\x00" in key:
        raise InvalidStorageKeyError("Storage key must not contain NUL")
    if "\\" in key:
        raise InvalidStorageKeyError("Storage key must use '/' separators only")
    if key.startswith("/"):
        raise InvalidStorageKeyError("Storage key must be relative")

    segments = key.split("/")
    for segment in segments:
        if not segment:
            raise InvalidStorageKeyError(
                f"Storage key has an empty segment: {key!r}"
            )
        if not _SEGMENT.match(segment):
            raise InvalidStorageKeyError(
                f"Illegal storage key segment {segment!r} in {key!r}"
            )
    return "/".join(segments)


class StorageDriver(ABC):
    """Backend-agnostic object storage interface."""

    @abstractmethod
    def put(self, key: str, data: bytes, mime_type: str) -> str:
        """Store data at key, overwriting any existing object."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Return the whole object. Raises ObjectNotFoundError if absent."""

    @abstractmethod
    def delete(self, key: str) -> bool:
        """Remove the object. Returns True if it existed, False otherwise."""

    @abstractmethod
    def exists(self, key: str) -> bool:
        """True if an object is stored at key."""

    @abstractmethod
    def stream(self, key: str) -> BinaryIO:
        """Return an open binary reader positioned at byte 0."""

    @abstractmethod
    def size(self, key: str) -> int:
        """Return the object's size in bytes without reading it."""