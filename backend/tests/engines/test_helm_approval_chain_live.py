"""Approval chains on real Postgres (Enterprise, `capability.helm_approval_chains`).

The settings API reads and sets a book's chain (validated in words, refused below Enterprise,
audited); every path that approves respects it: approving one action, approving the whole plan and
Act mode; a second approval must come from a different person, from a role the chain takes; two
people approving at the same moment both count; and changing the chain re-plans what is waiting.
"""

from __future__ import annotations

import threading
import uuid
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models.audit_log import AuditLog, AuditOutcome
from app.models.helm import HelmAction
from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
from app.models.verification import DocumentVerification, VerificationStatus
from app.models.workspace import WorkspaceRole
from app.services.helm import service
from app.services.helm import vocabulary as v
from tests.conftest import TestSessionLocal, _grant, _make_user, _seat
from tests.engines.conftest import drain
from tests.security.plans import put_on_plan
from tests.services.helm import factories

API = "/api/v1"
M = 1_000_000
LAKH = 100_000 * M


def _url(tenant, path: str) -> str:
    return f"{API}/organizations/{tenant.organization.id}/helm{path}"


def _start(client, db_session, tenant, plan: str) -> dict:
    put_on_plan(db_session, tenant.organization, plan)
    db_session.commit()
    response = client.post(_url(tenant, "/books"), json={"workspace_id": str(tenant.workspace.id)},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    book = response.json()
    response = client.patch(_url(tenant, f"/books/{book['id']}/settings"),
                            json={"identities": [{"name": "Meridian Wholesale", "tax_id": "27AAACM1234F1Z5"}],
                                  "run_weekdays": [1, 2, 3, 4, 5]}, headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    response = client.post(_url(tenant, f"/books/{book['id']}/cash-positions"),
                           json={"currency": "INR", "amount": "5000000", "as_of": date.today().isoformat()},
                           headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    return book


@pytest.fixture()
def book(client, db_session, tenant, test_sessions):
    return _start(client, db_session, tenant, "enterprise")


def _chain(client, tenant, book, tiers, *, headers=None):
    return client.patch(_url(tenant, f"/books/{book['id']}/settings"), json={"approval_chain": {"tiers": tiers}},
                        headers=headers or tenant.owner.headers)


def _invoice(db_session, tenant, number: str, amount: int, *, verified: bool = False):
    item = factories.invoice(db_session, workspace=tenant.workspace, organization_id=tenant.organization.id,
                             number=number, supplier=f"Supplier {number}", amount=amount,
                             on=date.today() - timedelta(days=20), terms="Net 30")
    if verified:
        db_session.add(DocumentVerification(work_item_id=item.id, workspace_id=tenant.workspace.id,
                                            organization_id=tenant.organization.id, status=VerificationStatus.AGREED,
                                            agent_count=2, agreement_score=Decimal("1"), confidence=Decimal("1"),
                                            details={}))
    db_session.commit()
    return item


def _plan(client, tenant, book, *, act_cap: int | None = None) -> dict[str, dict]:
    body: dict = {"template": "pay_on_time", "activate": True}
    if act_cap is not None:
        body["autonomy"] = {"mode": "act", "act_cap": {"amount_micros": act_cap, "currency": "INR"}}
    response = client.post(_url(tenant, f"/books/{book['id']}/mandates"), json=body, headers=tenant.owner.headers)
    assert response.status_code == 201, response.text
    response = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return {a["title"]: a for a in response.json()["actions"] if a["kind"] == v.ACTION_SCHEDULE_PAYMENT}


def _replan(client, tenant, book) -> dict[str, dict]:
    response = client.post(_url(tenant, f"/books/{book['id']}/plan"), headers=tenant.owner.headers)
    assert response.status_code == 200, response.text
    return {a["title"]: a for a in response.json()["actions"] if a["kind"] == v.ACTION_SCHEDULE_PAYMENT}


def _pay(actions: dict[str, dict], amount: int) -> dict:
    return next(a for a in actions.values() if a["amount_micros"] == amount * M)


def _approve(client, tenant, action, persona):
    return client.post(_url(tenant, f"/actions/{action['id']}/approve"), headers=persona.headers)


def _billing(db_session, tenant):
    persona = _make_user(db_session, f"billing-{uuid.uuid4().hex[:8]}@acme.com")
    _seat(db_session, tenant.organization, persona, OrganizationRole.BILLING)
    _grant(db_session, tenant.workspace, persona, WorkspaceRole.VIEWER)
    db_session.commit()
    return persona


def test_the_chain_is_read_set_validated_and_audited(client, db_session, tenant, book):
    url = _url(tenant, f"/books/{book['id']}/settings")
    assert client.get(url, headers=tenant.owner.headers).json()["approval_chain"] == {
        "available": True, "tiers": [], "sentences": [], "updated_at": None, "warnings": []}

    response = _chain(client, tenant, book, [
        {"above_micros": 10 * LAKH, "currency": "inr", "approvals": 2, "roles": ["admin", "OWNER"]}])
    assert response.status_code == 200, response.text
    chain = response.json()["approval_chain"]
    assert chain["tiers"] == [{"above_micros": 10 * LAKH, "currency": "INR", "approvals": 2,
                               "roles": ["OWNER", "ADMIN"]}]
    assert chain["sentences"] == ["Above ₹10 lakh: two approvals from different people (owners or admins)."]
    assert chain["updated_at"]
    # The billing role reads it too.
    assert client.get(url, headers=_billing(db_session, tenant).headers).json()["approval_chain"]["tiers"] == \
        chain["tiers"]

    audit = db_session.execute(select(AuditLog).where(
        AuditLog.organization_id == tenant.organization.id, AuditLog.resource_type == "HELM_SETTINGS")
        .order_by(AuditLog.created_at.desc())).scalars().first()
    assert audit.details["approval_chain"] == {"before": [], "after": chain["tiers"]}

    # Refused in words: contradictory, impossible (the organization has one owner and one admin), unknown roles.
    bad = _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 3,
                                         "roles": ["OWNER", "ADMIN"]},
                                        {"above_micros": 10 * LAKH, "currency": "INR", "approvals": 2}])
    assert bad.status_code == 422, bad.text
    assert "Above ₹10 lakh needs at least as many approvals as above ₹1 lakh." in bad.json()["details"]["problems"]
    assert any("could never be approved" in p for p in bad.json()["details"]["problems"])
    bad = _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2,
                                         "roles": ["MEMBER"]}])
    assert bad.status_code == 422
    assert bad.json()["details"]["problems"] == ["Tier 1: approvals come from owners, admins or the billing role."]
    # A refused change keeps the chain as it was; an empty list removes it.
    assert client.get(url, headers=tenant.owner.headers).json()["approval_chain"]["tiers"] == chain["tiers"]
    assert _chain(client, tenant, book, []).json()["approval_chain"]["tiers"] == []


