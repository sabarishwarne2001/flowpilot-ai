"""Supplier statement reconciliation (owner addition B). Pure: a statement and the ledger in, the
differences out.

A supplier's statement of account is their book of our account: the invoices they billed us
(debits), the payments and credit notes they applied (credits), and the balance they say we owe.
Helm compares it with the Promise ledger of the same workspace: our invoices from that supplier,
what we paid and when, and the credit notes we hold. Each difference names the evidence on both
sides and how it moves their balance against ours, so the balance difference is explained line by
line and whatever is left is said to be unexplained. Nothing here marks anything paid or moves money;
the mandate drafts an email.

Their balance minus ours is, by construction, the sum of the differences' effects plus what lies
before both records (an opening balance neither side's lines explain):

    their balance = opening + their debits - their credits
    our balance   = our invoices - our credit notes - our payments      (as of the statement date)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import lru_cache
from typing import Any, Iterable, Optional

from app.services.helm import identity as ident
from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.bank_match import mentions_document
from app.services.helm.snapshot import Payable, StatementEntry, SupplierStatement

#: Differences, in the order the email lists them.
NOT_IN_OUR_BOOKS = "NOT_IN_OUR_BOOKS"  # they billed an invoice we do not have
AMOUNT_DIFFERS = "AMOUNT_DIFFERS"  # the same invoice, a different amount
NOT_ON_THEIR_STATEMENT = "NOT_ON_THEIR_STATEMENT"  # our invoice from them is missing from their statement
PAYMENT_NOT_APPLIED = "PAYMENT_NOT_APPLIED"  # we paid; their statement shows no such payment
PAYMENT_AMOUNT_DIFFERS = "PAYMENT_AMOUNT_DIFFERS"  # they applied our payment at another amount
CREDIT_NOT_APPLIED = "CREDIT_NOT_APPLIED"  # a credit note we hold is not on their statement
CREDIT_NOT_IN_OUR_BOOKS = "CREDIT_NOT_IN_OUR_BOOKS"  # a payment or credit they show that we have no record of
BALANCE_DIFFERS = "BALANCE_DIFFERS"
ORDER = (NOT_IN_OUR_BOOKS, AMOUNT_DIFFERS, NOT_ON_THEIR_STATEMENT, PAYMENT_NOT_APPLIED, PAYMENT_AMOUNT_DIFFERS,
         CREDIT_NOT_APPLIED, CREDIT_NOT_IN_OUR_BOOKS, BALANCE_DIFFERS)

#: A payment shows on their statement from a few days before our record (a value date) to a few weeks
#: after (they apply receipts late).
APPLIED_BEFORE_DAYS = 3
APPLIED_AFTER_DAYS = 21
#: An invoice line with no number we know matches ours on its exact amount within this many days.
SAME_INVOICE_DAYS = 3
_CREDIT_NOTE_WORDS = ("credit note", "credit memo", "cn ", "cn-", "cn/", "c/n", "return", "rebate", "discount")


@dataclass(frozen=True)
class Difference:
    code: str
    summary: str
    #: How this difference moves their balance against ours (their minus ours), in micros.
    effect_micros: int
    their_micros: Optional[int] = None
    our_micros: Optional[int] = None
    evidence: tuple[dict[str, Any], ...] = ()

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, "summary": self.summary, "effect_micros": self.effect_micros,
                "their_micros": self.their_micros, "our_micros": self.our_micros, "evidence": list(self.evidence)}


@dataclass(frozen=True)
class Reconciliation:
    statement: SupplierStatement
    currency: Optional[str]
    their_balance_micros: Optional[int]
    our_balance_micros: int
    differences: tuple[Difference, ...]
    agreed_lines: int
    period_start: Optional[date]
    period_end: Optional[date]
    #: Their balance minus ours that no line explains (an opening balance before both records).
    unexplained_micros: Optional[int] = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def agrees(self) -> bool:
        return not self.differences

    @property
    def balance_gap_micros(self) -> Optional[int]:
        if self.their_balance_micros is None:
            return None
        return self.their_balance_micros - self.our_balance_micros

    def as_json(self) -> dict[str, Any]:
        st = self.statement
        return {
            "statement_work_item_id": st.work_item_id, "statement": st.document,
            "counterparty_key": st.counterparty_key, "counterparty": st.counterparty_name, "currency": self.currency,
            "statement_date": st.statement_date.isoformat() if st.statement_date else None,
            "period_start": self.period_start.isoformat() if self.period_start else None,
            "period_end": self.period_end.isoformat() if self.period_end else None,
            "their_balance_micros": self.their_balance_micros, "our_balance_micros": self.our_balance_micros,
            "unexplained_micros": self.unexplained_micros, "agreed_lines": self.agreed_lines,
            "differences": [d.as_json() for d in self.differences], "notes": list(self.notes),
        }


def _say(micros: Optional[int], currency: Optional[str]) -> str:
    return money.say(micros, currency) if micros is not None else "an unreadable amount"


def _day(on: Optional[date]) -> str:
    return money.day(on) if on else "an unstated date"


def _their(entry: StatementEntry, st: SupplierStatement) -> dict[str, Any]:
    return {"side": "theirs", "statement_work_item_id": st.work_item_id, "document": st.document,
            "line": entry.line_no, "on": entry.on.isoformat() if entry.on else None,
            "reference": entry.reference[:80], "description": entry.description[:200],
            "debit_micros": entry.debit_micros, "credit_micros": entry.credit_micros}


def _ours(p: Payable, **extra: Any) -> dict[str, Any]:
    return {"side": "ours", "work_item_id": p.work_item_id, "promise_id": p.promise_id, "document": p.document,
            "number": p.document_number, "invoice_date": p.invoice_date.isoformat() if p.invoice_date else None,
            "billed_micros": billed(p), "settlement": p.settlement_state, **extra}


def billed(p: Payable) -> int:
    """The invoice's own amount, before any credit note: what their statement debits."""
    return int(p.gross_micros if p.gross_micros is not None else p.amount_micros)


