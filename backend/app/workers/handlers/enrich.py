"""ARCH-10 Step 7 / ARCH-11.5 Step 3 — the `document.enrich` job handler."""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import SpendLimitExceededError
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services.document_models import DocumentPage
from app.services.pipeline_state import PipelineStage, transition_by_id

logger = logging.getLogger("app.workers.handlers.enrich")

JOB_TYPE = "document.enrich"


class Outcome:
    COMPLETED = "COMPLETED"
    PERMANENT_FAILURE = "PERMANENT_FAILURE"
    QUOTA_BLOCKED = "QUOTA_BLOCKED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True)
class _Target:
    work_item_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    original_filename: str
    created_by_user_id: Optional[uuid.UUID]
    stage: str
    job_id: Optional[uuid.UUID] = None
    uploaded_file_id: Optional[uuid.UUID] = None


def _resolve(db: Session, payload: dict[str, Any]) -> Optional[_Target]:
    raw_id = payload.get("work_item_id")
    if not raw_id:
        raise ValueError("document.enrich payload is missing work_item_id")

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
        organization_id=organization_id,
        workspace_id=work_item.workspace_id,
        original_filename=work_item.original_filename,
        created_by_user_id=work_item.created_by_user_id,
        stage=work_item.pipeline_stage,
        job_id=uuid.UUID(str(raw_job_id)) if raw_job_id else None,
        uploaded_file_id=work_item.uploaded_file_id,
    )


def _pages_from_extraction(work_item: WorkItem) -> list[DocumentPage]:
    from app.services.document_models import pages_from_work_item

    return pages_from_work_item(
        work_item.extraction_metadata, work_item.extracted_text
    )


def _enrich(db: Session, target: _Target) -> dict[str, Any]:
    from app import crud
    from app.services.chunk_writer import replace_document_chunks
    from app.services.chunking_service import (
        DEFAULT_CHUNK_OVERLAP_PCT,
        DEFAULT_CHUNK_SIZE_TOKENS,
        chunking_summary,
        split_pages,
    )
    from app.services.embedding_metering import embed_texts_with_metering
    from app.services.embedding_service import embedding_service
    from app.services.llm_service import llm_service
    from app.services.vocabulary_service import workspace_vocabulary_service

    work_item = db.execute(
        select(WorkItem).where(WorkItem.id == target.work_item_id)
    ).scalar_one()

    ai_settings = crud.get_ai_settings(db, workspace_id=target.workspace_id)
    if ai_settings is None:
        raise ValueError(f"No AI settings for workspace {target.workspace_id}")
    document_settings = crud.get_document_settings(
        db, workspace_id=target.workspace_id
    )
    if document_settings is None:
        raise ValueError(f"No document settings for workspace {target.workspace_id}")

    pages = _pages_from_extraction(work_item)
    full_text = "\n\n".join(page.text for page in pages)
    stats: dict[str, Any] = {"pages": len(pages), "characters": len(full_text)}

    if not full_text:
        logger.info(
            "enrich.no_text", extra={"work_item_id": str(target.work_item_id)}
        )
        return {**stats, "chunks": 0, "skipped": "no extracted text"}

    # Invalidate workspace vocabulary cache so next query re-derives fresh terms
    workspace_vocabulary_service.invalidate(target.workspace_id)

    # --- chunking + embedding ------------------------------------------
    size_tokens = getattr(
        document_settings, "chunk_size_tokens", DEFAULT_CHUNK_SIZE_TOKENS
    )
    overlap_pct = getattr(
        document_settings, "chunk_overlap_pct", DEFAULT_CHUNK_OVERLAP_PCT
    )

    model = embedding_service._get_model()
    candidates = split_pages(
        pages,
        tokenizer=model.tokenizer,
        chunk_size_tokens=size_tokens,
        chunk_overlap_pct=overlap_pct,
    )
    stats["chunks"] = len(candidates)
    stats.update(chunking_summary(candidates))

    if candidates:
        embeddings, embedding_plan = embed_texts_with_metering(
            db,
            organization_id=target.organization_id,
            workspace_id=target.workspace_id,
            work_item_id=work_item.id,
            texts=[c.content for c in candidates],
            job_id=target.job_id,
        )
        stats["embedding"] = embedding_plan.as_details()

        replace_document_chunks(
            db,
            workspace_id=target.workspace_id,
            organization_id=target.organization_id,
            work_item_id=work_item.id,
            uploaded_file_id=target.uploaded_file_id,
            candidates=candidates,
            embeddings=embeddings,
            embedding_model=embedding_plan.model_name,
        )
    else:
        logger.warning(
            "enrich.no_chunks", extra={"work_item_id": str(target.work_item_id)}
        )

    # --- classification + entities --------------------------------------
    if document_settings.automatic_classification:
        classification = llm_service.classify_document(
            full_text, ai_settings=ai_settings
        )
    else:
        classification = {"document_classification": "Other"}
    document_class = classification.get("document_classification", "Other")

    if document_settings.automatic_entity_extraction:
        entities = llm_service.extract_entities(
            full_text, document_class, ai_settings=ai_settings
        )
    else:
        entities = {}
    entities["classification_details"] = classification

    # --- summarisation ---------------------------------------------------
    summary = (
        llm_service.generate_summary(full_text, ai_settings=ai_settings)
        if document_settings.automatic_summarization
        else None
    )

    work_item.summary = summary
    work_item.extracted_entities = entities
    db.flush([work_item])

    stats["classification"] = document_class
    stats["summarised"] = summary is not None
    return stats


