"""S3 object storage driver (ARCH-08 §B.11 Option A).
"""

from __future__ import annotations

import logging
from typing import BinaryIO, Optional

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.storage.base import (
    InvalidStorageKeyError,
    ObjectNotFoundError,
    StorageDriver,
    StorageError,
    sanitize_key,
)

logger = logging.getLogger(__name__)

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}


class _S3ObjectReader:
    """Adapts botocore's StreamingBody to the BinaryIO shape the ABC promises."""

    def __init__(self, body, key: str) -> None:
        self._body = body
        self._key = key

    def read(self, size: int = -1) -> bytes:
        return self._body.read() if size is None or size < 0 else self._body.read(size)

    def close(self) -> None:
        self._body.close()

    def readable(self) -> bool:
        return True

    def __enter__(self) -> "_S3ObjectReader":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def __iter__(self):
        return iter(self._body)


class S3StorageDriver(StorageDriver):
    def __init__(
        self,
        *,
        bucket: str,
        region: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        prefix: str = "",
        sse: Optional[str] = "AES256",
        max_pool_connections: int = 20,
        client=None,
    ) -> None:
        if not bucket:
            raise StorageError("S3StorageDriver requires a bucket name")
        self._bucket = bucket
        self._prefix = prefix.strip("/")
        self._sse = sse
        self._client = client or boto3.client(
            "s3",
            region_name=region,
            endpoint_url=endpoint_url,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                max_pool_connections=max_pool_connections,
            ),
        )

    def _object_key(self, key: str) -> str:
        safe = sanitize_key(key)
        return f"{self._prefix}/{safe}" if self._prefix else safe

    @staticmethod
    def _is_not_found(exc: ClientError) -> bool:
        err = exc.response.get("Error", {})
        return (
            str(err.get("Code")) in _NOT_FOUND_CODES
            or exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404
        )

    # ---- The 6 ABC Methods ----

    def put(self, key: str, data: bytes, mime_type: str) -> str:
        object_key = self._object_key(key)
        params = {
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

    def iter_keys(self, prefix: str = "") -> list[str]:
        base = self._object_key(prefix) if prefix else self._prefix
        paginator = self._client.get_paginator("list_objects_v2")
        keys: list[str] = []
        strip = len(self._prefix) + 1 if self._prefix else 0
        for page in paginator.paginate(Bucket=self._bucket, Prefix=base):
            for item in page.get("Contents", []):
                k = item["Key"]
                keys.append(k[strip:] if strip > 0 and k.startswith(self._prefix) else k)
        return sorted(keys)