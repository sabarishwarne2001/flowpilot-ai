"""Helm's hands: approving, executing and undoing actions (brief §8).

APPROVE, THEN EXECUTE — EACH ONCE
=================================

Approval is one conditional UPDATE (`PROPOSED → APPROVED`, or one more approver on an approval
chain); execution another (`APPROVED → EXECUTING`), both on the row's id, so a double-click, two
tabs or two workers can win each step only once. The `helm.execute` job then re-runs the Guard on a
fresh snapshot: if the state changed and a check now fails, the action is BLOCKED and the book is
re-planned; otherwise the effect is applied through the existing services and the action becomes
EXECUTED, with an EXECUTION certificate. Every step is audited.

Money never moves here: "schedule" puts an invoice on the payment run list a person pays from their
own bank; "hold" keeps it off that list.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmCertificate, HelmTask
from app.services.helm import certificate as certs
from app.services.helm import money, settlements, snapshot_store
from app.services.helm import vocabulary as v
from app.services.helm.guard import ActionView, Actor, GuardContext, SYSTEM, guard

logger = logging.getLogger("app.services.helm.executor")


class ActionError(Exception):
    def __init__(self, code: str, message: str, *, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def view_of(action: HelmAction) -> ActionView:
    return ActionView(kind=action.kind, idempotency_key=action.idempotency_key,
                      mandate_id=str(action.mandate_id) if action.mandate_id else None,
                      mandate_version=action.mandate_version,
                      workspace_id=str(action.workspace_id) if action.workspace_id else None,
                      work_item_id=str(action.work_item_id) if action.work_item_id else None,
                      promise_id=str(action.promise_id) if action.promise_id else None,
                      counterparty_key=action.counterparty_key, amount_micros=action.amount_micros,
                      currency=action.currency, run_date=action.run_date, title=action.title,
                      params=dict(action.params or {}))


def _audit(db: Session, action: HelmAction, verb: str, *, actor_user_id: Optional[uuid.UUID],
           details: Optional[dict[str, Any]] = None) -> None:
    from app.services import audit_service

    try:
        with db.begin_nested():
            audit_service.record(
                db, organization_id=action.organization_id, workspace_id=action.workspace_id, actor_id=actor_user_id,
                resource_type="HELM_ACTION", resource_id=action.id, action=verb,
                details={"kind": action.kind, "book_id": str(action.book_id), "title": action.title[:300],
                         "amount_micros": action.amount_micros, "currency": action.currency,
                         "run_date": action.run_date.isoformat() if action.run_date else None,
                         "idempotency_key": action.idempotency_key, **(details or {})})
    except Exception:  # noqa: BLE001
        logger.exception("helm.action_audit_failed", extra={"action_id": str(action.id)})


def enqueue(db: Session, action: HelmAction) -> None:
    from app.services import job_service

    job_service.enqueue(db, job_type=v.JOB_EXECUTE, organization_id=action.organization_id,
                        payload={"action_id": str(action.id), "organization_id": str(action.organization_id),
                                 "book_id": str(action.book_id)},
                        idempotency_key=f"{v.JOB_EXECUTE}:{action.id}:{action.updated_at.isoformat() if action.updated_at else ''}")


def approve(db: Session, *, action: HelmAction, actor: Actor, required_approvals: int = 1) -> HelmAction:
    """One person's approval. Idempotent: approving an approved or executed action changes nothing."""
    from app.services.helm import gate

    if not gate.can_approve(actor.role, action.kind):
        raise ActionError("HELM_FORBIDDEN", "Only organization owners and admins approve this action"
                          + (" (the Billing role approves payment actions)." if action.kind not in v.PAYMENT_ACTIONS
                             else "."), status_code=403)
    if not actor.can_read_workspace:
        raise ActionError("HELM_NO_ACCESS", "You cannot read this document's workspace, so you cannot approve it. "
                          "Ask an organization owner for access.", status_code=403)
    if action.state in (v.ACTION_APPROVED, v.ACTION_EXECUTING, v.ACTION_EXECUTED):
        return action
    if action.state != v.ACTION_PROPOSED:
        raise ActionError("HELM_ACTION_NOT_PENDING", f"This action is {action.state.lower()}; only a proposed "
                          "action can be approved.")
    if not action.guard_passed:
        raise ActionError("HELM_ACTION_BLOCKED", "The Guard blocks this action; it cannot be approved.")
    now = datetime.now(timezone.utc)
    approvals = list(action.approvals or [])
    if any(a.get("user_id") == actor.user_id for a in approvals):
        return action
    approvals.append({"user_id": actor.user_id, "role": actor.role, "at": now.isoformat()})
    needed = max(int(action.required_approvals or 1), required_approvals)
    final = len([a for a in approvals if a.get("user_id")]) >= needed
    result = db.execute(
        update(HelmAction)
        .where(HelmAction.id == action.id, HelmAction.state == v.ACTION_PROPOSED)
        .values(approvals=approvals, required_approvals=needed,
                state=v.ACTION_APPROVED if final else v.ACTION_PROPOSED,
                decided_by_user_id=uuid.UUID(actor.user_id) if (final and actor.user_id) else action.decided_by_user_id,
                decided_at=now if final else action.decided_at, updated_at=now)
        .returning(HelmAction.id)
    ).first()
    db.refresh(action)
    if result is None:
        return action  # another request decided it first
    _audit(db, action, "APPROVED" if final else "UPDATED", actor_user_id=uuid.UUID(actor.user_id) if actor.user_id
           else None, details={"approvals": len(approvals), "required": needed})
    if final:
        enqueue(db, action)
    return action


