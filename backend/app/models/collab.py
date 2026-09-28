"""ARCH48-S1:models — soft review locks, resolution versions, discussion threads and comments.

The schema (every CHECK, the composite FKs, the lease bound) lives in
alembic/versions/arch48_step1_collaborative_review.py; these mappings are the
ORM view, with column types matching it exactly (verify_arch48 D2 compares).

`item_id` has no foreign key anywhere here, for the reason `models/review.py`
gives for assignments: a review item is polymorphic across nine source tables.
Every write resolves the item through `review.projection.load_item`, which is
workspace-scoped, before touching these tables.

Holders and authors reference `users`, not `workspace_members`: an
organization owner or admin reviews a workspace without a membership row
(`workspace_member_service.resolve_workspace_access`), and a composite FK onto
`workspace_members` would refuse exactly the people who most often break ties.
Access is re-checked on every request and every live connection instead.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import ARRAY, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _uuid(*args: Any, **kw: Any) -> Any:
    return mapped_column(PgUUID(as_uuid=True), *args, **kw)


def _ts(nullable: bool = False, server_now: bool = True) -> Any:
    if nullable:
        return mapped_column(DateTime(timezone=True), nullable=True)
    if server_now:
        return mapped_column(DateTime(timezone=True), nullable=False, server_default=sa_text("now()"))
    return mapped_column(DateTime(timezone=True), nullable=False)


def _user_fk(ondelete: str = "SET NULL", nullable: bool = True) -> Any:
    return _uuid(ForeignKey("users.id", ondelete=ondelete), nullable=nullable)


class ReviewItemVersion(Base):
    """The optimistic-concurrency version of one review item (absent = 0)."""

    __tablename__ = "review_item_versions"

    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    item_id: Mapped[uuid.UUID] = _uuid(primary_key=True)
    workspace_id: Mapped[uuid.UUID] = _uuid(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=sa_text("0"))
    updated_at: Mapped[datetime] = _ts()
    updated_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()


class ReviewLock(Base):
    """A soft lock: one live holder per item, a lease token and an expiry."""

    __tablename__ = "review_locks"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = _uuid(ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    holder_user_id: Mapped[uuid.UUID] = _user_fk(ondelete="CASCADE", nullable=False)
    lease_token: Mapped[uuid.UUID] = _uuid(nullable=False)
    acquired_at: Mapped[datetime] = _ts(server_now=False)
    heartbeat_at: Mapped[datetime] = _ts(server_now=False)
    expires_at: Mapped[datetime] = _ts(server_now=False)


class ReviewThread(Base):
    """A discussion on a review item, anchored to the item, a field or a paragraph."""

    __tablename__ = "review_threads"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    work_item_id: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    anchor_kind: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'ITEM'"))
    anchor_page: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anchor_paragraph: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    anchor_field: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    anchor_quote: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    anchor_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default=sa_text("'OPEN'"))
    created_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    created_at: Mapped[datetime] = _ts()
    last_activity_at: Mapped[datetime] = _ts()
    resolved_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    resolved_by_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()


class ReviewComment(Base):
    """One message in a thread. A deleted or erased comment keeps its row with a NULL body."""

    __tablename__ = "review_comments"

    id: Mapped[uuid.UUID] = _uuid(primary_key=True, default=uuid.uuid4)
    thread_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    workspace_id: Mapped[uuid.UUID] = _uuid(nullable=False)
    author_user_id: Mapped[Optional[uuid.UUID]] = _user_fk()
    body: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mentions: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(PgUUID(as_uuid=True)), nullable=False, default=list)
    client_nonce: Mapped[Optional[uuid.UUID]] = _uuid(nullable=True)
    created_at: Mapped[datetime] = _ts()
    edited_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    deleted_at: Mapped[Optional[datetime]] = _ts(nullable=True)
    erased_at: Mapped[Optional[datetime]] = _ts(nullable=True)


__all__ = ["ReviewComment", "ReviewItemVersion", "ReviewLock", "ReviewThread"]
