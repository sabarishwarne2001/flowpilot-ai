"""ARCH-11 Step 1 — `embedding.token` metering (closes ARCH-10 R20).

Everything needed to bill embeddings already exists: the taxonomy entry, the
`record_usage` write path, `guard_usage`, `SpendLimitExceededError`, the
`QUOTA_BLOCKED` pipeline stage, and `SPEND_DEFAULT_MONTHLY_EMBEDDING_TOKENS`.
The only missing piece is the call. This module is that call, kept out of the
handler so it can be unit-tested without a job, and so the token arithmetic
lives in one place.

Three decisions worth reading before changing anything here.

**1. Tokens come from the tokenizer, never from `len(text) / 4`.**
An estimate that drifts 20% is an invoice that drifts 20%, and it drifts
systematically rather than randomly: the ratio depends on the language, on
whether the document is prose or a table of part numbers, and on how the OCR
engine spaced the text. `_count_tokens` calls the model's own tokenizer with
padding off, which is exactly the number the encoder will consume.

**2. Billable tokens are capped at the model's sequence window.**
`all-MiniLM-L6-v2` truncates at 256 word-pieces. Tokens past that are never
seen by the encoder, cost nothing to produce, and must not be billed. The
difference is recorded as `truncated_tokens` in the event details — not for
billing, but because it is the honest measure of how much of each chunk is
actually being embedded, and Step 3 needs that number to choose a chunk size.
See finding F1 in the Step 1 notes: the masterplan's 300-500 token target does
not fit a 256-token window.

**3. Idempotency is keyed on the work item, not the job.**
`embed:{work_item_id}:{start}-{end}`. A reaped enrich job re-runs and re-embeds
— that is correct and unavoidable — but it must not re-bill, because the tenant
did not ask for the work twice. This differs deliberately from `ocr.page`,
which keys on `job_id` so that an explicit reprocess *does* bill again. The
consequence is that the second run collides on
`uq_usage_events_org_idempotency_key`, so every write goes through a SAVEPOINT
and a collision is a no-op rather than an aborted transaction.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.embeddings import (
    active_max_sequence_tokens,
    active_model_name,
)
from app.models.usage_event import UsageEvent
from app.services import spend_control_service as spend

logger = logging.getLogger("app.services.embedding_metering")

USAGE_EVENT_TYPE = "embedding.token"
USAGE_PROVIDER = "sentence_transformers"
RESOURCE_TYPE = "WORK_ITEM"

#: Name of the partial unique index that enforces idempotency. Matched against
#: the driver's constraint diagnostic so an unrelated IntegrityError is
#: re-raised rather than swallowed.
_IDEMPOTENCY_INDEX = "uq_usage_events_org_idempotency_key"


class EmbeddingMeteringError(RuntimeError):
    """Metering could not be performed; the caller must not proceed to embed."""


# ===========================================================================
# Planning — pure arithmetic, no database, no encoder
# ===========================================================================


@dataclass(frozen=True)
class EmbeddingBatch:
    """One encoder call's worth of chunks, priced before it is made."""

    start_index: int
    end_index: int  # inclusive
    texts: tuple[str, ...]
    #: Tokens the encoder will consume, per text, already capped at the window.
    billable_tokens_per_text: tuple[int, ...]
    #: Tokens discarded by truncation, per text. Never billed.
    truncated_tokens_per_text: tuple[int, ...]

    @property
    def chunk_range(self) -> str:
        """The `{start}-{end}` half of the idempotency key."""
        return f"{self.start_index}-{self.end_index}"

    @property
    def billable_tokens(self) -> int:
        return sum(self.billable_tokens_per_text)

    @property
    def truncated_tokens(self) -> int:
        return sum(self.truncated_tokens_per_text)

    @property
    def truncated_texts(self) -> int:
        return sum(1 for count in self.truncated_tokens_per_text if count > 0)


@dataclass(frozen=True)
class EmbeddingPlan:
    """Every batch for one work item, with the totals the guard checks."""

    batches: tuple[EmbeddingBatch, ...]
    model_name: str
    max_sequence_tokens: int

    @property
    def total_billable_tokens(self) -> int:
        return sum(batch.billable_tokens for batch in self.batches)

    @property
    def total_truncated_tokens(self) -> int:
        return sum(batch.truncated_tokens for batch in self.batches)

    @property
    def total_texts(self) -> int:
        return sum(len(batch.texts) for batch in self.batches)

    @property
    def truncated_texts(self) -> int:
        return sum(batch.truncated_texts for batch in self.batches)

    def as_details(self) -> dict[str, Any]:
        return {
            "model": self.model_name,
            "chunks": self.total_texts,
            "billable_tokens": self.total_billable_tokens,
            "truncated_tokens": self.total_truncated_tokens,
            "truncated_chunks": self.truncated_texts,
            "max_sequence_tokens": self.max_sequence_tokens,
        }


