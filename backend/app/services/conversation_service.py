"""ARCH-39 — conversation sessions: list, organise, scope, export, templates.

OWNERSHIP
=========

Every read and write here is keyed on (workspace_id, user_id). A conversation
is private to the person who started it, which is what `crud.get_conversation`
has always enforced; nothing in ARCH-39 widens that.

SCOPE
=====

  WORKSPACE  search every document in the workspace (the default)
  SELECTED   search only the documents in `conversation_scope_items`
  DOCUMENT   search only `conversations.work_item_id` (document chat)

A conversation's DOCUMENT-ness is fixed at creation: the database CHECK ties
`scope_mode = 'DOCUMENT'` to `work_item_id IS NOT NULL`, so a document chat
cannot be widened into a workspace chat by an API call, and the reverse.

MODEL OVERRIDE
==============

Limited to models the registry lists for the workspace's configured provider.
Changing provider would change whose credentials serve the request; that is
ARCH-22 routing's decision, not a chat preference.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.assistant import Conversation, ConversationMessage
from app.models.assistant_suite import (
    SCOPE_DOCUMENT,
    SCOPE_SELECTED,
    SCOPE_WORKSPACE,
    ConversationScopeItem,
    PromptTemplate,
)
from app.models.work_item import WorkItem

logger = logging.getLogger("app.services.conversation_service")

KIND_ALL = "all"
KIND_WORKSPACE = "workspace"
KIND_DOCUMENT = "document"
KINDS: tuple[str, ...] = (KIND_ALL, KIND_WORKSPACE, KIND_DOCUMENT)

EXPORT_FORMATS: tuple[str, ...] = ("json", "markdown")


class ConversationServiceError(ValueError):
    """A request this service refuses. Mapped to 422 by the router."""


class ScopeError(ConversationServiceError):
    pass


class ModelOverrideError(ConversationServiceError):
    pass


class PromptTemplateError(ConversationServiceError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def max_scope_documents() -> int:
    return int(getattr(settings, "ASSISTANT_SCOPE_MAX_DOCUMENTS", 20))


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionRow:
    conversation: Conversation
    message_count: int
    document_title: Optional[str]
    scope_document_count: int


def list_sessions(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    kind: str = KIND_ALL,
    archived: bool = False,
    query: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> list[SessionRow]:
    if kind not in KINDS:
        raise ConversationServiceError(f"kind must be one of {', '.join(KINDS)}.")

    message_count = (
        select(func.count(ConversationMessage.id))
        .where(ConversationMessage.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )
    scope_count = (
        select(func.count(ConversationScopeItem.work_item_id))
        .where(ConversationScopeItem.conversation_id == Conversation.id)
        .correlate(Conversation)
        .scalar_subquery()
    )

    statement = (
        select(Conversation, message_count, WorkItem.original_filename, scope_count)
        .outerjoin(WorkItem, WorkItem.id == Conversation.work_item_id)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )
    )

    if archived:
        statement = statement.where(Conversation.archived_at.is_not(None))
    else:
        statement = statement.where(Conversation.archived_at.is_(None))

    if kind == KIND_WORKSPACE:
        statement = statement.where(Conversation.work_item_id.is_(None))
    elif kind == KIND_DOCUMENT:
        statement = statement.where(Conversation.work_item_id.is_not(None))

    needle = (query or "").strip()
    if needle:
        pattern = f"%{_escape_like(needle[:100])}%"
        matching_message = (
            select(ConversationMessage.id)
            .where(
                ConversationMessage.conversation_id == Conversation.id,
                ConversationMessage.content.ilike(pattern, escape="\\"),
            )
            .correlate(Conversation)
            .exists()
        )
        statement = statement.where(
            or_(
                Conversation.title.ilike(pattern, escape="\\"),
                WorkItem.original_filename.ilike(pattern, escape="\\"),
                matching_message,
            )
        )

    statement = (
        statement.order_by(
            Conversation.pinned_at.desc().nulls_last(),
            func.coalesce(Conversation.last_message_at, Conversation.created_at).desc(),
        )
        .offset(max(0, int(offset)))
        .limit(max(1, min(int(limit), 100)))
    )

    return [
        SessionRow(
            conversation=row[0],
            message_count=int(row[1] or 0),
            document_title=row[2],
            scope_document_count=int(row[3] or 0),
        )
        for row in db.execute(statement).all()
    ]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def get_owned(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    conversation_id: uuid.UUID,
) -> Optional[Conversation]:
    return db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )
    ).scalar_one_or_none()


# ---------------------------------------------------------------------------
# Organising
# ---------------------------------------------------------------------------


def update_session(
    db: Session,
    *,
    conversation: Conversation,
    ai_settings: Any,
    title: Optional[str] = None,
    pinned: Optional[bool] = None,
    archived: Optional[bool] = None,
    model_override: Optional[str] = None,
    clear_model_override: bool = False,
) -> Conversation:
    if title is not None:
        cleaned = " ".join(title.split())
        limit = int(getattr(settings, "MAX_CONVERSATION_TITLE_LENGTH", 255))
        if not cleaned:
            raise ConversationServiceError("A title cannot be empty.")
        conversation.title = cleaned[:limit]

    if pinned is not None:
        conversation.pinned_at = _now() if pinned else None

    if archived is not None:
        conversation.archived_at = _now() if archived else None
        if archived:
            # An archived conversation stays out of the pinned group.
            conversation.pinned_at = None

    if clear_model_override:
        conversation.model_override = None
    elif model_override is not None:
        allowed = allowed_models(ai_settings)
        if model_override not in allowed:
            raise ModelOverrideError(
                f"{model_override!r} is not available for this workspace's "
                f"provider. Choose one of: {', '.join(allowed) or 'none'}."
            )
        base_model = str(getattr(ai_settings, "model", "") or "")
        conversation.model_override = None if model_override == base_model else model_override

    db.flush([conversation])
    return conversation


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def set_scope(
    db: Session,
    *,
    conversation: Conversation,
    mode: str,
    work_item_ids: Sequence[uuid.UUID] = (),
) -> list[uuid.UUID]:
    if conversation.work_item_id is not None:
        raise ScopeError(
            "A document conversation always searches its own document; its "
            "scope cannot be changed."
        )
    if mode not in (SCOPE_WORKSPACE, SCOPE_SELECTED):
        raise ScopeError("Scope must be WORKSPACE or SELECTED.")

    unique_ids = list(dict.fromkeys(work_item_ids))

    if mode == SCOPE_WORKSPACE:
        db.execute(
            delete(ConversationScopeItem).where(
                ConversationScopeItem.conversation_id == conversation.id
            )
        )
        conversation.scope_mode = SCOPE_WORKSPACE
        db.flush([conversation])
        return []

    if not unique_ids:
        raise ScopeError("Select at least one document, or search the whole workspace.")
    if len(unique_ids) > max_scope_documents():
        raise ScopeError(
            f"A conversation can be limited to at most {max_scope_documents()} documents."
        )

    found = set(
        db.execute(
            select(WorkItem.id).where(
                WorkItem.workspace_id == conversation.workspace_id,
                WorkItem.id.in_(unique_ids),
            )
        ).scalars()
    )
    missing = [str(item) for item in unique_ids if item not in found]
    if missing:
        raise ScopeError(f"Document(s) not found in this workspace: {', '.join(missing)}.")

    db.execute(
        delete(ConversationScopeItem).where(
            ConversationScopeItem.conversation_id == conversation.id
        )
    )
    for work_item_id in unique_ids:
        db.add(
            ConversationScopeItem(
                conversation_id=conversation.id,
                work_item_id=work_item_id,
                workspace_id=conversation.workspace_id,
            )
        )
    conversation.scope_mode = SCOPE_SELECTED
    db.flush()
    return unique_ids


def scope_item_ids(db: Session, *, conversation: Conversation) -> list[uuid.UUID]:
    return list(
        db.execute(
            select(ConversationScopeItem.work_item_id)
            .where(ConversationScopeItem.conversation_id == conversation.id)
            .order_by(ConversationScopeItem.created_at)
        ).scalars()
    )


def retrieval_work_item_ids(db: Session, *, conversation: Conversation) -> Optional[list[str]]:
    """What retrieval may search. None means the whole workspace.

    A SELECTED conversation whose documents have all been deleted returns an
    empty list, which retrieval treats as "search nothing" — never as the
    whole workspace.
    """
    if conversation.work_item_id is not None:
        return [str(conversation.work_item_id)]
    if getattr(conversation, "scope_mode", SCOPE_WORKSPACE) == SCOPE_SELECTED:
        return [str(item) for item in scope_item_ids(db, conversation=conversation)]
    return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


def _provider_key(ai_settings: Any) -> Any:
    return getattr(ai_settings, "provider", None)


def allowed_models(ai_settings: Any) -> list[str]:
    from app.core.ai_models import AI_MODELS

    provider = _provider_key(ai_settings)
    if provider is None:
        return []
    for key, models in AI_MODELS.items():
        if str(getattr(key, "value", key)).upper() == str(getattr(provider, "value", provider)).upper():
            return list(models)
    return []


def apply_model_override(ai_settings: Any, conversation: Conversation) -> Any:
    """AI settings with the conversation's model, never mutating the row."""
    override = getattr(conversation, "model_override", None)
    if not override or ai_settings is None:
        return ai_settings
    if override not in allowed_models(ai_settings):
        logger.warning(
            "assistant.model_override_ignored",
            extra={"conversation_id": str(conversation.id), "model": override},
        )
        return ai_settings
    from app.services.llm_service import _RoutedAISettings

    provider = str(getattr(_provider_key(ai_settings), "value", _provider_key(ai_settings)))
    return _RoutedAISettings(ai_settings, provider.upper(), override)


