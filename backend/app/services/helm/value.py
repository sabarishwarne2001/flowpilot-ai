"""The value-held ledger: only outcomes backed by evidence, each counted once (brief §9.2, N-085).

Four kinds of outcome count, and nothing else:

* DUPLICATE_BLOCKED     a probable duplicate Helm held, which a reviewer then confirmed as a duplicate
                        (the radar finding is CONFIRMED) and which was never paid;
* OVERCHARGE_RECOVERED  an invoice Helm held for billing above the agreed price, later credited (a
                        credit note against it) or corrected (re-read at or below the agreed price);
* DISCOUNT_CAPTURED     an early-payment discount the terms state, paid inside its window, with the
                        payment date evidenced (a person's mark or a statement line);
* LATE_FEE_AVOIDED      a late fee the terms state, avoided by paying on time an invoice Helm
                        scheduled, with the payment date evidenced.

Each event's `dedupe_key` is derived from what it is about (category + document), so a rebuild, a
second sweep or two workers never count one outcome twice (a unique index refuses the second row).
Projections — what Helm *might* save — are never written here; the cockpit labels them "potential".
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.helm import HelmAction, HelmBook, HelmPromise, HelmValueEvent
from app.models.radar import AnomalyFinding
from app.services.helm import ledger, money
from app.services.helm import vocabulary as v
from app.services.helm.ledger_store import book_workspace_ids


def dedupe_key(category: str, work_item_id: Any) -> str:
    return ledger.sha256(f"value|{category}|{work_item_id}")


def _record(db: Session, book: HelmBook, *, category: str, work_item_id: uuid.UUID, workspace_id: uuid.UUID,
            currency: Optional[str], amount_micros: int, occurred_on: date, evidence: dict[str, Any],
            action_id: Optional[uuid.UUID]) -> bool:
    if not currency or amount_micros <= 0:
        return False
    result = db.execute(pg_insert(HelmValueEvent).values(
        id=uuid.uuid4(), organization_id=book.organization_id, book_id=book.id, workspace_id=workspace_id,
        work_item_id=work_item_id, category=category, dedupe_key=dedupe_key(category, work_item_id),
        currency=currency, amount_micros=int(amount_micros), occurred_on=occurred_on,
        month=occurred_on.replace(day=1), evidence=ledger.json.loads(ledger.canonical(evidence)), action_id=action_id,
    ).on_conflict_do_nothing(constraint="uq_helm_value_events_dedupe_key"))
    return bool(result.rowcount)


def _date(value: Any) -> Optional[date]:
    if value is None or isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def sweep(db: Session, book: HelmBook) -> int:
    """Record every evidence-backed outcome not yet recorded. Idempotent. Flushes."""
    workspace_ids = book_workspace_ids(db, book)
    if not workspace_ids:
        return 0
    executed = {}
    for action in db.execute(select(HelmAction).where(HelmAction.book_id == book.id,
                                                      HelmAction.state == v.ACTION_EXECUTED)).scalars():
        if action.work_item_id:
            executed.setdefault((action.kind, action.work_item_id), action)
    promises = {p.work_item_id: p for p in db.execute(
        select(HelmPromise).where(HelmPromise.workspace_id.in_(workspace_ids),
                                  HelmPromise.kind == v.KIND_PAYMENT_DUE)).scalars()}
    discounts = {p.work_item_id: p for p in db.execute(
        select(HelmPromise).where(HelmPromise.workspace_id.in_(workspace_ids),
                                  HelmPromise.kind == v.KIND_DISCOUNT_OFFERED)).scalars()}
    recorded = 0

    # Duplicates Helm held that a reviewer confirmed, never paid.
    confirmed = set(db.execute(select(AnomalyFinding.subject_work_item_id).where(
        AnomalyFinding.workspace_id.in_(workspace_ids), AnomalyFinding.kind == "DUPLICATE_DOCUMENT",
        AnomalyFinding.status == "CONFIRMED")).scalars())
    for (kind, work_item_id), action in executed.items():
        if kind != v.ACTION_HOLD_PAYMENT:
            continue
        promise = promises.get(work_item_id)
        if promise is None:
            continue
        settlement = (promise.details or {}).get("settlement") or {}
        reason = (action.params or {}).get("reason")
        if reason == "PROBABLE_DUPLICATE" and work_item_id in confirmed and settlement.get("state") != v.SETTLEMENT_PAID:
            recorded += _record(db, book, category=v.VALUE_DUPLICATE_BLOCKED, work_item_id=work_item_id,
                                workspace_id=promise.workspace_id, currency=promise.currency,
                                amount_micros=int((promise.details or {}).get("gross_micros") or promise.amount_micros
                                                  or 0),
                                occurred_on=(action.executed_at or datetime.now(timezone.utc)).date(),
                                evidence={"finding": "confirmed duplicate", "action_id": str(action.id)},
                                action_id=action.id)
        if reason == "PRICE_ABOVE_AGREED":
            credited = int((promise.details or {}).get("credited_micros") or 0)
            overcharge = int((action.params or {}).get("overcharge_micros") or 0)
            if credited and overcharge:
                credit = next((f for f in promise.fulfilments or [] if f.get("kind") == "CREDIT"), {})
                recorded += _record(db, book, category=v.VALUE_OVERCHARGE_RECOVERED, work_item_id=work_item_id,
                                    workspace_id=promise.workspace_id, currency=promise.currency,
                                    amount_micros=min(credited, overcharge),
                                    occurred_on=_date(credit.get("on")) or datetime.now(timezone.utc).date(),
                                    evidence={"credit_note": credit.get("document"), "action_id": str(action.id)},
                                    action_id=action.id)

    # Discounts captured with an evidenced payment date inside the window.
    for work_item_id, offer in discounts.items():
        if offer.status != v.STATUS_KEPT:
            continue
        payment = next((f for f in offer.fulfilments or [] if f.get("kind") == "PAYMENT"), None)
        if not payment or not payment.get("on") or not payment.get("source"):
            continue
        action = executed.get((v.ACTION_SCHEDULE_PAYMENT, work_item_id))
        recorded += _record(db, book, category=v.VALUE_DISCOUNT_CAPTURED, work_item_id=work_item_id,
                            workspace_id=offer.workspace_id, currency=offer.currency,
                            amount_micros=int(offer.amount_micros or 0), occurred_on=_date(payment["on"]),
                            evidence={"paid_on": payment["on"], "source": payment["source"],
                                      "window_closed": offer.due_date.isoformat() if offer.due_date else None},
                            action_id=action.id if action else None)

    # Late fees avoided: a stated late fee, an invoice Helm scheduled, paid on time with evidence.
    for (kind, work_item_id), action in executed.items():
        if kind != v.ACTION_SCHEDULE_PAYMENT:
            continue
        promise = promises.get(work_item_id)
        if promise is None or promise.status != v.STATUS_KEPT:
            continue
        rate = (promise.terms or {}).get("late_fee_pct_per_month")
        settlement = (promise.details or {}).get("settlement") or {}
        if not rate or not settlement.get("paid_on"):
            continue
        fee = money.pct_of(int(promise.amount_micros or 0), Decimal(str(rate)))
        recorded += _record(db, book, category=v.VALUE_LATE_FEE_AVOIDED, work_item_id=work_item_id,
                            workspace_id=promise.workspace_id, currency=promise.currency, amount_micros=fee,
                            occurred_on=_date(settlement["paid_on"]),
                            evidence={"late_fee_pct_per_month": rate, "paid_on": settlement["paid_on"],
                                      "due_date": promise.due_date.isoformat() if promise.due_date else None},
                            action_id=action.id)
    db.flush()
    return recorded


def totals(db: Session, book: HelmBook) -> dict[str, Any]:
    rows = db.execute(select(HelmValueEvent.category, HelmValueEvent.currency, HelmValueEvent.month,
                             func.sum(HelmValueEvent.amount_micros), func.count())
                      .where(HelmValueEvent.book_id == book.id)
                      .group_by(HelmValueEvent.category, HelmValueEvent.currency, HelmValueEvent.month)).all()
    by_currency: dict[str, int] = {}
    by_category: dict[str, dict[str, int]] = {}
    by_month: dict[str, dict[str, int]] = {}
    count = 0
    for category, currency, month, amount, n in rows:
        amount = int(amount or 0)
        count += int(n)
        by_currency[currency] = by_currency.get(currency, 0) + amount
        by_category.setdefault(category, {})[currency] = by_category.get(category, {}).get(currency, 0) + amount
        key = month.isoformat()[:7]
        by_month.setdefault(key, {})[currency] = by_month.get(key, {}).get(currency, 0) + amount
    return {"total": by_currency, "by_category": by_category, "by_month": dict(sorted(by_month.items())),
            "events": count, "rule": "Only outcomes backed by evidence, each counted once."}


__all__ = ["dedupe_key", "sweep", "totals"]
