"""ARCH-48 — Real-Time Collaborative Review & Live Presence.

    GET    /workspaces/{wid}/review/collab/state                          live state   [CONTRIBUTOR]
    GET    /workspaces/{wid}/review/collab/{kind}/{id}/threads            threads      [CONTRIBUTOR]
    POST   /workspaces/{wid}/review/collab/{kind}/{id}/threads            new thread   [CONTRIBUTOR]
    GET    /workspaces/{wid}/review/collab/{kind}/{id}/paragraphs         anchors      [CONTRIBUTOR]
    POST   /workspaces/{wid}/review/collab/{kind}/{id}/lock/break         break lock   [ADMIN, audited]
    POST   /workspaces/{wid}/review/collab/threads/{tid}/comments         reply        [CONTRIBUTOR]
    POST   /workspaces/{wid}/review/collab/threads/{tid}/resolve          resolve      [CONTRIBUTOR]
    POST   /workspaces/{wid}/review/collab/threads/{tid}/reopen           reopen       [CONTRIBUTOR]
    PATCH  /workspaces/{wid}/review/collab/comments/{cid}                 edit         [author]
    DELETE /workspaces/{wid}/review/collab/comments/{cid}                 delete       [author / ADMIN, audited]
    WS     /workspaces/{wid}/review/collab/live                           the live channel

ARCH48-S1:collab-api. Every REST route is gated on capability.collaborative_review
as the FIRST statement of its body (the workspace and the CONTRIBUTOR role are
checked by the dependency before it): 402 CAPABILITY_REQUIRED, audited. The
WebSocket has no 402: the same capability is checked during the handshake and a
refusal closes with 1008 BEFORE accepting (`collab.gate`).

WHY `/collab/...` AND NOT `/{kind}/{id}/...` BESIDE THE HUB'S ROUTES
=================================================================
The hub's parameterised routes are `/{kind}/{item_id}/resolve|assign`: three
segments ending in a literal. `/threads/{tid}/resolve` would have been captured
by `/{kind}/{item_id}/resolve` with kind="threads". Under `/collab` nothing here
has the hub's shape, at any registration order.

Optimistic concurrency is NOT here: it is on the hub's resolve and bulk routes
and on every source screen's decision, for every plan (resolution.py).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, WebSocket, status
from sqlalchemy.orm import Session

from app.api import deps
from app.core.exceptions import FlowPilotError
from app.schemas.collab import (BreakLockResult, CommentCreated, EditComment, LiveState, LockOut, NewComment,
                                NewThread, ParagraphList, ParagraphOut, ThreadCreated, ThreadList, ThreadOut)
from app.services.collab import gate
from app.services.collab import vocabulary as v

logger = logging.getLogger("app.api.v1.review_collab")

router = APIRouter(tags=["Review Hub"])


def _gate(db: Session, context: Any, operation: str) -> None:
    gate.require(db, context=context, operation=operation)


def _item(db: Session, context: Any, kind: str, item_id: uuid.UUID) -> Any:
    """The hub item, workspace-scoped, of a kind the plan shows (400 / 403 / 404)."""
    from app.api.v1 import review as review_api
    from app.services.review import projection

    review_api._require_kind(db, context, kind)  # noqa: SLF001 - the hub's own rule
    item = projection.load_item(db, workspace_id=context.workspace_id, kind=kind, item_id=item_id)
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Review item not found in this workspace.")
    return item


class CollabRefusedError(FlowPilotError):
    """A thread refusal in the ARCH-01 envelope `{code, message, details}` (the console branches on `code`)."""

    status_code = 400
    code = "REFUSED"


_REFUSALS: dict[tuple[int, str], type[CollabRefusedError]] = {}


def _thread_error(exc: Exception) -> CollabRefusedError:
    key = (int(getattr(exc, "status", 400)), str(getattr(exc, "code", "REFUSED")))
    klass = _REFUSALS.get(key)
    if klass is None:
        klass = type(f"CollabRefused_{key[1]}_{key[0]}", (CollabRefusedError,), {"status_code": key[0], "code": key[1]})
        _REFUSALS[key] = klass
    return klass(str(exc))


def _is_admin(context: Any) -> bool:
    role = getattr(context, "role", None)
    return str(getattr(role, "value", role) or "").upper() == "ADMIN"


def _audit(db: Session, context: Any, *, item_id: uuid.UUID, operation: str, **details: Any) -> None:
    """ARCH48-S1:collab-audit. Moderation and lock breaks are workspace events, not resolutions: the
    REVIEW_ITEM row stays the resolution path's alone (ARCH-40 gate B8: one writer of a decision's audit)."""
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(db, organization_id=context.organization_id, workspace_id=context.workspace_id,
                         actor_id=context.user_id, resource_type=AuditResourceType.WORKSPACE,
                         resource_id=context.workspace_id, action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"operation": operation, "review_item_id": str(item_id),
                                  **{k: str(val) for k, val in details.items()}})


def _thread_out(db: Session, item: Any, thread_id: uuid.UUID) -> ThreadOut:
    from app.services.collab import threads

    for row in threads.list_threads(db, item=item):
        if row["id"] == str(thread_id):
            return ThreadOut(**row)
    raise HTTPException(status_code=404, detail="No such thread.")


def _item_of_thread(db: Session, context: Any, thread: Any) -> Any:
    from app.api.v1 import review as review_api
    from app.services.review import projection

    review_api._require_kind(db, context, thread.kind)  # noqa: SLF001
    item = projection.load_item(db, workspace_id=context.workspace_id, kind=thread.kind, item_id=thread.item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="The item this thread discussed no longer exists.")
    return item


# ---------------------------------------------------------------------------
# Live state (REST twin of the socket's hello)
# ---------------------------------------------------------------------------


def _locks_and_people(db: Session, workspace_id: uuid.UUID) -> tuple[list, dict]:
    from app.services.collab import service

    locks = service.workspace_locks(db, workspace_id=workspace_id)
    return locks, service.people(db, [lock.holder_user_id for lock in locks])


@router.get("/collab/state", response_model=LiveState, summary="Locks and presence, for a console without its socket")
async def live_state(db: Session = Depends(deps.get_db),
                     context: Any = Depends(deps.RequireWorkspaceContributor)) -> LiveState:
    from app.services.collab import hub as hub_module

    await hub_module.in_thread(_gate, db, context, "review.collab.state")
    locks, people = await hub_module.in_thread(_locks_and_people, db, context.workspace_id)
    presence = await hub_module.get_hub().presence_snapshot(str(context.workspace_id))
    return LiveState(
        locks=[LockOut(kind=lock.kind, item_id=str(lock.item_id), holder_user_id=str(lock.holder_user_id),
                       holder=people.get(str(lock.holder_user_id)), acquired_at=lock.acquired_at,
                       expires_at=lock.expires_at) for lock in locks],
        presence=presence, heartbeat_seconds=v.HEARTBEAT_SECONDS, lock_ttl_seconds=v.LOCK_TTL_SECONDS,
        subprotocol=v.SUBPROTOCOL, live_path=f"/workspaces/{context.workspace_id}/review{v.WS_SUFFIX}")


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


@router.get("/collab/{kind}/{item_id}/threads", response_model=ThreadList, summary="An item's discussion threads")
def list_threads(kind: str, item_id: uuid.UUID, include_resolved: bool = True, db: Session = Depends(deps.get_db),
                 context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadList:
    _gate(db, context, "review.collab.threads.list")
    from app.services.collab import threads

    item = _item(db, context, kind, item_id)
    rows = threads.list_threads(db, item=item, include_resolved=include_resolved)
    return ThreadList(kind=kind, item_id=str(item_id), threads=[ThreadOut(**row) for row in rows],
                      open_threads=threads.open_count(db, kind=kind, item_id=item_id))


@router.post("/collab/{kind}/{item_id}/threads", response_model=ThreadCreated, status_code=201,
             summary="Start a thread on the item, a field or a paragraph")
def start_thread(kind: str, item_id: uuid.UUID, body: NewThread, response: Response, db: Session = Depends(deps.get_db),
                 context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadCreated:
    _gate(db, context, "review.collab.threads.create")
    from app.services.collab import threads

    item = _item(db, context, kind, item_id)
    anchor = threads.Anchor(kind=body.anchor.kind, page=body.anchor.page, paragraph=body.anchor.paragraph,
                            field=body.anchor.field, digest=body.anchor.digest)
    try:
        thread, _comment, created = threads.start_thread(
            db, item=item, author_user_id=context.user_id, anchor=anchor, body=body.body, mentions=body.mentions,
            client_nonce=body.client_nonce)
    except threads.ThreadError as exc:
        db.rollback()
        raise _thread_error(exc) from exc
    db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return ThreadCreated(thread=_thread_out(db, item, thread.id), created=created)


@router.get("/collab/{kind}/{item_id}/paragraphs", response_model=ParagraphList,
            summary="The paragraphs of the item's document a thread can anchor to")
def list_paragraphs(kind: str, item_id: uuid.UUID, db: Session = Depends(deps.get_db),
                    context: Any = Depends(deps.RequireWorkspaceContributor)) -> ParagraphList:
    _gate(db, context, "review.collab.paragraphs")
    from app.services.collab import threads

    item = _item(db, context, kind, item_id)
    found = threads.document_paragraphs(db, workspace_id=context.workspace_id, work_item_id=item.work_item_id)
    return ParagraphList(kind=kind, item_id=str(item_id),
                         work_item_id=str(item.work_item_id) if item.work_item_id else None,
                         paragraphs=[ParagraphOut(**p.as_json()) for p in found],
                         truncated=len(found) >= v.MAX_PARAGRAPHS)


@router.post("/collab/threads/{thread_id}/comments", response_model=CommentCreated, status_code=201,
             summary="Reply in a thread")
def reply(thread_id: uuid.UUID, body: NewComment, response: Response, db: Session = Depends(deps.get_db),
          context: Any = Depends(deps.RequireWorkspaceContributor)) -> CommentCreated:
    _gate(db, context, "review.collab.comments.create")
    from app.services.collab import threads

    try:
        thread = threads.load_thread(db, workspace_id=context.workspace_id, thread_id=thread_id)
        item = _item_of_thread(db, context, thread)
        comment, created = threads.reply(db, thread=thread, item=item, author_user_id=context.user_id, body=body.body,
                                         mentions=body.mentions, client_nonce=body.client_nonce)
    except threads.ThreadError as exc:
        db.rollback()
        raise _thread_error(exc) from exc
    db.commit()
    if not created:
        response.status_code = status.HTTP_200_OK
    return CommentCreated(thread=_thread_out(db, item, thread.id), comment_id=str(comment.id), created=created)


def _set_status(db: Session, context: Any, thread_id: uuid.UUID, resolved: bool) -> ThreadOut:
    from app.services.collab import threads

    try:
        thread = threads.load_thread(db, workspace_id=context.workspace_id, thread_id=thread_id)
        item = _item_of_thread(db, context, thread)
        threads.set_status(db, thread=thread, actor_user_id=context.user_id, resolved=resolved)
    except threads.ThreadError as exc:
        db.rollback()
        raise _thread_error(exc) from exc
    db.commit()
    return _thread_out(db, item, thread_id)


@router.post("/collab/threads/{thread_id}/resolve", response_model=ThreadOut, summary="Mark a thread resolved")
def resolve_thread(thread_id: uuid.UUID, db: Session = Depends(deps.get_db),
                   context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadOut:
    _gate(db, context, "review.collab.threads.resolve")
    return _set_status(db, context, thread_id, True)


@router.post("/collab/threads/{thread_id}/reopen", response_model=ThreadOut, summary="Reopen a resolved thread")
def reopen_thread(thread_id: uuid.UUID, db: Session = Depends(deps.get_db),
                  context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadOut:
    _gate(db, context, "review.collab.threads.reopen")
    return _set_status(db, context, thread_id, False)


@router.patch("/collab/comments/{comment_id}", response_model=ThreadOut, summary="Edit your comment")
def edit_comment(comment_id: uuid.UUID, body: EditComment, db: Session = Depends(deps.get_db),
                 context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadOut:
    _gate(db, context, "review.collab.comments.edit")
    from app.services.collab import threads

    try:
        comment = threads.load_comment(db, workspace_id=context.workspace_id, comment_id=comment_id)
        thread = threads.load_thread(db, workspace_id=context.workspace_id, thread_id=comment.thread_id)
        item = _item_of_thread(db, context, thread)
        threads.edit(db, comment=comment, thread=thread, actor_user_id=context.user_id, body=body.body)
    except threads.ThreadError as exc:
        db.rollback()
        raise _thread_error(exc) from exc
    db.commit()
    return _thread_out(db, item, thread.id)


@router.delete("/collab/comments/{comment_id}", response_model=ThreadOut,
               summary="Delete your comment (a workspace admin may delete anyone's)")
def delete_comment(comment_id: uuid.UUID, db: Session = Depends(deps.get_db),
                   context: Any = Depends(deps.RequireWorkspaceContributor)) -> ThreadOut:
    _gate(db, context, "review.collab.comments.delete")
    from app.services.collab import threads

    try:
        comment = threads.load_comment(db, workspace_id=context.workspace_id, comment_id=comment_id)
        thread = threads.load_thread(db, workspace_id=context.workspace_id, thread_id=comment.thread_id)
        item = _item_of_thread(db, context, thread)
        author = comment.author_user_id
        moderated = threads.delete(db, comment=comment, thread=thread, actor_user_id=context.user_id,
                                   is_admin=_is_admin(context))
    except threads.ThreadError as exc:
        db.rollback()
        raise _thread_error(exc) from exc
    if moderated:
        # ARCH48-S1:moderation-audited. Removing someone else's words is a decision someone is accountable for.
        _audit(db, context, item_id=thread.item_id, operation="review_comment_moderated", comment_id=comment_id,
               thread_id=thread.id, author_user_id=author, review_kind=thread.kind)
    db.commit()
    return _thread_out(db, item, thread.id)


@router.post("/collab/{kind}/{item_id}/lock/break", response_model=BreakLockResult,
             summary="Break another reviewer's soft lock (workspace admin)")
def break_lock(kind: str, item_id: uuid.UUID, db: Session = Depends(deps.get_db),
               context: Any = Depends(deps.RequireWorkspaceAdmin)) -> BreakLockResult:
    _gate(db, context, "review.collab.lock.break")
    from app.services.collab import events, service

    _item(db, context, kind, item_id)
    holder = service.clear_item_lock(db, workspace_id=context.workspace_id, kind=kind, item_id=item_id)
    if holder is not None:
        events.lock_released(db, workspace_id=context.workspace_id, kind=kind, item_id=item_id,
                             holder_user_id=holder, reason=v.RELEASE_FORCED)
        _audit(db, context, item_id=item_id, operation="review_lock_broken", review_kind=kind, holder_user_id=holder)
    db.commit()
    return BreakLockResult(kind=kind, item_id=str(item_id), released=holder is not None,
                           holder_user_id=str(holder) if holder else None)


# ---------------------------------------------------------------------------
# The live channel
# ---------------------------------------------------------------------------


def _authenticate(token: str, workspace_id: uuid.UUID) -> gate.LivePrincipal:
    from app.db import session as session_module

    with session_module.SessionLocal() as db:
        return gate.authenticate(db, token=token, workspace_id=workspace_id)


@router.websocket(v.WS_SUFFIX)
async def live(websocket: WebSocket, workspace_id: uuid.UUID) -> None:
    """ARCH48-S1:ws-endpoint. Authenticate, gate, THEN accept (a refusal is 1008 before accepting)."""
    from app.services.collab import hub as hub_module

    headers = {key.lower(): value for key, value in websocket.headers.items()}
    try:
        await hub_module.in_thread(gate.check_host, headers.get("host"))
        gate.check_origin(headers.get("origin"), headers.get("host"))
        token, offered = gate.extract_token(headers, dict(websocket.query_params))
        principal = await hub_module.in_thread(_authenticate, token, workspace_id)
    except gate.LiveRefused as exc:
        logger.info("collab.handshake_refused", extra={"reason": exc.reason, "workspace_id": str(workspace_id)})
        await websocket.close(code=v.CLOSE_POLICY)
        return
    except Exception:  # noqa: BLE001 - the database or Redis is down: refuse, never accept half-checked
        logger.exception("collab.handshake_failed")
        await websocket.close(code=v.CLOSE_SERVER_ERROR)
        return
    await hub_module.get_hub().serve(websocket, principal, v.SUBPROTOCOL if offered else None)


__all__ = ["router"]
