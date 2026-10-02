"""F-037 — refuse NUL characters in the URL, the query string and JSON/form bodies.

PostgreSQL text cannot contain the NUL character (U+0000). The database driver
refuses it with `ValueError: A string literal cannot contain NUL (0x00)
characters` while it builds a statement, which no handler expects, so the
request became an unhandled 500. Any signed-in user could trigger it from ~19
routes by putting `%00` in a search box, and it fills the error log and trips
alerts. Nothing legitimate contains a NUL, and it would never survive to a
column anyway, so it is refused at the edge with a 400 that says so, before it
reaches a handler, a log line or an object-storage key.

Binary bodies (uploads) are untouched: only JSON and URL-encoded bodies are
inspected, and only when they are small enough to buffer.
"""

from __future__ import annotations

import re
from typing import Awaitable, Callable

from starlette.types import ASGIApp, Message, Receive, Scope, Send

MAX_INSPECTED_BODY_BYTES = 2 * 1024 * 1024

#: `\u0000` spelled as a JSON escape, not preceded by an escaped backslash
#: (`\\u0000` is a backslash followed by the text "u0000", which is legitimate).
_JSON_NUL_ESCAPE = re.compile(rb"(?<!\\)(?:\\\\)*\\u0000")
_BODY = b'{"detail":"NUL characters are not allowed in a request."}'


def _has_nul_in_url(scope: Scope) -> bool:
    raw_path = scope.get("raw_path") or b""
    query = scope.get("query_string") or b""
    haystack = (raw_path + b"?" + query).lower()
    return b"\x00" in haystack or b"%00" in haystack


def _header(scope: Scope, name: bytes) -> bytes:
    for key, value in scope.get("headers", []):
        if key.lower() == name:
            return value
    return b""


class NulByteGuardMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        if _has_nul_in_url(scope):
            await self._refuse(send)
            return

        content_type = _header(scope, b"content-type").split(b";")[0].strip().lower()
        inspect = content_type == b"application/json" or content_type == b"application/x-www-form-urlencoded"
        try:
            declared = int(_header(scope, b"content-length") or b"0")
        except ValueError:
            declared = 0
        if not inspect or declared <= 0 or declared > MAX_INSPECTED_BODY_BYTES:
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunks.append(message.get("body", b""))
            more = message.get("more_body", False)
        body = b"".join(chunks)

        if b"\x00" in body or _JSON_NUL_ESCAPE.search(body) or (
            content_type == b"application/x-www-form-urlencoded" and b"%00" in body.lower()
        ):
            await self._refuse(send)
            return

        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    @staticmethod
    async def _refuse(send: Send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 400,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(_BODY)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": _BODY})


__all__ = ["NulByteGuardMiddleware"]
