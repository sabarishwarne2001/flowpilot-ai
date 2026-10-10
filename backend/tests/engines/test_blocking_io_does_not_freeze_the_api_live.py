"""F-218 — slow work inside the remaining async routes froze every other request.

    pytest tests/engines/test_blocking_io_does_not_freeze_the_api_live.py -q

F-212 turned the routes that never await into plain `def`. Twelve routes stayed
`async def` because they await something (an upload body, a stream, a webhook
body), but they still did their synchronous work (database queries and lock
waits, object-storage writes, PDF and image processing, the model call) on the
event-loop thread, before or after the await. While that work ran, the whole API
stood still: the ordinary assistant message, for one, ran retrieval and the
whole model call on the loop, up to 25 s per attempt.

Each case below slows one blocking step inside a route to 3 s, fires the route,
and requires /health (which stays on the loop) to answer in under a second
while the slow step runs. The route itself must still answer.
"""

from __future__ import annotations

import io
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest
from PIL import Image

from app.core.config import settings
from app.core.storage import reset_storage_driver
from tests.conftest import Fixture
from tests.engines.conftest import make_pdf
from tests.engines.test_concurrent_requests_do_not_freeze_the_api_live import (  # noqa: F401
    live_server,
)
from tests.security.plans import put_on_plan
from tests.security.test_billing_webhooks import (  # noqa: F401
    DODO_PATH,
    _dodo_event,
    _dodo_headers,
    _stripe_event,
    _stripe_signature,
    dodo_configured,
    stripe_configured,
)

API = "/api/v1"
SLOW_SECONDS = 3.0
HEALTH_BUDGET_SECONDS = 1.0


@pytest.fixture()
def local_storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # An upload enqueues extraction, which needs the worker's job types registered.
    from app.workers.handlers import register_all

    register_all(replace=True)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield tmp_path
    reset_storage_driver()


@dataclass
class Slow:
    """Replaces `owner.attribute` with a wrapper that signals, sleeps, then delegates."""

    reached: threading.Event

    def install(self, monkeypatch, owner: Any, attribute: str, result: Callable[..., Any] | None = None):
        original = getattr(owner, attribute)
        reached = self.reached

        def slow(*args, **kwargs):
            reached.set()
            time.sleep(SLOW_SECONDS)
            return result(*args, **kwargs) if result is not None else original(*args, **kwargs)

        monkeypatch.setattr(owner, attribute, slow)


def _png(size: int = 64) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (size, size), (20, 120, 200)).save(buffer, format="PNG")
    return buffer.getvalue()


def _fire_and_time_health(live: str, request: Callable[[httpx.Client], httpx.Response], reached: threading.Event):
    outcome: dict[str, Any] = {}

    def run() -> None:
        with httpx.Client(base_url=live, timeout=60) as http:
            try:
                outcome["response"] = request(http)
            except Exception as exc:  # noqa: BLE001
                outcome["error"] = repr(exc)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    assert reached.wait(20), f"the slow step was never reached: {outcome}"
    started = time.monotonic()
    with httpx.Client(base_url=live, timeout=10) as http:
        try:
            health = http.get(f"{API}/health").status_code
        except Exception as exc:  # noqa: BLE001
            health = repr(exc)
    elapsed = time.monotonic() - started
    worker.join(60)
    return health, elapsed, outcome


def _assert_responsive(health, elapsed, outcome, *, expect: tuple[int, ...]) -> None:
    assert health == 200 and elapsed < HEALTH_BUDGET_SECONDS, (
        f"/health took {elapsed:.2f}s (answer {health}) while the route's blocking step ran: "
        "the event loop was blocked"
    )
    response = outcome.get("response")
    assert response is not None, outcome
    assert response.status_code in expect, (response.status_code, response.text[:300])