def test_only_owners_and_admins_set_the_chain_and_only_on_enterprise(client, db_session, tenant, test_sessions):
    book = _start(client, db_session, tenant, "business")
    tiers = [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]
    settings = client.get(_url(tenant, f"/books/{book['id']}/settings"), headers=tenant.owner.headers).json()
    assert settings["approval_chain"]["available"] is False
    refused = _chain(client, tenant, book, tiers)
    assert refused.status_code == 402, refused.text
    assert refused.json()["code"] == "CAPABILITY_REQUIRED"
    assert refused.json()["details"]["capability"] == "capability.helm_approval_chains"
    assert refused.json()["message"].startswith("Approval chains are part of the Enterprise plan")
    # The refusal is audited, like every capability refusal, and nothing was stored.
    denied = db_session.execute(select(AuditLog).where(
        AuditLog.organization_id == tenant.organization.id, AuditLog.outcome == AuditOutcome.DENIED)).scalars().all()
    assert any((row.details or {}).get("operation") == "helm.approval_chain.update" for row in denied)
    assert client.get(_url(tenant, f"/books/{book['id']}/settings"),
                      headers=tenant.owner.headers).json()["approval_chain"]["tiers"] == []

    put_on_plan(db_session, tenant.organization, "enterprise")
    assert _chain(client, tenant, book, tiers, headers=_billing(db_session, tenant).headers).status_code == 403
    assert _chain(client, tenant, book, tiers, headers=tenant.org_admin.headers).status_code == 200


def test_a_payment_above_the_chain_needs_two_different_people(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-1", 500_000)
    _invoice(db_session, tenant, "SMALL-1", 40_000)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    actions = _plan(client, tenant, book)
    big, small = _pay(actions, 500_000), _pay(actions, 40_000)
    assert (big["required_approvals"], big["approvals_given"], big["needs_approval"]) == (2, 0, True)
    assert small["required_approvals"] == 1

    first = _approve(client, tenant, big, tenant.owner)
    assert first.status_code == 200, first.text
    assert first.json()["state"] == v.ACTION_PROPOSED
    assert first.json()["approvals_given"] == 1
    assert [(a["role"], a["by_you"]) for a in first.json()["approvals"]] == [("OWNER", True)]
    # The same person cannot be the second approver; nothing changes.
    again = _approve(client, tenant, big, tenant.owner)
    assert again.status_code == 409, again.text
    assert again.json()["details"]["reason"] == "HELM_SAME_APPROVER"
    assert again.json()["message"] == ("You have already approved this. It needs two different people: ask someone "
                                       "else (owners, admins or the billing role) to add the second approval.")
    seen_by_admin = client.get(_url(tenant, f"/actions/{big['id']}"), headers=tenant.org_admin.headers).json()
    assert [(a["role"], a["by_you"]) for a in seen_by_admin["approvals"]] == [("OWNER", False)]

    second = _approve(client, tenant, big, tenant.org_admin)
    assert second.status_code == 200, second.text
    assert second.json()["state"] == v.ACTION_APPROVED and second.json()["approvals_given"] == 2
    # Below the chain one approval is enough, as always.
    assert _approve(client, tenant, small, tenant.owner).json()["state"] == v.ACTION_APPROVED
    assert sorted(r.ok for r in drain(only=[v.JOB_EXECUTE])) == [True, True]
    db_session.expire_all()
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).state == v.ACTION_EXECUTED


