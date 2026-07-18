"""
Document processing orchestration service for FlowPilot AI.

Coordinates the sequential AI pipeline (text extraction, chunking,
embeddings, vector storage, document classification, entity extraction,
summarization, and business automation execution).
"""

from __future__ import annotations

import logging
from pathlib import Path
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app import utils
from app.db.session import SessionLocal
from app.models.job import ProcessingJob
from app.models.work_item import WorkItem
from app.schemas.job import JobStatus, JobUpdate
from app.schemas.work_item import WorkItemStatus, WorkItemUpdate

from app.services.automation_service import automation_service
from app.services.chunking_service import split_text
from app.services.embedding_service import embedding_service
from app.services.extraction_service import extract_text_from_document
from app.services.llm_service import llm_service

from app.services.notification_service import notification_service

from app.services.bm25_service import bm25_service

from app.services.document_vocabulary_service import (
    DocumentVocabularyService,
)

from app.services.query_service import (
    query_service,
)

from app.models.notification import (
    NotificationChannel,
    NotificationPriority,
    NotificationType,
)

logger = logging.getLogger("app.services.document_processor")

document_vocabulary_service = (
    DocumentVocabularyService()
)


async def process_document_pipeline(
    work_item_id: uuid.UUID,
    job_id: uuid.UUID,
) -> None:
    """
    Execute the complete AI processing pipeline for a WorkItem.
    """
    logger.info(
        "Starting document pipeline. WorkItem=%s Job=%s",
        work_item_id,
        job_id,
    )

    db: Session = SessionLocal()

    try:
        work_item = db.execute(
            select(WorkItem).where(WorkItem.id == work_item_id)
        ).scalar_one_or_none()

        job = db.execute(
            select(ProcessingJob).where(ProcessingJob.id == job_id)
        ).scalar_one_or_none()

        if work_item is None or job is None:
            logger.error("Pipeline aborted. WorkItem or Job not found.")
            return

        crud.update_work_item_state(
            db,
            db_obj=work_item,
            obj_in=WorkItemUpdate(status=WorkItemStatus.PROCESSING),
        )

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(
                status=JobStatus.RUNNING,
                progress=10,
            ),
        )

        safe_path = Path(utils.get_safe_path(work_item.stored_filename))

        if not safe_path.exists():
            raise FileNotFoundError(f"Missing document: {safe_path}")

        # -----------------------------
        # Stage 1
        # -----------------------------
        pages = extract_text_from_document(
            safe_path,
            work_item.file_type,
        )

        full_text = "\n\n".join(
            page.text
            for page in pages
        )

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(progress=30),
        )

        #
        # Build document vocabulary used by
        # document-aware query expansion.
        #
        document_vocabulary_service.update_document(
            work_item_id=work_item.id,
            original_filename=work_item.original_filename,
            title=work_item.original_filename,
            full_text=full_text,
        )

        query_service.document_strategy.update_document_vocabulary(
            document_vocabulary_service.get_expansion_map(),
        )

        logger.info(
            "Document vocabulary indexed for WorkItem %s.",
            work_item.id,
        )

        # -----------------------------
        # Stage 2
        # -----------------------------
        chunks = split_text(pages)

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(progress=50),
        )

        # -----------------------------
        # Stage 3
        # -----------------------------
        if chunks:
            embeddings = embedding_service.generate_embeddings(
                [
                    chunk.text
                    for chunk in chunks
                ]
            )
            embedding_service.store_chunks(
                work_item_id=work_item.id,
                original_filename=work_item.original_filename,
                chunks=chunks,
                embeddings=embeddings,
            )
            
            bm25_service.rebuild_index()

            logger.info(
                "BM25 index rebuilt after document ingestion."
            )

        else:
            logger.warning(
                "No chunks generated for WorkItem %s.",
                work_item.id,
            )

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(progress=70),
        )

        # -----------------------------
        # Stage 4
        # -----------------------------
        classification = llm_service.classify_document(full_text)
        document_class = classification.get("document_classification", "Other")

        entities = llm_service.extract_entities(
            full_text,
            document_class,
        )
        entities["classification_details"] = classification

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(progress=90),
        )

        # -----------------------------
        # Stage 5
        # -----------------------------
        summary = llm_service.generate_summary(full_text)

        crud.update_work_item_state(
            db,
            db_obj=work_item,
            obj_in=WorkItemUpdate(
                status=WorkItemStatus.COMPLETED,
                summary=summary,
                extracted_entities=entities,
            ),
        )

        crud.update_job(
            db,
            db_obj=job,
            obj_in=JobUpdate(
                status=JobStatus.COMPLETED,
                progress=100,
            ),
        )
        logger.info("Pipeline completed successfully.")

        # -----------------------------
        # Automation Engine
        # -----------------------------
        try:
            automation_stats = (
                await automation_service.execute_rules_for_work_item(
                    db,
                    work_item_id=work_item.id,
                    event="WORK_ITEM_COMPLETED",
                )
            )

            logger.info(
                "Automation completed. %s",
                automation_stats,
            )
        except Exception:
            logger.exception(
                "Automation engine failed after successful pipeline."
            )

        # -----------------------------
        # In-App Notification
        # -----------------------------
        try:
            await notification_service.send_notification(
                db=db,
                user=work_item.user,
                title="Document processed successfully",
                message=(
                    f"{work_item.original_filename} "
                    "has finished processing."
                ),
                notification_type=NotificationType.DOCUMENT,
                priority=NotificationPriority.SUCCESS,
                delivery_channel=NotificationChannel.IN_APP,
                work_item=work_item,
            )

            logger.info(
                "Completion notification created for WorkItem %s.",
                work_item.id,
            )
        except Exception:
            logger.exception("Failed to create completion notification.")

    except Exception as exc:
        logger.exception("Pipeline failed.")

        try:
            db.rollback()

            work_item = db.execute(
                select(WorkItem).where(WorkItem.id == work_item_id)
            ).scalar_one_or_none()

            job = db.execute(
                select(ProcessingJob).where(ProcessingJob.id == job_id)
            ).scalar_one_or_none()

            if work_item and job:
                crud.update_work_item_state(
                    db,
                    db_obj=work_item,
                    obj_in=WorkItemUpdate(status=WorkItemStatus.FAILED),
                )

                crud.update_job(
                    db,
                    db_obj=job,
                    obj_in=JobUpdate(
                        status=JobStatus.FAILED,
                        error_message=str(exc)[:5000],
                    ),
                )
        except Exception:
            logger.exception("Unable to persist failure state.")

    finally:
        db.close()
        logger.info("Background session closed.")

    
