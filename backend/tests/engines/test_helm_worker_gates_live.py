"""Helm's worker gates on real Postgres (build checklist, box 8; ARCHITECTURE.md §10).

    pytest tests/engines/test_helm_worker_gates_live.py -q

Every one of Helm's job types (`vocabulary.JOB_TYPES`) is queued while the organization is on
Business, with work to do: a ledger to rebuild, a book to plan, an approved action to run, a backtest,
the Monday briefing (made due) and the sample company. Then the organization changes, before the
worker runs, and each job must refuse or skip, saying why in its result, and change nothing:

* its plan no longer includes Helm (`capability.helm`): the sample company and Helm preview's
  backtest are the documented exceptions, and run;
* it is suspended, or archived;
* it has not paid (read-only for non-payment, dunning RESTRICTED).

The scheduled sweeps (the hourly plan, the nightly ledger rebuild, the briefing) carry no organization;
they leave such an organization out themselves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import func, select

from app.models.helm import HelmAction, HelmBacktest, HelmBook, HelmPlan, HelmPromise, HelmSettings
from app.models.organization import OrganizationStatus
from app.services import job_service
from app.services.helm import backtest, briefing, executor, hands, plan_store, service
from app.services.helm import vocabulary as v
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

TODAY = date.today()
#: The tenant gate's conditions (app/workers/tenant_gate.py); a plan without Helm is checked job by job below.
INACTIVE = ("suspended", "archived", "unpaid")


@pytest.fixture()
def stubs(monkeypatch, tmp_path):
    """The sample company's documents, stored locally with the platform's ML stubs."""
    from app.core.config import settings
    from app.core.storage import reset_storage_driver
    from app.services.embedding_service import embedding_service

    monkeypatch.setattr(settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    reset_storage_driver()
    yield
    reset_storage_driver()


@pytest.fixture()
def outbox(monkeypatch) -> list[dict[str, Any]]:
    """The Monday briefing is due whenever the sweep runs; what it would send is kept here."""
    sent: list[dict[str, Any]] = []
    monkeypatch.setattr(briefing, "due", lambda db, book, settings, now: "2026-W43")
    monkeypatch.setattr(hands, "send_mail", lambda db, **mail: sent.append(mail) or {"sent": True})
    return sent


def _who(db_session, tenant):
    return service.viewer(db_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                          role="OWNER")


@pytest.fixture()
def queued(db_session, tenant, test_sessions, stubs, outbox) -> dict[str, Any]:
    """A Business book with one job of every Helm type queued, each with real work to do."""
    from app.workers.handlers import register_all

    register_all(replace=True)
    put_on_plan(db_session, tenant.organization, "business")
    org, ws = tenant.organization.id, tenant.workspace
    who = _who(db_session, tenant)
    book = service.create_book(db_session, who, workspace_id=ws.id)
    db_session.get(HelmSettings, book.id).briefing_enabled = True
    item = factories.invoice(db_session, workspace=ws, organization_id=org, number="G-1", supplier="Gamma Metals",
                             amount=40_000, on=TODAY - timedelta(days=5))
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])  # the book's first ledger, while the plan still includes Helm

    # Queued while on Business; the worker reaches them after the organization changed.
    late = factories.invoice(db_session, workspace=ws, organization_id=org, number="G-2", supplier="Gamma Metals",
                             amount=60_000, on=TODAY - timedelta(days=1))
    job_service.enqueue(db_session, job_type=v.JOB_LEDGER_BUILD, organization_id=org,
                        payload={"workspace_id": str(ws.id), "organization_id": str(org), "reason": "a new document"},
                        idempotency_key=f"gates:{uuid.uuid4()}")
    plan_store.enqueue(db_session, book, triggered_by=v.TRIGGER_PERSON, delay_s=0, force=True)
    action = HelmAction(organization_id=org, book_id=book.id, workspace_id=ws.id, work_item_id=item.id,
                        kind=v.ACTION_HOLD_PAYMENT, idempotency_key=f"helm:gates:{uuid.uuid4().hex}",
                        state=v.ACTION_APPROVED, autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True,
                        amount_micros=40_000 * 1_000_000, currency="INR", title="Hold G-1",
                        explanation="A test hold.", params={"reason": "TEST"}, undoable=True,
                        approvals=[{"user_id": str(tenant.owner.user.id), "role": "OWNER",
                                    "at": datetime.now(timezone.utc).isoformat()}])
    db_session.add(action)
    db_session.flush()
    executor.enqueue(db_session, action)
    run = backtest.request(db_session, book=book, user_id=tenant.owner.user.id, months=3, specs=["pay_on_time"],
                           preview=False)
    job_service.enqueue(db_session, job_type=v.JOB_BRIEFING, payload={}, idempotency_key=f"gates:{uuid.uuid4()}")
    service.build_sample(db_session, who)
    db_session.commit()
    return {"book_id": book.id, "workspace_id": ws.id, "late_item": late.id, "action_id": action.id,
            "backtest_id": run.id}


def _change(db_session, tenant, monkeypatch, condition: str) -> None:
    if condition == "no_helm":
        put_on_plan(db_session, tenant.organization, "developer")
    elif condition == "suspended":
        tenant.organization.status = OrganizationStatus.SUSPENDED
    elif condition == "archived":
        tenant.organization.status = OrganizationStatus.ARCHIVED
    elif condition == "unpaid":
        from app.services.billing import dunning_service
        from app.services.billing.dunning_service import BillingAccessState

        unpaid = tenant.organization.id
        real = dunning_service.access_state
        monkeypatch.setattr(dunning_service, "access_state", lambda db, *, organization_id: (
            BillingAccessState.RESTRICTED if organization_id == unpaid else real(db, organization_id=organization_id)))
    db_session.commit()


def _why(result: Any) -> str:
    """The reason a job's result gives for doing nothing, in words."""
    result = result or {}
    reason = result.get("reason") if result.get("outcome") == "SKIPPED" else (result.get("skipped")
                                                                               or result.get("reason"))
    assert isinstance(reason, str) and len(reason.split()) >= 2, result
    return reason


