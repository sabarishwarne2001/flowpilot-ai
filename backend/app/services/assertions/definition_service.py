"""ARCH-33 §4.2/§4.6 — saving an assertion, and testing one without running it.

COMPILATION HAPPENS AT SAVE TIME, IN THE REQUEST CYCLE
======================================================

§4.2: "the rule builder shows the compiled form before the rule can be saved."
That is a product requirement and it is also why `compiler.py` is pure and
fast — the administrator finds out their sentence was not understood while
they are still looking at it, not when a document fails three days later.

`preview()` compiles without writing. `save()` compiles again and persists
what IT compiled, never what the client sent back. A client that posted a plan
would be a client that could post any plan, and `plan` is the thing every
parser trusts.

VERSIONS, NOT EDITS
===================

Editing a rule writes a new `assertion_definitions` row with `version + 1`
against the same `node_id`; `uq_ad_node_version` makes the increment safe.
`assertion_evaluations.definition_id` then points at the definition that
actually ran, with `ON DELETE RESTRICT`.

The alternative — mutating the row — is cheaper and wrong in a way that shows
up in an audit: a rule tightened on Friday would retroactively relabel
Thursday's automatic passes as having been made under the new bound, and the
evidence that they were not would be gone.

"TEST ON A DOCUMENT" RUNS THE REAL PATH AND WRITES NOTHING
==========================================================

§4.6's runner "shows verdict, extracted value, confidence and the highlighted
paragraph without starting an execution". `simulate()` calls the same
`retrieve` and the same `evaluate` the executor calls, applies the same
threshold, and then stops: no `assertion_evaluations` row, no
`document_verifications` row, no meter.

A simulator that took a shortcut would be a simulator that agreed with
production right up until it mattered. The one thing it does NOT share is
metering — a preview is not consumption.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.assertion import AssertionDefinition, AssertionEvaluation
from app.models.automation import AutomationRule
from app.models.automation_graph import AutomationNode
from app.models.work_item import WorkItem
from app.models.workspace import Workspace
from app.services.assertions import (
    calibration,
    compiler,
    evaluate as evaluate_module,
    retrieve,
    routing,
    triage as triage_module,
    vocabulary as vocab,
)

logger = logging.getLogger("app.services.assertions.definition_service")

__all__ = [
    "AssertionError_",
    "CompiledPreview",
    "SimulationResult",
    "preview",
    "save",
    "latest_for_node",
    "latest_for_rule",
    "plan_from_row",
    "simulate",
    "recent_raw_scores",
]


class AssertionError_(ValueError):
    """A definition cannot be saved as asked. Rendered as an ARCH-01 envelope."""


@dataclass(frozen=True)
class CompiledPreview:
    outcome: Any
    threshold: Decimal
    effective_threshold: Decimal
    consequence: Any

    def as_payload(self) -> dict[str, Any]:
        plan = self.outcome.plan
        return {
            "family": plan.family,
            "evaluation_mode": self.outcome.evaluation_mode,
            "understood_as": plan.describe(),
            "requires_acknowledgement": self.outcome.requires_acknowledgement,
            "reason": self.outcome.reason,
            "notes": list(self.outcome.notes),
            "plan": plan.as_json(),
            "threshold": str(self.threshold),
            "effective_threshold": str(self.effective_threshold),
            "consequence": self.consequence.sentence(),
            "enough_labels": self.consequence.enough_labels,
            "label_count": self.consequence.label_count,
            "seed_phrases": list(vocab.seed_phrases_for(plan.family)),
        }


@dataclass(frozen=True)
class SimulationResult:
    evaluation: Any
    decision: Any
    effective_threshold: Decimal
    calibrated_probability: Optional[Decimal]
    work_item_id: uuid.UUID

    def as_payload(self) -> dict[str, Any]:
        return {
            "work_item_id": str(self.work_item_id),
            "verdict": self.evaluation.verdict,
            "routed_to": self.decision.routed_to,
            "edge": self.decision.edge,
            "reason": self.decision.reason,
            "raw_score": str(self.evaluation.raw_score),
            "calibrated_probability": (
                None
                if self.calibrated_probability is None
                else str(self.calibrated_probability)
            ),
            "effective_threshold": str(self.effective_threshold),
            "extracted_value": self.evaluation.extracted_value,
            "evidence": [dict(item) for item in self.evaluation.evidence],
            "features": (
                self.evaluation.features.as_evidence()
                if self.evaluation.features
                else None
            ),
        }


def recent_raw_scores(
    db: Session, *, organization_id: uuid.UUID, family: str, limit: int = 200
) -> tuple[Decimal, ...]:
    """Raw scores of recent evaluations, for §4.6's "recent documents" share.

    Recent EVALUATIONS of this family across the tenant, not of this rule: a
    rule being created has no history, and the slider has to say something
    truthful before the first document goes through it. The family is the
    right population because the score's distribution is a property of how the
    family reads documents, not of which bound an administrator chose.
    """
    rows = (
        db.execute(
            select(AssertionEvaluation.raw_score)
            .join(
                AssertionDefinition,
                AssertionDefinition.id == AssertionEvaluation.definition_id,
            )
            .where(
                AssertionEvaluation.organization_id == organization_id,
                AssertionDefinition.family == family,
            )
            .order_by(AssertionEvaluation.created_at.desc())
            .limit(int(limit))
        )
        .scalars()
        .all()
    )
    return tuple(Decimal(str(row)) for row in rows)


def preview(
    db: Session,
    *,
    organization_id: uuid.UUID,
    sentence: str,
    threshold: Any = vocab.DEFAULT_THRESHOLD,
) -> CompiledPreview:
    """Compile and describe, writing nothing. Powers "Understood as:"."""
    try:
        outcome = compiler.compile_sentence(sentence)
    except compiler.CompileError as exc:
        raise AssertionError_(str(exc)) from exc

    chosen = Decimal(str(threshold))
    _assert_threshold(chosen)

    model = triage_module.calibration_model_for(
        db, organization_id=organization_id, family=outcome.plan.family
    )
    consequence = calibration.consequence(
        model,
        recent_raw_scores=recent_raw_scores(
            db, organization_id=organization_id, family=outcome.plan.family
        ),
        threshold=chosen,
    )
    return CompiledPreview(
        outcome=outcome,
        threshold=chosen,
        effective_threshold=calibration.effective_threshold(chosen, model),
        consequence=consequence,
    )


def _assert_threshold(threshold: Decimal) -> None:
    low = Decimal(vocab.THRESHOLD_MIN_EXCLUSIVE)
    high = Decimal(vocab.THRESHOLD_MAX_EXCLUSIVE)
    if not (low < threshold < high):
        raise AssertionError_(
            f"A confidence setting must be above {low} and below {high}. "
            f"At {high} nothing can ever pass automatically, and below {low} "
            "the step would pass answers more likely wrong than right."
        )


def save(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    node: AutomationNode,
    sentence: str,
    threshold: Any,
    acknowledged_by: Optional[uuid.UUID] = None,
) -> AssertionDefinition:
    """Compile, version and persist. The plan is never taken from the client."""
    if node.node_type != vocab.ASSERTION_NODE_TYPE:
        raise AssertionError_(
            f"Node {node.node_key!r} is a {node.node_type} node. An assertion "
            "definition can only attach to an assertion node, which has pass "
            "and triage edges rather than true and false."
        )

    try:
        outcome = compiler.compile_sentence(sentence)
    except compiler.CompileError as exc:
        raise AssertionError_(str(exc)) from exc

    chosen = Decimal(str(threshold))
    _assert_threshold(chosen)

    if outcome.requires_acknowledgement and acknowledged_by is None:
        raise AssertionError_(
            "This sentence could not be compiled into a typed check, so it "
            "will be checked by the AI model and billed as assistant usage. "
            "Confirm that before saving. "
            + (outcome.reason or "")
        )
    if not outcome.requires_acknowledgement:
        # A deterministic family must NOT carry an acknowledgement. The
        # biconditional in ck_ad_llm_acknowledged is one-directional, but a
        # stray acknowledgement on a typed rule would make the console render
        # the AI notice on a rule that never calls a model.
        acknowledged_by = None

    current = (
        db.execute(
            select(func.max(AssertionDefinition.version)).where(
                AssertionDefinition.node_id == node.id
            )
        ).scalar()
        or 0
    )

    definition = AssertionDefinition(
        organization_id=organization_id,
        workspace_id=workspace_id,
        node_id=node.id,
        sentence=outcome.plan.sentence,
        family=outcome.plan.family,
        plan=outcome.plan.as_json(),
        threshold=chosen,
        evaluation_mode=outcome.evaluation_mode,
        llm_acknowledged_by=acknowledged_by,
        version=int(current) + 1,
    )
    db.add(definition)
    db.flush()

    logger.info(
        "assertion.definition_saved",
        extra={
            "definition_id": str(definition.id),
            "node_id": str(node.id),
            "family": definition.family,
            "version": definition.version,
        },
    )
    return definition


def latest_for_node(
    db: Session, *, node_id: uuid.UUID
) -> Optional[AssertionDefinition]:
    return db.execute(
        select(AssertionDefinition)
        .where(AssertionDefinition.node_id == node_id)
        .order_by(AssertionDefinition.version.desc())
        .limit(1)
    ).scalar_one_or_none()


def latest_for_rule(
    db: Session, *, rule_id: uuid.UUID
) -> list[AssertionDefinition]:
    """The live definition of every assertion node on one rule."""
    nodes = (
        db.execute(
            select(AutomationNode.id).where(
                AutomationNode.rule_id == rule_id,
                AutomationNode.node_type == vocab.ASSERTION_NODE_TYPE,
            )
        )
        .scalars()
        .all()
    )
    found: list[AssertionDefinition] = []
    for node_id in nodes:
        definition = latest_for_node(db, node_id=node_id)
        if definition is not None:
            found.append(definition)
    return found


def plan_from_row(definition: AssertionDefinition) -> compiler.AssertionPlan:
    """Rebuild the typed plan from the stored jsonb.

    Re-hydrated rather than re-compiled. Re-compiling would silently apply a
    NEWER compiler to an OLDER rule, so a grammar fix shipped on Tuesday would
    change what a rule saved in January means without anybody editing it. The
    stored plan carries its own `engine_version` for exactly this reason.
    """
    payload = dict(definition.plan or {})
    bound = payload.get("bound")
    return compiler.AssertionPlan(
        family=str(payload.get("family") or definition.family),
        subject=str(payload.get("subject") or ""),
        operator=str(payload.get("operator") or ""),
        bound=None if bound is None else Decimal(str(bound)),
        unit=payload.get("unit"),
        values=tuple(payload.get("values") or ()),
        basis=payload.get("basis"),
        currency=payload.get("currency"),
        clause_display=payload.get("clause_display"),
        reason=payload.get("reason"),
        retrieval_seeds=tuple(payload.get("retrieval_seeds") or ()),
        sentence=definition.sentence,
        engine_version=str(payload.get("engine_version") or vocab.ENGINE_VERSION),
    )


def simulate(
    db: Session,
    *,
    definition: Optional[AssertionDefinition] = None,
    sentence: Optional[str] = None,
    threshold: Any = vocab.DEFAULT_THRESHOLD,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item: WorkItem,
) -> SimulationResult:
    """§4.6's "Test on a document". Same code path, nothing written.

    Accepts either a saved definition or a bare sentence, because the runner
    in the rule builder is used BEFORE the step is saved — which is the only
    time it can change the author's mind.
    """
    if definition is not None:
        plan = plan_from_row(definition)
        chosen = Decimal(str(definition.threshold))
        family = definition.family
    else:
        try:
            outcome = compiler.compile_sentence(sentence or "")
        except compiler.CompileError as exc:
            raise AssertionError_(str(exc)) from exc
        plan = outcome.plan
        chosen = Decimal(str(threshold))
        _assert_threshold(chosen)
        family = plan.family

    outcome_retrieval = retrieve.retrieve(
        db,
        plan=plan,
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_id=work_item.id,
    )

    workspace = db.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()

    evaluation = evaluate_module.evaluate(
        db,
        plan=plan,
        chunks=outcome_retrieval.chunks,
        organization_id=organization_id,
        workspace_id=workspace_id,
        workspace_currency=getattr(workspace, "currency", None),
        query_phrases=outcome_retrieval.phrases,
    )

    model = triage_module.calibration_model_for(
        db, organization_id=organization_id, family=family
    )
    applied = calibration.effective_threshold(chosen, model)
    probability = calibration.calibrate(model, evaluation.raw_score)

    decision = routing.decide(
        verdict=evaluation.verdict,
        raw_score=evaluation.raw_score,
        calibrated_probability=probability,
        effective_threshold=applied,
    )

    # Nothing is written. No AssertionEvaluation, no DocumentVerification, no
    # usage event. A preview is not consumption, and a preview that opened a
    # review would put a document in a reviewer's queue because an
    # administrator was experimenting.
    logger.info(
        "assertion.simulated",
        extra={
            "workspace_id": str(workspace_id),
            "work_item_id": str(work_item.id),
            "family": family,
            "verdict": evaluation.verdict,
            "routed_to": decision.routed_to,
        },
    )

    return SimulationResult(
        evaluation=evaluation,
        decision=decision,
        effective_threshold=applied,
        calibrated_probability=probability,
        work_item_id=work_item.id,
    )


def assertion_node_for(
    db: Session, *, rule: AutomationRule, node_key: str
) -> AutomationNode:
    node = db.execute(
        select(AutomationNode).where(
            AutomationNode.rule_id == rule.id,
            AutomationNode.node_key == node_key,
        )
    ).scalar_one_or_none()
    if node is None:
        raise AssertionError_(
            f"Rule {rule.id} has no node {node_key!r}. Save the graph before "
            "attaching an assertion to one of its nodes."
        )
    return node