def reject(db: Session, *, action: HelmAction, actor: Actor, reason: str) -> HelmAction:
    from app.services.helm import gate

    if not gate.can_approve(actor.role, action.kind):
        raise ActionError("HELM_FORBIDDEN", "Only organization owners and admins reject this action.", status_code=403)
    reason = (reason or "").strip()
    if not reason:
        raise ActionError("HELM_REASON_REQUIRED", "Say why the action is rejected.", status_code=422)
    if action.state == v.ACTION_REJECTED:
        return action
    if action.state not in (v.ACTION_PROPOSED, v.ACTION_BLOCKED):
        raise ActionError("HELM_ACTION_NOT_PENDING", f"This action is {action.state.lower()}; it cannot be rejected.")
    now = datetime.now(timezone.utc)
    result = db.execute(
        update(HelmAction)
        .where(HelmAction.id == action.id, HelmAction.state.in_((v.ACTION_PROPOSED, v.ACTION_BLOCKED)))
        .values(state=v.ACTION_REJECTED, rejected_reason=reason[:1000],
                decided_by_user_id=uuid.UUID(actor.user_id) if actor.user_id else None, decided_at=now, updated_at=now)
        .returning(HelmAction.id)
    ).first()
    db.refresh(action)
    if result is not None:
        _audit(db, action, "REJECTED", actor_user_id=uuid.UUID(actor.user_id) if actor.user_id else None,
               details={"reason": reason[:500]})
        _bank_line_stopped(db, action, reason[:500], by=uuid.UUID(actor.user_id) if actor.user_id else None)
    return action


def _bank_line_stopped(db: Session, action: HelmAction, reason: str, *, by: Optional[uuid.UUID] = None) -> None:
    """A payment record from a bank line that ended without recording the payment sends the line back to a
    person (owner addition A)."""
    if action.kind == v.ACTION_MARK_PAID and (action.params or {}).get("bank_line_id"):
        from app.services.helm import bank

        bank.on_stopped(db, action, reason, by=by)


def _claim(db: Session, action_id: uuid.UUID) -> Optional[HelmAction]:
    claimed = db.execute(
        update(HelmAction).where(HelmAction.id == action_id, HelmAction.state == v.ACTION_APPROVED)
        .values(state=v.ACTION_EXECUTING, updated_at=datetime.now(timezone.utc)).returning(HelmAction.id)
    ).first()
    if claimed is None:
        return None
    return db.get(HelmAction, action_id)


def _destination(db: Session, action: HelmAction, snapshot) -> tuple[Optional[bool], str]:
    """Whether the email the action sends may leave: a known contact, and egress allows mail."""
    if action.kind not in (v.ACTION_SEND_REMINDER, v.ACTION_DRAFT_DISPUTE, v.ACTION_REQUEST_DOCUMENT,
                           v.ACTION_WEBHOOK):
        return None, ""
    if action.kind == v.ACTION_DRAFT_DISPUTE and action.autonomy != v.AUTONOMY_ACT:
        return True, "A draft only: nothing is sent."
    if action.kind == v.ACTION_WEBHOOK:
        from app.services.helm import hands

        return hands.webhook_destination(db, organization_id=action.organization_id) \
            if hasattr(hands, "webhook_destination") else (False, "Webhooks are not available yet.")
    counterparty = snapshot.counterparties.get(action.counterparty_key or "")
    contact = (action.params or {}).get("to") or (counterparty.contact_email if counterparty else None)
    from app.services.helm import hands

    return hands.email_destination(db, organization_id=action.organization_id, workspace_id=action.workspace_id,
                                   to=contact)


