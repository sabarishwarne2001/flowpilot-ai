"""Storage driver interface (ARCH-07 §B.8 Option A, extended by ARCH-10 Step 4).

The six original abstract methods are unchanged, so every existing caller and
every existing implementation keeps working untouched.

Everything ARCH-10 adds is concrete, with a default built on top of those six.
"""

from __future__ import annotations

import hashlib
import io
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import BinaryIO, Iterator, Optional

MAX_KEY_LENGTH = 512
_SEGMENT = re.compile(r"^(?=.*[^.])[A-Za-z0-9._-]+$")

#: Read granularity for every streaming copy in this module.
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024


class StorageError(RuntimeError):
    """Base class for every storage fault."""


class InvalidStorageKeyError(StorageError, ValueError):
    """The key does not satisfy the key grammar."""


class ObjectNotFoundError(StorageError, KeyError):
    """No object exists at the given key."""


class StorageCapabilityError(StorageError):
    """The backend cannot perform this operation, and never will."""


@dataclass(frozen=True)
class StoredObject:
    """What a durable write produced."""

    key: str
    size: int
    checksum_sha256: str
    mime_type: str
    multipart: bool = False


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


class _HashingReader:
    """Wraps a binary reader, hashing and counting bytes as they pass through."""

    def __init__(self, inner: BinaryIO) -> None:
        self._inner = inner
        self._digest = hashlib.sha256()
        self._count = 0
        self._start = inner.tell() if inner.seekable() else 0

    def read(self, size: int = -1) -> bytes:
        chunk = self._inner.read(size)
        if chunk:
            self._digest.update(chunk)
            self._count += len(chunk)
        return chunk

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        position = self._inner.seek(offset, whence)
        if position == self._start:
            self._digest = hashlib.sha256()
            self._count = 0
        return position

    def tell(self) -> int:
        return self._inner.tell()

    def seekable(self) -> bool:
        return self._inner.seekable()

    def readable(self) -> bool:
        return True

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    @property
    def bytes_read(self) -> int:
        return self._count


class StorageDriver(ABC):
    """Backend-agnostic object storage interface."""

    supports_presigned: bool = False
    supports_multipart: bool = False
    backend_name: str = "abstract"

    # ---- The six original abstract methods (ARCH-07 §B.8) ----------------

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

    # ---- ARCH-10 Step 4 additions, all concrete --------------------------

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        mime_type: str,
        *,
        content_length: Optional[int] = None,
        checksum_sha256: Optional[str] = None,
    ) -> StoredObject:
        reader = _HashingReader(fileobj)
        data = reader.read()
        stored_key = self.put(key, data, mime_type)
        return StoredObject(
            key=stored_key,
            size=reader.bytes_read,
            checksum_sha256=checksum_sha256 or reader.hexdigest,
            mime_type=mime_type,
            multipart=False,
        )

    def iter_chunks(
        self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE
    ) -> Iterator[bytes]:
        handle = self.stream(key)
        try:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    return
                yield chunk
        finally:
            close = getattr(handle, "close", None)
            if callable(close):
                close()

    def download_to(self, key: str, destination: BinaryIO) -> int:
        written = 0
        for chunk in self.iter_chunks(key):
            destination.write(chunk)
            written += len(chunk)
        destination.flush()
        return written

    def checksum(self, key: str) -> str:
        digest = hashlib.sha256()
        for chunk in self.iter_chunks(key):
            digest.update(chunk)
        return digest.hexdigest()

    def presigned_get_url(self, key: str, *, expires_in: int = 900) -> Optional[str]:
        return None

    def presigned_put_url(
        self, key: str, *, mime_type: str, expires_in: int = 900
    ) -> Optional[str]:
        return None

    def iter_keys(self, prefix: str = "") -> list[str]:
        raise StorageCapabilityError(
            f"{type(self).__name__} does not implement iter_keys()"
        )

    def usage_bytes(self, prefix: str = "") -> tuple[int, int]:
        total = 0
        count = 0
        for key in self.iter_keys(prefix):
            total += self.size(key)
            count += 1
        return total, count

    def health(self) -> dict[str, object]:
        return {"backend": self.backend_name, "reachable": True}