def _count_tokens(model: Any, texts: Sequence[str]) -> list[int]:
    """Untruncated word-piece counts from the model's own tokenizer.

    `padding=False` matters: with padding on, every sequence in the batch is
    counted at the length of the longest one, which would inflate a batch
    containing one long chunk by a factor of the batch size.
    """
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        raise EmbeddingMeteringError(
            "The loaded embedding model exposes no `.tokenizer`. Token counts "
            "must come from the tokenizer; refusing to fall back to a "
            "character-length estimate, which would bill wrong rather than "
            "fail loudly."
        )
    encoded = tokenizer(
        list(texts),
        add_special_tokens=True,
        padding=False,
        truncation=False,
        verbose=False,
    )
    return [len(ids) for ids in encoded["input_ids"]]


def plan_embedding_usage(
    texts: Sequence[str],
    *,
    model: Any,
    batch_size: Optional[int] = None,
    max_sequence_tokens: Optional[int] = None,
    model_name: Optional[str] = None,
) -> EmbeddingPlan:
    """Price a document's chunks without encoding any of them."""
    window = int(
        max_sequence_tokens
        or getattr(model, "max_seq_length", None)
        or active_max_sequence_tokens()
    )

    if not texts:
        return EmbeddingPlan(
            batches=(),
            model_name=model_name or active_model_name(),
            max_sequence_tokens=window,
        )

    size = int(batch_size or settings.EMBEDDING_BATCH_SIZE or 32)
    if size <= 0:
        raise EmbeddingMeteringError("EMBEDDING_BATCH_SIZE must be > 0.")

    raw_counts = _count_tokens(model, texts)
    if len(raw_counts) != len(texts):
        raise EmbeddingMeteringError(
            f"Tokenizer returned {len(raw_counts)} counts for {len(texts)} "
            "texts. Refusing to bill against a misaligned count."
        )

    batches: list[EmbeddingBatch] = []
    for start in range(0, len(texts), size):
        end = min(start + size, len(texts)) - 1
        window_texts = tuple(texts[start : end + 1])
        window_counts = raw_counts[start : end + 1]
        billable = tuple(min(count, window) for count in window_counts)
        truncated = tuple(max(0, count - window) for count in window_counts)
        batches.append(
            EmbeddingBatch(
                start_index=start,
                end_index=end,
                texts=window_texts,
                billable_tokens_per_text=billable,
                truncated_tokens_per_text=truncated,
            )
        )

    return EmbeddingPlan(
        batches=tuple(batches),
        model_name=model_name or active_model_name(),
        max_sequence_tokens=window,
    )


# ===========================================================================
# Recording — idempotent, inside the caller's transaction
# ===========================================================================


def idempotency_key(work_item_id: uuid.UUID, batch: EmbeddingBatch) -> str:
    return f"embed:{work_item_id}:{batch.chunk_range}"


def _is_idempotency_collision(exc: IntegrityError) -> bool:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint:
        return constraint == _IDEMPOTENCY_INDEX
    return _IDEMPOTENCY_INDEX in str(exc.orig)


def _existing_event(
    db: Session, *, organization_id: uuid.UUID, key: str
) -> Optional[UsageEvent]:
    return db.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == organization_id,
            UsageEvent.idempotency_key == key,
        )
    ).scalar_one_or_none()


