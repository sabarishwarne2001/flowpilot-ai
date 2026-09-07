"""Anchor regression tests for the three core idempotency contracts."""

from __future__ import annotations

import threading
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.idempotent_insert import insert_or_get

pytestmark = pytest.mark.integration


@pytest.fixture
def automation_rule(db_session, rule_factory):
    rule = rule_factory(name="anchor-contract-rule")
    db_session.flush()
    return rule


@pytest.fixture
def outbox_event(db_session, tenant):
    from app.models.outbox_event import OutboxEvent, OutboxEventStatus

    event = OutboxEvent(
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.completed",
        payload={},
        status=OutboxEventStatus.PENDING,
        depth=0,
    )
    db_session.add(event)
    db_session.flush()
    return event


@pytest.fixture
def second_db_session(test_database):
    from app.db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TestCreateExecutionReplayContract:
    def test_first_call_reports_created_true(
        self, db_session, tenant, automation_rule, outbox_event
    ) -> None:
        from app.services.automation import executor

        correlation_id = getattr(outbox_event, "chain_root_id", None) or outbox_event.id

        execution, created = executor.create_execution(
            db_session,
            rule=automation_rule,
            organization_id=tenant.organization.id,
            workspace_id=automation_rule.workspace_id,
            correlation_id=correlation_id,
            depth=0,
            work_item_id=None,
            outbox_event_id=outbox_event.id,
            causation_id=None,
        )

        assert created is True
        assert execution.id is not None
        assert execution.outbox_event_id == outbox_event.id

    def test_replay_reports_created_false_and_returns_the_same_row(
        self, db_session, tenant, automation_rule, outbox_event
    ) -> None:
        from app.services.automation import executor

        correlation_id = getattr(outbox_event, "chain_root_id", None) or outbox_event.id

        kwargs = dict(
            rule=automation_rule,
            organization_id=tenant.organization.id,
            workspace_id=automation_rule.workspace_id,
            correlation_id=correlation_id,
            depth=0,
            work_item_id=None,
            outbox_event_id=outbox_event.id,
            causation_id=None,
        )

        first, first_created = executor.create_execution(db_session, **kwargs)
        db_session.flush()
        first_id = first.id

        second, second_created = executor.create_execution(db_session, **kwargs)

        assert first_created is True
        assert second_created is False
        assert second.id == first_id

    def test_replay_creates_no_second_row(
        self, db_session, tenant, automation_rule, outbox_event
    ) -> None:
        from app.models.automation_execution import AutomationExecution
        from app.services.automation import executor

        correlation_id = getattr(outbox_event, "chain_root_id", None) or outbox_event.id

        kwargs = dict(
            rule=automation_rule,
            organization_id=tenant.organization.id,
            workspace_id=automation_rule.workspace_id,
            correlation_id=correlation_id,
            depth=0,
            work_item_id=None,
            outbox_event_id=outbox_event.id,
            causation_id=None,
        )
        executor.create_execution(db_session, **kwargs)
        db_session.flush()
        executor.create_execution(db_session, **kwargs)
        db_session.flush()

        count = db_session.execute(
            select(AutomationExecution)
            .where(AutomationExecution.rule_id == automation_rule.id)
            .where(AutomationExecution.outbox_event_id == outbox_event.id)
        ).scalars().all()

        assert len(count) == 1

    def test_null_outbox_event_is_never_deduplicated(
        self, db_session, tenant, automation_rule
    ) -> None:
        from app.services.automation import executor

        correlation_id = uuid.uuid4()

        kwargs = dict(
            rule=automation_rule,
            organization_id=tenant.organization.id,
            workspace_id=automation_rule.workspace_id,
            correlation_id=correlation_id,
            depth=0,
            work_item_id=None,
            outbox_event_id=None,
            causation_id=None,
        )

        first, first_created = executor.create_execution(db_session, **kwargs)
        db_session.flush()
        second, second_created = executor.create_execution(db_session, **kwargs)
        db_session.flush()

        assert first_created is True
        assert second_created is True
        assert first.id != second.id

    def test_return_shape_is_a_two_tuple(self) -> None:
        import inspect
        from app.services.automation import executor

        signature = inspect.signature(executor.create_execution)
        annotation = str(signature.return_annotation)
        assert "tuple" in annotation.lower()


