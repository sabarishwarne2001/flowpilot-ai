"""Helm's five jobs (LIGHT profile). Each is idempotent and gated:

`helm.ledger_build`  {workspace_id, organization_id}: rebuild that workspace's Promise ledger (a diff),
                     then ask each book that reads it for a plan (debounced). {"full": true}: the nightly
                     rebuild of every ledger some book reads.
`helm.plan`          {book_id}: plan one book (one run per organization at a time). {}: the hourly sweep
                     over every book.
`helm.execute`       {action_id}: run one approved action once, after the Guard re-checks it.
`helm.backtest`      {backtest_id}: replay a past period (the Time machine).
`helm.briefing`      hourly: the Monday 09:00 briefing in each organization's time zone.

A job for an organization whose plan lost `capability.helm` does nothing (the sample company and
preview backtests excepted); archived, suspended, unpaid and ended-trial organizations are skipped
by `tenant_gate` before the handler runs, and the sweeps filter them themselves.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy import select

logger = logging.getLogger("app.workers.handlers.helm")


def _uuid(value: Any) -> uuid.UUID:
    return uuid.UUID(str(value))


def _org_active(db: Any, organization_id: uuid.UUID) -> bool:
    from app.workers.tenant_gate import inactive_reason, unpaid_reason

    return inactive_reason(db, organization_id=organization_id) is None and \
        unpaid_reason(db, organization_id=organization_id) is None


def _books_reading(db: Any, workspace_id: uuid.UUID):
    from app.models.helm import HelmBook
    from app.models.workspace import Workspace
    from app.services.helm import vocabulary as v

    workspace = db.get(Workspace, workspace_id)
    if workspace is None:
        return []
    reads = HelmBook.workspace_id == workspace_id
    if not workspace.is_sample:
        reads = reads | (HelmBook.kind == v.BOOK_ORGANIZATION)
    return db.execute(select(HelmBook).where(
        HelmBook.organization_id == workspace.organization_id, HelmBook.status == v.BOOK_ACTIVE, reads,
    )).scalars().all()


def _closed(db: Any, *, workspace_id: uuid.UUID) -> Any:
    from app.workers.tenant_gate import inactive_reason

    return inactive_reason(db, workspace_id=workspace_id)


def _skipped(job_type: str, payload: dict[str, Any], reason: str) -> dict[str, Any]:
    """Nothing to do for this tenant: said in the job's result and logged, as the tenant gate does."""
    logger.info("jobs.skipped_inactive_tenant",
                extra={"job_type": job_type, "job_id": payload.get("job_id"), "reason": reason})
    return {"skipped": reason}


def _may_plan(db: Any, book: Any) -> bool:
    from app.services.helm import gate
    from app.services.helm import vocabulary as v

    return book.kind == v.BOOK_SAMPLE or gate.helm_held(db, book.organization_id)


