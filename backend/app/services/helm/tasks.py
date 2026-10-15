"""Helm's tasks in the review hub: verify a changed bank account, find a missing PO, decide a renewal.

A reviewer settles one with a verdict (`helm_verdict`):

    VERIFY_BANK_ACCOUNT  VERIFIED (confirmed by phone, on a number you already had) | NOT_VERIFIED
    DECIDE_RENEWAL       RENEW | CANCEL | RENEGOTIATE (recorded on the obligation, which is then DONE)
    any task             DONE | CANCEL

A verified bank account closes the radar's BANK_ACCOUNT_CHANGED flag as CONFIRMED with the note,
so the Guard lets that invoice be paid again; NOT_VERIFIED keeps it held. A renewal decision is
written to the obligation through the obligations service (an `obligation_events` row, state DONE).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmTask
from app.services.helm import vocabulary as v

VERDICTS = {
    v.TASK_VERIFY_BANK_ACCOUNT: ("VERIFIED", "NOT_VERIFIED", "CANCEL"),
    v.TASK_DECIDE_RENEWAL: ("RENEW", "CANCEL", "RENEGOTIATE"),
}
GENERIC = ("DONE", "CANCEL")


class TaskError(ValueError):
    pass


def verdicts_for(kind: str) -> tuple[str, ...]:
    return VERDICTS.get(kind, GENERIC)


def resolve(db: Session, *, task_id: uuid.UUID, workspace_id: uuid.UUID, verdict: str, note: Optional[str],
            actor_user_id: uuid.UUID) -> HelmTask:
    task = db.execute(select(HelmTask).where(HelmTask.id == task_id, HelmTask.workspace_id == workspace_id)
                      .with_for_update()).scalar_one_or_none()
    if task is None:
        raise TaskError("This Helm task no longer exists.")
    verdict = (verdict or "").strip().upper()
    allowed = verdicts_for(task.kind)
    if verdict not in allowed:
        raise TaskError(f"This task needs helm_verdict: {', '.join(allowed)}.")
    if task.status != v.TASK_OPEN:
        raise TaskError("This task is already settled.")
    now = datetime.now(timezone.utc)
    if task.kind == v.TASK_VERIFY_BANK_ACCOUNT and verdict in ("VERIFIED", "NOT_VERIFIED") and task.work_item_id:
        _settle_bank_flag(db, task=task, verified=verdict == "VERIFIED", note=note, actor_user_id=actor_user_id)
    if task.kind == v.TASK_DECIDE_RENEWAL and task.obligation_id and verdict in VERDICTS[v.TASK_DECIDE_RENEWAL]:
        _record_renewal(db, task=task, decision=verdict.lower(), note=note, actor_user_id=actor_user_id)
    task.status = v.TASK_CANCELLED if verdict == "CANCEL" else v.TASK_DONE
    task.decision = verdict
    task.decision_note = (note or None) and note[:2000]
    task.resolved_by_user_id = actor_user_id
    task.resolved_at = now
    task.updated_at = now
    db.flush()
    from app.services.helm import hooks

    hooks.document_changed(db, workspace_id=task.workspace_id, organization_id=task.organization_id,
                           reason="helm task resolved")
    return task


def _settle_bank_flag(db: Session, *, task: HelmTask, verified: bool, note: Optional[str],
                      actor_user_id: uuid.UUID) -> None:
    from app.models.payment_risk import KIND_BANK_ACCOUNT_CHANGED, PaymentRiskFlag

    flag = db.execute(select(PaymentRiskFlag).where(PaymentRiskFlag.work_item_id == task.work_item_id,
                                                    PaymentRiskFlag.kind == KIND_BANK_ACCOUNT_CHANGED,
                                                    PaymentRiskFlag.workspace_id == task.workspace_id)
                      ).scalar_one_or_none()
    if flag is None or flag.status != "OPEN" or not verified:
        return
    flag.status = "DISMISSED"
    flag.reviewed_by_user_id = actor_user_id
    flag.reviewed_at = datetime.now(timezone.utc)
    flag.review_note = ("Verified by phone on a number already on file (Helm task). " + (note or ""))[:2000]
    db.flush()


def _record_renewal(db: Session, *, task: HelmTask, decision: str, note: Optional[str],
                    actor_user_id: uuid.UUID) -> None:
    from app.models.obligations import Obligation
    from app.services.obligations import service as obligation_service

    obligation = db.execute(select(Obligation).where(Obligation.id == task.obligation_id,
                                                     Obligation.workspace_id == task.workspace_id)
                            ).scalar_one_or_none()
    if obligation is None:
        return
    detail = dict(obligation.detail or {})
    detail["helm_decision"] = {"decision": decision, "note": (note or "")[:500],
                               "by": str(actor_user_id), "at": datetime.now(timezone.utc).isoformat()}
    obligation.detail = detail
    if obligation.state in ("OPEN", "DUE_SOON", "OVERDUE"):
        obligation_service.complete(db, ob=obligation, actor_user_id=actor_user_id,
                                    note=f"Decided in Helm: {decision}" + (f" ({note})" if note else ""))
    db.flush()


__all__ = ["TaskError", "VERDICTS", "resolve", "verdicts_for"]
