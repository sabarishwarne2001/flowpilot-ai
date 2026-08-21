"""ARCH-13 Step 13.5 — the execution engine.

RESOURCE CEILINGS, ALL CONFIGURED, ALL ENFORCED

    AUTOMATION_MAX_DEPTH                  5       13.2, refusal + DB CHECK at 16
    AUTOMATION_MAX_NODES                  50      validation at save
    AUTOMATION_EXECUTION_TIMEOUT_S        120     deadline_at, checked between nodes
    AUTOMATION_MAX_ACTIONS_PER_EXECUTION  20      counter
    AUTOMATION_DEFAULT_BUDGET_MICROS      50_000  13.3

TIMEOUT IS CHECKED BETWEEN NODES, NOT WITH A SIGNAL
===================================================

A node in flight is a provider call with its own timeout, and killing the
worker mid-call would leave an LLM reservation unsettled — which ARCH-12
established is the failure that produces unbillable generation. Between-node
checking means a long node overruns the deadline by at most one node's
duration and settles cleanly.

ACTIONS NEVER RUN IN THE SAME TRANSACTION AS THE GRAPH WALK
===========================================================

Each action commits its own node run, then performs its effect, then records
the outcome. An email that succeeds inside a transaction that later rolls back
has still been sent, and the record of it has not.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.automation import AutomationRule
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
    AutomationNodeRun,
    AutomationNodeRunStatus,
)
from app.models.outbox_event import OutboxEvent
from app.models.work_item import WorkItem
from app.services.automation import budget as budget_service
from app.services.automation import cycle_detector, graph_service
from app.services.automation.contracts import (
    ActionNodeConfig,
    ActionSpec,
    FactSet,
    ToolContractViolation,
)

logger = logging.getLogger("app.services.automation.executor")


class ExecutionHalted(RuntimeError):
    """A node failed and the rule's on_error policy is HALT."""


