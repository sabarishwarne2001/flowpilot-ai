"""ARCH-12 Step 1 — the streaming generator with A13 resumption."""

from __future__ import annotations

import asyncio
import queue
import functools
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator, Optional

import anyio
from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.core.exceptions import SpendLimitExceededError
from app.core.redis_client import get_redis_client
from app.core.request_context import context_fields, request_scope, stage
from app.models.assistant import Conversation, FinishReason
from app.schemas.assistant import TokenUsage
from app.services import (
    conversation_service,
    llm_metering,
    provenance_service,
    rag_guard,
    stream_session,
)
from app.services.context_assembly_service import context_assembly_service
from app.services.context_budget import context_budget_service
from app.services.citation_service import citation_service
from app.services.fenced_context import FencedContext, empty_fence
from app.services.llm_metering import LLMMeteringError
from app.services.llm_stream import StreamChunk, StreamProviderError, provider_stream
from app.services.output_filter import StreamRedactor
from app.services.retrieval_service import retrieval_service

logger = logging.getLogger("app.services.assistant_stream")

QUEUE_MAXSIZE = 64
_DONE = object()
RAG_PROMPT_VERSION = "1.0.0"
REPLAY_TTL_SECONDS = 900
REPLAY_MAX_FRAMES = 20_000
REPLAY_IDLE_TIMEOUT_SECONDS = 60.0
REPLAY_POLL_INTERVAL_SECONDS = 0.15
#: How long sealing a stream's buffer waits for its writer to drain (off the event loop).
CLOSE_DRAIN_SECONDS = 5.0

_FRAMES_KEY = "stream:frames:{message_id}"
_STATE_KEY = "stream:state:{message_id}"

#: ARCH-0V Tranche 6. The last sequence number this turn emitted,
#: written at close. Without it, `replay` cannot distinguish "you are
#: current" from "I lost the frames you are missing" — both present as
#: an empty read against a CLOSED state, and the second one was being
#: reported to the client as `already_current`.
#:
#: A value of _LASTSEQ_UNKNOWN means the buffer was disabled mid-turn
#: (overflow, or a Redis write failure) and no completeness claim can
#: be made about it.
_LASTSEQ_KEY = "stream:lastseq:{message_id}"
_LASTSEQ_UNKNOWN = -1

_STATE_OPEN = "OPEN"
_STATE_CLOSED = "CLOSED"


def sse(event: str, data: Any, *, seq: Optional[int] = None) -> bytes:
    payload = dict(data) if isinstance(data, dict) else {"value": data}
    if seq is not None:
        payload["seq"] = seq

    body = json.dumps(payload, ensure_ascii=False, default=str)
    prefix = f"id: {seq}\n" if seq is not None else ""
    return f"{prefix}event: {event}\ndata: {body}\n\n".encode("utf-8")


