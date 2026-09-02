"""ARCH-22 §4.3 — per-tenant resolution of (task -> provider, model, key).

This service DECIDES. It does not build clients and it does not decrypt: that
is `provider_clients`. Keeping the decision separable is what lets the routing
table be previewed in the console without a single credential being touched.

RESOLUTION ORDER
================

    1. An enabled `tenant_model_routes` row for this (organization, task).
    2. Otherwise the workspace's `ai_settings` — the pre-ARCH-22 behaviour,
       unchanged for every tenant that never opens the BYOK console.

A route whose provider is not routable resolves with `use_tenant_key` forced
to False and a stated reason. It does NOT raise: a tenant who pointed
EXTRACTION at Gemini should keep getting extractions on the platform account,
with the console telling them why, rather than a broken pipeline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.byok_providers import (
    BYOK_TASK_TYPE_VALUES,
    is_routable,
    normalize_provider,
    normalize_task_type,
    spec_for,
    unroutable_reason,
)
from app.models.byok import TenantModelRoute
from app.services.byok import credential_service

logger = logging.getLogger("app.services.byok.model_routing_service")


class RoutingError(ValueError):
    """A routing rule could not be resolved or stored."""


class UnroutableProviderError(RoutingError):
    """A rule targets a provider the execution layer cannot use with a tenant key."""


@dataclass(frozen=True)
class RoutingDecision:
    """What will serve one task for one tenant, and on whose account."""

    task_type: str
    provider: str
    model_name: str

    #: True only when a tenant credential is present, active, and the target
    #: provider is routable. This is the input to
    #: `ProviderClientFactory.build(prefer_tenant_key=...)`, not a promise:
    #: the factory re-checks everything before it decrypts anything.
    use_tenant_key: bool

    #: Where the decision came from: "route_rule" or "ai_settings_default".
    origin: str

    #: Set when `use_tenant_key` was requested but denied.
    downgrade_reason: Optional[str] = None

    def as_details(self) -> dict[str, Any]:
        return {
            "route_task_type": self.task_type,
            "route_provider": self.provider,
            "route_model": self.model_name,
            "route_use_tenant_key": self.use_tenant_key,
            "route_origin": self.origin,
            "route_downgrade_reason": self.downgrade_reason,
        }


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def list_routes(
    db: Session, *, organization_id: uuid.UUID
) -> list[TenantModelRoute]:
    return list(
        db.execute(
            select(TenantModelRoute)
            .where(TenantModelRoute.organization_id == organization_id)
            .order_by(TenantModelRoute.task_type.asc())
        )
        .scalars()
        .all()
    )


def get_route(
    db: Session, *, organization_id: uuid.UUID, task_type: str
) -> Optional[TenantModelRoute]:
    return db.execute(
        select(TenantModelRoute).where(
            TenantModelRoute.organization_id == organization_id,
            TenantModelRoute.task_type == normalize_task_type(task_type),
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def resolve(
    db: Session,
    *,
    organization_id: uuid.UUID,
    task_type: str,
    ai_settings: Any = None,
) -> RoutingDecision:
    """Decide what serves this task, without touching any credential material."""
    task = normalize_task_type(task_type)
    if task not in BYOK_TASK_TYPE_VALUES:
        raise RoutingError(
            f"'{task_type}' is not a known task type. Expected one of: "
            f"{', '.join(BYOK_TASK_TYPE_VALUES)}."
        )

    route = get_route(db, organization_id=organization_id, task_type=task)

    if route is None or not route.is_enabled:
        provider = _provider_of(ai_settings)
        model = str(getattr(ai_settings, "model", "") or "")
        return RoutingDecision(
            task_type=task,
            provider=provider,
            model_name=model,
            use_tenant_key=False,
            origin="ai_settings_default",
            downgrade_reason=(
                "no_route_rule_configured"
                if route is None
                else "route_rule_disabled"
            ),
        )

    provider = normalize_provider(route.provider)
    wants_tenant_key = bool(route.use_tenant_key)

    if not wants_tenant_key:
        return RoutingDecision(
            task_type=task,
            provider=provider,
            model_name=route.model_name,
            use_tenant_key=False,
            origin="route_rule",
            downgrade_reason="route_rule_requests_platform_key",
        )

    if not is_routable(provider):
        logger.info(
            "byok.route_downgraded_unroutable",
            extra={
                "organization_id": str(organization_id),
                "task_type": task,
                "provider": provider,
            },
        )
        return RoutingDecision(
            task_type=task,
            provider=provider,
            model_name=route.model_name,
            use_tenant_key=False,
            origin="route_rule",
            downgrade_reason=f"provider_unroutable: {unroutable_reason(provider)}",
        )

    credential = credential_service.resolve_active(
        db, organization_id=organization_id, provider=provider
    )
    if credential is None:
        return RoutingDecision(
            task_type=task,
            provider=provider,
            model_name=route.model_name,
            use_tenant_key=False,
            origin="route_rule",
            downgrade_reason="no_tenant_credential_configured",
        )

    if credential.validation_error:
        # A key we know is broken. Attempting it would burn a request and a
        # retry budget to rediscover what the last validation already proved.
        logger.warning(
            "byok.route_downgraded_invalid_credential",
            extra={
                "organization_id": str(organization_id),
                "task_type": task,
                "provider": provider,
            },
        )
        return RoutingDecision(
            task_type=task,
            provider=provider,
            model_name=route.model_name,
            use_tenant_key=False,
            origin="route_rule",
            downgrade_reason="tenant_credential_last_validation_failed",
        )

    return RoutingDecision(
        task_type=task,
        provider=provider,
        model_name=route.model_name,
        use_tenant_key=True,
        origin="route_rule",
        downgrade_reason=None,
    )


def _provider_of(ai_settings: Any) -> str:
    """Mirror of `llm_metering._provider_of`, normalised upward.

    The metering module lowercases because the price book is keyed in
    lowercase; the BYOK vocabulary is uppercase. Converting here keeps the
    conversion in one place instead of at four call sites.
    """
    raw = getattr(ai_settings, "provider", None)
    value = getattr(raw, "value", raw)
    return normalize_provider(str(value or ""))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def upsert_route(
    db: Session,
    *,
    organization_id: uuid.UUID,
    task_type: str,
    provider: str,
    model_name: str,
    use_tenant_key: bool,
    is_enabled: bool = True,
) -> TenantModelRoute:
    """Create or replace the single rule for one task.

    Refuses `use_tenant_key=True` against a non-routable provider with a
    RoutingError, which the API layer renders as a 422. Accepting it and
    silently downgrading at execution time would let a tenant save a rule that
    reads "Anthropic, my key" in the console while every request goes to our
    Groq account. The rule has to be as true as the badge.
    """
    task = normalize_task_type(task_type)
    key = normalize_provider(provider)
    spec = spec_for(key)

    if task not in BYOK_TASK_TYPE_VALUES:
        raise RoutingError(
            f"'{task_type}' is not a known task type. Expected one of: "
            f"{', '.join(BYOK_TASK_TYPE_VALUES)}."
        )

    model = str(model_name or "").strip()
    if not model:
        raise RoutingError("A routing rule needs a model name.")

    if use_tenant_key and not spec.is_routable:
        raise UnroutableProviderError(
            f"{spec.label} cannot serve traffic on a tenant key in this "
            f"build. {spec.unroutable_reason} Save the rule with "
            "'Use platform key' instead, or choose a routable provider."
        )

    existing = get_route(db, organization_id=organization_id, task_type=task)

    if existing is None:
        route = TenantModelRoute(
            id=uuid.uuid4(),
            organization_id=organization_id,
            task_type=task,
            provider=key,
            model_name=model,
            use_tenant_key=bool(use_tenant_key),
            is_enabled=bool(is_enabled),
        )
        db.add(route)
        db.flush()
        logger.info(
            "byok.route_created",
            extra={
                "organization_id": str(organization_id),
                "task_type": task,
                "provider": key,
                "model": model,
                "use_tenant_key": bool(use_tenant_key),
            },
        )
        return route

    existing.provider = key
    existing.model_name = model
    existing.use_tenant_key = bool(use_tenant_key)
    existing.is_enabled = bool(is_enabled)
    db.flush()
    logger.info(
        "byok.route_updated",
        extra={
            "organization_id": str(organization_id),
            "task_type": task,
            "provider": key,
            "model": model,
            "use_tenant_key": bool(use_tenant_key),
        },
    )
    return existing


def delete_route(
    db: Session, *, organization_id: uuid.UUID, task_type: str
) -> bool:
    """Remove a rule, returning the task to its ai_settings default."""
    route = get_route(db, organization_id=organization_id, task_type=task_type)
    if route is None:
        return False
    db.delete(route)
    db.flush()
    logger.info(
        "byok.route_deleted",
        extra={
            "organization_id": str(organization_id),
            "task_type": normalize_task_type(task_type),
        },
    )
    return True


__all__ = [
    "RoutingDecision",
    "RoutingError",
    "UnroutableProviderError",
    "delete_route",
    "get_route",
    "list_routes",
    "resolve",
    "upsert_route",
]