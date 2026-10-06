"""F-106: a client that hangs up must not cancel the email a request still owes.

uvicorn raises `ClientDisconnected` from `send()` once the peer has gone (and
then swallows it). Starlette runs a response's background tasks after the body
is sent, so that raise skipped them: a sign-up whose browser, phone or proxy
dropped the connection before the reply was written never got its verification
email, and nothing was logged. Seen in the browser suite: two of four sign-ups
in one run issued no token at all.

The test drives the real application over ASGI, with a `send` that behaves
like uvicorn's for a client that has already left.
"""

from __future__ import annotations

import asyncio
import json
import uuid

from uvicorn.protocols.utils import ClientDisconnected

import app.api.v1.auth as auth_routes
from app.main import app

API = "/api/v1"


def _post(path: str, body: dict, *, client_gone: bool) -> list[dict]:
    payload = json.dumps(body).encode()
    sent: list[dict] = []
    delivered = False

    async def receive():
        nonlocal delivered
        if not delivered:
            delivered = True
            return {"type": "http.request", "body": payload, "more_body": False}
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def send(message):
        if client_gone and message["type"] == "http.response.body":
            raise ClientDisconnected()
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"),
                    (b"content-length", str(len(payload)).encode())],
        "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
    }

    async def run():
        await asyncio.wait_for(app(scope, receive, send), 30)

    try:
        asyncio.run(run())
    except ClientDisconnected:
        pass  # what uvicorn does with it: swallow
    return sent


def test_a_sign_up_still_sends_its_verification_email_when_the_client_has_left(client, monkeypatch):
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(auth_routes, "_send_verification_safely", lambda **kwargs: calls.append(kwargs["user_id"]))
    email = f"gone-{uuid.uuid4().hex[:10]}@example.com"

    _post(f"{API}/auth/register", {"email": email, "password": "Disconnect-Passw0rd!"}, client_gone=True)

    assert len(calls) == 1, "the verification email was dropped because the client hung up"


def test_a_connected_client_still_gets_its_reply_and_the_email(client, monkeypatch):
    calls: list[uuid.UUID] = []
    monkeypatch.setattr(auth_routes, "_send_verification_safely", lambda **kwargs: calls.append(kwargs["user_id"]))
    email = f"stay-{uuid.uuid4().hex[:10]}@example.com"

    sent = _post(f"{API}/auth/register", {"email": email, "password": "Disconnect-Passw0rd!"}, client_gone=False)

    assert sent[0]["type"] == "http.response.start" and sent[0]["status"] == 202
    assert b"Check your email" in b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert len(calls) == 1