def _run_side_effects(target: _Target) -> None:
    from app.db.session import SessionLocal
    from app.models.notification import (
        NotificationChannel,
        NotificationPriority,
        NotificationType,
    )
    from app.services.automation_service import automation_service
    from app.services.notification_service import notification_service

    async def _go() -> None:
        with SessionLocal() as db:
            try:
                stats = await automation_service.execute_rules_for_work_item(
                    db, work_item_id=target.work_item_id, event="WORK_ITEM_COMPLETED"
                )
                logger.info("enrich.automation_complete", extra={"stats": str(stats)})
            except Exception:  # noqa: BLE001
                logger.exception("enrich.automation_failed")

            try:
                work_item = db.execute(
                    select(WorkItem).where(WorkItem.id == target.work_item_id)
                ).scalar_one_or_none()
                recipient = work_item.created_by if work_item else None
                if recipient is None:
                    return
                await notification_service.send_notification(
                    db=db,
                    workspace_id=target.workspace_id,
                    user=recipient,
                    title="Document processed successfully",
                    message=f"{target.original_filename} has finished processing.",
                    notification_type=NotificationType.DOCUMENT,
                    priority=NotificationPriority.SUCCESS,
                    delivery_channel=NotificationChannel.IN_APP,
                    work_item=work_item,
                )
            except Exception:  # noqa: BLE001
                logger.exception("enrich.notification_failed")
            db.commit()

    try:
        asyncio.run(_go())
    except Exception:  # noqa: BLE001
        logger.exception("enrich.side_effects_failed")


def handle_document_enrich(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal

    with SessionLocal() as db:
        target = _resolve(db, payload)
        if target is None:
            db.commit()
            return {"outcome": Outcome.SKIPPED, "reason": "work item no longer exists"}
        if target.stage == PipelineStage.COMPLETED.value:
            db.commit()
            return {"outcome": Outcome.SKIPPED, "reason": "already COMPLETED"}

        transition_by_id(
            db,
            work_item_id=target.work_item_id,
            to_stage=PipelineStage.ENRICHING,
            organization_id=target.organization_id,
        )
        db.commit()

    try:
        with SessionLocal() as db:
            stats = _enrich(db, target)
            transition_by_id(
                db,
                work_item_id=target.work_item_id,
                to_stage=PipelineStage.COMPLETED,
                organization_id=target.organization_id,
                event_payload={"enrichment": stats},
            )
            db.commit()

    except SpendLimitExceededError as exc:
        logger.warning(
            "enrich.quota_blocked",
            extra={"work_item_id": str(target.work_item_id), "limit": exc.limit_key},
        )
        with SessionLocal() as db:
            transition_by_id(
                db,
                work_item_id=target.work_item_id,
                to_stage=PipelineStage.QUOTA_BLOCKED,
                organization_id=target.organization_id,
                failure_reason=str(exc),
            )
            db.commit()
        return {
            "outcome": Outcome.QUOTA_BLOCKED,
            "limit_key": exc.limit_key,
            "resets_at": exc.resets_at.isoformat() if exc.resets_at else None,
        }

    except ValueError as exc:
        logger.warning(
            "enrich.permanent_failure",
            extra={"work_item_id": str(target.work_item_id), "error": str(exc)},
        )
        with SessionLocal() as db:
            transition_by_id(
                db,
                work_item_id=target.work_item_id,
                to_stage=PipelineStage.FAILED,
                organization_id=target.organization_id,
                failure_reason=str(exc),
            )
            db.commit()
        return {"outcome": Outcome.PERMANENT_FAILURE, "error": str(exc)}

    except Exception as exc:
        logger.warning(
            "enrich.transient_failure",
            extra={
                "work_item_id": str(target.work_item_id),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise

    _run_side_effects(target)
    return {"outcome": Outcome.COMPLETED, **stats}


__all__ = ["Outcome", "handle_document_enrich"]