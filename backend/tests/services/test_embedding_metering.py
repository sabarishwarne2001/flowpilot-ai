"""ARCH-11 Step 1 — `embedding.token` metering.

The tokenizer is faked. That is deliberate: loading `all-MiniLM-L6-v2` in unit
tests costs ~90 MB and several seconds, and it would make these tests fail on a
machine with no model cache — which turns a metering regression into "the CI
box has no network" and nobody looks any further. What is under test here is
the arithmetic, the batching, the idempotency and the ceiling, none of which
depend on real weights.

`test_token_count_comes_from_a_tokenizer` is the one that guards the property
the fake cannot: that nothing in the module falls back to `len(text) / 4`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import SpendLimitExceededError
from app.models.spend_limit import SpendLimitPeriod
from app.models.usage_event import UsageEvent
from app.services import spend_control_service as spend
from app.services.embedding_metering import (
    EmbeddingMeteringError,
    idempotency_key,
    plan_embedding_usage,
    record_batch_usage,
)


# ===========================================================================
# Fakes
# ===========================================================================


class _FakeTokenizer:
    """Whitespace tokenizer plus two special tokens, so counts are predictable."""

    def __call__(self, texts, add_special_tokens=True, padding=False,
                 truncation=False, verbose=False):
        assert padding is False, "padding inflates per-text counts across a batch"
        assert truncation is False, "truncation would hide the discarded tokens"
        extra = 2 if add_special_tokens else 0
        return {"input_ids": [[0] * (len(t.split()) + extra) for t in texts]}


@dataclass
class _FakeModel:
    tokenizer: object
    max_seq_length: int = 8


class _NoTokenizerModel:
    pass


def _words(n: int) -> str:
    return " ".join(f"w{i}" for i in range(n))


@pytest.fixture()
def model() -> _FakeModel:
    return _FakeModel(tokenizer=_FakeTokenizer())


@pytest.fixture()
def org_id(db_session: Session) -> uuid.UUID:
    from app.models.organization import Organization

    org = Organization(name="embed-metering", slug=f"em-{uuid.uuid4().hex[:10]}")
    db_session.add(org)
    db_session.flush([org])
    return org.id


@pytest.fixture()
def workspace_id(db_session: Session, org_id: uuid.UUID) -> uuid.UUID:
    from app.models.workspace import Workspace, WorkspaceStatus

    workspace = Workspace(
        organization_id=org_id,
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        workspace_name="Metering",
        status=WorkspaceStatus.ACTIVE,
    )
    db_session.add(workspace)
    db_session.flush([workspace])
    return workspace.id


# ===========================================================================
# Planning
# ===========================================================================


@pytest.mark.no_db
def test_token_count_comes_from_a_tokenizer(model):
    plan = plan_embedding_usage([_words(3)], model=model, batch_size=4)
    # 3 words + 2 special tokens. A character-length estimate would give
    # len("w0 w1 w2") / 4 == 2, which is the failure this asserts against.
    assert plan.total_billable_tokens == 5


@pytest.mark.no_db
def test_missing_tokenizer_refuses_rather_than_estimating():
    with pytest.raises(EmbeddingMeteringError, match="tokenizer"):
        plan_embedding_usage(["anything"], model=_NoTokenizerModel())


@pytest.mark.no_db
def test_batches_follow_batch_size_and_are_contiguous(model):
    plan = plan_embedding_usage([_words(1)] * 7, model=model, batch_size=3)
    ranges = [(b.start_index, b.end_index) for b in plan.batches]
    assert ranges == [(0, 2), (3, 5), (6, 6)]
    assert plan.total_texts == 7


@pytest.mark.no_db
def test_tokens_beyond_the_window_are_recorded_but_not_billed(model):
    # window is 8; 20 words + 2 special = 22 tokens.
    plan = plan_embedding_usage([_words(20)], model=model, batch_size=4)
    assert plan.total_billable_tokens == 8
    assert plan.total_truncated_tokens == 14
    assert plan.truncated_texts == 1


@pytest.mark.no_db
def test_empty_input_produces_no_batches(model):
    plan = plan_embedding_usage([], model=model)
    assert plan.batches == ()
    assert plan.total_billable_tokens == 0


@pytest.mark.no_db
def test_idempotency_key_shape():
    work_item_id = uuid.UUID("33333333-3333-4333-8333-333333333333")
    plan = plan_embedding_usage(
        [_words(1)] * 5, model=_FakeModel(tokenizer=_FakeTokenizer()), batch_size=2
    )
    keys = [idempotency_key(work_item_id, batch) for batch in plan.batches]
    assert keys == [
        f"embed:{work_item_id}:0-1",
        f"embed:{work_item_id}:2-3",
        f"embed:{work_item_id}:4-4",
    ]


# ===========================================================================
# Recording
# ===========================================================================


def test_usage_is_recorded_once_per_batch(db_session, org_id, workspace_id, model):
    work_item_id = uuid.uuid4()
    plan = plan_embedding_usage([_words(2)] * 4, model=model, batch_size=2)

    for batch in plan.batches:
        assert record_batch_usage(
            db_session,
            organization_id=org_id,
            workspace_id=workspace_id,
            work_item_id=work_item_id,
            batch=batch,
            plan=plan,
        )

    rows = db_session.execute(
        select(UsageEvent).where(
            UsageEvent.organization_id == org_id,
            UsageEvent.event_type == "embedding.token",
        )
    ).scalars().all()

    assert len(rows) == 2
    assert sum(row.quantity for row in rows) == Decimal(plan.total_billable_tokens)
    assert {row.unit for row in rows} == {"token"}
    assert {row.provider for row in rows} == {"sentence_transformers"}
    assert all(row.workspace_id == workspace_id for row in rows)
    assert all(row.resource_id == work_item_id for row in rows)


def test_replaying_a_batch_does_not_rebill(db_session, org_id, workspace_id, model):
    """A reaped enrich job re-embeds. It must not re-invoice."""
    work_item_id = uuid.uuid4()
    plan = plan_embedding_usage([_words(2)] * 2, model=model, batch_size=2)
    batch = plan.batches[0]

    assert record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=batch, plan=plan,
    ) is True
    assert record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=batch, plan=plan,
    ) is False

    rows = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id)
    ).scalars().all()
    assert len(rows) == 1


def test_transaction_survives_the_collision(db_session, org_id, workspace_id, model):
    """The savepoint is the point. Without it the IntegrityError aborts the
    whole enrich transaction and the document fails instead of skipping a
    duplicate bill."""
    work_item_id = uuid.uuid4()
    plan = plan_embedding_usage([_words(2)] * 4, model=model, batch_size=2)

    record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=plan.batches[0], plan=plan,
    )
    record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=plan.batches[0], plan=plan,
    )
    # Still usable afterwards:
    assert record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=plan.batches[1], plan=plan,
    ) is True
    assert db_session.execute(select(UsageEvent.id)).first() is not None


def test_ceiling_refuses_before_the_batch(db_session, org_id, workspace_id, model):
    spend.set_limit(
        db_session,
        organization_id=org_id,
        limit_key="embedding.token",
        period=SpendLimitPeriod.MONTH,
        max_quantity=Decimal("5"),
        hard_stop=True,
    )
    work_item_id = uuid.uuid4()
    plan = plan_embedding_usage([_words(3)] * 2, model=model, batch_size=1)

    assert record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=plan.batches[0], plan=plan,
    )
    with pytest.raises(SpendLimitExceededError) as excinfo:
        record_batch_usage(
            db_session, organization_id=org_id, workspace_id=workspace_id,
            work_item_id=work_item_id, batch=plan.batches[1], plan=plan,
        )
    assert excinfo.value.limit_key == "embedding.token"

    rows = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id)
    ).scalars().all()
    assert len(rows) == 1, "the refused batch must leave no usage row behind"


def test_details_carry_the_truncation_evidence(db_session, org_id, workspace_id, model):
    work_item_id = uuid.uuid4()
    plan = plan_embedding_usage([_words(20)], model=model, batch_size=4)
    record_batch_usage(
        db_session, organization_id=org_id, workspace_id=workspace_id,
        work_item_id=work_item_id, batch=plan.batches[0], plan=plan,
    )
    row = db_session.execute(
        select(UsageEvent).where(UsageEvent.organization_id == org_id)
    ).scalar_one()
    assert row.details["truncated_tokens"] == 14
    assert row.details["max_sequence_tokens"] == 8
    assert row.details["chunk_start"] == 0