def handle_ledger_build(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.helm import ledger_store, plan_store, value
    from app.services.helm import vocabulary as v

    if payload.get("full"):
        rebuilt, failed = 0, 0
        with SessionLocal() as db:
            workspace_ids = ledger_store.workspace_ids_with_books(db)
        for workspace_id in workspace_ids:
            with SessionLocal() as db:
                from app.models.workspace import Workspace

                workspace = db.get(Workspace, workspace_id)
                if workspace is None or not _org_active(db, workspace.organization_id) or \
                        _closed(db, workspace_id=workspace_id) is not None:
                    continue
                # Like the rebuild a document asks for: only for a book that may plan.
                books = [b for b in _books_reading(db, workspace_id) if _may_plan(db, b)]
                if not books:
                    continue
                try:
                    ledger_store.rebuild_workspace(db, workspace_id=workspace_id)
                    for book in books:
                        plan_store.enqueue(db, book, triggered_by=v.TRIGGER_SCHEDULE)
                    db.commit()
                    rebuilt += 1
                except Exception:  # noqa: BLE001 - one tenant's bad data must not stop the others
                    db.rollback()
                    failed += 1
                    logger.exception("helm.full_rebuild_failed", extra={"workspace_id": str(workspace_id)})
        if failed:
            raise RuntimeError(f"{failed} Helm ledger(s) failed to rebuild ({rebuilt} rebuilt); see the log")
        return {"rebuilt": rebuilt}

    workspace_id = _uuid(payload["workspace_id"])
    with SessionLocal() as db:
        books = [b for b in _books_reading(db, workspace_id) if _may_plan(db, b)]
        if not books:
            return {"skipped": "no Helm book reads this workspace on a plan that includes Helm"}
        result = ledger_store.rebuild_workspace(db, workspace_id=workspace_id)
        for book in books:
            value.sweep(db, book)
            if result.changed:
                plan_store.enqueue(db, book, triggered_by=v.TRIGGER_LEDGER)
        db.commit()
        return result.as_dict()


def handle_plan(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.helm import HelmBook
    from app.services.helm import gate, plan_store
    from app.services.helm import vocabulary as v

    if not payload.get("book_id"):
        with SessionLocal() as db:
            books = db.execute(select(HelmBook.id, HelmBook.organization_id)
                               .where(HelmBook.status == v.BOOK_ACTIVE)).all()
        planned = 0
        for book_id, organization_id in books:
            with SessionLocal() as db:
                book = db.get(HelmBook, book_id)
                if book is None or not _org_active(db, organization_id) or gate.book_closed(db, book) or \
                        not _may_plan(db, book):
                    continue
                try:
                    if plan_store.run(db, book, triggered_by=v.TRIGGER_SCHEDULE) is not None:
                        planned += 1
                    db.commit()
                except Exception:  # noqa: BLE001
                    db.rollback()
                    logger.exception("helm.scheduled_plan_failed", extra={"book_id": str(book_id)})
        return {"planned": planned}

    with SessionLocal() as db:
        book = db.get(HelmBook, _uuid(payload["book_id"]))
        closed = gate.book_closed(db, book)
        if closed is not None:
            return _skipped(v.JOB_PLAN, payload, closed)
        if not _may_plan(db, book):
            return {"skipped": "capability.helm not held"}
        plan = plan_store.run(db, book, triggered_by=payload.get("triggered_by") or v.TRIGGER_LEDGER,
                              force=bool(payload.get("force")))
        db.commit()
        if plan is None:
            return {"skipped": "another plan run holds this organization"}
        return {"plan_id": str(plan.id), "version": plan.version, "status": plan.solver_status}


def handle_execute(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.models.helm import HelmAction, HelmBook
    from app.services.helm import executor, gate
    from app.services.helm import vocabulary as v

    with SessionLocal() as db:
        waiting = db.get(HelmAction, _uuid(payload["action_id"]))
        if waiting is not None:
            closed = gate.book_closed(db, db.get(HelmBook, waiting.book_id), workspace_id=waiting.workspace_id)
            if closed is not None:
                return _skipped(v.JOB_EXECUTE, payload, closed)
        action = executor.execute(db, action_id=_uuid(payload["action_id"]))
        db.commit()
        if action is None:
            return {"skipped": "the action is not waiting to run (already ran, or not approved)"}
        out = {"action_id": str(action.id), "state": action.state}
        if action.state == v.ACTION_BLOCKED:  # why, in the Guard's words (the plan lost Helm, a limit...)
            checks = (action.guard or {}).get("checks") or []
            failed = [c.get("detail") for c in checks if c.get("result") == v.CHECK_FAIL]
            out["reason"] = "; ".join(d for d in failed if d) or "the Guard blocked it"
        elif action.state == v.ACTION_FAILED:
            out["reason"] = (action.result or {}).get("message") or "the action could not be carried out"
        return out


def handle_backtest(payload: dict[str, Any]) -> dict[str, Any]:
    from app.db.session import SessionLocal
    from app.services.helm import backtest

    with SessionLocal() as db:
        result = backtest.run(db, backtest_id=_uuid(payload["backtest_id"]))
        db.commit()
        return result


def handle_sample(payload: dict[str, Any]) -> dict[str, Any]:
    """Build the organization's sample company (idempotent: one per organization)."""
    from app.db.session import SessionLocal
    from app.services.helm import sample_company

    with SessionLocal() as db:
        organization_id = _uuid(payload["organization_id"])
        if not _org_active(db, organization_id):
            return {"skipped": "organization not active"}
        user = payload.get("user_id")
        book = sample_company.build(db, organization_id=organization_id, actor_user_id=_uuid(user) if user else None)
        db.commit()
        return {"book_id": str(book.id), "workspace_id": str(book.workspace_id)}


def handle_briefing(payload: dict[str, Any]) -> dict[str, Any]:
    from app.services.helm import briefing

    return briefing.sweep()


__all__ = ["handle_backtest", "handle_briefing", "handle_execute", "handle_ledger_build", "handle_plan", "handle_sample"]