def test_an_assistant_message(live_server, tenant: Fixture, monkeypatch) -> None:
    from app.services.retrieval_service import retrieval_service

    reached = threading.Event()
    Slow(reached).install(monkeypatch, retrieval_service, "hybrid_search", result=lambda *a, **k: [])
    with httpx.Client(base_url=live_server, timeout=30) as http:
        conversation = http.post(
            f"{API}/workspaces/{tenant.workspace.id}/assistant/conversations",
            json={"title": "freeze probe"}, headers=tenant.owner.headers,
        )
        assert conversation.status_code in (200, 201), conversation.text
    cid = conversation.json()["id"]
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/workspaces/{tenant.workspace.id}/assistant/conversations/{cid}/messages",
            json={"content": "What is the total?"}, headers=tenant.owner.headers,
        ),
        reached,
    )
    # No model is configured in the test environment, so the answer may be a refusal;
    # what matters is that the API stayed up while retrieval ran.
    _assert_responsive(health, elapsed, outcome, expect=(200, 400, 402, 422, 429, 503))


def test_a_document_upload(live_server, tenant: Fixture, local_storage, monkeypatch) -> None:
    from app.core.storage.local import LocalStorageDriver

    reached = threading.Event()
    Slow(reached).install(monkeypatch, LocalStorageDriver, "put_stream")
    pdf = make_pdf([["INVOICE", f"Invoice Number: FRZ-{uuid.uuid4().hex[:6]}"]])
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/workspaces/{tenant.workspace.id}/work-items",
            files={"file": ("freeze.pdf", pdf, "application/pdf")}, headers=tenant.owner.headers,
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(201,))


def test_an_avatar_upload(live_server, tenant: Fixture, local_storage, monkeypatch) -> None:
    from app.core.storage.local import LocalStorageDriver

    reached = threading.Event()
    Slow(reached).install(monkeypatch, LocalStorageDriver, "put")
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/me/avatar", files={"file": ("me.png", _png(256), "image/png")}, headers=tenant.owner.headers
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200, 201))


def test_an_upload_session_part(live_server, tenant: Fixture, local_storage, monkeypatch) -> None:
    from app.core.storage.local import LocalStorageDriver

    with httpx.Client(base_url=live_server, timeout=30) as http:
        created = http.post(
            f"{API}/workspaces/{tenant.workspace.id}/upload-sessions",
            json={"filename": "parts.pdf", "mime_type": "application/pdf"}, headers=tenant.owner.headers,
        )
        assert created.status_code in (200, 201), created.text
    session_id = created.json()["id"]
    reached = threading.Event()
    Slow(reached).install(monkeypatch, LocalStorageDriver, "upload_part")
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.put(
            f"{API}/workspaces/{tenant.workspace.id}/upload-sessions/{session_id}/parts/1",
            content=make_pdf([["part one"]]), headers={**tenant.owner.headers, "content-type": "application/octet-stream"},
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200,))


def test_an_automation_rule_test(
    live_server, db_session, tenant: Fixture, rule_factory, work_item_factory, monkeypatch
) -> None:
    from app.services.automation import flow_service

    rule = rule_factory(actions=[{"action_type": "add_tag", "config": {"tag": "probe"}}])
    work_item = work_item_factory()
    db_session.commit()
    reached = threading.Event()
    Slow(reached).install(monkeypatch, flow_service, "dry_run")
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/workspaces/{tenant.workspace.id}/automation/rules/{rule.id}/test",
            json={"work_item_id": str(work_item.id)}, headers=tenant.owner.headers,
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200,))


def test_a_stripe_webhook(live_server, stripe_configured, monkeypatch) -> None:  # noqa: F811
    from app.services.billing import inbound_service

    reached = threading.Event()
    Slow(reached).install(monkeypatch, inbound_service, "record_event")
    payload = _stripe_event()
    from tests.security.test_billing_webhooks import SECRET_A

    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/billing/webhooks/stripe", content=payload,
            headers={"content-type": "application/json", "Stripe-Signature": _stripe_signature(payload, SECRET_A)},
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200,))