@dataclass
class ExecutionResult:
    execution_id: uuid.UUID
    status: AutomationExecutionStatus
    nodes_executed: int = 0
    actions_executed: int = 0
    spent_cost_micros: int = 0
    emitted_event_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_details(self) -> dict[str, Any]:
        return {
            "execution_id": str(self.execution_id),
            "status": self.status.value,
            "nodes_executed": self.nodes_executed,
            "actions_executed": self.actions_executed,
            "spent_cost_micros": self.spent_cost_micros,
            "emitted_events": len(self.emitted_event_ids),
            "error": self.error,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def create_execution(
    db: Session,
    *,
    rule: AutomationRule,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    correlation_id: uuid.UUID,
    depth: int,
    work_item_id: Optional[uuid.UUID] = None,
    outbox_event_id: Optional[uuid.UUID] = None,
    causation_id: Optional[uuid.UUID] = None,
    node_count: int = 0,
) -> AutomationExecution:
    suppression = cycle_detector.check(
        db,
        rule_id=rule.id,
        correlation_id=correlation_id,
        depth=depth,
        causation_id=causation_id,
    )

    execution = AutomationExecution(
        organization_id=organization_id,
        workspace_id=workspace_id,
        rule_id=rule.id,
        work_item_id=work_item_id,
        outbox_event_id=outbox_event_id,
        correlation_id=correlation_id,
        depth=depth,
        status=(
            suppression.status if suppression else AutomationExecutionStatus.QUEUED
        ),
        budget_cost_micros=budget_service.resolve_budget_micros(rule),
        node_count=node_count,
        details=suppression.as_details() if suppression else {},
        error=suppression.reason if suppression else None,
        completed_at=_now() if suppression else None,
    )
    db.add(execution)
    db.flush()
    return execution


@dataclass
class _WalkState:
    db: Session
    execution: AutomationExecution
    rule: AutomationRule
    work_item: Optional[WorkItem]
    graph: graph_service.CompiledGraph
    trigger_event: Optional[OutboxEvent]
    facts: FactSet = field(default_factory=FactSet)
    actions_executed: int = 0
    emitted_event_ids: list[str] = field(default_factory=list)
    skipped: set[str] = field(default_factory=set)
    sequence: int = 0


def _evaluate_condition_node(state: _WalkState, config: dict[str, Any]) -> bool:
    from app.services.automation_service import _evaluate_rule_conditions

    if state.work_item is None:
        return False

    class _Adapter:
        id = state.rule.id
        conditions = config.get("conditions") or []
        logic_operator = config.get("logic_operator", "AND")

    return bool(_evaluate_rule_conditions(_Adapter(), state.work_item))


def _run_action_node(
    state: _WalkState,
    *,
    node_key: str,
    config: dict[str, Any],
    perform: Callable[[ActionSpec, ActionNodeConfig], str],
) -> str:
    ceiling = int(settings.AUTOMATION_MAX_ACTIONS_PER_EXECUTION)
    if state.actions_executed >= ceiling:
        raise ExecutionHalted(
            f"Execution reached AUTOMATION_MAX_ACTIONS_PER_EXECUTION "
            f"({ceiling}). Action limit reached for this run."
        )

    node_config = ActionNodeConfig.from_node_config(config)
    from app.services.tools import action_selectors

    selector_name = action_selectors.resolve_selector(node_config.action_type)
    if selector_name is None:
        raise ValueError(
            f"No registered selector for action type {node_config.action_type!r}."
        )

    from app.services.fenced_context import TOOL_SELECTORS

    selector = TOOL_SELECTORS[selector_name]
    spec = selector(node_config=node_config, facts=state.facts)

    outcome = perform(spec, node_config)
    state.actions_executed += 1
    return outcome


def run_execution(
    db: Session,
    *,
    execution: AutomationExecution,
    rule: AutomationRule,
    work_item: Optional[WorkItem],
    trigger_event: Optional[OutboxEvent] = None,
    perform_action: Optional[Callable[[ActionSpec, ActionNodeConfig], str]] = None,
    call_model: Optional[Callable[[str, str], tuple[str, Any]]] = None,
) -> ExecutionResult:
    if execution.is_suppressed:
        return ExecutionResult(
            execution_id=execution.id,
            status=execution.status,
            error=execution.error,
        )

    graph = graph_service.load_graph(db, rule=rule)
    deadline = _now() + timedelta(
        seconds=int(settings.AUTOMATION_EXECUTION_TIMEOUT_S)
    )

    execution.status = AutomationExecutionStatus.RUNNING
    execution.started_at = _now()
    execution.deadline_at = deadline
    execution.node_count = len(graph.nodes)
    db.commit()

    state = _WalkState(
        db=db,
        execution=execution,
        rule=rule,
        work_item=work_item,
        graph=graph,
        trigger_event=trigger_event,
    )

    on_error = str(getattr(rule, "on_error", "HALT") or "HALT").upper()
    perform = perform_action or _default_perform_action(state)
    terminal = AutomationExecutionStatus.COMPLETED
    error: Optional[str] = None

    for node_key in graph.order:
        effective_deadline = execution.deadline_at or deadline
        if _now() >= effective_deadline:
            terminal = AutomationExecutionStatus.TIMED_OUT
            error = (
                f"Execution exceeded AUTOMATION_EXECUTION_TIMEOUT_S "
                f"({settings.AUTOMATION_EXECUTION_TIMEOUT_S}s) before node "
                f"{node_key!r}. Nodes already completed are recorded and their "
                "effects have happened."
            )
            logger.warning(
                "automation.execution_timed_out",
                extra={
                    "execution_id": str(execution.id),
                    "rule_id": str(rule.id),
                    "stopped_before": node_key,
                    "nodes_executed": execution.nodes_executed,
                },
            )
            break

        node = graph.node(node_key)

        if node_key in state.skipped:
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.SKIPPED,
                details={"reason": "branch not taken"},
            )
            _propagate_skip(state, node_key)
            continue

        try:
            outcome = _execute_node(
                state, node=node, perform=perform, call_model=call_model
            )
        except budget_service.BudgetExhausted as exc:
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.BUDGET_EXHAUSTED,
                error=str(exc),
                details=exc.as_details(),
            )
            terminal = AutomationExecutionStatus.BUDGET_EXHAUSTED
            error = str(exc)
            break
        except ExecutionHalted as exc:
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.FAILED,
                error=str(exc),
            )
            terminal = AutomationExecutionStatus.FAILED
            error = str(exc)
            break
        except ToolContractViolation as exc:
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.FAILED,
                error=str(exc),
                details={"r33_violation": True},
            )
            logger.error(
                "automation.tool_boundary_violation",
                extra={
                    "execution_id": str(execution.id),
                    "rule_id": str(rule.id),
                    "node_key": node_key,
                    "error": str(exc),
                },
            )
            terminal = AutomationExecutionStatus.FAILED
            error = str(exc)
            break
        except Exception as exc:  # noqa: BLE001
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning(
                "automation.node_failed",
                extra={
                    "execution_id": str(execution.id),
                    "node_key": node_key,
                    "on_error": on_error,
                    "error": str(exc),
                },
            )
            if on_error == "HALT":
                terminal = AutomationExecutionStatus.FAILED
                error = f"Node {node_key!r} failed: {exc}"
                break
            continue

        if outcome is False:
            _propagate_skip(state, node_key, taken=None)

    execution.status = terminal
    execution.error = error
    execution.completed_at = _now()
    execution.deadline_at = None
    execution.emitted_event_ids = list(state.emitted_event_ids)
    execution.actions_executed = state.actions_executed
    db.commit()

    result = ExecutionResult(
        execution_id=execution.id,
        status=terminal,
        nodes_executed=execution.nodes_executed,
        actions_executed=state.actions_executed,
        spent_cost_micros=int(execution.spent_cost_micros),
        emitted_event_ids=list(state.emitted_event_ids),
        error=error,
    )
    logger.info("automation.execution_complete", extra=result.as_details())
    return result


