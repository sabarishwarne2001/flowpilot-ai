"""F-106 — a client that hangs up must not cancel the work a request still owes.

uvicorn raises `ClientDisconnected` (an `OSError`) from `send()` once the peer
has gone, and then swallows it. Starlette runs a response's background tasks
after the body is sent, so that raise skipped them: the verification,
password-reset and invitation emails of a request whose browser, phone or proxy
dropped the connection before the reply was written were never sent, and
nothing was logged.

This layer, outermost, turns a send to a departed client into a no-op so the
request runs to its end, background tasks included. A streaming response still
stops early: it watches `receive()` for the disconnect, which is untouched.
"""

from __future__ import annotations

import logging

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger("app.middleware.client_gone")


class ClientGoneMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        gone = False

        async def send_while_connected(message: Message) -> None:
            nonlocal gone
            if gone:
                return
            try:
                await send(message)
            except OSError:
                gone = True
                logger.info("http.client_gone_before_reply", extra={"path": scope.get("path")})

        await self.app(scope, receive, send_while_connected)
