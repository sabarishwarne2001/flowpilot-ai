"""
AI Assistant orchestration service for FlowPilot AI.

Coordinates the complete conversational RAG workflow while maintaining
strict separation between business orchestration and infrastructure.

Responsibilities:

- Conversation validation
- Multi-tenant authorization
- Context retrieval
- Conversation memory
- LLM orchestration
- Citation generation
- Conversation persistence

The Assistant Service never performs:

- ChromaDB operations
- Embedding generation
- Prompt construction
- Provider-specific AI logic
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.models.assistant import Conversation
from app.models.work_item import WorkItem
from app.schemas.assistant import (
    ChatResponse,
    ConversationRole,
    SourceCitation,
)
from app.services.embedding_service import embedding_service
from app.services.llm_service import llm_service

logger = logging.getLogger(
    "app.services.assistant_service"
)


class AssistantService:
    """
    Coordinates conversational AI workflows.

    This service acts purely as an orchestrator between:

    - CRUD layer
    - Embedding Service
    - LLM Service
    """

    async def send_chat_message(
        self,
        db: Session,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
    ) -> ChatResponse:
        """
        Execute a complete conversational RAG transaction.
        """

        logger.info(
            "Conversation %s received a new message from user %s.",
            conversation_id,
            user_id,
        )

        conversation = self._get_conversation(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
        )

        work_items = self._resolve_scope(
            db=db,
            conversation=conversation,
            user_id=user_id,
        )

        context, citations = self._retrieve_context(
            db=db,
            work_items=work_items,
            query=query_text,
        )

        history = self._load_history(
            db=db,
            conversation=conversation,
        )

        response = llm_service.synthesize_response(
            query=query_text,
            context=context,
            history=history,
        )

        self._save_messages(
            db=db,
            conversation=conversation,
            query=query_text,
            response=response,
            citations=citations,
        )

        self._initialize_title(
            db=db,
            conversation=conversation,
            first_message=query_text,
            history=history,
        )

        return self._build_chat_response(
            response=response,
            citations=citations,
        )
    
    # ========================================================================
    # Conversation & Authorization
    # ========================================================================

    def _get_conversation(
        self,
        *,
        db: Session,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Conversation:
        """
        Retrieve a conversation while enforcing ownership.
        """

        conversation = crud.get_conversation_by_id(
            db,
            conversation_id=conversation_id,
            user_id=user_id,
        )

        if conversation is None:

            logger.error(
                "Conversation %s not found for user %s.",
                conversation_id,
                user_id,
            )

            raise ValueError(
                "Conversation not found or access denied."
            )

        return conversation

    def _resolve_scope(
        self,
        *,
        db: Session,
        conversation: Conversation,
        user_id: uuid.UUID,
    ) -> list[WorkItem]:
        """
        Resolve the searchable WorkItems for this conversation.

        Document Assistant:
            Returns exactly one validated WorkItem.

        Global Assistant:
            Returns every WorkItem owned by the user.
        """

        #
        # Document Assistant
        #
        if conversation.work_item_id is not None:

            work_item = crud.get_work_item_by_id(
                db,
                work_item_id=conversation.work_item_id,
                user_id=user_id,
            )

            if work_item is None:

                logger.error(
                    "WorkItem %s not found for user %s.",
                    conversation.work_item_id,
                    user_id,
                )

                raise ValueError(
                    "Associated document not found."
                )

            logger.info(
                "Document Assistant mode activated."
            )

            return [work_item]

        #
        # Global Assistant
        #
        work_items = crud.get_work_items_for_user(
            db,
            user_id=user_id,
            limit=1000,
        )

        logger.info(
            "Global Assistant resolved %d searchable documents.",
            len(work_items),
        )

        return work_items
    
    # ========================================================================
    # Context Retrieval
    # ========================================================================

    def _retrieve_context(
        self,
        *,
        db: Session,
        work_items: list[WorkItem],
        query: str,
    ) -> tuple[str, list[SourceCitation]]:
        """
        Retrieve semantic context from the Embedding Service.

        Returns:

        - concatenated context string
        - structured source citations
        """

        if not work_items:

            logger.info(
                "No searchable documents available."
            )

            return "", []

        filename_lookup = {
            item.id: item.original_filename
            for item in work_items
        }

        work_item_ids = [
            item.id
            for item in work_items
        ]

        #
        # Decide between Document Assistant
        # and Global Assistant.
        #
        if len(work_item_ids) == 1:

            results = embedding_service.similarity_search(
                query=query,
                top_k=settings.RAG_TOP_K,
                filter_work_item_id=work_item_ids[0],
                similarity_threshold=settings.RAG_SIMILARITY_THRESHOLD,
            )

        else:

            results = embedding_service.similarity_search(
                query=query,
                top_k=settings.RAG_TOP_K,
                filter_work_item_ids=work_item_ids,
                similarity_threshold=settings.RAG_SIMILARITY_THRESHOLD,
            )

        logger.info(
            "Retrieved %d semantic matches.",
            len(results),
        )

        context_chunks: list[str] = []

        citations: list[SourceCitation] = []

        context_length = 0

        for result in results:

            text = result["text"]

            if (
                context_length + len(text)
                > settings.RAG_MAX_CONTEXT_LENGTH
            ):

                remaining = (
                    settings.RAG_MAX_CONTEXT_LENGTH
                    - context_length
                )

                if remaining <= 50:
                    break

                text = text[:remaining]

            context_chunks.append(text)

            context_length += len(text)

            metadata = result.get(
                "metadata",
                {},
            )

            work_item_id = uuid.UUID(
                metadata["work_item_id"]
            )

            citations.append(

                self._build_citation(
                    work_item_id=work_item_id,
                    filename=filename_lookup.get(
                        work_item_id,
                        "Unknown Source",
                    ),
                    metadata=metadata,
                    text=text,
                    similarity_score=result.get(
                        "similarity_score",
                        0.0,
                    ),
                )

            )

            if (
                len(citations)
                >= settings.MAX_SOURCE_CITATIONS
            ):
                break

        context = "\n\n".join(
            context_chunks
        )

        logger.info(
            "Final context length: %d characters.",
            len(context),
        )

        return (
            context,
            citations,
        )

    def _build_citation(
        self,
        *,
        work_item_id: uuid.UUID,
        filename: str,
        metadata: dict[str, Any],
        text: str,
        similarity_score: float,
    ) -> SourceCitation:
        """
        Construct a standardized source citation.
        """

        return SourceCitation(
            work_item_id=work_item_id,
            original_filename=filename,
            chunk_index=metadata.get(
                "chunk_index",
                0,
            ),
            page_number=metadata.get(
                "page_number",
            ),
            similarity_score=similarity_score,
            snippet=text[:200],
        )
    
    # ========================================================================
    # Conversation Memory & LLM
    # ========================================================================

    def _load_history(
        self,
        *,
        db: Session,
        conversation: Conversation,
    ) -> list[dict[str, str]]:
        """
        Load recent conversation history.

        Only the configured number of recent messages are supplied
        to the LLM in order to control prompt size.
        """

        messages = crud.get_conversation_messages(
            db,
            conversation_id=conversation.id,
            limit=settings.MAX_CONVERSATION_MESSAGES,
        )

        logger.info(
            "Loaded %d historical messages.",
            len(messages),
        )

        return [
            {
                "role": message.role,
                "content": message.content,
            }
            for message in messages
        ]

    def _generate_response(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
    ) -> str:
        """
        Generate an AI response.

        Prompt construction and provider routing remain the
        responsibility of the LLMService.
        """

        logger.info(
            "Generating assistant response."
        )

        response = llm_service.synthesize_response(
            query=query,
            context=context,
            history=history,
        )

        logger.info(
            "Assistant response generated successfully."
        )

        return response
    
    # ========================================================================
    # Persistence
    # ========================================================================

    def _save_messages(
        self,
        *,
        db: Session,
        conversation: Conversation,
        query: str,
        response: str,
        citations: list[SourceCitation],
    ) -> None:
        """
        Persist both the user's message and the assistant's response.

        Assistant responses include structured source citations
        used during semantic retrieval.
        """

        logger.info(
            "Persisting conversation messages."
        )

        crud.create_conversation_message(
            db,
            conversation_id=conversation.id,
            role=ConversationRole.USER.value,
            content=query,
        )

        serialized_sources = [
            citation.model_dump(mode="json")
            for citation in citations
        ]

        crud.create_conversation_message(
            db,
            conversation_id=conversation.id,
            role=ConversationRole.ASSISTANT.value,
            content=response,
            sources=serialized_sources,
        )

        logger.info(
            "Conversation messages persisted successfully."
        )

    def _initialize_title(
        self,
        *,
        db: Session,
        conversation: Conversation,
        first_message: str,
        history: list[dict[str, str]],
    ) -> None:
        """
        Initialize the conversation title from the first user message.

        Titles are generated only once. Existing titles are never
        overwritten.
        """

        if history:
            return

        max_length = (
            settings.MAX_CONVERSATION_TITLE_LENGTH
        )

        title = first_message.strip()

        #
        # Keep room for "..."
        #
        if len(title) > max_length:

            title = (
                title[: max_length - 3]
                .rstrip()
                + "..."
            )

        crud.update_conversation_title(
            db,
            conversation=conversation,
            title=title,
        )

        logger.info(
            "Conversation %s title initialized.",
            conversation.id,
        )

    # ========================================================================
    # Response Builder
    # ========================================================================

    def _build_chat_response(
        self,
        *,
        response: str,
        citations: list[SourceCitation],
    ) -> ChatResponse:
        """
        Construct the standardized API response.

        Keeping response construction centralized allows future
        expansion without modifying the orchestration workflow.

        Future additions may include:

        - token usage
        - model name
        - provider
        - latency
        - estimated cost
        """

        return ChatResponse(
            response=response,
            sources=citations,
        )


assistant_service = AssistantService()


    
