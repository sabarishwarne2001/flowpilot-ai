"""ARCH-50 RevOps — invoiced enterprise contracts. ARCH50-S1:contracts

A contract is how Enterprise (and every sovereign deployment, which has no card gateway) is sold: a term, seats,
an amount per billing period (month / quarter / year) in USD or INR, a tax rate, payment terms and a PO.

    activate   DRAFT -> ACTIVE: the plan is assigned (`quota_service.assign_tier`), a promo on the contract is
               reserved and redeemed in the same transaction; refused while the organization has a live gateway
               subscription (it would pin a different plan over the contract's) or another ACTIVE contract (a
               partial UNIQUE says so too)
    issue      one invoice per period whose start has arrived, idempotent (a partial UNIQUE on (contract,
               period_start) unless voided; a racing second issuer gets nothing). The last period is prorated by
               days when the term is not a whole number of periods. Discount by the promo's duration (ONCE: the
               first invoice; REPEATING: invoices starting within N months; FOREVER: all), tax on the discounted
               subtotal, due after the payment terms. Numbers: <contract number>-<period index, 3 digits>
    pay / void a person records the payment (with a reference) or voids with a reason; a voided period can be
               issued again
    end        ENDED (term over) or CANCELLED; the organization returns to the free plan unless a gateway
               subscription now carries it
    sweep      daily: ends contracts whose term is over and issues whatever periods have started
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time as dtime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.services.revops import promos
from app.services.revops import vocabulary as v
from app.services.revops.service import RevOpsError, add_months, now, to_minor_micros

FREE_TIER_KEY = "free"


def _contract(db: Session, contract_id: uuid.UUID, *, lock: bool = False) -> Any:
    row = db.execute(text("SELECT * FROM enterprise_contracts WHERE id = :id" + (" FOR UPDATE" if lock else "")),
                     {"id": contract_id}).mappings().first()
    if row is None:
        raise RevOpsError("no such contract", "NOT_FOUND", 404)
    return row


def _audit(db: Session, organization_id: uuid.UUID, actor_id: Optional[uuid.UUID], operation: str,
           **details: Any) -> None:
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(db, organization_id=organization_id, actor_id=actor_id,
                         resource_type=AuditResourceType.BILLING_ACCOUNT, resource_id=organization_id,
                         action=AuditAction.UPDATED, outcome=AuditOutcome.ALLOWED,
                         details={"operation": operation, **{k: str(x) for k, x in details.items()}})


def detail(db: Session, contract_id: uuid.UUID) -> dict[str, Any]:
    out = dict(_contract(db, contract_id))
    moment = now()
    invoices = [dict(r) for r in db.execute(text(
        "SELECT * FROM contract_invoices WHERE contract_id = :id ORDER BY period_start, issued_at"),
        {"id": contract_id}).mappings().all()]
    for inv in invoices:
        inv["overdue"] = inv["status"] == "ISSUED" and inv["due_at"] < moment
    out["invoices"] = invoices
    out["organization_name"] = db.execute(text("SELECT name FROM organizations WHERE id = :o"),
                                          {"o": out["organization_id"]}).scalar()
    return out


def list_contracts(db: Session, *, organization_id: Optional[uuid.UUID] = None) -> list[dict[str, Any]]:
    where = "WHERE c.organization_id = :o" if organization_id else ""
    rows = db.execute(text(
        f"SELECT c.*, o.name AS organization_name, "
        f"(SELECT count(*) FROM contract_invoices i WHERE i.contract_id = c.id AND i.status = 'ISSUED') AS open_invoices, "
        f"(SELECT count(*) FROM contract_invoices i WHERE i.contract_id = c.id AND i.status = 'ISSUED' AND i.due_at < :t) "
        f"AS overdue_invoices FROM enterprise_contracts c JOIN organizations o ON o.id = c.organization_id {where} "
        f"ORDER BY c.created_at DESC"), {"o": organization_id, "t": now()}).mappings().all()
    return [dict(r) for r in rows]


def organizations(db: Session, *, query: Optional[str] = None, limit: int = 200) -> list[dict[str, Any]]:
    """Organizations a contract can be drafted for, by name (the console's picker).

    Phase 3: the contract form asked for a pasted organization UUID. Archived organizations are
    left out (a contract cannot be activated for one); `has_active_contract` marks the ones that
    already carry a contract, which activation would refuse.
    """
    needle = (query or "").strip()[:120]
    pattern = "%" + needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    rows = db.execute(text(
        "SELECT o.id, o.name, o.slug, o.status, qt.key AS tier_key, "
        "EXISTS (SELECT 1 FROM enterprise_contracts c WHERE c.organization_id = o.id AND c.status = 'ACTIVE') "
        "AS has_active_contract "
        "FROM organizations o LEFT JOIN quota_tiers qt ON qt.id = o.quota_tier_id "
        "WHERE o.status <> 'ARCHIVED' AND (:all OR o.name ILIKE :p ESCAPE '\\' OR o.slug ILIKE :p ESCAPE '\\') "
        "ORDER BY lower(o.name), o.slug LIMIT :limit"),
        {"all": needle == "", "p": pattern, "limit": limit}).mappings().all()
    return [dict(r) for r in rows]


def create(db: Session, *, spec: dict[str, Any], actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    number = str(spec["contract_number"]).strip().upper()
    if db.execute(text("SELECT 1 FROM enterprise_contracts WHERE contract_number = :n"), {"n": number}).first():
        raise RevOpsError(f"contract number {number} is taken", "CONTRACT_NUMBER_TAKEN")
    term_start, term_end = spec["term_start"], spec["term_end"]
    if term_end <= term_start:
        raise RevOpsError("the term must end after it starts", "CONTRACT_TERM_TOO_LONG", 422)
    if add_months(term_start, v.MAX_CONTRACT_TERM_MONTHS) < term_end:
        raise RevOpsError(f"a contract term is at most {v.MAX_CONTRACT_TERM_MONTHS} months", "CONTRACT_TERM_TOO_LONG",
                          422)
    if not db.execute(text("SELECT 1 FROM quota_tiers WHERE key = :k AND published_at IS NOT NULL AND is_active"),
                      {"k": spec["tier_key"]}).first():
        raise RevOpsError(f"no published tier named {spec['tier_key']!r}", "TIER_NOT_PUBLISHED", 422)
    promo_id = None
    if spec.get("promo_code"):
        promo_id = db.execute(text("SELECT id FROM promo_codes WHERE code = :c"),
                              {"c": promos.normalize_code(spec["promo_code"])}).scalar()
        if promo_id is None:
            raise RevOpsError("this promo code does not exist", "PROMO_UNKNOWN", 404)
    contract_id = uuid.uuid4()
    db.execute(text(
        "INSERT INTO enterprise_contracts (id, organization_id, contract_number, tier_key, seats, currency, "
        "billing_interval, amount_per_period_micros, tax_rate_bps, term_start, term_end, payment_terms_days, "
        "po_number, billing_email, promo_code_id, notes, created_by_user_id) VALUES (:id, :o, :n, :t, :s, :c, :i, "
        ":a, :tax, :ts, :te, :terms, :po, :email, :promo, :notes, :u)"),
        {"id": contract_id, "o": spec["organization_id"], "n": number, "t": spec["tier_key"], "s": int(spec["seats"]),
         "c": spec["currency"], "i": spec["billing_interval"],
         "a": int(spec["amount_per_period"]) * v.MICROS_PER_MINOR_UNIT, "tax": int(spec.get("tax_rate_bps") or 0),
         "ts": term_start, "te": term_end, "terms": int(spec.get("payment_terms_days", 30)),
         "po": spec.get("po_number") or None, "email": spec.get("billing_email") or None, "promo": promo_id,
         "notes": spec.get("notes") or None, "u": actor_id})
    _audit(db, spec["organization_id"], actor_id, "contract.created", contract_id=contract_id, number=number)
    db.flush()
    return detail(db, contract_id)


def _live_subscription(db: Session, organization_id: uuid.UUID) -> bool:
    from app.models.subscription import LIVE_SUBSCRIPTION_STATUSES

    live = [s.value if hasattr(s, "value") else str(s) for s in LIVE_SUBSCRIPTION_STATUSES]
    return bool(db.execute(text(
        "SELECT 1 FROM subscriptions s JOIN billing_accounts a ON a.id = s.billing_account_id "
        "WHERE a.organization_id = :o AND s.status::text = ANY(:live) LIMIT 1"),
        {"o": organization_id, "live": live}).first())


def activate(db: Session, *, contract_id: uuid.UUID, actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    from app.services import quota_service

    c = _contract(db, contract_id, lock=True)
    if c["status"] != "DRAFT":
        raise RevOpsError(f"contract {c['contract_number']} is {c['status']}", "CONTRACT_NOT_DRAFT")
    org = c["organization_id"]
    # FOR NO KEY UPDATE: serialises contract changes without blocking FK checks
    # (e.g. an independent audit write) that reference this organization.
    db.execute(text("SELECT 1 FROM organizations WHERE id = :o FOR NO KEY UPDATE"), {"o": org})
    if _live_subscription(db, org):
        raise RevOpsError("the organization has a live gateway subscription; cancel it before an invoiced contract "
                          "carries the plan", "SUBSCRIPTION_ACTIVE")
    if db.execute(text("SELECT 1 FROM enterprise_contracts WHERE organization_id = :o AND status = 'ACTIVE'"),
                  {"o": org}).first():
        raise RevOpsError("the organization already has an active contract", "CONTRACT_ACTIVE_EXISTS")
    if c["promo_code_id"] is not None:
        code = db.execute(text("SELECT code FROM promo_codes WHERE id = :p"), {"p": c["promo_code_id"]}).scalar_one()
        reservation = promos.reserve(db, organization_id=org, code=code, tier_key=c["tier_key"],
                                     interval=c["billing_interval"], currency=c["currency"],
                                     list_amount_micros=int(c["amount_per_period_micros"]), for_gateway=False,
                                     contract_id=contract_id)
        promos.redeem(db, redemption_id=reservation["redemption_id"])
    try:
        quota_service.assign_tier(db, organization_id=org, tier_key=c["tier_key"])
    except quota_service.QuotaTierValidationError as exc:
        raise RevOpsError(str(exc), "TIER_NOT_PUBLISHED", 422) from exc
    moment = now()
    try:
        with db.begin_nested():
            db.execute(text("UPDATE enterprise_contracts SET status = 'ACTIVE', activated_at = :t, "
                            "activated_by_user_id = :u, updated_at = :t WHERE id = :id"),
                       {"t": moment, "u": actor_id, "id": contract_id})
    except Exception as exc:  # noqa: BLE001 -- two activations racing: the partial UNIQUE answers
        if "uq_enterprise_contracts_one_active" in str(exc):
            raise RevOpsError("the organization already has an active contract", "CONTRACT_ACTIVE_EXISTS") from exc
        raise
    quota_service.clear_cache()
    _audit(db, org, actor_id, "contract.activated", contract_id=contract_id, tier_key=c["tier_key"])
    db.flush()
    issue_due_invoices(db, contract_id=contract_id)
    return detail(db, contract_id)


def periods(c: Any) -> list[tuple[int, date, date]]:
    months = v.MONTHS_IN[c["billing_interval"]]
    out, index, start = [], 1, c["term_start"]
    while start < c["term_end"]:
        end = min(add_months(c["term_start"], months * index), c["term_end"])
        out.append((index, start, end))
        start = end
        index += 1
    return out


def _period_amount(c: Any, index: int, start: date, end: date) -> int:
    months = v.MONTHS_IN[c["billing_interval"]]
    full_end = add_months(c["term_start"], months * index)
    full_start = add_months(c["term_start"], months * (index - 1))
    amount = int(c["amount_per_period_micros"])
    if end < full_end:  # a short final period: prorate by days
        amount = to_minor_micros(Decimal(amount) * Decimal((end - start).days) / Decimal((full_end - full_start).days))
    return amount


def _discount(db: Session, c: Any, index: int, start: date, subtotal: int) -> int:
    if c["promo_code_id"] is None:
        return 0
    promo = db.execute(text("SELECT * FROM promo_codes WHERE id = :p"), {"p": c["promo_code_id"]}).mappings().one()
    applies = {"ONCE": index == 1, "FOREVER": True,
               "REPEATING": start < add_months(c["term_start"], int(promo["duration_in_months"] or 0))}[promo["duration"]]
    return promos.discount_for(promo, subtotal) if applies else 0


def issue_due_invoices(db: Session, *, contract_id: uuid.UUID, through: Optional[date] = None) -> list[uuid.UUID]:
    c = _contract(db, contract_id, lock=True)
    if c["status"] != "ACTIVE":
        raise RevOpsError(f"contract {c['contract_number']} is {c['status']}", "CONTRACT_NOT_ACTIVE")
    moment = now()
    horizon = through or moment.date()
    created: list[uuid.UUID] = []
    for index, start, end in periods(c):
        if start > horizon:
            break
        if db.execute(text("SELECT 1 FROM contract_invoices WHERE contract_id = :c AND period_start = :s "
                           "AND status <> 'VOID'"), {"c": contract_id, "s": start}).first():
            continue
        subtotal = _period_amount(c, index, start, end)
        discount = _discount(db, c, index, start, subtotal)
        tax = to_minor_micros(Decimal(subtotal - discount) * Decimal(int(c["tax_rate_bps"])) / Decimal(10000))
        voided = int(db.execute(text("SELECT count(*) FROM contract_invoices WHERE contract_id = :c AND period_start = :s"),
                                {"c": contract_id, "s": start}).scalar_one())
        number = f"{c['contract_number']}-{index:03d}" + (f"R{voided}" if voided else "")
        invoice_id = uuid.uuid4()
        issued = max(moment, datetime.combine(start, dtime.min, tzinfo=timezone.utc))
        try:
            with db.begin_nested():
                db.execute(text(
                    "INSERT INTO contract_invoices (id, contract_id, organization_id, invoice_number, period_start, "
                    "period_end, currency, subtotal_micros, discount_micros, tax_micros, total_micros, issued_at, "
                    "due_at) VALUES (:id, :c, :o, :n, :s, :e, :cur, :sub, :d, :tax, :tot, :iss, :due)"),
                    {"id": invoice_id, "c": contract_id, "o": c["organization_id"], "n": number, "s": start, "e": end,
                     "cur": c["currency"], "sub": subtotal, "d": discount, "tax": tax, "tot": subtotal - discount + tax,
                     "iss": issued, "due": issued + timedelta(days=int(c["payment_terms_days"]))})
        except Exception as exc:  # noqa: BLE001 -- another issuer took this period first
            if "uq_contract_invoices_period" in str(exc) or "contract_invoices_invoice_number_key" in str(exc):
                continue
            raise
        created.append(invoice_id)
    db.flush()
    return created


def mark_paid(db: Session, *, invoice_id: uuid.UUID, reference: str, paid_at: Optional[datetime],
              actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    row = db.execute(text("SELECT contract_id, organization_id, status FROM contract_invoices WHERE id = :id FOR UPDATE"),
                     {"id": invoice_id}).first()
    if row is None:
        raise RevOpsError("no such invoice", "NOT_FOUND", 404)
    if row[2] != "ISSUED":
        raise RevOpsError(f"the invoice is {row[2]}", "INVOICE_NOT_ISSUED")
    db.execute(text("UPDATE contract_invoices SET status = 'PAID', paid_at = :t, payment_reference = :r WHERE id = :id"),
               {"t": paid_at or now(), "r": str(reference)[:128], "id": invoice_id})
    _audit(db, row[1], actor_id, "contract.invoice_paid", invoice_id=invoice_id)
    db.flush()
    return detail(db, row[0])


def void(db: Session, *, invoice_id: uuid.UUID, reason: str, actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    row = db.execute(text("SELECT contract_id, organization_id, status FROM contract_invoices WHERE id = :id FOR UPDATE"),
                     {"id": invoice_id}).first()
    if row is None:
        raise RevOpsError("no such invoice", "NOT_FOUND", 404)
    if row[2] != "ISSUED":
        raise RevOpsError(f"the invoice is {row[2]}", "INVOICE_NOT_ISSUED")
    db.execute(text("UPDATE contract_invoices SET status = 'VOID', void_reason = :r WHERE id = :id"),
               {"r": str(reason)[:200], "id": invoice_id})
    _audit(db, row[1], actor_id, "contract.invoice_voided", invoice_id=invoice_id)
    db.flush()
    return detail(db, row[0])


def end(db: Session, *, contract_id: uuid.UUID, reason: str, actor_id: Optional[uuid.UUID]) -> dict[str, Any]:
    from app.services import quota_service

    if reason not in v.CONTRACT_END_REASONS:
        raise RevOpsError(f"{reason} is not an end reason", "CONTRACT_NOT_ACTIVE", 422)
    c = _contract(db, contract_id, lock=True)
    if c["status"] not in ("ACTIVE", "DRAFT"):
        raise RevOpsError(f"contract {c['contract_number']} is {c['status']}", "CONTRACT_NOT_ACTIVE")
    status = "ENDED" if (c["status"] == "ACTIVE" and reason == "TERM_ENDED") else "CANCELLED"
    moment = now()
    db.execute(text("UPDATE enterprise_contracts SET status = :s, ended_at = :t, end_reason = :r, updated_at = :t "
                    "WHERE id = :id"), {"s": status, "t": moment, "r": reason, "id": contract_id})
    if c["status"] == "ACTIVE" and not _live_subscription(db, c["organization_id"]):
        try:
            quota_service.assign_tier(db, organization_id=c["organization_id"], tier_key=FREE_TIER_KEY)
        except quota_service.QuotaTierValidationError:
            pass  # no free tier published: the organization keeps its tier row; nothing else to fall back to
        quota_service.clear_cache()
    _audit(db, c["organization_id"], actor_id, "contract.ended", contract_id=contract_id, reason=reason)
    db.flush()
    return detail(db, contract_id)


def sweep(db: Session) -> dict[str, int]:
    today = now().date()
    ended = issued = 0
    for (cid,) in db.execute(text("SELECT id FROM enterprise_contracts WHERE status = 'ACTIVE' AND term_end <= :d"),
                             {"d": today}).all():
        issue_due_invoices(db, contract_id=cid, through=today)
        end(db, contract_id=cid, reason="TERM_ENDED", actor_id=None)
        ended += 1
    for (cid,) in db.execute(text("SELECT id FROM enterprise_contracts WHERE status = 'ACTIVE'")).all():
        issued += len(issue_due_invoices(db, contract_id=cid, through=today))
    return {"contracts_ended": ended, "invoices_issued": issued}


__all__ = ["FREE_TIER_KEY", "activate", "create", "detail", "end", "issue_due_invoices", "list_contracts",
           "mark_paid", "periods", "sweep", "void"]
