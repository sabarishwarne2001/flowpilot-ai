"""ARCH45-S1:rules — ARCH-33 assertions reused as corroboration rules. Pure.

A rule is an ARCH-33 plan: a workspace's saved assertion definition (its
latest version per Flow Builder node) or a sentence given with the comparison
and compiled by ARCH-33's compiler ("Payment terms must not exceed 30 days",
"The notice period must be at least 60 days", "Governing law must be India").
Every rule is evaluated on EVERY document by ARCH-33's deterministic family
parsers, reading the document's clauses as chunks -- the same parsers, the
same verdicts, the same evidence a Flow Builder assertion node produces.

  RULE_CONFLICT  one document passes and another fails
  RULE_FAILED    every document that could be read fails
  RULE_VALUE     verdicts agree but the value read differs (45 days / 60 days)

Only the deterministic families run. The `llm` family needs a model call per
document, which the corroborator does not make (no model cost, no new cost
category): an ad-hoc sentence that compiles to `llm` is refused with the
compiler's reason, and a workspace definition of that family is listed as
skipped.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.services.corroboration import materiality as mat
from app.services.corroboration import vocabulary as v


class RuleError(ValueError):
    pass


@dataclass(frozen=True)
class RuleSpec:
    key: str                 # stable: "rule:<digest12>"
    sentence: str
    plan: Any                # assertions.compiler.AssertionPlan
    source: str              # WORKSPACE | ADHOC
    digest: str
    definition_id: Optional[str] = None

    def as_json(self) -> dict:
        return {"key": self.key, "sentence": self.sentence, "source": self.source, "digest": self.digest,
                "definition_id": self.definition_id, "family": self.plan.family, "understood_as": self.plan.describe(),
                "plan": self.plan.as_json()}


def plan_digest(plan: Any) -> str:
    return hashlib.sha256(json.dumps(plan.as_json(), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def spec_from_plan(plan: Any, *, sentence: str, source: str, definition_id: Optional[str] = None) -> RuleSpec:
    digest = plan_digest(plan)
    return RuleSpec(f"rule:{digest[:12]}", sentence, plan, source, digest, definition_id)


def compile_adhoc(sentence: str) -> RuleSpec:
    from app.services.assertions import compiler
    from app.services.assertions import vocabulary as av

    text = " ".join((sentence or "").split())
    if not text:
        raise RuleError("A rule cannot be empty.")
    if len(text) > v.MAX_RULE_LENGTH:
        raise RuleError(f"A rule is at most {v.MAX_RULE_LENGTH} characters.")
    outcome = compiler.compile_sentence(text)
    if outcome.plan.family == av.FAMILY_LLM:
        raise RuleError(f"'{text}' could not be read as a checkable rule ({outcome.reason or 'no deterministic family'}). "
                        "Phrase it like 'Payment terms must not exceed 30 days' or 'Governing law must be India'.")
    return spec_from_plan(outcome.plan, sentence=text, source=v.RULE_SOURCE_ADHOC)


def spec_from_json(payload: dict) -> RuleSpec:
    """Re-hydrate a rule stored on a run (corroboration_runs.rules)."""
    from decimal import Decimal

    from app.services.assertions import compiler

    p = dict(payload.get("plan") or {})
    bound = p.get("bound")
    plan = compiler.AssertionPlan(
        family=str(p.get("family")), subject=str(p.get("subject") or ""), operator=str(p.get("operator") or ""),
        bound=None if bound is None else Decimal(str(bound)), unit=p.get("unit"), values=tuple(p.get("values") or ()),
        basis=p.get("basis"), currency=p.get("currency"), clause_display=p.get("clause_display"),
        reason=p.get("reason"), retrieval_seeds=tuple(p.get("retrieval_seeds") or ()),
        sentence=str(payload.get("sentence") or ""), engine_version=str(p.get("engine_version") or ""))
    return RuleSpec(str(payload["key"]), str(payload.get("sentence") or ""), plan, str(payload.get("source")),
                    str(payload.get("digest")), payload.get("definition_id"))


@dataclass
class Reading:
    verdict: str
    value: Optional[str]
    unit: Optional[str]
    literal: Optional[str]
    clause_index: Optional[int]
    quote: str = ""

    @property
    def shown(self) -> str:
        if self.value is not None:
            return f"{self.value} {self.unit or ''}".strip()
        return self.literal or ("—" if self.verdict == "UNDETERMINED" else self.verdict.lower())

    @property
    def compared(self) -> Optional[str]:
        if self.value is not None:
            return f"{self.value}|{self.unit or ''}"
        return self.literal


def evaluate(spec: RuleSpec, clauses: Sequence[Any], *, workspace_currency: Optional[str] = None) -> Reading:
    """One rule on one document's clauses (ARCH-33 evaluate_deterministic)."""
    from app.services.assertions.evaluate import evaluate_deterministic
    from app.services.assertions.families import Chunk

    chunks = [Chunk(chunk_id=f"c{c.index}", chunk_index=c.index, text=c.text, page_number=c.page, retrieval_score=1.0)
              for c in clauses]
    if not chunks:
        return Reading("UNDETERMINED", None, None, None, None)
    result = evaluate_deterministic(spec.plan, chunks, workspace_currency=workspace_currency)
    ev = result.extracted_value or {}
    index = None
    chunk_id = ev.get("chunk_id")
    if chunk_id and str(chunk_id).startswith("c"):
        try:
            index = int(str(chunk_id)[1:])
        except ValueError:
            index = None
    return Reading(result.verdict, ev.get("value"), ev.get("unit"), ev.get("literal"), index, str(ev.get("quote") or ""))


@dataclass
class RuleFinding:
    kind: str
    spec: RuleSpec
    materiality: float
    readings: dict[int, Reading]
    detail: dict = field(default_factory=dict)


def compare(spec: RuleSpec, readings: dict[int, Reading]) -> Optional[RuleFinding]:
    determined = {d: r for d, r in readings.items() if r.verdict in ("PASS", "FAIL")}
    undetermined = sorted(d for d, r in readings.items() if r.verdict not in ("PASS", "FAIL"))
    if not determined:
        return None
    verdicts = {r.verdict for r in determined.values()}
    detail = {"undetermined_in": undetermined, "understood_as": spec.plan.describe()}
    if verdicts == {"PASS", "FAIL"}:
        return RuleFinding(v.KIND_RULE_CONFLICT, spec, mat.RULE_CONFLICT, readings, detail)
    if verdicts == {"FAIL"}:
        return RuleFinding(v.KIND_RULE_FAILED, spec, mat.RULE_FAILED, readings, detail)
    values = {r.compared for r in determined.values() if r.compared is not None}
    if len(values) > 1:
        return RuleFinding(v.KIND_RULE_VALUE, spec, mat.RULE_VALUE, readings, detail)
    return None


__all__ = ["Reading", "RuleError", "RuleFinding", "RuleSpec", "compare", "compile_adhoc", "evaluate", "plan_digest",
           "spec_from_json", "spec_from_plan"]
