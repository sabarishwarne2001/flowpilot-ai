"""
Knowledge Base administration service.
"""

from __future__ import annotations

import logging
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class KnowledgeBaseService:
    """
    Knowledge Base administration.
    """

    def clear_knowledge_base(
        self,
    ) -> dict[str, int]:
        """
        Completely reset the searchable knowledge base.
        This operation is temporarily disabled under ARCH-02 Step 7.
        """
        logger.warning(
            "Knowledge base reset aborted. Operation is temporarily disabled "
            "until vector store partitioning is completed in Step 9."
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reset is temporarily disabled until vector store partitioning is completed in Step 9."
        )

knowledge_base_service = KnowledgeBaseService()