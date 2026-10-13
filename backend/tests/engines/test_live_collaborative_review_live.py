"""Two reviewers on the live review channel at once (campaign session 4, ledger).

    pytest tests/engines/test_live_collaborative_review_live.py -q

`WS …/review/collab/live` had its handshake refusals proven (sign-in policy, token not in the URL),
never a session with two people on it. Here a workspace admin and a contributor open the channel on
an Enterprise workspace with two documents held for review:

* presence: each sees the other arrive, and which item the other has open;
* one item, two reviewers: the first takes the item's soft lock, the second is told who holds it
  (over the channel and from the resolve route, 409 LOCKED); the holder resolves it, the lock goes
  with the decision, the second reviewer is told, and their stale decision is refused;
* losing access mid-session: an admin revokes the contributor's workspace access while they hold a
  lock; within one re-check the channel is closed (4403), they leave the presence list, and the lock
  they held is released at once, so the item is not shown as "being decided" by someone who can no
  longer decide it.
"""

from __future__ import annotations

import json
from typing import Any, Callable

import anyio
import pytest
from starlette.websockets import WebSocketDisconnect

from app.services.collab import vocabulary as v
from tests.engines.conftest import Engines
from tests.engines.test_review_hub_live import BASE, _held


@pytest.fixture(autouse=True)
def _fresh_hub(monkeypatch: pytest.MonkeyPatch):
    from app.services.collab import hub as hub_module

    # Re-check every second rather than every minute, so "within one re-check" is quick to observe.
    monkeypatch.setattr(v, "SESSION_RECHECK_SECONDS", 1)
    previous = hub_module.set_hub(None)
    yield
    hub_module.set_hub(previous)


def _open(engines: Engines, persona) -> Any:
    path = f"/api/v1/workspaces/{engines.ws}/review{v.WS_SUFFIX}"
    return engines.client.websocket_connect(path, subprotocols=[v.SUBPROTOCOL, f"{v.TOKEN_PREFIX}{persona.token}"])


def _next(ws: Any, seconds: float = 10.0) -> dict:
    """The next frame, or the close code as WebSocketDisconnect; never waits forever."""
    async def receive() -> dict:
        with anyio.fail_after(seconds):
            return await ws._send_rx.receive()  # noqa: SLF001 - the test client's receive, with a deadline

    message = ws.portal.call(receive)
    if message["type"] == "websocket.close":
        raise WebSocketDisconnect(message.get("code", 1000), message.get("reason"))
    return json.loads(message["text"])


def _until(ws: Any, kind: str, match: Callable[[dict], bool] = lambda m: True, frames: int = 40) -> dict:
    seen = []
    for _ in range(frames):
        message = _next(ws)
        if message["type"] == kind and match(message):
            return message
        seen.append(message["type"])
    raise AssertionError(f"no {kind} frame; saw {seen}")


def _viewing(user_id: str, item_id: str | None) -> Callable[[dict], bool]:
    return lambda m: any(e["user_id"] == user_id and e["item_id"] == item_id for e in m["viewers"])


