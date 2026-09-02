"""ARCH-12 Step 6 — sealing a generation, and rendering its provenance.

`context_hash` and `audit_log_id` are the moat. They turn "the AI said so"
into "here is the exact context the model saw, sealed in a tamper-evident log
at this timestamp". ARCH-07 already built the log; this writes one row per
generation and hands the frontend a payload it can highlight from.

WHY THE SEAL IS WRITTEN BEFORE THE FIRST TOKEN
==============================================

The audit row records *what the model was given*, not what it produced. That
is fully known at prompt-assembly time and it is the thing a dispute is about
— "your assistant told my auditor X" is answered by showing the evidence set,
not by showing the sentence again. Writing it up front also means the seal
survives a disconnect: a stream abandoned at token 40 still has a complete,
timestamped record of the context that produced those 40 tokens.

WHY IT USES `record_independently`
==================================

`audit_service.record` flushes into the caller's transaction and lets the
caller commit. Here the caller is about to spend twenty seconds streaming,
during which holding an open write transaction would pin a connection and
block the `audit_logs` insert behind whatever the settlement transaction
later does. `record_independently` commits on its own session immediately —
the same reasoning ARCH-08 applied to denial records, for the same reason:
the audit fact must not be contingent on a long-running operation succeeding.

WHY A FAILED SEAL DOES NOT FAIL THE ANSWER
==========================================

`record_independently` returns None on failure rather than raising. A user
asking a question in a workspace whose audit table is momentarily unavailable
should still get an answer; what they must not get is an answer that
*claims* to be sealed when it is not. `CitationEnvelope.is_sealed` is False
whenever either field is missing, and the frontend contract is that the
provenance badge renders only when `is_sealed` is true.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.schemas.citation import (
    CitationBoundingBox,
    CitationClaim,
    CitationEnvelope,
    CitationSource,
)
from app.services import audit_service
from app.services.citation_service import snippet_service
from app.services.fenced_context import FencedContext

logger = logging.getLogger("app.services.provenance")

#: Cap on how many chunk ids go into the audit row's details. The full set is
#: recoverable from `conversation_messages.sources`; the audit row needs
#: enough to prove the evidence set, not a second copy of it.
MAX_SEALED_CHUNK_IDS = 50


def seal_generation(
    db: Optional[Session],
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    fenced: FencedContext,
    query: str,
    provider: str,
    model: str,
    prompt_version: Optional[str] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> tuple[str, Optional[uuid.UUID]]:
    """Write the tamper-evident record. Returns (context_hash, audit_log_id).

    `context_hash` is always returned — it is a pure function of the context
    and cannot fail. `audit_log_id` is None if the write did not land, and the
    caller must not present the result as sealed in that case.
    """
    context_hash = fenced.sha256()

    audit_log_id = audit_service.record_independently(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        resource_type=getattr(AuditResourceType, "CONVERSATION", AuditResourceType.WORKSPACE),
        resource_id=conversation_id,
        action=getattr(AuditAction, "GENERATED", AuditAction.CREATED),
        outcome=AuditOutcome.ALLOWED,
        details={
            "message_id": str(message_id),
            "context_hash": context_hash,
            "context_characters": len(fenced),
            "passages_included": fenced.passages_included,
            "passages_dropped": fenced.passages_dropped,
            "context_truncated": fenced.truncated,
            "injection_flags": dict(fenced.injection_flags),
            "chunk_ids": list(fenced.chunk_ids[:MAX_SEALED_CHUNK_IDS]),
            "chunk_id_count": len(fenced.chunk_ids),
            "provider": provider,
            "model": model,
            "prompt_version": prompt_version,
            # The question is recorded; the answer is not. The answer lives on
            # the message row and is subject to the output filter, and
            # duplicating it into an immutable table would put unredacted
            # model output somewhere ARCH-18 erasure cannot reach.
            "query_characters": len(query or ""),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    if audit_log_id is None:
        logger.error(
            "provenance.seal_failed",
            extra={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "context_hash": context_hash,
            },
        )
    else:
        logger.info(
            "provenance.sealed",
            extra={
                "conversation_id": str(conversation_id),
                "message_id": str(message_id),
                "audit_log_id": str(audit_log_id),
                "passages": fenced.passages_included,
            },
        )

    return context_hash, audit_log_id


def source_from_result(
    result: dict[str, Any], *, query: str, rank: int
) -> CitationSource:
    """Map one retrieval result onto the wire contract."""
    metadata = result.get("metadata") or {}
    chunk_page_start = metadata.get("page_start_char")

    snippet = snippet_service.generate(
        text=result.get("text") or "",
        query=query,
        chunk_page_start=chunk_page_start,
    )

    return CitationSource(
        work_item_id=uuid.UUID(str(metadata.get("work_item_id") or result["work_item_id"])),
        original_filename=(
            metadata.get("original_filename")
            or result.get("document_name")
            or "Unknown Document"
        ),
        chunk_id=str(result.get("id") or ""),
        chunk_index=int(metadata.get("chunk_index", result.get("chunk_index", 0)) or 0),
        page_number=metadata.get("page_number"),
        bbox=CitationBoundingBox.from_chunk_bbox(metadata.get("bbox")),
        # Snippet offsets are page-absolute when the chunk knows its own page
        # span, and fall back to the chunk's own span otherwise. The
        # difference matters: a viewer resolving page offsets against a chunk
        # highlights the wrong region rather than none.
        page_start_char=snippet.page_start_char
        if snippet.page_start_char is not None
        else chunk_page_start,
        page_end_char=snippet.page_end_char
        if snippet.page_end_char is not None
        else metadata.get("page_end_char"),
        snippet=snippet.text or (result.get("text") or "")[:300],
        similarity_score=float(result.get("similarity_score", 0.0) or 0.0),
        rank=rank,
    )


def build_envelope(
    *,
    message_id: uuid.UUID,
    conversation_id: uuid.UUID,
    answer: str,
    results: Sequence[dict[str, Any]],
    query: str,
    fenced: Optional[FencedContext],
    context_hash: Optional[str],
    audit_log_id: Optional[uuid.UUID],
    provider: str,
    model: str,
    prompt_version: Optional[str] = None,
    passages_dropped_budget: int = 0,
    truncated: bool = False,
    finish_reason: Optional[str] = None,
    usage_estimated: bool = False,
) -> CitationEnvelope:
    """Assemble the payload the citation panel renders from.

    One claim covering the whole answer is emitted for now. Per-sentence claim
    attribution requires the model to emit span markers, which is an ARCH-13
    prompt change — the envelope shape is a list of claims from day one so
    that change does not become a breaking API revision.
    """
    sources = [
        source_from_result(result, query=query, rank=index)
        for index, result in enumerate(results)
    ]

    claims = [
        CitationClaim(
            claim_id="c1",
            text_span=(0, len(answer or "")),
            sources=sources,
        )
    ]

    return CitationEnvelope(
        message_id=message_id,
        conversation_id=conversation_id,
        claims=claims,
        context_hash=context_hash,
        audit_log_id=audit_log_id,
        model=model,
        provider=provider,
        prompt_version=prompt_version,
        generated_at=datetime.now(timezone.utc).isoformat(),
        passages_included=fenced.passages_included if fenced else 0,
        passages_dropped_injection=fenced.passages_dropped if fenced else 0,
        passages_dropped_budget=passages_dropped_budget,
        truncated=truncated,
        finish_reason=finish_reason,
        usage_estimated=usage_estimated,
    )


def serialise_sources(envelope: CitationEnvelope) -> list[dict[str, Any]]:
    """What gets written to `conversation_messages.sources`.

    Flattened to one list rather than nested under claims, because the stored
    column has always been a flat list and existing readers — the conversation
    GET endpoint, the frontend transcript — must keep working unchanged.
    """
    return [
        source.model_dump(mode="json")
        for claim in envelope.claims
        for source in claim.sources
    ]


__all__ = [
    "MAX_SEALED_CHUNK_IDS",
    "build_envelope",
    "seal_generation",
    "serialise_sources",
    "source_from_result",
]
