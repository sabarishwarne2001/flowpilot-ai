"""Matching bank statement lines to open invoices (owner addition A): pure functions, no database.

A line of money OUT can settle an invoice we owe (a payable); a line of money IN, an invoice owed to
us (a receivable). Helm matches a line by itself only when the evidence leaves one answer:

* the amount equals the invoice exactly (or exactly the amount less its early-payment discount, when
  the line cleared by the discount deadline plus a few days for the bank; after that it is a part payment);
* the invoice number appears in the narration or reference. For a payable, the payee's full name and a
  line within the date window of its planned run (or due date) are enough too. A customer's payment is
  matched by itself only by its invoice number: a customer may owe several invoices of one amount;
* for a payable, Helm had it SCHEDULED or APPROVED (a payment Helm planned and a person approved);
* the narration names no other invoice number the book knows (that points at a different invoice);
* exactly one open invoice qualifies, and no other line of the import claims it.

Anything less is a suggestion for a person, with the reasons in words; a line with no invoice of its
amount is left unmatched for a person. A suggestion to an invoice Helm HELD (a probable duplicate, a
changed bank account) carries a warning: paying it may be the very loss the hold was there to stop.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Iterable, Optional, Sequence

from app.services.helm import money
from app.services.helm import vocabulary as v

#: Days after the planned run (or due date) a payment may clear and still be "that" payment; and before.
WINDOW_AFTER_DAYS = 10
WINDOW_BEFORE_DAYS = 3
MAX_SUGGESTIONS = 3
#: Days after the early-payment discount deadline a payment may clear and still earn the discount (the
#: payment left on time, the bank took a day or two to show it).
DISCOUNT_CLEARING_DAYS = 3

AUTO = "AUTO"
SUGGESTED = "SUGGESTED"
NONE = "NONE"

PAYABLE = "PAYABLE"
RECEIVABLE = "RECEIVABLE"

#: Words in company names that say nothing about which company it is.
_NAME_NOISE = frozenset({"private", "limited", "pvt", "ltd", "llp", "inc", "corp", "corporation", "company", "india",
                         "the", "and", "co", "enterprises", "enterprise", "traders", "trading", "services", "solutions",
                         "industries", "group", "international", "global", "agency", "sons", "ms"})


@dataclass(frozen=True)
class Target:
    """An open invoice a bank line could settle."""

    kind: str  # PAYABLE | RECEIVABLE
    promise_id: str
    work_item_id: str
    workspace_id: str
    document: str
    document_number: Optional[str]
    counterparty_name: Optional[str]
    currency: Optional[str]
    amount_micros: int
    expected_on: Optional[date] = None  # the planned run date, else the due date
    planned: bool = False  # a payable Helm SCHEDULED or a person APPROVED
    held: bool = False
    hold_reason: Optional[str] = None
    discounted_micros: Optional[int] = None  # what it costs paid inside its discount window
    discount_deadline: Optional[date] = None  # the last day of that window


@dataclass(frozen=True)
class BankLine:
    id: str
    on: date
    amount_micros: int  # in > 0, out < 0
    description: str
    reference: str = ""
    currency: Optional[str] = None


@dataclass(frozen=True)
class Candidate:
    target: Target
    reasons: tuple[str, ...]
    score: int
    by_reference: bool
    by_payee: bool
    in_window: bool
    discounted: bool
    other_documents: tuple[str, ...] = ()  # other invoice numbers the narration names


@dataclass
class Verdict:
    line: BankLine
    status: str = NONE
    target: Optional[Target] = None
    reasons: tuple[str, ...] = ()
    suggestions: list[Candidate] = field(default_factory=list)
    warning: Optional[str] = None


def _tokens(text: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (text or "").lower()) if t]


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def mentions_document(number: Optional[str], text: str) -> bool:
    """The invoice number in the narration, as whole tokens ("KP-1182" in "NEFT-KP 1182-..."), or run
    together when it is long enough not to hide inside another number ("KP1182" in "KONKANKP1182").
    A number that runs on into more digits is a different number: "INV-118" is not in "INV-1182"."""
    doc = _tokens(number or "")
    joined = _norm(number or "")
    # "S-77": letters and digits in separate parts are distinctive even when short; "12" alone is not.
    short_ok = len(doc) > 1 and any(c.isalpha() for c in joined) and any(c.isdigit() for c in joined)
    if not doc or len(joined) < (3 if short_ok else 4):
        return False
    words = _tokens(text)
    for i in range(len(words) - len(doc) + 1):
        if words[i:i + len(doc)] == doc:
            return True
    if len(joined) < 6 or joined.isdigit():
        return False
    before = r"(?<!\d)" if joined[0].isdigit() else ""
    return re.search(before + re.escape(joined) + r"(?!\d)", _norm(text)) is not None


def _only_noise(text: str) -> bool:
    """True when the letters are nothing but filler words of company names run together ("pvtltd"), or none."""
    fits = [True] + [False] * len(text)
    for end in range(1, len(text) + 1):
        fits[end] = any(fits[start] and text[start:end] in _NAME_NOISE for start in range(end))
    return fits[-1]


def mentions_payee(name: Optional[str], text: str) -> bool:
    """The payee's name in the narration: every distinctive word of it as a whole word ("KONKAN PACKAGING"),
    or the name run together the way UPI narrations write it ("KONKANPACKAGING", "KONKANPACKAGINGPVTLTD").
    One shared word ("TECHNOLOGIES" of another company) or the name inside a longer word ("PRAKASH" in
    "JAYAPRAKASH") is not the payee."""
    distinctive = [t for t in _tokens(name or "") if len(t) > 1 and t not in _NAME_NOISE and not t.isdigit()]
    if len("".join(distinctive)) < 4:
        return False
    words = _tokens(text)
    if set(distinctive) <= set(words):
        return True
    forms = {"".join(distinctive), "".join(_tokens(name or ""))}
    for i in range(len(words)):
        run = ""
        for word in words[i:i + 6]:  # the name may run together across a few words ("KONKAN PACKAGINGPVT")
            run += word
            for form in forms:
                at = run.find(form)
                while at >= 0:
                    if _only_noise(run[:at]) and _only_noise(run[at + len(form):]):
                        return True
                    at = run.find(form, at + 1)
    return False


def discount_applies(target: Target, on: Optional[date]) -> bool:
    """Whether the early-payment price still settles the invoice for a payment that cleared on that day:
    by the discount deadline, plus DISCOUNT_CLEARING_DAYS for the bank. After that, paying the discounted
    amount leaves the rest owed (a part payment), so it does not settle the invoice."""
    if not target.discounted_micros:
        return False
    if target.discount_deadline is None:
        return True  # a target built without its deadline; Helm's own (targets_from) always carry it
    if on is None:
        return False
    return (on - target.discount_deadline).days <= DISCOUNT_CLEARING_DAYS


def _numbers_named(numbers: Sequence[tuple[str, str, str]], text: str) -> list[str]:
    """The invoice numbers (prepared as number, first word, run together) that the text names."""
    words, joined = set(_tokens(text)), _norm(text)
    return [number for number, first, run in numbers
            if (first in words or run in joined) and mentions_document(number, text)]


def _others(target: Target, named: Sequence[str], text: str) -> tuple[str, ...]:
    """Invoice numbers the text names that are not this target's own. A number inside the target's own
    ("1182" in "KP-1182") is the target's number seen again, not another invoice."""
    own = _norm(target.document_number or "")
    own_named = bool(own) and mentions_document(target.document_number, text)
    out: dict[str, str] = {}
    for number in named:
        key = _norm(number)
        if key and key != own and not (own_named and key in own):
            out.setdefault(key, number)
    return tuple(out.values())


