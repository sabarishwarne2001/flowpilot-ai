"""Team management and the audit trail, live.

* Role changes: only an OWNER may grant ADMIN. An ADMIN who tries is refused
  promptly (403) and the refusal is audited. That refusal used to HANG the
  request forever: the organization row was locked FOR UPDATE, and the
  independent audit write's foreign-key check waited on that lock while the
  request waited on the write. A MEMBER may change nobody's role; nobody may
  demote the last OWNER.
* Removal: deactivating a member of a PAID organization records
  billing.seat_removed (the event F-051 showed the database refused) and the
  removed person loses access at once.
* Every one of those actions writes an audit row naming the actor, and the
  audit log is append-only in the database itself: UPDATE, DELETE and
  TRUNCATE on audit_logs are refused even to the application's own role.
"""

from __future__ import annotations

import time
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.models.audit_log import AuditLog
from app.models.billing_account import BillingAccount
from app.models.organization import OrganizationMember
from app.models.outbox_event import OutboxEvent
from tests.conftest import TestSessionLocal
from tests.engines.conftest import Engines


def _membership(engines: Engines, persona) -> str:
    engines.refresh()
    return str(engines.db.execute(select(OrganizationMember.id).where(
        OrganizationMember.organization_id == engines.org,
        OrganizationMember.user_id == persona.user.id)).scalar_one())


def test_role_changes_removal_and_their_audit_trail(engines: Engines) -> None:
    customer = f"cus_test_{uuid.uuid4().hex[:10]}"
    engines.db.add(BillingAccount(organization_id=engines.org, stripe_customer_id=customer,
                                  gateway_customer_id=customer, currency="USD", billing_email="billing@acme.example"))
    engines.db.commit()
    t = engines.tenant

    contributor = _membership(engines, t.contributor)
    refused = engines.patch(f"/members/{contributor}", {"role": "ADMIN"}, org=True, as_user=t.viewer)
    assert refused.status_code == 403, refused.text
    started = time.monotonic()
    by_admin = engines.patch(f"/members/{contributor}", {"role": "ADMIN"}, org=True, as_user=t.org_admin)
    assert by_admin.status_code == 403, by_admin.text
    assert time.monotonic() - started < 4, "the refused role change waited on a lock"
    engines.refresh()
    denied = engines.db.execute(select(AuditLog).where(
        AuditLog.organization_id == engines.org, AuditLog.actor_id == t.org_admin.user.id,
        AuditLog.outcome == "DENIED")).scalars().all()
    assert denied, "the refused role change was not audited"
    promoted = engines.patch(f"/members/{contributor}", {"role": "ADMIN"}, org=True, as_user=t.owner)
    assert promoted.status_code == 200 and promoted.json()["role"] == "ADMIN", promoted.text

    owner = _membership(engines, t.owner)
    demote_last_owner = engines.patch(f"/members/{owner}", {"role": "MEMBER"}, org=True, as_user=t.owner)
    assert demote_last_owner.status_code in (400, 403, 409, 422), demote_last_owner.text

    viewer = _membership(engines, t.viewer)
    assert engines.get("/members", org=True, as_user=t.viewer).status_code == 200
    removed = engines.post(f"/members/{viewer}/deactivate", org=True, as_user=t.org_admin)
    assert removed.status_code == 200, removed.text
    assert engines.get("/members", org=True, as_user=t.viewer).status_code in (403, 404)
    assert engines.get("/work-items", as_user=t.viewer).status_code in (403, 404)

    engines.refresh()
    seat_events = engines.db.execute(select(OutboxEvent).where(
        OutboxEvent.organization_id == engines.org, OutboxEvent.event_type == "billing.seat_removed")).scalars().all()
    assert len(seat_events) == 1

    rows = engines.db.execute(select(AuditLog).where(AuditLog.organization_id == engines.org)).scalars().all()
    assert any(r.actor_id == t.owner.user.id and "ROLE" in str(r.action) for r in rows), [(r.actor_id, r.action) for r in rows]
    assert any(r.actor_id == t.org_admin.user.id and r.action.value == "DISABLED" for r in rows), \
        [(r.actor_id, r.action) for r in rows]


@pytest.mark.parametrize("statement", [
    "UPDATE audit_logs SET action = action WHERE organization_id = :org",
    "DELETE FROM audit_logs WHERE organization_id = :org",
    "TRUNCATE audit_logs",
])
def test_the_audit_log_is_append_only_in_the_database(engines: Engines, statement: str) -> None:
    # Make sure there is at least one row to refuse to change: an audited action through the API.
    rule = {"name": "Audit me", "triggers": ["document.completed"], "condition_groups": [], "actions": [
        {"action_type": "work_item.mutate", "config": {"target_field": "summary", "target_value": "x"}}]}
    assert engines.post("/automation/rules", rule).status_code == 201
    engines.refresh()
    assert engines.db.execute(select(AuditLog).where(AuditLog.organization_id == engines.org)).first()
    engines.db.commit()  # release this session's locks: TRUNCATE needs an exclusive one
    with TestSessionLocal() as db:
        db.execute(text("SET lock_timeout = '5s'"))
        with pytest.raises(DBAPIError) as refused:
            db.execute(text(statement), {"org": engines.org})
            db.flush()
        db.rollback()
    assert "lock timeout" not in str(refused.value).lower(), refused.value  # refused by the trigger, not a lock
