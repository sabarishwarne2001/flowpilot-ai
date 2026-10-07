"""ASVS V8.2.1 — API responses are not stored by browsers or proxies unless they say so.

JSON from the API is tenant data. Endpoints that are meant to be cached (page images, logos,
avatars, rendered pages) set their own `Cache-Control`; every other response under `/api/` or
`/scim/` gets `Cache-Control: no-store`. A header a handler set is never overwritten.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_PREFIXES = ("/api/", "/scim/")


class NoStoreApiResponsesMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith(_PREFIXES):
            await self.app(scope, receive, send)
            return

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if "cache-control" not in headers:
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, send_with_header)


__all__ = ["NoStoreApiResponsesMiddleware"]