def _run(job_type: str) -> list:
    runs = drain(only=[job_type])
    assert runs, f"no {job_type} job ran"
    return runs


def test_every_helm_job_type_is_gated(queued) -> None:
    from app.workers.tenant_gate import BILLING_GATED_JOB_TYPES, TENANT_ACTIVITY_JOB_TYPES

    assert set(v.JOB_TYPES) <= TENANT_ACTIVITY_JOB_TYPES
    assert set(v.JOB_TYPES) <= BILLING_GATED_JOB_TYPES


def _not_rebuilt(db_session, queued) -> None:
    db_session.expire_all()
    assert db_session.execute(select(HelmPromise).where(HelmPromise.work_item_id == queued["late_item"])
                              ).first() is None, "the ledger was rebuilt"


def _plans(db_session) -> int:
    return db_session.execute(select(func.count()).select_from(HelmPlan)).scalar_one()


def _not_executed(db_session, queued) -> HelmAction:
    db_session.expire_all()
    action = db_session.get(HelmAction, queued["action_id"])
    assert action.state in (v.ACTION_APPROVED, v.ACTION_BLOCKED) and action.executed_at is None, action.state
    return action


def _sample_books(db_session, tenant) -> list[HelmBook]:
    db_session.expire_all()
    return list(db_session.execute(select(HelmBook).where(HelmBook.organization_id == tenant.organization.id,
                                                          HelmBook.kind == v.BOOK_SAMPLE)).scalars())


