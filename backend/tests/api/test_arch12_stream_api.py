"""Gates 12.2 and 12.6 — refusal semantics, concurrency slots, and the citation contract."""

from __future__ import annotations

import json
import uuid

import pytest

from app.core.exceptions import RateLimitExceededError
from app.schemas.citation import (
    CitationBoundingBox,
    CitationEnvelope,
    CitationSource,
)
from app.services import stream_concurrency


# ===========================================================================
# 12.2 — rate limiting on generation
# ===========================================================================


@pytest.mark.no_db
def test_concurrency_limits_come_from_settings():
    from app.core.config import settings

    limits = stream_concurrency.GenerationLimits.resolve()
    assert limits.concurrent_per_user == settings.STREAM_MAX_CONCURRENT_PER_USER
    assert limits.concurrent_per_user == 2, "A2 specifies two concurrent streams"
    assert limits.messages_per_minute_per_conversation == 10


@pytest.mark.no_db
def test_organization_refusal_releases_the_user_slot(monkeypatch):
    """An ordering bug here leaks a slot on every org-level refusal.

    The leak is invisible until the tenant can no longer start any stream at
    all, at which point the cause is several days behind the symptom.
    """
    released: list[str] = []
    acquired: list[str] = []

    def _acquire(key: str, limit: int) -> bool:
        acquired.append(key)
        return ":user:" in key  # user slot succeeds, org slot refuses

    monkeypatch.setattr(stream_concurrency, "_enabled", lambda: True)
    monkeypatch.setattr(stream_concurrency, "_acquire_slot", _acquire)
    monkeypatch.setattr(stream_concurrency, "_release_slot", released.append)
    monkeypatch.setattr(
        stream_concurrency, "check_message_rate", lambda *a, **k: None
    )

    user_id, org_id, conv_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    with pytest.raises(RateLimitExceededError) as excinfo:
        with stream_concurrency.generation_slot(
            user_id=user_id, organization_id=org_id, conversation_id=conv_id
        ):
            pytest.fail("the body must not run when the org slot is refused")

    assert excinfo.value.policy == "assistant_concurrent_org"
    assert any(":user:" in key for key in released), "the user slot leaked"


@pytest.mark.no_db
def test_slot_is_released_when_the_body_raises(monkeypatch):
    released: list[str] = []
    monkeypatch.setattr(stream_concurrency, "_enabled", lambda: True)
    monkeypatch.setattr(stream_concurrency, "_acquire_slot", lambda k, l: True)
    monkeypatch.setattr(stream_concurrency, "_release_slot", released.append)
    monkeypatch.setattr(stream_concurrency, "check_message_rate", lambda *a, **k: None)

    with pytest.raises(RuntimeError):
        with stream_concurrency.generation_slot(
            user_id=uuid.uuid4(),
            organization_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
        ):
            raise RuntimeError("provider died")

    assert len(released) == 2, "both slots must be released"


@pytest.mark.no_db
def test_rate_limit_error_carries_retry_after():
    error = RateLimitExceededError("slow down", retry_after=12, policy="x")
    assert error.response_headers["Retry-After"] == "12"


@pytest.mark.no_db
def test_limiter_fails_open_when_redis_is_gone(monkeypatch):
    """A cache outage must not become a full outage (ARCH-08 §B.5)."""
    monkeypatch.setattr(stream_concurrency, "_enabled", lambda: True)
    monkeypatch.setattr(stream_concurrency, "get_redis_client", lambda: None)
    monkeypatch.setattr(stream_concurrency, "check_message_rate", lambda *a, **k: None)

    entered = False
    with stream_concurrency.generation_slot(
        user_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        conversation_id=uuid.uuid4(),
    ):
        entered = True
    assert entered


# ===========================================================================
# 12.2 — endpoint-level refusal codes
# ===========================================================================


def test_quota_refusal_is_402_not_429(client, tenant, monkeypatch):
    from app.core.exceptions import SpendLimitExceededError
    from app.services.assistant_stream import assistant_stream_service

    def _refuse(*args, **kwargs):
        raise SpendLimitExceededError(
            limit_key="llm.output_token",
            period="MONTH",
            dimension="ORGANIZATION",
            ceiling="500000",
            current="500000",
            requested="1024",
        )

    monkeypatch.setattr(assistant_stream_service, "prepare", _refuse)

    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/assistant/conversations/{uuid.uuid4()}/messages/stream",
        json={"content": "What is the total?"},
        headers=tenant.contributor.headers,
    )
    assert response.status_code == 402


