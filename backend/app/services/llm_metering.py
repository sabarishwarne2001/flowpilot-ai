"""ARCH-11.5 Step 1, ARCH-12 Step 1/3, ARCH-14 Step 1 & ARCH-14 Step 4 — LLM spend ceilings, token metering and overages."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.supplier_cogs import SOURCE_ZERO_BYOK
from app.models.usage_event import UsageEvent
from app.services import pricing_service
from app.services import spend_control_service as spend
from app.services.pricing_service import PriceUnavailableError, ResolvedPrice

logger = logging.getLogger("app.services.llm_metering")

INPUT_EVENT = "llm.input_token"
OUTPUT_EVENT = "llm.output_token"
CONVERSATION_RESOURCE = "CONVERSATION"
WORK_ITEM_RESOURCE = "WORK_ITEM"
ORGANIZATION_RESOURCE = "ORGANIZATION"

ENRICH_OPERATIONS = ("classify", "entities", "summary")

_IDEMPOTENCY_INDEX = "uq_usage_events_org_idempotency_key"
_CHARS_PER_TOKEN = 3.5


class LLMMeteringError(RuntimeError):
    """Metering could not be performed; the caller must not call the provider."""


def estimate_prompt_tokens(prompt: str, *, model: Optional[str] = None) -> int:
    if not prompt:
        return 0
    return max(1, int(len(prompt) / _CHARS_PER_TOKEN) + 1)


def estimate_enrichment_tokens(prompts: dict[str, str]) -> int:
    return sum(estimate_prompt_tokens(prompt) for prompt in prompts.values())


def _provider_of(ai_settings: Any) -> str:
    raw = getattr(ai_settings, "provider", None)
    value = getattr(raw, "value", raw)
    return str(value or "").strip().lower()


@dataclass
class LLMReservation:
    organization_id: uuid.UUID
    workspace_id: Optional[uuid.UUID]
    scope: str
    resource_type: str
    resource_id: uuid.UUID
    estimated_input_tokens: int
    max_output_tokens: int
    input_price: Optional[ResolvedPrice] = None
    output_price: Optional[ResolvedPrice] = None
    settled: bool = False
    details_extra: dict[str, Any] = field(default_factory=dict)

    # ARCH-22 B3. The receipt naming the key that ACTUALLY served the call,
    # attached by the caller after ProviderClientFactory.build() returns and
    # before settle(). None means the platform account paid, which is the
    # correct assumption when nobody has said otherwise.
    #
    # This is deliberately not a bool. A reservation records intent; a receipt
    # records what happened. `llm_resilience.execute` can fail over between
    # them, and stamping ZERO_BYOK on intent would mark real supplier spend as
    # free — the ARCH-18 zero-cost hazard, inverted. See `_byok_applies`.
    credential_use: Optional[Any] = None

    @property
    def input_cost_per_1k(self) -> float:
        if self.input_price is None:
            return 0.0
        return pricing_service.per_1k_from_unit_micros(
            self.input_price.unit_price_micros
        )

    @property
    def output_cost_per_1k(self) -> float:
        if self.output_price is None:
            return 0.0
        return pricing_service.per_1k_from_unit_micros(
            self.output_price.unit_price_micros
        )

    def key(self, suffix: str) -> str:
        return f"{self.scope}:{suffix}"

    def as_details(self) -> dict[str, Any]:
        details = {
            "scope": self.scope,
            "resource_type": self.resource_type,
            "resource_id": str(self.resource_id),
            "estimated_input_tokens": self.estimated_input_tokens,
            "max_output_tokens": self.max_output_tokens,
            "price_book_version": (
                self.input_price.price_book_version if self.input_price else None
            ),
        }
        if self.credential_use is not None:
            details.update(self.credential_use.as_details())
        return details

    def attach_credential_use(self, credential_use: Any) -> None:
        """Record which key served this call. Called once, after execution.

        Kept as a method rather than a bare assignment so the verification
        gate has a name to grep for and so the one-way nature is documented at
        the point of use.
        """
        self.credential_use = credential_use


def _is_collision(exc: IntegrityError) -> bool:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint:
        return constraint == _IDEMPOTENCY_INDEX
    return _IDEMPOTENCY_INDEX in str(exc.orig)


def _reserve(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    scope: str,
    resource_type: str,
    resource_id: uuid.UUID,
    prompt: str,
    ai_settings: Any,
    details_extra: Optional[dict[str, Any]] = None,
) -> LLMReservation:
    if not settings.LLM_METERING_ENABLED:
        logger.warning("llm.metering_disabled", extra={"scope": scope})
        return LLMReservation(
            organization_id=organization_id,
            workspace_id=workspace_id,
            scope=scope,
            resource_type=resource_type,
            resource_id=resource_id,
            estimated_input_tokens=0,
            max_output_tokens=0,
            details_extra=dict(details_extra or {}),
        )

    estimated_input = estimate_prompt_tokens(prompt)
    max_output = int(getattr(ai_settings, "max_output_tokens", 0) or 0)
    if max_output <= 0:
        raise LLMMeteringError(
            "ai_settings.max_output_tokens is unset. Without a provider-side "
            "output ceiling there is no worst case to check a limit against."
        )

    provider = _provider_of(ai_settings)
    model = getattr(ai_settings, "model", None)
    at = datetime.now(timezone.utc)

    try:
        input_price = pricing_service.resolve(
            db, event_type=INPUT_EVENT, provider=provider, model=model, at=at
        )
        output_price = pricing_service.resolve(
            db, event_type=OUTPUT_EVENT, provider=provider, model=model, at=at
        )
    except PriceUnavailableError as exc:
        logger.error(
            "llm.price_unavailable",
            extra={
                "scope": scope,
                "provider": provider,
                "model": model,
                "organization_id": str(organization_id),
            },
        )
        raise LLMMeteringError(
            f"No published price covers {provider}/{model}. Refusing the "
            "generation: an unpriced call is revenue that cannot be invoiced "
            "and spend that cannot be capped."
        ) from exc

    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=INPUT_EVENT,
        quantity=estimated_input,
        cost_micros=input_price.cost_micros(estimated_input),
        workspace_id=workspace_id,
    )
    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=OUTPUT_EVENT,
        quantity=max_output,
        cost_micros=output_price.cost_micros(max_output),
        workspace_id=workspace_id,
    )

    reservation = LLMReservation(
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope=scope,
        resource_type=resource_type,
        resource_id=resource_id,
        estimated_input_tokens=estimated_input,
        max_output_tokens=max_output,
        details_extra=dict(details_extra or {}),
        input_price=input_price,
        output_price=output_price,
    )
    logger.info("llm.reserved", extra=reservation.as_details())
    return reservation


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
    return _reserve(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope=f"llm:{conversation_id}:{message_id}",
        resource_type=CONVERSATION_RESOURCE,
        resource_id=conversation_id,
        prompt=prompt,
        ai_settings=ai_settings,
    )


def reserve_for_enrichment(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    work_item_id: uuid.UUID,
    operation: str,
    prompt: str,
    ai_settings: Any,
) -> LLMReservation:
    if operation not in ENRICH_OPERATIONS:
        raise LLMMeteringError(
            f"unknown enrichment operation {operation!r}; expected one of {ENRICH_OPERATIONS}"
        )
    return _reserve(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope=f"llm:{work_item_id}:enrich:{operation}",
        resource_type=WORK_ITEM_RESOURCE,
        resource_id=work_item_id,
        prompt=prompt,
        ai_settings=ai_settings,
    )


def reserve_for_summary(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    conversation_id: uuid.UUID,
    turn: int,
    prompt: str,
    ai_settings: Any,
) -> LLMReservation:
    return _reserve(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope=f"llm:{conversation_id}:summary:{turn}",
        resource_type=CONVERSATION_RESOURCE,
        resource_id=conversation_id,
        prompt=prompt,
        ai_settings=ai_settings,
    )


_CALLER_SCOPE_PREFIXES: tuple[str, ...] = ("llm:automation:",)
_VERIFY_SCOPE_MARKER: str = ":verify:"


def _assert_caller_scope(scope: str) -> None:
    if scope.startswith(_CALLER_SCOPE_PREFIXES):
        return
    if scope.startswith("llm:") and _VERIFY_SCOPE_MARKER in scope:
        return
    raise LLMMeteringError(
        f"scope {scope!r} is not a recognised caller-supplied scope. "
        f"Expected one of {_CALLER_SCOPE_PREFIXES} or "
        f"'llm:<work_item_id>{_VERIFY_SCOPE_MARKER}<n>'."
    )


def reserve_for_node(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    work_item_id: Optional[uuid.UUID],
    scope: str,
    prompt: str,
    ai_settings: Any,
    details_extra: Optional[dict[str, Any]] = None,
) -> LLMReservation:
    _assert_caller_scope(scope)
    return _reserve(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        scope=scope,
        resource_type=WORK_ITEM_RESOURCE if work_item_id else ORGANIZATION_RESOURCE,
        resource_id=work_item_id or organization_id,
        prompt=prompt,
        ai_settings=ai_settings,
        details_extra=details_extra,
    )


def reserve_for_verification(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    work_item_id: uuid.UUID,
    agent_index: int,
    prompt: str,
    ai_settings: Any,
) -> LLMReservation:
    return reserve_for_node(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_id=work_item_id,
        scope=f"llm:{work_item_id}:verify:{agent_index}",
        prompt=prompt,
        ai_settings=ai_settings,
        details_extra={"operation": "verify", "agent_index": agent_index},
    )


def already_recorded(
    db: Session, *, organization_id: uuid.UUID, scope: str
) -> bool:
    return (
        db.execute(
            select(UsageEvent.id)
            .where(
                UsageEvent.organization_id == organization_id,
                UsageEvent.idempotency_key == f"{scope}:input",
            )
            .limit(1)
        ).first()
        is not None
    )


def _settlement_price(
    db: Session,
    *,
    event_type: str,
    provider: str,
    model: str,
    occurred_at: datetime,
    reserved: Optional[ResolvedPrice],
) -> Optional[ResolvedPrice]:
    try:
        return pricing_service.resolve(
            db,
            event_type=event_type,
            provider=provider,
            model=model,
            at=occurred_at,
        )
    except PriceUnavailableError:
        if reserved is not None and reserved.provider == provider:
            logger.warning(
                "llm.settle_price_reused_from_reservation",
                extra={
                    "event_type": event_type,
                    "provider": provider,
                    "model": model,
                    "price_book_version": reserved.price_book_version,
                },
            )
            return reserved
        logger.critical(
            "llm.settle_price_unavailable",
            extra={
                "event_type": event_type,
                "provider": provider,
                "model": model,
                "occurred_at": occurred_at.isoformat(),
            },
        )
        return None


def _byok_applies(
    reservation: LLMReservation, *, settled_provider: str
) -> tuple[bool, Optional[str]]:
    """Decide whether this event may be stamped ZERO_BYOK. ARCH-22 B3.

    Three conditions, all required:

      1. A receipt exists. No receipt means the platform account paid.
      2. The receipt says a TENANT key served the call and did not fall back.
      3. The provider that actually ANSWERED is the provider the receipt names.

    Condition 3 is the one that matters and the one a naive implementation
    omits. `llm_resilience.execute` can fail over from the reserved provider
    to `LLM_FALLBACK_PROVIDER` mid-call. `settle` reads the provider back off
    `token_usage`, so by the time we get here `settled_provider` is ground
    truth. If it disagrees with the receipt, the tokens were bought on the
    platform's supplier contract and stamping them zero would report real COGS
    as free — a 100% margin on spend we actually made. ARCH-18 forbids the
    unknown-reads-as-zero direction of this error; this is the same error
    pointing the other way.

    Returns (apply_zero_cogs, mismatch_reason).
    """
    use = reservation.credential_use
    if use is None:
        return False, None

    if not getattr(use, "is_zero_cogs", False):
        return False, getattr(use, "reason", None)

    receipt_provider = str(getattr(use, "provider", "")).strip().lower()
    actual_provider = str(settled_provider or "").strip().lower()

    if receipt_provider != actual_provider:
        logger.critical(
            "llm.byok_failover_cost_reattributed",
            extra={
                "scope": reservation.scope,
                "organization_id": str(reservation.organization_id),
                "receipt_provider": receipt_provider,
                "settled_provider": actual_provider,
                "key_fingerprint": getattr(use, "key_fingerprint", None),
            },
        )
        return False, (
            f"byok_receipt_provider_mismatch: reserved on {receipt_provider}, "
            f"settled on {actual_provider}; cost re-attributed to the price "
            "book because the platform's supplier account served this call"
        )

    return True, None


def _record(
    db: Session,
    *,
    reservation: LLMReservation,
    event_type: str,
    suffix: str,
    quantity: int,
    price: Optional[ResolvedPrice],
    provider: str,
    model: str,
    occurred_at: datetime,
    details: dict[str, Any],
) -> bool:
    from app.services import usage_service

    key = reservation.key(suffix)

    if price is not None:
        cost = price.cost_micros(quantity)
        price_details = price.as_details()
        price_book_id = price.price_book_id
        unit_price_micros = price.unit_price_micros
        # ARCH-18. Denormalised from the same ResolvedPrice that produced the
        # revenue figure, so both sides of the margin come from one book
        # version at one instant. None when the entry carries no cost basis —
        # honest unknown, never a zero.
        cost_basis_micros = price.cost_basis_for(quantity)
        cost_basis_source = price.cost_basis_source
    else:
        cost = None
        price_details = {
            "price_source": "unavailable",
            "price_unavailable": True,
            "price_unavailable_provider": provider,
            "price_unavailable_model": model,
        }
        price_book_id = None
        unit_price_micros = None
        cost_basis_micros = None
        cost_basis_source = None

    # ARCH-22 §3.3. The tenant's own provider account was billed for these
    # tokens, so FlowPilot's supplier cost for them is genuinely zero. Revenue
    # is deliberately NOT touched: we still charge for the platform service,
    # and `cost_micros` / `unit_price_micros` stay exactly as the price book
    # resolved them. Only the COST half becomes zero.
    #
    # ZERO_BYOK is already in HARD_COST_BASIS_SOURCES (models/supplier_cogs),
    # so ARCH-18 margin reporting counts this at a truthful 100% rather than
    # excluding it — this is the one case where a 100% margin is a fact and
    # not the silent-zero defect the invariant guards against.
    byok_zero, mismatch_reason = _byok_applies(reservation, settled_provider=provider)
    byok_details: dict[str, Any] = {}
    if byok_zero:
        cost_basis_micros = Decimal(0)
        cost_basis_source = SOURCE_ZERO_BYOK
        byok_details["cost_basis_zeroed_by"] = "byok_tenant_key"
    elif mismatch_reason:
        byok_details["byok_not_applied_reason"] = mismatch_reason

    savepoint = db.begin_nested()
    try:
        usage_service.record_usage(
            db,
            organization_id=reservation.organization_id,
            event_type=event_type,
            quantity=Decimal(quantity),
            cost_micros=cost,
            price_book_id=price_book_id,
            unit_price_micros=unit_price_micros,
            cost_basis_micros=cost_basis_micros,
            cost_basis_source=cost_basis_source,
            workspace_id=reservation.workspace_id,
            resource_type=reservation.resource_type,
            resource_id=reservation.resource_id,
            provider=provider,
            occurred_at=occurred_at,
            idempotency_key=key,
            details={
                "model": model,
                **price_details,
                **byok_details,
                **reservation.details_extra,
                **details,
            },
        )
        savepoint.commit()
    except IntegrityError as exc:
        savepoint.rollback()
        if not _is_collision(exc):
            raise
        logger.info(
            "llm.already_billed",
            extra={"idempotency_key": key, **reservation.as_details()},
        )
        return False

    from app.services import quota_service

    quota_service.bill_overage_if_any(
        db,
        organization_id=reservation.organization_id,
        event_type=event_type,
        quantity=quantity,
        workspace_id=reservation.workspace_id,
        occurred_at=occurred_at,
        idempotency_key=key,
        provider=provider,
        model=model,
        resource_type=reservation.resource_type,
        resource_id=reservation.resource_id,
    )
    return True


def settle(
    db: Session,
    *,
    reservation: LLMReservation,
    token_usage: Any,
    estimated: bool = False,
) -> dict[str, Any]:
    if not settings.LLM_METERING_ENABLED or reservation.settled:
        return {}

    provider = str(getattr(token_usage, "provider", "unknown")).strip().lower()
    model = str(getattr(token_usage, "model", "unknown"))
    prompt_tokens = int(getattr(token_usage, "prompt_tokens", 0) or 0)
    completion_tokens = int(getattr(token_usage, "completion_tokens", 0) or 0)
    occurred_at = datetime.now(timezone.utc)

    input_price = _settlement_price(
        db,
        event_type=INPUT_EVENT,
        provider=provider,
        model=model,
        occurred_at=occurred_at,
        reserved=reservation.input_price,
    )
    output_price = _settlement_price(
        db,
        event_type=OUTPUT_EVENT,
        provider=provider,
        model=model,
        occurred_at=occurred_at,
        reserved=reservation.output_price,
    )

    failed_over = (
        reservation.input_price is not None
        and reservation.input_price.provider != provider
    )
    if failed_over:
        logger.warning(
            "llm.settled_on_fallback_provider",
            extra={
                "reserved_provider": reservation.input_price.provider,
                "settled_provider": provider,
                "scope": reservation.scope,
            },
        )

    drift = prompt_tokens - reservation.estimated_input_tokens
    recorded_input = _record(
        db,
        reservation=reservation,
        event_type=INPUT_EVENT,
        suffix="input",
        quantity=prompt_tokens,
        price=input_price,
        provider=provider,
        model=model,
        occurred_at=occurred_at,
        details={
            "estimated_tokens": reservation.estimated_input_tokens,
            "estimate_drift_tokens": drift,
            "estimated": estimated,
            "failed_over": failed_over,
        },
    )
    recorded_output = _record(
        db,
        reservation=reservation,
        event_type=OUTPUT_EVENT,
        suffix="output",
        quantity=completion_tokens,
        price=output_price,
        provider=provider,
        model=model,
        occurred_at=occurred_at,
        details={
            "max_output_tokens": reservation.max_output_tokens,
            "hit_output_ceiling": completion_tokens >= reservation.max_output_tokens,
            "estimated": estimated,
            "failed_over": failed_over,
        },
    )
    reservation.settled = True

    input_cost = input_price.cost_micros(prompt_tokens) if input_price else 0
    output_cost = (
        output_price.cost_micros(completion_tokens) if output_price else 0
    )

    summary = {
        "provider": provider,
        "model": model,
        "input_cost_micros": int(input_cost),
        "output_cost_micros": int(output_cost),
        "total_cost_micros": int(input_cost) + int(output_cost),
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "estimate_drift_tokens": drift,
        "recorded_input": recorded_input,
        "recorded_output": recorded_output,
        "estimated": estimated,
        "price_book_version": (
            input_price.price_book_version if input_price else None
        ),
        "price_fallback": bool(input_price and input_price.fallback),
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
    "CONVERSATION_RESOURCE",
    "ENRICH_OPERATIONS",
    "INPUT_EVENT",
    "LLMMeteringError",
    "LLMReservation",
    "OUTPUT_EVENT",
    "WORK_ITEM_RESOURCE",
    "already_recorded",
    "estimate_enrichment_tokens",
    "estimate_prompt_tokens",
    "recorded_for_message",
    "ORGANIZATION_RESOURCE",
    "reserve",
    "reserve_for_enrichment",
    "reserve_for_node",
    "reserve_for_summary",
    "reserve_for_verification",
    "settle",
]
