"""
Knowledge Base administration service.
"""

from __future__ import annotations

import logging
import uuid
from sqlalchemy.orm import Session

from app import crud
from app.core.storage import get_storage_driver
from app.services.bm25_service import bm25_service
from app.services.document_vocabulary_service import document_vocabulary_service
from app.services.embedding_service import embedding_service
from app.services.query_service import query_service

logger = logging.getLogger("app.services.knowledge_base")


class KnowledgeBaseService:
    def clear_knowledge_base(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
    ) -> dict[str, int]:
        logger.info(
            "Starting knowledge base reset for workspace %s.",
            workspace_id,
        )

        work_items = crud.list_work_items(db, workspace_id=workspace_id, limit=100)

        documents_deleted = 0
        files_deleted = 0
        driver = get_storage_driver()

        for work_item in work_items:
            if work_item.stored_filename and driver.exists(work_item.stored_filename):
                try:
                    driver.delete(work_item.stored_filename)
                    files_deleted += 1
                except Exception:
                    pass
            db.delete(work_item)
            documents_deleted += 1

        db.commit()

        vectors_deleted = embedding_service.clear_workspace_collection(
            workspace_id=workspace_id
        )
        bm25_service.invalidate(workspace_id=workspace_id)
        document_vocabulary_service.clear()

        query_service.document_strategy.update_document_vocabulary(
            document_vocabulary_service.get_expansion_map(),
        )

        logger.info(
            "Knowledge base reset completed for workspace %s.",
            workspace_id,
        )

        return {
            "documents_deleted": documents_deleted,
            "files_deleted": files_deleted,
            "vectors_deleted": vectors_deleted,
        }


knowledge_base_service = KnowledgeBaseService()