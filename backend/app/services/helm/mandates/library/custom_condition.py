"""Custom condition (Enterprise) — the owner's own rule over an invoice's facts.

"Hold any invoice where the amount is above ₹5 lakh and the supplier is new." A custom condition is
`all` or `any` of one to eight conditions `field op value` over a closed list of an invoice's facts
(`FIELDS`: nothing outside it can be named, so a condition reads only what the ledger already holds),
and one outcome:

* `hold`      Helm holds a matching invoice, with the condition's name as the reason, and proposes the
              release once the condition stops holding. Each custom mandate holds under its own code
              (`CUSTOM_CONDITION:<mandate id>`), so one never releases another's hold. The Guard refuses
              paying or releasing a matching invoice, and the optimiser leaves it out.
* `approval`  the Guard asks a person before any matching invoice is paid (never a refusal).
* `notify`    Helm tells owners and admins about each matching invoice, once.

The parameter model refuses an unknown field, an operator that does not fit the field and a value of
the wrong kind, each in plain words; `validate` adds what only the whole rule shows (conditions that
contradict each other, a hold that depends on being held). It is Enterprise only (`plan`): the
registry's validator refuses it on other plans, and the drafting prompt and the backtest leave it out.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Optional

from pydantic import BaseModel, ConfigDict, model_validator

from app.services.helm import money
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

KEY = "custom_condition"
CODE = "CUSTOM_CONDITION"
MAX_CONDITIONS = 8
MAX_NAME = 80
MAX_TEXT = 200
MAX_LIST = 20
MAX_DAYS = 3650

HOLD = "hold"
APPROVAL = "approval"
NOTIFY = "notify"
OUTCOMES = (HOLD, APPROVAL, NOTIFY)
MATCHES = ("all", "any")
#: The Guard's paying actions (guard.PAYING_ACTIONS; the Guard imports the library, not the reverse).
PAYING_ACTIONS = (v.ACTION_SCHEDULE_PAYMENT, v.ACTION_APPROVE_PAYMENT)

MONEY = "money"
NUMBER = "number"
TEXT = "text"
BOOL = "bool"
CHOICE = "choice"

COMPARE = (">", ">=", "<", "<=", "=", "!=")
OP_WORDS = {">": "is above", ">=": "is at least", "<": "is below", "<=": "is at most", "=": "is", "!=": "is not",
            "contains": "contains", "in": "is one of"}
#: How a number of days reads in a sentence: "due in fewer than 5 days".
DAY_WORDS = {">": "more than", ">=": "at least", "<": "fewer than", "<=": "at most", "=": "exactly"}
#: Days until due of 0 or less, as a person says it: "< 0" is "overdue", "<= -3" "at least 3 days overdue".
DUE_TODAY = {"<": "it is overdue", "<=": "it is due today or overdue", ">": "it is due after today",
             ">=": "it is not overdue", "=": "it is due today", "!=": "it is not due today"}
OVERDUE = {"<": "it is more than {} overdue", "<=": "it is at least {} overdue",
           ">": "it is fewer than {} overdue, or not overdue", ">=": "it is at most {} overdue, or not overdue",
           "=": "it is exactly {} overdue", "!=": "it is not exactly {} overdue"}


@dataclass(frozen=True)
class Fact:
    key: str
    label: str  # in the editor's list: "Amount"
    noun: str  # in a sentence: "the amount"
    type: str
    ops: tuple[str, ...]
    choices: tuple[tuple[str, str], ...] = ()
    #: A yes/no fact in words: (when it is true, when it is false).
    yes_no: tuple[str, str] = ("", "")
    unit: Optional[str] = None
    upper: bool = False
    #: The smallest number of days it takes (days until due go below 0 once an invoice is overdue).
    lowest: int = -MAX_DAYS


FIELDS: dict[str, Fact] = {f.key: f for f in (
    Fact("amount", "Amount", "the amount", MONEY, COMPARE),
    Fact("currency", "Currency", "the currency", TEXT, ("=", "!=", "in"), upper=True),
    Fact("counterparty", "Supplier name", "the supplier", TEXT, ("=", "!=", "contains", "in")),
    Fact("days_until_due", "Days until due", "the days until it is due", NUMBER, COMPARE, unit="days"),
    Fact("days_since_invoice", "Days since the invoice date", "the invoice's age", NUMBER, COMPARE, unit="days",
         lowest=0),
    Fact("has_matched_po", "Has a matching purchase order", "a matching purchase order", BOOL, ("=", "!="),
          yes_no=("it has a matching purchase order", "it has no matching purchase order")),
    # The India pack's reading (msme.MsmeStatus.protected): micro or small, or registered with its
    # category not confirmed yet (treated as micro or small, to be safe); a medium enterprise is not.
    Fact("msme_supplier", "MSME supplier (micro or small)", "a micro or small supplier", BOOL, ("=", "!="),
          yes_no=("the supplier is a micro or small enterprise", "the supplier is not a micro or small enterprise")),
    Fact("new_supplier", "New supplier (first invoice from them)", "a new supplier", BOOL, ("=", "!="),
          yes_no=("the supplier is new", "the supplier is not new")),
    Fact("confidence", "How sure the facts are", "its facts", CHOICE, ("=", "!=", "in"),
          choices=((v.CONFIDENCE_VERIFIED, "checked by a person"), (v.CONFIDENCE_CALIBRATED, "checked automatically"),
                   (v.CONFIDENCE_EXTRACTED, "read by the model and not checked"))),
    Fact("bank_account_changed", "Bank account changed", "a changed bank account", BOOL, ("=", "!="),
          yes_no=("its bank account changed", "its bank account did not change")),
    Fact("held", "Already held", "already held", BOOL, ("=", "!="),
          yes_no=("it is already held", "it is not held")),
)}


@dataclass(frozen=True)
class Rule:
    field: str
    op: str
    value: Any


# ---------------------------------------------------------------------------
# The parameters, checked in plain words
# ---------------------------------------------------------------------------


def _clean_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = re.sub(r"\s+", " ", value).strip()
    return text if 0 < len(text) <= MAX_TEXT else None


def _clean_value(found: Fact, op: str, value: Any) -> tuple[Any, Optional[str]]:
    """(the stored value, or a problem in words)."""
    if found.type == MONEY:
        if isinstance(value, dict) and set(value) == {"amount_micros", "currency"}:
            amount, currency = value["amount_micros"], value["currency"]
            if isinstance(amount, int) and not isinstance(amount, bool) and 0 <= amount <= 10**18 and \
                    isinstance(currency, str) and re.fullmatch(r"[A-Za-z]{3}", currency.strip()):
                return {"amount_micros": amount, "currency": currency.strip().upper()}, None
        return None, "write the amount and its currency, for example 500000 INR."
    if found.type == NUMBER:
        if isinstance(value, int) and not isinstance(value, bool) and found.lowest <= value <= MAX_DAYS:
            return value, None
        return None, f"write the number of days as a whole number between {found.lowest} and {MAX_DAYS}."
    if found.type == BOOL:
        if isinstance(value, bool):
            return value, None
        return None, "choose yes or no."
    allowed = {c for c, _ in found.choices}
    if op == "in":
        items = value if isinstance(value, list) else None
        cleaned = [_clean_text(item) for item in items or []]
        if not items or len(items) > MAX_LIST or any(item is None for item in cleaned):
            return None, f"list one to {MAX_LIST} values, separated by commas."
        cleaned = [c.upper() if found.upper or allowed else c for c in cleaned]
        if allowed and any(c not in allowed for c in cleaned):
            return None, "choose from " + _or([w for _, w in found.choices]) + "."
        if found.upper and any(not re.fullmatch(r"[A-Z]{3}", c) for c in cleaned):
            return None, "write each currency as three letters, for example INR."
        return list(dict.fromkeys(cleaned)), None
    text = _clean_text(value)
    if text is None:
        return None, ("choose " + _or([w for _, w in found.choices]) + "." if allowed else
                      f"write {found.noun}'s value (up to {MAX_TEXT} characters).")
    if allowed:
        text = text.upper()
        if text not in allowed:
            return None, "choose " + _or([w for _, w in found.choices]) + "."
    if found.upper:
        text = text.upper()
        if not re.fullmatch(r"[A-Z]{3}", text):
            return None, "write the currency as three letters, for example INR."
    return text, None


def _low(label: str) -> str:
    """A label inside a sentence: "Days until due" -> "days until due", but "MSME supplier" stays."""
    return label if label[:2].isupper() else label[:1].lower() + label[1:]


def _or(words: list[str]) -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + " or " + words[-1]


def _and(words: list[str], joiner: str = "and") -> str:
    return words[0] if len(words) == 1 else ", ".join(words[:-1]) + f" {joiner} " + words[-1]


NAME_WORDS = "Give the condition a short name, for example \"Large invoices from new suppliers\"."
LIST_WORDS = f"List the conditions, one to {MAX_CONDITIONS} of them."
MATCH_WORDS = "Choose whether all the conditions must be true, or any one of them."
OUTCOME_WORDS = "Choose what Helm does: hold the invoice, ask a person before paying it, or tell you."


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: A short name the owner gives the rule; it is the mandate's title and the hold's reason.
    name: str
    match: str = "all"
    #: One to eight {"field", "op", "value"}; checked below, in words.
    conditions: list[Any]
    outcome: str = HOLD

    @model_validator(mode="before")
    @classmethod
    def _kinds(cls, raw: Any) -> Any:
        """A setting of the wrong kind (a name that is not text, conditions that are not a list), in
        words before Pydantic would say it in its own."""
        if not isinstance(raw, dict):
            return raw
        kinds = (("name", str, NAME_WORDS), ("conditions", list, LIST_WORDS), ("match", str, MATCH_WORDS),
                 ("outcome", str, OUTCOME_WORDS))
        problems = [words for key, kind, words in kinds if key in raw and not isinstance(raw[key], kind)]
        if problems:
            raise base.Problems(problems)
        return raw

    @model_validator(mode="after")
    def _plain(self) -> "Params":
        problems: list[str] = []
        self.name = re.sub(r"\s+", " ", self.name).strip()
        if not self.name:
            problems.append(NAME_WORDS)
        elif len(self.name) > MAX_NAME:
            problems.append(f"Keep the name under {MAX_NAME} characters.")
        if self.match not in MATCHES:
            problems.append(MATCH_WORDS)
        if self.outcome not in OUTCOMES:
            problems.append(OUTCOME_WORDS)
        if not self.conditions:
            problems.append("Add at least one condition.")
        elif len(self.conditions) > MAX_CONDITIONS:
            problems.append(f"A custom condition has at most {MAX_CONDITIONS} conditions.")
        cleaned: list[dict[str, Any]] = []
        for index, raw in enumerate(self.conditions[:MAX_CONDITIONS], start=1):
            where = f"Condition {index}"
            if not isinstance(raw, dict) or not {"field", "op"} <= set(raw) or set(raw) - {"field", "op", "value"}:
                problems.append(f"{where}: give a fact, a comparison and a value.")
                continue
            found = FIELDS.get(raw["field"]) if isinstance(raw["field"], str) else None
            if found is None:
                problems.append(f"{where}: Helm cannot test \"{raw['field']}\". Choose one of: "
                                + ", ".join(_low(f.label) for f in FIELDS.values()) + ".")
                continue
            op = raw["op"]
            if op not in found.ops:
                problems.append(f"{where}: {found.noun} cannot be compared with \"{op}\"; use "
                                + _or([f"\"{OP_WORDS[o]}\"" for o in found.ops]) + ".")
                continue
            value, problem = _clean_value(found, op, raw.get("value"))
            if problem:
                problems.append(f"{where}, {_low(found.label)}: {problem}")
                continue
            cleaned.append({"field": found.key, "op": op, "value": value})
        if problems:
            raise base.Problems(problems)
        self.conditions = cleaned
        return self

    def rules(self) -> tuple[Rule, ...]:
        return tuple(Rule(c["field"], c["op"], c["value"]) for c in self.conditions)


def _contradictions(params: Params) -> list[str]:
    """With `all`, conditions on one fact that no invoice can meet together (above 10 and below 5)."""
    if params.match != "all":
        return []
    problems: list[str] = []
    bounds: dict[tuple[str, Optional[str]], list[Rule]] = {}
    flags: dict[str, set[bool]] = {}
    for rule in params.rules():
        found = FIELDS[rule.field]
        if found.type == MONEY:
            bounds.setdefault((rule.field, rule.value["currency"]), []).append(rule)
        elif found.type == NUMBER:
            bounds.setdefault((rule.field, None), []).append(rule)
        elif found.type == BOOL:
            flags.setdefault(rule.field, set()).add(rule.value if rule.op == "=" else not rule.value)
    for (key, currency), rules in bounds.items():
        if len(rules) < 2:
            continue
        low, high = -float("inf"), float("inf")
        low_open = high_open = False
        equals: set[int] = set()
        for rule in rules:
            number = rule.value["amount_micros"] if currency else rule.value
            if rule.op in (">", ">="):
                if number > low or (number == low and rule.op == ">"):
                    low, low_open = number, rule.op == ">"
            elif rule.op in ("<", "<="):
                if number < high or (number == high and rule.op == "<"):
                    high, high_open = number, rule.op == "<"
            elif rule.op == "=":
                equals.add(number)
        empty = low > high or (low == high and (low_open or high_open)) or len(equals) > 1 or any(
            e < low or e > high or (e == low and low_open) or (e == high and high_open) for e in equals)
        if empty:
            problems.append(f"The conditions on {FIELDS[key].noun} contradict each other, so the rule would match "
                            "no invoice.")
    for key, wanted in flags.items():
        if len(wanted) > 1:
            problems.append(f"The conditions ask for {FIELDS[key].noun} and its opposite at once, so the rule would "
                            "match no invoice.")
    return problems


def _validate(spec: MandateSpec, params: Params) -> list[str]:
    problems = _contradictions(params)
    if params.outcome == HOLD and any(r.field == "held" for r in params.rules()):
        problems.append("Whether an invoice is already held cannot decide whether to hold it: remove that condition, "
                        "or choose to ask a person or to be told instead.")
    return problems


# ---------------------------------------------------------------------------
# Words
# ---------------------------------------------------------------------------


def _days(n: int) -> str:
    return f"{n} day" if abs(n) == 1 else f"{n} days"


def phrase(rule: Rule) -> str:
    """One condition in words: "the amount is above ₹5 lakh", "the supplier is new"."""
    found = FIELDS[rule.field]
    if found.type == BOOL:
        positive = rule.value if rule.op == "=" else not rule.value
        return found.yes_no[0] if positive else found.yes_no[1]
    if found.type == MONEY:
        return f"{found.noun} {OP_WORDS[rule.op]} {money.say(rule.value['amount_micros'], rule.value['currency'])}"
    if rule.field == "days_until_due":
        if rule.value == 0:
            return DUE_TODAY[rule.op]
        if rule.value < 0:
            return OVERDUE[rule.op].format(_days(-rule.value))
        if rule.op == "!=":
            return f"it is not due in exactly {_days(rule.value)}"
        return f"it is due in {DAY_WORDS[rule.op]} {_days(rule.value)}"
    if rule.field == "days_since_invoice":
        if rule.op == "!=":
            return f"the invoice is not exactly {_days(rule.value)} old"
        return f"the invoice is {DAY_WORDS[rule.op]} {_days(rule.value)} old"
    words = dict(found.choices)
    if found.type == CHOICE:
        said = [words.get(item, item) for item in (rule.value if rule.op == "in" else [rule.value])]
        return f"{found.noun} are {'not ' if rule.op == '!=' else ''}{_or(said)}"
    if rule.op == "in":
        return f"{found.noun} is one of {_and(list(rule.value), 'or')}"
    if rule.op == "contains":
        return f"{found.noun}'s name contains \"{rule.value}\""
    return f"{found.noun} {OP_WORDS[rule.op]} {rule.value}"


LEADS = {HOLD: "Hold", APPROVAL: "Ask a person before paying", NOTIFY: "Tell owners and admins about"}
TAILS = {HOLD: "; release it once that is no longer true.", APPROVAL: ".", NOTIFY: "."}


def _readback(spec: MandateSpec, params: Params) -> base.Readback:
    pieces: list[Any] = [base.Chip("outcome", params.outcome, LEADS[params.outcome], "choice"), " any invoice where "]
    rules = params.rules()
    joiner = " and " if params.match == "all" else " or "
    for index, rule in enumerate(rules):
        if index:
            pieces.append(joiner if index == len(rules) - 1 else ", ")
        pieces.append(base.Chip("conditions", {"field": rule.field, "op": rule.op, "value": rule.value},
                                phrase(rule), "condition"))
    pieces.append(TAILS[params.outcome])
    return base.readback(*pieces)


# ---------------------------------------------------------------------------
# Reading an invoice's facts
# ---------------------------------------------------------------------------


def code_of(mandate: base.ActiveMandate) -> str:
    return f"{CODE}:{mandate.id}"


class Facts:
    """The facts the conditions read, for the invoices of one snapshot, as of one day."""

    def __init__(self, snapshot: Snapshot, mandate: base.ActiveMandate):
        self.snapshot = snapshot
        self.mandate = mandate
        self.params: Params = mandate.params
        self.code = code_of(mandate)
        self._first: Optional[dict[str, date]] = None

    def _first_seen(self, key: str) -> Optional[date]:
        """The day this supplier's first invoice arrived (a credit note is not an invoice)."""
        if self._first is None:
            first: dict[str, date] = {}
            for p in self.snapshot.payables:
                if p.amount_micros <= 0:
                    continue
                seen, key_of = base.seen_on(p), p.counterparty_key
                if key_of and seen and (key_of not in first or seen < first[key_of]):
                    first[key_of] = seen
            self._first = first
        return self._first.get(key)

    def value(self, payable: Payable, field: str, on: date) -> Any:
        if field == "amount":
            return payable.amount_micros, payable.currency
        if field == "currency":
            return payable.currency
        if field == "counterparty":
            return payable.counterparty_name
        if field == "days_until_due":
            return (payable.due_date - on).days if payable.due_date else None
        if field == "days_since_invoice":
            return (on - payable.invoice_date).days if payable.invoice_date else None
        if field == "has_matched_po":
            return payable.has_matched_po
        if field == "msme_supplier":
            counterparty = self.snapshot.counterparties.get(payable.counterparty_key or "")
            return counterparty.msme.protected if counterparty else bool(payable.udyam_number)
        if field == "new_supplier":
            if not payable.counterparty_key:
                return True  # nobody Helm knows: treated as new, the careful reading
            first, seen = self._first_seen(payable.counterparty_key), base.seen_on(payable)
            return first is None or seen is None or seen <= first
        if field == "confidence":
            return payable.confidence
        if field == "bank_account_changed":
            return payable.flag("BANK_ACCOUNT_CHANGED") is not None
        if field == "held":  # held for another reason (this mandate's own hold does not count)
            return payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED) and \
                payable.hold_code != self.code
        raise KeyError(field)

    def matched(self, payable: Payable, *, on: Optional[date] = None) -> Optional[list[Rule]]:
        """The rules that hold for this invoice when the condition as a whole holds; None when it does not."""
        on = on or self.snapshot.as_of
        hits = [rule for rule in self.params.rules() if satisfied(rule, self.value(payable, rule.field, on))]
        whole = len(hits) == len(self.params.conditions) if self.params.match == "all" else bool(hits)
        return hits if whole else None


