"""Settlement: whether an invoice was paid, held, disputed or scheduled. A source of truth.

Every change bumps `version` and records who (a person, a statement match, a Helm action) and why.
The previous state is returned so an action can be undone exactly. "Paid" always carries evidence:
a person's mark (with an optional reference) or a STATEMENT document line.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmSettlement
from app.services.helm import vocabulary as v


def get(db: Session, work_item_id: uuid.UUID) -> Optional[HelmSettlement]:
    return db.execute(
        select(HelmSettlement).where(HelmSettlement.work_item_id == work_item_id).with_for_update()
    ).scalar_one_or_none()


def snapshot(row: Optional[HelmSettlement]) -> dict[str, Any]:
    if row is None:
        return {"state": v.SETTLEMENT_UNPAID, "exists": False}
    return {"state": row.state, "exists": True, "run_date": row.run_date.isoformat() if row.run_date else None,
            "paid_on": row.paid_on.isoformat() if row.paid_on else None, "hold_reason": row.hold_reason,
            "paid_reference": row.paid_reference, "paid_amount_micros": row.paid_amount_micros,
            "source": row.source, "version": row.version}


def set_state(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, work_item_id: uuid.UUID,
              state: str, source: str, actor_user_id: Optional[uuid.UUID] = None,
              action_id: Optional[uuid.UUID] = None, run_date: Optional[date] = None, paid_on: Optional[date] = None,
              paid_amount_micros: Optional[int] = None, paid_reference: Optional[str] = None,
              hold_reason: Optional[str] = None, evidence: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Move one invoice to `state`; returns the previous settlement (for undo). Flushes."""
    if state not in v.SETTLEMENT_STATES:
        raise ValueError(f"unknown settlement state {state!r}")
    row = get(db, work_item_id)
    previous = snapshot(row)
    now = datetime.now(timezone.utc)
    if row is None:
        row = HelmSettlement(organization_id=organization_id, workspace_id=workspace_id, work_item_id=work_item_id,
                             state=state, source=source, version=1, evidence={})
        db.add(row)
    else:
        row.version = int(row.version or 1) + 1
    row.state = state
    row.source = source
    row.updated_by_user_id = actor_user_id
    row.action_id = action_id
    row.updated_at = now
    row.run_date = run_date if state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED) else None
    row.hold_reason = hold_reason if state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED) else None
    if state == v.SETTLEMENT_PAID:
        row.paid_on = paid_on or now.date()
        row.paid_amount_micros = paid_amount_micros
        row.paid_reference = (paid_reference or None) and paid_reference[:200]
    else:
        row.paid_on = None
        row.paid_amount_micros = None
        row.paid_reference = None
    row.evidence = dict(evidence or {})
    db.flush()
    return previous


def restore(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, work_item_id: uuid.UUID,
            previous: dict[str, Any], actor_user_id: Optional[uuid.UUID], action_id: Optional[uuid.UUID]) -> None:
    """Put a settlement back the way an undone action found it."""
    if not previous.get("exists"):
        row = get(db, work_item_id)
        if row is not None:
            db.delete(row)
            db.flush()
        return
    set_state(db, organization_id=organization_id, workspace_id=workspace_id, work_item_id=work_item_id,
              state=previous["state"], source=previous.get("source") or v.SOURCE_PERSON, actor_user_id=actor_user_id,
              action_id=action_id,
              run_date=date.fromisoformat(previous["run_date"]) if previous.get("run_date") else None,
              paid_on=date.fromisoformat(previous["paid_on"]) if previous.get("paid_on") else None,
              paid_amount_micros=previous.get("paid_amount_micros"), paid_reference=previous.get("paid_reference"),
              hold_reason=previous.get("hold_reason"), evidence={"restored_by_undo": True})


__all__ = ["get", "restore", "set_state", "snapshot"]
