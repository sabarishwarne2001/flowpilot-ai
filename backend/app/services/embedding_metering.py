"""ARCH-11 Step 1 & Step 4 — `embedding.token` & `embedding.backfill_token` metering.

Everything needed to bill embeddings already exists: the taxonomy entry, the
`record_usage` write path, `guard_usage`, `SpendLimitExceededError`, the
`QUOTA_BLOCKED` pipeline stage, and `SPEND_DEFAULT_MONTHLY_EMBEDDING_TOKENS`.
This module prices and meters embedding batches with exact tokenizer counts,
sequence window caps, and idempotent savepoint-protected recording.
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

#: Name of the partial unique index that enforces idempotency.
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
    """Untruncated word-piece counts from the model's own tokenizer."""
    tokenizer = getattr(model, "tokenizer", None)
    if tokenizer is None:
        raise EmbeddingMeteringError(
            "The loaded embedding model exposes no `.tokenizer`. Token counts "
            "must come from the tokenizer; refusing to fall back to a "
            "character-length estimate."
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


def idempotency_key(
    work_item_id: uuid.UUID, batch: EmbeddingBatch, *, prefix: str = "embed"
) -> str:
    return f"{prefix}:{work_item_id}:{batch.chunk_range}"


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
    event_type: str = USAGE_EVENT_TYPE,
    idempotency_prefix: str = "embed",
) -> bool:
    """Check the ceiling, then record one batch. Returns False if already billed."""
    key = idempotency_key(work_item_id, batch, prefix=idempotency_prefix)

    with spend.guard_usage(
        db,
        organization_id=organization_id,
        event_type=event_type,
        estimated_quantity=batch.billable_tokens,
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
    event_type: str = USAGE_EVENT_TYPE,
    idempotency_prefix: str = "embed",
) -> tuple[list[list[float]], EmbeddingPlan]:
    """Meter and embed one work item's chunks, in order."""
    from app.services.embedding_service import embedding_service

    if not texts:
        empty = EmbeddingPlan(
            batches=(),
            model_name=active_model_name(),
            max_sequence_tokens=active_max_sequence_tokens(),
        )
        return [], empty

    encoder = encode or embedding_service.generate_embeddings
    model = embedding_service._get_model()  # noqa: SLF001

    plan = plan_embedding_usage(texts, model=model)

    if not settings.EMBEDDING_METERING_ENABLED:
        logger.warning(
            "embedding.metering_disabled",
            extra={"work_item_id": str(work_item_id), "chunks": plan.total_texts},
        )
        return list(encoder(list(texts))), plan

    # Fail fast on the whole document before the first encoder call
    spend.ensure_within_limits(
        db,
        organization_id=organization_id,
        event_type=event_type,
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
            event_type=event_type,
            idempotency_prefix=idempotency_prefix,
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
