"""ARCH48-S1:threads — discussion threads and comments on review items.

Every function takes an item already loaded through `review.projection.load_item`
(workspace-scoped: another workspace's item does not exist here) and never
commits: the route owns the transaction, and the live event is queued to go out
after that commit (`events.thread_changed`).

WHO MAY DO WHAT
===============

  start a thread, comment, resolve or reopen a thread   anyone who can review (CONTRIBUTOR)
  edit a comment                                        its author
  delete a comment                                      its author, or a workspace ADMIN
                                                        (moderation: audited)

A deleted comment keeps its row and its place in the thread with a NULL body
(the CHECK makes a body and a deletion mutually exclusive), so a reply is never
left answering nothing without saying so.

MENTIONS
========

Mentions are user ids the console picked from the workspace's reviewers. Each
is re-checked against workspace access when the comment is written; a person
who cannot open the workspace is refused rather than silently dropped, because
a mention that notifies nobody is a promise the product did not keep. A
mentioned person gets an in-app notification (the ARCH-06 notification feed)
naming the item -- never the comment's text, which stays behind the review
hub's authorisation.

IDEMPOTENCE
===========

A client nonce (a UUID the console makes per submission) is UNIQUE per thread
and author: a double click or a retried request after a lost response writes
one comment, and the retry is answered with the comment already written.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.services.collab import anchors as A
from app.services.collab import events
from app.services.collab import service
from app.services.collab import vocabulary as v


class ThreadError(ValueError):
    """A request the thread rules refuse (a 400/403/404/409 at the route)."""

    def __init__(self, message: str, *, status: int = 400, code: str = "REFUSED") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Anchor:
    kind: str = v.ANCHOR_ITEM
    page: Optional[int] = None
    paragraph: Optional[int] = None
    field: Optional[str] = None
    digest: Optional[str] = None


def _clean_body(body: str) -> str:
    text_ = (body or "").replace("\r\n", "\n").strip()
    if not text_:
        raise ThreadError("A comment cannot be empty.")
    if len(text_) > v.MAX_COMMENT_CHARS:
        raise ThreadError(f"A comment is at most {v.MAX_COMMENT_CHARS} characters.")
    return text_


def document_paragraphs(db: Session, *, workspace_id: uuid.UUID, work_item_id: Optional[uuid.UUID]) -> list[A.Paragraph]:
    """The paragraphs of the item's document, from what the pipeline stored."""
    if work_item_id is None:
        return []
    from app.models.work_item import WorkItem

    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)
                      ).scalar_one_or_none()
    if item is None:
        return []
    meta = item.extraction_metadata if isinstance(item.extraction_metadata, dict) else {}
    return A.paragraphs(meta.get("pages") or [], fallback_text=item.extracted_text or "")


def _resolve_anchor(db: Session, *, item: Any, anchor: Anchor) -> dict:
    """The stored anchor columns, validated against the document as it is now."""
    if anchor.kind == v.ANCHOR_ITEM:
        return {"anchor_kind": v.ANCHOR_ITEM}
    if anchor.kind == v.ANCHOR_FIELD:
        name = (anchor.field or "").strip()
        if not name or len(name) > v.MAX_FIELD_CHARS:
            raise ThreadError(f"A field anchor needs the field's path (at most {v.MAX_FIELD_CHARS} characters).")
        return {"anchor_kind": v.ANCHOR_FIELD, "anchor_field": name}
    if anchor.kind != v.ANCHOR_PARAGRAPH:
        raise ThreadError(f"'{anchor.kind}' is not an anchor kind. Expected one of: {', '.join(v.ANCHOR_KINDS)}.")
    if item.work_item_id is None:
        raise ThreadError("This item has no document, so a thread cannot be anchored to a paragraph of it.")
    if anchor.paragraph is None or anchor.page is None:
        raise ThreadError("A paragraph anchor needs the page and the paragraph index.")
    found = document_paragraphs(db, workspace_id=item.workspace_id, work_item_id=item.work_item_id)
    index = int(anchor.paragraph)
    if not (0 <= index < len(found)) or found[index].page != int(anchor.page):
        raise ThreadError("That paragraph is not in the document as it is now. Reload the paragraphs and try again.",
                          status=409, code="ANCHOR_MOVED")
    paragraph = found[index]
    if anchor.digest and anchor.digest != paragraph.digest:
        raise ThreadError("The document changed since the paragraphs were loaded. Reload them and try again.",
                          status=409, code="ANCHOR_MOVED")
    return {"anchor_kind": v.ANCHOR_PARAGRAPH, "anchor_page": paragraph.page, "anchor_paragraph": paragraph.index,
            "anchor_digest": paragraph.digest, "anchor_quote": A.quote(paragraph.text)}


