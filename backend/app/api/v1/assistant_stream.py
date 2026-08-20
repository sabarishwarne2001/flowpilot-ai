"""ARCH-12 — the SSE generation endpoint.

A SEPARATE ROUTER, MOUNTED AT THE SAME PREFIX
=============================================

`app/api/v1/assistant.py` is 377 lines of CRUD that has been stable since
ARCH-11. Adding a streaming endpoint to it means every future streaming change
touches the file the conversation list endpoints live in. FastAPI happily
includes two routers under one prefix, so this ships as an additive module and
the diff to the existing file is one line in `router.py`.

WHY EVERYTHING THAT CAN REFUSE, REFUSES BEFORE `StreamingResponse`
==================================================================

Once a `StreamingResponse` is returned, the status line is already 200. A
spend ceiling hit at that point cannot be expressed as 402 — the best
available is an SSE `error` frame that a naive client renders as answer text.
So `prepare()` runs synchronously inside the request, on the request's
session, and every refusal path (404, 402, 429) resolves there.

The rate limiter is a context manager held **for the life of the stream**,
which is why it wraps the generator rather than sitting in `Depends`. A
dependency releases at response time, which for streaming is before the first
token — exactly the bug that A1 describes for sessions, applied to slots.

HEADERS
=======

`X-Accel-Buffering: no` disables nginx proxy buffering. Without it nginx holds
tokens until its buffer fills and the measured TTFT is whatever the buffer
size divided by the token rate happens to be — which is how a system that
streams correctly ships with a 4-second time-to-first-token.
"""

from __future__ import annotations

import logging
import uuid
from typing import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api import deps
from app.core.exceptions import RateLimitExceededError, SpendLimitExceededError
from app.schemas.assistant import ChatQuery
from app.services.assistant_stream import assistant_stream_service, sse
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
        "text/event-stream. Frames: start, token, citations, done, error."
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
        """Hold the concurrency slot for the whole stream, not the request."""
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
            # The limiter can only refuse before the first provider token, so
            # nothing has been emitted yet and an error frame is honest.
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
        guarded(), media_type="text/event-stream", headers=SSE_HEADERS
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
    """Re-read the sealed record for a message the client already has.

    Exists so a citation panel opened days later renders from the stored row
    rather than from whatever the client kept in memory during the stream.
    """
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