def _execution_context(db: Session, action: HelmAction, snapshot, mandates) -> dict[str, Any]:
    """Payments already on runs before this one, and the cautious inflow by its run day."""
    from app.services.helm import simulation

    ahead: dict[str, int] = {}
    if action.kind in (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT) and action.currency:
        day = action.run_date or snapshot.as_of
        for payable in snapshot.payables:
            if payable.promise_id == (str(action.promise_id) if action.promise_id else None):
                continue
            if payable.currency == action.currency and payable.run_date and payable.run_date <= day and \
                    payable.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED):
                ahead[action.currency] = ahead.get(action.currency, 0) + payable.amount_micros
    sim = simulation.simulate(snapshot, seed=simulation.seed_for(snapshot.digest),
                              horizon_days=snapshot.horizon_days)
    inflow = {c: sim.p10_inflow_by(c, action.run_date or snapshot.as_of) for c in snapshot.cash}
    return {"scheduled_before": ahead, "inflow_by": inflow}


def execute(db: Session, *, action_id: uuid.UUID, actor: Actor = SYSTEM) -> Optional[HelmAction]:
    """Run one approved action once. None when it was not (or no longer) APPROVED. Flushes."""
    action = _claim(db, action_id)
    if action is None:
        return None
    book = db.get(HelmBook, action.book_id)
    from app.services.helm import gate, plan_store

    snapshot = snapshot_store.take(db, book)
    mandates = plan_store.active_mandates(db, book.id)
    destination_ok, destination_detail = _destination(db, action, snapshot)
    extra = _execution_context(db, action, snapshot, mandates)
    context = GuardContext(stage=v.CERT_STAGE_EXECUTION,
                           capability_held=gate.helm_held(db, action.organization_id) or book.kind == v.BOOK_SAMPLE,
                           destination_allowed=destination_ok, destination_detail=destination_detail,
                           already_ran=False, scheduled_before=extra["scheduled_before"], inflow_by=extra["inflow_by"])
    view = view_of(action)
    result = guard(view, snapshot, mandates, actor, context)
    # The plan-time approval requirement is what the approver answered; at execution only a FAIL stops it.
    now = datetime.now(timezone.utc)
    issued = certs.issue(action=view, result=result, snapshot=snapshot, mandates=mandates, actor=actor,
                         stage=v.CERT_STAGE_EXECUTION, issued_at=now, plan_id=str(action.plan_id) if action.plan_id
                         else None)
    cert = HelmCertificate(organization_id=action.organization_id, book_id=action.book_id, action_id=action.id,
                           plan_id=action.plan_id, stage=v.CERT_STAGE_EXECUTION, passed=issued.passed,
                           canonical=issued.canonical, sha256=issued.sha256)
    db.add(cert)
    db.flush()
    action.certificate_id = cert.id
    action.guard = result.as_json()
    action.guard_passed = result.passed
    if not result.passed:
        action.state = v.ACTION_BLOCKED
        action.result = {"blocked_at": now.isoformat(), "failed": [c.id for c in result.failed]}
        action.updated_at = now
        db.flush()
        _audit(db, action, "BLOCKED", actor_user_id=None, details={"failed": [c.id for c in result.failed]})
        _bank_line_stopped(db, action, "; ".join(c.detail for c in result.failed) or "a check failed")
        plan_store.enqueue(db, book, triggered_by=v.TRIGGER_EXECUTION, delay_s=1, force=True)
        return action
    from app.services.helm.hands import HandError

    try:
        with db.begin_nested():
            outcome = _apply(db, action=action, book=book, snapshot=snapshot, actor=actor)
    except (ActionError, HandError) as exc:
        # The effect could not happen (a refused email, a task with nowhere to go): the action is FAILED
        # with the reason in words, never left EXECUTING.
        action.state = v.ACTION_FAILED
        action.result = {"error": exc.code, "message": exc.message}
        action.updated_at = now
        db.flush()
        _audit(db, action, "UPDATED", actor_user_id=None, details={"failed": exc.code})
        _bank_line_stopped(db, action, exc.message)
        return action
    action.state = v.ACTION_EXECUTED
    action.executed_at = now
    action.executed_by_user_id = uuid.UUID(actor.user_id) if actor.user_id else None
    action.result = outcome
    action.updated_at = now
    db.flush()
    _audit(db, action, "EXECUTED", actor_user_id=action.executed_by_user_id,
           details={"certificate_sha256": cert.sha256})
    if action.workspace_id:
        from app.services.helm import ledger_store

        ledger_store.enqueue_rebuild(db, workspace_id=action.workspace_id, organization_id=action.organization_id,
                                     reason=f"helm action {action.kind}")
    return action


