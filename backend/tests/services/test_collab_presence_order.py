"""F-229 — two presence announcements at once could publish out of order.

    pytest tests/services/test_collab_presence_order.py -q

F-218 moved the live-collaboration publish (a Redis call that can block for
2 s) off the event loop. Two announcements for one workspace then published
from two worker threads, so an older snapshot could land last: viewers saw
stale presence, and the hub's digest held the newer snapshot, so its
self-healing tick did not correct it. Announcements for a workspace now run
one at a time: snapshot, publish and digest.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from app.services.collab import broker as broker_module
from app.services.collab import hub as hub_module

pytestmark = pytest.mark.no_db


class SlowFirstBroker(broker_module.MemoryBroker):
    """The first snapshot is the older one, and its publish is the slow one."""

    def __init__(self) -> None:
        super().__init__()
        self.snapshots = [[{"connection_id": "a", "since": 1}], [{"connection_id": "a", "since": 1},
                                                                 {"connection_id": "b", "since": 2}]]
        self.published: list[list[dict]] = []

    async def presence_all(self, workspace_id: str, now_epoch: float):
        return self.snapshots.pop(0), 0

    def publish(self, workspace_id: str, message: dict) -> None:
        if len(message["viewers"]) == 1:
            time.sleep(0.3)
        self.published.append(message["viewers"])


def test_announcements_for_a_workspace_publish_in_order() -> None:
    broker = SlowFirstBroker()
    previous = broker_module.set_broker(broker)
    try:
        hub = hub_module.Hub()

        async def both() -> None:
            first = asyncio.create_task(hub.announce_presence("ws-1"))
            await asyncio.sleep(0)  # the first has taken its snapshot
            await asyncio.gather(first, hub.announce_presence("ws-1"))

        asyncio.run(both())
    finally:
        broker_module.set_broker(previous)

    assert [len(viewers) for viewers in broker.published] == [1, 2], "the older snapshot was published last"
    newest = [{k: e.get(k) for k in ("connection_id", "user_id", "name", "email", "kind", "item_id", "since")}
              for e in [{"connection_id": "a", "since": 1}, {"connection_id": "b", "since": 2}]]
    assert hub._presence_digest["ws-1"] == json.dumps(newest, sort_keys=True, default=str)
