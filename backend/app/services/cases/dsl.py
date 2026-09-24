"""ARCH43-S1:dsl — the case consistency rules. Pure; no I/O.

A rule compares values extracted from the case's documents:

  EQUAL        left == right (normalised: case, spacing, punctuation)
  FUZZY_EQUAL  Jaro-Winkler(left, right) >= threshold (default 0.92), on
               names normalised with ARCH-42's organisation/person rules
  DATE_ORDER   left <= right (dates parsed with ARCH-42's parser)
  WITHIN_DAYS  |right - left| <= days
  SUM_EQUALS   sum(terms) == right, within tolerance (default 0.01). A term
               sums the field over EVERY case document of its type, so
               "the invoices add up to the PO" is one rule.

An operand is {"doc_type": ..., "field": ...}. Outcomes: PASS, FAIL, MISSING
(no document of that type yet, or the field is absent) and ERROR (a value
that will not parse as a number or date). Only PASS and FAIL are verdicts;
MISSING keeps a case INCOMPLETE rather than failing it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Optional, Sequence

from app.services.entities import normalize as n

OPS = ("EQUAL", "FUZZY_EQUAL", "DATE_ORDER", "WITHIN_DAYS", "SUM_EQUALS")
PASS, FAIL, MISSING, ERROR = "PASS", "FAIL", "MISSING", "ERROR"
DEFAULT_FUZZY = 0.92
DEFAULT_TOLERANCE = Decimal("0.01")
_RULE_ID = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_DOC_TYPE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


class RuleError(ValueError):
    """A rule the DSL cannot run. Refused when the template is saved."""


@dataclass(frozen=True)
class Outcome:
    rule_id: str
    op: str
    outcome: str
    left: Optional[str] = None
    right: Optional[str] = None
    detail: dict[str, Any] = field(default_factory=dict)


def _operand(value: Any, where: str) -> dict[str, str]:
    if not isinstance(value, Mapping) or not isinstance(value.get("doc_type"), str) or not isinstance(value.get("field"), str):
        raise RuleError(f"{where} must be {{doc_type, field}}")
    if not _DOC_TYPE.match(value["doc_type"]):
        raise RuleError(f"{where}.doc_type {value['doc_type']!r} is not a document type key")
    if not value["field"].strip():
        raise RuleError(f"{where}.field is empty")
    return {"doc_type": value["doc_type"], "field": value["field"].strip()}


def validate_rule(rule: Any) -> dict[str, Any]:
    if not isinstance(rule, Mapping):
        raise RuleError("a rule must be an object")
    rid, op = str(rule.get("id") or ""), str(rule.get("op") or "").upper()
    if not _RULE_ID.match(rid):
        raise RuleError(f"rule id {rid!r} must be 1-64 lowercase letters, digits, - or _")
    if op not in OPS:
        raise RuleError(f"rule {rid}: op must be one of {', '.join(OPS)}")
    out: dict[str, Any] = {"id": rid, "op": op, "label": str(rule.get("label") or rid)[:200]}
    if op == "SUM_EQUALS":
        terms = rule.get("terms")
        if not isinstance(terms, Sequence) or isinstance(terms, (str, bytes)) or not terms:
            raise RuleError(f"rule {rid}: SUM_EQUALS needs a non-empty terms list")
        out["terms"] = [_operand(t, f"rule {rid}.terms[{i}]") for i, t in enumerate(terms)]
        out["right"] = _operand(rule.get("right"), f"rule {rid}.right")
        try:
            out["tolerance"] = str(Decimal(str(rule.get("tolerance", DEFAULT_TOLERANCE))))
        except InvalidOperation as exc:
            raise RuleError(f"rule {rid}: tolerance is not a number") from exc
        return out
    out["left"] = _operand(rule.get("left"), f"rule {rid}.left")
    out["right"] = _operand(rule.get("right"), f"rule {rid}.right")
    if op == "FUZZY_EQUAL":
        threshold = float(rule.get("threshold", DEFAULT_FUZZY))
        if not 0.5 <= threshold <= 1.0:
            raise RuleError(f"rule {rid}: threshold must be between 0.5 and 1")
        out["threshold"] = threshold
    if op == "WITHIN_DAYS":
        days = rule.get("days")
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise RuleError(f"rule {rid}: WITHIN_DAYS needs a whole number of days")
        out["days"] = days
    return out


def validate_rules(rules: Any) -> list[dict[str, Any]]:
    if not isinstance(rules, list):
        raise RuleError("rules must be a list")
    checked = [validate_rule(r) for r in rules]
    ids = [r["id"] for r in checked]
    if len(set(ids)) != len(ids):
        raise RuleError("rule ids must be unique within a template")
    return checked


def doc_types_of(rules: Sequence[Mapping[str, Any]]) -> set[str]:
    out: set[str] = set()
    for r in rules:
        for key in ("left", "right"):
            if isinstance(r.get(key), Mapping):
                out.add(r[key]["doc_type"])
        for term in r.get("terms") or []:
            out.add(term["doc_type"])
    return out


def field_value(fields: Mapping[str, Any], name: str) -> Any:
    if name in fields:
        return fields[name]
    wanted = re.sub(r"[\s\-]+", "_", name.strip().lower())
    for key, value in fields.items():
        if re.sub(r"[\s\-]+", "_", str(key).strip().lower()) == wanted:
            return value
    return None


def _number(value: Any) -> Decimal:
    if isinstance(value, bool):
        raise InvalidOperation
    if isinstance(value, (int, float, Decimal)):
        return Decimal(str(value))
    text = re.sub(r"[^\d.\-]", "", str(value).replace(",", ""))
    if text in ("", "-", ".", "-."):
        raise InvalidOperation
    return Decimal(text)


def _date(value: Any) -> date:
    iso = n.parse_date(str(value))
    if not iso:
        raise ValueError(f"not a date: {value!r}")
    return date.fromisoformat(iso)


def _plain(value: Any) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", str(value).lower())).strip()


def _fuzzy_norm(value: Any) -> str:
    text = str(value)
    org = n.normalize_organization(text)
    return org or n.normalize_person(text) or _plain(text)


Documents = Mapping[str, Sequence[Mapping[str, Any]]]  # doc_type -> extracted fields of each document


def _first(docs: Documents, operand: Mapping[str, str]) -> Any:
    for fields in docs.get(operand["doc_type"], ()):
        value = field_value(fields, operand["field"])
        if value not in (None, "", []):
            return value
    return None


def evaluate(rule: Mapping[str, Any], docs: Documents) -> Outcome:
    rid, op = rule["id"], rule["op"]
    try:
        if op == "SUM_EQUALS":
            total = Decimal("0")
            seen = 0
            for term in rule["terms"]:
                for fields in docs.get(term["doc_type"], ()):
                    value = field_value(fields, term["field"])
                    if value in (None, ""):
                        continue
                    total += _number(value)
                    seen += 1
            right = _first(docs, rule["right"])
            if not seen or right is None:
                return Outcome(rid, op, MISSING, str(total) if seen else None, None if right is None else str(right))
            target = _number(right)
            ok = abs(total - target) <= Decimal(rule.get("tolerance") or DEFAULT_TOLERANCE)
            return Outcome(rid, op, PASS if ok else FAIL, str(total), str(target), {"terms": seen, "difference": str(total - target)})
        left, right = _first(docs, rule["left"]), _first(docs, rule["right"])
        if left is None or right is None:
            return Outcome(rid, op, MISSING, None if left is None else str(left), None if right is None else str(right))
        if op == "EQUAL":
            return Outcome(rid, op, PASS if _plain(left) == _plain(right) else FAIL, str(left), str(right))
        if op == "FUZZY_EQUAL":
            score = n.jaro_winkler(_fuzzy_norm(left), _fuzzy_norm(right))
            threshold = float(rule.get("threshold", DEFAULT_FUZZY))
            return Outcome(rid, op, PASS if score >= threshold else FAIL, str(left), str(right),
                           {"similarity": round(score, 4), "threshold": threshold})
        a, b = _date(left), _date(right)
        if op == "DATE_ORDER":
            return Outcome(rid, op, PASS if a <= b else FAIL, a.isoformat(), b.isoformat())
        days = abs((b - a).days)
        return Outcome(rid, op, PASS if days <= int(rule["days"]) else FAIL, a.isoformat(), b.isoformat(),
                       {"days": days, "limit": int(rule["days"])})
    except (InvalidOperation, ValueError, TypeError) as exc:
        return Outcome(rid, op, ERROR, detail={"error": str(exc)[:200]})


__all__ = ["DEFAULT_FUZZY", "ERROR", "FAIL", "MISSING", "OPS", "Outcome", "PASS", "RuleError", "doc_types_of",
           "evaluate", "field_value", "validate_rule", "validate_rules"]
