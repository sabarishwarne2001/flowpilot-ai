"""ARCH-11.5 Step 1 — LLM spend ceilings and token metering.

Enforces pre-call spend limits (reserving prompt tokens and worst-case max output tokens)
and post-call settlement with true provider counts inside transactional savepoints.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.usage_event import UsageEvent
from app.services import spend_control_service as spend

logger = logging.getLogger("app.services.llm_metering")

INPUT_EVENT = "llm.input_token"
OUTPUT_EVENT = "llm.output_token"
RESOURCE_TYPE = "CONVERSATION"

_IDEMPOTENCY_INDEX = "uq_usage_events_org_idempotency_key"
_CHARS_PER_TOKEN = 3.5


class LLMMeteringError(RuntimeError):
    """Metering could not be performed; the caller must not call the provider."""


def estimate_prompt_tokens(prompt: str, *, model: Optional[str] = None) -> int:
    """Conservative pre-call estimate of prompt tokens."""
    if not prompt:
        return 0
    return max(1, int(len(prompt) / _CHARS_PER_TOKEN) + 1)


def _cost_micros(tokens: int, cost_per_1k: float) -> int:
    return int(round((tokens / 1000.0) * float(cost_per_1k or 0.0) * 1_000_000))


@dataclass
class LLMReservation:
    """A ceiling check that passed. Holds no usage rows until `settle`."""

    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID]
    conversation_id: uuid.UUID
    message_id: uuid.UUID
    estimated_input_tokens: int
    max_output_tokens: int
    input_cost_per_1k: float
    output_cost_per_1k: float
    settled: bool = False

    def key(self, suffix: str) -> str:
        return f"llm:{self.conversation_id}:{self.message_id}:{suffix}"

    def as_details(self) -> dict[str, Any]:
        return {
            "conversation_id": str(self.conversation_id),
            "message_id": str(self.message_id),
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_output_tokens": self.max_output_tokens,
        }


def _is_collision(exc: IntegrityError) -> bool:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint:
        return constraint == _IDEMPOTENCY_INDEX
    return _IDEMPOTENCY_INDEX in str(exc.orig)


def reserve(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    prompt: str,
    ai_settings: Any,
) -> LLMReservation:
    """Check both ceilings before the provider is called. Raises to refuse."""
    if not settings.LLM_METERING_ENABLED:
        logger.warning(
            "llm.metering_disabled",
            extra={"conversation_id": str(conversation_id)},
        )
        return LLMReservation(
            organization_id=organization_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            message_id=message_id,
            estimated_input_tokens=0,
            max_output_tokens=0,
            input_cost_per_1k=0.0,
            output_cost_per_1k=0.0,
        )

    estimated_input = estimate_prompt_tokens(prompt, model=getattr(ai_settings, "model", None))
    max_output = int(getattr(ai_settings, "max_output_tokens", 0) or 0)
    if max_output <= 0:
        raise LLMMeteringError(
            "ai_settings.max_output_tokens is unset. Without a provider-side "
            "output ceiling there is no worst case to check a limit against."
        )

    input_cost = float(getattr(ai_settings, "input_cost_per_1k_tokens", 0.0) or 0.0)
    output_cost = float(getattr(ai_settings, "output_cost_per_1k_tokens", 0.0) or 0.0)

    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=INPUT_EVENT,
        quantity=estimated_input,
        cost_micros=_cost_micros(estimated_input, input_cost),
        workspace_id=workspace_id,
    )
    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=OUTPUT_EVENT,
        quantity=max_output,
        cost_micros=_cost_micros(max_output, output_cost),
        workspace_id=workspace_id,
    )

    reservation = LLMReservation(
        organization_id=organization_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        message_id=message_id,
        estimated_input_tokens=estimated_input,
        max_output_tokens=max_output,
        input_cost_per_1k=input_cost,
        output_cost_per_1k=output_cost,
    )
    logger.info("llm.reserved", extra=reservation.as_details())
    return reservation


def _record(
    db: Session,
    *,
    reservation: LLMReservation,
    event_type: str,
    suffix: str,
    quantity: int,
    cost_per_1k: float,
    provider: str,
    model: str,
    details: dict[str, Any],
) -> bool:
    """One idempotent row inside a SAVEPOINT. False if already recorded."""
    from app.services import usage_service

    key = reservation.key(suffix)
    savepoint = db.begin_nested()
    try:
        usage_service.record_usage(
            db,
            organization_id=reservation.organization_id,
            event_type=event_type,
            quantity=Decimal(quantity),
            cost_micros=_cost_micros(quantity, cost_per_1k),
            workspace_id=reservation.workspace_id,
            resource_type=RESOURCE_TYPE,
            resource_id=reservation.conversation_id,
            provider=provider,
            idempotency_key=key,
            details={"model": model, **details},
        )
        savepoint.commit()
        return True
    except IntegrityError as exc:
        savepoint.rollback()
        if not _is_collision(exc):
            raise
        logger.info(
            "llm.already_billed",
            extra={"idempotency_key": key, **reservation.as_details()},
        )
        return False


def settle(
    db: Session,
    *,
    reservation: LLMReservation,
    token_usage: Any,
) -> dict[str, Any]:
    """Record the provider's true counts. Flushes; the caller commits."""
    if not settings.LLM_METERING_ENABLED or reservation.settled:
        return {}

    provider = str(getattr(token_usage, "provider", "unknown"))
    model = str(getattr(token_usage, "model", "unknown"))
    prompt_tokens = int(getattr(token_usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(token_usage, "completion_tokens", 0) or 0)

    drift = prompt_tokens - reservation.estimated_input_tokens
    recorded_input = _record(
        db,
        reservation=reservation,
        event_type=INPUT_EVENT,
        suffix="input",
        quantity=prompt_tokens,
        cost_per_1k=reservation.input_cost_per_1k,
        provider=provider,
        model=model,
        details={
            "estimated_tokens": reservation.estimated_input_tokens,
            "estimate_drift_tokens": drift,
        },
    )
    recorded_output = _record(
        db,
        reservation=reservation,
        event_type=OUTPUT_EVENT,
        suffix="output",
        quantity=completion_tokens,
        cost_per_1k=reservation.output_cost_per_1k,
        provider=provider,
        model=model,
        details={
            "max_output_tokens": reservation.max_output_tokens,
            "hit_output_ceiling": completion_tokens >= reservation.max_output_tokens,
        },
    )
    reservation.settled = True

    summary = {
        "provider": provider,
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimate_drift_tokens": drift,
        "recorded_input": recorded_input,
        "recorded_output": recorded_output,
    }
    logger.info("llm.settled", extra={**reservation.as_details(), **summary})
    return summary


def recorded_for_message(
    db: Session, *, organization_id: uuid.UUID, conversation_id: uuid.UUID,
    message_id: uuid.UUID,
) -> list[UsageEvent]:
    prefix = f"llm:{conversation_id}:{message_id}:"
    return list(
        db.execute(
            select(UsageEvent).where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.idempotency_key.startswith(prefix),
            )
        ).scalars().all()
    )


__all__ = [
    "INPUT_EVENT",
    "LLMMeteringError",
    "LLMReservation",
    "OUTPUT_EVENT",
    "estimate_prompt_tokens",
    "recorded_for_message",
    "reserve",
    "settle",
]