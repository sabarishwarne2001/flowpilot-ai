"""ARCH-11 Step 4 — the `knowledge.reindex` job handler."""

from __future__ import annotations

import calendar
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import SpendLimitExceededError
from app.models.work_item import WorkItem
from app.models.workspace import Workspace

logger = logging.getLogger("app.workers.handlers.knowledge_reindex")

JOB_TYPE = "knowledge.reindex"
USAGE_EVENT_TYPE = "embedding.backfill_token"
IDEMPOTENCY_PREFIX = "reindex"


class Outcome:
    COMPLETED = "COMPLETED"
    SKIPPED = "SKIPPED"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    BUDGET_BLOCKED = "BUDGET_BLOCKED"


class PlatformBudgetExceeded(RuntimeError):
    """The platform-wide backfill ceiling was reached."""


@dataclass(frozen=True)
class _Target:
    work_item_id: uuid.UUID
    workspace_id: uuid.UUID
    organization_id: uuid.UUID
    uploaded_file_id: Optional[uuid.UUID]
    job_id: Optional[uuid.UUID]


def _resolve(db: Session, payload: dict[str, Any]) -> Optional[_Target]:
    raw_id = payload.get("work_item_id")
    if not raw_id:
        raise ValueError("knowledge.reindex payload is missing work_item_id")

    work_item_id = uuid.UUID(str(raw_id))
    row = db.execute(
        select(WorkItem, Workspace.organization_id)
        .join(Workspace, Workspace.id == WorkItem.workspace_id)
        .where(WorkItem.id == work_item_id)
    ).first()
    if row is None:
        return None

    work_item, organization_id = row
    raw_job_id = payload.get("job_id")
    return _Target(
        work_item_id=work_item.id,
        workspace_id=work_item.workspace_id,
        organization_id=organization_id,
        uploaded_file_id=work_item.uploaded_file_id,
        job_id=uuid.UUID(str(raw_job_id)) if raw_job_id else None,
    )


def _month_start(now: Optional[datetime] = None) -> datetime:
    moment = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    return moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _assert_platform_budget(db: Session, *, requested_tokens: int) -> None:
    """Platform-wide ceiling on backfill tokens for the calendar month."""
    ceiling = getattr(settings, "SPEND_PLATFORM_MONTHLY_BACKFILL_TOKENS", None)
    if not ceiling:
        return

    from sqlalchemy import func
    from app.models.usage_event import UsageEvent

    consumed = int(
        db.execute(
            select(func.coalesce(func.sum(UsageEvent.quantity), 0)).where(
                UsageEvent.event_type == USAGE_EVENT_TYPE,
                UsageEvent.occurred_at >= _month_start(),
            )
        ).scalar_one()
    )
    if consumed + requested_tokens > int(ceiling):
        raise PlatformBudgetExceeded(
            f"platform backfill budget exhausted: {consumed} consumed this "
            f"month, {requested_tokens} requested, ceiling {ceiling}."
        )


def _reindex(db: Session, target: _Target) -> dict[str, Any]:
    from app import crud
    from app.services.chunk_writer import replace_document_chunks
    from app.services.chunking_service import (
        DEFAULT_CHUNK_OVERLAP_PCT,
        DEFAULT_CHUNK_SIZE_TOKENS,
        chunking_summary,
        split_document,
    )
    from app.services.embedding_metering import embed_texts_with_metering
    from app.services.embedding_service import embedding_service

    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == target.work_item_id)
    ).scalar_one()

    document_settings = crud.get_document_settings(
        db, workspace_id=target.workspace_id
    )
    size_tokens = getattr(
        document_settings, "chunk_size_tokens", DEFAULT_CHUNK_SIZE_TOKENS
    )
    overlap_pct = getattr(
        document_settings, "chunk_overlap_pct", DEFAULT_CHUNK_OVERLAP_PCT
    )

    model = embedding_service._get_model()  # noqa: SLF001
    candidates = split_document(
        extraction_metadata=work_item.extraction_metadata,
        extracted_text=work_item.extracted_text,
        tokenizer=model.tokenizer,
        chunk_size_tokens=size_tokens,
        chunk_overlap_pct=overlap_pct,
    )

    summary = chunking_summary(candidates)
    if not candidates:
        removed = replace_document_chunks(
            db,
            workspace_id=target.workspace_id,
            organization_id=target.organization_id,
            work_item_id=target.work_item_id,
            uploaded_file_id=target.uploaded_file_id,
            candidates=[],
            embeddings=[],
        )
        return {**summary, **removed, "note": "no extractable text"}

    _assert_platform_budget(
        db, requested_tokens=sum(c.token_count for c in candidates)
    )

    embeddings, plan = embed_texts_with_metering(
        db,
        organization_id=target.organization_id,
        workspace_id=target.workspace_id,
        work_item_id=target.work_item_id,
        texts=[candidate.content for candidate in candidates],
        job_id=target.job_id,
        event_type=USAGE_EVENT_TYPE,
        idempotency_prefix=IDEMPOTENCY_PREFIX,
    )

    written = replace_document_chunks(
        db,
        workspace_id=target.workspace_id,
        organization_id=target.organization_id,
        work_item_id=target.work_item_id,
        uploaded_file_id=target.uploaded_file_id,
        candidates=candidates,
        embeddings=embeddings,
        embedding_model=plan.model_name,
    )
    return {**summary, **written, "embedding": plan.as_details()}


def handle_knowledge_reindex(payload: dict[str, Any]) -> dict[str, Any]:
    """One work item, one transaction. Chunks and usage commit together."""
    from app.core.principal import system_principal
    from app.db.session import SessionLocal

    raw_job_id = payload.get("job_id")
    job_id = uuid.UUID(str(raw_job_id)) if raw_job_id else None

    with system_principal(job_name=JOB_TYPE, job_id=job_id):
        with SessionLocal() as db:
            target = _resolve(db, payload)
            if target is None:
                db.commit()
                return {
                    "outcome": Outcome.SKIPPED,
                    "reason": "work item no longer exists",
                }
            try:
                stats = _reindex(db, target)
                db.commit()
            except PlatformBudgetExceeded as exc:
                db.rollback()
                logger.warning(
                    "reindex.budget_blocked",
                    extra={"work_item_id": str(target.work_item_id)},
                )
                return {"outcome": Outcome.BUDGET_BLOCKED, "error": str(exc)}
            except SpendLimitExceededError as exc:
                db.rollback()
                logger.error(
                    "reindex.unexpected_quota_block",
                    extra={
                        "work_item_id": str(target.work_item_id),
                        "limit_key": exc.limit_key,
                    },
                )
                return {"outcome": Outcome.BUDGET_BLOCKED, "error": str(exc)}
            except ValueError as exc:
                db.rollback()
                logger.warning(
                    "reindex.permanent_failure",
                    extra={
                        "work_item_id": str(target.work_item_id),
                        "error": str(exc),
                    },
                )
                return {"outcome": Outcome.PERMANENT_FAILURE, "error": str(exc)}

    logger.info(
        "reindex.complete",
        extra={"work_item_id": str(target.work_item_id), **stats},
    )
    return {"outcome": Outcome.COMPLETED, **stats}


__all__ = [
    "JOB_TYPE",
    "Outcome",
    "PlatformBudgetExceeded",
    "USAGE_EVENT_TYPE",
    "handle_knowledge_reindex",
]