@pytest.mark.parametrize("condition", INACTIVE)
def test_each_queued_job_refuses_in_words_and_changes_nothing(db_session, tenant, monkeypatch, queued, outbox,
                                                              condition) -> None:
    plans_before = _plans(db_session)
    _change(db_session, tenant, monkeypatch, condition)

    for run in _run(v.JOB_LEDGER_BUILD):
        _why(run.result)
    _not_rebuilt(db_session, queued)
    for run in _run(v.JOB_PLAN):
        _why(run.result)
    assert _plans(db_session) == plans_before
    [execute] = _run(v.JOB_EXECUTE)
    _why(execute.result)
    _not_executed(db_session, queued)
    [replay] = _run(v.JOB_BACKTEST)
    _why(replay.result)
    db_session.expire_all()
    assert db_session.get(HelmBacktest, queued["backtest_id"]).status != v.BACKTEST_DONE
    # The briefing is a sweep (no organization in its payload): it leaves this organization out itself.
    [weekly] = _run(v.JOB_BRIEFING)
    assert weekly.result == {"books": 0, "emails": 0} and outbox == []
    [sample] = _run(v.JOB_SAMPLE)
    _why(sample.result)
    assert _sample_books(db_session, tenant) == []


def test_on_business_the_same_jobs_do_their_work(db_session, tenant, queued, outbox) -> None:
    """The control: with nothing changed, every queued job does what it was queued for."""
    drain(only=[v.JOB_LEDGER_BUILD])
    db_session.expire_all()
    assert db_session.execute(select(HelmPromise).where(HelmPromise.work_item_id == queued["late_item"])).first()
    assert any(r.result.get("plan_id") for r in drain(only=[v.JOB_PLAN]))
    drain(only=[v.JOB_EXECUTE])
    db_session.expire_all()
    assert db_session.get(HelmAction, queued["action_id"]).state == v.ACTION_EXECUTED
    drain(only=[v.JOB_BACKTEST])
    db_session.expire_all()
    assert db_session.get(HelmBacktest, queued["backtest_id"]).status == v.BACKTEST_DONE
    [weekly] = drain(only=[v.JOB_BRIEFING])
    assert weekly.result["books"] == 1 and outbox
    [sample] = drain(only=[v.JOB_SAMPLE])
    assert sample.result.get("book_id")


# ---------------------------------------------------------------------------
# A plan without Helm (`capability.helm` lost before the worker ran)
# ---------------------------------------------------------------------------


def test_without_helm_ledger_and_plan_jobs_skip_in_words(db_session, tenant, monkeypatch, queued) -> None:
    plans_before = _plans(db_session)
    _change(db_session, tenant, monkeypatch, "no_helm")
    for run in _run(v.JOB_LEDGER_BUILD):
        assert "Helm" in _why(run.result)
    _not_rebuilt(db_session, queued)
    for run in _run(v.JOB_PLAN):
        assert "helm" in _why(run.result).lower()
    assert _plans(db_session) == plans_before


def test_without_helm_an_approved_action_is_blocked_and_its_job_says_why(db_session, tenant, monkeypatch,
                                                                       queued) -> None:
    _change(db_session, tenant, monkeypatch, "no_helm")
    [execute] = _run(v.JOB_EXECUTE)
    action = _not_executed(db_session, queued)
    assert action.state == v.ACTION_BLOCKED
    assert "does not include Helm" in _why(execute.result), execute.result


def test_without_helm_a_queued_backtest_does_not_run_and_says_why(db_session, tenant, monkeypatch, queued) -> None:
    """A Business backtest (up to 6 months, any number of mandates) queued before the plan lost Helm is not
    Helm preview's weekly backtest: it does not run, and the Time machine says why."""
    _change(db_session, tenant, monkeypatch, "no_helm")
    [replay] = _run(v.JOB_BACKTEST)
    assert "Helm" in _why(replay.result), replay.result
    db_session.expire_all()
    row = db_session.get(HelmBacktest, queued["backtest_id"])
    assert row.status == v.BACKTEST_FAILED and not row.result, (row.status, row.result)
    assert row.error and "Helm" in row.error