def _apply(db: Session, *, action: HelmAction, book: HelmBook, snapshot, actor: Actor) -> dict[str, Any]:
    params = dict(action.params or {})
    who = uuid.UUID(actor.user_id) if actor.user_id else None
    if action.kind == v.ACTION_HOLD_PAYMENT:
        previous = settlements.set_state(
            db, organization_id=action.organization_id, workspace_id=action.workspace_id,
            work_item_id=action.work_item_id, state=v.SETTLEMENT_HELD, source=v.SOURCE_HELM, actor_user_id=who,
            action_id=action.id, hold_reason=action.explanation[:1000],
            evidence={"reason": params.get("reason"), "certificate_id": str(action.certificate_id)})
        _notify_hold(db, action, book)
        return {"previous": previous, "settlement": v.SETTLEMENT_HELD}
    if action.kind == v.ACTION_RELEASE_HOLD:
        previous = settlements.set_state(
            db, organization_id=action.organization_id, workspace_id=action.workspace_id,
            work_item_id=action.work_item_id, state=v.SETTLEMENT_UNPAID, source=v.SOURCE_HELM, actor_user_id=who,
            action_id=action.id, evidence={"released": params.get("reason"),
                                           "certificate_id": str(action.certificate_id)})
        return {"previous": previous, "settlement": v.SETTLEMENT_UNPAID}
    if action.kind == v.ACTION_SCHEDULE_PAYMENT:
        previous = settlements.set_state(
            db, organization_id=action.organization_id, workspace_id=action.workspace_id,
            work_item_id=action.work_item_id, state=v.SETTLEMENT_SCHEDULED, source=v.SOURCE_HELM, actor_user_id=who,
            action_id=action.id, run_date=action.run_date,
            evidence={"certificate_id": str(action.certificate_id), "erp": "payment run list (N-088)"})
        return {"previous": previous, "settlement": v.SETTLEMENT_SCHEDULED,
                "run_date": action.run_date.isoformat() if action.run_date else None}
    if action.kind == v.ACTION_APPROVE_PAYMENT:
        previous = settlements.set_state(
            db, organization_id=action.organization_id, workspace_id=action.workspace_id,
            work_item_id=action.work_item_id, state=v.SETTLEMENT_APPROVED, source=v.SOURCE_PERSON, actor_user_id=who,
            action_id=action.id, run_date=action.run_date or date.today())
        return {"previous": previous, "settlement": v.SETTLEMENT_APPROVED}
    if action.kind == v.ACTION_MARK_PAID:
        paid_on = date.fromisoformat(params["paid_on"]) if params.get("paid_on") else date.today()
        # A payment a bank statement line shows (owner addition A) is evidenced by that line.
        bank_line = params.get("bank_line_id")
        evidence = ({"bank_line_id": bank_line, "bank_import_id": params.get("bank_import_id"),
                     "account": params.get("account"), "narration": params.get("narration"),
                     "matched_by": params.get("matched_by")} if bank_line else {"marked_by": actor.user_id})
        previous = settlements.set_state(
            db, organization_id=action.organization_id, workspace_id=action.workspace_id,
            work_item_id=action.work_item_id, state=v.SETTLEMENT_PAID,
            source=v.SOURCE_STATEMENT if bank_line else v.SOURCE_PERSON, actor_user_id=who,
            action_id=action.id, paid_on=paid_on, paid_amount_micros=action.amount_micros,
            paid_reference=params.get("reference"), evidence=evidence)
        return {"previous": previous, "settlement": v.SETTLEMENT_PAID, "paid_on": paid_on.isoformat()}
    if action.kind == v.ACTION_DRAFT_DISPUTE:
        from app.services.helm import hands

        return hands.draft_dispute(db, action=action, book=book, snapshot=snapshot)
    if action.kind == v.ACTION_CREATE_TASK:
        from app.services.helm import hands

        return hands.create_task(db, action=action, book=book)
    if action.kind == v.ACTION_NOTIFY:
        from app.services.helm import hands

        return hands.notify(db, action=action, book=book)
    from app.services.helm import hands

    handler = getattr(hands, action.kind, None)
    if handler is None:
        raise ActionError("HELM_UNKNOWN_ACTION", f"Helm cannot run a {action.kind} action yet.")
    return handler(db, action=action, book=book, snapshot=snapshot)


