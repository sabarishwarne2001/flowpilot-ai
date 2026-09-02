"""ARCH-12 — the SSE generation endpoint."""

from __future__ import annotations

import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.core.exceptions import RateLimitExceededError, SpendLimitExceededError
from app.schemas.assistant import ChatQuery
from app.services.assistant_stream import (
    ReplayIncompleteError,
    ReplayUnavailableError,
    assistant_stream_service,
    sse,
)
from app.services.llm_metering import LLMMeteringError
from app.services.stream_concurrency import generation_slot

logger = logging.getLogger("app.api.v1.assistant_stream")

router = APIRouter(tags=["AI Assistant"])

SSE_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


@router.post(
    "/conversations/{conversation_id}/messages/stream",
    summary="Send Message (streaming)",
    response_description=(
        "text/event-stream. Frames: start, token, citations, done, error. "
        "Every frame carries a monotonic `seq` for A13 resumption."
    ),
    responses={
        402: {"description": "Workspace AI usage limit reached."},
        429: {"description": "Too many concurrent or rapid generations."},
    },
)
async def stream_chat_query(
    conversation_id: uuid.UUID,
    query_in: ChatQuery,
    request: Request,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> StreamingResponse:
    from app.services.audit_service import context_from_request

    audit_context = context_from_request(request)

    try:
        plan = assistant_stream_service.prepare(
            db,
            conversation_id=conversation_id,
            user_id=context.user_id,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            query_text=query_in.content,
            ip_address=audit_context.get("ip_address"),
            user_agent=audit_context.get("user_agent"),
        )
        db.commit()

    except SpendLimitExceededError as exc:
        db.rollback()
        logger.warning(
            "assistant.stream_quota_blocked",
            extra={"conversation_id": str(conversation_id), "limit_key": exc.limit_key},
        )
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=(
                "This workspace has reached its monthly AI usage limit. "
                "Retrieval still works; generation is paused until the limit "
                "resets or is raised."
            ),
        ) from exc

    except LLMMeteringError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    except Exception:
        db.rollback()
        logger.exception(
            "assistant.stream_prepare_failed",
            extra={"conversation_id": str(conversation_id)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal server error.",
        )

    request_id = getattr(request.state, "request_id", None)

    async def guarded() -> AsyncIterator[bytes]:
        try:
            with generation_slot(
                user_id=context.user_id,
                organization_id=context.organization_id,
                conversation_id=conversation_id,
            ):
                async for frame in assistant_stream_service.stream_answer(
                    plan, request_id=request_id
                ):
                    yield frame
        except RateLimitExceededError as exc:
            yield sse(
                "error",
                {
                    "code": "RATE_LIMIT_EXCEEDED",
                    "message": str(exc),
                    "retry_after": exc.retry_after,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "assistant.stream_failed",
                extra={"conversation_id": str(conversation_id)},
            )
            yield sse(
                "error",
                {
                    "code": "GENERATION_FAILED",
                    "message": "The answer could not be completed.",
                    "detail": type(exc).__name__,
                },
            )

    return StreamingResponse(
        guarded(),
        media_type="text/event-stream",
        headers={
            **SSE_HEADERS,
            "X-Message-Id": str(plan.message_id),
        },
    )


@router.get(
    "/messages/{message_id}/stream",
    summary="Resume Message Stream (A13)",
    response_description=(
        "text/event-stream. Replays buffered frames with seq > from_seq. "
        "Never re-invokes the model."
    ),
    responses={
        404: {"description": "Message not found, or no buffered frames remain."},
        409: {
            "description": (
                "`resume_unavailable` — the replay window has partially "
                "expired and completeness cannot be proven. Refetch the "
                "message; do not re-send the query, it was already "
                "billed."
            )
        },
    },
)
async def resume_message_stream(
    message_id: uuid.UUID,
    request: Request,
    from_seq: int = Query(
        0,
        ge=0,
        description=(
            "Last sequence number the client durably handled. Frames with "
            "seq <= from_seq are not re-sent. 0 replays the whole turn."
        ),
    ),
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> StreamingResponse:
    from sqlalchemy import select

    from app.models.assistant import Conversation, ConversationMessage

    row = db.execute(
        select(ConversationMessage.id)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(
            ConversationMessage.id == message_id,
            Conversation.workspace_id == context.workspace_id,
            Conversation.user_id == context.user_id,
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found."
        )

    request_id = getattr(request.state, "request_id", None)

    try:
        frames = assistant_stream_service.replay(
            message_id=message_id, from_seq=from_seq, request_id=request_id
        )
        first = await frames.__anext__()
    except ReplayIncompleteError as exc:
        # ARCH-0V Tranche 6. Distinct from the 404 below: the message
        # exists and frames may exist, but not the ones this client is
        # missing. 409 rather than 404 so the resumable client in FE-1
        # can tell 'gone' from 'incomplete' and refetch instead of
        # rendering a truncated answer as a finished one.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "resume_unavailable",
                "message": str(exc),
                "action": "refetch_message",
                "billed": True,
            },
        ) from exc
    except ReplayUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"{exc} Do not re-send the query — the original generation was "
                "already billed."
            ),
        ) from exc
    except StopAsyncIteration:
        async def empty() -> AsyncIterator[bytes]:
            yield sse("done", {"finish_reason": "already_current", "resumed": True})

        return StreamingResponse(
            empty(), media_type="text/event-stream", headers=SSE_HEADERS
        )

    async def replayed() -> AsyncIterator[bytes]:
        yield first
        async for frame in frames:
            yield frame

    return StreamingResponse(
        replayed(),
        media_type="text/event-stream",
        headers={**SSE_HEADERS, "X-Message-Id": str(message_id)},
    )


@router.get(
    "/messages/{message_id}/provenance",
    summary="Citation Provenance",
    response_description="Sealed provenance envelope for one assistant message.",
)
async def get_message_provenance(
    message_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceViewer),
) -> dict:
    from sqlalchemy import select

    from app.models.assistant import Conversation, ConversationMessage

    row = db.execute(
        select(ConversationMessage)
        .join(Conversation, Conversation.id == ConversationMessage.conversation_id)
        .where(
            ConversationMessage.id == message_id,
            Conversation.workspace_id == context.workspace_id,
            Conversation.user_id == context.user_id,
        )
    ).scalar_one_or_none()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Message not found."
        )

    return {
        "message_id": str(row.id),
        "conversation_id": str(row.conversation_id),
        "context_hash": row.context_hash,
        "audit_log_id": str(row.audit_log_id) if row.audit_log_id else None,
        "is_sealed": bool(row.context_hash and row.audit_log_id),
        "sources": row.sources or [],
        "truncated": row.truncated,
        "finish_reason": row.finish_reason,
        "usage_estimated": row.usage_estimated,
        "stream_state": row.stream_state.value if row.stream_state else None,
    }
