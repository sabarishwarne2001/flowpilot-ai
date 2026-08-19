"""
AI Assistant orchestration service for FlowPilot AI.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.models.ai_settings import AISettings
from app.models.assistant import Conversation
from app.models.work_item import WorkItem
from app.schemas.assistant import (
    ChatResponse,
    ConversationRole,
    SourceCitation,
    TokenUsage,
)
from app.services.citation_service import citation_service
from app.services.llm_service import llm_service
from app.services.retrieval_service import retrieval_service
from app.services.snippet_service import snippet_service

logger = logging.getLogger("app.services.assistant_service")


class AssistantService:
    """
    Coordinates conversational AI workflows scoped strictly to a workspace.
    """

    async def send_chat_message(
        self,
        db: Session,
        *,
        conversation_id: uuid.UUID,
        user_id: uuid.UUID,
        query_text: str,
    ) -> ChatResponse:
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
            conversation=conversation,
            work_items=work_items,
            query=query_text,
        )

        history = self._load_history(
            db=db,
            conversation=conversation,
        )

        if not context.strip():
            logger.info("Knowledge base is empty. Returning canned response.")
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
                workspace_id=conversation.workspace_id,
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
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
        conversation = db.execute(statement).scalar_one_or_none()

        if conversation is None:
            logger.error(
                "Conversation %s not found for user %s.",
                conversation_id,
                user_id,
            )
            raise ValueError("Conversation not found or access denied.")

        return conversation

    def _resolve_scope(
        self,
        *,
        db: Session,
        conversation: Conversation,
        user_id: uuid.UUID,
    ) -> list[WorkItem]:
        if conversation.work_item_id is not None:
            work_item = crud.get_work_item(
                db,
                workspace_id=conversation.workspace_id,
                work_item_id=conversation.work_item_id,
            )

            if work_item is None:
                logger.error(
                    "WorkItem %s not found in workspace %s.",
                    conversation.work_item_id,
                    conversation.workspace_id,
                )
                raise ValueError("Associated document not found.")

            logger.info("Document Assistant mode activated.")
            return [work_item]

        work_items = crud.list_work_items(
            db,
            workspace_id=conversation.workspace_id,
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
        unique: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()

        for result in sorted(
            results,
            key=lambda item: item.get("similarity_score", 0.0),
            reverse=True,
        ):
            metadata = result.get("metadata", {})
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
        document_scores: dict[str, float] = {}
        for result in results:
            metadata = result.get("metadata", {})
            work_item_id = metadata.get("work_item_id")
            if not work_item_id:
                continue

            similarity = result.get("similarity_score", 0.0)
            document_scores[work_item_id] = document_scores.get(work_item_id, 0.0) + similarity

        logger.info("Document ranking scores: %s", document_scores)
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
        query_lower = query.lower()
        chunk_lower = chunk_text.lower()
        score = similarity_score * 0.30

        query_words = {word for word in query_lower.split() if len(word) >= 3}
        chunk_words = set(chunk_lower.split())
        overlap = len(query_words.intersection(chunk_words))

        if query_words:
            score += (overlap / len(query_words)) * 0.30

        if query_lower in chunk_lower:
            score += 0.20

        if any(word in document_name.lower() for word in query_words):
            score += 0.10

        score += (lexical_score * 0.25)
        return score

    def _compress_context_results(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        compressed: list[dict[str, Any]] = []
        seen_prefixes: set[str] = set()

        for result in results:
            text = result["text"].strip()
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
        query = query.lower().strip()
        broad_keywords = ("summarize", "summary", "overview", "everything", "all", "entire", "complete", "explain")
        factual_keywords = ("email", "phone", "date", "salary", "amount", "id", "address", "who", "when", "where")

        if any(keyword in query for keyword in broad_keywords):
            return min(settings.RAG_TOP_K + 3, 10)
        if any(keyword in query for keyword in factual_keywords):
            return max(3, settings.RAG_TOP_K - 2)
        return settings.RAG_TOP_K

    def _determine_similarity_threshold(
        self,
        query: str,
    ) -> float:
        query = query.lower().strip()
        broad_keywords = ("summarize", "summary", "overview", "all", "everything", "entire", "complete", "explain")
        factual_keywords = ("email", "phone", "address", "salary", "id", "date", "where", "who", "when")

        if any(keyword in query for keyword in broad_keywords):
            return max(settings.RAG_SIMILARITY_THRESHOLD - 0.05, 0.15)
        if any(keyword in query for keyword in factual_keywords):
            return min(settings.RAG_SIMILARITY_THRESHOLD + 0.10, 0.40)
        return settings.RAG_SIMILARITY_THRESHOLD

    def _retrieve_context(
        self,
        *,
        db: Session,
        conversation: Conversation,
        work_items: list[WorkItem],
        query: str,
    ) -> tuple[str, list[SourceCitation]]:
        if not work_items:
            logger.info("No searchable documents available.")
            return "", []

        filename_lookup = {item.id: item.original_filename for item in work_items}
        work_item_ids = [item.id for item in work_items]
        
        top_k = self._determine_top_k(query)
        similarity_threshold = self._determine_similarity_threshold(query)

        # Thread workspace_id and active database session into hybrid retriever
        results = retrieval_service.hybrid_search(
            workspace_id=conversation.workspace_id,
            query=query,
            work_item_ids=[str(work_item_id) for work_item_id in work_item_ids],
            top_k=top_k,
            similarity_threshold=similarity_threshold,
            db=db,
        )
        logger.info("Hybrid retrieval returned %d result(s).", len(results))

        results = self._deduplicate_results(results)
        results = self._compress_context_results(results)

        if not results:
            logger.info("No retrieval results after filtering.")
            return "", []

        document_scores = self._rank_documents(results)

        results.sort(
            key=lambda item: (
                -document_scores.get(item.get("metadata", {}).get("work_item_id", ""), 0.0),
                -self._compute_chunk_score(
                    query=query,
                    chunk_text=item["text"],
                    similarity_score=item.get("similarity_score", 0.0),
                    document_name=item.get("metadata", {}).get("original_filename", ""),
                    lexical_score=item.get("lexical_score", 0.0),
                ),
                item.get("metadata", {}).get("page_number", 0),
                item.get("metadata", {}).get("chunk_index", 0),
            ),
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

            if context_length + len(text) > settings.RAG_MAX_CONTEXT_LENGTH:
                remaining = settings.RAG_MAX_CONTEXT_LENGTH - context_length
                if remaining <= 50:
                    break
                text = text[:remaining]

            if document_name != current_document:
                current_document = document_name
                current_page = None
                context_chunks.append(
                    "\n" + "=" * 70 + "\n" + f"Document: {document_name}\n" + "=" * 70
                )

            if page_number != current_page:
                current_page = page_number
                if page_number is not None:
                    context_chunks.append(f"\nPage {page_number}\n" + "-" * 25)

            context_chunks.append(text)
            context_length += len(text)

        context = "\n\n".join(context_chunks)

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
        snippet = snippet_service.generate_snippet(
            text=text,
            query=query,
        )

        return SourceCitation(
            work_item_id=work_item_id,
            original_filename=filename,
            chunk_index=metadata.get("chunk_index", 0),
            page_number=metadata.get("page_number"),
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
        messages = crud.get_conversation_messages(
            db,
            conversation_id=conversation.id,
            limit=settings.MAX_CONVERSATION_MESSAGES,
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
        workspace_id: uuid.UUID,
    ) -> AISettings:
        ai_settings = crud.get_ai_settings(
            db=db,
            workspace_id=workspace_id,
        )

        if ai_settings is None:
            raise ValueError("AI settings have not been configured.")

        return ai_settings

    def _generate_response(
        self,
        *,
        query: str,
        context: str,
        history: list[dict[str, str]],
        ai_settings: AISettings,
    ) -> tuple[str, TokenUsage]:
        response, token_usage = llm_service.synthesize_response(
            query=query,
            context=context,
            history=history,
            ai_settings=ai_settings,
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

    def _initialize_title(
        self,
        *,
        db: Session,
        conversation: Conversation,
        first_message: str,
        history: list[dict[str, str]],
    ) -> None:
        if history:
            return

        max_length = settings.MAX_CONVERSATION_TITLE_LENGTH
        title = first_message.strip()

        if len(title) > max_length:
            title = title[: max_length - 3].rstrip() + "..."

        crud.update_conversation_title(
            db,
            conversation=conversation,
            title=title,
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
        return ChatResponse(
            response=response,
            sources=citations,
            token_usage=token_usage,
        )


assistant_service = AssistantService()