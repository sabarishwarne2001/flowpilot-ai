"""Local filesystem storage driver (ARCH-07 §B.8, ARCH-10 Step 4).

Development and single-host use only.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import BinaryIO, Optional

from app.core.storage.base import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_PART_SIZE,
    InvalidStorageKeyError,
    MultipartUpload,
    ObjectNotFoundError,
    StorageDriver,
    StorageError,
    StoredObject,
    UploadedPart,
    _HashingReader,
    sanitize_key,
)

logger = logging.getLogger(__name__)

#: ARCH38-S1. Upload ids are minted here, so anything else is a caller
#: attempting to name a directory. 32 hex characters, nothing else.
_UPLOAD_ID = re.compile(r"^[0-9a-f]{32}$")


class LocalStorageDriver(StorageDriver):
    """Objects as files beneath a single root directory."""

    supports_presigned = False
    # ARCH38-S1:storage-multipart-local. Staged parts under STAGING_DIRNAME.
    supports_multipart = True
    backend_name = "local"

    #: Parts are staged inside the storage root rather than the OS temp
    #: directory: tests/test_storage_boundary.py (E10) forbids filesystem
    #: calls outside this package, and a part left in /tmp would survive a
    #: container restart that wiped the object it belonged to.
    STAGING_DIRNAME = "_multipart"

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

    # ---- ARCH-38 multipart -----------------------------------------------
    #
    # The development driver has to support the same protocol the browser
    # speaks against MinIO, or "resumable upload" would be a feature that only
    # exists in production and is therefore never exercised before it ships.
    # Parts are files under `_multipart/<upload_id>/<part_number>.part`;
    # completing concatenates them in part-number order and does one atomic
    # put, so a half-assembled object is never visible at the final key.

    def _staging_dir(self, upload_id: str) -> Path:
        if not _UPLOAD_ID.match(upload_id):
            raise StorageError(f"Illegal multipart upload id {upload_id!r}")
        return self._root / self.STAGING_DIRNAME / upload_id

    def create_multipart(
        self, key: str, mime_type: str, *, part_size: int = DEFAULT_PART_SIZE
    ) -> MultipartUpload:
        safe_key = sanitize_key(key)
        self.validate_part_size(part_size)
        upload_id = uuid.uuid4().hex
        staging = self._staging_dir(upload_id)
        staging.mkdir(parents=True, exist_ok=True)
        (staging / "key").write_text(safe_key, encoding="utf-8")
        return MultipartUpload(key=safe_key, upload_id=upload_id, part_size=part_size)

    def upload_part(
        self, key: str, upload_id: str, part_number: int, data: bytes
    ) -> UploadedPart:
        self.validate_part_number(part_number)
        staging = self._staging_dir(upload_id)
        if not staging.is_dir():
            raise ObjectNotFoundError(f"multipart upload {upload_id}")
        self._assert_staged_key(staging, key)
        target = staging / f"{part_number:05d}.part"
        # Write-then-rename, so a part is either wholly present or absent. A
        # retry of the same part number overwrites it: part uploads are
        # idempotent by part number, which is what makes resume safe.
        temp = staging / f".{part_number:05d}.tmp"
        temp.write_bytes(data)
        os.replace(temp, target)
        return UploadedPart(
            part_number=part_number,
            etag=hashlib.md5(data, usedforsecurity=False).hexdigest(),
            size=len(data),
        )

    def complete_multipart(
        self, key: str, upload_id: str, parts: list[UploadedPart], mime_type: str
    ) -> StoredObject:
        staging = self._staging_dir(upload_id)
        if not staging.is_dir():
            raise ObjectNotFoundError(f"multipart upload {upload_id}")
        self._assert_staged_key(staging, key)

        digest = hashlib.sha256()
        total = 0
        path = self._resolve_key_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Optional[Path] = None
        handle = None
        try:
            fd, temp_name = tempfile.mkstemp(
                dir=str(path.parent), prefix=".tmp-", suffix=".assembled"
            )
            temp_path = Path(temp_name)
            handle = os.fdopen(fd, "wb")
            for part in sorted(parts, key=lambda p: p.part_number):
                source = staging / f"{part.part_number:05d}.part"
                if not source.is_file():
                    raise StorageError(
                        f"multipart upload {upload_id} is missing part "
                        f"{part.part_number}"
                    )
                with source.open("rb") as reader:
                    while True:
                        chunk = reader.read(DEFAULT_CHUNK_SIZE)
                        if not chunk:
                            break
                        digest.update(chunk)
                        total += len(chunk)
                        handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
            handle.close()
            handle = None
            os.replace(temp_path, path)
            temp_path = None
        except OSError as exc:
            raise StorageError(f"Failed to assemble {key!r}: {exc}") from exc
        finally:
            if handle is not None:
                handle.close()
            if temp_path is not None and temp_path.exists():
                temp_path.unlink(missing_ok=True)

        self.abort_multipart(key, upload_id)
        return StoredObject(
            key=sanitize_key(key),
            size=total,
            checksum_sha256=digest.hexdigest(),
            mime_type=mime_type,
            multipart=True,
        )

    def abort_multipart(self, key: str, upload_id: str) -> None:
        staging = self._staging_dir(upload_id)
        if staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)

    @staticmethod
    def _assert_staged_key(staging: Path, key: str) -> None:
        """Refuse a part sent to an upload id that belongs to another key."""
        marker = staging / "key"
        if not marker.is_file():
            raise StorageError(f"multipart staging for {staging.name} has no key")
        recorded = marker.read_text(encoding="utf-8").strip()
        if recorded != sanitize_key(key):
            raise StorageError(
                f"multipart upload {staging.name} belongs to {recorded!r}, "
                f"not {key!r}"
            )

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
        staging_root = f"{self.STAGING_DIRNAME}/"
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.name.startswith(".tmp-"):
                continue
            relative = path.relative_to(self._root).as_posix()
            # ARCH38-S1:storage-multipart-local. A staged part is not an
            # object. Listing them would put them in usage_bytes() and in
            # ARCH-07's storage sampler, both of which count what a tenant
            # stores.
            if relative.startswith(staging_root):
                continue
            keys.append(relative)
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
