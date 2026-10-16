"""The webhook settings page offers every event an endpoint can subscribe to.

`frontend/src/types/webhook.ts` says it mirrors `app/core/webhook_events.py::WEBHOOK_EVENT_TYPES`,
and the page draws one checkbox per name in it. A name the backend publishes but the page leaves
out is an event nobody can subscribe to from the product (only through the API). The one exception
is `workflow.triggered`: it is never fanned out to subscribers, a workflow rule names the endpoint
it posts to, so a checkbox for it would subscribe to nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.webhook_events import WEBHOOK_EVENT_TYPES

pytestmark = pytest.mark.no_db

SOURCE = Path(__file__).resolve().parents[3] / "frontend" / "src" / "types" / "webhook.ts"
#: Delivered only to the endpoint a workflow rule names (`webhook.send`), never to subscribers.
NOT_SUBSCRIBABLE = frozenset({"workflow.triggered"})


def _frontend_events() -> set[str]:
    text = SOURCE.read_text(encoding="utf-8")
    block = re.search(r"WEBHOOK_EVENT_TYPES\s*=\s*\[(.*?)\]", text, re.S)
    assert block, "WEBHOOK_EVENT_TYPES is no longer a literal list in webhook.ts"
    return set(re.findall(r'"([a-z_]+(?:\.[a-z_]+)+)"', block.group(1)))


@pytest.mark.skipif(not SOURCE.exists(), reason="frontend sources not in this checkout")
def test_the_settings_page_lists_every_subscribable_event() -> None:
    missing = sorted(set(WEBHOOK_EVENT_TYPES) - NOT_SUBSCRIBABLE - _frontend_events())
    assert missing == [], f"the webhook settings page cannot subscribe to: {missing}"


@pytest.mark.skipif(not SOURCE.exists(), reason="frontend sources not in this checkout")
def test_the_settings_page_lists_nothing_the_backend_refuses() -> None:
    unknown = sorted(_frontend_events() - set(WEBHOOK_EVENT_TYPES))
    assert unknown == [], f"the webhook settings page offers events the backend refuses: {unknown}"