def test_missing_conversation_is_404(client, tenant):
    response = client.post(
        f"/api/v1/workspaces/{tenant.workspace.id}/assistant/conversations/{uuid.uuid4()}/messages/stream",
        json={"content": "hello"},
        headers=tenant.contributor.headers,
    )
    assert response.status_code == 404


def test_stream_response_disables_proxy_buffering():
    """Without X-Accel-Buffering, nginx holds tokens and TTFT is the buffer."""
    from app.api.v1 import assistant_stream as endpoint

    assert endpoint.SSE_HEADERS["X-Accel-Buffering"] == "no"
    assert "no-transform" in endpoint.SSE_HEADERS["Cache-Control"]


# ===========================================================================
# 12.6 — the citation contract
# ===========================================================================


@pytest.mark.no_db
def test_sse_frames_survive_newlines_in_tokens():
    from app.services.assistant_stream import sse

    frame = sse("token", {"text": "line one\n\nline two"})
    body = frame.decode("utf-8")

    assert body.startswith("event: token\ndata: ")
    assert body.endswith("\n\n")
    # Exactly one blank line — the frame terminator — and it is the last one.
    payload = body.split("data: ", 1)[1].rsplit("\n\n", 1)[0]
    assert json.loads(payload)["text"] == "line one\n\nline two"


@pytest.mark.no_db
def test_envelope_is_sealed_only_when_both_fields_present():
    base = {
        "message_id": uuid.uuid4(),
        "conversation_id": uuid.uuid4(),
        "context_hash": "sha256:" + "a" * 64,
    }

    assert not CitationEnvelope(**base).is_sealed
    assert CitationEnvelope(**base, audit_log_id=uuid.uuid4()).is_sealed
    assert not CitationEnvelope(
        message_id=base["message_id"],
        conversation_id=base["conversation_id"],
        audit_log_id=uuid.uuid4(),
    ).is_sealed


@pytest.mark.no_db
def test_context_hash_shape_is_enforced():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CitationEnvelope(
            message_id=uuid.uuid4(),
            conversation_id=uuid.uuid4(),
            context_hash="md5:deadbeef",
        )


@pytest.mark.no_db
def test_missing_bbox_yields_none_not_a_zero_rectangle():
    """A zero rectangle highlights the top-left corner and looks correct."""
    assert CitationBoundingBox.from_chunk_bbox(None) is None
    assert CitationBoundingBox.from_chunk_bbox({}) is None
    assert CitationBoundingBox.from_chunk_bbox({"x0": 1, "y0": 2}) is None


@pytest.mark.no_db
def test_bbox_round_trips_from_union_box_output():
    from app.services.document_models import BlockSpan, union_box

    spans = [
        BlockSpan(index=0, start=0, end=5, text="hello", box={"x0": 10.0, "y0": 20.0, "x1": 600.0, "y1": 88.0}),
        BlockSpan(index=1, start=6, end=11, text="world", box={"x0": 12.0, "y0": 90.0, "x1": 580.0, "y1": 140.0}),
    ]
    raw = union_box(spans, page_number=3, page_width=1700, page_height=2200)

    box = CitationBoundingBox.from_chunk_bbox(raw)
    assert box is not None
    assert (box.x0, box.y0, box.x1, box.y1) == (10.0, 20.0, 600.0, 140.0)
    assert box.width == 1700 and box.height == 2200
    assert box.space == "pixels"
    assert box.page == 3


@pytest.mark.no_db
def test_source_is_locatable_only_with_a_box_and_a_page():
    common = {
        "work_item_id": uuid.uuid4(),
        "original_filename": "invoice.pdf",
        "chunk_id": "abc_chunk_0",
        "chunk_index": 0,
        "snippet": "Total due 4,200.00",
    }
    assert not CitationSource(**common).is_locatable
    assert CitationSource(
        **common,
        page_number=3,
        bbox=CitationBoundingBox(x0=1, y0=2, x1=3, y1=4),
    ).is_locatable