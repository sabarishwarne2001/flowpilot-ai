"""ARCH-11 Step 7 — the internal service client for trusted first-party RPC."""

from __future__ import annotations

import http.client
import json
import logging
import socket
import time
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import urlsplit

from app.core.config import settings

logger = logging.getLogger("app.core.internal_http")

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class InternalServiceError(RuntimeError):
    """The internal service could not be reached or did not answer usefully."""


class InternalServiceTimeout(InternalServiceError):
    pass


class InternalServiceStatusError(InternalServiceError):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"internal service returned HTTP {status}: {body[:200]}")
        self.status = status


@dataclass(frozen=True)
class InternalResponse:
    status: int
    payload: Any
    elapsed_ms: float


def _assert_trusted_base(base_url: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in {"http", "https"}:
        raise InternalServiceError(
            f"internal base URL must be http or https, got {parsed.scheme!r}"
        )
    if not parsed.hostname:
        raise InternalServiceError("internal base URL has no hostname")
    if parsed.scheme == "http" and getattr(settings, "ENVIRONMENT", "") == "production":
        logger.warning(
            "internal_http.plaintext_in_production",
            extra={
                "host": parsed.hostname,
                "note": (
                    "the service token is transmitted in clear text on this "
                    "hop; use https:// or a service mesh with mTLS"
                ),
            },
        )
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return parsed.scheme, parsed.hostname, port, parsed.path.rstrip("/")


class InternalServiceClient:
    """JSON POST to one trusted first-party service. No redirects, no retries."""

    def __init__(
        self,
        *,
        name: str,
        base_url: str,
        token: Optional[str] = None,
        connect_timeout: float = 1.0,
        total_timeout: float = 5.0,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self.name = name
        self._scheme, self._host, self._port, self._base_path = _assert_trusted_base(
            base_url
        )
        self._token = token
        self._connect_timeout = connect_timeout
        self._total_timeout = total_timeout
        self._max_response_bytes = max_response_bytes

    def _connection(self) -> http.client.HTTPConnection:
        if self._scheme == "https":
            return http.client.HTTPSConnection(
                self._host, self._port, timeout=self._connect_timeout
            )
        return http.client.HTTPConnection(
            self._host, self._port, timeout=self._connect_timeout
        )

    def post_json(
        self, path: str, payload: dict[str, Any], *, request_id: Optional[str] = None
    ) -> InternalResponse:
        body = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Content-Length": str(len(body)),
            "Connection": "close",
        }
        if self._token:
            headers["X-Internal-Token"] = self._token
        if request_id:
            headers["X-Request-Id"] = request_id

        started = time.perf_counter()
        connection = self._connection()
        try:
            connection.request(
                "POST", f"{self._base_path}{path}", body=body, headers=headers
            )
            connection.sock.settimeout(self._total_timeout)  # type: ignore[union-attr]
            response = connection.getresponse()
            raw = response.read(self._max_response_bytes + 1)
            if len(raw) > self._max_response_bytes:
                raise InternalServiceError(
                    f"{self.name} response exceeded {self._max_response_bytes} bytes"
                )
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            if response.status >= 400:
                raise InternalServiceStatusError(
                    response.status, raw.decode("utf-8", "replace")
                )
            if response.status in {301, 302, 303, 307, 308}:
                raise InternalServiceError(
                    f"{self.name} returned a redirect; a first-party service "
                    "must not redirect and this client will not follow one"
                )

            try:
                parsed = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise InternalServiceError(
                    f"{self.name} returned a non-JSON body"
                ) from exc

            return InternalResponse(
                status=response.status, payload=parsed, elapsed_ms=elapsed_ms
            )
        except (socket.timeout, TimeoutError) as exc:
            raise InternalServiceTimeout(
                f"{self.name} exceeded {self._total_timeout}s"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise InternalServiceError(f"{self.name} unreachable: {exc}") from exc
        finally:
            try:
                connection.close()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "InternalResponse",
    "InternalServiceClient",
    "InternalServiceError",
    "InternalServiceStatusError",
    "InternalServiceTimeout",
]