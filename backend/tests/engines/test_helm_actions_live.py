"""Releasing Helm's holds on real Postgres: a released hold that is undone is held again for the same
reason; a person who settles a missing-PO task DONE ends the hold for good."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.models.helm import HelmAction, HelmBook, HelmSettings, HelmSettlement, HelmTask
from app.services.helm import executor, settlements, tasks
from app.services.helm import vocabulary as v
from app.services.helm.guard import Actor
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
    db_session.flush()
    return book


def _action(db_session, book, *, kind: str, work_item_id=None, params=None) -> HelmAction:
    now = datetime.now(timezone.utc)
    action = HelmAction(organization_id=book.organization_id, book_id=book.id, workspace_id=book.workspace_id,
                        work_item_id=work_item_id, kind=kind, idempotency_key=f"helm:test:{uuid.uuid4().hex}",
                        state=v.ACTION_APPROVED, autonomy=v.AUTONOMY_PROPOSE, needs_approval=True, guard_passed=True,
                        title=f"Test {kind}", explanation="A test action.", params=params or {}, undoable=True,
                        approvals=[{"role": "OWNER", "at": now.isoformat()}])
    db_session.add(action)
    db_session.flush()
    return action


def test_undoing_a_released_hold_holds_it_again_for_the_same_reason(db_session, tenant):
    book = _book(db_session, tenant)
    item = factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id,
                             number="D-1", supplier="Delta", amount=900, on=date.today() - timedelta(days=4))
    settlements.set_state(db_session, organization_id=book.organization_id, workspace_id=book.workspace_id,
                          work_item_id=item.id, state=v.SETTLEMENT_HELD, source=v.SOURCE_HELM,
                          hold_reason="Probable duplicate", evidence={"reason": "PROBABLE_DUPLICATE"})
    action = _action(db_session, book, kind=v.ACTION_RELEASE_HOLD, work_item_id=item.id,
                     params={"reason": "PROBABLE_DUPLICATE"})
    executor.execute(db_session, action_id=action.id)
    db_session.refresh(action)
    assert action.state == v.ACTION_EXECUTED, action.result
    assert settlements.get(db_session, item.id).state == v.SETTLEMENT_UNPAID

    executor.undo(db_session, action=action, actor=Actor(user_id=str(tenant.owner.user.id), role="OWNER"))
    row = db_session.execute(select(HelmSettlement).where(HelmSettlement.work_item_id == item.id)).scalar_one()
    assert row.state == v.SETTLEMENT_HELD
    assert (row.evidence or {}).get("reason") == "PROBABLE_DUPLICATE"


def test_a_missing_po_task_settled_done_ends_the_hold_for_good(db_session, tenant):
    book = _book(db_session, tenant)
    item = factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id,
                             number="P-1", supplier="Papyrus", amount=90_000, on=date.today() - timedelta(days=2))
    settlements.set_state(db_session, organization_id=book.organization_id, workspace_id=book.workspace_id,
                          work_item_id=item.id, state=v.SETTLEMENT_HELD, source=v.SOURCE_HELM,
                          hold_reason="No matching PO", evidence={"reason": "NO_MATCHED_PO"})
    task = HelmTask(organization_id=book.organization_id, workspace_id=book.workspace_id, book_id=book.id,
                    kind=v.TASK_REVIEW_PO, work_item_id=item.id, title="Find the PO", body="", severity="MEDIUM",
                    status=v.TASK_OPEN)
    db_session.add(task)
    db_session.flush()
    person = tenant.owner.user.id
    tasks.resolve(db_session, task_id=task.id, workspace_id=book.workspace_id, verdict="DONE",
                  note="Agreed by phone; no PO for this one", actor_user_id=person)
    row = settlements.get(db_session, item.id)
    assert row.state == v.SETTLEMENT_UNPAID
    assert row.evidence.get("waived") == ["NO_MATCHED_PO"]
    # Later moves keep the person's waiver.
    settlements.set_state(db_session, organization_id=book.organization_id, workspace_id=book.workspace_id,
                          work_item_id=item.id, state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM,
                          run_date=date.today() + timedelta(days=2), evidence={"certificate_id": "x"})
    assert settlements.get(db_session, item.id).evidence.get("waived") == ["NO_MATCHED_PO"]