def _notify_hold(db: Session, action: HelmAction, book: HelmBook) -> None:
    if book.kind == v.BOOK_SAMPLE:
        return
    from app.models.notification import NotificationPriority, NotificationType
    from app.models.organization import OrganizationRole
    from app.services import organization_notification_service as notices

    try:
        with db.begin_nested():
            notices.emit_to_roles(
                db, organization_id=action.organization_id,
                roles=(OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING),
                title=f"Helm held {money.say(action.amount_micros, action.currency)}",
                message=action.explanation[:1900], notification_type=NotificationType.SYSTEM,
                priority=NotificationPriority.WARNING, category=v.NOTIFICATION_CATEGORY,
                action=v.NOTIFICATION_ACTION)
    except Exception:  # noqa: BLE001
        logger.exception("helm.hold_notify_failed", extra={"action_id": str(action.id)})


def undo(db: Session, *, action: HelmAction, actor: Actor) -> HelmAction:
    """Undo a hold, a schedule or a task. Sent emails cannot be undone; the screen says so. Undoing a payment
    record also takes back the value held that rested on that payment."""
    from app.services.helm import gate

    if not gate.can_approve(actor.role, action.kind):
        raise ActionError("HELM_FORBIDDEN", "Only organization owners and admins undo this action.", status_code=403)
    if not actor.can_read_workspace:
        raise ActionError("HELM_NO_ACCESS", "You cannot read this document's workspace, so you cannot undo its "
                          "action. Ask an organization owner for access.", status_code=403)
    if action.state == v.ACTION_UNDONE:
        return action
    if action.state != v.ACTION_EXECUTED:
        raise ActionError("HELM_ACTION_NOT_EXECUTED", "Only an action that ran can be undone.")
    if not action.undoable:
        raise ActionError("HELM_NOT_UNDOABLE", "This action sent an email; it cannot be taken back.")
    now = datetime.now(timezone.utc)
    claimed = db.execute(
        update(HelmAction).where(HelmAction.id == action.id, HelmAction.state == v.ACTION_EXECUTED)
        .values(state=v.ACTION_UNDONE, updated_at=now).returning(HelmAction.id)
    ).first()
    db.refresh(action)
    if claimed is None:
        return action
    who = uuid.UUID(actor.user_id) if actor.user_id else None
    result = dict(action.result or {})
    if action.kind in (v.ACTION_HOLD_PAYMENT, v.ACTION_RELEASE_HOLD, v.ACTION_SCHEDULE_PAYMENT,
                       v.ACTION_APPROVE_PAYMENT, v.ACTION_MARK_PAID) and result.get("previous") is not None:
        settlements.restore(db, organization_id=action.organization_id, workspace_id=action.workspace_id,
                            work_item_id=action.work_item_id, previous=result["previous"], actor_user_id=who,
                            action_id=action.id)
    elif action.kind in (v.ACTION_CREATE_TASK, v.ACTION_DECIDE_RENEWAL) and result.get("task_id"):
        task = db.get(HelmTask, uuid.UUID(result["task_id"]))
        if task is not None and task.status == v.TASK_OPEN:
            task.status, task.resolved_at, task.resolved_by_user_id = v.TASK_CANCELLED, now, who
            task.decision_note = "Cancelled: the Helm action that opened it was undone."
    withdrawn: list[dict[str, Any]] = []
    if action.kind == v.ACTION_MARK_PAID and action.work_item_id:
        from app.services.helm import value

        withdrawn = value.withdraw(db, work_item_id=action.work_item_id)
    result["undone_at"] = now.isoformat()
    action.result = result
    db.flush()
    if action.kind == v.ACTION_MARK_PAID and (action.params or {}).get("bank_line_id"):
        from app.services.helm import bank

        bank.on_undone(db, action, by=who)
    _audit(db, action, "UNDONE", actor_user_id=who, details={"value_withdrawn": withdrawn} if withdrawn else None)
    if action.workspace_id:
        from app.services.helm import ledger_store

        ledger_store.enqueue_rebuild(db, workspace_id=action.workspace_id, organization_id=action.organization_id,
                                     reason="helm action undone")
    return action


__all__ = ["ActionError", "approve", "enqueue", "execute", "reject", "undo", "view_of"]
