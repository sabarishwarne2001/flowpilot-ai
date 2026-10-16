"""A plan decides from one consistent picture of the ledger (brief §4.2, "reads are consistent
snapshots"): it never reads half of a ledger rebuild. A snapshot waits for a rebuild that is
being written, and a rebuild waits for a plan that is reading, on real Postgres sessions."""

from __future__ import annotations

import threading
from datetime import date, timedelta

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.models.helm import HelmBook, HelmSettings
from app.services.helm import ledger_store, snapshot_store
from app.services.helm import vocabulary as v
from tests.security.plans import put_on_plan
from tests.services.helm import factories


def _book(db_session, tenant) -> HelmBook:
    put_on_plan(db_session, tenant.organization, "business")
    book = HelmBook(organization_id=tenant.organization.id, workspace_id=tenant.workspace.id, kind=v.BOOK_WORKSPACE,
                    name="Engineering", status=v.BOOK_ACTIVE)
    db_session.add(book)
    db_session.flush()
    db_session.add(HelmSettings(book_id=book.id, organization_id=book.organization_id,
                                identities=[{"name": "Meridian Wholesale", "tax_id": None}]))
    factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id, number="C-1",
                      supplier="Gamma", amount=1500, on=date.today() - timedelta(days=3))
    db_session.commit()
    return book


def _in_thread(work) -> tuple[threading.Thread, threading.Event, list[BaseException]]:
    done, errors = threading.Event(), []

    def body():
        try:
            work()
        except BaseException as exc:  # noqa: BLE001 - reported to the test
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=body, daemon=True)
    thread.start()
    return thread, done, errors


def test_a_snapshot_waits_for_a_ledger_rebuild_being_written(db_session, tenant):
    book = _book(db_session, tenant)
    rebuilding = SessionLocal()
    try:
        # A rebuild holds its workspace's ledger lock until it commits.
        rebuilding.execute(select(func.pg_advisory_xact_lock(
            func.hashtextextended(f"helm-ledger:{tenant.workspace.id}", 0))))

        def snapshot():
            with SessionLocal() as db:
                snapshot_store.take(db, db.get(HelmBook, book.id))
                db.rollback()

        thread, done, errors = _in_thread(snapshot)
        assert not done.wait(1.5), "the snapshot read the ledger while a rebuild was half written"
        rebuilding.commit()
        assert done.wait(15), "the snapshot never finished once the rebuild committed"
        thread.join(5)
        assert not errors, errors
    finally:
        rebuilding.close()


def test_a_rebuild_waits_for_a_plan_that_is_reading(db_session, tenant):
    book = _book(db_session, tenant)
    planning = SessionLocal()
    try:
        snapshot_store.take(planning, planning.get(HelmBook, book.id))  # the plan's transaction stays open

        def rebuild():
            with SessionLocal() as db:
                ledger_store.rebuild_workspace(db, workspace_id=tenant.workspace.id)
                db.commit()

        thread, done, errors = _in_thread(rebuild)
        assert not done.wait(1.5), "a rebuild changed the ledger under a plan that was still reading it"
        planning.rollback()
        assert done.wait(15), "the rebuild never ran once the plan finished"
        thread.join(5)
        assert not errors, errors
    finally:
        planning.close()
