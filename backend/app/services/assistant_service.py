"""
AI Assistant orchestration service for FlowPilot AI.
ARCH-11.5 Step 1 & 6: Request tracing, pre-generated message IDs, and HTTP 402 quota conversions.
"""

from __future__ import annotations

import logging
import uuid
from collections import OrderedDict
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crud
from app.core.config import settings
from app.core.exceptions import SpendLimitExceededError
from app.core.request_context import context_fields, request_scope, stage
from app.models.ai_settings import AISettings
from app.models.assistant import Conversation
from app.models.work_item import WorkItem
from app.schemas.assistant import (
    ChatResponse,
    ConversationRole,
    SourceCitation,
    TokenUsage,
)
from app.services.citation_service import citation_service, snippet_service
from app.services.context_assembly_service import context_assembly_service
from app.services.llm_service import llm_service
from app.services.retrieval_service import retrieval_service

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
        request_id: str | None = None,
    ) -> ChatResponse:
        conversation = self._get_conversation(
            db=db,
            conversation_id=conversation_id,
            user_id=user_id,
        )

        with request_scope(
            request_id=request_id or str(uuid.uuid4()),
            workspace_id=conversation.workspace_id,
            organization_id=conversation.organization_id if hasattr(conversation, "organization_id") else None,
        ) as trace:
            logger.info(
                "Conversation %s received a new message from user %s.",
                conversation_id,
                user_id,
            )

            work_items = self._resolve_scope(
                db=db,
                conversation=conversation,
                user_id=user_id,
            )

            with stage("retrieval"):
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

            assistant_message_id = uuid.uuid4()

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

                workspace = crud.get_workspace(db, workspace_id=conversation.workspace_id)
                organization_id = workspace.organization_id if workspace else conversation.workspace_id

                try:
                    response, token_usage = llm_service.synthesize_response(
                        db=db,
                        organization_id=organization_id,
                        workspace_id=conversation.workspace_id,
                        conversation_id=conversation.id,
                        message_id=assistant_message_id,
                        query=query_text,
                        context=context,
                        history=history,
                        ai_settings=ai_settings,
                    )
                except SpendLimitExceededError as exc:
                    logger.warning(
                        "assistant.quota_blocked",
                        extra={"limit_key": exc.limit_key, **context_fields()},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_402_PAYMENT_REQUIRED,
                        detail=(
                            "This workspace has reached its monthly AI usage limit. "
                            "Retrieval still works; generation is paused until the "
                            "limit resets or is raised."
                        ),
                    ) from exc

            self._save_messages(
                db=db,
                conversation=conversation,
                query=query_text,
                response=response,
                citations=citations,
                token_usage=token_usage,
                assistant_message_id=assistant_message_id,
            )

            self._initialize_title(
                db=db,
                conversation=conversation,
                first_message=query_text,
                history=history,
            )

            logger.info("assistant.complete", extra=trace.as_details())
            return self._build_chat_response(
                response=response,
                citations=citations,
                token_usage=token_usage,
            )

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
                raise ValueError("Associated document not found.")
            return [work_item]

        return crud.list_work_items(
            db,
            workspace_id=conversation.workspace_id,
            limit=1000,
        )

    def _retrieve_context(
        self,
        *,
        db: Session,
        conversation: Conversation,
        work_items: list[WorkItem],
        query: str,
    ) -> tuple[str, list[SourceCitation]]:
        if not work_items:
            return "", []

        filename_lookup = {item.id: item.original_filename for item in work_items}
        work_item_ids = [item.id for item in work_items]

        results = retrieval_service.hybrid_search(
            workspace_id=conversation.workspace_id,
            query=query,
            work_item_ids=[str(work_item_id) for work_item_id in work_item_ids],
            top_k=settings.RAG_TOP_K,
            similarity_threshold=settings.RAG_SIMILARITY_THRESHOLD,
            db=db,
            request_id=str(getattr(conversation, "id", "")),
        )

        if not results:
            return "", []

        with stage("context_assembly"):
            assembled = context_assembly_service.assemble(
                results,
                max_characters=settings.RAG_MAX_CONTEXT_LENGTH,
                block_threshold=settings.CONTEXT_INJECTION_BLOCK_THRESHOLD,
            )
            context = assembled.text

        with stage("citation"):
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
        snippet = snippet_service.generate(
            text=text,
            query=query,
            chunk_page_start=metadata.get("page_start_char"),
        )

        return SourceCitation(
            work_item_id=work_item_id,
            original_filename=filename,
            chunk_index=metadata.get("chunk_index", 0),
            page_number=metadata.get("page_number"),
            similarity_score=similarity_score,
            snippet=snippet.text,
        )

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

    def _save_messages(
        self,
        *,
        db: Session,
        conversation: Conversation,
        query: str,
        response: str,
        citations: list[SourceCitation],
        token_usage: TokenUsage,
        assistant_message_id: uuid.UUID | None = None,
    ) -> None:
        crud.create_conversation_message(
            db,
            conversation_id=conversation.id,
            role=ConversationRole.USER.value,
            content=query,
        )

        serialized_sources = [
            citation.model_dump(mode="json") for citation in citations
        ]

        from app.models.assistant import ConversationMessage

        msg = ConversationMessage(
            id=assistant_message_id or uuid.uuid4(),
            conversation_id=conversation.id,
            role=ConversationRole.ASSISTANT.value,
            content=response,
            sources=serialized_sources,
            token_usage=token_usage.model_dump(mode="json"),
        )
        db.add(msg)
        db.flush([msg])

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

__all__ = ["AssistantService", "assistant_service"]