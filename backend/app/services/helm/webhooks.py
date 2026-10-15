"""Helm's outgoing webhooks (Enterprise): `helm.plan.ready`, `helm.action.executed`, `helm.mandate.breached`.

THROUGH THE PLATFORM'S WEBHOOK SERVICE, NEVER A COPY OF IT
==========================================================

An event is one PUBLIC outbox row (`outbox_service.emit`) written in the caller's transaction, so a
plan or an action that rolls back takes its event with it. The relay then fans it out to every
ACTIVE endpoint of the organization subscribed to it (`webhook_service.fan_out_event`), and the
delivery loop signs it (HMAC-SHA256 over the exact bytes sent), sends it through the egress gate
(`egress.WEBHOOK`), records every attempt and retries, like every other event (ARCH-09).

WHO EMITS, AND WHAT A PAYLOAD MAY CARRY
=======================================

Only an organization whose plan holds `capability.helm_api` (Enterprise), never the sample company,
and only when an endpoint listens for that event (no row is written for nobody). A webhook leaves
the platform for whatever URL a tenant configured, so a payload carries ids and figures (amounts as
integer micros with their currency, dates, counts, states, digests), never document text, a
counterparty's name, an action's title or explanation, or a masked fact. The API serves the rest
to whoever may read it.

An event that cannot be written is logged and dropped: a webhook never stops a plan or an action.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmCertificate, HelmPlan
from app.services.helm import vocabulary as v

logger = logging.getLogger("app.services.helm.webhooks")

#: The plan counts a `helm.plan.ready` payload carries.
PLAN_COUNTS = ("actions", "payments", "needs_approval", "blocked")


def _id(value: Any) -> Optional[str]:
    return str(value) if value is not None else None


def _int(value: Any) -> Optional[int]:
    return int(value) if value is not None else None


def _figure(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


# ---------------------------------------------------------------------------
# Payloads: ids and figures only
# ---------------------------------------------------------------------------


def _instrument(row: dict[str, Any]) -> dict[str, Any]:
    return {"mandate_id": _id(row.get("mandate_id")), "mandate_version": _int(row.get("version")),
            "template": row.get("template"), "kind": row.get("kind"), "mode": row.get("mode"),
            "state": row.get("state"), "figure_value": _figure(row.get("figure_value")),
            "items": len(row.get("items") or [])}


def plan_payload(book: HelmBook, plan: HelmPlan) -> dict[str, Any]:
    summary = plan.summary or {}
    counts = summary.get("counts") or {}
    return {
        "book_id": _id(book.id), "workspace_id": _id(book.workspace_id), "plan_id": _id(plan.id),
        "version": plan.version, "status": plan.solver_status, "triggered_by": plan.triggered_by,
        "inputs_digest": plan.inputs_digest, "engine_version": plan.engine_version,
        "horizon_days": plan.horizon_days, "as_of": summary.get("as_of"), "conflict": bool(plan.conflict),
        "counts": {key: int(counts.get(key) or 0) for key in PLAN_COUNTS},
        "payments_by_currency": {str(currency): int(micros or 0)
                                 for currency, micros in (summary.get("payments_by_currency") or {}).items()},
        "instruments": [_instrument(row) for row in summary.get("instruments") or []],
    }


def action_payload(action: HelmAction, certificate: Optional[HelmCertificate] = None) -> dict[str, Any]:
    return {
        "action_id": _id(action.id), "book_id": _id(action.book_id), "plan_id": _id(action.plan_id),
        "workspace_id": _id(action.workspace_id), "work_item_id": _id(action.work_item_id),
        "mandate_id": _id(action.mandate_id), "mandate_version": action.mandate_version, "kind": action.kind,
        "state": action.state, "autonomy": action.autonomy, "amount_micros": _int(action.amount_micros),
        "currency": action.currency, "run_date": action.run_date.isoformat() if action.run_date else None,
        "executed_at": action.executed_at.isoformat() if action.executed_at else None,
        "certificate_id": _id(certificate.id if certificate is not None else action.certificate_id),
        "certificate_sha256": certificate.sha256 if certificate is not None else None,
    }


def action_event(action: HelmAction) -> str:
    """The event a `webhook` action sends: its `event` parameter, else `helm.mandate.breached`."""
    return str((action.params or {}).get("event") or v.WEBHOOK_MANDATE_BREACHED)


def breach_payload(book: HelmBook, plan: HelmPlan, instrument: dict[str, Any],
                   previous_state: Optional[str]) -> dict[str, Any]:
    return {"book_id": _id(book.id), "workspace_id": _id(book.workspace_id), "plan_id": _id(plan.id),
            "plan_version": plan.version, "previous_state": previous_state, **_instrument(instrument)}


# ---------------------------------------------------------------------------
# Who listens, and emitting
# ---------------------------------------------------------------------------


def listening(db: Session, organization_id: uuid.UUID, event: Optional[str] = None) -> list[Any]:
    """The organization's ACTIVE endpoints subscribed to `event` (to any of Helm's events when None)."""
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

    events = WebhookEndpoint.event_types.contains([event]) if event else \
        WebhookEndpoint.event_types.overlap(list(v.WEBHOOK_EVENTS))
    return list(db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.organization_id == organization_id,
                                      WebhookEndpoint.status == WebhookEndpointStatus.ACTIVE, events)
        .order_by(WebhookEndpoint.created_at)
    ).scalars())


def may_emit(db: Session, book: HelmBook) -> bool:
    """Enterprise (`capability.helm_api`), and never the sample company."""
    from app.services.helm import gate

    return book.kind != v.BOOK_SAMPLE and gate.api_held(db, book.organization_id)


def emit(db: Session, *, book: HelmBook, event: str, payload: dict[str, Any], resource_id: Optional[uuid.UUID],
         idempotency_key: str) -> Optional[uuid.UUID]:
    """Write one Helm event to the outbox; the outbox row's id, or None when nothing was written
    (not Enterprise, the sample company, nobody listening, or the write failed and was logged)."""
    if event not in v.WEBHOOK_EVENTS:
        raise ValueError(f"{event!r} is not one of Helm's webhook events")
    if not may_emit(db, book):
        return None
    try:
        if not listening(db, book.organization_id, event):
            return None
        from app.services import outbox_service

        with db.begin_nested():
            row = outbox_service.emit(db, organization_id=book.organization_id, event_type=event, payload=payload,
                                      workspace_id=book.workspace_id, resource_id=resource_id,
                                      idempotency_key=idempotency_key)
        return row.id
    except Exception:  # noqa: BLE001 - a webhook never stops a plan or an action
        logger.exception("helm.webhook_emit_failed", extra={"event": event, "book_id": str(book.id)})
        return None


def after_plan(db: Session, *, book: HelmBook, plan: HelmPlan, previous: dict[str, str]) -> list[uuid.UUID]:
    """A new plan version: `helm.plan.ready`, and `helm.mandate.breached` for each instrument that is
    breached now and was not on the plan before (the same rule as the owners' notice)."""
    if not may_emit(db, book):
        return []
    written = [emit(db, book=book, event=v.WEBHOOK_PLAN_READY, payload=plan_payload(book, plan), resource_id=plan.id,
                    idempotency_key=f"{v.WEBHOOK_PLAN_READY}:{plan.id}")]
    from app.services.helm.mandates import base

    for row in (plan.summary or {}).get("instruments") or []:
        mandate_id = str(row.get("mandate_id"))
        if row.get("state") == base.BREACHED and previous.get(mandate_id) != base.BREACHED:
            written.append(emit(db, book=book, event=v.WEBHOOK_MANDATE_BREACHED,
                                payload=breach_payload(book, plan, row, previous.get(mandate_id)),
                                resource_id=plan.id,
                                idempotency_key=f"{v.WEBHOOK_MANDATE_BREACHED}:{plan.id}:{mandate_id}"))
    return [found for found in written if found is not None]


def after_execution(db: Session, *, action: HelmAction, book: HelmBook,
                    certificate: Optional[HelmCertificate] = None) -> Optional[uuid.UUID]:
    """An action ran: `helm.action.executed`, with its EXECUTION certificate's id and hash."""
    return emit(db, book=book, event=v.WEBHOOK_ACTION_EXECUTED, payload=action_payload(action, certificate),
                resource_id=action.id, idempotency_key=f"{v.WEBHOOK_ACTION_EXECUTED}:{action.id}")


__all__ = ["action_event", "action_payload", "after_execution", "after_plan", "breach_payload", "emit", "listening",
           "may_emit", "plan_payload"]
