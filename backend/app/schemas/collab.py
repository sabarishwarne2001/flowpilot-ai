"""ARCH48-S1:schemas — the live review wire shapes (REST). Mirrored by frontend/src/types/collab.ts
(verify_arch48 W3 compares the field lists)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.collab import vocabulary as v


class Person(BaseModel):
    user_id: str
    email: str = ""
    name: str = ""


class AnchorIn(BaseModel):
    """What the console anchors a new thread to."""

    model_config = ConfigDict(extra="forbid")

    kind: str = v.ANCHOR_ITEM
    page: Optional[int] = Field(default=None, ge=1)
    paragraph: Optional[int] = Field(default=None, ge=0)
    field: Optional[str] = Field(default=None, max_length=v.MAX_FIELD_CHARS)
    #: The paragraph digest the console read: a document that changed since is refused (409 ANCHOR_MOVED).
    digest: Optional[str] = Field(default=None, pattern=r"^[0-9a-f]{32}$")

    @field_validator("kind")
    @classmethod
    def _known(cls, value: str) -> str:
        upper = (value or "").strip().upper()
        if upper not in v.ANCHOR_KINDS:
            raise ValueError(f"anchor kind must be one of: {', '.join(v.ANCHOR_KINDS)}")
        return upper


class AnchorOut(BaseModel):
    kind: str
    page: Optional[int] = None
    paragraph: Optional[int] = None
    field: Optional[str] = None
    quote: Optional[str] = None
    digest: Optional[str] = None
    #: CURRENT, MOVED or OUTDATED: where the anchored paragraph is in the document now.
    state: str = v.ANCHOR_CURRENT
    current_page: Optional[int] = None
    current_paragraph: Optional[int] = None


class CommentOut(BaseModel):
    id: str
    author: Optional[Person] = None
    body: Optional[str] = None
    deleted: bool = False
    erased: bool = False
    mentions: list[Person] = Field(default_factory=list)
    created_at: datetime
    edited_at: Optional[datetime] = None


class ThreadOut(BaseModel):
    id: str
    kind: str
    item_id: str
    status: str
    anchor: AnchorOut
    created_by: Optional[Person] = None
    created_at: datetime
    last_activity_at: datetime
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[Person] = None
    comments: list[CommentOut] = Field(default_factory=list)


class ThreadList(BaseModel):
    kind: str
    item_id: str
    threads: list[ThreadOut]
    open_threads: int


class NewThread(BaseModel):
    model_config = ConfigDict(extra="forbid")

    anchor: AnchorIn = Field(default_factory=AnchorIn)
    body: str = Field(min_length=1, max_length=v.MAX_COMMENT_CHARS)
    mentions: list[uuid.UUID] = Field(default_factory=list, max_length=v.MAX_MENTIONS)
    client_nonce: Optional[uuid.UUID] = None


class NewComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=v.MAX_COMMENT_CHARS)
    mentions: list[uuid.UUID] = Field(default_factory=list, max_length=v.MAX_MENTIONS)
    client_nonce: Optional[uuid.UUID] = None


class EditComment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=v.MAX_COMMENT_CHARS)


class ThreadCreated(BaseModel):
    thread: ThreadOut
    created: bool


class CommentCreated(BaseModel):
    thread: ThreadOut
    comment_id: str
    created: bool


class ParagraphOut(BaseModel):
    index: int
    page: int
    text: str
    digest: str


class ParagraphList(BaseModel):
    kind: str
    item_id: str
    work_item_id: Optional[str] = None
    paragraphs: list[ParagraphOut]
    truncated: bool = False


class LockOut(BaseModel):
    kind: str
    item_id: str
    holder_user_id: str
    holder: Optional[Person] = None
    acquired_at: datetime
    expires_at: datetime


class LiveState(BaseModel):
    """What the live channel's hello carries, over REST: for a console whose socket is down."""

    locks: list[LockOut]
    presence: list[dict]
    heartbeat_seconds: int
    lock_ttl_seconds: int
    subprotocol: str
    live_path: str


class BreakLockResult(BaseModel):
    kind: str
    item_id: str
    released: bool
    holder_user_id: Optional[str] = None


__all__ = ["AnchorIn", "AnchorOut", "BreakLockResult", "CommentCreated", "CommentOut", "EditComment", "LiveState",
           "LockOut", "NewComment", "NewThread", "ParagraphList", "ParagraphOut", "Person", "ThreadCreated",
           "ThreadList", "ThreadOut"]
