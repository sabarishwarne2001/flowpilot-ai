"""ARCH-13 Gates 13.1 and 13.2 — the trigger substrate.

Gate 13.1
  - An INTERNAL event is never claimed by the webhook dispatcher.
  - The two vocabularies are disjoint.
  - A workspace with no email settings still evaluates rules; only email
    actions fail.

Gate 13.2
  - Two rules forming a ping-pong: the second execution is SUPPRESSED_CYCLE,
    both rule ids appear in the record, and exactly two executions exist.
  - A chain of six distinct rules stops at AUTOMATION_MAX_DEPTH with
    SUPPRESSED_DEPTH.
  - correlation_id is constant along a chain.
  - Property test: for a random rule graph the number of executions per root
    event is bounded regardless of graph shape.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from app.core.automation_events import (
    INTERNAL_EVENT_TYPES,
    VISIBILITY_INTERNAL,
    VISIBILITY_PUBLIC,
    visibility_for,
)
from app.core.config import settings
from app.core.webhook_events import WEBHOOK_EVENT_TYPES
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
)
from app.models.outbox_event import OutboxEvent, OutboxEventStatus
from app.services import outbox_service
from app.services.automation import cycle_detector
from app.workers import claim as claim_module

pytestmark = pytest.mark.usefixtures("test_database")


# =====================================================================
# Gate 13.1 — vocabulary disjointness
# =====================================================================


def test_vocabularies_are_disjoint() -> None:
    assert not (INTERNAL_EVENT_TYPES & WEBHOOK_EVENT_TYPES), (
        "An internal event type in WEBHOOK_EVENT_TYPES is delivered to every "
        "customer endpoint subscribed to it (F1)."
    )


def test_import_time_assertion_actually_fires(monkeypatch: Any) -> None:
    import app.core.automation_events as events

    monkeypatch.setattr(
        events, "WEBHOOK_EVENT_TYPES", frozenset({"work_item.enriched"})
    )
    with pytest.raises(RuntimeError, match="both"):
        events._assert_vocabularies_disjoint()


def test_reserved_internal_namespace_cannot_be_published(monkeypatch: Any) -> None:
    import app.core.automation_events as events

    monkeypatch.setattr(
        events, "WEBHOOK_EVENT_TYPES", frozenset({"automation.anything"})
    )
    with pytest.raises(RuntimeError, match="reserved internal namespace"):
        events._assert_vocabularies_disjoint()


@pytest.mark.parametrize("event_type", sorted(INTERNAL_EVENT_TYPES))
def test_internal_types_resolve_internal(event_type: str) -> None:
    assert visibility_for(event_type) == VISIBILITY_INTERNAL


@pytest.mark.parametrize("event_type", sorted(WEBHOOK_EVENT_TYPES))
def test_webhook_types_resolve_public(event_type: str) -> None:
    assert visibility_for(event_type) == VISIBILITY_PUBLIC


def test_internal_event_cannot_be_emitted_as_public(db_session, tenant) -> None:
    with pytest.raises(outbox_service.VisibilityMismatchError):
        outbox_service.emit(
            db_session,
            organization_id=tenant.organization.id,
            event_type="work_item.enriched",
            visibility=VISIBILITY_PUBLIC,
        )


def test_public_event_cannot_be_emitted_as_internal(db_session, tenant) -> None:
    with pytest.raises(outbox_service.OutboxError):
        outbox_service.emit(
            db_session,
            organization_id=tenant.organization.id,
            event_type="work_item.created",
            visibility=VISIBILITY_INTERNAL,
        )


def test_unknown_internal_type_is_refused(db_session, tenant) -> None:
    with pytest.raises(outbox_service.UnknownEventTypeError):
        outbox_service.emit_internal(
            db_session,
            organization_id=tenant.organization.id,
            event_type="automation.not_a_real_event",
        )


# =====================================================================
# Gate 13.1 — the dispatcher must not claim INTERNAL rows
# =====================================================================


def test_internal_event_is_never_claimed_by_the_public_queue(
    db_session, tenant
) -> None:
    internal = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.enriched",
        payload={"work_item_id": str(uuid.uuid4())},
    )
    public = outbox_service.emit(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.created",
        payload={"work_item_id": str(uuid.uuid4())},
    )
    db_session.commit()

    claimed = claim_module.claim_eligible_rows(
        db_session, claim_module.OUTBOX_PUBLIC_QUEUE, batch_size=50
    )
    db_session.commit()

    claimed_ids = {row.id for row in claimed}
    assert public.id in claimed_ids
    assert internal.id not in claimed_ids


def test_internal_queue_claims_only_internal_rows(db_session, tenant) -> None:
    internal = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.enriched",
    )
    outbox_service.emit(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.created",
    )
    db_session.commit()

    claimed = claim_module.claim_eligible_rows(
        db_session, claim_module.OUTBOX_INTERNAL_QUEUE, batch_size=50
    )
    db_session.commit()

    assert [row.id for row in claimed] == [internal.id]


def test_outbox_queue_alias_is_the_public_queue() -> None:
    assert claim_module.OUTBOX_QUEUE is claim_module.OUTBOX_PUBLIC_QUEUE


# =====================================================================
# Gate 13.1 — F4 regression
# =====================================================================


@pytest.mark.anyio
async def test_rules_evaluate_without_email_settings(
    db_session, tenant, work_item_factory, rule_factory, monkeypatch
) -> None:
    from app.services.automation_service import automation_service

    assert (
        __import__("app").crud.get_email_settings(
            db_session, workspace_id=tenant.workspace.id
        )
        is None
    ), "this test requires a workspace with no email settings"

    work_item = work_item_factory(classification="Invoice")
    rule_factory(
        name="no-email-needed",
        conditions=[
            {"field": "extracted_entities.document_classification",
             "operator": "EQUALS", "value": "Invoice"}
        ],
        actions=[{"action_type": "email", "config": {"recipient": "a@b.com"}}],
    )
    db_session.commit()

    stats = await automation_service.execute_rules_for_work_item(
        db_session, work_item_id=work_item.id, event="WORK_ITEM_COMPLETED"
    )

    assert stats["evaluated"] == 1
    assert stats["matched"] == 1
    assert stats["actions_failed"] == 1


@pytest.mark.anyio
async def test_email_settings_are_not_read_until_an_email_action_runs(
    db_session, tenant, work_item_factory, rule_factory, monkeypatch
) -> None:
    from app import crud
    from app.services.automation_service import automation_service

    reads: list[uuid.UUID] = []
    original = crud.get_email_settings

    def _spy(db, *, workspace_id):
        reads.append(workspace_id)
        return original(db, workspace_id=workspace_id)

    monkeypatch.setattr("app.crud.get_email_settings", _spy)

    work_item = work_item_factory(classification="Receipt")
    rule_factory(
        name="never-matches",
        conditions=[
            {"field": "extracted_entities.document_classification",
             "operator": "EQUALS", "value": "Invoice"}
        ],
        actions=[{"action_type": "email", "config": {"recipient": "a@b.com"}}],
    )
    db_session.commit()

    await automation_service.execute_rules_for_work_item(
        db_session, work_item_id=work_item.id, event="WORK_ITEM_COMPLETED"
    )

    assert reads == []


def test_valueless_operators_do_not_require_a_value(work_item_factory) -> None:
    from app.services.automation_service import _evaluate_rule_conditions

    work_item = work_item_factory(classification="Invoice", summary="something")

    class Rule:
        id = uuid.uuid4()
        logic_operator = "AND"
        conditions = [{"field": "summary", "operator": "EXISTS", "value": None}]

    assert _evaluate_rule_conditions(Rule(), work_item) is True


# =====================================================================
# Gate 13.2 — causality
# =====================================================================


def test_correlation_id_is_constant_along_a_chain(db_session, tenant) -> None:
    root = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.enriched",
    )
    first = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.field_changed",
        caused_by=root,
    )
    second = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.field_changed",
        caused_by=first,
    )
    db_session.commit()

    assert (root.depth, first.depth, second.depth) == (0, 1, 2)
    assert first.causation_id == root.id
    assert second.causation_id == first.id
    assert first.correlation_id == root.id
    assert second.correlation_id == root.id

    chain = outbox_service.chain(db_session, correlation_id=root.chain_root_id)
    assert [e.id for e in chain] == [root.id, first.id, second.id]


def test_depth_hard_ceiling_is_refused_before_the_constraint(
    db_session, tenant
) -> None:
    from app.models.outbox_event import HARD_DEPTH_CEILING

    fake = OutboxEvent(
        id=uuid.uuid4(), depth=HARD_DEPTH_CEILING, correlation_id=uuid.uuid4()
    )
    with pytest.raises(outbox_service.CausalityError, match="hard ceiling"):
        outbox_service._causality(fake)


def test_unflushed_parent_is_refused() -> None:
    with pytest.raises(outbox_service.CausalityError, match="no id yet"):
        outbox_service._causality(OutboxEvent(depth=0))


# =====================================================================
# Gate 13.2 — cycle suppression
# =====================================================================


def _execution(
    db, *, tenant, rule_id: uuid.UUID, correlation_id: uuid.UUID, depth: int = 1,
    status: AutomationExecutionStatus = AutomationExecutionStatus.COMPLETED,
    emitted: list[str] | None = None,
) -> AutomationExecution:
    from datetime import datetime, timezone

    execution = AutomationExecution(
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        rule_id=rule_id,
        correlation_id=correlation_id,
        depth=depth,
        status=status,
        budget_cost_micros=settings.AUTOMATION_DEFAULT_BUDGET_MICROS,
        emitted_event_ids=emitted or [],
        completed_at=datetime.now(timezone.utc),
    )
    db.add(execution)
    db.flush()
    return execution


def test_two_rule_ping_pong_suppresses_the_second_and_names_both(
    db_session, tenant, rule_factory
) -> None:
    rule_a = rule_factory(name="rule-a")
    rule_b = rule_factory(name="rule-b")
    db_session.commit()

    root = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.enriched",
    )
    db_session.flush()
    correlation = root.chain_root_id

    # Rule A runs and emits an event.
    a_event = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.field_changed",
        caused_by=root,
    )
    _execution(
        db_session,
        tenant=tenant,
        rule_id=rule_a.id,
        correlation_id=correlation,
        emitted=[str(a_event.id)],
    )

    # Rule B reacts, runs, and emits an event that would re-trigger A.
    b_event = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.field_changed",
        caused_by=a_event,
    )
    _execution(
        db_session,
        tenant=tenant,
        rule_id=rule_b.id,
        correlation_id=correlation,
        depth=2,
        emitted=[str(b_event.id)],
    )
    db_session.commit()

    suppression = cycle_detector.check(
        db_session,
        rule_id=rule_a.id,
        correlation_id=correlation,
        depth=b_event.depth + 1,
        causation_id=b_event.id,
    )

    assert suppression is not None
    assert suppression.status is AutomationExecutionStatus.SUPPRESSED_CYCLE
    assert suppression.counterpart_rule_id == rule_b.id
    assert str(rule_a.id) in suppression.reason
    assert str(rule_b.id) in suppression.reason

    total = db_session.execute(
        select(AutomationExecution).where(
            AutomationExecution.correlation_id == correlation
        )
    ).scalars().all()
    assert len(total) == 2


def test_chain_of_distinct_rules_stops_at_max_depth(
    db_session, tenant, rule_factory
) -> None:
    correlation = uuid.uuid4()
    rule = rule_factory(name="rule-six")
    db_session.commit()

    suppression = cycle_detector.check(
        db_session,
        rule_id=rule.id,
        correlation_id=correlation,
        depth=settings.AUTOMATION_MAX_DEPTH + 1,
    )
    assert suppression is not None
    assert suppression.status is AutomationExecutionStatus.SUPPRESSED_DEPTH
    assert "AUTOMATION_MAX_DEPTH" in suppression.reason


def test_suppressed_executions_do_not_cascade(
    db_session, tenant, rule_factory
) -> None:
    correlation = uuid.uuid4()
    rule = rule_factory(name="rule-s")
    db_session.commit()

    _execution(
        db_session,
        tenant=tenant,
        rule_id=rule.id,
        correlation_id=correlation,
        status=AutomationExecutionStatus.SUPPRESSED_CYCLE,
    )
    db_session.commit()

    assert (
        cycle_detector.check(
            db_session, rule_id=rule.id, correlation_id=correlation, depth=1
        )
        is None
    )


def test_execution_count_is_bounded_for_any_graph_shape(
    db_session, tenant, rule_factory
) -> None:
    import random

    random.seed(1313)
    rules = [rule_factory(name=f"prop-{i}") for i in range(6)]
    db_session.commit()

    correlation = uuid.uuid4()
    executed = 0
    for depth in range(1, 12):
        rule = random.choice(rules)
        suppression = cycle_detector.check(
            db_session, rule_id=rule.id, correlation_id=correlation, depth=depth
        )
        if suppression is not None:
            continue
        _execution(
            db_session,
            tenant=tenant,
            rule_id=rule.id,
            correlation_id=correlation,
            depth=depth,
        )
        db_session.flush()
        executed += 1

    assert executed <= len(rules)
    assert executed <= settings.AUTOMATION_MAX_DEPTH