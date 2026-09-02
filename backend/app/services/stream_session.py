"""ARCH-12 Step 1 — the session the streaming generator owns.

THE PROBLEM THIS MODULE EXISTS TO SOLVE
=======================================

FastAPI closes the request-scoped session when the response object is
returned. For a `StreamingResponse` that is **before the first token**. Any
`db` captured from `Depends(get_db)` is therefore already closed by the time
settlement matters, and a `settle()` against it either raises
`DetachedInstanceError` or — worse — silently no-ops against a rolled-back
connection.

So the generator opens its own session, and closes it itself.

WHY SETTLEMENT IS SYNCHRONOUS AND UNSHIELDED
============================================

When a client disconnects, Starlette cancels the task running the generator.
`asyncio.CancelledError` is raised at the next `await`. That means **any await
inside the `finally` block is cancelled immediately** — including
`asyncio.to_thread(...)`, which is the obvious way to keep a blocking DB write
off the event loop.

`asyncio.shield` does not fix this either: shield protects the inner task from
*outer* cancellation, but the generator's `finally` is running inside the task
that is already being cancelled, and awaiting a shielded future from there
re-raises immediately.

The reliable construction is to make the settlement contain **no await points
at all**. Synchronous SQLAlchemy in a `finally` block cannot be interrupted by
cancellation, because cancellation is delivered at suspension points and there
are none. It blocks the event loop for the duration of one small INSERT/UPDATE
transaction — measured at 3–6ms against a warm pool — and in exchange the
billing row is written on every path including the abrupt one.

That trade is stated explicitly because it is the one place in this codebase
where blocking the loop is the correct answer rather than an oversight.

WHY THIS NEVER RAISES
=====================

An exception escaping a `finally` block **replaces** the exception that was
propagating — so a database error during settlement would swallow the
`CancelledError` that Starlette needs to see, and the connection would be left
half-torn-down. `settle_and_persist` therefore catches everything, logs at
ERROR with enough context to reconstruct the row by hand, and returns an
outcome object. There is exactly one condition under which this module raises,
and it is a programming error: calling it with no reservation.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.assistant import ConversationMessage, FinishReason, StreamState
from app.schemas.assistant import TokenUsage
from app.services import llm_metering
from app.services.llm_metering import LLMReservation

logger = logging.getLogger("app.services.stream_session")

#: Characters per token, matching `llm_metering._CHARS_PER_TOKEN`. Kept as its
#: own name so the fallback estimate is obviously the same arithmetic the
#: pre-call reservation used — a fallback that estimated differently from the
#: reservation would make drift impossible to attribute.
_CHARS_PER_TOKEN = 3.5


@dataclass
class StreamOutcome:
    """What actually happened, for the caller's logs and for tests."""

    finish_reason: str
    emitted_characters: int
    truncated: bool
    usage_estimated: bool
    settled: bool
    persisted: bool
    message_id: Optional[uuid.UUID] = None
    error: Optional[str] = None
    settlement: dict[str, Any] = field(default_factory=dict)

    def as_details(self) -> dict[str, Any]:
        return {
            "finish_reason": self.finish_reason,
            "emitted_characters": self.emitted_characters,
            "truncated": self.truncated,
            "usage_estimated": self.usage_estimated,
            "settled": self.settled,
            "persisted": self.persisted,
            "message_id": str(self.message_id) if self.message_id else None,
            "error": self.error,
        }


@contextmanager
def settlement_session() -> Iterator[Session]:
    """A session with no relationship to the request that started the stream.

    Rolls back on the way out of an exception so a half-applied settlement is
    never left on the connection when it returns to the pool.
    """
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def estimate_usage_from_emitted(
    *,
    emitted_text: str,
    reservation: LLMReservation,
    provider: str,
    model: str,
    input_cost_per_1k: float,
    output_cost_per_1k: float,
) -> TokenUsage:
    """Local fallback when the provider's usage chunk never arrived.

    Prompt tokens come from the reservation rather than being re-derived: the
    reservation already estimated them from the exact prompt string, and using
    a second estimate here would introduce drift between the ceiling check and
    the settled row that no reconciliation could later explain.
    """
    completion_tokens = max(1, int(len(emitted_text) / _CHARS_PER_TOKEN) + 1)
    prompt_tokens = int(reservation.estimated_input_tokens or 0)
    return TokenUsage(
        provider=provider,
        model=model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        estimated_cost=(
            (prompt_tokens / 1000) * float(input_cost_per_1k or 0.0)
            + (completion_tokens / 1000) * float(output_cost_per_1k or 0.0)
        ),
    )


