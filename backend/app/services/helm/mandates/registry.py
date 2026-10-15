"""The library, and the one way a stored spec becomes a live mandate.

`TEMPLATES` is ordered by the brief's order of importance. `load(spec)` validates the spec's
parameters against the template's `Params` model; `validate(spec, ...)` adds the checks only the
book can answer (a scope that matches nothing, a duplicate of an active mandate) and returns every
problem in words a finance person reads, never a Pydantic error string.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from pydantic import ValidationError

from app.services.helm import ledger
from app.services.helm import vocabulary as v
from app.services.helm.mandates import base
from app.services.helm.mandates.spec import MandateSpec

_MODULES = (
    "agreed_price",
    "duplicates",
    "bank_account",
    "po_required",
    "on_time",
    "discounts",
    "cash_floor",
    "msme",
    "renewals",
    "compliance_docs",
    "receivables",
    "review_backlog",
    "supplier_statements",
    "custom_condition",
)


def _load_templates() -> dict[str, base.Template]:
    found: dict[str, base.Template] = {}
    for name in _MODULES:
        try:
            module = importlib.import_module(f"app.services.helm.mandates.library.{name}")
        except ModuleNotFoundError as exc:
            if exc.name == f"app.services.helm.mandates.library.{name}":
                continue  # not built yet (the build order adds them step by step)
            raise
        template = module.TEMPLATE
        found[template.key] = template
    return dict(sorted(found.items(), key=lambda kv: kv[1].order))


TEMPLATES: dict[str, base.Template] = _load_templates()


class MandateError(ValueError):
    def __init__(self, problems: Sequence[str]):
        super().__init__("; ".join(problems))
        self.problems = list(problems)


def template(key: str) -> base.Template:
    try:
        return TEMPLATES[key]
    except KeyError as exc:
        raise MandateError([f"There is no mandate template called {key!r}."]) from exc


_FIELD_WORDS = {
    "floor": "the cash floor", "amount_micros": "the amount", "tolerance_pct": "the tolerance",
    "grace_days": "the grace period", "min_probability_pct": "the confidence", "min_annual_rate_pct": "the yearly rate",
    "threshold": "the amount", "days_before": "the number of days", "days": "the reminder days",
    "max_age_days": "the backlog age", "currency": "the currency", "reminder_every_days": "the reminder interval",
}


def _words(error: ValidationError) -> list[str]:
    problems = []
    for item in error.errors():
        where = next((_FIELD_WORDS[str(p)] for p in reversed(item.get("loc", ())) if str(p) in _FIELD_WORDS),
                     " / ".join(str(p) for p in item.get("loc", ())) or "a value")
        kind = item.get("type", "")
        ctx = item.get("ctx") or {}
        if kind in ("greater_than_equal", "greater_than"):
            limit = ctx.get("ge", ctx.get("gt"))
            problems.append(f"{where.capitalize()} cannot be below {limit}." if str(limit) != "0" else
                            f"{where.capitalize()} cannot be below zero.")
        elif kind in ("less_than_equal", "less_than"):
            problems.append(f"{where.capitalize()} cannot be more than {ctx.get('le', ctx.get('lt'))}.")
        elif kind == "missing":
            problems.append(f"{where.capitalize()} is needed.")
        elif kind == "extra_forbidden":
            problems.append(f"{where!r} is not a setting of this mandate.")
        else:
            problems.append(f"{where.capitalize()}: {item.get('msg', 'not valid')}.")
    return problems


def parse_spec(raw: dict[str, Any]) -> MandateSpec:
    try:
        return MandateSpec.model_validate(raw)
    except ValidationError as exc:
        raise MandateError(_words(exc)) from exc


def params_of(spec: MandateSpec) -> Any:
    found = template(spec.template)
    try:
        return found.Params.model_validate(spec.params or {})
    except ValidationError as exc:
        raise MandateError(_words(exc)) from exc


def load(*, mandate_id: Any, version: int, status: str, title: str, spec: MandateSpec | dict) -> base.ActiveMandate:
    spec = spec if isinstance(spec, MandateSpec) else parse_spec(spec)
    return base.ActiveMandate(id=str(mandate_id), version=int(version), status=status, title=title, spec=spec,
                              params=params_of(spec))


def normalized(spec: MandateSpec) -> MandateSpec:
    """The spec with its params filled in from the template's defaults and its kind set."""
    found = template(spec.template)
    params = params_of(spec)
    return spec.model_copy(update={"params": params.model_dump(mode="json"), "kind": found.kind})


def spec_hash(spec: MandateSpec) -> str:
    return ledger.sha256(spec.as_json())


@dataclass(frozen=True)
class BookFacts:
    """What the validator needs to know about the book (no database inside the validator)."""

    workspace_ids: frozenset[str]
    counterparty_keys: frozenset[str]
    currencies: frozenset[str]
    active: Sequence[base.ActiveMandate] = ()
    enterprise: bool = False