@dataclass
class StreamFrameBuffer:
    """The resumable stream's frames in Redis, written by a per-stream writer thread.

    Campaign session 2. Every token used to be appended to Redis with a blocking round
    trip on the event loop, so a slow Redis slowed every request on the API (bounded
    only by the 250 ms Redis timeout per call). The loop now only queues; one thread per
    stream owns the Redis calls and runs them in order (open, frames, close), so the
    ARCH-0V proof holds: the state turns CLOSED, with its sequence marker, only after
    every frame before it is written. A failure disables the buffer the same way as
    before (sequence marker UNKNOWN, so a resume refuses rather than truncates).
    """

    message_id: uuid.UUID
    enabled: bool = True
    frames_written: int = 0
    _client: Any = field(default=None, repr=False)
    _queue: "queue.Queue[Any]" = field(default=None, repr=False)  # type: ignore[assignment]
    _thread: Optional[threading.Thread] = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            self._client = get_redis_client()  # lazy: no network until a command
        except Exception:  # noqa: BLE001
            self._client = None

        if self._client is None:
            self.enabled = False
            logger.debug(
                "stream.replay_buffer_unavailable",
                extra={"message_id": str(self.message_id)},
            )
            return
        self._queue = queue.Queue(maxsize=REPLAY_MAX_FRAMES + 16)
        self._thread = threading.Thread(
            target=self._write_loop, name=f"stream-buffer-{self.message_id}", daemon=True
        )
        self._thread.start()

    @property
    def _frames_key(self) -> str:
        return _FRAMES_KEY.format(message_id=self.message_id)

    @property
    def _state_key(self) -> str:
        return _STATE_KEY.format(message_id=self.message_id)

    @property
    def _lastseq_key(self) -> str:
        return _LASTSEQ_KEY.format(message_id=self.message_id)

    # -- the event loop's side: never touches Redis -------------------------

    def _enqueue(self, op: tuple[Any, ...]) -> None:
        try:
            self._queue.put_nowait(op)
        except queue.Full:
            # Cannot happen while frames stay under REPLAY_MAX_FRAMES (the queue holds more);
            # if it ever does, buffering stops and the marker `open` set (UNKNOWN) stands,
            # so a resume refuses rather than truncates. Never blocks the event loop.
            self.enabled = False
            logger.warning("stream.replay_buffer_backlog", extra={"message_id": str(self.message_id)})

    def open(self) -> None:
        if not self.enabled:
            return
        self._enqueue(("open",))

    def append(self, *, seq: int, event: str, data: Any) -> None:
        if not self.enabled:
            return

        if self.frames_written >= REPLAY_MAX_FRAMES:
            logger.warning(
                "stream.replay_buffer_overflow",
                extra={
                    "message_id": str(self.message_id),
                    "frames": self.frames_written,
                },
            )
            self.enabled = False
            self._enqueue(("disable", "overflow"))
            return

        try:
            record = json.dumps(
                {"seq": seq, "event": event, "data": data},
                ensure_ascii=False,
                default=str,
            )
        except Exception:  # noqa: BLE001
            self.enabled = False
            self._enqueue(("disable", "append"))
            return
        self._enqueue(("append", record))
        self.frames_written += 1

    def close(self, *, last_seq: int, timeout: float = CLOSE_DRAIN_SECONDS) -> None:
        """Seal the buffer, recording how many frames the turn emitted.

        `last_seq` is what makes a later resume provable. It is written
        in the same pipeline as the state flip so a reader never sees
        CLOSED without a companion sequence number. Queued after every
        frame, so it is written after them; returns once the writer has
        drained (bounded by `timeout`). Blocking: the stream calls it from
        a worker thread, never on the event loop.
        """
        if self._queue is None:
            return
        if self.enabled:
            self._enqueue(("close", int(last_seq)))
        self._queue.put(_BUFFER_DONE)
        self.flush(timeout)

    def flush(self, timeout: float = CLOSE_DRAIN_SECONDS) -> bool:
        """Wait until every queued Redis write is done (or `timeout` passes)."""
        if self._queue is None:
            return True
        deadline = time.monotonic() + timeout
        with self._queue.all_tasks_done:
            while self._queue.unfinished_tasks:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._queue.all_tasks_done.wait(remaining)
        return True

    # -- the writer thread: the only code that talks to Redis --------------

    def _write_loop(self) -> None:
        healthy = True
        while True:
            try:
                op = self._queue.get(timeout=REPLAY_TTL_SECONDS)
            except queue.Empty:
                return  # the stream never closed (a crashed request); the keys expire on their own
            try:
                if op is _BUFFER_DONE:
                    return
                healthy = self._write(op, healthy)
            finally:
                self._queue.task_done()

    def _write(self, op: tuple[Any, ...], healthy: bool) -> bool:
        """One queued operation, on the writer thread. Returns whether the buffer is still healthy."""
        kind = op[0]
        if kind == "disable":
            if healthy:
                self._disable_now(op[1])
            return False
        if not healthy:
            return False
        try:
            pipe = self._client.pipeline()
            if kind == "open":
                pipe.delete(self._frames_key)
                # UNKNOWN until close(). A turn that dies mid-flight leaves
                # this sentinel behind, and a resume against it correctly
                # refuses rather than claiming the client is current.
                pipe.set(self._lastseq_key, _LASTSEQ_UNKNOWN, ex=REPLAY_TTL_SECONDS)
                pipe.set(self._state_key, _STATE_OPEN, ex=REPLAY_TTL_SECONDS)
            elif kind == "append":
                pipe.rpush(self._frames_key, op[1])
                pipe.expire(self._frames_key, REPLAY_TTL_SECONDS)
            else:  # close
                pipe.set(self._lastseq_key, op[1], ex=REPLAY_TTL_SECONDS)
                pipe.set(self._state_key, _STATE_CLOSED, ex=REPLAY_TTL_SECONDS)
            pipe.execute()
        except Exception:  # noqa: BLE001
            self.enabled = False
            self._disable_now(kind)
            return False
        return True

    def _disable(self, phase: str) -> None:
        """Stop buffering and mark the turn unprovable, in order after what was queued."""
        self.enabled = False
        writer = self._thread
        if writer is not None and writer.is_alive() and threading.current_thread() is not writer:
            self._queue.put(("disable", phase))
            self.flush()
            return
        self._disable_now(phase)

    def _disable_now(self, phase: str) -> None:
        self.enabled = False
        logger.warning(
            "stream.replay_buffer_disabled",
            extra={"message_id": str(self.message_id), "phase": phase},
        )
        try:
            # ARCH-0V Tranche 6 — a disabled buffer can prove nothing
            # about completeness, so the sequence marker is forced back
            # to UNKNOWN even if a close() had already written a real
            # one. Refusing a resume is correct here; claiming currency
            # would be a silent truncation in the client.
            self._client.set(
                self._lastseq_key, _LASTSEQ_UNKNOWN, ex=REPLAY_TTL_SECONDS
            )
            self._client.set(self._state_key, _STATE_CLOSED, ex=REPLAY_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            pass


#: Ends a buffer's writer thread.
_BUFFER_DONE = object()


@dataclass
class StreamPlan:
    conversation: Conversation
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    ai_settings: Any
    prompt: str
    fenced: FencedContext
    results: list[dict[str, Any]]
    reservation: Any
    message_id: uuid.UUID
    context_hash: Optional[str]
    audit_log_id: Optional[uuid.UUID]
    passages_dropped_budget: int
    budget_warnings: list[str]
    # ARCH39-S1:stream-plan — what a single shrink-and-retry needs.
    query_text: str = ""
    system_prompt: str = ""
    history_full: list[dict[str, str]] = field(default_factory=list)
    digest: str = ""
    all_results: list[dict[str, Any]] = field(default_factory=list)
    window_tokens: int = 0
    context_trimmed: bool = False


class ReplayUnavailableError(RuntimeError):
    """No buffered frames exist for this message."""


class ReplayIncompleteError(ReplayUnavailableError):
    """Frames exist, but not the ones this client is missing.

    ARCH-0V Tranche 6. Distinct from its parent because the caller
    must not treat it as "nothing to send". Deriving from
    ReplayUnavailableError keeps every existing `except` clause
    correct; the API layer catches this one first to return a
    different status and an explicit `resume_unavailable` code.

    Raised when the buffer is CLOSED and either the final sequence
    number is unknown, or fewer frames survive than the client is
    missing. Both happen in production: the frames list has its TTL
    refreshed on every append while the state key does not, Redis
    evicts the large list before the small string under memory
    pressure, and ARCH-19 Sentinel failover replicates the two keys
    asynchronously.
    """


#: ARCH-0V: exported so the API layer can distinguish 'nothing to send'
#: from 'I cannot prove there is nothing to send'.
REPLAY_ERRORS = (
    "ReplayIncompleteError",
    "ReplayUnavailableError",
)


class AssistantStreamService:
    def prepare(
        self,
        db: Session,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        organization_id: uuid.UUID,
        workspace_id: uuid.UUID,
        query_text: str,
        ip_address: Optional[str] = None,
        user_agent: Optional[str] = None,
    ) -> StreamPlan:
        conversation = crud.get_conversation(
            db,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            user_id=user_id,
        )
        if conversation is None:
            raise ValueError("Conversation not found.")

        ai_settings = crud.get_ai_settings(db=db, workspace_id=workspace_id)
        if ai_settings is None:
            raise ValueError("AI settings have not been configured.")
        ai_settings = conversation_service.apply_model_override(ai_settings, conversation)

        with stage("retrieval"):
            results = self._retrieve(
                db=db,
                conversation=conversation,
                workspace_id=workspace_id,
                query=query_text,
            )

        history = self._load_history(db=db, conversation=conversation)
        turn = len(history)

        from app.services.llm_service import llm_service

        system_prompt = llm_service.system_prompt_for(
            query=query_text, ai_settings=ai_settings
        )

        with stage("context_budget"):
            budgeted = context_budget_service.build(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                turn=turn,
                system_prompt=system_prompt,
                results=results,
                history=history,
                existing_digest=self._load_digest(db, conversation=conversation),
                ai_settings=ai_settings,
                assemble=context_assembly_service.assemble,
            )

        retained_ids = set(budgeted.fenced.chunk_ids)
        retained_results = [
            result for result in results if str(result.get("id")) in retained_ids
        ]

        prompt = llm_service.build_streaming_prompt(
            query=query_text,
            fenced=budgeted.fenced,
            history=budgeted.history,
            digest=budgeted.digest,
            ai_settings=ai_settings,
        )

        message_id = uuid.uuid4()

        reservation = llm_metering.reserve(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            message_id=message_id,
            prompt=prompt,
            ai_settings=ai_settings,
        )

        context_hash: Optional[str] = None
        audit_log_id: Optional[uuid.UUID] = None
        if not budgeted.fenced.is_empty:
            context_hash, audit_log_id = provenance_service.seal_generation(
                db,
                organization_id=organization_id,
                workspace_id=workspace_id,
                conversation_id=conversation.id,
                message_id=message_id,
                fenced=budgeted.fenced,
                query=query_text,
                provider=ai_settings.provider.value,
                model=ai_settings.model,
                prompt_version=RAG_PROMPT_VERSION,
                ip_address=ip_address,
                user_agent=user_agent,
            )

        crud.create_conversation_message(
            db,
            conversation_id=conversation.id,
            role="user",
            content=query_text,
        )
        stream_session.open_assistant_message(
            db, message_id=message_id, conversation_id=conversation.id
        )

        return StreamPlan(
            conversation=conversation,
            organization_id=organization_id,
            workspace_id=workspace_id,
            ai_settings=ai_settings,
            prompt=prompt,
            fenced=budgeted.fenced,
            results=retained_results,
            reservation=reservation,
            message_id=message_id,
            context_hash=context_hash,
            audit_log_id=audit_log_id,
            passages_dropped_budget=budgeted.chunks_dropped_for_budget,
            budget_warnings=budgeted.warnings,
            query_text=query_text,
            system_prompt=system_prompt,
            history_full=list(history),
            digest=budgeted.digest,
            all_results=list(results),
            window_tokens=int(budgeted.budget.window_tokens),
        )

    async def stream_answer(
        self, plan: StreamPlan, *, request_id: Optional[str] = None
    ) -> AsyncIterator[bytes]:
        redactor = StreamRedactor(fence_nonce=plan.fenced.fence_nonce or None)
        usage: Optional[TokenUsage] = None
        finish = FinishReason.COMPLETED.value
        saw_any_chunk = False
        provider = plan.ai_settings.provider.value
        model = plan.ai_settings.model

        buffer = StreamFrameBuffer(message_id=plan.message_id)
        buffer.open()

        seq_counter = {"value": 0}

        def emit(event: str, data: Any) -> bytes:
            seq_counter["value"] += 1
            seq = seq_counter["value"]
            buffer.append(seq=seq, event=event, data=data)
            return sse(event, data, seq=seq)

        with request_scope(
            request_id=request_id or str(uuid.uuid4()),
            workspace_id=plan.workspace_id,
            organization_id=plan.organization_id,
        ):
            try:
                yield emit(
                    "start",
                    {
                        "message_id": str(plan.message_id),
                        "conversation_id": str(plan.conversation.id),
                        "model": model,
                        "provider": provider,
                        "passages": plan.fenced.passages_included,
                        "warnings": plan.budget_warnings,
                        "resumable": buffer.enabled,
                    },
                )

                # Campaign session 2 (F-266): Settings -> AI -> Streaming responses. Off, the
                # answer is filtered in full and sent as one piece when it is complete (for
                # teams that must not show text before the whole answer is checked).
                whole_answer = not bool(getattr(plan.ai_settings, "enable_streaming", True))
                held: list[str] = []

                # ARCH39-S1:stream-retry — one smaller retry when the provider
                # refuses the request size before any token was emitted.
                attempt = 0
                while True:
                    try:
                        async for chunk in self._drain(plan):
                            saw_any_chunk = True
                            if chunk.usage is not None:
                                usage = chunk.usage
                            if chunk.text:
                                safe = redactor.feed(chunk.text)
                                if safe and whole_answer:
                                    held.append(safe)
                                elif safe:
                                    yield emit("token", {"text": safe})
                            if chunk.finish_reason == "length":
                                finish = FinishReason.OUTPUT_CEILING.value
                        break
                    except (asyncio.CancelledError, SpendLimitExceededError):
                        raise
                    except Exception as exc:  # noqa: BLE001
                        too_large = (
                            None
                            if saw_any_chunk or attempt
                            else rag_guard.classify_request_too_large(exc)
                        )
                        if too_large is None:
                            # F-258 (campaign session 2). One transient provider error
                            # (503, 502, a timeout, a rate limit) before the first token
                            # is retried once, as the plain answer path retries it.
                            # Nothing was shown or served yet, so a retry is invisible
                            # and charged once.
                            from app.services import llm_resilience

                            kind = llm_resilience.classify(exc)
                            retryable = kind in (
                                llm_resilience.FailureClass.TRANSIENT,
                                llm_resilience.FailureClass.RATE_LIMITED,
                            )
                            if saw_any_chunk or attempt or not retryable:
                                raise
                            attempt += 1
                            logger.warning(
                                "stream.provider_retry",
                                extra={"message_id": str(plan.message_id), "failure_class": kind.value,
                                       "error_type": type(exc).__name__},
                            )
                            await asyncio.sleep(
                                llm_resilience.backoff_delay(
                                    1, base=0.5, cap=2.0,
                                    rate_limited=kind is llm_resilience.FailureClass.RATE_LIMITED,
                                )
                            )
                            continue
                        attempt += 1
                        # F-218. Resealing writes the audit record: blocking
                        # database work, so it runs off the event loop.
                        plan = await anyio.to_thread.run_sync(
                            self._shrink_plan, plan, too_large
                        )
                        yield emit(
                            "notice",
                            {
                                "code": "context_trimmed",
                                "message": (
                                    "The request was too large for the model; "
                                    "retrying with less context."
                                ),
                            },
                        )

                tail = redactor.flush()
                if whole_answer:
                    whole = "".join(held) + (tail or "")
                    if whole:
                        yield emit("token", {"text": whole})
                elif tail:
                    yield emit("token", {"text": tail})

                envelope = provenance_service.build_envelope(
                    message_id=plan.message_id,
                    conversation_id=plan.conversation.id,
                    answer=redactor.emitted_text,
                    results=plan.results,
                    query="",
                    fenced=plan.fenced,
                    context_hash=plan.context_hash,
                    audit_log_id=plan.audit_log_id,
                    provider=provider,
                    model=model,
                    prompt_version=RAG_PROMPT_VERSION,
                    passages_dropped_budget=plan.passages_dropped_budget,
                    truncated=finish != FinishReason.COMPLETED.value,
                    finish_reason=finish,
                    usage_estimated=usage is None,
                )
                yield emit("citations", envelope.model_dump(mode="json"))
                yield emit(
                    "done",
                    {
                        "finish_reason": finish,
                        "truncated": finish != FinishReason.COMPLETED.value,
                        "usage_estimated": usage is None,
                        "context_trimmed": plan.context_trimmed,
                    },
                )

            except asyncio.CancelledError:
                finish = FinishReason.CLIENT_DISCONNECTED.value
                raise
            except SpendLimitExceededError:
                finish = FinishReason.SPEND_LIMIT.value
                raise
            except (StreamProviderError, Exception):  # noqa: BLE001
                finish = FinishReason.PROVIDER_ERROR.value
                raise
            finally:
                emitted = redactor.emitted_text + redactor.flush()
                truncated = finish != FinishReason.COMPLETED.value
                # Campaign session 2: sealing waits for the buffer's writer thread to drain,
                # so it runs off the event loop (shielded: a disconnect must not skip it).
                with anyio.CancelScope(shield=True):
                    await anyio.to_thread.run_sync(
                        functools.partial(buffer.close, last_seq=seq_counter["value"])
                    )

                sources: list[dict[str, Any]] = []
                if plan.results:
                    sources = provenance_service.serialise_sources(
                        provenance_service.build_envelope(
                            message_id=plan.message_id,
                            conversation_id=plan.conversation.id,
                            answer=emitted,
                            results=plan.results,
                            query="",
                            fenced=plan.fenced,
                            context_hash=plan.context_hash,
                            audit_log_id=plan.audit_log_id,
                            provider=provider,
                            model=model,
                        )
                    )

                # F-218. Billing and persisting the answer is blocking database
                # work (lock waits included): it runs off the event loop. The
                # scope is shielded because this `finally` also runs when the
                # client disconnected, and a cancelled await here would leave
                # the reservation unsettled and the answer unsaved.
                with anyio.CancelScope(shield=True):
                    outcome = await anyio.to_thread.run_sync(
                        functools.partial(
                            stream_session.settle_and_persist,
                            reservation=plan.reservation,
                            message_id=plan.message_id,
                            conversation_id=plan.conversation.id,
                            emitted_text=emitted,
                            token_usage=usage,
                            finish_reason=finish,
                            truncated=truncated,
                            sources=sources or None,
                            context_hash=plan.context_hash,
                            audit_log_id=plan.audit_log_id,
                            provider=provider,
                            model=model,
                        )
                    )
                redactor.log_summary(message_id=str(plan.message_id))
                logger.info(
                    "stream.finished",
                    extra={
                        **outcome.as_details(),
                        "saw_any_chunk": saw_any_chunk,
                        "frames_emitted": seq_counter["value"],
                        "replayable": buffer.enabled,
                        **context_fields(),
                    },
                )

    async def replay(
        self,
        *,
        message_id: uuid.UUID,
        from_seq: int = 0,
        request_id: Optional[str] = None,
    ) -> AsyncIterator[bytes]:
        client = None
        try:
            client = get_redis_client()
        except Exception:  # noqa: BLE001
            client = None

        if client is None:
            raise ReplayUnavailableError(
                "Stream resumption is unavailable. Refetch the message instead."
            )

        frames_key = _FRAMES_KEY.format(message_id=message_id)
        state_key = _STATE_KEY.format(message_id=message_id)
        lastseq_key = _LASTSEQ_KEY.format(message_id=message_id)

        def _snapshot() -> tuple[Any, Any, Any]:
            return client.get(state_key), client.llen(frames_key), client.get(lastseq_key)

        try:
            # Campaign session 2: Redis reads run off the event loop.
            state, buffered, raw_last_seq = await anyio.to_thread.run_sync(_snapshot)
        except Exception as exc:  # noqa: BLE001
            raise ReplayUnavailableError(
                "Stream resumption is unavailable. Refetch the message instead."
            ) from exc

        if state is None and not buffered:
            raise ReplayUnavailableError(
                "No buffered frames for this message. It may have expired."
            )

        if isinstance(state, bytes):
            state = state.decode("utf-8", "replace")

        # ARCH-0V Tranche 6: prove completeness before replaying.
        #
        # Through ARCH-22 this method returned an empty generator
        # whenever the state was CLOSED and no frames came back, and
        # the API turned that into `finish_reason: already_current`.
        # That is a lie in three reachable situations: the buffer was
        # disabled mid-turn, Redis evicted the frames list before the
        # state key, or a Sentinel failover replicated them out of
        # step. In each, the client is told it holds the whole turn
        # while holding a truncated one — and truncated model output
        # that looks complete is worse than a visible failure.
        last_seq = _LASTSEQ_UNKNOWN
        if raw_last_seq is not None:
            if isinstance(raw_last_seq, bytes):
                raw_last_seq = raw_last_seq.decode("utf-8", "replace")
            try:
                last_seq = int(raw_last_seq)
            except (TypeError, ValueError):
                last_seq = _LASTSEQ_UNKNOWN

        cursor_start = max(int(from_seq), 0)

        if state != _STATE_OPEN:
            if last_seq == _LASTSEQ_UNKNOWN:
                raise ReplayIncompleteError(
                    "This turn's frame buffer was sealed without a "
                    "sequence marker, so there is no way to prove you "
                    "have the whole response. Refetch the message."
                )
            if cursor_start < last_seq and buffered < (
                last_seq - cursor_start
            ):
                raise ReplayIncompleteError(
                    f"Frames {cursor_start + 1}..{last_seq} were "
                    f"requested but only {buffered} remain buffered. "
                    "The replay window has partially expired. Refetch "
                    "the message."
                )

        cursor = cursor_start
        idle_since = time.monotonic()

        # Campaign session 2: the request scope tags each log line only. Held across the
        # yields below, it was entered in the request's context (the API reads the first
        # frame there) and closed in the response task's, which raised at the end of every
        # replay of a finished answer.
        scope_id = request_id or str(uuid.uuid4())

        def _log(write: Any, event: str, **kwargs: Any) -> None:
            with request_scope(request_id=scope_id):
                write(event, **kwargs)

        _log(
            logger.info,
            "stream.replay_started",
            extra={
                "message_id": str(message_id),
                "from_seq": cursor,
                "buffered_frames": buffered,
                "state": state,
            },
        )

        sealed = False
        while True:
            try:
                records = await anyio.to_thread.run_sync(client.lrange, frames_key, cursor, -1)
            except Exception:  # noqa: BLE001
                _log(
                    logger.warning,
                    "stream.replay_read_failed",
                    extra={"message_id": str(message_id)},
                )
                return

            if records:
                for raw in records:
                    if isinstance(raw, bytes):
                        raw = raw.decode("utf-8", "replace")
                    try:
                        record = json.loads(raw)
                    except (ValueError, TypeError):
                        continue

                    seq = int(record.get("seq", 0))
                    if seq <= cursor:
                        continue

                    cursor = seq
                    yield sse(
                        str(record.get("event", "token")),
                        record.get("data", {}),
                        seq=seq,
                    )

                idle_since = time.monotonic()

            if sealed:
                # The pass after CLOSED: frames written just before the seal are in.
                _log(
                    logger.info,
                    "stream.replay_completed",
                    extra={"message_id": str(message_id), "last_seq": cursor},
                )
                return

            try:
                state = await anyio.to_thread.run_sync(client.get, state_key)
            except Exception:  # noqa: BLE001
                state = None

            if isinstance(state, bytes):
                state = state.decode("utf-8", "replace")

            if state == _STATE_CLOSED:
                # Campaign session 2: read once more. A frame appended between the
                # read above and the seal would otherwise be lost to this client.
                sealed = True
                continue

            if state != _STATE_OPEN:
                _log(
                    logger.info,
                    "stream.replay_completed",
                    extra={"message_id": str(message_id), "last_seq": cursor},
                )
                return

            if time.monotonic() - idle_since > REPLAY_IDLE_TIMEOUT_SECONDS:
                _log(
                    logger.warning,
                    "stream.replay_idle_timeout",
                    extra={"message_id": str(message_id), "last_seq": cursor},
                )
                yield sse(
                    "error",
                    {
                        "code": "REPLAY_IDLE_TIMEOUT",
                        "message": (
                            "The generation stopped producing output. "
                            "Refetch the message to see what was saved."
                        ),
                    },
                )
                return

            await asyncio.sleep(REPLAY_POLL_INTERVAL_SECONDS)

    async def _drain(self, plan: StreamPlan) -> AsyncIterator[StreamChunk]:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        stop = threading.Event()

        def produce() -> None:
            try:
                # ARCH39-S1:stream-open — ARCH-23 made `client` mandatory on
                # provider_stream and this call never passed one, so every
                # stream raised TypeError. open_stream resolves the client
                # (platform key or the tenant's BYOK key) and wraps it in the
                # provider breaker. The session is only needed to resolve it.
                from app.db.session import SessionLocal
                from app.services import llm_stream

                session = SessionLocal()
                try:
                    opened = llm_stream.open_stream(
                        session,
                        organization_id=plan.organization_id,
                        prompt=plan.prompt,
                        temperature=plan.ai_settings.temperature,
                        ai_settings=plan.ai_settings,
                    )
                finally:
                    session.close()
                attach = getattr(plan.reservation, "attach_credential_use", None)
                if callable(attach):
                    attach(opened.credential_use)
                for chunk in opened.chunks:
                    if stop.is_set():
                        break
                    asyncio.run_coroutine_threadsafe(queue.put(chunk), loop).result()
            except BaseException as exc:  # noqa: BLE001
                try:
                    asyncio.run_coroutine_threadsafe(queue.put(exc), loop).result()
                except Exception:  # noqa: BLE001
                    pass
            finally:
                try:
                    asyncio.run_coroutine_threadsafe(queue.put(_DONE), loop).result()
                except Exception:  # noqa: BLE001
                    pass

        worker = threading.Thread(
            target=produce, name=f"stream-{plan.message_id}", daemon=True
        )
        worker.start()

        try:
            while True:
                item = await queue.get()
                if item is _DONE:
                    return
                if isinstance(item, BaseException):
                    raise item
                yield item
        finally:
            stop.set()

    def _shrink_plan(self, plan: StreamPlan, too_large: Any) -> StreamPlan:
        """ARCH-39. The same turn with a smaller window, resealed.

        Pure recomputation from what `prepare` kept, except the provenance
        seal: the retried request carries a different context, and the audit
        record must describe what was actually sent.
        """
        from app.db.session import SessionLocal
        from app.services.context_budget import WindowBudget, trim_results_to_budget
        from app.services.fenced_context import fence
        from app.services.llm_service import llm_service

        provider = str(getattr(plan.ai_settings.provider, "value", plan.ai_settings.provider))
        model = str(plan.ai_settings.model)
        max_output = int(getattr(plan.ai_settings, "max_output_tokens", 0) or 0)
        if getattr(too_large, "limit", None):
            rag_guard.learned_ceilings.learn(provider, model, int(too_large.limit))

        previous = plan.window_tokens or rag_guard.estimate_tokens(plan.prompt)
        window = rag_guard.shrunk_budget(previous, too_large, max_output_tokens=max_output)
        budget = WindowBudget.allocate(window_tokens=window, system_prompt=plan.system_prompt)

        kept, dropped = trim_results_to_budget(
            plan.all_results, token_budget=budget.context_tokens
        )
        assembled = context_assembly_service.assemble(
            kept,
            max_characters=int(budget.context_tokens * 3.5),
            block_threshold=settings.CONTEXT_INJECTION_BLOCK_THRESHOLD,
        )
        fenced = fence(assembled, chunk_ids=[str(r.get("id") or "") for r in kept])
        history, _ = rag_guard.fit_history(plan.history_full, token_budget=budget.history_tokens)
        prompt = llm_service.build_streaming_prompt(
            query=plan.query_text,
            fenced=fenced,
            history=history,
            digest=plan.digest,
            ai_settings=plan.ai_settings,
        )

        context_hash, audit_log_id = plan.context_hash, plan.audit_log_id
        if not fenced.is_empty:
            session = SessionLocal()
            try:
                context_hash, audit_log_id = provenance_service.seal_generation(
                    session,
                    organization_id=plan.organization_id,
                    workspace_id=plan.workspace_id,
                    conversation_id=plan.conversation.id,
                    message_id=plan.message_id,
                    fenced=fenced,
                    query=plan.query_text,
                    provider=provider,
                    model=model,
                    prompt_version=RAG_PROMPT_VERSION,
                )
                session.commit()
            finally:
                session.close()

        retained = set(fenced.chunk_ids)
        logger.warning(
            "stream.request_too_large_retry",
            extra={
                "message_id": str(plan.message_id),
                "provider": provider,
                "model": model,
                "limit": getattr(too_large, "limit", None),
                "retry_window": window,
            },
        )
        return replace(
            plan,
            prompt=prompt,
            fenced=fenced,
            results=[r for r in plan.all_results if str(r.get("id")) in retained],
            context_hash=context_hash,
            audit_log_id=audit_log_id,
            passages_dropped_budget=plan.passages_dropped_budget + dropped,
            budget_warnings=[
                *plan.budget_warnings,
                "context trimmed after the provider refused the request size",
            ],
            window_tokens=window,
            context_trimmed=True,
        )

    def _retrieve(
        self,
        *,
        db: Session,
        conversation: Conversation,
        workspace_id: uuid.UUID,
        query: str,
    ) -> list[dict[str, Any]]:
        work_item_ids_param: Optional[list[str]] = None
        if conversation.work_item_id is not None:
            work_item = crud.get_work_item(
                db, workspace_id=workspace_id, work_item_id=conversation.work_item_id
            )
            if work_item is None:
                raise ValueError("Associated document not found.")
            work_item_ids_param = [str(work_item.id)]
        else:
            # ARCH39-S1:stream-scope — None (whole workspace) or the
            # conversation's selected documents; an empty selection searches
            # nothing rather than everything.
            work_item_ids_param = conversation_service.retrieval_work_item_ids(
                db, conversation=conversation
            )

        results = retrieval_service.hybrid_search(
            workspace_id=workspace_id,
            query=query,
            work_item_ids=work_item_ids_param,
            top_k=settings.RAG_TOP_K,
            similarity_threshold=settings.RAG_SIMILARITY_THRESHOLD,
            db=db,
            request_id=str(conversation.id),
        )
        if not results:
            return []

        with stage("citation"):
            return citation_service.rank_citations(results)

    def _load_history(
        self, *, db: Session, conversation: Conversation
    ) -> list[dict[str, str]]:
        messages = crud.get_conversation_messages(db, conversation_id=conversation.id)
        return [
            {"role": message.role, "content": message.content}
            for message in messages
            if (message.content or "").strip()
        ]

    def _load_digest(self, db: Session, *, conversation: Conversation) -> str:
        messages = crud.get_conversation_messages(db, conversation_id=conversation.id)
        for message in reversed(messages):
            payload = message.token_usage or {}
            digest = payload.get("conversation_digest")
            if digest:
                return str(digest)
        return ""


assistant_stream_service = AssistantStreamService()

__all__ = [
    "AssistantStreamService",
    "QUEUE_MAXSIZE",
    "REPLAY_IDLE_TIMEOUT_SECONDS",
    "REPLAY_MAX_FRAMES",
    "REPLAY_TTL_SECONDS",
    "ReplayUnavailableError",
    "StreamFrameBuffer",
    "StreamPlan",
    "assistant_stream_service",
    "sse",
]
