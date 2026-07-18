"""
Knowledge Base administration service.

Responsible for maintaining the searchable
knowledge base.

Responsibilities

- Clear knowledge base
- Rebuild indexes
- Future re-index
- Future statistics
"""

from __future__ import annotations

import logging

from pathlib import Path

from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.db.session import SessionLocal

from app.services.embedding_service import (
    embedding_service,
)

from app.services.bm25_service import (
    bm25_service,
)

from app.services.document_processor import (
    document_vocabulary_service,
)

from app.services.query_service import (
    query_service,
)

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """
    Knowledge Base administration.
    """

    def clear_knowledge_base(
        self,
    ) -> dict[str, int]:
        """
        Completely reset the searchable
        knowledge base.

        Removes

        - database records
        - uploaded files
        - vector embeddings
        - BM25 index

        Returns statistics.
        """

        logger.info(
            "Starting knowledge base reset."
        )

        db: Session = SessionLocal()

        try:

            work_items = crud.get_all_work_items(
                db,
            )

            documents_deleted = 0

            files_deleted = 0

            for work_item in work_items:

                file_path = Path(
                    settings.UPLOAD_DIR
                ) / work_item.stored_filename

                if file_path.exists():

                    file_path.unlink()

                    files_deleted += 1

                db.delete(
                    work_item,
                )

                documents_deleted += 1

            db.commit()

            vectors_deleted = (
                embedding_service.clear_collection()
            )

            bm25_service.rebuild_index()

            document_vocabulary_service.clear()

            query_service.update_document_vocabulary(
                document_vocabulary_service.get_expansion_map(),
            )

            logger.info(
                "Document vocabulary cleared.",
            )

            logger.info(
                "Knowledge base reset completed."
            )

            return {

                "documents_deleted": documents_deleted,

                "files_deleted": files_deleted,

                "vectors_deleted": vectors_deleted,

            }
        
        except Exception:
            db.rollback()
            logger.exception("Knowledge base reset failed.")
            raise

        finally:

            db.close()

knowledge_base_service = KnowledgeBaseService()