def credited(p: Payable) -> int:
    return max(0, billed(p) - int(p.amount_micros))


def paid_by(p: Payable, on: Optional[date]) -> bool:
    if p.settlement_state != v.SETTLEMENT_PAID:
        return False
    return on is None or p.paid_on is None or p.paid_on <= on


def paid_amount(p: Payable) -> int:
    return int(p.paid_amount_micros) if p.paid_amount_micros else int(p.amount_micros)


@lru_cache(maxsize=8192)
def _name_key(name: Optional[str]) -> Optional[str]:
    return ident.key_for(name)


def same_party(p: Payable, st: SupplierStatement) -> bool:
    if p.workspace_id != st.workspace_id:
        return False
    if st.counterparty_key and p.counterparty_key == st.counterparty_key:
        return True
    a, b = _name_key(p.counterparty_name), _name_key(st.counterparty_name)
    return bool(a and b and a == b)


def _text(entry: StatementEntry) -> str:
    return f"{entry.reference} {entry.description}"


def _invoice_for(entry: StatementEntry, ours: list[Payable], taken: set[str]) -> tuple[Optional[Payable], str]:
    """Our invoice this debit line bills: by its number, else (one only) by its exact amount and date."""
    by_number = [p for p in ours if p.promise_id not in taken and p.document_number
                 and mentions_document(p.document_number, _text(entry))]
    if len(by_number) == 1:
        return by_number[0], "number"
    if len(by_number) > 1:
        exact = [p for p in by_number if billed(p) == entry.debit_micros]
        return (exact[0], "number") if len(exact) == 1 else (None, "")
    if entry.on is None or entry.debit_micros is None:
        return None, ""
    by_amount = [p for p in ours if p.promise_id not in taken and billed(p) == entry.debit_micros
                 and p.invoice_date and abs((p.invoice_date - entry.on).days) <= SAME_INVOICE_DAYS]
    return (by_amount[0], "amount and date") if len(by_amount) == 1 else (None, "")


def _applied(p: Payable, credits: list[StatementEntry], used: set[int], rounding: int) -> Optional[StatementEntry]:
    """Their credit line for our payment of `p`: one naming the invoice, else one of the paid amount
    dated within the window around our payment."""
    named = [e for e in credits if e.line_no not in used and p.document_number
             and mentions_document(p.document_number, _text(e)) and not _is_credit_note(e)]
    if named:
        return min(named, key=lambda e: abs((e.credit_micros or 0) - paid_amount(p)))
    amount = paid_amount(p)
    window = [e for e in credits if e.line_no not in used and e.credit_micros is not None
              and abs(e.credit_micros - amount) <= rounding and not _is_credit_note(e)
              and (p.paid_on is None or e.on is None or
                   p.paid_on - timedelta(days=APPLIED_BEFORE_DAYS) <= e.on
                   <= p.paid_on + timedelta(days=APPLIED_AFTER_DAYS))]
    return window[0] if len(window) == 1 else None


