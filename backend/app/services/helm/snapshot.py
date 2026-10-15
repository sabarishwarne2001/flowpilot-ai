"""A consistent snapshot of one book: everything the Planner and the Guard decide from.

`Snapshot` is plain frozen data. `take(db, book)` first takes a shared hold on the ledger lock of
every workspace it reads (`rebuild_workspace` holds it exclusively), so it waits for a rebuild being
written and no rebuild can change the ledger until the plan's transaction ends: a plan never sees
half of a ledger rebuild. `digest` is the SHA-256 of the canonical inputs: an unchanged digest means an
unchanged plan (the Planner reuses the current one).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from app.services.helm import ledger
from app.services.helm import msme as msme_rules
from app.services.helm import terms as terms_parser
from app.services.helm import vocabulary as v


@dataclass(frozen=True)
class Flag:
    kind: str  # DUPLICATE | BANK_ACCOUNT_CHANGED | CONFLICT
    id: str
    summary: str
    severity: str = "HIGH"
    strong: bool = True


@dataclass(frozen=True)
class PriceBreach:
    promise_id: str
    description: str
    agreed_micros: int
    billed_micros: int
    above_micros: int
    overcharge_micros: int
    tolerance_bps: int
    po_number: Optional[str] = None


@dataclass(frozen=True)
class Payable:
    promise_id: str
    work_item_id: str
    workspace_id: str
    document: str
    document_number: Optional[str]
    counterparty_key: Optional[str]
    counterparty_name: Optional[str]
    currency: Optional[str]
    amount_micros: int
    gross_micros: Optional[int]
    invoice_date: Optional[date]
    due_date: Optional[date]
    received_on: Optional[date]
    terms: terms_parser.Terms
    settlement_state: str
    settlement_source: Optional[str]
    run_date: Optional[date]
    paid_on: Optional[date]
    hold_reason: Optional[str]
    settlement_version: int
    confidence: str
    status: str
    bank_account: Optional[str] = None
    po_reference: Optional[str] = None
    has_matched_po: bool = False
    flags: tuple[Flag, ...] = ()
    price_breaches: tuple[PriceBreach, ...] = ()
    udyam_number: Optional[str] = None
    msme_deadline: Optional[date] = None
    msme_explanation: Optional[str] = None
    #: Why it is held, as a code (see ledger.SettlementFacts.hold_code).
    hold_code: Optional[str] = None
    #: Hold reasons a person waived for it (e.g. "NO_MATCHED_PO": pay without a PO).
    waived: tuple[str, ...] = ()

    @property
    def open(self) -> bool:
        return self.settlement_state != v.SETTLEMENT_PAID and self.amount_micros > 0

    def held_by_helm_for(self, code: str) -> bool:
        """Held (by an action Helm proposed) for this reason, so the mandate that held it may release it."""
        return self.settlement_state == v.SETTLEMENT_HELD and self.hold_code == code and \
            self.settlement_source == v.SOURCE_HELM

    @property
    def schedulable(self) -> bool:
        return self.open and self.settlement_state not in v.BLOCKED_SETTLEMENTS

    @property
    def discount_deadline(self) -> Optional[date]:
        return self.terms.discount_deadline(self.invoice_date)

    @property
    def discount_micros(self) -> int:
        if not self.terms.has_discount:
            return 0
        from app.services.helm import money

        return money.pct_of(self.amount_micros, self.terms.discount_pct or Decimal(0))

    def flag(self, kind: str) -> Optional[Flag]:
        return next((f for f in self.flags if f.kind == kind), None)


@dataclass(frozen=True)
class Receivable:
    promise_id: str
    work_item_id: str
    workspace_id: str
    document: str
    document_number: Optional[str]
    counterparty_key: Optional[str]
    counterparty_name: Optional[str]
    currency: Optional[str]
    amount_micros: int
    invoice_date: Optional[date]
    due_date: Optional[date]
    status: str
    paid_on: Optional[date]
    confidence: str

    @property
    def open(self) -> bool:
        return self.paid_on is None and self.amount_micros > 0


@dataclass(frozen=True)
class Notice:
    promise_id: str
    obligation_id: str
    work_item_id: Optional[str]
    workspace_id: str
    title: str
    due_date: Optional[date]
    status: str
    counterparty_name: Optional[str]


@dataclass(frozen=True)
class ComplianceDoc:
    promise_id: str
    obligation_id: str
    work_item_id: Optional[str]
    workspace_id: str
    title: str
    valid_until: Optional[date]
    status: str
    counterparty_key: Optional[str]
    counterparty_name: Optional[str]


@dataclass(frozen=True)
class ReviewFacts:
    kind: str
    item_id: str
    workspace_id: str
    headline: str
    age_days: int
    assignee_user_id: Optional[str] = None


@dataclass(frozen=True)
class Cash:
    currency: str
    amount_micros: int
    as_of: date
    source: str
    age_days: int
    evidence: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Counterparty:
    key: str
    name: str
    role: str
    msme: msme_rules.MsmeStatus
    contact_email: Optional[str] = None
    behaviour: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Snapshot:
    book_id: str
    organization_id: str
    as_of: date
    horizon_days: int
    run_days: tuple[date, ...]
    cost_of_capital_bps: int
    payables: tuple[Payable, ...] = ()
    receivables: tuple[Receivable, ...] = ()
    notices: tuple[Notice, ...] = ()
    compliance: tuple[ComplianceDoc, ...] = ()
    review_items: tuple[ReviewFacts, ...] = ()
    cash: dict[str, Cash] = field(default_factory=dict)
    counterparties: dict[str, Counterparty] = field(default_factory=dict)
    workspace_ids: tuple[str, ...] = ()
    settled_action_keys: frozenset[str] = frozenset()
    reminders_sent: dict[str, tuple[int, ...]] = field(default_factory=dict)
    account_writable: bool = True
    account_reason: Optional[str] = None
    promise_counts: dict[str, int] = field(default_factory=dict)
    visibility: dict[str, Any] = field(default_factory=dict)
    digest: str = ""

    @property
    def horizon_end(self) -> date:
        return self.as_of + timedelta(days=self.horizon_days)

    def payable(self, promise_id: str) -> Optional[Payable]:
        return next((p for p in self.payables if p.promise_id == promise_id), None)

    def payable_for_work_item(self, work_item_id: str) -> Optional[Payable]:
        return next((p for p in self.payables if p.work_item_id == work_item_id), None)

    def currencies(self) -> list[str]:
        found = {p.currency for p in self.payables if p.currency} | {r.currency for r in self.receivables
                                                                      if r.currency} | set(self.cash)
        return sorted(found)

    def with_digest(self) -> "Snapshot":
        return replace(self, digest=ledger.sha256(digest_content(self)))


def digest_content(snapshot: Snapshot) -> dict[str, Any]:
    """What the digest covers: every input to a decision, nothing that is only display."""
    def payable(p: Payable) -> list[Any]:
        return [p.promise_id, p.currency, p.amount_micros, p.due_date, p.invoice_date, p.settlement_state,
                p.run_date, p.settlement_version, p.confidence, p.terms.as_json(), p.counterparty_key,
                [f.id for f in p.flags], [(b.promise_id, b.above_micros) for b in p.price_breaches],
                p.has_matched_po, p.msme_deadline, p.hold_code, list(p.waived)]

    return {
        "book": snapshot.book_id, "as_of": snapshot.as_of, "horizon": snapshot.horizon_days,
        "run_days": list(snapshot.run_days), "coc": snapshot.cost_of_capital_bps,
        "payables": [payable(p) for p in snapshot.payables],
        "receivables": [[r.promise_id, r.amount_micros, r.due_date, r.paid_on, r.counterparty_key]
                        for r in snapshot.receivables],
        "notices": [[n.promise_id, n.due_date, n.status] for n in snapshot.notices],
        "compliance": [[c.promise_id, c.valid_until, c.status] for c in snapshot.compliance],
        "review": [[r.kind, r.item_id, r.age_days] for r in snapshot.review_items],
        "cash": {c: [x.amount_micros, x.as_of] for c, x in snapshot.cash.items()},
        "counterparties": {k: [c.msme.category, c.msme.registered, c.contact_email]
                           for k, c in snapshot.counterparties.items()},
        "settled": sorted(snapshot.settled_action_keys),
        "reminders": {k: list(val) for k, val in snapshot.reminders_sent.items()},
        "account": [snapshot.account_writable, snapshot.account_reason],
    }


def run_days(*, start: date, horizon_days: int, weekdays: Sequence[int], holidays: Iterable[date] = ()) -> tuple[date, ...]:
    """Payment-run days from `start` (inclusive) through the horizon: the configured ISO weekdays,
    skipping the workspace's holidays. A run day that falls on a holiday is simply not a run day
    (the next configured weekday takes its payments)."""
    blocked = set(holidays)
    wanted = {int(d) for d in weekdays if 1 <= int(d) <= 7} or {2, 5}
    days = []
    for offset in range(horizon_days + 1):
        day = start + timedelta(days=offset)
        if day.isoweekday() in wanted and day not in blocked:
            days.append(day)
    return tuple(days)


__all__ = ["Cash", "ComplianceDoc", "Counterparty", "Flag", "Notice", "Payable", "PriceBreach", "Receivable",
           "ReviewFacts", "Snapshot", "digest_content", "run_days"]
