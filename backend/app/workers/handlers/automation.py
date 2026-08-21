"""ARCH-13 Step 13.5 — the `automation.execute` job handler.

LIGHT profile: SQL plus action dispatch. The LLM actions run through the
existing metering path and do not need a heavy image.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.automation import AutomationRule
from app.models.automation_execution import AutomationExecutionStatus
from app.models.outbox_event import OutboxEvent
from app.models.work_item import WorkItem
from app.models.workspace import Workspace

logger = logging.getLogger("app.workers.handlers.automation")

JOB_TYPE = "automation.execute"


class Outcome:
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    SUPPRESSED = "SUPPRESSED"
    FAILED = "FAILED"
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"
    TIMED_OUT = "TIMED_OUT"


EVENT_TO_RULE_TRIGGER: dict[str, str] = {
    "work_item.enriched": "WORK_ITEM_COMPLETED",
    "work_item.field_changed": "WORK_ITEM_UPDATED",
    "work_item.verification_completed": "WORK_ITEM_COMPLETED",
}


def _model_caller(db: Session, execution: Any, rule: Any, ai_settings: Any):
    from app.services.automation import budget as budget_service
    from app.services.llm_service import llm_service

    def _call(node_key: str, prompt: str) -> tuple[str, Any]:
        budgeted = budget_service.reserve_for_automation(
            db,
            execution=execution,
            node_key=node_key,
            prompt=prompt,
            ai_settings=ai_settings,
            rule=rule,
        )
        response, token_usage = llm_service.execute_prompt(
            prompt=prompt,
            temperature=0.0,
            ai_settings=ai_settings,
        )
        budget_service.settle_and_debit(
            db,
            execution=execution,
            budgeted=budgeted,
            token_usage=token_usage,
        )
        db.commit()
        return response, token_usage

    return _call


def _resolve_rules(
    db: Session, *, workspace_id: uuid.UUID, event_type: str
) -> list[AutomationRule]:
    from app import crud

    trigger = EVENT_TO_RULE_TRIGGER.get(event_type)
    if trigger is None:
        return []
    rules = crud.list_active_rules_for_event(
        db, workspace_id=workspace_id, event=trigger
    )
    return sorted(
        rules,
        key=lambda r: (
            r.priority,
            r.created_at.timestamp() if getattr(r, "created_at", None) else 0,
        ),
    )


def handle_automation_execute(payload: dict[str, Any]) -> dict[str, Any]:
    from app.core.config import settings
    from app.db.session import SessionLocal
    from app.services.automation import executor

    raw_event_id = payload.get("outbox_event_id")
    if not raw_event_id:
        raise ValueError("automation.execute payload is missing outbox_event_id")

    if not settings.AUTOMATION_ENGINE_ENABLED:
        return {"outcome": Outcome.SKIPPED, "reason": "engine disabled"}

    event_id = uuid.UUID(str(raw_event_id))
    results: list[dict[str, Any]] = []

    with SessionLocal() as db:
        event = db.execute(
            select(OutboxEvent).where(OutboxEvent.id == event_id)
        ).scalar_one_or_none()
        if event is None:
            return {"outcome": Outcome.SKIPPED, "reason": "event no longer exists"}
        if not event.is_internal:
            logger.error(
                "automation.public_event_reached_engine",
                extra={"outbox_event_id": str(event_id), "event_type": event.event_type},
            )
            return {"outcome": Outcome.SKIPPED, "reason": "event is not INTERNAL"}

        workspace_id = event.workspace_id
        if workspace_id is None:
            return {"outcome": Outcome.SKIPPED, "reason": "event has no workspace"}

        organization_id = db.execute(
            select(Workspace.organization_id).where(Workspace.id == workspace_id)
        ).scalar_one_or_none()
        if organization_id is None:
            return {"outcome": Outcome.SKIPPED, "reason": "workspace no longer exists"}

        work_item_id = event.resource_id or (
            uuid.UUID(str(event.payload["work_item_id"]))
            if isinstance(event.payload, dict) and event.payload.get("work_item_id")
            else None
        )
        work_item = (
            db.execute(select(WorkItem).where(WorkItem.id == work_item_id)).scalar_one_or_none()
            if work_item_id
            else None
        )

        if work_item is not None and _verification_blocks(db, work_item_id=work_item.id):
            logger.info(
                "automation.blocked_pending_verification",
                extra={"work_item_id": str(work_item.id)},
            )
            return {
                "outcome": Outcome.SKIPPED,
                "reason": "verification pending human review",
            }

        rules = _resolve_rules(
            db, workspace_id=workspace_id, event_type=event.event_type
        )
        if not rules:
            return {"outcome": Outcome.SKIPPED, "reason": "no matching rules"}

        from app import crud

        ai_settings = crud.get_ai_settings(db, workspace_id=workspace_id)

        for rule in rules:
            try:
                execution = executor.create_execution(
                    db,
                    rule=rule,
                    organization_id=organization_id,
                    workspace_id=workspace_id,
                    correlation_id=event.chain_root_id,
                    depth=int(event.depth or 0),
                    work_item_id=work_item.id if work_item else None,
                    outbox_event_id=event.id,
                    causation_id=event.causation_id,
                )
                db.commit()
            except IntegrityError:
                db.rollback()
                logger.info(
                    "automation.replay_suppressed",
                    extra={
                        "rule_id": str(rule.id),
                        "outbox_event_id": str(event.id),
                    },
                )
                results.append({"rule_id": str(rule.id), "status": "REPLAY"})
                continue

            if execution.is_suppressed:
                results.append(
                    {"rule_id": str(rule.id), "status": execution.status.value}
                )
                continue

            result = executor.run_execution(
                db,
                execution=execution,
                rule=rule,
                work_item=work_item,
                trigger_event=event,
                call_model=(
                    _model_caller(db, execution, rule, ai_settings)
                    if ai_settings is not None
                    else None
                ),
            )
            results.append({"rule_id": str(rule.id), "status": result.status.value})

            if result.status is AutomationExecutionStatus.BUDGET_EXHAUSTED:
                _emit_budget_exhausted(db, execution=execution, event=event)

    statuses = {entry["status"] for entry in results}
    if AutomationExecutionStatus.BUDGET_EXHAUSTED.value in statuses:
        outcome = Outcome.BUDGET_EXHAUSTED
    elif AutomationExecutionStatus.TIMED_OUT.value in statuses:
        outcome = Outcome.TIMED_OUT
    elif AutomationExecutionStatus.FAILED.value in statuses:
        outcome = Outcome.FAILED
    elif statuses and statuses <= {"SUPPRESSED_CYCLE", "SUPPRESSED_DEPTH", "REPLAY"}:
        outcome = Outcome.SUPPRESSED
    else:
        outcome = Outcome.COMPLETED

    return {"outcome": outcome, "executions": results}


def _verification_blocks(db: Session, *, work_item_id: uuid.UUID) -> bool:
    try:
        from app.models.verification import (
            DocumentVerification,
            VerificationStatus,
        )
    except ImportError:
        return False

    return (
        db.execute(
            select(DocumentVerification.id)
            .where(
                DocumentVerification.work_item_id == work_item_id,
                DocumentVerification.status.in_(
                    [VerificationStatus.PENDING, VerificationStatus.DISAGREED]
                ),
            )
            .limit(1)
        ).first()
        is not None
    )


def _emit_budget_exhausted(db: Session, *, execution: Any, event: OutboxEvent) -> None:
    from app.services import outbox_service

    outbox_service.emit_internal(
        db,
        organization_id=execution.organization_id,
        workspace_id=execution.workspace_id,
        event_type="automation.budget_exhausted",
        resource_id=execution.rule_id,
        payload={
            "rule_id": str(execution.rule_id),
            "execution_id": str(execution.id),
            "budget_cost_micros": int(execution.budget_cost_micros),
            "spent_cost_micros": int(execution.spent_cost_micros),
        },
        caused_by=event,
    )
    db.commit()


__all__ = ["EVENT_TO_RULE_TRIGGER", "JOB_TYPE", "Outcome", "handle_automation_execute"]