def open_assistant_message(
    db: Session,
    *,
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> ConversationMessage:
    """Write the STREAMING placeholder row and commit it.

    Committed before the first token deliberately. If the process dies
    mid-stream, a row in STREAMING with a `stream_started_at` in the past is
    what the sweeper needs in order to know a generation was in flight at all.
    Without it, a crashed stream is indistinguishable from a request that was
    never made.
    """
    message = ConversationMessage(
        id=message_id,
        conversation_id=conversation_id,
        role="assistant",
        content="",
        stream_state=StreamState.STREAMING,
        stream_started_at=datetime.now(timezone.utc),
    )
    db.add(message)
    db.commit()
    return message


def settle_and_persist(
    *,
    reservation: Optional[LLMReservation],
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    emitted_text: str,
    token_usage: Optional[TokenUsage],
    finish_reason: str,
    truncated: bool,
    sources: Optional[list[dict[str, Any]]] = None,
    context_hash: Optional[str] = None,
    audit_log_id: Optional[uuid.UUID] = None,
    provider: str = "unknown",
    model: str = "unknown",
) -> StreamOutcome:
    """Bill for what was generated and persist what the user saw.

    Called from the generator's `finally`. Runs on its own session, commits
    once, and never raises. Both halves live in one transaction: a settled
    reservation with no message row is a support ticket, and a message row
    with no usage row is unbilled revenue.
    """
    if reservation is None:
        raise ValueError(
            "settle_and_persist requires a reservation. A stream that reached "
            "the provider without one is a metering bypass, not an edge case."
        )

    usage_estimated = token_usage is None
    outcome = StreamOutcome(
        finish_reason=finish_reason,
        emitted_characters=len(emitted_text),
        truncated=truncated,
        usage_estimated=usage_estimated,
        settled=False,
        persisted=False,
        message_id=message_id,
    )

    resolved_usage = token_usage or estimate_usage_from_emitted(
        emitted_text=emitted_text,
        reservation=reservation,
        provider=provider,
        model=model,
        input_cost_per_1k=reservation.input_cost_per_1k,
        output_cost_per_1k=reservation.output_cost_per_1k,
    )

    try:
        with settlement_session() as db:
            summary = llm_metering.settle(
                db,
                reservation=reservation,
                token_usage=resolved_usage,
                estimated=usage_estimated,
            )
            outcome.settled = True
            outcome.settlement = summary

            message = db.execute(
                select(ConversationMessage).where(
                    ConversationMessage.id == message_id,
                    ConversationMessage.conversation_id == conversation_id,
                )
            ).scalar_one_or_none()

            if message is None:
                # The placeholder commit failed or the conversation was
                # deleted mid-stream. Write the row anyway: the usage event
                # about to be committed alongside it must have something to
                # point at.
                message = ConversationMessage(
                    id=message_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=emitted_text,
                )
                db.add(message)

            message.content = emitted_text
            message.sources = sources
            message.token_usage = resolved_usage.model_dump(mode="json")
            message.truncated = truncated
            message.usage_estimated = usage_estimated
            message.finish_reason = finish_reason
            message.stream_state = (
                StreamState.COMPLETE
                if finish_reason == FinishReason.COMPLETED.value
                else StreamState.ABORTED
            )
            message.context_hash = context_hash
            message.audit_log_id = audit_log_id

            db.commit()
            outcome.persisted = True

    except Exception as exc:  # noqa: BLE001 — see the module docstring.
        outcome.error = f"{type(exc).__name__}: {exc}"
        logger.error(
            "stream.settlement_failed",
            extra={
                "message_id": str(message_id),
                "conversation_id": str(conversation_id),
                "scope": reservation.scope,
                "finish_reason": finish_reason,
                "emitted_characters": len(emitted_text),
                "prompt_tokens": resolved_usage.prompt_tokens,
                "completion_tokens": resolved_usage.completion_tokens,
                "estimated": usage_estimated,
            },
            exc_info=True,
        )
        return outcome

    logger.info("stream.settled", extra=outcome.as_details())
    return outcome


def sweep_in_flight(db: Session, *, older_than_seconds: float) -> list[uuid.UUID]:
    """Find assistant rows still in STREAMING past the deadline.

    This is the detection for failure mode (2) in the Step 1 migration
    docstring — the stream that completed and whose settlement commit then
    failed, or whose process died. It returns ids rather than mutating,
    because what to do about them is an ARCH-14 reconciliation decision and
    not a decision this module should make on its own.
    """
    cutoff = datetime.now(timezone.utc).timestamp() - float(older_than_seconds)
    cutoff_dt = datetime.fromtimestamp(cutoff, tz=timezone.utc)
    rows = db.execute(
        select(ConversationMessage.id).where(
            ConversationMessage.stream_state == StreamState.STREAMING,
            ConversationMessage.stream_started_at < cutoff_dt,
        )
    ).scalars()
    stranded = list(rows)
    if stranded:
        logger.warning(
            "stream.in_flight_stranded",
            extra={"count": len(stranded), "older_than_seconds": older_than_seconds},
        )
    return stranded


__all__ = [
    "StreamOutcome",
    "estimate_usage_from_emitted",
    "open_assistant_message",
    "settle_and_persist",
    "settlement_session",
    "sweep_in_flight",
]
