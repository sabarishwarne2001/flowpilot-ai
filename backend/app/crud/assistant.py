"""
Database CRUD (Create, Read, Update, Delete) repository layer for Conversation Memory.

Handles direct relational queries and transactions targeting conversations and
conversation messages. This module contains database access only and does not
implement business logic.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.assistant import Conversation, ConversationMessage


def create_conversation(
    db: Session,
    *,
    user_id: uuid.UUID,
    title: str,
    work_item_id: uuid.UUID | None = None,
) -> Conversation:
    """
    Create and persist a new conversation.
    """

    conversation = Conversation(
        user_id=user_id,
        title=title,
        work_item_id=work_item_id,
    )

    db.add(conversation)
    db.commit()
    db.refresh(conversation)

    return conversation


def get_conversation_by_id(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation | None:
    """
    Retrieve a conversation owned by the specified user.
    """

    statement = (
        select(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
        )
    )

    return db.execute(statement).scalar_one_or_none()


def get_document_conversation(
    db: Session,
    *,
    user_id: uuid.UUID,
    work_item_id: uuid.UUID,
) -> Conversation | None:
    """
    Return the existing conversation associated with
    a document for the specified user.

    Used by the Document Assistant so reopening
    a document continues the previous conversation.
    """

    statement = (
        select(Conversation)
        .where(
            Conversation.user_id == user_id,
            Conversation.work_item_id == work_item_id,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )

    return db.execute(statement).scalar_one_or_none()


def get_user_conversations(
    db: Session,
    *,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[Conversation]:
    """
    Retrieve conversations belonging to a user ordered by newest first.
    """

    statement = (
        select(Conversation)
        .where(Conversation.user_id == user_id)
        .order_by(Conversation.created_at.desc())
        .offset(skip)
        .limit(limit)
    )

    return list(db.execute(statement).scalars())


def update_conversation_title(
    db: Session,
    *,
    conversation: Conversation,
    title: str,
) -> Conversation:
    """
    Update a conversation title.
    """

    conversation.title = title

    db.commit()
    db.refresh(conversation)

    return conversation


def delete_conversation(
    db: Session,
    *,
    conversation: Conversation,
) -> None:
    """
    Delete a conversation.

    Child messages are removed automatically through cascade rules.
    """

    db.delete(conversation)
    db.commit()


def create_conversation_message(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    role: str,
    content: str,
    sources: list[dict[str, Any]] | None = None,
    token_usage: dict[str, Any] | None = None,
) -> ConversationMessage:
    """
    Create and persist a conversation message.
    """

    message = ConversationMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        sources=sources,
        token_usage=token_usage,
    )

    db.add(message)
    db.commit()
    db.refresh(message)

    return message


def get_conversation_messages(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    limit: int | None = None,
) -> list[ConversationMessage]:
    """
    Retrieve conversation messages in chronological order.
    """

    statement = (
        select(ConversationMessage)
        .where(
            ConversationMessage.conversation_id == conversation_id
        )
        .order_by(ConversationMessage.created_at.asc())
    )

    if limit is not None:
        statement = statement.limit(limit)

    return list(db.execute(statement).scalars())


def delete_conversation_messages(
    db: Session,
    *,
    conversation_id: uuid.UUID,
) -> None:
    """
    Delete every message belonging to a conversation.
    """

    statement = delete(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id
    )

    db.execute(statement)
    db.commit()