def record_batch_usage(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    batch: EmbeddingBatch,
    plan: EmbeddingPlan,
    job_id: Optional[uuid.UUID] = None,
) -> bool:
    """Check the ceiling, then record one batch. Returns False if already billed.

    The ceiling check happens *before* the encoder call for this batch, which is
    what makes the control a control: a runaway document stops at the batch that
    would cross the limit rather than after the whole corpus has been encoded.
    Rows flushed by earlier batches are visible to this session's aggregate, so
    the accumulation is correct within the transaction.
    """
    key = idempotency_key(work_item_id, batch)

    with spend.guard_usage(
        db,
        organization_id=organization_id,
        event_type=USAGE_EVENT_TYPE,
        estimated_quantity=batch.billable_tokens,
        # Self-hosted sentence-transformers has no per-call provider cost.
        # The ceiling that bites here is the quantity ceiling, which is why
        # ARCH-10 Step 3 caps on quantity *and* cost rather than cost alone.
        estimated_cost_micros=None,
        workspace_id=workspace_id,
        job_id=job_id,
        resource_type=RESOURCE_TYPE,
        resource_id=work_item_id,
        idempotency_key=key,
    ) as guard:
        savepoint = db.begin_nested()
        try:
            guard.record(
                quantity=batch.billable_tokens,
                provider=USAGE_PROVIDER,
                details={
                    "model": plan.model_name,
                    "chunk_start": batch.start_index,
                    "chunk_end": batch.end_index,
                    "chunks": len(batch.texts),
                    "truncated_tokens": batch.truncated_tokens,
                    "truncated_chunks": batch.truncated_texts,
                    "max_sequence_tokens": plan.max_sequence_tokens,
                },
            )
            savepoint.commit()
            return True
        except IntegrityError as exc:
            savepoint.rollback()
            if not _is_idempotency_collision(exc):
                raise
            prior = _existing_event(db, organization_id=organization_id, key=key)
            if prior is not None:
                # Keep guard.recorded honest so guard_usage does not log
                # "recorded nothing" for a batch that was billed on a prior run.
                guard.recorded.append(prior)
            logger.info(
                "embedding.already_billed",
                extra={
                    "work_item_id": str(work_item_id),
                    "idempotency_key": key,
                    "prior_usage_event_id": str(prior.id) if prior else None,
                },
            )
            return False


# ===========================================================================
# The call site's entry point
# ===========================================================================


def embed_texts_with_metering(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    texts: Sequence[str],
    job_id: Optional[uuid.UUID] = None,
    encode: Optional[Any] = None,
) -> tuple[list[list[float]], EmbeddingPlan]:
    """Meter and embed one work item's chunks, in order.

    Raises `SpendLimitExceededError` from `guard_usage` when a batch would cross
    the tenant's ceiling. The caller is expected to let that propagate: the
    enrich handler already converts it into a `QUOTA_BLOCKED` transition, and
    because nothing is written to the vector store until every batch has
    returned, a refusal leaves no half-embedded document behind.
    """
    from app.services.embedding_service import embedding_service

    if not texts:
        empty = EmbeddingPlan(
            batches=(),
            model_name=active_model_name(),
            max_sequence_tokens=active_max_sequence_tokens(),
        )
        return [], empty

    encoder = encode or embedding_service.generate_embeddings
    model = embedding_service._get_model()  # noqa: SLF001 — the tokenizer lives here

    plan = plan_embedding_usage(texts, model=model)

    if not settings.EMBEDDING_METERING_ENABLED:
        logger.warning(
            "embedding.metering_disabled",
            extra={"work_item_id": str(work_item_id), "chunks": plan.total_texts},
        )
        return list(encoder(list(texts))), plan

    # Fail fast on the whole document before the first encoder call. This is a
    # convenience, not the enforcement point — the per-batch guard below is.
    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=USAGE_EVENT_TYPE,
        quantity=plan.total_billable_tokens,
        workspace_id=workspace_id,
    )

    embeddings: list[list[float]] = []
    billed_batches = 0
    for batch in plan.batches:
        newly_billed = record_batch_usage(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
            batch=batch,
            plan=plan,
            job_id=job_id,
        )
        billed_batches += int(newly_billed)
        embeddings.extend(encoder(list(batch.texts)))

    if len(embeddings) != len(texts):
        raise EmbeddingMeteringError(
            f"Encoder returned {len(embeddings)} vectors for {len(texts)} "
            "chunks. Refusing to store a misaligned corpus."
        )

    logger.info(
        "embedding.metered",
        extra={
            "work_item_id": str(work_item_id),
            "organization_id": str(organization_id),
            "batches": len(plan.batches),
            "batches_billed": billed_batches,
            **plan.as_details(),
        },
    )
    return embeddings, plan


__all__ = [
    "EmbeddingBatch",
    "EmbeddingMeteringError",
    "EmbeddingPlan",
    "USAGE_EVENT_TYPE",
    "embed_texts_with_metering",
    "idempotency_key",
    "plan_embedding_usage",
    "record_batch_usage",
]