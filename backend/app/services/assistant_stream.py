"""ARCH-12 Step 1 — the streaming generator with A13 resumption."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Optional

from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.core.exceptions import SpendLimitExceededError
from app.core.redis_client import get_redis_client
from app.core.request_context import context_fields, request_scope, stage
from app.models.assistant import Conversation, FinishReason
from app.schemas.assistant import TokenUsage
from app.services import llm_metering, provenance_service, stream_session
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
    message_id: uuid.UUID
    enabled: bool = True
    frames_written: int = 0
    _client: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        try:
            self._client = get_redis_client()
        except Exception:  # noqa: BLE001
            self._client = None

        if self._client is None:
            self.enabled = False
            logger.debug(
                "stream.replay_buffer_unavailable",
                extra={"message_id": str(self.message_id)},
            )

    @property
    def _frames_key(self) -> str:
        return _FRAMES_KEY.format(message_id=self.message_id)

    @property
    def _state_key(self) -> str:
        return _STATE_KEY.format(message_id=self.message_id)

    @property
    def _lastseq_key(self) -> str:
        return _LASTSEQ_KEY.format(message_id=self.message_id)

    def open(self) -> None:
        if not self.enabled:
            return
        try:
            pipe = self._client.pipeline()
            pipe.delete(self._frames_key)
            # UNKNOWN until close(). A turn that dies mid-flight leaves
            # this sentinel behind, and a resume against it correctly
            # refuses rather than claiming the client is current.
            pipe.set(
                self._lastseq_key, _LASTSEQ_UNKNOWN, ex=REPLAY_TTL_SECONDS
            )
            pipe.set(self._state_key, _STATE_OPEN, ex=REPLAY_TTL_SECONDS)
            pipe.execute()
        except Exception:  # noqa: BLE001
            self._disable("open")

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
            self._disable("overflow")
            return

        try:
            record = json.dumps(
                {"seq": seq, "event": event, "data": data},
                ensure_ascii=False,
                default=str,
            )
            pipe = self._client.pipeline()
            pipe.rpush(self._frames_key, record)
            pipe.expire(self._frames_key, REPLAY_TTL_SECONDS)
            pipe.execute()
            self.frames_written += 1
        except Exception:  # noqa: BLE001
            self._disable("append")

    def close(self, *, last_seq: int) -> None:
        """Seal the buffer, recording how many frames the turn emitted.

        `last_seq` is what makes a later resume provable. It is written
        in the same pipeline as the state flip so a reader never sees
        CLOSED without a companion sequence number.
        """
        if not self.enabled:
            return
        try:
            pipe = self._client.pipeline()
            pipe.set(self._lastseq_key, int(last_seq), ex=REPLAY_TTL_SECONDS)
            pipe.set(self._state_key, _STATE_CLOSED, ex=REPLAY_TTL_SECONDS)
            pipe.execute()
        except Exception:  # noqa: BLE001
            self._disable("close")

    def _disable(self, phase: str) -> None:
        if not self.enabled:
            return
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

                async for chunk in self._drain(plan):
                    saw_any_chunk = True
                    if chunk.usage is not None:
                        usage = chunk.usage
                    if chunk.text:
                        safe = redactor.feed(chunk.text)
                        if safe:
                            yield emit("token", {"text": safe})
                    if chunk.finish_reason == "length":
                        finish = FinishReason.OUTPUT_CEILING.value

                tail = redactor.flush()
                if tail:
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
                buffer.close(last_seq=seq_counter["value"])

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

                outcome = stream_session.settle_and_persist(
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

        try:
            state = client.get(state_key)
            buffered = client.llen(frames_key)
            raw_last_seq = client.get(lastseq_key)
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

        with request_scope(request_id=request_id or str(uuid.uuid4())):
            logger.info(
                "stream.replay_started",
                extra={
                    "message_id": str(message_id),
                    "from_seq": cursor,
                    "buffered_frames": buffered,
                    "state": state,
                },
            )

            while True:
                try:
                    records = client.lrange(frames_key, cursor, -1)
                except Exception:  # noqa: BLE001
                    logger.warning(
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

                try:
                    state = client.get(state_key)
                except Exception:  # noqa: BLE001
                    state = None

                if isinstance(state, bytes):
                    state = state.decode("utf-8", "replace")

                if state != _STATE_OPEN:
                    logger.info(
                        "stream.replay_completed",
                        extra={"message_id": str(message_id), "last_seq": cursor},
                    )
                    return

                if time.monotonic() - idle_since > REPLAY_IDLE_TIMEOUT_SECONDS:
                    logger.warning(
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
                for chunk in provider_stream(
                    prompt=plan.prompt,
                    temperature=plan.ai_settings.temperature,
                    ai_settings=plan.ai_settings,
                ):
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