def test_without_helm_a_preview_backtest_still_runs(db_session, tenant, monkeypatch, queued) -> None:
    _change(db_session, tenant, monkeypatch, "no_helm")
    drain(only=[v.JOB_BACKTEST])  # the Business one
    who = _who(db_session, tenant)
    assert not who.helm
    requested = service.request_backtest(db_session, who, book_id=queued["book_id"], workspace_id=None, months=3,
                                         templates=["pay_on_time"])
    db_session.commit()
    assert requested["preview"] is True
    [replay] = drain(only=[v.JOB_BACKTEST])
    assert replay.result["status"] == v.BACKTEST_DONE, replay.result


def test_without_helm_the_briefing_is_not_sent(db_session, tenant, monkeypatch, queued, outbox) -> None:
    _change(db_session, tenant, monkeypatch, "no_helm")
    [weekly] = _run(v.JOB_BRIEFING)
    assert weekly.result == {"books": 0, "emails": 0} and outbox == []


def test_without_helm_the_sample_company_still_builds(db_session, tenant, monkeypatch, queued) -> None:
    _change(db_session, tenant, monkeypatch, "no_helm")
    [sample] = _run(v.JOB_SAMPLE)
    assert sample.result.get("book_id") and len(_sample_books(db_session, tenant)) == 1, sample.result


# ---------------------------------------------------------------------------
# The sweeps
# ---------------------------------------------------------------------------


def _sweep(db_session, tenant, monkeypatch, queued, outbox, condition: str) -> None:
    _change(db_session, tenant, monkeypatch, condition)
    plans_before = _plans(db_session)
    for job_type, payload in ((v.JOB_LEDGER_BUILD, {"full": True}), (v.JOB_PLAN, {}), (v.JOB_BRIEFING, {})):
        job_service.enqueue(db_session, job_type=job_type, payload=payload, idempotency_key=f"sweep:{uuid.uuid4()}")
    db_session.commit()
    full = [r for r in drain(only=[v.JOB_LEDGER_BUILD]) if r.payload.get("full")]
    assert [r.ok for r in full] == [True]
    _not_rebuilt(db_session, queued)
    sweep = [r for r in drain(only=[v.JOB_PLAN]) if not r.payload.get("book_id")]
    assert [r.result for r in sweep] == [{"planned": 0}]
    assert _plans(db_session) == plans_before
    drain(only=[v.JOB_BRIEFING])
    assert outbox == []


@pytest.mark.parametrize("condition", INACTIVE)
def test_the_sweeps_leave_an_inactive_organization_out(db_session, tenant, monkeypatch, queued, outbox,
                                                       condition) -> None:
    _sweep(db_session, tenant, monkeypatch, queued, outbox, condition)


def test_the_sweeps_leave_an_organization_without_helm_out(db_session, tenant, monkeypatch, queued, outbox) -> None:
    """The nightly ledger rebuild included: like the per-document rebuild, it is Helm's work, and only books
    that may plan (Helm on the plan, or the sample company) get it."""
    _sweep(db_session, tenant, monkeypatch, queued, outbox, "no_helm")


# ---------------------------------------------------------------------------
# A workspace archived or suspended, or the sample company deleted, before the worker ran
# ---------------------------------------------------------------------------

#: The workspace a book reads is no longer active (the platform's delete of a workspace archives it).
CLOSED = ("workspace_archived", "workspace_suspended")


def _close(db_session, tenant, condition: str) -> None:
    from app.models.workspace import WorkspaceStatus

    tenant.workspace.status = {"workspace_archived": WorkspaceStatus.ARCHIVED,
                               "workspace_suspended": WorkspaceStatus.SUSPENDED}[condition]
    db_session.commit()


def _plans_of(db_session, book_id: uuid.UUID) -> int:
    db_session.expire_all()
    return db_session.execute(select(func.count()).select_from(HelmPlan).where(HelmPlan.book_id == book_id)
                              ).scalar_one()