def model_catalog(db: Session, *, ai_settings: Any) -> list[dict[str, Any]]:
    from app.services import pricing_service
    from app.services.llm_metering import INPUT_EVENT, OUTPUT_EVENT

    provider = str(getattr(_provider_key(ai_settings), "value", "") or "")
    configured = str(getattr(ai_settings, "model", "") or "")
    rows: list[dict[str, Any]] = []
    for model in allowed_models(ai_settings):
        entry: dict[str, Any] = {
            "provider": provider,
            "model": model,
            "is_workspace_default": model == configured,
            "input_micros_per_million": None,
            "output_micros_per_million": None,
            "currency": None,
        }
        for event, field in ((INPUT_EVENT, "input"), (OUTPUT_EVENT, "output")):
            try:
                price = pricing_service.resolve(
                    db, event_type=event, provider=provider.lower(), model=model
                )
            except Exception:  # noqa: BLE001 — an unpriced model is listed, not hidden
                continue
            entry[f"{field}_micros_per_million"] = int(price.cost_micros(1_000_000))
            entry["currency"] = price.currency
        rows.append(entry)
    return rows


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------


def _messages(db: Session, conversation: Conversation) -> list[ConversationMessage]:
    return list(
        db.execute(
            select(ConversationMessage)
            .where(ConversationMessage.conversation_id == conversation.id)
            .order_by(ConversationMessage.created_at, ConversationMessage.id)
        ).scalars()
    )