def _price(line: BankLine, target: Target) -> Optional[bool]:
    """None when the line's amount does not settle the invoice; otherwise whether it is the discounted price."""
    paid = abs(line.amount_micros)
    if line.currency and target.currency and line.currency != target.currency:
        return None
    if paid == target.amount_micros:
        return False
    if paid == target.discounted_micros and discount_applies(target, line.on):
        return True
    return None


def _candidate(line: BankLine, target: Target, named: Sequence[str] = ()) -> Optional[Candidate]:
    discounted = _price(line, target)
    if discounted is None:
        return None
    paid = abs(line.amount_micros)
    text = f"{line.description} {line.reference}"
    by_reference = mentions_document(target.document_number, text)
    by_payee = mentions_payee(target.counterparty_name, text)
    if target.expected_on is None:
        in_window = target.kind == RECEIVABLE
    else:
        days = (line.on - target.expected_on).days
        in_window = -WINDOW_BEFORE_DAYS <= days <= WINDOW_AFTER_DAYS if target.kind == PAYABLE else days >= -60
    reasons = [f"the amount is exactly {money.say(paid, target.currency)}"
               + (" (the invoice less its early-payment discount)" if discounted else "")]
    if by_reference:
        reasons.append(f"the narration names invoice {target.document_number}")
    if by_payee:
        reasons.append(f"the narration names {target.counterparty_name}")
    if target.expected_on is not None and in_window and target.kind == PAYABLE:
        reasons.append(f"it cleared within days of {'the planned run' if target.planned else 'the due date'} "
                       f"({money.day(target.expected_on)})")
    others = _others(target, named, text)
    for number in others:
        reasons.append(f"but the narration also names invoice {number}, a different invoice: check which one "
                       "this line paid")
    score = 4 * by_reference + 2 * by_payee + in_window + target.planned
    return Candidate(target=target, reasons=tuple(reasons), score=score, by_reference=by_reference,
                     by_payee=by_payee, in_window=in_window, discounted=discounted, other_documents=others)