@pytest.mark.parametrize("condition", CLOSED)
def test_each_queued_job_of_a_closed_workspace_does_nothing_and_says_why(db_session, tenant, queued, outbox,
                                                                         condition) -> None:
    plans_before = _plans_of(db_session, queued["book_id"])
    _close(db_session, tenant, condition)

    for run in _run(v.JOB_LEDGER_BUILD):
        _why(run.result)
    _not_rebuilt(db_session, queued)
    for run in _run(v.JOB_PLAN):
        _why(run.result)
    assert _plans_of(db_session, queued["book_id"]) == plans_before
    [execute] = _run(v.JOB_EXECUTE)
    _why(execute.result)
    _not_executed(db_session, queued)
    [replay] = _run(v.JOB_BACKTEST)
    _why(replay.result)
    db_session.expire_all()
    assert db_session.get(HelmBacktest, queued["backtest_id"]).status != v.BACKTEST_DONE
    [weekly] = _run(v.JOB_BRIEFING)
    assert weekly.result == {"books": 0, "emails": 0} and outbox == []
    # The sample company is a workspace of its own: closing another one does not stop it.
    [sample] = _run(v.JOB_SAMPLE)
    assert sample.result.get("book_id"), sample.result


def _sample_hold(db_session, book: HelmBook, owner_id: uuid.UUID) -> HelmAction:
    """An approved hold on one of the sample company's invoices, queued to run."""
    from app.models.work_item import WorkItem

    item = db_session.execute(select(WorkItem).where(WorkItem.workspace_id == book.workspace_id)
                              .order_by(WorkItem.original_filename).limit(1)).scalar_one()
    action = HelmAction(organization_id=book.organization_id, book_id=book.id, workspace_id=book.workspace_id,
                        work_item_id=item.id, kind=v.ACTION_HOLD_PAYMENT,
                        idempotency_key=f"helm:gates:{uuid.uuid4().hex}", state=v.ACTION_APPROVED,
                        autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True,
                        amount_micros=1_000 * 1_000_000, currency="INR", title="Hold a sample invoice",
                        explanation="A test hold.", params={"reason": "TEST"}, undoable=True,
                        approvals=[{"user_id": str(owner_id), "role": "OWNER",
                                    "at": datetime.now(timezone.utc).isoformat()}])
    db_session.add(action)
    db_session.flush()
    executor.enqueue(db_session, action)
    return action


def test_jobs_queued_for_the_sample_company_do_nothing_once_it_is_deleted(db_session, tenant, queued) -> None:
    [built] = _run(v.JOB_SAMPLE)
    book = db_session.get(HelmBook, uuid.UUID(built.result["book_id"]))
    drain(only=[v.JOB_PLAN, v.JOB_LEDGER_BUILD])  # everything the build asked for, while it still exists
    # Queued while the sample company exists; the worker reaches them after "Delete the sample company".
    plan_store.enqueue(db_session, book, triggered_by=v.TRIGGER_PERSON, delay_s=0, force=True)
    hold = _sample_hold(db_session, book, tenant.owner.user.id)
    run = backtest.request(db_session, book=book, user_id=tenant.owner.user.id, months=3, specs=["pay_on_time"],
                           preview=False)
    db_session.commit()
    hold_id, backtest_id, book_id = hold.id, run.id, book.id
    plans_before = _plans_of(db_session, book_id)
    service.delete_sample(db_session, _who(db_session, tenant))
    db_session.commit()

    for run in drain(only=[v.JOB_PLAN]):
        if run.payload.get("book_id") == str(book_id):
            _why(run.result)
    assert _plans_of(db_session, book_id) == plans_before
    for run in drain(only=[v.JOB_EXECUTE]):
        if run.payload.get("action_id") == str(hold_id):
            _why(run.result)
    db_session.expire_all()
    held = db_session.get(HelmAction, hold_id)
    assert held.state != v.ACTION_EXECUTED and held.executed_at is None, held.state
    for run in drain(only=[v.JOB_BACKTEST]):
        if run.payload.get("backtest_id") == str(backtest_id):
            _why(run.result)
    db_session.expire_all()
    assert db_session.get(HelmBacktest, backtest_id).status != v.BACKTEST_DONE


