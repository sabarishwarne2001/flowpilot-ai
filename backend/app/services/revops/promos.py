"""ARCH-50 RevOps — promo codes. ARCH50-S1:promos

    quote     what a code would take off (never writes)
    reserve   at checkout: the code's row is locked (SELECT ... FOR UPDATE) so N concurrent checkouts cannot
              over-allocate a capped code -- capacity = cap - redeemed - live reservations; one live redemption
              per (code, organization) is a partial UNIQUE as well
    redeem    when the subscription or contract actually starts (the RevOps sweep confirms gateway checkouts
              from the subscription the webhook created); `times_redeemed` moves under the same row lock and a
              CHECK keeps it within the cap
    release / expire   a reservation that never became a subscription gives its capacity back

A code with a `gateway_coupon_id` is honoured by the gateway at checkout; one without applies to invoiced
contracts only (a gateway checkout refuses it with PROMO_NOT_AVAILABLE_ONLINE rather than promising a discount
the payment page will not show). Discounts are whole minor units.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revops import vocabulary as v
from app.services.revops.service import RevOpsError, now, to_minor_micros


def normalize_code(code: str) -> str:
    return str(code or "").strip().upper()


def _row(db: Session, code: str, *, lock: bool = False) -> Any:
    row = db.execute(text("SELECT * FROM promo_codes WHERE code = :c" + (" FOR UPDATE" if lock else "")),
                     {"c": normalize_code(code)}).mappings().first()
    if row is None:
        raise RevOpsError("this promo code does not exist", "PROMO_UNKNOWN", 404)
    return row


def discount_for(promo: Any, list_amount_micros: int) -> int:
    if promo["percent_off"] is not None:
        raw = Decimal(int(list_amount_micros)) * Decimal(promo["percent_off"]) / Decimal(100)
        return min(int(list_amount_micros), to_minor_micros(int(raw)))
    return min(int(list_amount_micros), int(promo["amount_off_micros"]))


def _live_reservations(db: Session, promo_id: uuid.UUID, at: datetime) -> int:
    return int(db.execute(text("SELECT count(*) FROM promo_redemptions WHERE promo_code_id = :p AND status = 'RESERVED' "
                               "AND expires_at > :t"), {"p": promo_id, "t": at}).scalar_one())


def _has_prior_commercial(db: Session, organization_id: uuid.UUID) -> bool:
    sub = db.execute(text(
        "SELECT 1 FROM subscriptions s JOIN billing_accounts a ON a.id = s.billing_account_id "
        "WHERE a.organization_id = :o LIMIT 1"), {"o": organization_id}).first()
    contract = db.execute(text("SELECT 1 FROM enterprise_contracts WHERE organization_id = :o AND status <> 'DRAFT' "
                               "LIMIT 1"), {"o": organization_id}).first()
    return bool(sub or contract)


def check(db: Session, promo: Any, *, organization_id: uuid.UUID, tier_key: str, interval: str, currency: str,
          at: datetime, counting_reservations: bool = True) -> None:
    if not promo["is_active"]:
        raise RevOpsError("this promo code is no longer active", "PROMO_INACTIVE")
    if promo["redeem_by"] is not None and at > promo["redeem_by"]:
        raise RevOpsError("this promo code has expired", "PROMO_EXPIRED")
    if promo["applies_to_tiers"] and tier_key not in promo["applies_to_tiers"]:
        raise RevOpsError("this promo code does not apply to this plan", "PROMO_NOT_APPLICABLE", tier_key=tier_key)
    if promo["applies_to_intervals"] and interval not in promo["applies_to_intervals"]:
        raise RevOpsError("this promo code does not apply to this billing interval", "PROMO_NOT_APPLICABLE",
                          interval=interval)
    if promo["amount_off_micros"] is not None and promo["currency"] != currency:
        raise RevOpsError(f"this promo code is an amount in {promo['currency']}", "PROMO_CURRENCY_MISMATCH")
    if promo["first_subscription_only"] and _has_prior_commercial(db, organization_id):
        raise RevOpsError("this promo code is for a first subscription only", "PROMO_FIRST_SUBSCRIPTION_ONLY")
    if promo["max_redemptions"] is not None:
        used = int(promo["times_redeemed"]) + (_live_reservations(db, promo["id"], at) if counting_reservations else 0)
        if used >= int(promo["max_redemptions"]):
            raise RevOpsError("this promo code has been fully redeemed", "PROMO_EXHAUSTED")


def quote(db: Session, *, organization_id: uuid.UUID, code: str, tier_key: str, interval: str, currency: str,
          list_amount_micros: int) -> dict[str, Any]:
    promo = _row(db, code)
    moment = now()
    check(db, promo, organization_id=organization_id, tier_key=tier_key, interval=interval, currency=currency,
          at=moment)
    live = db.execute(text("SELECT 1 FROM promo_redemptions WHERE promo_code_id = :p AND organization_id = :o "
                           "AND status IN ('RESERVED', 'REDEEMED')"), {"p": promo["id"], "o": organization_id}).first()
    if live:
        raise RevOpsError("this organization has already used this code", "PROMO_ALREADY_USED")
    discount = discount_for(promo, list_amount_micros)
    return {"code": promo["code"], "valid": True, "duration": promo["duration"],
            "duration_in_months": promo["duration_in_months"], "list_amount_micros": int(list_amount_micros),
            "discount_micros": discount, "final_amount_micros": int(list_amount_micros) - discount,
            "online": bool(promo["gateway_coupon_id"])}


def reserve(db: Session, *, organization_id: uuid.UUID, code: str, tier_key: str, interval: str, currency: str,
            list_amount_micros: int, for_gateway: bool, contract_id: Optional[uuid.UUID] = None) -> dict[str, Any]:
    promo = _row(db, code, lock=True)
    if for_gateway and not promo["gateway_coupon_id"]:
        raise RevOpsError("this promo code applies to invoiced contracts only", "PROMO_NOT_AVAILABLE_ONLINE")
    moment = now()
    check(db, promo, organization_id=organization_id, tier_key=tier_key, interval=interval, currency=currency,
          at=moment)
    discount = discount_for(promo, list_amount_micros)
    redemption_id = uuid.uuid4()
    try:
        with db.begin_nested():
            db.execute(text(
                "INSERT INTO promo_redemptions (id, promo_code_id, organization_id, status, tier_key, billing_interval, "
                "currency, list_amount_micros, discount_micros, contract_id, reserved_at, expires_at) VALUES "
                "(:id, :p, :o, 'RESERVED', :t, :i, :c, :l, :d, :k, :r, :e)"),
                {"id": redemption_id, "p": promo["id"], "o": organization_id, "t": tier_key, "i": interval,
                 "c": currency, "l": int(list_amount_micros), "d": discount, "k": contract_id, "r": moment,
                 "e": moment + timedelta(hours=v.RESERVATION_TTL_HOURS)})
    except Exception as exc:  # noqa: BLE001 -- the partial UNIQUE: one live redemption per (code, organization)
        if "uq_promo_redemptions_one_live" in str(exc):
            raise RevOpsError("this organization has already used this code", "PROMO_ALREADY_USED") from exc
        raise
    return {"redemption_id": redemption_id, "promo_code_id": promo["id"], "discount_micros": discount,
            "gateway_coupon_id": promo["gateway_coupon_id"], "code": promo["code"]}


def redeem(db: Session, *, redemption_id: uuid.UUID) -> bool:
    row = db.execute(text("SELECT promo_code_id, status FROM promo_redemptions WHERE id = :id FOR UPDATE"),
                     {"id": redemption_id}).first()
    if row is None or row[1] != "RESERVED":
        return False
    promo = db.execute(text("SELECT * FROM promo_codes WHERE id = :p FOR UPDATE"), {"p": row[0]}).mappings().one()
    if promo["max_redemptions"] is not None and int(promo["times_redeemed"]) >= int(promo["max_redemptions"]):
        raise RevOpsError("this promo code has been fully redeemed", "PROMO_EXHAUSTED")
    db.execute(text("UPDATE promo_redemptions SET status = 'REDEEMED', redeemed_at = :t WHERE id = :id"),
               {"t": now(), "id": redemption_id})
    db.execute(text("UPDATE promo_codes SET times_redeemed = times_redeemed + 1 WHERE id = :p"), {"p": row[0]})
    return True


def release(db: Session, *, redemption_id: uuid.UUID, status: str = "RELEASED") -> bool:
    assert status in ("RELEASED", "EXPIRED")
    result = db.execute(text("UPDATE promo_redemptions SET status = :s, released_at = :t WHERE id = :id "
                             "AND status = 'RESERVED'"), {"s": status, "t": now(), "id": redemption_id})
    return bool(result.rowcount)


def expire_reservations(db: Session) -> int:
    rows = db.execute(text("SELECT id FROM promo_redemptions WHERE status = 'RESERVED' AND expires_at <= :t "
                           "AND contract_id IS NULL"), {"t": now()}).all()
    return sum(1 for (rid,) in rows if release(db, redemption_id=rid, status="EXPIRED"))


def confirm_from_subscriptions(db: Session) -> int:
    """A reservation whose organization now has a live subscription to that plan, started after the checkout,
    was used: redeem it."""
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES

    live = [s.value if hasattr(s, "value") else str(s) for s in LIVE_SUBSCRIPTION_STATUSES]
    rows = db.execute(text(
        "SELECT r.id FROM promo_redemptions r WHERE r.status = 'RESERVED' AND r.contract_id IS NULL AND EXISTS ("
        " SELECT 1 FROM subscriptions s JOIN billing_accounts a ON a.id = s.billing_account_id"
        " WHERE a.organization_id = r.organization_id AND s.quota_tier_key = r.tier_key"
        " AND s.created_at >= r.reserved_at - interval '5 minutes' AND s.status::text = ANY(:live))"),
        {"live": live}).all()
    done = 0
    for (rid,) in rows:
        try:
            with db.begin_nested():
                done += int(redeem(db, redemption_id=rid))
        except RevOpsError:
            release(db, redemption_id=rid)
    return done


def create_code(db: Session, *, spec: dict[str, Any], actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    code = normalize_code(spec["code"])
    if db.execute(text("SELECT 1 FROM promo_codes WHERE code = :c"), {"c": code}).first():
        raise RevOpsError(f"promo code {code} exists", "PROMO_CODE_TAKEN")
    promo_id = uuid.uuid4()
    amount_minor = spec.get("amount_off")
    db.execute(text(
        "INSERT INTO promo_codes (id, code, description, percent_off, amount_off_micros, currency, duration, "
        "duration_in_months, max_redemptions, redeem_by, applies_to_tiers, applies_to_intervals, "
        "first_subscription_only, gateway_coupon_id, created_by_user_id) VALUES (:id, :code, :d, :pct, :amt, :cur, "
        ":dur, :months, :cap, :by, :tiers, :ints, :first, :gw, :u)"),
        {"id": promo_id, "code": code, "d": spec.get("description"), "pct": spec.get("percent_off"),
         "amt": None if amount_minor is None else int(amount_minor) * v.MICROS_PER_MINOR_UNIT,
         "cur": spec.get("currency"), "dur": spec.get("duration", "ONCE"), "months": spec.get("duration_in_months"),
         "cap": spec.get("max_redemptions"), "by": spec.get("redeem_by"), "tiers": spec.get("applies_to_tiers") or None,
         "ints": spec.get("applies_to_intervals") or None, "first": bool(spec.get("first_subscription_only")),
         "gw": spec.get("gateway_coupon_id") or None, "u": actor_id})
    db.flush()
    return get_code(db, promo_id)


def get_code(db: Session, promo_id: uuid.UUID) -> dict[str, Any]:
    row = db.execute(text("SELECT * FROM promo_codes WHERE id = :id"), {"id": promo_id}).mappings().first()
    if row is None:
        raise RevOpsError("no such promo code", "NOT_FOUND", 404)
    out = dict(row)
    out["redemptions"] = {r[0]: int(r[1]) for r in db.execute(text(
        "SELECT status, count(*) FROM promo_redemptions WHERE promo_code_id = :id GROUP BY status"), {"id": promo_id})}
    return out


def list_codes(db: Session) -> list[dict[str, Any]]:
    ids = [r[0] for r in db.execute(text("SELECT id FROM promo_codes ORDER BY created_at DESC")).all()]
    return [get_code(db, i) for i in ids]


def set_active(db: Session, *, promo_id: uuid.UUID, active: bool) -> dict[str, Any]:
    if not db.execute(text("UPDATE promo_codes SET is_active = :a WHERE id = :id RETURNING id"),
                      {"a": bool(active), "id": promo_id}).first():
        raise RevOpsError("no such promo code", "NOT_FOUND", 404)
    db.flush()
    return get_code(db, promo_id)


__all__ = ["check", "confirm_from_subscriptions", "create_code", "discount_for", "expire_reservations", "get_code",
           "list_codes", "normalize_code", "quote", "redeem", "release", "reserve", "set_active"]