def _automatic(candidate: Candidate) -> bool:
    if candidate.target.held or candidate.other_documents:
        return False
    if candidate.target.kind == PAYABLE:
        return candidate.target.planned and (candidate.by_reference or (candidate.by_payee and candidate.in_window))
    # A customer may owe several invoices of one amount, and names are shared or abbreviated: only the
    # invoice number says which invoice the money paid.
    return candidate.by_reference


def match_all(lines: Sequence[BankLine], targets: Iterable[Target], *,
              other_numbers: Iterable[str] = ()) -> list[Verdict]:
    """A verdict for every line. Automatic matches are unique both ways: one invoice per line and one line
    per invoice; when two lines or two invoices tie, a person decides.

    other_numbers are every invoice number the book knows, paid or not: a line that names one of them other
    than its invoice's own number points at a different invoice, so it is only a suggestion."""
    pool = list(targets)
    numbers: list[tuple[str, str, str]] = []
    for number in dict.fromkeys(n for n in other_numbers if n):
        words = _tokens(number)
        if words:
            numbers.append((number, words[0], _norm(number)))
    out: list[Verdict] = []
    claims: dict[str, list[int]] = {}
    for index, line in enumerate(lines):
        kind = PAYABLE if line.amount_micros < 0 else RECEIVABLE
        fits = [t for t in pool if t.kind == kind and _price(line, t) is not None]
        # Only for a line some invoice's amount fits: the book may know many numbers.
        named = _numbers_named(numbers, f"{line.description} {line.reference}") if fits and numbers else []
        found = [c for c in (_candidate(line, t, named) for t in fits) if c is not None]
        found.sort(key=lambda c: (-c.score, c.target.expected_on or date.max, c.target.document_number or ""))
        verdict = Verdict(line=line, suggestions=found[:MAX_SUGGESTIONS])
        automatic = [c for c in found if _automatic(c)]
        if len(automatic) == 1 and not any(c is not automatic[0] and c.score >= automatic[0].score for c in found):
            verdict.status, verdict.target, verdict.reasons = AUTO, automatic[0].target, automatic[0].reasons
            claims.setdefault(automatic[0].target.promise_id, []).append(index)
        elif found:
            verdict.status = SUGGESTED
        held = next((c.target for c in verdict.suggestions if c.target.held), None)
        if held is not None:
            verdict.warning = (f"{held.document} was on hold ({held.hold_reason or 'Helm held it'}); if this line paid "
                               "it, the hold did not stop the payment. Check before you confirm.")
        out.append(verdict)
    for promise_id, indices in claims.items():
        if len(indices) > 1:  # two lines claim one invoice: neither is automatic
            for i in indices:
                out[i].status, out[i].target, out[i].reasons = SUGGESTED, None, ()
    return out


def targets_from(snapshot) -> list[Target]:
    """The open invoices of a Helm snapshot that a bank line could settle."""
    out: list[Target] = []
    for p in snapshot.payables:
        if not p.open or p.settlement_state == v.SETTLEMENT_PAID or p.paid_on is not None:
            continue
        planned = p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)
        discount = p.amount_micros - p.discount_micros if p.discount_deadline and p.discount_micros else None
        out.append(Target(kind=PAYABLE, promise_id=p.promise_id, work_item_id=p.work_item_id,
                          workspace_id=p.workspace_id, document=p.document, document_number=p.document_number,
                          counterparty_name=p.counterparty_name, currency=p.currency, amount_micros=p.amount_micros,
                          expected_on=(p.run_date if planned and p.run_date else p.due_date), planned=planned,
                          held=p.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED),
                          hold_reason=p.hold_reason, discounted_micros=discount,
                          discount_deadline=p.discount_deadline if discount else None))
    for r in snapshot.receivables:
        if r.paid_on is not None or r.status not in v.OPEN_STATUSES:
            continue
        out.append(Target(kind=RECEIVABLE, promise_id=r.promise_id, work_item_id=r.work_item_id,
                          workspace_id=r.workspace_id, document=r.document, document_number=r.document_number,
                          counterparty_name=r.counterparty_name, currency=r.currency, amount_micros=r.amount_micros,
                          expected_on=r.due_date))
    return out


__all__ = ["AUTO", "BankLine", "Candidate", "DISCOUNT_CLEARING_DAYS", "NONE", "PAYABLE", "RECEIVABLE", "SUGGESTED",
           "Target", "Verdict", "discount_applies", "match_all", "mentions_document", "mentions_payee",
           "targets_from"]
