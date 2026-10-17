"""A browser that hangs up while sending a JSON body is not a server error (website session).

`NulByteGuardMiddleware` reads small JSON bodies to refuse NUL characters. When the client
disconnected before the body arrived, the guard returned without answering, and the tracing
middleware above it (a Starlette BaseHTTPMiddleware) raised "RuntimeError: No response
returned." for a request nobody was waiting for: uvicorn logged it as "Exception in ASGI
application" with three tracebacks (seen once in a full browser run, when a test navigated away
during a POST). A departed client must end the request quietly, like any other hang-up
(F-106, `ClientGoneMiddleware`).
"""

from __future__ import annotations

import asyncio
import logging

import pytest

pytestmark = pytest.mark.no_db


def _scope(path: str) -> dict:
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"content-length", b"64"),
        ],
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
    }


def test_a_disconnect_before_the_body_ends_the_request_quietly(caplog: pytest.LogCaptureFixture) -> None:
    from app.main import app

    sent: list[dict] = []

    async def receive() -> dict:
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        sent.append(message)

    with caplog.at_level(logging.INFO):
        asyncio.run(app(_scope("/api/v1/auth/login"), receive, send))

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], [r.getMessage() for r in caplog.records]
    starts = [m for m in sent if m["type"] == "http.response.start"]
    assert len(starts) == 1 and starts[0]["status"] == 499, starts