def validate(spec: MandateSpec, facts: BookFacts, *, ignore_mandate_id: Optional[str] = None) -> list[str]:
    """Every reason this spec cannot become a mandate, in plain words (empty: it can)."""
    problems: list[str] = []
    try:
        found = template(spec.template)
    except MandateError as exc:
        return exc.problems
    if found.plan == "enterprise" and not facts.enterprise:
        problems.append(f"\"{found.title}\" is part of the Enterprise plan.")
    try:
        params = params_of(spec)
    except MandateError as exc:
        return problems + exc.problems
    scope = spec.scope
    unknown_ws = [str(w) for w in scope.workspace_ids if str(w) not in facts.workspace_ids]
    if unknown_ws:
        problems.append("The scope names a workspace this book does not cover.")
    if scope.counterparty_keys and not set(scope.counterparty_keys) & facts.counterparty_keys:
        problems.append("The scope names suppliers or customers Helm has not seen in this book, so it would match "
                        "nothing.")
    if scope.currency and facts.currencies and scope.currency not in facts.currencies:
        problems.append(f"No document in this book is in {scope.currency}, so the mandate would match nothing.")
    autonomy = spec.autonomy
    if autonomy.act_cap is not None and autonomy.mode != v.AUTONOMY_ACT and autonomy.act_cap.amount_micros > 0:
        problems.append("An act cap only applies in Act mode; switch the mode to Act or set the cap to zero.")
    problems.extend(found.validate(spec, params))
    mine = spec_hash(normalized(spec).model_copy(update={"status": "active", "owner_user_id": None,
                                                         "autonomy": spec.autonomy}))
    for other in facts.active:
        if ignore_mandate_id and other.id == ignore_mandate_id:
            continue
        if other.template != spec.template:
            continue
        theirs = spec_hash(normalized(other.spec).model_copy(update={"status": "active", "owner_user_id": None,
                                                                     "autonomy": spec.autonomy}))
        if theirs == mine:
            problems.append(f"This is the same as the active mandate \"{other.title}\".")
            break
    return problems


def library(*, enterprise: bool = True) -> list[dict[str, Any]]:
    """The gallery: every template with its defaults and its readback."""
    out = []
    for found in TEMPLATES.values():
        defaults = _defaults(found)
        spec = MandateSpec(template=found.key, kind=found.kind, params=defaults)
        try:
            params = found.Params.model_validate(defaults)
            rb = found.readback(spec, params).as_json()
        except ValidationError:
            rb = {"sentence": found.summary, "parts": [{"text": found.summary}]}
        out.append({"key": found.key, "title": found.title, "group": found.group, "order": found.order,
                    "kind": found.kind, "summary": found.summary, "plan": found.plan,
                    "available": enterprise or found.plan != "enterprise", "defaults": defaults, "readback": rb,
                    "is_limit": found.is_limit, "law_links": list(found.law_links), "disclaimer": found.disclaimer,
                    "recommended_for": list(found.recommended_for), "required": required_params(found.key)})
    return out


def required_params(key: str) -> list[str]:
    """Parameters with no default: the gallery's value for them is an example a person must replace."""
    return [name for name, field in template(key).Params.model_fields.items() if field.is_required()]


DEFAULT_FLOOR_CURRENCY = "INR"


def _defaults(found: base.Template) -> dict[str, Any]:
    """Defaults a gallery card can show; templates whose parameters have no default get a sample."""
    samples: dict[str, dict[str, Any]] = {
        "keep_cash_above": {"floor": {"amount_micros": 1_000_000 * 1_000_000, "currency": DEFAULT_FLOOR_CURRENCY}},
        "po_required_above": {"threshold": {"amount_micros": 50_000 * 1_000_000, "currency": DEFAULT_FLOOR_CURRENCY}},
    }
    raw = samples.get(found.key, {})
    try:
        return found.Params.model_validate(raw).model_dump(mode="json")
    except ValidationError:
        return raw


def defaults_for(key: str, *, currency: Optional[str] = None) -> dict[str, Any]:
    params = _defaults(template(key))
    if currency:
        for value in params.values():
            if isinstance(value, dict) and "currency" in value:
                value["currency"] = currency
    return params


def recommended(business_type: Optional[str]) -> list[str]:
    """First run (§10.3): the mandates recommended for this kind of business, in library order."""
    keys = []
    for found in TEMPLATES.values():
        if found.plan == "enterprise":
            continue
        if not found.recommended_for or (business_type and business_type in found.recommended_for):
            keys.append(found.key)
    return keys


__all__ = ["BookFacts", "MandateError", "TEMPLATES", "defaults_for", "library", "load", "normalized", "params_of",
           "parse_spec", "recommended", "required_params", "spec_hash", "template", "validate"]