def test_a_chain_with_roles_refuses_anyone_else(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-2", 500_000)
    _invoice(db_session, tenant, "SMALL-2", 40_000)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2,
                                          "roles": ["OWNER", "ADMIN"]}]).status_code == 200
    actions = _plan(client, tenant, book)
    big, small = _pay(actions, 500_000), _pay(actions, 40_000)
    assert big["approver_roles"] == ["OWNER", "ADMIN"] and small["approver_roles"] is None
    billing = _billing(db_session, tenant)
    refused = _approve(client, tenant, big, billing)
    assert refused.status_code == 403, refused.text
    assert refused.json()["message"] == "This payment's approval chain takes approvals from owners or admins only."
    assert _approve(client, tenant, small, billing).json()["state"] == v.ACTION_APPROVED


def test_approve_all_counts_only_what_it_approved(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-3", 500_000)
    _invoice(db_session, tenant, "SMALL-3", 40_000)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    actions = _plan(client, tenant, book)
    big = _pay(actions, 500_000)
    url = _url(tenant, f"/books/{book['id']}/plan/approve-all")

    def approved_now() -> int:
        db_session.expire_all()
        return len([a for a in db_session.execute(select(HelmAction).where(
            HelmAction.book_id == uuid.UUID(book["id"]))).scalars() if a.state == v.ACTION_APPROVED])

    result = client.post(url, headers=tenant.owner.headers).json()
    assert result == {"approved": approved_now(), "waiting": 1, "skipped": 0}
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).state == v.ACTION_PROPOSED
    # Approving everything again changes nothing and says so.
    assert client.post(url, headers=tenant.owner.headers).json() == {"approved": 0, "waiting": 1, "skipped": 0}
    # A different person completes the chain.
    assert client.post(url, headers=tenant.org_admin.headers).json() == {"approved": 1, "waiting": 0, "skipped": 0}
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).state == v.ACTION_APPROVED


def test_act_mode_never_runs_a_payment_the_chain_covers(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-4", 500_000, verified=True)
    _invoice(db_session, tenant, "SMALL-4", 40_000, verified=True)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    actions = _plan(client, tenant, book, act_cap=10 * LAKH)
    big, small = _pay(actions, 500_000), _pay(actions, 40_000)
    # Within the act cap and on checked facts, the small payment runs on Helm's own say-so...
    assert small["state"] == v.ACTION_APPROVED and small["approvals"][0]["by_helm"] is True
    # ...the one the chain covers waits for two people, and Helm's approval is not one of them.
    assert (big["state"], big["needs_approval"], big["required_approvals"], big["approvals_given"]) == \
        (v.ACTION_PROPOSED, True, 2, 0)
    # The plan counts it as waiting for approval, though the Guard alone would have let it run.
    cockpit = client.get(_url(tenant, f"/books/{book['id']}/cockpit"), headers=tenant.owner.headers).json()
    waiting = [a for a in cockpit["actions"] if a["state"] == v.ACTION_PROPOSED and a["needs_approval"]]
    assert cockpit["summary"]["counts"]["needs_approval"] == len(waiting) >= 1


def test_a_kept_chain_applies_only_while_the_plan_carries_it(client, db_session, tenant, book):
    """Plan gating: below Enterprise the stored chain stays (shown as not available) but no payment waits
    for it; back on Enterprise it applies again."""
    _invoice(db_session, tenant, "BIG-8", 500_000)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    assert _pay(_plan(client, tenant, book), 500_000)["required_approvals"] == 2

    put_on_plan(db_session, tenant.organization, "business")
    db_session.commit()
    chain = client.get(_url(tenant, f"/books/{book['id']}/settings"), headers=tenant.owner.headers).json()[
        "approval_chain"]
    assert chain["available"] is False and len(chain["tiers"]) == 1
    big = _pay(_replan(client, tenant, book), 500_000)
    assert (big["required_approvals"], big["approver_roles"]) == (1, None)

    put_on_plan(db_session, tenant.organization, "enterprise")
    db_session.commit()
    big = _pay(_replan(client, tenant, book), 500_000)
    assert big["required_approvals"] == 2
    assert _approve(client, tenant, big, tenant.owner).json()["state"] == v.ACTION_PROPOSED


def test_a_chain_the_organization_can_no_longer_meet_is_said(client, db_session, tenant, book):
    tiers = [{"above_micros": LAKH, "currency": "INR", "approvals": 2, "roles": ["OWNER", "ADMIN"]}]
    assert _chain(client, tenant, book, tiers).json()["approval_chain"]["warnings"] == []
    member = db_session.execute(select(OrganizationMember).where(
        OrganizationMember.organization_id == tenant.organization.id,
        OrganizationMember.user_id == tenant.org_admin.user.id)).scalar_one()
    member.status = MembershipStatus.DEACTIVATED
    db_session.commit()
    chain = client.get(_url(tenant, f"/books/{book['id']}/settings"), headers=tenant.owner.headers).json()[
        "approval_chain"]
    assert chain["warnings"] == [
        "Above ₹1 lakh the chain needs two different people from owners or admins, and your organization has one: "
        "those payments wait until someone is added or the chain is changed."]


def test_changing_the_chain_replans_what_is_waiting(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-5", 500_000)
    big = _pay(_plan(client, tenant, book), 500_000)
    assert big["required_approvals"] == 1

    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    # Shown at once; the re-plan the change asked for stores it.
    assert client.get(_url(tenant, f"/actions/{big['id']}"),
                      headers=tenant.owner.headers).json()["required_approvals"] == 2
    drain(only=[v.JOB_PLAN])
    db_session.expire_all()
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).required_approvals == 2
    assert _approve(client, tenant, big, tenant.owner).json()["state"] == v.ACTION_PROPOSED

    # The chain removed: the approval already given is enough, and its giver may finish it.
    assert _chain(client, tenant, book, []).status_code == 200
    drain(only=[v.JOB_PLAN])
    db_session.expire_all()
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).required_approvals == 1
    done = _approve(client, tenant, big, tenant.owner)
    assert done.status_code == 200, done.text
    assert done.json()["state"] == v.ACTION_APPROVED


