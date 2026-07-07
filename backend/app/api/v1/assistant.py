"""
AI Assistant API router for FlowPilot AI.

Provides authenticated endpoints for:

- Conversation management
- Conversation history
- AI chat interactions
- Global Assistant
- Document Assistant

This router remains intentionally thin.

Responsibilities:

- Request validation
- Authentication
- Dependency injection
- HTTP exception mapping
- Delegation to CRUD and Service layers

Business logic belongs inside AssistantService.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Response,
    status,
)
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.assistant import Conversation
from app.models.user import User
from app.schemas.assistant import (
    ChatQuery,
    ChatResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.services import assistant_service

logger = logging.getLogger(
    "app.api.v1.assistant"
)

router = APIRouter(
    tags=["AI Assistant"],
)


# ============================================================
# Internal Helpers
# ============================================================

def _get_user_conversation(
    *,
    db: Session,
    conversation_id: uuid.UUID,
    current_user: User,
) -> Conversation:
    """
    Retrieve a conversation owned by the authenticated user.

    Raises HTTP 404 if the conversation does not exist or
    does not belong to the authenticated user.
    """

    conversation = crud.get_conversation_by_id(
        db,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )

    if conversation is None:

        logger.warning(
            "Conversation %s not found for user %s.",
            conversation_id,
            current_user.id,
        )

        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found.",
        )

    return conversation


# ============================================================
# Conversation Endpoints
# ============================================================

@router.post(
    "/conversations",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    status_code=status.HTTP_201_CREATED,
    summary="Create Conversation",
)
async def create_chat_session(
    conversation_in: ConversationCreate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user
    ),
) -> ConversationResponse:
    """
    Create a new conversation session.

    If a WorkItem is supplied,
    ownership is validated before creation.
    """

    if conversation_in.work_item_id is not None:

        work_item = crud.get_work_item_by_id(
            db,
            work_item_id=conversation_in.work_item_id,
            user_id=current_user.id,
        )

        if work_item is None:

            logger.warning(
                "User %s attempted to use unauthorized WorkItem %s.",
                current_user.id,
                conversation_in.work_item_id,
            )

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated document not found.",
            )

    conversation = crud.create_conversation(
        db,
        user_id=current_user.id,
        title=conversation_in.title
        or "New Conversation",
        work_item_id=conversation_in.work_item_id,
    )

    logger.info(
        "Conversation %s created for user %s.",
        conversation.id,
        current_user.id,
    )

    return conversation

@router.get(
    "/conversations",
    response_model=list[ConversationResponse],
    response_model_exclude_none=True,
    summary="List Conversations",
)
async def list_conversations(
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
    skip: int = Query(
        default=0,
        ge=0,
        description="Number of conversations to skip.",
    ),
    limit: int = Query(
        default=100,
        ge=1,
        le=100,
        description="Maximum number of conversations to return.",
    ),
) -> list[ConversationResponse]:
    """
    Return all conversations owned by the authenticated user.
    """

    conversations = crud.get_user_conversations(
        db,
        user_id=current_user.id,
        skip=skip,
        limit=limit,
    )

    logger.info(
        "Returned %d conversations for user %s.",
        len(conversations),
        current_user.id,
    )

    return conversations


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    summary="Get Conversation",
)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> ConversationResponse:
    """
    Retrieve a conversation together with its
    chronological message history.
    """

    conversation = _get_user_conversation(
        db=db,
        conversation_id=conversation_id,
        current_user=current_user,
    )

    conversation.messages = (
        crud.get_conversation_messages(
            db,
            conversation_id=conversation.id,
        )
    )

    logger.info(
        "Conversation %s retrieved for user %s.",
        conversation.id,
        current_user.id,
    )

    return conversation

@router.patch(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    summary="Rename Conversation",
)
async def rename_conversation(
    conversation_id: uuid.UUID,
    conversation_in: ConversationUpdate,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> ConversationResponse:
    """
    Rename an existing conversation.

    Only the owner of the conversation is permitted
    to modify its title.
    """

    conversation = _get_user_conversation(
        db=db,
        conversation_id=conversation_id,
        current_user=current_user,
    )

    updated_conversation = crud.update_conversation_title(
        db,
        conversation=conversation,
        title=conversation_in.title,
    )

    logger.info(
        "Conversation %s renamed by user %s.",
        conversation_id,
        current_user.id,
    )

    return updated_conversation


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Conversation",
)
async def delete_chat_session(
    conversation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> Response:
    """
    Delete a conversation and all associated
    conversation messages.

    Child messages are removed automatically
    through database cascade rules.
    """

    conversation = _get_user_conversation(
        db=db,
        conversation_id=conversation_id,
        current_user=current_user,
    )

    crud.delete_conversation(
        db,
        conversation=conversation,
    )

    logger.info(
        "Conversation %s deleted by user %s.",
        conversation_id,
        current_user.id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )

@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatResponse,
    summary="Send Message",
    response_description=(
        "AI-generated response with structured source citations."
    ),
)
async def post_chat_query(
    conversation_id: uuid.UUID,
    query_in: ChatQuery,
    db: Session = Depends(deps.get_db),
    current_user: User = Depends(
        deps.get_current_active_user,
    ),
) -> ChatResponse:
    """
    Submit a user message to the AI Assistant.

    The AssistantService orchestrates:

    - conversation validation
    - authorization
    - semantic retrieval
    - conversation memory
    - LLM response generation
    - persistence
    """

    try:

        response = await assistant_service.send_chat_message(
            db=db,
            conversation_id=conversation_id,
            user_id=current_user.id,
            query_text=query_in.content,
        )

        logger.info(
            "Assistant response generated for conversation %s.",
            conversation_id,
        )

        return response

    except ValueError as exc:

        logger.warning(
            "Assistant request rejected for conversation %s: %s",
            conversation_id,
            str(exc),
        )

        error_message = str(exc).lower()

        #
        # Resource lookup failures
        #
        if (
            "conversation" in error_message
            or "document" in error_message
            or "workitem" in error_message
        ):

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )

        #
        # Validation failures
        #
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    except Exception:

        logger.exception(
            "Unexpected assistant failure for conversation %s.",
            conversation_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error.",
        )