def test_a_gateway_webhook(live_server, dodo_configured, monkeypatch) -> None:  # noqa: F811
    from app.services.billing import inbound_service

    reached = threading.Event()
    Slow(reached).install(monkeypatch, inbound_service, "persist_gateway_event")
    payload = _dodo_event()
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            DODO_PATH, content=payload,
            headers={"content-type": "application/json", **_dodo_headers(payload, webhook_id=f"msg_{uuid.uuid4().hex}")},
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200,))


def test_the_sign_in_check_every_request_makes(live_server, tenant: Fixture, monkeypatch) -> None:
    from app.api import deps

    reached = threading.Event()
    Slow(reached).install(monkeypatch, deps, "_session_is_revoked")
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.get(f"{API}/workspaces/{tenant.workspace.id}/work-items", headers=tenant.owner.headers),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200,))


def test_a_streamed_assistant_message(live_server, db_session, tenant: Fixture, monkeypatch) -> None:
    from app.models.ai_settings import AIProvider, AISettings
    from app.services.assistant_stream import assistant_stream_service

    # The stream refuses a workspace with no AI settings before it retrieves anything.
    db_session.add(AISettings(
        workspace_id=tenant.workspace.id, provider=AIProvider.GROQ, model="llama-3.1-8b-instant",
        temperature=0.2, max_output_tokens=512, top_p=1.0, frequency_penalty=0.0, presence_penalty=0.0,
    ))
    db_session.commit()
    reached = threading.Event()
    Slow(reached).install(monkeypatch, assistant_stream_service, "_retrieve", result=lambda *a, **k: [])
    with httpx.Client(base_url=live_server, timeout=30) as http:
        conversation = http.post(
            f"{API}/workspaces/{tenant.workspace.id}/assistant/conversations",
            json={"title": "stream probe"}, headers=tenant.owner.headers,
        )
        assert conversation.status_code in (200, 201), conversation.text
    cid = conversation.json()["id"]
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/workspaces/{tenant.workspace.id}/assistant/conversations/{cid}/messages/stream",
            json={"content": "What is the total?"}, headers=tenant.owner.headers,
        ),
        reached,
    )
    # As above: the answer may be a refusal without a configured model.
    _assert_responsive(health, elapsed, outcome, expect=(200, 400, 402, 404, 422, 429, 503))


def test_a_public_document_request_upload(live_server, db_session, tenant: Fixture, local_storage, monkeypatch) -> None:
    from app.services.cases import requests as document_requests

    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    workspace = f"{API}/workspaces/{tenant.workspace.id}"
    with httpx.Client(base_url=live_server, timeout=30, headers=tenant.owner.headers) as http:
        template = http.post(f"{workspace}/case-templates", json={
            "key": "freeze", "name": "Freeze", "rules": [],
            "required_documents": [{"doc_type": "invoice", "label": "Invoice"}],
        })
        assert template.status_code == 201, template.text
        assert http.post(f"{workspace}/case-templates/{template.json()['id']}/publish").status_code == 200
        case = http.post(f"{workspace}/cases", json={"template_id": template.json()["id"], "title": "Freeze probe"})
        assert case.status_code == 201, case.text
        created = http.post(f"{workspace}/cases/{case.json()['case']['id']}/requests",
                            json={"document_type": "invoice", "recipient_label": "Supplier"})
        assert created.status_code == 201, created.text
    token = created.json()["token"]
    reached = threading.Event()
    Slow(reached).install(monkeypatch, document_requests, "peek")
    pdf = make_pdf([["INVOICE", f"Invoice Number: REQ-{uuid.uuid4().hex[:6]}"]])
    health, elapsed, outcome = _fire_and_time_health(
        live_server,
        lambda http: http.post(
            f"{API}/public/document-requests/{token}", files={"file": ("invoice.pdf", pdf, "application/pdf")}
        ),
        reached,
    )
    _assert_responsive(health, elapsed, outcome, expect=(200, 422))
