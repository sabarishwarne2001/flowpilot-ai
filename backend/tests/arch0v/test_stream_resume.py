"""ARCH-0V Tranche 6 — the A13 resume test that did not exist.

At ARCH-22 completion, `grep -rln "from_seq" tests/` returned nothing. The
resume endpoint existed, its contract was documented in the route description
("Replays buffered frames with seq > from_seq. Never re-invokes the model"),
and no test asserted any part of it.

WHY THIS RUNS AGAINST A FAKE REDIS AND NOT THE TEST CLIENT

`tests/conftest.py` requires a live Postgres and builds a full tenant fixture.
The replay path touches neither: it reads three Redis keys and yields bytes.
Driving it through the API would test the auth dependency chain and the
workspace scoping — both already covered — while adding a database dependency
to a test about a Redis buffer. Same reasoning as ARCH-19, where the rate-limit
tests were driven against `InMemoryBackend` directly because the harness
bypasses middleware.

THE TWO ASSERTIONS THAT MATTER

  1. Resuming at N yields exactly N+1.. and nothing else.
  2. The provider is invoked exactly ONCE across both connections.

The second is the one worth having. The first can pass while the model is
silently re-invoked and the tenant billed twice — the resume endpoint's whole
reason for existing is that the original generation was already paid for.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, Optional

import pytest

pytestmark = pytest.mark.no_db


# ---------------------------------------------------------------------------
# A Redis double. Implements exactly the surface StreamFrameBuffer and
# replay() use, and nothing else — a fuller fake would hide a call this code
# is not supposed to make.
# ---------------------------------------------------------------------------
class FakeRedis:
    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.lists: dict[str, list[str]] = {}
        self.read_failures = 0

    # -- strings ------------------------------------------------------
    def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        self.strings[key] = str(value)
        return True

    def get(self, key: str) -> Optional[str]:
        if self.read_failures:
            self.read_failures -= 1
            raise ConnectionError("simulated Redis read failure")
        return self.strings.get(key)

    def delete(self, key: str) -> int:
        self.strings.pop(key, None)
        return int(bool(self.lists.pop(key, None)))

    # -- lists --------------------------------------------------------
    def rpush(self, key: str, value: str) -> int:
        self.lists.setdefault(key, []).append(value)
        return len(self.lists[key])

    def llen(self, key: str) -> int:
        return len(self.lists.get(key, []))

    def lrange(self, key: str, start: int, end: int) -> list[str]:
        items = self.lists.get(key, [])
        if end == -1:
            return items[start:]
        return items[start : end + 1]

    def expire(self, key: str, seconds: int) -> bool:
        return True

    # -- pipeline: executes eagerly, which is fine for these assertions --
    def pipeline(self) -> "FakePipeline":
        return FakePipeline(self)

    # -- test helpers -------------------------------------------------
    def evict_frames(self, message_id: uuid.UUID) -> None:
        """Simulate the frames list being evicted while the state key lives.

        Reachable three ways in production: `_disable()` on overflow stops
        refreshing the frames TTL but still writes CLOSED; Redis evicts the
        large list before the small string under `allkeys-lru`; and ARCH-19
        Sentinel failover replicates the two keys asynchronously.
        """
        self.lists.pop(f"stream:frames:{message_id}", None)


class FakePipeline:
    def __init__(self, client: FakeRedis) -> None:
        self._client = client
        self._queued: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def queue(*args: Any, **kwargs: Any) -> "FakePipeline":
            self._queued.append((name, args, kwargs))
            return self

        return queue

    def execute(self) -> list[Any]:
        results = []
        for name, args, kwargs in self._queued:
            results.append(getattr(self._client, name)(*args, **kwargs))
        self._queued.clear()
        return results


@pytest.fixture()
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    client = FakeRedis()
    import app.services.assistant_stream as module

    monkeypatch.setattr(module, "get_redis_client", lambda: client)
    return client


@pytest.fixture()
def buffered_turn(fake_redis: FakeRedis):
    """A completed five-frame turn, sealed the way stream_answer seals one."""
    import app.services.assistant_stream as module

    message_id = uuid.uuid4()
    buffer = module.StreamFrameBuffer(message_id=message_id)
    buffer.open()

    for seq in range(1, 6):
        buffer.append(seq=seq, event="token", data={"text": f"chunk-{seq}"})
    buffer.close(last_seq=5)

    return message_id, buffer


def _collect(agen) -> list[dict]:
    """Drain an async generator of SSE bytes into parsed frames."""

    async def run() -> list[dict]:
        frames = []
        async for chunk in agen:
            text = chunk.decode("utf-8")
            for line in text.splitlines():
                if line.startswith("data: "):
                    frames.append(json.loads(line[len("data: ") :]))
        return frames

    return asyncio.run(run())


# ===========================================================================
# The contract
# ===========================================================================


def test_resume_yields_exactly_frames_after_cursor(buffered_turn, fake_redis):
    """0V-G12, assertion 1: resuming at N yields exactly N+1..end."""
    from app.services.assistant_stream import assistant_stream_service

    message_id, _ = buffered_turn

    frames = _collect(
        assistant_stream_service.replay(message_id=message_id, from_seq=2)
    )

    seqs = [frame["seq"] for frame in frames if "seq" in frame]
    assert seqs == [3, 4, 5], (
        f"Expected exactly frames 3,4,5 after from_seq=2; got {seqs}. A "
        f"duplicate means the client renders text twice; a gap means it "
        f"renders a truncated answer."
    )


def test_resume_from_zero_replays_the_whole_turn(buffered_turn, fake_redis):
    from app.services.assistant_stream import assistant_stream_service

    message_id, _ = buffered_turn
    frames = _collect(
        assistant_stream_service.replay(message_id=message_id, from_seq=0)
    )
    assert [f["seq"] for f in frames if "seq" in f] == [1, 2, 3, 4, 5]


def test_resume_never_invokes_the_provider(buffered_turn, fake_redis, monkeypatch):
    """0V-G12, assertion 2 — the one that matters.

    The original generation was already metered and billed. A resume that
    re-invokes the model bills the tenant twice for one answer, and because
    the replayed text would look correct, nothing downstream would notice.
    """
    import app.services.llm_stream as llm_stream

    calls: list[str] = []

    def exploding_provider_stream(*args: Any, **kwargs: Any):
        calls.append("provider_stream")
        raise AssertionError(
            "replay() invoked the LLM provider. The turn was already billed; "
            "resuming must read the buffer and nothing else."
        )

    monkeypatch.setattr(llm_stream, "provider_stream", exploding_provider_stream)

    from app.services.assistant_stream import assistant_stream_service

    message_id, _ = buffered_turn
    frames = _collect(
        assistant_stream_service.replay(message_id=message_id, from_seq=1)
    )

    assert calls == [], f"provider was invoked during replay: {calls}"
    assert [f["seq"] for f in frames if "seq" in f] == [2, 3, 4, 5]


# ===========================================================================
# D-0V.2 — the degradation must be explicit
# ===========================================================================


def test_lost_frames_refuse_rather_than_claim_currency(buffered_turn, fake_redis):
    """The ARCH-0V fix, asserted directly.

    Through ARCH-22 this exact state produced an empty generator, which the
    router turned into `finish_reason: "already_current"` — telling the client
    it held the complete turn while it held two frames of five.
    """
    from app.services.assistant_stream import (
        ReplayIncompleteError,
        assistant_stream_service,
    )

    message_id, _ = buffered_turn
    fake_redis.evict_frames(message_id)

    with pytest.raises(ReplayIncompleteError):
        _collect(
            assistant_stream_service.replay(message_id=message_id, from_seq=2)
        )


def test_client_already_current_is_not_an_error(buffered_turn, fake_redis):
    """The other side of the same coin.

    A client that genuinely holds all five frames must NOT get a refusal —
    otherwise the fix above turns every clean reconnect into an error, and the
    UI refetches a message it already has.
    """
    from app.services.assistant_stream import assistant_stream_service

    message_id, _ = buffered_turn
    frames = _collect(
        assistant_stream_service.replay(message_id=message_id, from_seq=5)
    )
    assert frames == [], (
        "A caller that is genuinely current must receive no frames and no "
        "exception. The router renders that as already_current, which is only "
        "truthful when last_seq proves it."
    )


def test_buffer_disabled_midturn_cannot_claim_completeness(fake_redis):
    """A turn whose buffer was disabled has no provable last_seq."""
    import app.services.assistant_stream as module
    from app.services.assistant_stream import (
        ReplayIncompleteError,
        assistant_stream_service,
    )

    message_id = uuid.uuid4()
    buffer = module.StreamFrameBuffer(message_id=message_id)
    buffer.open()
    buffer.append(seq=1, event="token", data={"text": "a"})
    buffer._disable("overflow")

    with pytest.raises(ReplayIncompleteError):
        _collect(
            assistant_stream_service.replay(message_id=message_id, from_seq=1)
        )


def test_no_buffer_at_all_is_unavailable_not_incomplete(fake_redis):
    """An expired turn is 404 territory, not 409 — the distinction is the point."""
    from app.services.assistant_stream import (
        ReplayIncompleteError,
        ReplayUnavailableError,
        assistant_stream_service,
    )

    with pytest.raises(ReplayUnavailableError) as caught:
        _collect(
            assistant_stream_service.replay(message_id=uuid.uuid4(), from_seq=0)
        )

    assert not isinstance(caught.value, ReplayIncompleteError), (
        "A message with no buffer at all must raise the base "
        "ReplayUnavailableError (404 — gone), not ReplayIncompleteError "
        "(409 — exists but unprovable). A client cannot act correctly on the "
        "two if they collapse into one."
    )


def test_sequence_marker_is_written_on_close(buffered_turn, fake_redis):
    """last_seq is what makes completeness provable at all."""
    message_id, _ = buffered_turn
    assert fake_redis.strings.get(f"stream:lastseq:{message_id}") == "5"
    assert fake_redis.strings.get(f"stream:state:{message_id}") == "CLOSED"
