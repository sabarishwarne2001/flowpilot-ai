"""Local filesystem storage driver (ARCH-07 §B.8, ARCH-10 Step 4).

Development and single-host use only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import BinaryIO, Optional

from app.core.storage.base import (
    DEFAULT_CHUNK_SIZE,
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageDriver,
    StorageError,
    StoredObject,
    _HashingReader,
    sanitize_key,
)

logger = logging.getLogger(__name__)


class LocalStorageDriver(StorageDriver):
    """Objects as files beneath a single root directory."""

    supports_presigned = False
    supports_multipart = False
    backend_name = "local"

    def __init__(self, root: Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _resolve_key_path(self, key: str) -> Path:
        safe_key = sanitize_key(key)
        candidate = (self._root / safe_key).resolve(strict=False)

        if candidate == self._root or self._root not in candidate.parents:
            raise InvalidStorageKeyError(
                f"Storage key {key!r} resolves outside the storage root"
            )
        return candidate

    def put(self, key: str, data: bytes, mime_type: str) -> str:
        path = self._resolve_key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        handle = None
        temp_path: Optional[Path] = None
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".tmp-", suffix=".part"
            )
            temp_path = Path(temp_name)
            handle = os.fdopen(fd, "wb")
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None

            os.replace(temp_path, path)
            temp_path = None
            return sanitize_key(key)

        except OSError as exc:
            raise StorageError(f"Failed to store object at {key!r}: {exc}") from exc
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        path = self._resolve_key_path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to read object at {key!r}: {exc}") from exc

    def delete(self, key: str) -> bool:
        path = self._resolve_key_path(key)
        try:
            path.unlink()
            return True
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise StorageError(f"Failed to delete object at {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            return self._resolve_key_path(key).is_file()
        except InvalidStorageKeyError:
            return False

    def stream(self, key: str) -> BinaryIO:
        path = self._resolve_key_path(key)
        try:
            return path.open("rb")
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to open object at {key!r}: {exc}") from exc

    def size(self, key: str) -> int:
        path = self._resolve_key_path(key)
        try:
            return path.stat().st_size
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to stat object at {key!r}: {exc}") from exc

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        mime_type: str,
        *,
        content_length: Optional[int] = None,
        checksum_sha256: Optional[str] = None,
    ) -> StoredObject:
        path = self._resolve_key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)

        reader = _HashingReader(fileobj)
        handle = None
        temp_path: Optional[Path] = None
        written = 0
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".tmp-", suffix=".part"
            )
            temp_path = Path(temp_name)
            handle = os.fdopen(fd, "wb")
            while True:
                chunk = reader.read(DEFAULT_CHUNK_SIZE)
                if not chunk:
                    break
                handle.write(chunk)
                written += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None

            os.replace(temp_path, path)
            temp_path = None
        except OSError as exc:
            raise StorageError(f"Failed to stream object to {key!r}: {exc}") from exc
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        return StoredObject(
            key=sanitize_key(key),
            size=written,
            checksum_sha256=checksum_sha256 or reader.hexdigest,
            mime_type=mime_type,
            multipart=False,
        )

    def download_to(self, key: str, destination: BinaryIO) -> int:
        source = self._resolve_key_path(key)
        try:
            with source.open("rb") as handle:
                shutil.copyfileobj(handle, destination, DEFAULT_CHUNK_SIZE)
        except FileNotFoundError as exc:
            raise ObjectNotFoundError(key) from exc
        except OSError as exc:
            raise StorageError(f"Failed to download {key!r}: {exc}") from exc
        destination.flush()
        return destination.tell()

    def checksum(self, key: str) -> str:
        digest = hashlib.sha256()
        with self.stream(key) as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def iter_keys(self, prefix: str = "") -> list[str]:
        if prefix:
            base = (self._root / prefix.rstrip("/")).resolve(strict=False)
            if base != self._root and self._root not in base.parents:
                raise InvalidStorageKeyError(
                    f"Prefix {prefix!r} resolves outside the storage root"
                )
        else:
            base = self._root
        if not base.exists():
            return []
        keys: list[str] = []
        for path in sorted(base.rglob("*")):
            if path.is_file() and not path.name.startswith(".tmp-"):
                keys.append(path.relative_to(self._root).as_posix())
        return keys

    def usage_bytes(self, prefix: str = "") -> tuple[int, int]:
        total = 0
        count = 0
        for key in self.iter_keys(prefix):
            total += (self._root / key).stat().st_size
            count += 1
        return total, count

    def health(self) -> dict[str, object]:
        return {
            "backend": self.backend_name,
            "root": str(self._root),
            "reachable": self._root.is_dir() and os.access(self._root, os.W_OK),
        }