def _mentions(db: Session, *, workspace_id: uuid.UUID, mentions: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """The mentioned people, each of whom can open the workspace (else refused)."""
    from app.services import workspace_member_service, workspace_service

    unique = list(dict.fromkeys(mentions or ()))
    if len(unique) > v.MAX_MENTIONS:
        raise ThreadError(f"A comment mentions at most {v.MAX_MENTIONS} people.")
    if not unique:
        return []
    workspace = workspace_service.get_workspace_or_raise(db, workspace_id=workspace_id)
    for user_id in unique:
        access = workspace_member_service.resolve_workspace_access(db, workspace=workspace, user_id=user_id)
        if not access.has_access:
            raise ThreadError("A mentioned person cannot open this workspace.", code="MENTION_REFUSED")
    return unique


def _notify(db: Session, *, item: Any, thread_id: uuid.UUID, author_user_id: uuid.UUID,
            mentions: Sequence[uuid.UUID]) -> int:
    """ARCH48-S1:mention-notification. One in-app notification per mentioned person, in the comment's own
    transaction (`crud.create_notification` commits, so the row is built here): a comment that rolls back
    notifies nobody. The notification names the item, never the comment's text."""
    from app.models.notification import (Notification, NotificationChannel, NotificationPriority,
                                         NotificationStatus, NotificationType)

    recipients = [m for m in mentions if m != author_user_id]
    headline = (item.headline or "a review item").strip()
    for user_id in recipients:
        db.add(Notification(
            workspace_id=item.workspace_id, user_id=user_id, work_item_id=item.work_item_id,
            title="You were mentioned in a review discussion"[:150],
            message=f"On: {headline[:300]}",
            notification_type=NotificationType.DOCUMENT if item.work_item_id else NotificationType.SYSTEM,
            priority=NotificationPriority.INFO, delivery_channel=NotificationChannel.IN_APP,
            delivery_status=NotificationStatus.SENT, retry_count=0, failure_reason=None, is_read=False))
    if recipients:
        db.flush()
    _ = thread_id
    return len(recipients)


def open_count(db: Session, *, kind: str, item_id: uuid.UUID) -> int:
    from app.models.collab import ReviewThread

    return int(db.execute(select(func.count()).select_from(ReviewThread).where(
        ReviewThread.kind == kind, ReviewThread.item_id == item_id, ReviewThread.status == v.THREAD_OPEN)).scalar_one())


def _add_comment(db: Session, *, thread: Any, author_user_id: uuid.UUID, body: str,
                 mentions: Sequence[uuid.UUID], client_nonce: Optional[uuid.UUID]) -> tuple[Any, bool]:
    """(the comment, created) -- idempotent on the client nonce."""
    from app.models.collab import ReviewComment

    moment = service.now()
    comment_id = uuid.uuid4()
    row = db.execute(
        text(
            "INSERT INTO review_comments (id, thread_id, workspace_id, author_user_id, body, mentions, client_nonce, "
            "created_at) VALUES (:id, :t, :w, :a, :b, CAST(:m AS uuid[]), :n, :now) "
            "ON CONFLICT DO NOTHING RETURNING id"
        ),
        {"id": comment_id, "t": thread.id, "w": thread.workspace_id, "a": author_user_id, "b": body,
         "m": [str(m) for m in mentions], "n": client_nonce, "now": moment},
    ).first()
    if row is None:
        existing = db.execute(select(ReviewComment).where(
            ReviewComment.thread_id == thread.id, ReviewComment.author_user_id == author_user_id,
            ReviewComment.client_nonce == client_nonce)).scalar_one()
        return existing, False
    thread.last_activity_at = moment
    db.flush()
    return db.get(ReviewComment, comment_id), True


def start_thread(db: Session, *, item: Any, author_user_id: uuid.UUID, anchor: Anchor, body: str,
                 mentions: Sequence[uuid.UUID] = (), client_nonce: Optional[uuid.UUID] = None) -> tuple[Any, Any, bool]:
    """(thread, first comment, created). A retried request with the same nonce returns the first answer."""
    from app.models.collab import ReviewComment, ReviewThread

    cleaned = _clean_body(body)
    if client_nonce is not None:
        earlier = db.execute(
            select(ReviewComment, ReviewThread).join(ReviewThread, ReviewThread.id == ReviewComment.thread_id)
            .where(ReviewThread.kind == item.kind, ReviewThread.item_id == item.item_id,
                   ReviewComment.author_user_id == author_user_id, ReviewComment.client_nonce == client_nonce)
        ).first()
        if earlier is not None:
            return earlier[1], earlier[0], False
    existing = db.execute(select(func.count()).select_from(ReviewThread).where(
        ReviewThread.kind == item.kind, ReviewThread.item_id == item.item_id)).scalar_one()
    if int(existing) >= v.MAX_THREADS_PER_ITEM:
        raise ThreadError(f"An item holds at most {v.MAX_THREADS_PER_ITEM} threads.", status=409, code="TOO_MANY")
    columns = _resolve_anchor(db, item=item, anchor=anchor)
    people = _mentions(db, workspace_id=item.workspace_id, mentions=mentions)
    moment = service.now()
    thread = ReviewThread(id=uuid.uuid4(), organization_id=item.organization_id, workspace_id=item.workspace_id,
                          kind=item.kind, item_id=item.item_id, work_item_id=item.work_item_id,
                          created_by_user_id=author_user_id, created_at=moment, last_activity_at=moment,
                          status=v.THREAD_OPEN, **columns)
    db.add(thread)
    db.flush()
    comment, _ = _add_comment(db, thread=thread, author_user_id=author_user_id, body=cleaned, mentions=people,
                              client_nonce=client_nonce)
    _notify(db, item=item, thread_id=thread.id, author_user_id=author_user_id, mentions=people)
    events.thread_changed(db, workspace_id=item.workspace_id, kind=item.kind, item_id=item.item_id,
                          thread_id=thread.id, action=v.THREAD_CREATED,
                          open_threads=open_count(db, kind=item.kind, item_id=item.item_id))
    return thread, comment, True


def load_thread(db: Session, *, workspace_id: uuid.UUID, thread_id: uuid.UUID) -> Any:
    from app.models.collab import ReviewThread

    thread = db.execute(select(ReviewThread).where(ReviewThread.id == thread_id,
                                                   ReviewThread.workspace_id == workspace_id)).scalar_one_or_none()
    if thread is None:
        raise ThreadError("No such thread in this workspace.", status=404, code="NOT_FOUND")
    return thread


def reply(db: Session, *, thread: Any, item: Any, author_user_id: uuid.UUID, body: str,
          mentions: Sequence[uuid.UUID] = (), client_nonce: Optional[uuid.UUID] = None) -> tuple[Any, bool]:
    cleaned = _clean_body(body)
    people = _mentions(db, workspace_id=thread.workspace_id, mentions=mentions)
    comment, created = _add_comment(db, thread=thread, author_user_id=author_user_id, body=cleaned, mentions=people,
                                    client_nonce=client_nonce)
    if created:
        _notify(db, item=item, thread_id=thread.id, author_user_id=author_user_id, mentions=people)
        events.thread_changed(db, workspace_id=thread.workspace_id, kind=thread.kind, item_id=thread.item_id,
                              thread_id=thread.id, action=v.THREAD_COMMENTED,
                              open_threads=open_count(db, kind=thread.kind, item_id=thread.item_id))
    return comment, created


def set_status(db: Session, *, thread: Any, actor_user_id: uuid.UUID, resolved: bool) -> Any:
    if resolved and thread.status == v.THREAD_RESOLVED:
        raise ThreadError("This thread is already resolved.", status=409, code="ALREADY_RESOLVED")
    if not resolved and thread.status == v.THREAD_OPEN:
        raise ThreadError("This thread is already open.", status=409, code="ALREADY_OPEN")
    moment = service.now()
    thread.status = v.THREAD_RESOLVED if resolved else v.THREAD_OPEN
    thread.resolved_at = moment if resolved else None
    thread.resolved_by_user_id = actor_user_id if resolved else None
    thread.last_activity_at = moment
    db.flush()
    events.thread_changed(db, workspace_id=thread.workspace_id, kind=thread.kind, item_id=thread.item_id,
                          thread_id=thread.id, action=v.THREAD_RESOLVED_ACTION if resolved else v.THREAD_REOPENED,
                          open_threads=open_count(db, kind=thread.kind, item_id=thread.item_id))
    return thread


def load_comment(db: Session, *, workspace_id: uuid.UUID, comment_id: uuid.UUID) -> Any:
    from app.models.collab import ReviewComment

    comment = db.execute(select(ReviewComment).where(ReviewComment.id == comment_id,
                                                     ReviewComment.workspace_id == workspace_id)).scalar_one_or_none()
    if comment is None:
        raise ThreadError("No such comment in this workspace.", status=404, code="NOT_FOUND")
    return comment


def edit(db: Session, *, comment: Any, thread: Any, actor_user_id: uuid.UUID, body: str) -> Any:
    if comment.author_user_id != actor_user_id:
        raise ThreadError("Only its author can edit a comment.", status=403, code="NOT_AUTHOR")
    if comment.body is None:
        raise ThreadError("A deleted comment cannot be edited.", status=409, code="DELETED")
    comment.body = _clean_body(body)
    comment.edited_at = service.now()
    thread.last_activity_at = comment.edited_at
    db.flush()
    events.thread_changed(db, workspace_id=thread.workspace_id, kind=thread.kind, item_id=thread.item_id,
                          thread_id=thread.id, action=v.THREAD_EDITED,
                          open_threads=open_count(db, kind=thread.kind, item_id=thread.item_id))
    return comment


def delete(db: Session, *, comment: Any, thread: Any, actor_user_id: uuid.UUID, is_admin: bool) -> bool:
    """Soft delete; True when a moderator removed someone else's comment (the route audits that)."""
    moderated = comment.author_user_id != actor_user_id
    if moderated and not is_admin:
        raise ThreadError("Only its author or a workspace admin can delete a comment.", status=403, code="NOT_AUTHOR")
    if comment.body is None:
        raise ThreadError("This comment was already deleted.", status=409, code="DELETED")
    comment.body = None
    comment.deleted_at = service.now()
    db.flush()
    events.thread_changed(db, workspace_id=thread.workspace_id, kind=thread.kind, item_id=thread.item_id,
                          thread_id=thread.id, action=v.THREAD_DELETED_COMMENT,
                          open_threads=open_count(db, kind=thread.kind, item_id=thread.item_id))
    return moderated


def list_threads(db: Session, *, item: Any, include_resolved: bool = True) -> list[dict]:
    """The item's threads with their comments, authors and where each paragraph anchor is now."""
    from app.models.collab import ReviewComment, ReviewThread

    query = select(ReviewThread).where(ReviewThread.workspace_id == item.workspace_id, ReviewThread.kind == item.kind,
                                       ReviewThread.item_id == item.item_id)
    if not include_resolved:
        query = query.where(ReviewThread.status == v.THREAD_OPEN)
    threads = list(db.execute(query.order_by(ReviewThread.created_at, ReviewThread.id)).scalars())
    if not threads:
        return []
    comments = list(db.execute(select(ReviewComment).where(ReviewComment.thread_id.in_([t.id for t in threads]))
                               .order_by(ReviewComment.created_at, ReviewComment.id)).scalars())
    people = service.people(db, [t.created_by_user_id for t in threads] + [t.resolved_by_user_id for t in threads]
                            + [c.author_user_id for c in comments] + [m for c in comments for m in (c.mentions or [])])
    paragraphs: Optional[list[A.Paragraph]] = None
    if any(t.anchor_kind == v.ANCHOR_PARAGRAPH for t in threads):
        paragraphs = document_paragraphs(db, workspace_id=item.workspace_id, work_item_id=item.work_item_id)
    by_thread: dict[uuid.UUID, list[Any]] = {}
    for c in comments:
        by_thread.setdefault(c.thread_id, []).append(c)
    out = []
    for t in threads:
        anchor: dict[str, Any] = {"kind": t.anchor_kind, "page": t.anchor_page, "paragraph": t.anchor_paragraph,
                                  "field": t.anchor_field, "quote": t.anchor_quote, "digest": t.anchor_digest,
                                  "state": v.ANCHOR_CURRENT, "current_page": t.anchor_page,
                                  "current_paragraph": t.anchor_paragraph}
        if t.anchor_kind == v.ANCHOR_PARAGRAPH:
            located = A.locate(paragraphs or [], page=int(t.anchor_page), index=int(t.anchor_paragraph),
                               digest_hex=str(t.anchor_digest))
            anchor.update(state=located.state, current_page=located.page, current_paragraph=located.index)
        out.append({
            "id": str(t.id), "kind": t.kind, "item_id": str(t.item_id), "status": t.status, "anchor": anchor,
            "created_by": people.get(str(t.created_by_user_id)) if t.created_by_user_id else None,
            "created_at": t.created_at, "last_activity_at": t.last_activity_at, "resolved_at": t.resolved_at,
            "resolved_by": people.get(str(t.resolved_by_user_id)) if t.resolved_by_user_id else None,
            "comments": [{
                "id": str(c.id), "author": people.get(str(c.author_user_id)) if c.author_user_id else None,
                "body": c.body, "deleted": c.deleted_at is not None, "erased": c.erased_at is not None,
                "mentions": [people.get(str(m)) or {"user_id": str(m), "email": "", "name": ""} for m in (c.mentions or [])],
                "created_at": c.created_at, "edited_at": c.edited_at,
            } for c in by_thread.get(t.id, [])],
        })
    return out


def open_counts(db: Session, *, workspace_id: uuid.UUID, item_ids: Sequence[uuid.UUID]) -> dict[tuple[str, str], int]:
    """{(kind, item id): open threads} for a page of the queue."""
    from app.models.collab import ReviewThread

    if not item_ids:
        return {}
    rows = db.execute(select(ReviewThread.kind, ReviewThread.item_id, func.count())
                      .where(ReviewThread.workspace_id == workspace_id, ReviewThread.item_id.in_(list(item_ids)),
                             ReviewThread.status == v.THREAD_OPEN)
                      .group_by(ReviewThread.kind, ReviewThread.item_id)).all()
    return {(str(k), str(i)): int(n) for k, i, n in rows}


def erase_for_work_items(db: Session, work_item_ids: Sequence[uuid.UUID]) -> int:
    """ARCH-20: discussions anchored on erased documents quote them -- they go (their comments cascade)."""
    if not work_item_ids:
        return 0
    return int(db.execute(text("DELETE FROM review_threads WHERE work_item_id = ANY(:ids)"),
                          {"ids": list(work_item_ids)}).rowcount or 0)


def erase_author(db: Session, *, organization_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """ARCH-20: a data subject's own words leave every thread in the organization; the thread keeps its shape."""
    return int(db.execute(text(
        "UPDATE review_comments c SET body = NULL, erased_at = now(), client_nonce = NULL "
        "FROM review_threads t WHERE t.id = c.thread_id AND t.workspace_id = c.workspace_id "
        "AND t.organization_id = :o AND c.author_user_id = :u AND c.erased_at IS NULL"),
        {"o": organization_id, "u": user_id}).rowcount or 0)


__all__ = ["Anchor", "ThreadError", "delete", "document_paragraphs", "edit", "erase_author", "erase_for_work_items",
           "list_threads", "load_comment", "load_thread", "open_count", "open_counts", "reply", "set_status",
           "start_thread"]
