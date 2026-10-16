"""What every mandate template is made of, and the shared types they return.

A template module (one per library mandate, `mandates/library/`) defines a `TEMPLATE` object with:

* `Params`          a Pydantic model of its parameters (typed, bounded, defaulted);
* `readback(spec)`  the plain sentence a person sees, with editable chips for each parameter;
* `validate(...)`   impossible or contradictory parameters, in words (beyond the Pydantic bounds);
* `evaluate(...)`   the drift: holding, drifting or breached, a key figure, the items behind it;
* `propose(...)`    candidate actions;
* `guard_checks(...)` the checks this mandate adds to the Guard for an action;
* `optimizer(...)`  what it contributes to payment timing (exclusions, deadlines, discounts, floors);
* `backtest(...)`   what it would have done over a past period, with evidence and value classes;
* `forecast(...)`   upcoming risks it sees, with their probability where the simulation gives one.

Templates are deterministic and pure: they read a `Snapshot` and never the database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Optional, Sequence

from pydantic import BaseModel

from app.services.helm import vocabulary as v
from app.services.helm.mandates.spec import MandateSpec
from app.services.helm.snapshot import Payable, Snapshot

HOLDING = "holding"
DRIFTING = "drifting"
BREACHED = "breached"
UNKNOWN = "unknown"
DRIFT_STATES = (HOLDING, DRIFTING, BREACHED, UNKNOWN)

GROUP_PROTECT = "Protect payables"
GROUP_RUN = "Run payables well"
GROUP_INDIA = "India pack"
GROUP_CONTRACTS = "Contracts and compliance"
GROUP_RECEIVABLES = "Receivables"
GROUP_OPERATIONS = "Operations"
GROUP_CUSTOM = "Custom"
GROUPS = (GROUP_PROTECT, GROUP_RUN, GROUP_INDIA, GROUP_CONTRACTS, GROUP_RECEIVABLES, GROUP_OPERATIONS, GROUP_CUSTOM)


@dataclass(frozen=True)
class ActiveMandate:
    id: str
    version: int
    status: str
    title: str
    spec: MandateSpec
    params: Any  # the template's Params instance

    @property
    def template(self) -> str:
        return self.spec.template

    @property
    def mode(self) -> str:
        return self.spec.autonomy.mode

    @property
    def ref(self) -> dict[str, Any]:
        return {"mandate_id": self.id, "version": self.version, "template": self.template}


@dataclass
class Chip:
    param: str
    value: Any
    display: str
    kind: str  # money | percent | days | count | bool | list | text | weekdays | choice | condition


@dataclass
class Readback:
    sentence: str
    parts: list[dict[str, Any]]

    def as_json(self) -> dict[str, Any]:
        return {"sentence": self.sentence, "parts": self.parts}


def readback(*pieces: Any) -> Readback:
    """Build a readback from text pieces and Chips; the sentence is the pieces joined."""
    parts: list[dict[str, Any]] = []
    words: list[str] = []
    for piece in pieces:
        if isinstance(piece, Chip):
            parts.append({"chip": piece.param, "value": piece.value, "display": piece.display, "kind": piece.kind})
            words.append(piece.display)
        else:
            parts.append({"text": str(piece)})
            words.append(str(piece))
    return Readback(sentence="".join(words), parts=parts)


@dataclass
class Drift:
    state: str
    figure: str
    detail: str
    figure_value: Optional[float] = None
    items: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"state": self.state, "figure": self.figure, "detail": self.detail,
                "figure_value": self.figure_value, "items": self.items[:50]}


@dataclass
class Candidate:
    kind: str
    title: str
    explanation: str
    key_parts: tuple[Any, ...]
    mandate: Optional[ActiveMandate] = None
    workspace_id: Optional[str] = None
    work_item_id: Optional[str] = None
    promise_id: Optional[str] = None
    counterparty_key: Optional[str] = None
    amount_micros: Optional[int] = None
    currency: Optional[str] = None
    run_date: Optional[date] = None
    value_micros: Optional[int] = None
    params: dict[str, Any] = field(default_factory=dict)

    @property
    def idempotency_key(self) -> str:
        from app.services.helm.ledger import sha256

        mandate = f"{self.mandate.id}:{self.mandate.version}" if self.mandate else "-"
        return f"helm:{self.kind}:{sha256('|'.join(str(p) for p in (mandate, *self.key_parts)))[:40]}"


@dataclass
class Check:
    id: str
    title: str
    result: str  # pass | fail | needs_approval | not_applicable
    detail: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"id": self.id, "title": self.title, "result": self.result, "detail": self.detail,
                "evidence": self.evidence}


def check(id: str, title: str, ok: bool, *, detail: str, on_fail: str = v.CHECK_FAIL,
          evidence: Optional[dict[str, Any]] = None) -> Check:
    return Check(id=id, title=title, result=v.CHECK_PASS if ok else on_fail, detail=detail, evidence=evidence or {})


@dataclass
class OptimizerTerms:
    """One mandate's contribution to payment timing."""

    #: payable promise id -> why it may not be scheduled now (a limit is breached)
    exclude: dict[str, str] = field(default_factory=dict)
    #: payable promise id -> (deadline, kind "MSME" | "ON_TIME", explanation)
    deadlines: dict[str, tuple[date, str, str]] = field(default_factory=dict)
    #: payable promise id -> discount value in micros when paid by its discount deadline
    discounts: dict[str, int] = field(default_factory=dict)
    #: payable promise id -> (per day, once) late penalty in micros
    late_penalties: dict[str, tuple[int, int]] = field(default_factory=dict)
    #: currency -> cash floor in micros
    floors: dict[str, int] = field(default_factory=dict)


