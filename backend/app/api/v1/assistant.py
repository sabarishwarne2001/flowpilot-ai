"""
AI Assistant API router for FlowPilot AI.
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
from app.schemas.assistant import (
    ChatQuery,
    ChatResponse,
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
)
from app.services.assistant_service import assistant_service

logger = logging.getLogger("app.api.v1.assistant")

router = APIRouter(
    tags=["AI Assistant"],
)


def _get_user_conversation(
    *,
    db: Session,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation:
    conversation = crud.get_conversation(
        db,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        user_id=user_id,
    )

    if conversation is None:
        logger.warning(
            "Conversation %s not found for user %s in workspace %s.",
            conversation_id,
            user_id,
            workspace_id,
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ConversationResponse:
    if conversation_in.work_item_id is not None:
        work_item = crud.get_work_item(
            db,
            workspace_id=context.workspace_id,
            work_item_id=conversation_in.work_item_id,
        )

        if work_item is None:
            logger.warning(
                "User %s attempted to use unauthorized WorkItem %s.",
                context.user_id,
                conversation_in.work_item_id,
            )
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Associated document not found.",
            )

    conversation = crud.create_conversation(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        title=conversation_in.title or "New Conversation",
        work_item_id=conversation_in.work_item_id,
    )
    db.commit()
    db.refresh(conversation)

    logger.info(
        "Conversation %s created for user %s inside workspace %s.",
        conversation.id,
        context.user_id,
        context.workspace_id,
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
    skip: int = Query(default=0, ge=0, description="Number of conversations to skip."),
    limit: int = Query(default=100, ge=1, le=100, description="Maximum number of conversations to return."),
) -> list[ConversationResponse]:
    conversations = crud.list_conversations(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        skip=skip,
        limit=limit,
    )

    logger.info(
        "Returned %d conversations for user %s in workspace %s.",
        len(conversations),
        context.user_id,
        context.workspace_id,
    )

    return conversations


@router.get(
    "/documents/{work_item_id}/conversation",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    summary="Get Document Conversation",
)
async def get_document_conversation(
    work_item_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationResponse:
    work_item = crud.get_work_item(
        db,
        workspace_id=context.workspace_id,
        work_item_id=work_item_id,
    )

    if work_item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Associated document not found.",
        )

    conversation = crud.get_document_conversation(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        work_item_id=work_item_id,
    )

    if conversation is None:
        conversation = crud.create_conversation(
            db,
            workspace_id=context.workspace_id,
            user_id=context.user_id,
            title=work_item.original_filename,
            work_item_id=work_item_id,
        )
        db.commit()
        db.refresh(conversation)

        logger.info(
            "Created and committed document conversation %s for WorkItem %s in workspace %s.",
            conversation.id,
            work_item_id,
            context.workspace_id,
        )
    else:
        logger.info(
            "Reusing document conversation %s for WorkItem %s in workspace %s.",
            conversation.id,
            work_item_id,
            context.workspace_id,
        )

    return conversation


@router.get(
    "/conversations/{conversation_id}",
    response_model=ConversationResponse,
    response_model_exclude_none=True,
    summary="Get Conversation",
)
async def get_conversation(
    conversation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationResponse:
    conversation = _get_user_conversation(
        db=db,
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        user_id=context.user_id,
    )

    conversation.messages = crud.get_conversation_messages(
        db,
        conversation_id=conversation.id,
    )

    logger.info(
        "Conversation %s retrieved for user %s inside workspace %s.",
        conversation.id,
        context.user_id,
        context.workspace_id,
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ConversationResponse:
    conversation = _get_user_conversation(
        db=db,
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        user_id=context.user_id,
    )

    updated_conversation = crud.update_conversation_title(
        db,
        conversation=conversation,
        title=conversation_in.title,
    )
    db.commit()

    logger.info(
        "Conversation %s renamed by user %s inside workspace %s.",
        conversation_id,
        context.user_id,
        context.workspace_id,
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
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    conversation = _get_user_conversation(
        db=db,
        workspace_id=context.workspace_id,
        conversation_id=conversation_id,
        user_id=context.user_id,
    )

    crud.delete_conversation(db, conversation=conversation)
    db.commit()

    logger.info(
        "Conversation %s deleted by user %s inside workspace %s.",
        conversation_id,
        context.user_id,
        context.workspace_id,
    )

    return Response(
        status_code=status.HTTP_204_NO_CONTENT,
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatResponse,
    summary="Send Message",
    response_description="AI-generated response with structured source citations.",
)
async def post_chat_query(
    conversation_id: uuid.UUID,
    query_in: ChatQuery,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> ChatResponse:
    try:
        _get_user_conversation(
            db=db,
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
            user_id=context.user_id,
        )

        response = await assistant_service.send_chat_message(
            db=db,
            conversation_id=conversation_id,
            user_id=context.user_id,
            query_text=query_in.content,
        )
        db.commit()

        logger.info(
            "Response generated for conversation %s in workspace %s.",
            conversation_id,
            context.workspace_id,
        )

        return response

    except ValueError as exc:
        logger.warning(
            "Assistant request rejected for conversation %s in workspace %s: %s",
            conversation_id,
            context.workspace_id,
            str(exc),
        )

        error_message = str(exc).lower()

        if (
            "conversation" in error_message
            or "document" in error_message
            or "workitem" in error_message
        ):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    except HTTPException:
        raise

    except Exception:
        logger.exception(
            "Unexpected assistant failure for conversation %s.",
            conversation_id,
        )

        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error.",
        )
