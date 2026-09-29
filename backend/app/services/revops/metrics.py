"""ARCH-50 RevOps — revenue metrics. ARCH50-S1:revenue-metrics

MRR per currency, never summed across currencies (a USD and an INR customer are two numbers, not one):

  subscriptions  every gateway subscription live at the moment asked about. Its price is what its checkout sold
                 (`checkout_selections`: tier, interval, currency, unit amount, seats) -- an annual plan counts
                 1/12 a month, a seat price times the seats -- less a promo still running (REPEATING within its
                 months, FOREVER always; ONCE never recurs). A subscription with no recorded checkout falls back to
                 its pinned tier version's own price; one with neither is counted as UNPRICED, never as zero.
  contracts      every ACTIVE invoiced contract: its amount per period / months in the period, less a running promo.
  ARR = 12 x MRR. Movements between two moments per organization and currency: NEW (from nothing), EXPANSION,
  CONTRACTION, CHURN (to nothing). Receivables: ISSUED contract invoices, and the ones past due.

Point-in-time reconstruction uses each subscription's `created_at` / `canceled_at` and each contract's
`activated_at` / `ended_at`. It does NOT replay a subscription's historical seat changes (the gateway row holds
only today's seat count); a closed month's snapshot is therefore frozen by the sweep on the 1st
(`revenue_snapshots`, never overwritten), and history is read from snapshots.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, time as dtime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revops import promos
from app.services.revops import vocabulary as v
from app.services.revops.service import add_months, month_start, now


def _promo_running(promo: Optional[Any], started: datetime, at: datetime) -> bool:
    if promo is None:
        return False
    if promo["duration"] == "FOREVER":
        return True
    if promo["duration"] == "REPEATING":
        return at < datetime.combine(add_months(started.date(), int(promo["duration_in_months"] or 0)),
                                     started.timetz())
    return False


def _promo(db: Session, promo_id: Optional[uuid.UUID]) -> Optional[Any]:
    if promo_id is None:
        return None
    return db.execute(text("SELECT * FROM promo_codes WHERE id = :p"), {"p": promo_id}).mappings().first()


def org_mrr(db: Session, at: datetime) -> tuple[dict[tuple[uuid.UUID, str], int], list[dict[str, Any]]]:
    """(organization, currency) -> MRR micros at `at`, and the unpriced subscriptions."""
    out: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
    unpriced: list[dict[str, Any]] = []
    subs = db.execute(text(
        "SELECT s.id, a.organization_id, s.quota_tier_key, s.seats_purchased, s.created_at, s.canceled_at, "
        "s.status::text AS status, q.unit_amount_micros, q.currency AS tier_currency, q.billing_interval AS tier_interval "
        "FROM subscriptions s JOIN billing_accounts a ON a.id = s.billing_account_id "
        "LEFT JOIN quota_tiers q ON q.id = s.quota_tier_id "
        "WHERE s.created_at <= :t AND (s.canceled_at IS NULL OR s.canceled_at > :t)"), {"t": at}).mappings().all()
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES

    live = {s.value if hasattr(s, "value") else str(s) for s in LIVE_SUBSCRIPTION_STATUSES}
    for s in subs:
        if s["status"] not in live and s["canceled_at"] is None:
            continue  # incomplete / never paid
        sel = db.execute(text(
            "SELECT c.billing_interval, c.currency, c.unit_amount_micros, c.seats, r.promo_code_id, r.status AS rstatus "
            "FROM checkout_selections c LEFT JOIN promo_redemptions r ON r.id = c.promo_redemption_id "
            "WHERE c.organization_id = :o AND c.tier_key = :k AND c.created_at <= :created + interval '1 day' "
            "ORDER BY c.created_at DESC LIMIT 1"),
            {"o": s["organization_id"], "k": s["quota_tier_key"], "created": s["created_at"]}).mappings().first()
        seats = max(1, int(s["seats_purchased"] or 1))
        if sel is not None:
            per_period = int(sel["unit_amount_micros"]) * seats
            months = v.MONTHS_IN[sel["billing_interval"]]
            currency = sel["currency"]
            promo = _promo(db, sel["promo_code_id"]) if sel["rstatus"] == "REDEEMED" else None
            if _promo_running(promo, s["created_at"], at):
                per_period -= promos.discount_for(promo, per_period)
        elif s["unit_amount_micros"] is not None and s["tier_currency"]:
            per_period = int(s["unit_amount_micros"]) * seats
            months = v.MONTHS_IN.get(s["tier_interval"] or "month", 1)
            currency = str(s["tier_currency"]).upper()
        else:
            unpriced.append({"subscription_id": s["id"], "organization_id": s["organization_id"],
                             "tier_key": s["quota_tier_key"]})
            continue
        if currency not in v.CURRENCIES:
            unpriced.append({"subscription_id": s["id"], "organization_id": s["organization_id"],
                             "tier_key": s["quota_tier_key"], "currency": currency})
            continue
        out[(s["organization_id"], currency)] += per_period // months
    contracts = db.execute(text(
        "SELECT id, organization_id, currency, billing_interval, amount_per_period_micros, promo_code_id, activated_at "
        "FROM enterprise_contracts WHERE activated_at IS NOT NULL AND activated_at <= :t "
        "AND (ended_at IS NULL OR ended_at > :t) AND status IN ('ACTIVE', 'ENDED', 'CANCELLED')"),
        {"t": at}).mappings().all()
    for c in contracts:
        per_period = int(c["amount_per_period_micros"])
        promo = _promo(db, c["promo_code_id"])
        if _promo_running(promo, c["activated_at"], at):
            per_period -= promos.discount_for(promo, per_period)
        out[(c["organization_id"], c["currency"])] += per_period // v.MONTHS_IN[c["billing_interval"]]
    return dict(out), unpriced


def movements(before: dict[tuple[uuid.UUID, str], int],
              after: dict[tuple[uuid.UUID, str], int]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {c: {"new": 0, "expansion": 0, "contraction": 0, "churned": 0}
                                      for c in v.CURRENCIES}
    for key in set(before) | set(after):
        currency = key[1]
        a, b = before.get(key, 0), after.get(key, 0)
        if a == 0 and b > 0:
            out[currency]["new"] += b
        elif a > 0 and b == 0:
            out[currency]["churned"] += a
        elif b > a:
            out[currency]["expansion"] += b - a
        elif a > b:
            out[currency]["contraction"] += a - b
    return out


def compute(db: Session, *, at: Optional[datetime] = None, compare_days: int = 30) -> dict[str, Any]:
    moment = at or now()
    current, unpriced = org_mrr(db, moment)
    previous, _ = org_mrr(db, moment - timedelta(days=compare_days))
    moves = movements(previous, current)
    currencies: dict[str, Any] = {}
    for currency in v.CURRENCIES:
        mrr = sum(amount for (org, cur), amount in current.items() if cur == currency)
        customers = sorted({org for (org, cur), amount in current.items() if cur == currency and amount > 0},
                           key=str)
        contract_orgs = {r[0] for r in db.execute(text(
            "SELECT DISTINCT organization_id FROM enterprise_contracts WHERE status = 'ACTIVE' AND currency = :c"),
            {"c": currency}).all()}
        currencies[currency] = {
            "mrr_micros": mrr, "arr_micros": 12 * mrr, "customers": len(customers),
            "contract_customers": len([o for o in customers if o in contract_orgs]),
            "arpa_micros": (mrr // len(customers)) if customers else 0,
            "movements": moves[currency],
        }
    receivables: dict[str, Any] = {}
    for currency in v.CURRENCIES:
        row = db.execute(text(
            "SELECT COALESCE(sum(total_micros), 0), COALESCE(sum(total_micros) FILTER (WHERE due_at < :t), 0), "
            "count(*) FILTER (WHERE due_at < :t) FROM contract_invoices WHERE status = 'ISSUED' AND currency = :c"),
            {"t": moment, "c": currency}).one()
        receivables[currency] = {"outstanding_micros": int(row[0]), "overdue_micros": int(row[1]),
                                 "overdue_invoices": int(row[2])}
    promo_rows = db.execute(text("SELECT status, count(*), COALESCE(sum(discount_micros), 0) FROM promo_redemptions "
                                 "GROUP BY status")).all()
    return {
        "as_of": moment, "compare_days": compare_days, "currencies": currencies, "receivables": receivables,
        "unpriced_subscriptions": unpriced,
        "promo_redemptions": {r[0]: {"count": int(r[1]), "discount_micros": int(r[2])} for r in promo_rows},
        "snapshots": [dict(r) for r in db.execute(text(
            "SELECT month, currency, mrr_micros, arr_micros, new_mrr_micros, expansion_mrr_micros, "
            "contraction_mrr_micros, churned_mrr_micros, active_customers, unpriced_customers FROM revenue_snapshots "
            "ORDER BY month DESC, currency LIMIT 48")).mappings().all()],
    }


def snapshot_month(db: Session, *, month: date) -> int:
    """Freeze a CLOSED month (its last instant) per currency. Never overwrites an existing snapshot."""
    import json

    first = month_start(month)
    closes = datetime.combine(add_months(first, 1), dtime.min, tzinfo=timezone.utc) - timedelta(microseconds=1)
    if closes >= now():
        return 0
    opens = datetime.combine(first, dtime.min, tzinfo=timezone.utc) - timedelta(microseconds=1)
    after, unpriced = org_mrr(db, closes)
    before, _ = org_mrr(db, opens)
    moves = movements(before, after)
    written = 0
    for currency in v.CURRENCIES:
        mrr = sum(a for (o, c), a in after.items() if c == currency)
        customers = len({o for (o, c), a in after.items() if c == currency and a > 0})
        result = db.execute(text(
            "INSERT INTO revenue_snapshots (id, month, currency, mrr_micros, arr_micros, new_mrr_micros, "
            "expansion_mrr_micros, contraction_mrr_micros, churned_mrr_micros, active_customers, unpriced_customers, "
            "details) VALUES (:id, :m, :c, :mrr, :arr, :n, :e, :x, :ch, :cu, :up, CAST(:d AS jsonb)) "
            "ON CONFLICT (month, currency) DO NOTHING"),
            {"id": uuid.uuid4(), "m": first, "c": currency, "mrr": mrr, "arr": 12 * mrr,
             "n": moves[currency]["new"], "e": moves[currency]["expansion"], "x": moves[currency]["contraction"],
             "ch": moves[currency]["churned"], "cu": customers, "up": len(unpriced),
             "d": json.dumps({"closed_at": closes.isoformat()})})
        written += int(result.rowcount or 0)
    return written


def sweep(db: Session) -> dict[str, int]:
    """The daily RevOps sweep: reservations, redemptions, contracts, last month's snapshot."""
    from app.services.revops import contracts

    out = {"reservations_expired": promos.expire_reservations(db),
           "redemptions_confirmed": promos.confirm_from_subscriptions(db)}
    out.update(contracts.sweep(db))
    out["snapshots_written"] = snapshot_month(db, month=add_months(month_start(now().date()), -1))
    return out


__all__ = ["compute", "movements", "org_mrr", "snapshot_month", "sweep"]
