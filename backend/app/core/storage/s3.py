"""S3-compatible object storage driver (ARCH-08 §B.11 Option A, ARCH-10 Step 4).

One driver implementation for AWS S3, Cloudflare R2, and MinIO.
"""

from __future__ import annotations

import logging
from typing import Any, BinaryIO, Optional

import boto3
from boto3.s3.transfer import TransferConfig
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

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

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}

DEFAULT_MULTIPART_THRESHOLD = 16 * 1024 * 1024
DEFAULT_MULTIPART_CHUNKSIZE = 16 * 1024 * 1024


class _S3ObjectReader:
    """Adapts botocore's StreamingBody to the BinaryIO shape the ABC promises."""

    def __init__(self, body: Any, key: str) -> None:
        self._body = body
        self._key = key

    def read(self, size: int = -1) -> bytes:
        return self._body.read() if size is None or size < 0 else self._body.read(size)

    def close(self) -> None:
        self._body.close()

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return False

    def __enter__(self) -> "_S3ObjectReader":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def __iter__(self) -> Any:
        return iter(self._body)


class S3CompatibleStorageDriver(StorageDriver):
    """One implementation for AWS S3, Cloudflare R2 and MinIO."""

    supports_presigned = True
    supports_multipart = True
    backend_name = "s3"

    _NO_SSE_FLAVORS = frozenset({"r2", "minio"})

    def __init__(
        self,
        *,
        bucket: str,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        prefix: str = "",
        sse: Optional[str] = "AES256",
        max_pool_connections: int = 20,
        flavor: str = "aws",
        multipart_threshold: int = DEFAULT_MULTIPART_THRESHOLD,
        multipart_chunksize: int = DEFAULT_MULTIPART_CHUNKSIZE,
        max_concurrency: int = 4,
        addressing_style: Optional[str] = None,
        client: Any = None,
    ) -> None:
        if not bucket:
            raise StorageError("S3CompatibleStorageDriver requires a bucket name")

        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._flavor = flavor.strip().lower()
        self.backend_name = self._flavor

        self._sse = None if self._flavor in self._NO_SSE_FLAVORS else sse

        self._transfer = TransferConfig(
            multipart_threshold=multipart_threshold,
            multipart_chunksize=multipart_chunksize,
            max_concurrency=max_concurrency,
            use_threads=max_concurrency > 1,
        )
        self._multipart_threshold = multipart_threshold

        if client is not None:
            self._client = client
            return

        s3_options: dict[str, Any] = {}
        if addressing_style is not None:
            s3_options["addressing_style"] = addressing_style
        elif self._flavor == "minio":
            s3_options["addressing_style"] = "path"

        self._client = boto3.client(
            "s3",
            region_name="auto" if self._flavor == "r2" else region,
            endpoint_url=endpoint_url,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                max_pool_connections=max_pool_connections,
                s3=s3_options or None,
                signature_version="s3v4",
            ),
        )

    def _object_key(self, key: str) -> str:
        safe = sanitize_key(key)
        return f"{self._prefix}/{safe}" if self._prefix else safe

    def _strip_prefix(self, object_key: str) -> str:
        if self._prefix and object_key.startswith(self._prefix + "/"):
            return object_key[len(self._prefix) + 1 :]
        return object_key

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        err = exc.response.get("Error", {})
        return (
            str(err.get("Code")) in _NOT_FOUND_CODES
            or exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
        )

    def _extra_args(self, mime_type: str) -> dict[str, str]:
        extra = {"ContentType": mime_type}
        if self._sse:
            extra["ServerSideEncryption"] = self._sse
        return extra

    def put(self, key: str, data: bytes, mime_type: str) -> str:
        object_key = self._object_key(key)
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": object_key,
            "Body": data,
            "ContentType": mime_type,
        }
        if self._sse:
            params["ServerSideEncryption"] = self._sse
        try:
            self._client.put_object(**params)
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to store object at {key!r}: {exc}") from exc
        return sanitize_key(key)

    def get(self, key: str) -> bytes:
        object_key = self._object_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
            with response["Body"] as body:
                return body.read()
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise StorageError(f"Failed to read object at {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to read object at {key!r}: {exc}") from exc

    def delete(self, key: str) -> bool:
        object_key = self._object_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if self._is_not_found(exc):
                return False
            raise StorageError(f"Failed to stat object at {key!r}: {exc}") from exc

        try:
            self._client.delete_object(Bucket=self._bucket, Key=object_key)
            return True
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to delete object at {key!r}: {exc}") from exc

    def exists(self, key: str) -> bool:
        try:
            object_key = self._object_key(key)
        except InvalidStorageKeyError:
            return False
        try:
            self._client.head_object(Bucket=self._bucket, Key=object_key)
            return True
        except ClientError as exc:
            if self._is_not_found(exc):
                return False
            raise StorageError(f"Failed to stat object at {key!r}: {exc}") from exc

    def stream(self, key: str) -> BinaryIO:
        object_key = self._object_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise StorageError(f"Failed to open object at {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to open object at {key!r}: {exc}") from exc
        return _S3ObjectReader(response["Body"], key)

    def size(self, key: str) -> int:
        object_key = self._object_key(key)
        try:
            response = self._client.head_object(Bucket=self._bucket, Key=object_key)
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise StorageError(f"Failed to stat object at {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to stat object at {key!r}: {exc}") from exc
        return int(response["ContentLength"])

    def put_stream(
        self,
        key: str,
        fileobj: BinaryIO,
        mime_type: str,
        *,
        content_length: Optional[int] = None,
        checksum_sha256: Optional[str] = None,
    ) -> StoredObject:
        object_key = self._object_key(key)
        reader = _HashingReader(fileobj) if checksum_sha256 is None else fileobj

        try:
            self._client.upload_fileobj(
                reader,
                self._bucket,
                object_key,
                ExtraArgs=self._extra_args(mime_type),
                Config=self._transfer,
            )
        except (ClientError, BotoCoreError) as exc:
            raise StorageError(f"Failed to stream object to {key!r}: {exc}") from exc

        if checksum_sha256 is None:
            digest = reader.hexdigest
            size = reader.bytes_read
        else:
            digest = checksum_sha256
            size = content_length if content_length is not None else self.size(key)

        multipart = size >= self._multipart_threshold
        logger.info(
            "storage.put_stream",
            extra={
                "backend": self.backend_name,
                "key": key,
                "size": size,
                "multipart": multipart,
            },
        )
        return StoredObject(
            key=sanitize_key(key),
            size=size,
            checksum_sha256=digest,
            mime_type=mime_type,
            multipart=multipart,
        )

    def download_to(self, key: str, destination: BinaryIO) -> int:
        object_key = self._object_key(key)
        try:
            self._client.download_fileobj(
                self._bucket, object_key, destination, Config=self._transfer
            )
        except ClientError as exc:
            if self._is_not_found(exc):
                raise ObjectNotFoundError(key) from exc
            raise StorageError(f"Failed to download {key!r}: {exc}") from exc
        except BotoCoreError as exc:
            raise StorageError(f"Failed to download {key!r}: {exc}") from exc
        destination.flush()
        return destination.tell()

    def presigned_get_url(self, key: str, *, expires_in: int = 900) -> Optional[str]:
        object_key = self._object_key(key)
        try:
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": object_key},
                ExpiresIn=int(expires_in),
            )
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "storage.presign_failed", extra={"key": key, "error": str(exc)}
            )
            return None

    def presigned_put_url(
        self, key: str, *, mime_type: str, expires_in: int = 900
    ) -> Optional[str]:
        object_key = self._object_key(key)
        params: dict[str, Any] = {
            "Bucket": self._bucket,
            "Key": object_key,
            "ContentType": mime_type,
        }
        if self._sse:
            params["ServerSideEncryption"] = self._sse
        try:
            return self._client.generate_presigned_url(
                "put_object", Params=params, ExpiresIn=int(expires_in)
            )
        except (ClientError, BotoCoreError) as exc:
            logger.warning(
                "storage.presign_put_failed", extra={"key": key, "error": str(exc)}
            )
            return None

    def iter_keys(self, prefix: str = "") -> list[str]:
        base = self._object_key(prefix) if prefix else self._prefix
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        for page in paginator.paginate(Bucket=self._bucket, Prefix=base):
            for item in page.get("Contents", []):
                keys.append(self._strip_prefix(item["Key"]))
        return sorted(keys)

    def usage_bytes(self, prefix: str = "") -> tuple[int, int]:
        base = self._object_key(prefix) if prefix else self._prefix
        paginator = self._client.get_paginator("list_objects_v2")
        total = 0
        count = 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=base):
            for item in page.get("Contents", []):
                total += int(item.get("Size", 0))
                count += 1
        return total, count

    def health(self) -> dict[str, object]:
        try:
            self._client.head_bucket(Bucket=self._bucket)
            return {
                "backend": self.backend_name,
                "bucket": self._bucket,
                "reachable": True,
            }
        except Exception as exc:
            return {
                "backend": self.backend_name,
                "bucket": self._bucket,
                "reachable": False,
                "error": str(exc),
            }


class S3StorageDriver(S3CompatibleStorageDriver):
    """AWS S3."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("flavor", "aws")
        super().__init__(**kwargs)


class R2StorageDriver(S3CompatibleStorageDriver):
    """Cloudflare R2."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["flavor"] = "r2"
        if not kwargs.get("endpoint_url"):
            raise StorageError(
                "R2StorageDriver requires S3_ENDPOINT_URL "
                "(https://<account_id>.r2.cloudflarestorage.com)"
            )
        super().__init__(**kwargs)


class MinIOStorageDriver(S3CompatibleStorageDriver):
    """MinIO for local development."""

    def __init__(self, **kwargs: Any) -> None:
        kwargs["flavor"] = "minio"
        if not kwargs.get("endpoint_url"):
            raise StorageError(
                "MinIOStorageDriver requires S3_ENDPOINT_URL (e.g. "
                "http://localhost:9000)"
            )
        super().__init__(**kwargs)
