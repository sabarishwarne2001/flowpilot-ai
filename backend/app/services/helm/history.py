"""What the Time machine replays: a book's own past, from its documents and their evidence.

`History` is plain frozen data, like `Snapshot`, so every mandate's `backtest(history, mandate)` is a
pure function. It holds:

* `snapshot`: the book as of today with every promise, kept and broken ones included (paid invoices
  carry their evidenced payment date; the ledger records when each document arrived);
* `flags`: every radar finding Helm's limits care about, whatever became of it (open, confirmed by a
  reviewer, dismissed), with the day it was raised;
* `cash`: every cash observation (statement closing balances and typed positions), oldest first.

The day a document arrived (its received date) is the day Helm would have seen it; a finding is
dated by the later of that day and the day the radar raised it. Nothing here is estimated: where a
fact is missing the backtest says "potential", never more.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.helm import HelmBook
from app.models.payment_risk import PaymentRiskFlag
from app.models.radar import AnomalyFinding
from app.services.helm import snapshot as snap
from app.services.helm import snapshot_store
from app.services.helm.ledger_store import book_workspace_ids


@dataclass(frozen=True)
class PastFlag:
    kind: str  # DUPLICATE | BANK_ACCOUNT_CHANGED
    work_item_id: str
    status: str  # OPEN | CONFIRMED | DISMISSED (radar findings: OPEN | CONFIRMED | DISMISSED | RESOLVED)
    raised_on: date
    summary: str
    strong: bool = True
    reviewed: bool = False
    counterpart_work_item_id: Optional[str] = None


@dataclass(frozen=True)
class History:
    start: date
    end: date
    snapshot: snap.Snapshot
    flags: tuple[PastFlag, ...] = ()
    cash: tuple[snap.Cash, ...] = ()

    def within(self, on: Optional[date]) -> bool:
        return on is not None and self.start <= on <= self.end

    def flags_for(self, work_item_id: str, kind: str) -> list[PastFlag]:
        return [f for f in self.flags if f.work_item_id == work_item_id and f.kind == kind]


def _day(moment: Optional[datetime]) -> date:
    if moment is None:
        return datetime.now(timezone.utc).date()
    return moment.date()


def build(db: Session, book: HelmBook, *, start: date, end: date) -> History:
    workspace_ids = book_workspace_ids(db, book)
    current = snapshot_store.take(db, book, as_of=end)
    flags: list[PastFlag] = []
    if workspace_ids:
        for row in db.execute(
            select(AnomalyFinding.subject_work_item_id, AnomalyFinding.status, AnomalyFinding.layer,
                   AnomalyFinding.headline, AnomalyFinding.created_at, AnomalyFinding.resolved_at,
                   AnomalyFinding.counterpart_work_item_id)
            .where(AnomalyFinding.workspace_id.in_(workspace_ids), AnomalyFinding.kind == "DUPLICATE_DOCUMENT")
        ).all():
            flags.append(PastFlag(kind="DUPLICATE", work_item_id=str(row.subject_work_item_id), status=row.status,
                                  raised_on=_day(row.created_at), summary=row.headline,
                                  strong=row.layer in snapshot_store.STRONG_DUPLICATE_LAYERS,
                                  reviewed=row.resolved_at is not None,
                                  counterpart_work_item_id=str(row.counterpart_work_item_id)
                                  if row.counterpart_work_item_id else None))
        for row in db.execute(
            select(PaymentRiskFlag.work_item_id, PaymentRiskFlag.status, PaymentRiskFlag.summary,
                   PaymentRiskFlag.created_at, PaymentRiskFlag.reviewed_at)
            .where(PaymentRiskFlag.workspace_id.in_(workspace_ids), PaymentRiskFlag.kind == "BANK_ACCOUNT_CHANGED")
        ).all():
            flags.append(PastFlag(kind="BANK_ACCOUNT_CHANGED", work_item_id=str(row.work_item_id), status=row.status,
                                  raised_on=_day(row.created_at), summary=row.summary,
                                  reviewed=row.reviewed_at is not None))
    cash = snapshot_store.cash_history(db, book, workspace_ids, end)
    return History(start=start, end=end, snapshot=current,
                   flags=tuple(sorted(flags, key=lambda f: (f.raised_on, f.kind, f.work_item_id))),
                   cash=tuple(c for c in cash if c.as_of <= end))


__all__ = ["History", "PastFlag", "build"]
