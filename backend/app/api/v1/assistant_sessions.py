"""ARCH-39 — conversation sessions API.

Mounted under /workspaces/{workspace_id}/assistant beside the ARCH-11 and
ARCH-12 routers. Paths are distinct (`/sessions`, `/models`,
`/prompt-templates`) so nothing here shadows an existing route.

The ARCH-11 `GET /conversations` stays for existing clients. It returns every
message of every conversation; `GET /sessions` is the list the console uses.
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import crud
from app.api import deps
from app.models.assistant import Conversation
from app.models.assistant_suite import SCOPE_WORKSPACE
from app.models.organization import OrganizationRole
from app.schemas.assistant_suite import (
    AssistantModelOption,
    ConversationScopeRead,
    ConversationScopeUpdate,
    ConversationSessionSummary,
    ConversationSessionUpdate,
    PromptTemplateRead,
    PromptTemplateWrite,
)
from app.services import conversation_service
from app.services.conversation_service import ConversationServiceError, SessionRow

logger = logging.getLogger("app.api.v1.assistant_sessions")

router = APIRouter(tags=["AI Assistant"])

_TEMPLATE_MANAGER_ROLES = {OrganizationRole.OWNER, OrganizationRole.ADMIN}


def _refuse(exc: ConversationServiceError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={"code": type(exc).__name__.upper(), "message": str(exc), "details": {}},
    )


def _owned(db: Session, context: deps.TenantContext, conversation_id: uuid.UUID) -> Conversation:
    conversation = conversation_service.get_owned(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        conversation_id=conversation_id,
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


def _summary(row: SessionRow) -> ConversationSessionSummary:
    conversation = row.conversation
    return ConversationSessionSummary(
        id=conversation.id,
        title=conversation.title,
        kind="document" if conversation.work_item_id else "workspace",
        scope_mode=getattr(conversation, "scope_mode", None) or SCOPE_WORKSPACE,
        work_item_id=conversation.work_item_id,
        document_title=row.document_title,
        scope_document_count=row.scope_document_count,
        model_override=conversation.model_override,
        pinned=conversation.pinned_at is not None,
        archived=conversation.archived_at is not None,
        message_count=row.message_count,
        created_at=conversation.created_at,
        last_message_at=conversation.last_message_at,
    )


def _ai_settings(db: Session, context: deps.TenantContext):
    ai_settings = crud.get_ai_settings(db=db, workspace_id=context.workspace_id)
    if ai_settings is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="AI settings have not been configured for this workspace.",
        )
    return ai_settings


# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=list[ConversationSessionSummary],
    summary="List conversation sessions",
)
def list_sessions(
    kind: Literal["all", "workspace", "document"] = Query(default="all"),
    archived: bool = Query(default=False),
    q: Optional[str] = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[ConversationSessionSummary]:
    rows = conversation_service.list_sessions(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        kind=kind,
        archived=archived,
        query=q,
        limit=limit,
        offset=offset,
    )
    return [_summary(row) for row in rows]


@router.patch(
    "/sessions/{conversation_id}",
    response_model=ConversationSessionSummary,
    summary="Rename, pin, archive, or choose a model",
)
def update_session(
    conversation_id: uuid.UUID,
    payload: ConversationSessionUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationSessionSummary:
    conversation = _owned(db, context, conversation_id)
    ai_settings = None
    if payload.model_override is not None or payload.clear_model_override:
        ai_settings = _ai_settings(db, context)
    try:
        conversation_service.update_session(
            db,
            conversation=conversation,
            ai_settings=ai_settings,
            title=payload.title,
            pinned=payload.pinned,
            archived=payload.archived,
            model_override=payload.model_override,
            clear_model_override=payload.clear_model_override,
        )
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    db.commit()

    rows = conversation_service.list_sessions(
        db,
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        kind="all",
        archived=conversation.archived_at is not None,
        limit=100,
    )
    for row in rows:
        if row.conversation.id == conversation.id:
            return _summary(row)
    return _summary(SessionRow(conversation, 0, None, 0))


@router.get(
    "/sessions/{conversation_id}/scope",
    response_model=ConversationScopeRead,
    summary="Which documents a conversation searches",
)
def get_scope(
    conversation_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationScopeRead:
    conversation = _owned(db, context, conversation_id)
    if conversation.work_item_id is not None:
        return ConversationScopeRead(
            mode="DOCUMENT",
            work_item_ids=[conversation.work_item_id],
            max_documents=conversation_service.max_scope_documents(),
        )
    return ConversationScopeRead(
        mode=conversation.scope_mode or SCOPE_WORKSPACE,
        work_item_ids=conversation_service.scope_item_ids(db, conversation=conversation),
        max_documents=conversation_service.max_scope_documents(),
    )


@router.put(
    "/sessions/{conversation_id}/scope",
    response_model=ConversationScopeRead,
    summary="Search the whole workspace or selected documents",
)
def put_scope(
    conversation_id: uuid.UUID,
    payload: ConversationScopeUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> ConversationScopeRead:
    conversation = _owned(db, context, conversation_id)
    try:
        ids = conversation_service.set_scope(
            db,
            conversation=conversation,
            mode=payload.mode,
            work_item_ids=payload.work_item_ids,
        )
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    db.commit()
    return ConversationScopeRead(
        mode=payload.mode,
        work_item_ids=ids,
        max_documents=conversation_service.max_scope_documents(),
    )


@router.get(
    "/sessions/{conversation_id}/export",
    summary="Download a conversation as JSON or Markdown",
    response_class=Response,
)
def export_session(
    conversation_id: uuid.UUID,
    format: Literal["json", "markdown"] = Query(default="markdown"),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> Response:
    conversation = _owned(db, context, conversation_id)
    body, media_type, filename = conversation_service.export_conversation(
        db, conversation=conversation, fmt=format
    )
    return Response(
        content=body.encode("utf-8"),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


@router.get(
    "/models",
    response_model=list[AssistantModelOption],
    summary="Models a conversation may use, with price-book rates",
)
def list_models(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[AssistantModelOption]:
    ai_settings = _ai_settings(db, context)
    return [
        AssistantModelOption(**row)
        for row in conversation_service.model_catalog(db, ai_settings=ai_settings)
    ]


# ---------------------------------------------------------------------------
# Prompt templates (organization-wide, reached through the workspace)
# ---------------------------------------------------------------------------


@router.get(
    "/prompt-templates",
    response_model=list[PromptTemplateRead],
    summary="Prompt templates shared across the organization",
)
def list_prompt_templates(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> list[PromptTemplateRead]:
    return [
        PromptTemplateRead.model_validate(template)
        for template in conversation_service.list_templates(
            db, organization_id=context.organization_id
        )
    ]


@router.post(
    "/prompt-templates",
    response_model=PromptTemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a prompt template",
)
def create_prompt_template(
    payload: PromptTemplateWrite,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> PromptTemplateRead:
    try:
        template = conversation_service.create_template(
            db,
            organization_id=context.organization_id,
            user_id=context.user_id,
            name=payload.name,
            body=payload.body,
        )
        db.commit()
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with that name already exists.",
        ) from exc
    db.refresh(template)
    return PromptTemplateRead.model_validate(template)


def _manageable_template(db: Session, context: deps.TenantContext, template_id: uuid.UUID):
    template = conversation_service.get_template(
        db, organization_id=context.organization_id, template_id=template_id
    )
    if template is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found.")
    if (
        template.created_by_user_id != context.user_id
        and context.organization_role not in _TEMPLATE_MANAGER_ROLES
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the author or an organization admin can change this template.",
        )
    return template


@router.put(
    "/prompt-templates/{template_id}",
    response_model=PromptTemplateRead,
    summary="Edit a prompt template",
)
def update_prompt_template(
    template_id: uuid.UUID,
    payload: PromptTemplateWrite,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> PromptTemplateRead:
    template = _manageable_template(db, context, template_id)
    try:
        conversation_service.update_template(
            db, template=template, name=payload.name, body=payload.body
        )
        db.commit()
    except ConversationServiceError as exc:
        db.rollback()
        raise _refuse(exc) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A template with that name already exists.",
        ) from exc
    db.refresh(template)
    return PromptTemplateRead.model_validate(template)


@router.delete(
    "/prompt-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Archive a prompt template",
)
def archive_prompt_template(
    template_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> Response:
    template = _manageable_template(db, context, template_id)
    conversation_service.archive_template(db, template=template)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