def test_a_job_whose_book_was_removed_finishes_cleanly_and_says_why(db_session, queued) -> None:
    """The book's rows are gone (its actions and backtests with it) before the worker reaches its jobs: each
    job ends in words, never an error the queue would retry."""
    from sqlalchemy import delete

    db_session.execute(delete(HelmBook).where(HelmBook.id == queued["book_id"]))
    db_session.commit()
    for job_type in (v.JOB_LEDGER_BUILD, v.JOB_PLAN, v.JOB_EXECUTE, v.JOB_BACKTEST):
        for run in _run(job_type):
            _why(run.result)


def test_gated_jobs_succeed_once_and_are_not_retried(db_session, tenant, monkeypatch, queued) -> None:
    """A refusal is a decision, not a failure: each job succeeds at its first attempt, and nothing it queued
    keeps coming back."""
    from app.models.job import Job, JobStatus

    _change(db_session, tenant, monkeypatch, "suspended")
    for _ in range(2):
        drain(only=list(v.JOB_TYPES))
    db_session.expire_all()
    jobs = db_session.execute(select(Job).where(Job.organization_id == tenant.organization.id,
                                                Job.job_type.in_(v.JOB_TYPES))).scalars().all()
    assert jobs and all(j.status == JobStatus.SUCCEEDED and j.attempts == 1 for j in jobs), \
        [(j.job_type, j.status, j.attempts) for j in jobs]


# ---------------------------------------------------------------------------
# Another organization is never touched, and still gets its own work
# ---------------------------------------------------------------------------


@pytest.fixture()
def other(db_session, tenant, queued) -> dict[str, Any]:
    """Another organization on Business, with a Helm book and a document its ledger has not read yet."""
    from app.models.organization import Organization

    org = db_session.get(Organization, tenant.foreign_workspace.organization_id)
    put_on_plan(db_session, org, "business")
    who = service.viewer(db_session, organization_id=org.id, user_id=tenant.other_org_member.user.id, role="OWNER")
    book = service.create_book(db_session, who, workspace_id=tenant.foreign_workspace.id)
    item = factories.invoice(db_session, workspace=tenant.foreign_workspace, organization_id=org.id, number="B-1",
                             supplier="Beta Supplies", amount=25_000, on=TODAY - timedelta(days=3))
    db_session.commit()
    return {"book_id": book.id, "item": item.id, "organization_id": org.id}


@pytest.mark.parametrize("condition", (*INACTIVE, "no_helm", *CLOSED))
def test_the_sweeps_still_serve_another_organization(db_session, tenant, monkeypatch, queued, outbox, other,
                                                     condition) -> None:
    if condition in CLOSED:
        _close(db_session, tenant, condition)
    else:
        _change(db_session, tenant, monkeypatch, condition)
    gated_plans = _plans_of(db_session, queued["book_id"])
    for job_type, payload in ((v.JOB_LEDGER_BUILD, {"full": True}), (v.JOB_PLAN, {})):
        job_service.enqueue(db_session, job_type=job_type, payload=payload, idempotency_key=f"sweep:{uuid.uuid4()}")
    db_session.commit()
    drain(only=[v.JOB_LEDGER_BUILD])
    drain(only=[v.JOB_PLAN])

    db_session.expire_all()
    assert db_session.execute(select(HelmPromise).where(HelmPromise.work_item_id == other["item"])).first(), \
        "the other organization's ledger was not built"
    assert _plans_of(db_session, other["book_id"]) >= 1, "the other organization's book was not planned"
    _not_rebuilt(db_session, queued)
    assert _plans_of(db_session, queued["book_id"]) == gated_plans
    # Nothing either tenant's work wrote landed in the other's rows.
    for model in (HelmPlan, HelmPromise, HelmAction):
        stray = db_session.execute(select(func.count()).select_from(model).where(
            model.organization_id == other["organization_id"],
            model.book_id == queued["book_id"] if hasattr(model, "book_id") else
            model.workspace_id == tenant.workspace.id)).scalar_one()
        assert stray == 0, model.__name__