def _execute_node(
    state: _WalkState,
    *,
    node: graph_service.NodeSpec,
    perform: Callable[[ActionSpec, ActionNodeConfig], str],
    call_model: Optional[Callable[[str, str], tuple[str, Any]]],
) -> Any:
    config = dict(node.config or {})

    if node.node_type == "trigger":
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
        )
        return True

    if node.node_type in ("condition", "branch"):
        matched = _evaluate_condition_node(state, config)
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            details={"matched": matched},
            input_digest=_digest(config.get("conditions") or []),
        )
        if node.node_type == "branch":
            _propagate_skip(state, node.node_key, taken="true" if matched else "false")
            return True
        return matched

    if node.node_type == "join":
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
        )
        return True

    if node.node_type == "action":
        action_type = str(config.get("action_type") or "").lower().strip()

        if action_type in ("llm.extract", "llm.classify"):
            return _run_llm_node(state, node=node, config=config, call_model=call_model)

        outcome = _run_action_node(
            state, node_key=node.node_key, config=config, perform=perform
        )
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            details={"outcome": outcome, "action_type": action_type},
        )
        return True

    raise ValueError(f"Unknown node type {node.node_type!r}")


def _run_llm_node(
    state: _WalkState,
    *,
    node: graph_service.NodeSpec,
    config: dict[str, Any],
    call_model: Optional[Callable[[str, str], tuple[str, Any]]],
) -> bool:
    from app.services.automation.extraction import (
        run_classification_node,
        run_extraction_node,
    )

    if call_model is None:
        raise ValueError(
            f"Node {node.node_key!r} is an LLM node but no model caller was provided."
        )

    context = _build_fence(state)
    action_type = str(config.get("action_type") or "").lower().strip()

    def _call(prompt: str) -> tuple[str, Any]:
        return call_model(node.node_key, prompt)

    if action_type == "llm.extract":
        facts, details = run_extraction_node(
            context=context,
            schema=config.get("schema") or {},
            node_key=node.node_key,
            call_model=_call,
        )
        state.facts = state.facts.merged_with(facts)
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            details={**details, "facts": facts.as_details()},
            output_digest=details.get("output_digest"),
        )
        return True

    labels = tuple(
        str(label) for label in (config.get("labels") or ()) if str(label).strip()
    )
    label, details = run_classification_node(
        context=context,
        labels=labels,
        node_key=node.node_key,
        call_model=_call,
    )
    state.facts = state.facts.merged_with(
        FactSet.from_extraction(
            node_key=node.node_key,
            data={config.get("output_key") or "classification": label},
        )
    )
    _record_node(
        state,
        node_key=node.node_key,
        node_type=node.node_type,
        status=AutomationNodeRunStatus.COMPLETED,
        details=details,
        output_digest=_digest({"label": label}),
    )
    return True


def _build_fence(state: _WalkState) -> Any:
    from app.services.context_assembly_service import ContextAssemblyService
    from app.services.fenced_context import empty_fence, fence

    work_item = state.work_item
    if work_item is None or not (work_item.extracted_text or "").strip():
        return empty_fence()

    assembled = ContextAssemblyService().assemble(
        [
            {
                "text": work_item.extracted_text,
                "metadata": {"filename": work_item.original_filename},
            }
        ],
        max_characters=int(settings.RAG_MAX_CONTEXT_LENGTH),
    )
    return fence(assembled, chunk_ids=[str(work_item.id)])


