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
from collections import OrderedDict

from sqlalchemy.orm import Session

from app import crud
from app.models.ai_settings import AISettings
from app.core.config import settings
from app.models.assistant import Conversation
from app.models.work_item import WorkItem
from app.schemas.assistant import (
    ChatResponse,
    ConversationRole,
    SourceCitation,
    TokenUsage
)

from app.services.llm_service import llm_service
from app.services.retrieval_service import retrieval_service
from app.services.citation_service import (
    citation_service,
)
from app.services.snippet_service import (
    snippet_service,
)

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

        #
        # Skip the LLM when there is no searchable knowledge.
        #
        if not context.strip():

            logger.info(
                "Knowledge base is empty. Returning canned response."
            )

            response = (
                "Your knowledge base is currently empty.\n\n"
                "Please upload one or more documents before asking questions."
            )

            token_usage = TokenUsage(
                provider="none",
                model="none",
                prompt_tokens=0,
                completion_tokens=0,
                total_tokens=0,
                estimated_cost=0.0,
            )

        else:

            ai_settings = self._get_ai_settings(
                db=db,
                user_id=user_id,
            )

            response, token_usage = self._generate_response(
                query=query_text,
                context=context,
                history=history,
                ai_settings=ai_settings,
            )

        self._save_messages(
            db=db,
            conversation=conversation,
            query=query_text,
            response=response,
            citations=citations,
            token_usage=token_usage,
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
            token_usage=token_usage,
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


    def _deduplicate_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Remove duplicate semantic matches.

        When multiple retrieved chunks represent the same
        document page and chunk index, retain only the
        highest-similarity match.
        """

        unique: OrderedDict[
            tuple[str, int, int],
            dict[str, Any],
        ] = OrderedDict()

        for result in sorted(
            results,
            key=lambda item: item.get(
                "similarity_score",
                0.0,
            ),
            reverse=True,
        ):

            metadata = result.get(
                "metadata",
                {},
            )

            key = (
                metadata.get("work_item_id", ""),
                metadata.get("page_number", -1),
                metadata.get("chunk_index", -1),
            )

            if key not in unique:
                unique[key] = result

        logger.info(
            "Deduplicated %d semantic matches to %d.",
            len(results),
            len(unique),
        )

        return list(unique.values())
    

    def _rank_documents(
        self,
        results: list[dict[str, Any]],
    ) -> dict[str, float]:
        """
        Compute an overall score for every document.

        The score rewards:

        - higher similarity
        - multiple supporting chunks

        This helps surface the most relevant document instead of
        simply selecting whichever individual chunk scored highest.
        """

        document_scores: dict[str, float] = {}

        for result in results:

            metadata = result.get("metadata", {})

            work_item_id = metadata.get("work_item_id")

            if not work_item_id:
                continue

            similarity = result.get(
                "similarity_score",
                0.0,
            )

            #
            # Accumulate evidence from multiple chunks.
            #
            document_scores[work_item_id] = (
                document_scores.get(work_item_id, 0.0)
                + similarity
            )

        logger.info(
            "Document ranking scores: %s",
            document_scores,
        )

        return document_scores
    

    def _compute_chunk_score(
        self,
        *,
        query: str,
        chunk_text: str,
        similarity_score: float,
        lexical_score: float,
        document_name: str,
    ) -> float:
        """
        Compute a production-quality score for a retrieved chunk.

        Multiple ranking signals are combined instead of relying only
        on embedding similarity.
        """

        query_lower = query.lower()
        chunk_lower = chunk_text.lower()

        score = similarity_score * 0.30

        #
        # Keyword overlap
        #
        query_words = {
            word
            for word in query_lower.split()
            if len(word) >= 3
        }

        chunk_words = set(chunk_lower.split())

        overlap = len(
            query_words.intersection(chunk_words)
        )

        if query_words:
            score += (
                overlap / len(query_words)
            ) * 0.30

        #
        # Exact query phrase
        #
        if query_lower in chunk_lower:
            score += 0.20

        #
        # Filename relevance
        #
        if any(
            word in document_name.lower()
            for word in query_words
        ):
            score += 0.10

        #
        # Hybrid lexical score.
        #
        score += (
            lexical_score * 0.25
        )

        return score
    

    def _compress_context_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Remove highly overlapping retrieved chunks.

        This reduces prompt size while preserving
        semantic coverage.
        """

        compressed: list[dict[str, Any]] = []

        seen_prefixes: set[str] = set()

        for result in results:

            text = result["text"].strip()

            #
            # First 120 characters are usually enough
            # to identify overlapping chunks.
            #
            prefix = text[:120].lower()

            if prefix in seen_prefixes:
                continue

            seen_prefixes.add(prefix)

            compressed.append(result)

        logger.info(
            "Compressed retrieved context from %d to %d chunk(s).",
            len(results),
            len(compressed),
        )

        return compressed
    

    def _determine_top_k(
        self,
        query: str,
    ) -> int:
        """
        Dynamically determine the number of chunks
        to retrieve based on the user's query.
        """

        query = query.lower().strip()

        broad_keywords = (
            "summarize",
            "summary",
            "overview",
            "everything",
            "all",
            "entire",
            "complete",
            "explain",
        )

        factual_keywords = (
            "email",
            "phone",
            "date",
            "salary",
            "amount",
            "id",
            "address",
            "who",
            "when",
            "where",
        )

        #
        # Broad queries require more context.
        #
        if any(
            keyword in query
            for keyword in broad_keywords
        ):
            return min(
                settings.RAG_TOP_K + 3,
                10,
            )

        #
        # Simple factual lookups need fewer chunks.
        #
        if any(
            keyword in query
            for keyword in factual_keywords
        ):
            return max(
                3,
                settings.RAG_TOP_K - 2,
            )

        return settings.RAG_TOP_K
    
    
    def _determine_similarity_threshold(
        self,
        query: str,
    ) -> float:
        """
        Dynamically determine the minimum similarity
        score required for retrieved chunks.
        """

        query = query.lower().strip()

        broad_keywords = (
            "summarize",
            "summary",
            "overview",
            "all",
            "everything",
            "entire",
            "complete",
            "explain",
        )

        factual_keywords = (
            "email",
            "phone",
            "address",
            "salary",
            "id",
            "date",
            "where",
            "who",
            "when",
        )

        #
        # Broad questions:
        # allow slightly lower similarity
        #
        if any(keyword in query for keyword in broad_keywords):
            return max(
                settings.RAG_SIMILARITY_THRESHOLD - 0.05,
                0.15,
            )

        #
        # Precise questions:
        # require higher similarity
        #
        if any(keyword in query for keyword in factual_keywords):
            return min(
                settings.RAG_SIMILARITY_THRESHOLD + 0.10,
                0.40,
            )

        return settings.RAG_SIMILARITY_THRESHOLD


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
        - formatted context string
        - structured source citations
        """
        if not work_items:
            logger.info("No searchable documents available.")
            return "", []

        filename_lookup = {item.id: item.original_filename for item in work_items}
        work_item_ids = [item.id for item in work_items]
        
        top_k = self._determine_top_k(query)
        similarity_threshold = self._determine_similarity_threshold(query)

        logger.info("Dynamic similarity threshold: %.2f", similarity_threshold)
        logger.info("Dynamic top_k selected: %d", top_k)

        # -----------------------------
        # Document Assistant
        # -----------------------------
        results = retrieval_service.hybrid_search(
            query=query,
            work_item_ids=[str(work_item_id) for work_item_id in work_item_ids],
            top_k=top_k,
            similarity_threshold=similarity_threshold,
        )
        logger.info("Hybrid retrieval returned %d result(s).", len(results))
        logger.info("Retrieved %d semantic matches.", len(results))

        # -----------------------------
        # Clean & Compress Matches
        # -----------------------------
        results = self._deduplicate_results(results)
        results = self._compress_context_results(results)

        if not results:
            logger.info("No retrieval results after filtering.")
            return "", []

        # -----------------------------
        # Rank Retrieved Documents
        # -----------------------------
        document_scores = self._rank_documents(results)

        # Production reranking
        results.sort(
            key=lambda item: (
                # 1. Best overall document
                -document_scores.get(item.get("metadata", {}).get("work_item_id", ""), 0.0),
                # 2. Best chunk within that document
                -self._compute_chunk_score(
                    query=query,
                    chunk_text=item["text"],
                    similarity_score=item.get("similarity_score", 0.0),
                    document_name=item.get("metadata", {}).get("original_filename", ""),
                    lexical_score=item.get("lexical_score", 0.0),
                ),
                # 3. Preserve natural document flow.
                item.get("metadata", {}).get("page_number", 0),
                item.get("metadata", {}).get("chunk_index", 0),
            ),
        )

        logger.info("Top reranked chunks:")
        for index, result in enumerate(results[:5], start=1):
            metadata = result.get("metadata", {})
            rerank_score = self._compute_chunk_score(
                query=query,
                chunk_text=result["text"],
                similarity_score=result.get("similarity_score", 0.0),
                document_name=metadata.get("original_filename", ""),
                lexical_score=result.get("lexical_score", 0.0),
            )
            logger.info(
                "%d | %s | page=%s | similarity=%.3f | rerank=%.3f",
                index,
                metadata.get("original_filename"),
                metadata.get("page_number"),
                result.get("similarity_score", 0.0),
                rerank_score,
            )

        context_chunks: list[str] = []
        context_length = 0
        current_document: str | None = None
        current_page: int | None = None

        for result in results:
            metadata = result.get("metadata", {})
            document_name = metadata.get("original_filename", "Unknown Document")
            page_number = metadata.get("page_number")
            text = result["text"].strip()

            # Respect context budget.
            if context_length + len(text) > settings.RAG_MAX_CONTEXT_LENGTH:
                remaining = settings.RAG_MAX_CONTEXT_LENGTH - context_length
                if remaining <= 50:
                    break
                text = text[:remaining]

            # Document header.
            if document_name != current_document:
                current_document = document_name
                current_page = None
                context_chunks.append(
                    "\n" + "=" * 70 + "\n" + f"Document: {document_name}\n" + "=" * 70
                )

            # Page header.
            if page_number != current_page:
                current_page = page_number
                if page_number is not None:
                    context_chunks.append(f"\nPage {page_number}\n" + "-" * 25)

            context_chunks.append(text)
            context_length += len(text)

        # Build context.
        context = "\n\n".join(context_chunks)

        # Rank citations before converting them into SourceCitation objects.
        ranked_results = citation_service.rank_citations(results)
        citations: list[SourceCitation] = []

        for result in ranked_results:
            metadata = result.get("metadata", {})
            work_item_id = uuid.UUID(metadata["work_item_id"])
            citation = self._build_citation(
                work_item_id=work_item_id,
                filename=filename_lookup.get(work_item_id, "Unknown Source"),
                metadata=metadata,
                text=result["text"],
                query=query,
                similarity_score=result.get("similarity_score", 0.0),
            )
            citations.append(citation)

        logger.info("Final context length: %d characters.", len(context))
        logger.info("Returning %d ranked citation(s).", len(citations))
        
        for citation in citations:
            logger.info(
                "Citation -> %s | Page %s | Chunk %s",
                citation.original_filename,
                citation.page_number,
                citation.chunk_index,
            )

        return context, citations


    def _build_citation(
        self,
        *,
        work_item_id: uuid.UUID,
        filename: str,
        metadata: dict[str, Any],
        text: str,
        query: str,
        similarity_score: float,
    ) -> SourceCitation:
        """
        Construct a standardized source citation.
        """

        snippet = snippet_service.generate_snippet(
            text=text,
            query=query,
        )

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
            snippet=snippet,
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
    
    def _get_ai_settings(
        self,
        *,
        db: Session,
        user_id: uuid.UUID,
    ) -> AISettings:
        """
        Load the user's AI runtime configuration.

        AI Settings are required for every LLM request.
        """

        ai_settings = crud.get_ai_settings(
            db=db,
            user_id=user_id,
        )

        if ai_settings is None:
            raise ValueError(
                "AI settings have not been configured."
            )

        return ai_settings

    def _generate_response(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
        ai_settings: AISettings,
    ) -> tuple[str, TokenUsage]:
        """
        Generate an AI response.

        Prompt construction and provider routing remain the
        responsibility of the LLMService.
        """

        logger.info(
            "Generating assistant response."
        )

        response, token_usage = llm_service.synthesize_response(
            query=query,
            context=context,
            history=history,
            ai_settings=ai_settings,
        )

        logger.info(
            "Assistant response generated successfully."
        )

        return response, token_usage
    
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
        token_usage: TokenUsage,
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
            token_usage=token_usage.model_dump(mode="json"),
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
        token_usage: TokenUsage,
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
            token_usage=token_usage,
        )


assistant_service = AssistantService()


    