class TestInsertOrGetRaceContract:
    def test_returns_existing_row_without_inserting(
        self, db_session, tenant
    ) -> None:
        from app.models.job import Job

        key = f"anchor-test:{uuid.uuid4()}"

        def _lookup():
            return db_session.execute(
                select(Job)
                .where(Job.job_type == "usage.rollup")
                .where(Job.idempotency_key == key)
                .limit(1)
            ).scalar_one_or_none()

        first, created_first = insert_or_get(
            db_session,
            instance=Job(
                job_type="usage.rollup",
                payload={},
                organization_id=tenant.organization.id,
                idempotency_key=key,
            ),
            lookup=_lookup,
            label="test.job",
        )
        db_session.flush()

        second, created_second = insert_or_get(
            db_session,
            instance=Job(
                job_type="usage.rollup",
                payload={},
                organization_id=tenant.organization.id,
                idempotency_key=key,
            ),
            lookup=_lookup,
            label="test.job",
        )

        assert created_first is True
        assert created_second is False
        assert second.id == first.id

    def test_losing_race_does_not_poison_the_transaction(
        self, db_session, second_db_session, tenant
    ) -> None:
        from app.models.job import Job

        key = f"anchor-race:{uuid.uuid4()}"
        canary_key = f"anchor-canary:{uuid.uuid4()}"

        canary = Job(
            job_type="usage.rollup",
            payload={"canary": True},
            organization_id=tenant.organization.id,
            idempotency_key=canary_key,
        )
        db_session.add(canary)
        db_session.flush()

        second_db_session.add(
            Job(
                job_type="usage.rollup",
                payload={},
                organization_id=tenant.organization.id,
                idempotency_key=key,
            )
        )
        second_db_session.commit()

        def _lookup():
            return db_session.execute(
                select(Job)
                .where(Job.job_type == "usage.rollup")
                .where(Job.idempotency_key == key)
                .limit(1)
            ).scalar_one_or_none()

        row, created = insert_or_get(
            db_session,
            instance=Job(
                job_type="usage.rollup",
                payload={},
                organization_id=tenant.organization.id,
                idempotency_key=key,
            ),
            lookup=_lookup,
            label="test.race",
        )

        assert created is False
        assert row.idempotency_key == key

        surviving = db_session.execute(
            select(Job).where(Job.idempotency_key == canary_key)
        ).scalar_one_or_none()
        assert surviving is not None
        db_session.commit()

    def test_unrelated_integrity_error_is_reraised(
        self, db_session, tenant
    ) -> None:
        from app.models.job import Job

        bad = Job(
            job_type="usage.rollup",
            payload={},
            organization_id=uuid.uuid4(),
            idempotency_key=f"anchor-fk:{uuid.uuid4()}",
        )

        with pytest.raises(IntegrityError):
            insert_or_get(
                db_session,
                instance=bad,
                lookup=lambda: None,
                label="test.unrelated",
            )

        db_session.rollback()


class TestDropOrphanEventsContract:
    def test_live_events_pass_through_untouched(
        self, db_session, tenant
    ) -> None:
        from app.services import rollup_service

        events = [
            self._claimed_event(tenant.organization.id, "ocr.page"),
            self._claimed_event(tenant.organization.id, "llm.tokens"),
        ]

        live, orphaned = rollup_service.drop_orphan_events(db_session, events=events)
        assert len(live) == 2
        assert orphaned == []

    def test_orphaned_events_are_split_out(
        self, db_session, tenant
    ) -> None:
        from app.services import rollup_service

        dead_org = uuid.uuid4()
        events = [
            self._claimed_event(tenant.organization.id, "ocr.page"),
            self._claimed_event(dead_org, "ocr.page"),
            self._claimed_event(dead_org, "llm.tokens"),
        ]

        live, orphaned = rollup_service.drop_orphan_events(db_session, events=events)
        assert len(live) == 1
        assert len(orphaned) == 2
        assert {event.organization_id for event in orphaned} == {dead_org}

    def test_fold_survives_a_wholly_orphaned_batch(
        self, db_session, tenant
    ) -> None:
        from app.services import rollup_service

        dead_org = uuid.uuid4()
        events = [self._claimed_event(dead_org, "ocr.page") for _ in range(3)]
        result = rollup_service.RollupResult()

        touched = rollup_service.fold(
            db_session, events=events, now=_utcnow(), result=result
        )
        assert touched == set()

    def test_orphan_drop_is_logged_with_the_missing_org(
        self, db_session, tenant, caplog
    ) -> None:
        from app.services import rollup_service

        dead_org = uuid.uuid4()
        events = [self._claimed_event(dead_org, "ocr.page")]

        with caplog.at_level("WARNING"):
            live, orphaned = rollup_service.drop_orphan_events(db_session, events=events)

        assert len(orphaned) == 1

    def test_empty_batch_is_a_no_op(self, db_session) -> None:
        from app.services import rollup_service

        live, orphaned = rollup_service.drop_orphan_events(db_session, events=[])
        assert live == []
        assert orphaned == []

    @staticmethod
    def _claimed_event(organization_id: uuid.UUID, event_type: str):
        from app.services.rollup_service import ClaimedEvent

        return ClaimedEvent(
            id=uuid.uuid4(),
            seq=1,
            organization_id=organization_id,
            workspace_id=None,
            event_type=event_type,
            provider="groq",
            model="openai/gpt-oss-20b",
            price_book_id=None,
            unit_price_micros=None,
            quantity=1,
            cost_micros=1000,
            cost_basis_micros=None,
            cost_basis_source=None,
            occurred_at=_utcnow() - timedelta(minutes=5),
            estimated=False,
        )
