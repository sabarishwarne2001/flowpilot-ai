"""ARCH-33 §4.3/§4.5 — the assertion node executor.

WHY A SEPARATE MODULE RATHER THAN A BRANCH INSIDE `executor.py`
===============================================================

`executor.py` is the hot path of the automation engine and is gated by
ARCH-13's own suite. ARCH-33 adds one node type; putting sixty lines of
retrieval, evaluation and triage inside `_execute_node` would mean every
future change to an assertion is a change to the module that runs every
workflow in the product.

So `executor.py` gains four lines that dispatch here, and everything specific
to an assertion lives in one file that `verify_arch33.py` can reason about.

THE EXECUTOR CONTRACT THIS HONOURS
==================================

`automation_node_runs` already records node_key, node_type, sequence, status,
timings, input and output digests, and details. An assertion node run is an
ordinary row in that table: a reader who has never heard of ARCH-33 sees a
COMPLETED node with a digest and a details blob, exactly like an LLM
extraction node.

`input_digest` is the plan's digest including `ENGINE_VERSION` and the rule's
threshold. That is what makes a re-run auditable: the same document under the
same rule and the same engine produced the same digest, and a digest that
changed says which of the three moved.

RESUMPTION IS A READ, NOT A SECOND EVALUATION
=============================================

§4.5: "When the reviewer resolves it, the execution resumes on the edge
matching the reviewer's verdict."

The re-run enters this function again, because `automation.execute` re-walks
the whole graph — that is how ARCH-13 works and ARCH-33 does not change it. So
the FIRST thing this does is look for an already-resolved evaluation of this
definition against this work item. If one exists, it takes the reviewer's edge
and evaluates nothing.

Re-evaluating instead would be worse than wasteful. The document has not
changed, so the engine would reach the same verdict it reached before, triage
it again, and open a second review — and a reviewer would find the item they
just resolved back in their queue. The read is what makes the resolution
stick.

WHY A TRIAGED ASSERTION STILL TAKES ITS EDGE
============================================

The `triage` edge is a real edge and the execution walks it. §4.3 draws it
going to `document_verifications`, and a workflow author typically wires it to
a notification. It is not a pause: ARCH-13 executions are single-pass, and an
execution that blocked mid-walk would hold a worker for as long as a human
took to answer.

So the first pass routes to triage, opens the review and finishes. The
resolution enqueues a fresh execution, which re-walks the graph, reads the
reviewer's verdict here, and continues on the right edge. The
`automation_node_runs` rows then read as two passes over the same node, which
is what actually happened.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from sqlalchemy import select

from app.models.assertion import AssertionDefinition, AssertionEvaluation
from app.models.automation_graph import AutomationNode
from app.models.workspace import Workspace
from app.services.assertions import (
    definition_service,
    evaluate as evaluate_module,
    retrieve as retrieve_module,
    triage as triage_module,
    vocabulary as vocab,
)

logger = logging.getLogger("app.services.assertions.node_executor")

__all__ = ["execute", "AssertionNodeError"]


class AssertionNodeError(RuntimeError):
    """The node cannot run at all. Recorded as a FAILED node run."""


def _definition_for(state: Any, node_key: str) -> AssertionDefinition:
    node = state.db.execute(
        select(AutomationNode).where(
            AutomationNode.rule_id == state.rule.id,
            AutomationNode.node_key == node_key,
        )
    ).scalar_one_or_none()
    if node is None:
        raise AssertionNodeError(
            f"Assertion node {node_key!r} has no row in automation_nodes; the "
            "graph and the execution disagree about what this rule contains."
        )

    definition = definition_service.latest_for_node(state.db, node_id=node.id)
    if definition is None:
        raise AssertionNodeError(
            f"Assertion node {node_key!r} has no saved clause. The step was "
            "added to the graph but never given a sentence, so there is "
            "nothing to check this document against."
        )
    return definition


def _resolved_evaluation(
    state: Any, *, definition: AssertionDefinition, work_item_id: uuid.UUID
) -> Optional[AssertionEvaluation]:
    """A reviewer's answer to this definition on this document, if there is one."""
    return state.db.execute(
        select(AssertionEvaluation)
        .where(
            AssertionEvaluation.definition_id == definition.id,
            AssertionEvaluation.work_item_id == work_item_id,
            AssertionEvaluation.reviewer_verdict.isnot(None),
        )
        .order_by(AssertionEvaluation.reviewed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def execute(state: Any, *, node: Any) -> bool:
    """Run one assertion node. Returns True; the edge is chosen by skipping.

    `state` is `executor._WalkState`, typed loosely so this module does not
    import `executor` at module scope and create a cycle — `executor` imports
    THIS module, lazily, from inside `_execute_node`.
    """
    from app.services.automation.executor import _propagate_skip, _record_node
    from app.models.automation_execution import AutomationNodeRunStatus

    work_item = state.work_item
    if work_item is None:
        raise AssertionNodeError(
            "An assertion checks a document, and this execution has no work "
            "item attached. A clause step on a rule triggered by something "
            "other than a document has nothing to read."
        )

    definition = _definition_for(state, node.node_key)
    plan = definition_service.plan_from_row(definition)
    digest = plan.input_digest(
        settings={
            "threshold": str(definition.threshold),
            "definition_version": definition.version,
            "work_item_id": str(work_item.id),
        }
    )

    # --- resumption -------------------------------------------------------
    resolved = _resolved_evaluation(
        state, definition=definition, work_item_id=work_item.id
    )
    if resolved is not None:
        edge = (
            vocab.EDGE_PASS
            if resolved.reviewer_verdict == vocab.VERDICT_PASS
            else vocab.EDGE_TRIAGE
        )
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            input_digest=digest,
            details={
                "assertion": True,
                "resumed": True,
                "evaluation_id": str(resolved.id),
                "engine_verdict": resolved.verdict,
                "reviewer_verdict": resolved.reviewer_verdict,
                "edge": edge,
                "sentence": definition.sentence,
            },
        )
        _propagate_skip(state, node.node_key, taken=edge)
        logger.info(
            "assertion.node_resumed",
            extra={
                "definition_id": str(definition.id),
                "work_item_id": str(work_item.id),
                "edge": edge,
            },
        )
        return True

    # --- first pass -------------------------------------------------------
    run = _record_node(
        state,
        node_key=node.node_key,
        node_type=node.node_type,
        # RUNNING, not COMPLETED. The row is written BEFORE retrieval so that
        # `assertion_evaluations.node_run_id` has something to point at — the
        # FK is NOT NULL — and so a worker killed mid-evaluation leaves a
        # RUNNING node run that `reap_stranded` can see rather than no row at
        # all.
        status=AutomationNodeRunStatus.RUNNING,
        input_digest=digest,
        details={
            "assertion": True,
            "sentence": definition.sentence,
            "family": definition.family,
            "understood_as": plan.describe(),
        },
    )

    retrieval = retrieve_module.retrieve(
        state.db,
        plan=plan,
        organization_id=definition.organization_id,
        workspace_id=definition.workspace_id,
        work_item_id=work_item.id,
    )

    workspace = state.db.execute(
        select(Workspace).where(Workspace.id == definition.workspace_id)
    ).scalar_one_or_none()

    evaluation_result = evaluate_module.evaluate(
        state.db,
        plan=plan,
        chunks=retrieval.chunks,
        organization_id=definition.organization_id,
        workspace_id=definition.workspace_id,
        workspace_currency=getattr(workspace, "currency", None),
        query_phrases=retrieval.phrases,
    )

    outcome = triage_module.record_evaluation(
        state.db,
        definition=definition,
        evaluation_result=evaluation_result,
        node_run_id=run.id,
        work_item_id=work_item.id,
        retrieval=retrieval,
        input_digest=digest,
    )

    run.status = AutomationNodeRunStatus.COMPLETED
    # `_record_node` only increments this for a row it wrote as COMPLETED, and
    # this one was written as RUNNING. Incrementing here keeps
    # `automation_executions.nodes_executed` honest; without it an assertion
    # node would be invisible in every execution summary.
    state.execution.nodes_executed += 1
    run.output_digest = f"sha256:{outcome.evaluation.id.hex}"
    run.details = {
        **(run.details or {}),
        **outcome.decision.as_details(),
        "evaluation_id": str(outcome.evaluation.id),
        "verification_id": (
            str(outcome.verification.id) if outcome.verification else None
        ),
        "retrieval": retrieval.as_details(),
        "features": (
            evaluation_result.features.as_evidence()
            if evaluation_result.features
            else None
        ),
        "notes": list(evaluation_result.notes),
    }
    state.db.flush()

    _propagate_skip(state, node.node_key, taken=outcome.edge)
    state.db.commit()

    logger.info(
        "assertion.node_completed",
        extra={
            "definition_id": str(definition.id),
            "work_item_id": str(work_item.id),
            "edge": outcome.edge,
            "verdict": outcome.evaluation.verdict,
        },
    )
    return True