@dataclass
class Finding:
    """A backtest finding: what Helm would have done on a past day, and what it was worth."""

    mandate: str
    on: date
    action: str
    summary: str
    honesty: str  # confirmed | likely | potential
    category: str
    currency: Optional[str]
    value_micros: int
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"mandate": self.mandate, "on": self.on.isoformat(), "action": self.action, "summary": self.summary,
                "honesty": self.honesty, "category": self.category, "currency": self.currency,
                "value_micros": self.value_micros, "evidence": self.evidence}


@dataclass
class Risk:
    mandate: str
    on: Optional[date]
    summary: str
    probability: Optional[float] = None
    currency: Optional[str] = None
    amount_micros: Optional[int] = None

    def as_json(self) -> dict[str, Any]:
        return {"mandate": self.mandate, "on": self.on.isoformat() if self.on else None, "summary": self.summary,
                "probability": self.probability, "currency": self.currency, "amount_micros": self.amount_micros}


def in_scope(mandate: ActiveMandate, *, workspace_id: Optional[str], counterparty_key: Optional[str],
             currency: Optional[str]) -> bool:
    scope = mandate.spec.scope
    if scope.workspace_ids and workspace_id and uuid.UUID(str(workspace_id)) not in set(scope.workspace_ids):
        return False
    if scope.counterparty_keys and counterparty_key not in set(scope.counterparty_keys):
        return False
    if scope.currency and currency and scope.currency != currency:
        return False
    return True


def payables_in_scope(snapshot: Snapshot, mandate: ActiveMandate) -> list[Payable]:
    return [p for p in snapshot.payables
            if in_scope(mandate, workspace_id=p.workspace_id, counterparty_key=p.counterparty_key,
                        currency=p.currency)]


@dataclass(frozen=True)
class Template:
    key: str
    kind: str
    title: str
    group: str
    order: int
    summary: str
    Params: type[BaseModel]
    readback: Callable[[MandateSpec, Any], Readback]
    #: evaluate(snapshot, mandate, context): `context` carries the plan's simulation and schedule
    #: ({"sim": SimulationResult | None, "schedule": {promise_id: date | None}}) when there is one.
    evaluate: Callable[[Snapshot, ActiveMandate, dict], Drift]
    propose: Callable[[Snapshot, ActiveMandate], list[Candidate]] = lambda s, m: []
    #: guard_checks(action, snapshot, mandate, context): `context` as for evaluate, plus the
    #: payments already in the plan ahead of this one ({"scheduled_before": {currency: micros}}).
    guard_checks: Callable[[Any, Snapshot, ActiveMandate, dict], list[Check]] = lambda a, s, m, c: []
    optimizer: Callable[[Snapshot, ActiveMandate], OptimizerTerms] = lambda s, m: OptimizerTerms()
    backtest: Callable[[Any, ActiveMandate], list[Finding]] = lambda history, m: []
    forecast: Callable[[Snapshot, ActiveMandate, Any], list[Risk]] = lambda s, m, sim: []
    validate: Callable[[MandateSpec, Any], list[str]] = lambda spec, params: []
    #: The plan that carries it: "business" (capability.helm) or "enterprise" (capability.helm_custom).
    plan: str = "business"
    #: Words the sentence matcher and the drafting prompt use to recognise this template.
    keywords: tuple[str, ...] = ()
    #: Which business types (first run, §10.3) it is recommended for; empty = everyone.
    recommended_for: tuple[str, ...] = ()
    law_links: tuple[dict[str, str], ...] = ()
    disclaimer: Optional[str] = None
    #: Is it a limit the Guard enforces (an action that breaks it never passes)?
    is_limit: bool = False
    #: The hold reason this mandate places ("PROBABLE_DUPLICATE", ...), and whether it still applies
    #: to a payable: when it no longer does, the planner proposes releasing the hold (`release_hold`).
    hold_code: Optional[str] = None
    still_holds: Optional[Callable[[Snapshot, Payable, ActiveMandate], bool]] = None
    #: A template many mandates share with different rules (the custom condition) holds each invoice
    #: under its own mandate's code, so one mandate never releases another's hold.
    hold_code_for: Optional[Callable[[ActiveMandate], str]] = None
    #: The mandate's title from its parameters (a custom condition is called what its owner named it).
    title_from: Optional[Callable[[Any], str]] = None
    #: What the editor needs beyond the readback's chips (the custom condition's fields and operators).
    editor: Optional[dict[str, Any]] = None
    #: Several mandates may use it, each with its own rule (the custom condition); the gallery keeps
    #: offering it after the first. An identical one is still refused as a duplicate.
    repeatable: bool = False


