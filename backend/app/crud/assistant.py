import uuid
from typing import Any
from sqlalchemy import select, delete
from sqlalchemy.orm import Session
from app.models.assistant import Conversation, ConversationMessage

def create_conversation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    title: str,
    work_item_id: uuid.UUID | None = None,
) -> Conversation:
    conversation = Conversation(
        workspace_id=workspace_id,
        user_id=user_id,
        title=title,
        work_item_id=work_item_id,
    )
    db.add(conversation)
    db.commit()
    db.refresh(conversation)
    return conversation

def get_conversation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID,
) -> Conversation | None:
    statement = select(Conversation).where(
        Conversation.workspace_id == workspace_id,
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
    )
    return db.execute(statement).scalar_one_or_none()

def get_document_conversation(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    work_item_id: uuid.UUID,
) -> Conversation | None:
    statement = (
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
            Conversation.work_item_id == work_item_id,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    return db.execute(statement).scalar_one_or_none()

def list_conversations(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    skip: int = 0,
    limit: int = 100,
) -> list[Conversation]:
    statement = (
        select(Conversation)
        .where(
            Conversation.workspace_id == workspace_id,
            Conversation.user_id == user_id,
        )
        .order_by(Conversation.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(db.execute(statement).scalars().all())

def update_conversation_title(
    db: Session,
    *,
    conversation: Conversation,
    title: str,
) -> Conversation:
    conversation.title = title
    db.commit()
    db.refresh(conversation)
    return conversation

def delete_conversation(
    db: Session,
    *,
    conversation: Conversation,
) -> None:
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
    statement = (
        select(ConversationMessage)
        .where(ConversationMessage.conversation_id == conversation_id)
        .order_by(ConversationMessage.created_at.asc())
    )
    if limit is not None:
        statement = statement.limit(limit)
    return list(db.execute(statement).scalars().all())

def delete_conversation_messages(
    db: Session,
    *,
    conversation_id: uuid.UUID,
) -> None:
    statement = delete(ConversationMessage).where(
        ConversationMessage.conversation_id == conversation_id
    )
    db.execute(statement)
    db.commit()