def test_two_reviewers_share_one_queue_live(engines: Engines) -> None:
    engines.plan("enterprise")  # real-time collaborative review is an Enterprise capability
    assert engines.put("/document-settings/", {"verification_enabled": True, "verification_agents": 2}).status_code == 200
    held = {_held(engines, n) for n in (41, 42)}
    items = [i for i in engines.get("/review", params={"kind": "EXTRACTION"}).json()["items"]
             if str(i["work_item_id"]) in held]
    assert len(items) == 2, items
    first, second = items
    admin, contributor = engines.tenant.ws_admin, engines.tenant.contributor
    admin_id, contributor_id = str(admin.user.id), str(contributor.user.id)

    with _open(engines, admin) as a:
        hello = _next(a)
        assert hello["type"] == v.SERVER_HELLO and hello["you"]["user_id"] == admin_id, hello
        assert "EXTRACTION" in hello["allowed_kinds"]

        with _open(engines, contributor) as b:
            assert _next(b)["type"] == v.SERVER_HELLO
            # Presence: the admin sees the contributor arrive, then sees which item they opened.
            _until(a, v.EVENT_PRESENCE, _viewing(contributor_id, None))
            b.send_json({"type": v.CLIENT_VIEW, "kind": "EXTRACTION", "item_id": first["item_id"]})
            _until(a, v.EVENT_PRESENCE, _viewing(contributor_id, first["item_id"]))
            state = engines.get("/review/collab/state")
            assert {e["user_id"] for e in state.json()["presence"]} >= {admin_id, contributor_id}, state.text

            # One item, two reviewers: the admin takes the lock; the contributor is told who holds it.
            a.send_json({"type": v.CLIENT_LOCK, "kind": "EXTRACTION", "item_id": first["item_id"]})
            assert _until(a, v.SERVER_LOCK_RESULT)["ok"] is True
            acquired = _until(b, v.EVENT_LOCK_ACQUIRED, lambda m: m["item_id"] == first["item_id"])
            assert str(acquired["holder_user_id"]) == admin_id, acquired
            b.send_json({"type": v.CLIENT_LOCK, "kind": "EXTRACTION", "item_id": first["item_id"]})
            refused = _until(b, v.SERVER_LOCK_RESULT)
            assert refused["ok"] is False and refused["code"] == v.CODE_LOCKED, refused
            assert refused["holder"]["user_id"] == admin_id, refused
            underneath = engines.post(f"/review/EXTRACTION/{first['item_id']}/resolve",
                                      {"values": {"invoice_number": "INV-RH-41"},
                                       "expected_version": first["version"]}, as_user=contributor)
            assert underneath.status_code == 409 and underneath.json()["code"] == "LOCKED", underneath.text

            # The holder decides; the lock goes with the decision and the other reviewer hears both.
            a.send_json({"type": v.CLIENT_PING})
            assert _until(a, v.SERVER_PONG)["leases"], "a heartbeat extends the lease the admin holds"
            # (An Enterprise workspace asks the reviewer to confirm every field, not only the disputed one.)
            decided = engines.post(f"/review/EXTRACTION/{first['item_id']}/resolve",
                                   {"values": {**BASE, "invoice_number": "INV-RH-41"},
                                    "expected_version": first["version"]},
                                   as_user=admin)
            assert decided.status_code == 200, decided.text
            released = _until(b, v.EVENT_LOCK_RELEASED, lambda m: m["item_id"] == first["item_id"])
            assert released["reason"] == v.RELEASE_RESOLVED, released
            _until(b, v.EVENT_ITEM_RESOLVED, lambda m: m["item_id"] == first["item_id"])
            stale = engines.post(f"/review/EXTRACTION/{first['item_id']}/resolve",
                                 {"values": {**BASE, "invoice_number": "PO-RH-41"}, "expected_version": first["version"]},
                                 as_user=contributor)
            assert stale.status_code == 409, stale.text
            assert stale.json()["code"] in (v.CODE_STALE_VERSION, v.CODE_ALREADY_RESOLVED), stale.text

            # Losing access mid-session: the contributor holds the second item when access is revoked.
            b.send_json({"type": v.CLIENT_LOCK, "kind": "EXTRACTION", "item_id": second["item_id"]})
            assert _until(b, v.SERVER_LOCK_RESULT)["ok"] is True
            _until(a, v.EVENT_LOCK_ACQUIRED, lambda m: m["item_id"] == second["item_id"])

            from app.models.workspace import WorkspaceMember

            grant = engines.db.query(WorkspaceMember).filter_by(workspace_id=engines.ws,
                                                                user_id=contributor.user.id).one()
            revoked = engines.post(f"/members/{grant.id}/revoke", as_user=admin)
            assert revoked.status_code == 200, revoked.text
            with pytest.raises(WebSocketDisconnect) as closed:
                _until(b, "never")
            assert closed.value.code == v.CLOSE_ACCESS_LOST, closed.value.code

        # The admin sees them leave, and the item they held is free at once, not in ninety seconds.
        gone = _until(a, v.EVENT_LOCK_RELEASED, lambda m: m["item_id"] == second["item_id"])
        assert str(gone["holder_user_id"]) == contributor_id, gone
        _until(a, v.EVENT_PRESENCE, lambda m: contributor_id not in {e["user_id"] for e in m["viewers"]})
        a.send_json({"type": v.CLIENT_LOCK, "kind": "EXTRACTION", "item_id": second["item_id"]})
        taken = _until(a, v.SERVER_LOCK_RESULT)
        assert taken["ok"] is True, taken
        # A workspace someone no longer belongs to reads as not found to them, as on every route.
        assert engines.get("/review/collab/state", as_user=contributor).status_code == 404