def _source_label(source: Any) -> str:
    if not isinstance(source, dict):
        return ""
    name = source.get("original_filename") or source.get("filename") or "Document"
    page = source.get("page_number")
    return f"{name}, p. {page}" if page else str(name)


def export_conversation(
    db: Session, *, conversation: Conversation, fmt: str
) -> tuple[str, str, str]:
    """(body, media type, filename)."""
    if fmt not in EXPORT_FORMATS:
        raise ConversationServiceError("format must be json or markdown.")

    messages = _messages(db, conversation)
    stamp = _now().strftime("%Y%m%d-%H%M")
    safe_title = "".join(ch if ch.isalnum() else "-" for ch in conversation.title)[:40].strip("-")
    base_name = f"{safe_title or 'conversation'}-{stamp}"

    if fmt == "json":
        payload = {
            "id": str(conversation.id),
            "title": conversation.title,
            "scope_mode": getattr(conversation, "scope_mode", SCOPE_WORKSPACE),
            "work_item_id": str(conversation.work_item_id) if conversation.work_item_id else None,
            "created_at": conversation.created_at.isoformat() if conversation.created_at else None,
            "exported_at": _now().isoformat(),
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "created_at": message.created_at.isoformat() if message.created_at else None,
                    "sources": message.sources or [],
                    "token_usage": message.token_usage,
                }
                for message in messages
            ],
        }
        return json.dumps(payload, indent=2, ensure_ascii=False), "application/json", f"{base_name}.json"

    lines = [f"# {conversation.title}", ""]
    lines.append(f"_Exported {_now().strftime('%Y-%m-%d %H:%M UTC')}_")
    lines.append("")
    for message in messages:
        speaker = "You" if message.role == "user" else "Assistant"
        lines.append(f"### {speaker}")
        lines.append("")
        lines.append((message.content or "").rstrip())
        sources = [label for label in (_source_label(s) for s in (message.sources or [])) if label]
        if sources:
            lines.append("")
            lines.append("Sources: " + "; ".join(dict.fromkeys(sources)))
        lines.append("")
    return "\n".join(lines), "text/markdown; charset=utf-8", f"{base_name}.md"


# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------


def list_templates(db: Session, *, organization_id: uuid.UUID) -> list[PromptTemplate]:
    return list(
        db.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.organization_id == organization_id,
                PromptTemplate.archived_at.is_(None),
            )
            .order_by(func.lower(PromptTemplate.name))
        ).scalars()
    )


def _clean_template(name: str, body: str) -> tuple[str, str]:
    from app.models.assistant_suite import PROMPT_TEMPLATE_BODY_MAX, PROMPT_TEMPLATE_NAME_MAX

    cleaned_name = " ".join((name or "").split())
    cleaned_body = (body or "").strip()
    if not 1 <= len(cleaned_name) <= PROMPT_TEMPLATE_NAME_MAX:
        raise PromptTemplateError(f"A name must be 1–{PROMPT_TEMPLATE_NAME_MAX} characters.")
    if not 1 <= len(cleaned_body) <= PROMPT_TEMPLATE_BODY_MAX:
        raise PromptTemplateError(f"A prompt must be 1–{PROMPT_TEMPLATE_BODY_MAX} characters.")
    return cleaned_name, cleaned_body


def _name_taken(
    db: Session, *, organization_id: uuid.UUID, name: str, exclude: Optional[uuid.UUID]
) -> bool:
    statement = select(PromptTemplate.id).where(
        PromptTemplate.organization_id == organization_id,
        PromptTemplate.archived_at.is_(None),
        func.lower(PromptTemplate.name) == name.lower(),
    )
    if exclude is not None:
        statement = statement.where(PromptTemplate.id != exclude)
    return db.execute(statement).first() is not None


def create_template(
    db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID, name: str, body: str
) -> PromptTemplate:
    cleaned_name, cleaned_body = _clean_template(name, body)
    if _name_taken(db, organization_id=organization_id, name=cleaned_name, exclude=None):
        raise PromptTemplateError(f"A template named {cleaned_name!r} already exists.")
    template = PromptTemplate(
        organization_id=organization_id,
        name=cleaned_name,
        body=cleaned_body,
        created_by_user_id=user_id,
    )
    db.add(template)
    db.flush([template])
    return template


def get_template(
    db: Session, *, organization_id: uuid.UUID, template_id: uuid.UUID
) -> Optional[PromptTemplate]:
    return db.execute(
        select(PromptTemplate).where(
            PromptTemplate.id == template_id,
            PromptTemplate.organization_id == organization_id,
            PromptTemplate.archived_at.is_(None),
        )
    ).scalar_one_or_none()


def update_template(
    db: Session, *, template: PromptTemplate, name: str, body: str
) -> PromptTemplate:
    cleaned_name, cleaned_body = _clean_template(name, body)
    if _name_taken(
        db, organization_id=template.organization_id, name=cleaned_name, exclude=template.id
    ):
        raise PromptTemplateError(f"A template named {cleaned_name!r} already exists.")
    template.name = cleaned_name
    template.body = cleaned_body
    db.flush([template])
    return template


def archive_template(db: Session, *, template: PromptTemplate) -> None:
    template.archived_at = _now()
    db.flush([template])


__all__ = [
    "ConversationServiceError",
    "EXPORT_FORMATS",
    "KINDS",
    "ModelOverrideError",
    "PromptTemplateError",
    "ScopeError",
    "SessionRow",
    "allowed_models",
    "apply_model_override",
    "archive_template",
    "create_template",
    "export_conversation",
    "get_owned",
    "get_template",
    "list_sessions",
    "list_templates",
    "model_catalog",
    "retrieval_work_item_ids",
    "scope_item_ids",
    "set_scope",
    "update_session",
    "update_template",
]