def test_two_people_approving_at_the_same_moment_both_count(client, db_session, tenant, book):
    _invoice(db_session, tenant, "BIG-6", 500_000)
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    big = _pay(_plan(client, tenant, book), 500_000)
    action_id = uuid.UUID(big["id"])
    first_session, second_session = TestSessionLocal(), TestSessionLocal()
    errors: list[BaseException] = []
    try:
        owner = service.viewer(first_session, organization_id=tenant.organization.id, user_id=tenant.owner.user.id,
                               role="OWNER")
        admin = service.viewer(second_session, organization_id=tenant.organization.id,
                               user_id=tenant.org_admin.user.id, role="ADMIN")
        # Both read the action before either approves.
        first_session.get(HelmAction, action_id)
        second_session.get(HelmAction, action_id)
        service.approve(first_session, owner, action_id)  # holds the row until it commits

        def second() -> None:
            try:
                service.approve(second_session, admin, action_id)
                second_session.commit()
            except BaseException as exc:  # noqa: BLE001 - reported below
                errors.append(exc)

        thread = threading.Thread(target=second)
        thread.start()
        thread.join(timeout=1)
        assert thread.is_alive(), "the second approval must wait for the first"
        first_session.commit()
        thread.join(timeout=15)
        assert not thread.is_alive() and not errors, errors
    finally:
        first_session.close()
        second_session.close()
    db_session.expire_all()
    action = db_session.get(HelmAction, action_id)
    assert sorted(a["role"] for a in action.approvals) == ["ADMIN", "OWNER"]
    assert action.state == v.ACTION_APPROVED


def test_a_chain_saved_after_helms_own_approval_holds_the_payment_back(client, db_session, tenant, book):
    """Act mode approved a payment on Helm's own say-so; before the worker ran it, the owner saved a chain
    that covers it. It must not run on Helm's approval: at execution it goes back to wait for its people."""
    from app.services.helm import executor

    _invoice(db_session, tenant, "BIG-9", 500_000, verified=True)
    big = _pay(_plan(client, tenant, book, act_cap=10 * LAKH), 500_000)
    assert big["state"] == v.ACTION_APPROVED and big["approvals"][0]["by_helm"] is True
    assert _chain(client, tenant, book, [{"above_micros": LAKH, "currency": "INR", "approvals": 2}]).status_code == 200
    with TestSessionLocal() as db:
        ran = executor.execute(db, action_id=uuid.UUID(big["id"]))
        db.commit()
        assert ran is not None and (ran.state, ran.required_approvals) == (v.ACTION_PROPOSED, 2)
    db_session.expire_all()
    assert db_session.get(HelmAction, uuid.UUID(big["id"])).state == v.ACTION_PROPOSED