def _norm(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _compare(left: int, op: str, right: int) -> bool:
    return {">": left > right, ">=": left >= right, "<": left < right, "<=": left <= right, "=": left == right,
            "!=": left != right}[op]


def satisfied(rule: Rule, actual: Any) -> bool:
    """Does one condition hold for a fact? An amount in another currency, or a number of days Helm does
    not have (no due date), never satisfies a comparison; a missing name is an empty one, so "the
    supplier is not Acme" holds for an invoice with no supplier name and "contains" never does."""
    found = FIELDS[rule.field]
    if found.type == MONEY:
        amount, currency = actual
        return currency is not None and currency.upper() == rule.value["currency"] and \
            _compare(int(amount), rule.op, int(rule.value["amount_micros"]))
    if found.type == NUMBER:
        return actual is not None and _compare(int(actual), rule.op, int(rule.value))
    if found.type == BOOL:
        return (bool(actual) == rule.value) if rule.op == "=" else (bool(actual) != rule.value)
    have = _norm(actual)
    if rule.op == "in":
        return have in {_norm(item) for item in rule.value}
    if rule.op == "contains":
        return _norm(rule.value) in have
    return (have == _norm(rule.value)) if rule.op == "=" else (have != _norm(rule.value))


def _because(params: Params, hits: list[Rule]) -> str:
    return f"\"{params.name}\": {_and([phrase(r) for r in hits])}"


# ---------------------------------------------------------------------------
# Drift, proposals, the Guard, the optimiser, the backtest
# ---------------------------------------------------------------------------


def _matching(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[tuple[Payable, list[Rule]]]:
    facts = Facts(snapshot, mandate)
    out = []
    for payable in base.payables_in_scope(snapshot, mandate):
        if not payable.open:
            continue
        hits = facts.matched(payable)
        if hits is not None:
            out.append((payable, hits))
    return out


def _evaluate(snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> base.Drift:
    params: Params = mandate.params
    found = _matching(snapshot, mandate)
    items = [{"promise_id": p.promise_id, "work_item_id": p.work_item_id, "document": p.document,
              "counterparty": p.counterparty_name, "currency": p.currency, "amount_micros": p.amount_micros,
              "settlement": p.settlement_state, "because": [phrase(r) for r in hits]} for p, hits in found]
    if params.outcome == HOLD:
        exposed = [p for p, _ in found if p.settlement_state not in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED)]
        paying = [p for p in exposed if p.settlement_state in (v.SETTLEMENT_SCHEDULED, v.SETTLEMENT_APPROVED)]
        if paying:
            return base.Drift(base.BREACHED, f"{len(paying)} on a payment run",
                              f"An invoice that matches \"{params.name}\" is scheduled to be paid.", len(paying),
                              items)
        if exposed:
            return base.Drift(base.DRIFTING, f"{len(exposed)} not held yet",
                              f"{len(exposed)} invoice(s) match \"{params.name}\" and are not held yet.", len(exposed),
                              items)
        return base.Drift(base.HOLDING, f"{len(found)} held" if found else "Nothing matches",
                          f"No invoice that matches \"{params.name}\" can be paid.", len(found), items)
    if params.outcome == APPROVAL:
        return base.Drift(base.HOLDING, f"{len(found)} ask a person" if found else "Nothing matches",
                          f"A person approves every payment of an invoice that matches \"{params.name}\".", len(found),
                          items)
    return base.Drift(base.HOLDING, f"{len(found)} matching" if found else "Nothing matches",
                      f"Owners and admins hear about every invoice that matches \"{params.name}\".", len(found), items)


def _propose(snapshot: Snapshot, mandate: base.ActiveMandate) -> list[base.Candidate]:
    params: Params = mandate.params
    if params.outcome == APPROVAL:
        return []  # the Guard asks a person when a payment comes up; nothing to do before
    out: list[base.Candidate] = []
    for payable, hits in _matching(snapshot, mandate):
        name = payable.counterparty_name or "the supplier"
        what = f"{payable.document} ({money.say(payable.amount_micros, payable.currency)} from {name})"
        if params.outcome == HOLD:
            if payable.settlement_state in (v.SETTLEMENT_HELD, v.SETTLEMENT_DISPUTED):
                continue
            out.append(base.Candidate(
                kind=v.ACTION_HOLD_PAYMENT, mandate=mandate,
                title=f"Hold {payable.document_number or payable.document}: {params.name}",
                explanation=(f"{what} matches {_because(params, hits)}. Helm holds it until that is no longer "
                             "true, or a person releases it."),
                key_parts=(payable.promise_id, "custom", payable.settlement_version),
                workspace_id=payable.workspace_id, work_item_id=payable.work_item_id, promise_id=payable.promise_id,
                counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
                currency=payable.currency, params={"reason": code_of(mandate), "condition": params.name}))
        else:
            out.append(base.Candidate(
                kind=v.ACTION_NOTIFY, mandate=mandate,
                title=f"{params.name}: {payable.document_number or payable.document} from {name}",
                explanation=f"{what} matches {_because(params, hits)}.",
                key_parts=(payable.promise_id, "custom-notify"), workspace_id=payable.workspace_id,
                work_item_id=payable.work_item_id, promise_id=payable.promise_id,
                counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros,
                currency=payable.currency, params={"condition": params.name}))
    return out


def _guard(action: Any, snapshot: Snapshot, mandate: base.ActiveMandate, context: dict) -> list[base.Check]:
    params: Params = mandate.params
    kinds = {HOLD: (*PAYING_ACTIONS, v.ACTION_RELEASE_HOLD), APPROVAL: PAYING_ACTIONS}.get(params.outcome, ())
    if action.kind not in kinds:
        return []
    payable = snapshot.payable(action.promise_id) if action.promise_id else None
    if payable is None and action.work_item_id:
        payable = snapshot.payable_for_work_item(str(action.work_item_id))
    if payable is None or not base.in_scope(mandate, workspace_id=payable.workspace_id,
                                            counterparty_key=payable.counterparty_key, currency=payable.currency):
        return []
    hits = Facts(snapshot, mandate).matched(payable)
    evidence = {"condition": params.name, "matched": [phrase(r) for r in hits or []]}
    if params.outcome == HOLD:
        return [base.check(f"limit:{KEY}:{mandate.id}", f"Not caught by \"{params.name}\"", hits is None,
                           detail=f"It does not match \"{params.name}\"." if hits is None else
                           f"It matches {_because(params, hits)}, so Helm holds it.", evidence=evidence)]
    return [base.check(f"limit:{KEY}:{mandate.id}", f"\"{params.name}\" asks a person", hits is None,
                       on_fail=v.CHECK_APPROVAL,
                       detail=f"It does not match \"{params.name}\"." if hits is None else
                       f"It matches {_because(params, hits)}, so a person approves the payment.", evidence=evidence)]


def _optimizer(snapshot: Snapshot, mandate: base.ActiveMandate) -> base.OptimizerTerms:
    terms = base.OptimizerTerms()
    if mandate.params.outcome == HOLD:
        for payable, _ in _matching(snapshot, mandate):
            terms.exclude[payable.promise_id] = f"matches \"{mandate.params.name}\""
    return terms


def _still_holds(snapshot: Snapshot, payable: Payable, mandate: base.ActiveMandate) -> bool:
    return mandate.params.outcome == HOLD and Facts(snapshot, mandate).matched(payable) is not None


WOULD = {HOLD: "Helm would have held it", APPROVAL: "Helm would have asked a person before paying it",
         NOTIFY: "Helm would have told you"}


def _backtest(history, mandate: base.ActiveMandate) -> list[base.Finding]:
    """Each invoice that matched on the day it arrived (its days counted from that day)."""
    params: Params = mandate.params
    facts = Facts(history.snapshot, mandate)
    out: list[base.Finding] = []
    for payable in base.payables_in_scope(history.snapshot, mandate):
        on = base.seen_on(payable)
        if payable.amount_micros <= 0 or not history.within(on):
            continue
        hits = facts.matched(payable, on=on)
        if hits is None:
            continue
        trusted = payable.confidence in v.TRUSTED_CONFIDENCES
        paid = " It was paid." if payable.settlement_state == v.SETTLEMENT_PAID else ""
        out.append(base.Finding(
            mandate=KEY, on=on, action=v.ACTION_HOLD_PAYMENT if params.outcome == HOLD else
            (v.ACTION_APPROVE_PAYMENT if params.outcome == APPROVAL else v.ACTION_NOTIFY),
            honesty=v.HONESTY_CONFIRMED if trusted else v.HONESTY_POTENTIAL, category=base.CATEGORY_CUSTOM,
            currency=payable.currency, value_micros=0,
            summary=(f"{payable.document} ({money.say(payable.amount_micros, payable.currency)}) matched "
                     f"{_because(params, hits)}.{paid} {WOULD[params.outcome]}."
                     + ("" if trusted else " Its facts were read by the model and not checked.")),
            evidence=base.evidence_of(payable)))
    return out


def _editor() -> dict[str, Any]:
    """What the editor's rows offer: each fact with its comparisons in words, and the outcomes."""
    def ops(found: Fact) -> list[dict[str, str]]:
        return [{"op": op, "words": OP_WORDS[op]} for op in found.ops]

    return {
        "fields": [{"key": f.key, "label": f.label, "type": f.type, "ops": ops(f), "unit": f.unit,
                    "choices": [{"value": c, "label": w} for c, w in f.choices]} for f in FIELDS.values()],
        "matches": [{"value": "all", "label": "All of these are true"},
                    {"value": "any", "label": "Any one of these is true"}],
        "outcomes": [
            {"value": HOLD, "label": "Hold the invoice",
             "help": "Helm holds a matching invoice and releases it once the condition is no longer true."},
            {"value": APPROVAL, "label": "Ask a person before paying",
             "help": "A matching invoice is never paid without a person's approval."},
            {"value": NOTIFY, "label": "Tell owners and admins",
             "help": "Helm sends a notice about each matching invoice, once."},
        ],
        "max_conditions": MAX_CONDITIONS,
    }


_title: Callable[[Params], str] = lambda params: params.name

TEMPLATE = base.Template(
    key=KEY, kind=v.MANDATE_KIND_LIMIT, title="Custom condition", group=base.GROUP_CUSTOM, order=99,
    summary="Your own rule over an invoice's facts: hold it, ask a person before paying it, or be told.",
    Params=Params, readback=_readback, evaluate=_evaluate, propose=_propose, guard_checks=_guard,
    optimizer=_optimizer, backtest=_backtest, validate=_validate, plan="enterprise", is_limit=True, hold_code=CODE,
    hold_code_for=code_of, still_holds=_still_holds, title_from=_title, editor=_editor(), repeatable=True,
    keywords=("custom", "condition", "my own rule", "any invoice where", "whenever an invoice"),
)
