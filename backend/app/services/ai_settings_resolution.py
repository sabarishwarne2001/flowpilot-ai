"""ARCH-40 — what actually serves this workspace, and what it costs.

The AI settings page used to present eleven writable fields, four of which
nothing read. Three are dropped by the ARCH-40 contract migration; this module
exists for the other half of the problem, which is the fields the page did not
show at all.

A workspace administrator setting `model = "llama-3.1-8b-instant"` has no way,
before ARCH-40, to learn that their organization has an enabled
`tenant_model_routes` row for EXTRACTION pointing somewhere else — so the value
they typed is a default that is never reached. `resolve` answers the question
the page should have been answering: what runs, where the decision came from,
what the platform charges for it, and what is standing in the way.

NOTHING HERE IS WRITABLE
========================

Every field is read-through. `tenant_model_routes` owns routing for a routed
task and `ai_settings` owns the workspace default; ARCH-22 already documented
that layering and ARCH-40 does not change it. What ARCH-40 changes is that the
console now shows which one won, instead of showing the loser and implying it
is in force.

That is the "name one owner and make the other a read-through" rule applied to
a case where both values are legitimately live at different scopes. The wrong
fix would have been to delete one of them.

PRICES ARE NEVER LITERALS
=========================

`price_per_1m_input_micros` comes from `pricing_service.resolve` against the
price book in force. `PriceUnavailableError` yields None, never 0 — ARCH-39
removed the last hard-coded `0.0` from the cost path and a zero here would put
one back where an administrator would read it as free.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.ai_settings_resolution")

#: The task the AI settings page describes. Extraction is the pipeline task a
#: workspace's model choice actually governs; the assistant and embedding
#: tasks have their own routes and their own pages.
PRIMARY_TASK_TYPE: str = "EXTRACTION"


@dataclass
class ResolvedModel:
    provider: str
    model_name: str
    #: "route_rule" when tenant_model_routes decided, "ai_settings_default"
    #: when the workspace's own row did.
    origin: str
    uses_tenant_key: bool
    downgrade_reason: Optional[str] = None


@dataclass
class ResolvedPricing:
    currency: Optional[str] = None
    price_per_1m_input_micros: Optional[int] = None
    price_book_version: Optional[int] = None
    #: True when the book had no entry for this exact model and a provider
    #: fallback entry was used. The console says so rather than implying the
    #: number is exact.
    fallback: bool = False


@dataclass
class SpendLimitState:
    configured: bool = False
    hard_stop: bool = False
    max_cost_micros: Optional[int] = None
    period: Optional[str] = None


@dataclass
class ResolvedAISettings:
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    resolved: ResolvedModel
    pricing: ResolvedPricing
    spend_limit: SpendLimitState
    byok_configured: bool
    #: ARCH40-S1:streaming-reader. `ai_settings.enable_streaming` had no reader
    #: anywhere in the product before this line. It is reported here, and the
    #: console shows it, which is why the column survives the contract
    #: migration that takes the other three.
    streaming_enabled: bool
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "workspace_id": str(self.workspace_id),
            "organization_id": str(self.organization_id),
            "resolved_provider": self.resolved.provider,
            "resolved_model": self.resolved.model_name,
            "resolution_origin": self.resolved.origin,
            "uses_tenant_key": self.resolved.uses_tenant_key,
            "downgrade_reason": self.resolved.downgrade_reason,
            "currency": self.pricing.currency,
            "price_per_1m_input_micros": self.pricing.price_per_1m_input_micros,
            "price_book_version": self.pricing.price_book_version,
            "price_is_fallback": self.pricing.fallback,
            "spend_limit_configured": self.spend_limit.configured,
            "spend_limit_hard_stop": self.spend_limit.hard_stop,
            "spend_limit_max_cost_micros": self.spend_limit.max_cost_micros,
            "spend_limit_period": self.spend_limit.period,
            "byok_configured": self.byok_configured,
            "streaming_enabled": self.streaming_enabled,
            "warnings": list(self.warnings),
        }


def _spend_limit(db: Session, *, organization_id: uuid.UUID) -> SpendLimitState:
    from app.models.spend_limit import SpendLimit

    row = db.execute(
        select(SpendLimit)
        .where(
            SpendLimit.organization_id == organization_id,
            SpendLimit.is_active.is_(True),
        )
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return SpendLimitState()
    period = getattr(row.period, "value", row.period)
    return SpendLimitState(
        configured=True,
        hard_stop=bool(row.hard_stop),
        max_cost_micros=row.max_cost_micros,
        period=str(period) if period is not None else None,
    )


def _byok_configured(db: Session, *, organization_id: uuid.UUID) -> bool:
    try:
        from app.models.byok import TenantProviderCredential

        return (
            db.execute(
                select(TenantProviderCredential.id)
                .where(TenantProviderCredential.organization_id == organization_id)
                .limit(1)
            ).scalar_one_or_none()
            is not None
        )
    except Exception:  # pragma: no cover - BYOK is optional
        logger.debug("BYOK lookup failed; reporting not configured", exc_info=True)
        return False


def _pricing(
    db: Session, *, provider: str, model_name: str
) -> tuple[ResolvedPricing, Optional[str]]:
    from app.services import pricing_service

    try:
        price = pricing_service.resolve(
            db,
            event_type="llm.tokens_in",
            provider=provider,
            model=model_name,
            at=datetime.now(timezone.utc),
        )
    except Exception as exc:  # PriceUnavailableError and friends
        return (
            ResolvedPricing(),
            f"No price-book entry for {provider}/{model_name}: {exc}",
        )

    unit_price = getattr(price, "unit_price_micros", None)
    per_million: Optional[int] = None
    if unit_price is not None:
        # The book prices per token; the console speaks per million.
        per_million = int(Decimal(unit_price) * Decimal(1_000_000))

    return (
        ResolvedPricing(
            currency=getattr(price, "currency", None),
            price_per_1m_input_micros=per_million,
            price_book_version=getattr(price, "price_book_version", None),
            fallback=bool(getattr(price, "fallback", False)),
        ),
        None,
    )


def resolve(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    ai_settings: Any,
) -> ResolvedAISettings:
    """What serves this workspace's extraction, and what it costs."""
    from app.services.byok import model_routing_service

    warnings: list[str] = []

    decision = model_routing_service.resolve(
        db,
        organization_id=organization_id,
        task_type=PRIMARY_TASK_TYPE,
        ai_settings=ai_settings,
    )

    resolved = ResolvedModel(
        provider=str(decision.provider),
        model_name=str(decision.model_name),
        origin=str(decision.origin),
        uses_tenant_key=bool(decision.use_tenant_key),
        downgrade_reason=getattr(decision, "downgrade_reason", None),
    )
    if resolved.downgrade_reason:
        warnings.append(resolved.downgrade_reason)

    configured_model = str(getattr(ai_settings, "model", "") or "")
    if resolved.origin != "ai_settings_default" and configured_model and (
        configured_model != resolved.model_name
    ):
        warnings.append(
            f"This workspace is set to {configured_model}, but an organization "
            f"routing rule sends extraction to {resolved.model_name}. The "
            "routing rule wins; the workspace value is the fallback if the "
            "rule is disabled."
        )

    pricing, pricing_warning = _pricing(
        db, provider=resolved.provider, model_name=resolved.model_name
    )
    if pricing_warning:
        warnings.append(pricing_warning)

    return ResolvedAISettings(
        workspace_id=workspace_id,
        organization_id=organization_id,
        resolved=resolved,
        pricing=pricing,
        spend_limit=_spend_limit(db, organization_id=organization_id),
        byok_configured=_byok_configured(db, organization_id=organization_id),
        streaming_enabled=bool(getattr(ai_settings, "enable_streaming", True)),
        warnings=warnings,
    )


__all__ = [
    "PRIMARY_TASK_TYPE",
    "ResolvedAISettings",
    "ResolvedModel",
    "ResolvedPricing",
    "SpendLimitState",
    "resolve",
]