def hold_code_of(template: Template, mandate: ActiveMandate) -> Optional[str]:
    """The code this mandate's holds carry (`Payable.hold_code`), which its release looks for."""
    return template.hold_code_for(mandate) if template.hold_code_for is not None else template.hold_code


class Problems(ValueError):
    """Raised by a template's parameter model: every problem is already a whole sentence in plain words,
    and the registry passes them on as they are (never as a Pydantic error string)."""

    def __init__(self, problems: Sequence[str]):
        super().__init__(" ".join(problems))
        self.problems = list(problems)


#: Backtest finding categories beyond the value-held ones (vocabulary.VALUE_CATEGORIES): facts a
#: mandate would have caught that carry no money claim of their own.
CATEGORY_PAYMENT_PROTECTED = "PAYMENT_PROTECTED"  # a payment into an unverified new account
CATEGORY_CONTROL = "CONTROL"  # an invoice paid without a required PO
CATEGORY_CASH_FLOOR = "CASH_FLOOR"
CATEGORY_MSME_LATE = "MSME_LATE"
CATEGORY_RENEWAL = "RENEWAL_MISSED"
CATEGORY_DOCUMENT = "DOCUMENT_EXPIRED"
CATEGORY_RECEIVABLE = "RECEIVABLE_LATE"
CATEGORY_CUSTOM = "CUSTOM_CONDITION"  # an invoice that matched an owner's custom condition


def evidence_of(payable: Any) -> list[dict[str, Any]]:
    """A finding's evidence: the document it stands on."""
    return [{"work_item_id": payable.work_item_id, "document": payable.document,
             "document_number": getattr(payable, "document_number", None)}]


def seen_on(payable: Any) -> Optional[date]:
    """The day Helm would have seen a document: the day it arrived, else its own date."""
    return getattr(payable, "received_on", None) or getattr(payable, "invoice_date", None) or payable.due_date


def release_candidate(payable: Payable, mandate: ActiveMandate, why: str) -> Candidate:
    """Release a hold this mandate placed, now that its reason is gone."""
    from app.services.helm import money

    return Candidate(
        kind=v.ACTION_RELEASE_HOLD, mandate=mandate,
        title=f"Release {payable.document_number or payable.document} for payment",
        explanation=f"Helm held {payable.document} ({money.say(payable.amount_micros, payable.currency)} from "
                    f"{payable.counterparty_name or 'the supplier'}). {why} It goes back into the payment plan.",
        key_parts=(payable.promise_id, "release", payable.hold_code, payable.settlement_version),
        workspace_id=payable.workspace_id, work_item_id=payable.work_item_id, promise_id=payable.promise_id,
        counterparty_key=payable.counterparty_key, amount_micros=payable.amount_micros, currency=payable.currency,
        params={"reason": payable.hold_code, "document_number": payable.document_number})


Templates = Sequence[Template]

__all__ = [
    "ActiveMandate", "BREACHED", "Candidate", "Check", "Chip", "DRIFTING", "DRIFT_STATES", "Drift", "Finding",
    "GROUPS", "GROUP_CONTRACTS", "GROUP_CUSTOM", "GROUP_INDIA", "GROUP_OPERATIONS", "GROUP_PROTECT",
    "GROUP_RECEIVABLES", "GROUP_RUN", "HOLDING", "OptimizerTerms", "Readback", "Risk", "Template", "UNKNOWN",
    "CATEGORY_CASH_FLOOR", "CATEGORY_CONTROL", "CATEGORY_DOCUMENT", "CATEGORY_MSME_LATE", "CATEGORY_PAYMENT_PROTECTED",
    "CATEGORY_CUSTOM", "CATEGORY_RECEIVABLE", "CATEGORY_RENEWAL", "Problems", "evidence_of", "hold_code_of",
    "release_candidate", "seen_on", "check", "in_scope", "payables_in_scope", "readback",
]