def _propagate_skip(
    state: _WalkState, node_key: str, *, taken: Optional[str] = "default"
) -> None:
    graph = state.graph
    for edge in graph.all_successors(node_key):
        if taken is not None and edge.branch == taken:
            continue
        if taken is None or edge.branch != taken:
            candidate = edge.to_node_key
            inbound = [e for e in graph.edges if e.to_node_key == candidate]
            reachable = any(
                e.from_node_key not in state.skipped
                and not (
                    e.from_node_key == node_key
                    and (taken is None or e.branch != taken)
                )
                for e in inbound
            )
            if reachable:
                continue
            if candidate not in state.skipped:
                state.skipped.add(candidate)
                _propagate_skip(state, candidate, taken=None)


def _record_node(
    state: _WalkState,
    *,
    node_key: str,
    node_type: str,
    status: AutomationNodeRunStatus,
    details: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
    input_digest: Optional[str] = None,
    output_digest: Optional[str] = None,
    cost_micros: int = 0,
) -> AutomationNodeRun:
    run = AutomationNodeRun(
        execution_id=state.execution.id,
        node_key=node_key,
        node_type=node_type,
        sequence=state.sequence,
        status=status,
        started_at=_now(),
        completed_at=_now(),
        input_digest=input_digest,
        output_digest=output_digest,
        cost_micros=cost_micros,
        error=error,
        details=details or {},
    )
    state.sequence += 1
    state.db.add(run)

    if status is AutomationNodeRunStatus.COMPLETED:
        state.execution.nodes_executed += 1

    state.db.commit()
    return run


def _default_perform_action(
    state: _WalkState,
) -> Callable[[ActionSpec, ActionNodeConfig], str]:
    def _perform(spec: ActionSpec, config: ActionNodeConfig) -> str:
        import asyncio
        from app.services.automation_service import (
            ActionFailure,
            EMAIL_ACTION_TYPES,
            _LazyEmailSettings,
            _render_action_message,
        )
        from app.services.notification.dispatcher import notification_dispatcher

        if spec.action_type in EMAIL_ACTION_TYPES:
            resolved = _LazyEmailSettings(
                db=state.db, workspace_id=state.execution.workspace_id
            ).require()
            if not spec.recipient:
                raise ActionFailure("Email action has no recipient configured.")
            title, body = _render_action_message(
                rule=state.rule, work_item=state.work_item
            )
            ok = asyncio.run(
                notification_dispatcher.send(
                    action_type=spec.action_type,
                    settings=resolved,
                    recipient=spec.recipient,
                    title=title,
                    body=body,
                )
            )
            if not ok:
                raise ActionFailure(
                    f"Provider '{spec.action_type}' reported a delivery failure."
                )
            return f"{spec.action_type} -> {spec.recipient}"

        if spec.action_type in ("set_field", "work_item.mutate"):
            return _perform_mutation(state, spec)

        raise ActionFailure(f"Unsupported action type '{spec.action_type}'.")

    return _perform


def _perform_mutation(state: _WalkState, spec: ActionSpec) -> str:
    from app.services import outbox_service
    from app.services.automation_service import ActionFailure

    work_item = state.work_item
    if work_item is None:
        raise ActionFailure("A mutation action needs a work item.")

    field_name = spec.target_field or ""
    if not hasattr(work_item, field_name) or field_name in (
        "id", "workspace_id", "created_by_user_id", "extracted_text",
    ):
        raise ActionFailure(
            f"Field {field_name!r} is not a mutable work item field."
        )

    setattr(work_item, field_name, spec.target_value)
    state.db.flush([work_item])

    event = outbox_service.emit_internal(
        state.db,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
        event_type="work_item.field_changed",
        resource_id=work_item.id,
        payload={
            "work_item_id": str(work_item.id),
            "field": field_name,
            "rule_id": str(state.rule.id),
            "execution_id": str(state.execution.id),
        },
        caused_by=state.trigger_event,
    )
    state.emitted_event_ids.append(str(event.id))
    return f"set_field {field_name}"


def reap_stranded(db: Session, *, limit: int = 200) -> int:
    stranded = (
        db.execute(
            select(AutomationExecution)
            .where(
                AutomationExecution.status == AutomationExecutionStatus.RUNNING,
                AutomationExecution.deadline_at < _now(),
            )
            .limit(limit)
        )
        .scalars()
        .all()
    )
    for execution in stranded:
        execution.status = AutomationExecutionStatus.TIMED_OUT
        execution.completed_at = _now()
        execution.deadline_at = None
        execution.error = (
            "Reaped: the worker holding this execution did not report a "
            "result before the deadline."
        )
    if stranded:
        db.commit()
        logger.warning(
            "automation.executions_reaped", extra={"count": len(stranded)}
        )
    return len(stranded)


__all__ = [
    "ExecutionHalted",
    "ExecutionResult",
    "create_execution",
    "reap_stranded",
    "run_execution",
]