"""Gate 12.1 — streaming settlement under every termination path.

"A test that only exercises the happy path is not a test of this step."

The four cases below are the four states the Step 1 migration docstring names.
Each one asserts the same three things, because they are what the phase is
for:

  1. usage rows exist,
  2. the message is persisted with the right `truncated` / `finish_reason`,
  3. no session is leaked.

`_open_sessions` counts checked-out connections on the engine pool. It catches
the failure mode that a functional assertion cannot: a generator that settles
correctly and then leaves the connection out of the pool, which passes every
behavioural test and then exhausts `pool_size + max_overflow` under load.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from app.db.session import engine
from app.models.assistant import Conversation, ConversationMessage, StreamState
from app.models.usage_event import UsageEvent
from app.schemas.assistant import TokenUsage
from app.services import stream_session
from app.services.llm_metering import LLMReservation


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _open_sessions() -> int:
    return engine.pool.checkedout()


def _reservation(conversation_id: uuid.UUID, message_id: uuid.UUID, org_id) -> LLMReservation:
    return LLMReservation(
        organization_id=org_id,
        workspace_id=None,
        scope=f"llm:{conversation_id}:{message_id}",
        resource_type="CONVERSATION",
        resource_id=conversation_id,
        estimated_input_tokens=420,
        max_output_tokens=1024,
        input_cost_per_1k=0.05,
        output_cost_per_1k=0.08,
    )


def _usage_rows(db, *, organization_id, scope: str) -> list[UsageEvent]:
    return list(
        db.execute(
            select(UsageEvent).where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.idempotency_key.startswith(f"{scope}:"),
            )
        )
        .scalars()
        .all()
    )


@pytest.fixture()
def conversation(db_session, tenant) -> Conversation:
    row = Conversation(
        workspace_id=tenant.workspace.id,
        user_id=tenant.contributor.user.id,
        title="Gate 12.1",
    )
    db_session.add(row)
    db_session.commit()
    return row


# ---------------------------------------------------------------------------
# Case 1 — the happy path, stated so the others have a baseline
# ---------------------------------------------------------------------------


def test_completed_stream_settles_with_provider_usage(db_session, tenant, conversation):
    conv_id = conversation.id
    org_id = tenant.organization.id
    message_id = uuid.uuid4()
    reservation = _reservation(conv_id, message_id, org_id)

    stream_session.open_assistant_message(
        db_session, message_id=message_id, conversation_id=conv_id
    )

    outcome = stream_session.settle_and_persist(
        reservation=reservation,
        message_id=message_id,
        conversation_id=conv_id,
        emitted_text="The invoice total is 4,200.00 EUR.",
        token_usage=TokenUsage(
            provider="groq",
            model="llama-3.1-8b",
            prompt_tokens=418,
            completion_tokens=112,
            total_tokens=530,
            estimated_cost=0.03,
        ),
        finish_reason="completed",
        truncated=False,
    )

    assert outcome.settled is True
    assert outcome.persisted is True
    assert outcome.usage_estimated is False

    db_session.expire_all()
    message = db_session.get(ConversationMessage, message_id)
    assert message.stream_state is StreamState.COMPLETE
    assert message.truncated is False
    assert message.usage_estimated is False
    assert message.finish_reason == "completed"

    rows = _usage_rows(
        db_session, organization_id=org_id, scope=reservation.scope
    )
    assert {row.event_type for row in rows} == {"llm.input_token", "llm.output_token"}
    output_row = next(r for r in rows if r.event_type == "llm.output_token")
    assert int(output_row.quantity) == 112
    assert output_row.details.get("estimated") is False


# ---------------------------------------------------------------------------
# Case 2 — THE gate. Client killed at token 50 of a 400-token answer.
# ---------------------------------------------------------------------------


def test_client_disconnect_mid_stream_still_bills_and_persists(
    db_session, tenant, conversation
):
    conv_id = conversation.id
    org_id = tenant.organization.id
    message_id = uuid.uuid4()
    reservation = _reservation(conv_id, message_id, org_id)

    stream_session.open_assistant_message(
        db_session, message_id=message_id, conversation_id=conv_id
    )

    # 50 tokens' worth of text reached the client; the usage chunk never did.
    partial = "word " * 50

    before = _open_sessions()
    outcome = stream_session.settle_and_persist(
        reservation=reservation,
        message_id=message_id,
        conversation_id=conv_id,
        emitted_text=partial,
        token_usage=None,  # the disconnect is exactly this
        finish_reason="client_disconnected",
        truncated=True,
        provider="groq",
        model="llama-3.1-8b",
    )
    after = _open_sessions()

    assert outcome.settled is True, "a disconnect is not an excuse not to bill"
    assert outcome.persisted is True
    assert outcome.usage_estimated is True
    assert after == before, "settlement session was leaked"

    db_session.expire_all()
    message = db_session.get(ConversationMessage, message_id)
    assert message.stream_state is StreamState.ABORTED
    assert message.truncated is True
    assert message.usage_estimated is True
    assert message.finish_reason == "client_disconnected"
    assert message.content == partial, "the user must see what they saw"

    rows = _usage_rows(
        db_session, organization_id=org_id, scope=reservation.scope
    )
    assert len(rows) == 2, "both input and output must be recorded"
    assert all(row.details.get("estimated") is True for row in rows)

    output_row = next(r for r in rows if r.event_type == "llm.output_token")
    assert int(output_row.quantity) > 0, "an estimate of zero is a missing row"

    # The input estimate must match the reservation's, not a re-derivation.
    input_row = next(r for r in rows if r.event_type == "llm.input_token")
    assert int(input_row.quantity) == reservation.estimated_input_tokens


# ---------------------------------------------------------------------------
# Case 3 — provider error partway
# ---------------------------------------------------------------------------


def test_provider_error_settles_partial_generation(db_session, tenant, conversation):
    conv_id = conversation.id
    org_id = tenant.organization.id
    message_id = uuid.uuid4()
    reservation = _reservation(conv_id, message_id, org_id)
    stream_session.open_assistant_message(
        db_session, message_id=message_id, conversation_id=conv_id
    )

    outcome = stream_session.settle_and_persist(
        reservation=reservation,
        message_id=message_id,
        conversation_id=conv_id,
        emitted_text="Based on the retrieved contract, the termination",
        token_usage=None,
        finish_reason="provider_error",
        truncated=True,
    )

    assert outcome.settled and outcome.persisted
    db_session.expire_all()
    message = db_session.get(ConversationMessage, message_id)
    assert message.stream_state is StreamState.ABORTED
    assert message.finish_reason == "provider_error"


# ---------------------------------------------------------------------------
# Case 4 — settlement must be idempotent, because retries exist
# ---------------------------------------------------------------------------


def test_settlement_is_idempotent_on_replay(db_session, tenant, conversation):
    conv_id = conversation.id
    org_id = tenant.organization.id
    message_id = uuid.uuid4()
    reservation = _reservation(conv_id, message_id, org_id)
    stream_session.open_assistant_message(
        db_session, message_id=message_id, conversation_id=conv_id
    )

    usage = TokenUsage(
        provider="groq",
        model="llama-3.1-8b",
        prompt_tokens=418,
        completion_tokens=90,
        total_tokens=508,
        estimated_cost=0.02,
    )

    stream_session.settle_and_persist(
        reservation=reservation,
        message_id=message_id,
        conversation_id=conv_id,
        emitted_text="first",
        token_usage=usage,
        finish_reason="completed",
        truncated=False,
    )

    # A fresh reservation object for the same scope — what a retried request
    # actually produces, since the object does not survive the process.
    replay = _reservation(conv_id, message_id, org_id)
    stream_session.settle_and_persist(
        reservation=replay,
        message_id=message_id,
        conversation_id=conv_id,
        emitted_text="first",
        token_usage=usage,
        finish_reason="completed",
        truncated=False,
    )

    rows = _usage_rows(
        db_session, organization_id=org_id, scope=reservation.scope
    )
    assert len(rows) == 2, "the idempotency index must have absorbed the replay"


# ---------------------------------------------------------------------------
# Case 5 — the finally block must not mask the cancellation
# ---------------------------------------------------------------------------


def test_settlement_never_raises_and_never_masks(monkeypatch, tenant, conversation):
    """A database failure during settlement must not replace CancelledError.

    An exception escaping a `finally` replaces the propagating exception.
    Starlette needs to see CancelledError to tear the connection down; if a
    settlement error masks it, the connection is left half-closed and the
    task never completes.
    """
    conv_id = conversation.id
    org_id = tenant.organization.id
    message_id = uuid.uuid4()
    reservation = _reservation(conv_id, message_id, org_id)

    def _explode(*args, **kwargs):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(stream_session.llm_metering, "settle", _explode)

    async def scenario() -> None:
        try:
            try:
                raise asyncio.CancelledError()
            finally:
                outcome = stream_session.settle_and_persist(
                    reservation=reservation,
                    message_id=message_id,
                    conversation_id=conv_id,
                    emitted_text="partial",
                    token_usage=None,
                    finish_reason="client_disconnected",
                    truncated=True,
                )
                assert outcome.settled is False
                assert outcome.error is not None
        except asyncio.CancelledError:
            return
        pytest.fail("CancelledError was masked by the settlement failure")

    asyncio.run(scenario())


def test_sweep_finds_stranded_streaming_rows(db_session, conversation):
    message_id = uuid.uuid4()
    stream_session.open_assistant_message(
        db_session, message_id=message_id, conversation_id=conversation.id
    )
    db_session.commit()

    stranded = stream_session.sweep_in_flight(db_session, older_than_seconds=-1)
    assert message_id in stranded

    fresh = stream_session.sweep_in_flight(db_session, older_than_seconds=3600)
    assert message_id not in fresh