def _is_credit_note(entry: StatementEntry) -> bool:
    said = f" {_text(entry).lower()} "
    return any(word in said for word in _CREDIT_NOTE_WORDS)


def _credit_applied(p: Payable, credits: list[StatementEntry], used: set[int],
                    rounding: int) -> Optional[StatementEntry]:
    amount = credited(p)
    found = [e for e in credits if e.line_no not in used and e.credit_micros is not None
             and abs(e.credit_micros - amount) <= rounding
             and (_is_credit_note(e) or (p.document_number and mentions_document(p.document_number, _text(e))))]
    return found[0] if found else None


def reconcile(statement: SupplierStatement, payables: Iterable[Payable], *, rounding_micros: int = money.MICROS
              ) -> Reconciliation:
    """Compare one supplier statement with our ledger for that supplier, in the statement's workspace.

    Amounts within `rounding_micros` of each other agree (paise and cents differences in totals)."""
    st = statement
    currency = st.currency
    ours = [p for p in payables if same_party(p, st) and (currency is None or p.currency in (None, currency))]
    if currency is None:
        currency = next((p.currency for p in ours if p.currency), None)
    end = st.statement_date
    dated = [e.on for e in st.entries if e.on]
    start = st.period_start or (min(dated) if dated else None)
    ours = [p for p in ours if end is None or p.invoice_date is None or p.invoice_date <= end]

    differences: list[Difference] = []
    notes: list[str] = []
    agreed = 0
    taken: set[str] = set()

    # Their invoices (debits) against ours.
    for entry in (e for e in st.entries if e.debit_micros):
        mine, how = _invoice_for(entry, ours, taken)
        if mine is None:
            differences.append(Difference(
                NOT_IN_OUR_BOOKS,
                f"Their statement bills {_say(entry.debit_micros, currency)} on {_day(entry.on)}"
                f"{' (' + entry.reference + ')' if entry.reference else ''}"
                f"{': ' + entry.description[:80] if entry.description and entry.description != entry.reference else ''}"
                ", and Helm has no such invoice from them. Ask for a copy before it is paid.",
                effect_micros=int(entry.debit_micros or 0), their_micros=entry.debit_micros,
                evidence=(_their(entry, st),)))
            continue
        taken.add(mine.promise_id)
        if abs(int(entry.debit_micros or 0) - billed(mine)) > rounding_micros:
            differences.append(Difference(
                AMOUNT_DIFFERS,
                f"{mine.document_number or mine.document}: their statement bills "
                f"{_say(entry.debit_micros, currency)}; the invoice we hold is for {_say(billed(mine), currency)}.",
                effect_micros=int(entry.debit_micros or 0) - billed(mine), their_micros=entry.debit_micros,
                our_micros=billed(mine), evidence=(_their(entry, st), _ours(mine, matched_by=how))))
        else:
            agreed += 1

    # Our invoices from them in the statement's period that it does not list.
    if start is not None:
        for p in ours:
            if p.promise_id in taken or p.invoice_date is None or p.invoice_date < start:
                continue
            differences.append(Difference(
                NOT_ON_THEIR_STATEMENT,
                f"{p.document_number or p.document} of {_day(p.invoice_date)} for {_say(billed(p), currency)} "
                "is in our books but not on their statement.",
                effect_micros=-billed(p), our_micros=billed(p), evidence=(_ours(p),)))
    elif st.entries:
        notes.append("The statement's lines carry no dates, so invoices it may have left out are not listed.")

    # Our payments and credit notes against their credits.
    credits = [e for e in st.entries if e.credit_micros]
    used: set[int] = set()
    for p in sorted(ours, key=lambda p: (p.paid_on or date.max, p.promise_id)):
        if not paid_by(p, end) or (start is not None and p.paid_on is not None and p.paid_on < start):
            continue
        entry = _applied(p, credits, used, rounding_micros)
        evidence = _ours(p, paid_on=p.paid_on.isoformat() if p.paid_on else None, paid_micros=paid_amount(p),
                         paid_reference=p.paid_reference, paid_source=p.settlement_source)
        if entry is None:
            differences.append(Difference(
                PAYMENT_NOT_APPLIED,
                f"We paid {_say(paid_amount(p), currency)} for {p.document_number or p.document} on "
                f"{_day(p.paid_on)}{' (reference ' + p.paid_reference + ')' if p.paid_reference else ''}; "
                "their statement does not show it.",
                effect_micros=paid_amount(p), our_micros=paid_amount(p), evidence=(evidence,)))
            continue
        used.add(entry.line_no)
        if abs(int(entry.credit_micros or 0) - paid_amount(p)) > rounding_micros:
            differences.append(Difference(
                PAYMENT_AMOUNT_DIFFERS,
                f"Our payment for {p.document_number or p.document} was {_say(paid_amount(p), currency)}; "
                f"their statement applies {_say(entry.credit_micros, currency)}.",
                effect_micros=paid_amount(p) - int(entry.credit_micros or 0), their_micros=entry.credit_micros,
                our_micros=paid_amount(p), evidence=(_their(entry, st), evidence)))
        else:
            agreed += 1
    for p in ours:
        if not credited(p) or (start is not None and p.invoice_date is not None and p.invoice_date < start):
            continue
        entry = _credit_applied(p, credits, used, rounding_micros)
        if entry is None:
            differences.append(Difference(
                CREDIT_NOT_APPLIED,
                f"We hold a credit of {_say(credited(p), currency)} against {p.document_number or p.document}; "
                "their statement does not show it.",
                effect_micros=credited(p), our_micros=credited(p), evidence=(_ours(p, credited_micros=credited(p)),)))
        else:
            used.add(entry.line_no)
            agreed += 1
    for entry in credits:
        if entry.line_no in used:
            continue
        differences.append(Difference(
            CREDIT_NOT_IN_OUR_BOOKS,
            f"Their statement shows {_say(entry.credit_micros, currency)} received or credited on {_day(entry.on)}"
            f"{' (' + entry.reference + ')' if entry.reference else ''}"
            f"{': ' + entry.description[:80] if entry.description and entry.description != entry.reference else ''}"
            ", and Helm has no record of it. If it is a payment we made, record it before paying again.",
            effect_micros=-int(entry.credit_micros or 0), their_micros=entry.credit_micros,
            evidence=(_their(entry, st),)))

    our_balance = sum(int(p.amount_micros) for p in ours if not paid_by(p, end))
    their_balance = st.closing_balance_micros
    unexplained = None
    if their_balance is not None and abs(their_balance - our_balance) > rounding_micros:
        gap = their_balance - our_balance
        explained = sum(d.effect_micros for d in differences)
        unexplained = gap - explained
        rest = ""
        if abs(unexplained) > rounding_micros:
            rest = (f" {_say(abs(unexplained), currency)} of it is not explained by any line: usually invoices or "
                    "payments from before this statement"
                    + (f" (their opening balance is {_say(st.opening_balance_micros, currency)})"
                       if st.opening_balance_micros is not None else "") + ".")
        elif differences:
            rest = " The differences above explain all of it."
        differences.append(Difference(
            BALANCE_DIFFERS,
            f"Their statement says we owe {_say(their_balance, currency)} at {_day(end)}; our books say "
            f"{_say(our_balance, currency)}, a difference of {_say(abs(gap), currency)}.{rest}",
            effect_micros=0, their_micros=their_balance, our_micros=our_balance,
            evidence=({"side": "theirs", "statement_work_item_id": st.work_item_id, "document": st.document,
                       "closing_balance_micros": their_balance},)))
    elif their_balance is None:
        notes.append("Their closing balance could not be read, so only the lines are compared.")
    else:
        unexplained = 0
    differences.sort(key=lambda d: ORDER.index(d.code))
    return Reconciliation(statement=st, currency=currency, their_balance_micros=their_balance,
                          our_balance_micros=our_balance, differences=tuple(differences), agreed_lines=agreed,
                          period_start=start, period_end=end, unexplained_micros=unexplained, notes=tuple(notes))


__all__ = ["AMOUNT_DIFFERS", "BALANCE_DIFFERS", "CREDIT_NOT_APPLIED", "CREDIT_NOT_IN_OUR_BOOKS", "Difference",
           "NOT_IN_OUR_BOOKS", "NOT_ON_THEIR_STATEMENT", "ORDER", "PAYMENT_AMOUNT_DIFFERS", "PAYMENT_NOT_APPLIED",
           "Reconciliation", "billed", "reconcile", "same_party"]
