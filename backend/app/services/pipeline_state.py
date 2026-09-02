"""ARCH-10 Step 7 — the document pipeline state machine."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum as PyEnum
from typing import Any, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.work_item import WorkItem
from app.services import outbox_service

logger = logging.getLogger("app.services.pipeline_state")

PIPELINE_STAGE_ENUM_NAME = "work_item_pipeline_stage"


class PipelineStage(str, PyEnum):
    QUEUED = "QUEUED"
    EXTRACTING = "EXTRACTING"
    EXTRACTED = "EXTRACTED"
    ENRICHING = "ENRICHING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"


PUBLIC_STATUS_BY_STAGE: Mapping[PipelineStage, str] = {
    PipelineStage.QUEUED: "QUEUED",
    PipelineStage.EXTRACTING: "PROCESSING",
    PipelineStage.EXTRACTED: "PROCESSING",
    PipelineStage.ENRICHING: "PROCESSING",
    PipelineStage.COMPLETED: "COMPLETED",
    PipelineStage.FAILED: "FAILED",
    PipelineStage.QUOTA_BLOCKED: "FAILED",
}

STAGE_TRANSITIONS: Mapping[PipelineStage, frozenset[PipelineStage]] = {
    PipelineStage.QUEUED: frozenset(
        {
            PipelineStage.EXTRACTING,
            PipelineStage.FAILED,
            PipelineStage.QUOTA_BLOCKED,
        }
    ),
    PipelineStage.EXTRACTING: frozenset(
        {
            PipelineStage.EXTRACTED,
            PipelineStage.FAILED,
            PipelineStage.QUOTA_BLOCKED,
        }
    ),
    PipelineStage.EXTRACTED: frozenset(
        {
            PipelineStage.ENRICHING,
            PipelineStage.COMPLETED,
            PipelineStage.FAILED,
        }
    ),
    PipelineStage.ENRICHING: frozenset(
        {PipelineStage.COMPLETED, PipelineStage.FAILED}
    ),
    PipelineStage.COMPLETED: frozenset({PipelineStage.QUEUED}),
    PipelineStage.FAILED: frozenset({PipelineStage.QUEUED}),
    PipelineStage.QUOTA_BLOCKED: frozenset({PipelineStage.QUEUED}),
}

TERMINAL_STAGES: frozenset[PipelineStage] = frozenset(
    {PipelineStage.COMPLETED, PipelineStage.FAILED, PipelineStage.QUOTA_BLOCKED}
)

EVENT_BY_STAGE: Mapping[PipelineStage, Optional[str]] = {
    PipelineStage.QUEUED: "document.queued",
    PipelineStage.EXTRACTING: "document.processing",
    PipelineStage.EXTRACTED: None,
    PipelineStage.ENRICHING: None,
    PipelineStage.COMPLETED: "document.completed",
    PipelineStage.FAILED: "document.failed",
    PipelineStage.QUOTA_BLOCKED: "document.failed",
}


class PipelineStateError(Exception):
    """Base class for state machine faults."""


class IllegalTransitionError(PipelineStateError):
    """The requested move is not in the transition table."""

    def __init__(self, *, work_item_id: uuid.UUID, current: str, requested: str) -> None:
        super().__init__(
            f"work_item {work_item_id}: {current} -> {requested} is not a legal "
            f"pipeline transition. Legal from {current}: "
            f"{sorted(s.value for s in STAGE_TRANSITIONS.get(PipelineStage(current), frozenset()))}."
        )
        self.work_item_id = work_item_id
        self.current = current
        self.requested = requested


@dataclass(frozen=True)
class TransitionResult:
    work_item_id: uuid.UUID
    previous: PipelineStage
    current: PipelineStage
    public_status: str
    changed: bool
    event_type: Optional[str] = None


def can_transition(current: PipelineStage, target: PipelineStage) -> bool:
    if current is target:
        return True
    return target in STAGE_TRANSITIONS.get(current, frozenset())


def transition(
    db: Session,
    *,
    work_item: WorkItem,
    to_stage: PipelineStage,
    organization_id: uuid.UUID,
    failure_reason: Optional[str] = None,
    event_payload: Optional[dict[str, Any]] = None,
    emit_event: bool = True,
) -> TransitionResult:
    current = PipelineStage(work_item.pipeline_stage or PipelineStage.QUEUED.value)

    if not can_transition(current, to_stage):
        raise IllegalTransitionError(
            work_item_id=work_item.id,
            current=current.value,
            requested=to_stage.value,
        )

    if current is to_stage:
        logger.debug(
            "pipeline.transition_noop",
            extra={"work_item_id": str(work_item.id), "stage": current.value},
        )
        return TransitionResult(
            work_item_id=work_item.id,
            previous=current,
            current=current,
            public_status=PUBLIC_STATUS_BY_STAGE[current],
            changed=False,
        )

    public_status = PUBLIC_STATUS_BY_STAGE[to_stage]
    work_item.pipeline_stage = to_stage.value
    work_item.status = public_status
    work_item.stage_updated_at = datetime.now(timezone.utc)
    if to_stage in {PipelineStage.FAILED, PipelineStage.QUOTA_BLOCKED}:
        work_item.failure_stage = current.value
        work_item.failure_reason = (failure_reason or "")[:1000] or None
    elif to_stage is PipelineStage.QUEUED:
        work_item.failure_stage = None
        work_item.failure_reason = None

    db.flush([work_item])

    event_type = EVENT_BY_STAGE.get(to_stage)
    if emit_event and event_type:
        payload: dict[str, Any] = {
            "work_item_id": str(work_item.id),
            "workspace_id": str(work_item.workspace_id),
            "original_filename": work_item.original_filename,
            "stage": to_stage.value,
            "previous_stage": current.value,
            "status": public_status,
        }
        if work_item.page_count is not None:
            payload["page_count"] = work_item.page_count
        if to_stage in {PipelineStage.FAILED, PipelineStage.QUOTA_BLOCKED}:
            payload["failure_stage"] = current.value
            payload["failure_reason"] = work_item.failure_reason
            payload["quota_blocked"] = to_stage is PipelineStage.QUOTA_BLOCKED
        if event_payload:
            payload.update(event_payload)

        outbox_service.emit(
            db,
            organization_id=organization_id,
            workspace_id=work_item.workspace_id,
            event_type=event_type,
            resource_id=work_item.id,
            payload=payload,
            idempotency_key=f"{event_type}:{work_item.id}:{to_stage.value}",
        )

    logger.info(
        "pipeline.transition",
        extra={
            "work_item_id": str(work_item.id),
            "from": current.value,
            "to": to_stage.value,
            "status": public_status,
            "event": event_type,
        },
    )
    return TransitionResult(
        work_item_id=work_item.id,
        previous=current,
        current=to_stage,
        public_status=public_status,
        changed=True,
        event_type=event_type,
    )


def transition_by_id(
    db: Session,
    *,
    work_item_id: uuid.UUID,
    to_stage: PipelineStage,
    organization_id: uuid.UUID,
    failure_reason: Optional[str] = None,
    event_payload: Optional[dict[str, Any]] = None,
    lock: bool = True,
) -> Optional[TransitionResult]:
    stmt = select(WorkItem).where(WorkItem.id == work_item_id)
    if lock:
        stmt = stmt.with_for_update()
    work_item = db.execute(stmt).scalar_one_or_none()
    if work_item is None:
        logger.info(
            "pipeline.work_item_gone", extra={"work_item_id": str(work_item_id)}
        )
        return None
    return transition(
        db,
        work_item=work_item,
        to_stage=to_stage,
        organization_id=organization_id,
        failure_reason=failure_reason,
        event_payload=event_payload,
    )


def sql_transition_literal() -> str:
    pairs = sorted(
        f"('{source.value}','{target.value}')"
        for source, targets in STAGE_TRANSITIONS.items()
        for target in targets
    )
    return ", ".join(pairs)
