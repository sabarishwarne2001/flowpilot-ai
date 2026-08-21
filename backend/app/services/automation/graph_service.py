"""ARCH-13 Step 13.4 — node graph persistence and save-time DAG validation."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.automation import AutomationRule
from app.models.automation_graph import (
    BRANCH_LABELS,
    GRAPH_VERSION_DAG,
    GRAPH_VERSION_FLAT,
    NODE_TYPES,
    AutomationEdge,
    AutomationNode,
)

logger = logging.getLogger("app.services.automation.graph_service")


class GraphValidationError(ValueError):
    """The graph is not a valid DAG, or violates a structural rule."""

    def __init__(self, message: str, *, nodes: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.nodes = list(nodes)


@dataclass(frozen=True)
class NodeSpec:
    node_key: str
    node_type: str
    config: dict[str, Any] = field(default_factory=dict)
    position: Optional[dict[str, Any]] = None


@dataclass(frozen=True)
class EdgeSpec:
    from_node_key: str
    to_node_key: str
    branch: str = "default"


@dataclass(frozen=True)
class CompiledGraph:
    """A validated graph in topological order, ready to walk."""

    nodes: tuple[NodeSpec, ...]
    edges: tuple[EdgeSpec, ...]
    order: tuple[str, ...]
    trigger_key: str

    def node(self, node_key: str) -> NodeSpec:
        for spec in self.nodes:
            if spec.node_key == node_key:
                return spec
        raise KeyError(node_key)

    def successors(self, node_key: str, *, branch: str = "default") -> tuple[str, ...]:
        return tuple(
            edge.to_node_key
            for edge in self.edges
            if edge.from_node_key == node_key and edge.branch == branch
        )

    def all_successors(self, node_key: str) -> tuple[EdgeSpec, ...]:
        return tuple(
            edge for edge in self.edges if edge.from_node_key == node_key
        )


def _validate_shape(nodes: Sequence[NodeSpec], edges: Sequence[EdgeSpec]) -> None:
    if not nodes:
        raise GraphValidationError("A graph must contain at least one node.")

    max_nodes = int(settings.AUTOMATION_MAX_NODES)
    if len(nodes) > max_nodes:
        raise GraphValidationError(
            f"Graph has {len(nodes)} nodes, over the AUTOMATION_MAX_NODES "
            f"ceiling of {max_nodes}."
        )

    keys = [n.node_key for n in nodes]
    duplicates = sorted({k for k in keys if keys.count(k) > 1})
    if duplicates:
        raise GraphValidationError(
            f"Duplicate node keys: {', '.join(duplicates)}.", nodes=duplicates
        )

    unknown_types = sorted(
        {n.node_key for n in nodes if n.node_type not in NODE_TYPES}
    )
    if unknown_types:
        raise GraphValidationError(
            f"Unknown node type on: {', '.join(unknown_types)}. "
            f"Known types: {', '.join(sorted(NODE_TYPES))}.",
            nodes=unknown_types,
        )

    triggers = [n.node_key for n in nodes if n.node_type == "trigger"]
    if len(triggers) != 1:
        raise GraphValidationError(
            f"A graph needs exactly one trigger node, found {len(triggers)}"
            + (f": {', '.join(triggers)}" if triggers else "")
            + ". The trigger is the entry point; without exactly one, "
            "'where does execution start' has no answer.",
            nodes=triggers,
        )

    known = set(keys)
    dangling = sorted(
        {
            key
            for edge in edges
            for key in (edge.from_node_key, edge.to_node_key)
            if key not in known
        }
    )
    if dangling:
        raise GraphValidationError(
            f"Edges reference unknown nodes: {', '.join(dangling)}.",
            nodes=dangling,
        )

    bad_branches = sorted(
        {edge.branch for edge in edges if edge.branch not in BRANCH_LABELS}
    )
    if bad_branches:
        raise GraphValidationError(
            f"Unknown branch labels: {', '.join(bad_branches)}. "
            f"Known: {', '.join(sorted(BRANCH_LABELS))}."
        )

    self_loops = sorted(
        {e.from_node_key for e in edges if e.from_node_key == e.to_node_key}
    )
    if self_loops:
        raise GraphValidationError(
            f"Self-loop on: {', '.join(self_loops)}.", nodes=self_loops
        )

    by_key = {n.node_key: n for n in nodes}

    for node in nodes:
        if node.node_type != "branch":
            continue
        labels = {e.branch for e in edges if e.from_node_key == node.node_key}
        missing = {"true", "false"} - labels
        if missing:
            raise GraphValidationError(
                f"Branch node '{node.node_key}' has no "
                f"{'/'.join(sorted(missing))} edge. Both outcomes must be "
                "wired; an unwired outcome silently ends the execution.",
                nodes=[node.node_key],
            )

    for edge in edges:
        source = by_key[edge.from_node_key]
        if edge.branch in ("true", "false") and source.node_type != "branch":
            raise GraphValidationError(
                f"Edge from '{edge.from_node_key}' (a {source.node_type} node) "
                f"is labelled '{edge.branch}', but only a branch node has "
                "true/false outcomes.",
                nodes=[edge.from_node_key],
            )
        if edge.branch == "default" and source.node_type == "branch":
            raise GraphValidationError(
                f"Branch node '{edge.from_node_key}' has a 'default' edge. "
                "A branch's out-edges must be labelled true or false.",
                nodes=[edge.from_node_key],
            )

    trigger_key = triggers[0]
    inbound = [e.from_node_key for e in edges if e.to_node_key == trigger_key]
    if inbound:
        raise GraphValidationError(
            f"Trigger node '{trigger_key}' has inbound edges from "
            f"{', '.join(sorted(set(inbound)))}. The trigger is the entry "
            "point and cannot be a target.",
            nodes=[trigger_key],
        )


def topological_order(
    nodes: Sequence[NodeSpec], edges: Sequence[EdgeSpec]
) -> tuple[str, ...]:
    keys = [n.node_key for n in nodes]
    indegree: dict[str, int] = {key: 0 for key in keys}
    adjacency: dict[str, list[str]] = {key: [] for key in keys}

    for edge in edges:
        adjacency[edge.from_node_key].append(edge.to_node_key)
        indegree[edge.to_node_key] += 1

    ready = sorted(key for key, degree in indegree.items() if degree == 0)
    ordered: list[str] = []

    while ready:
        current = ready.pop(0)
        ordered.append(current)
        newly_ready = []
        for successor in adjacency[current]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                newly_ready.append(successor)
        if newly_ready:
            ready = sorted(ready + newly_ready)

    if len(ordered) != len(keys):
        remaining = sorted(set(keys) - set(ordered))
        cycle = _find_cycle(remaining, edges)
        raise GraphValidationError(
            "The graph contains a cycle: "
            + " -> ".join(cycle + [cycle[0]])
            + ". Automation graphs must be acyclic; a loop here would run "
            "forever inside a single execution, which no depth bound catches "
            "because it never emits an event.",
            nodes=cycle,
        )

    return tuple(ordered)


def _find_cycle(
    candidates: Sequence[str], edges: Sequence[EdgeSpec]
) -> list[str]:
    adjacency: dict[str, list[str]] = {key: [] for key in candidates}
    pool = set(candidates)
    for edge in edges:
        if edge.from_node_key in pool and edge.to_node_key in pool:
            adjacency[edge.from_node_key].append(edge.to_node_key)

    for start in sorted(candidates):
        stack = [(start, [start])]
        seen: set[str] = set()
        while stack:
            node, path = stack.pop()
            for successor in sorted(adjacency.get(node, ())):
                if successor == start:
                    return path
                if successor not in seen:
                    seen.add(successor)
                    stack.append((successor, path + [successor]))
    return sorted(candidates)[:1] or ["?"]


def compile_graph(
    nodes: Sequence[NodeSpec], edges: Sequence[EdgeSpec]
) -> CompiledGraph:
    _validate_shape(nodes, edges)
    trigger = next(n.node_key for n in nodes if n.node_type == "trigger")

    orphans = unreachable_nodes(nodes, edges, trigger)
    if orphans:
        raise GraphValidationError(
            f"Nodes unreachable from the trigger: {', '.join(orphans)}. An "
            "unreachable node is a node the customer thinks will run.",
            nodes=orphans,
        )

    order = topological_order(nodes, edges)

    if order[0] != trigger:
        raise GraphValidationError(
            f"Topological order starts at '{order[0]}', not at trigger "
            f"'{trigger}'.",
            nodes=[order[0], trigger],
        )

    return CompiledGraph(
        nodes=tuple(nodes), edges=tuple(edges), order=order, trigger_key=trigger
    )


def unreachable_nodes(
    nodes: Sequence[NodeSpec], edges: Sequence[EdgeSpec], trigger_key: str
) -> list[str]:
    adjacency: dict[str, list[str]] = {n.node_key: [] for n in nodes}
    for edge in edges:
        adjacency[edge.from_node_key].append(edge.to_node_key)

    reached = {trigger_key}
    frontier = [trigger_key]
    while frontier:
        current = frontier.pop()
        for successor in adjacency.get(current, ()):
            if successor not in reached:
                reached.add(successor)
                frontier.append(successor)

    return sorted({n.node_key for n in nodes} - reached)


def save_graph(
    db: Session,
    *,
    rule: AutomationRule,
    nodes: Sequence[NodeSpec],
    edges: Sequence[EdgeSpec],
) -> CompiledGraph:
    compiled = compile_graph(nodes, edges)

    db.execute(delete(AutomationEdge).where(AutomationEdge.rule_id == rule.id))
    db.execute(delete(AutomationNode).where(AutomationNode.rule_id == rule.id))
    db.flush()

    position_of = {n.node_key: n for n in nodes}
    for index, node_key in enumerate(compiled.order):
        spec = position_of[node_key]
        db.add(
            AutomationNode(
                rule_id=rule.id,
                node_key=spec.node_key,
                node_type=spec.node_type,
                config=dict(spec.config or {}),
                position=spec.position,
                topological_order=index,
            )
        )
    db.flush()

    for edge in edges:
        db.add(
            AutomationEdge(
                rule_id=rule.id,
                from_node_key=edge.from_node_key,
                to_node_key=edge.to_node_key,
                branch=edge.branch,
            )
        )

    rule.graph_version = GRAPH_VERSION_DAG
    db.flush()

    logger.info(
        "automation.graph_saved",
        extra={
            "rule_id": str(rule.id),
            "nodes": len(nodes),
            "edges": len(edges),
            "order": list(compiled.order),
        },
    )
    return compiled


def load_graph(db: Session, *, rule: AutomationRule) -> CompiledGraph:
    if int(getattr(rule, "graph_version", GRAPH_VERSION_FLAT)) == GRAPH_VERSION_FLAT:
        return flatten_legacy_rule(rule)

    rows = (
        db.execute(
            select(AutomationNode)
            .where(AutomationNode.rule_id == rule.id)
            .order_by(AutomationNode.topological_order.asc())
        )
        .scalars()
        .all()
    )
    if not rows:
        logger.warning(
            "automation.graph_version_without_nodes",
            extra={"rule_id": str(rule.id)},
        )
        return flatten_legacy_rule(rule)

    edge_rows = (
        db.execute(
            select(AutomationEdge)
            .where(AutomationEdge.rule_id == rule.id)
            .order_by(AutomationEdge.from_node_key.asc(), AutomationEdge.to_node_key.asc())
        )
        .scalars()
        .all()
    )

    nodes = tuple(
        NodeSpec(
            node_key=row.node_key,
            node_type=row.node_type,
            config=dict(row.config or {}),
            position=row.position,
        )
        for row in rows
    )
    edges = tuple(
        EdgeSpec(
            from_node_key=row.from_node_key,
            to_node_key=row.to_node_key,
            branch=row.branch,
        )
        for row in edge_rows
    )

    return CompiledGraph(
        nodes=nodes,
        edges=edges,
        order=tuple(row.node_key for row in rows),
        trigger_key=next(
            (row.node_key for row in rows if row.node_type == "trigger"),
            rows[0].node_key,
        ),
    )


def flatten_legacy_rule(rule: AutomationRule) -> CompiledGraph:
    nodes: list[NodeSpec] = [NodeSpec(node_key="trigger", node_type="trigger")]
    edges: list[EdgeSpec] = []

    conditions = list(getattr(rule, "conditions", []) or [])
    actions = list(getattr(rule, "actions", []) or [])

    previous = "trigger"
    if conditions:
        nodes.append(
            NodeSpec(
                node_key="conditions",
                node_type="condition",
                config={
                    "conditions": conditions,
                    "logic_operator": getattr(rule, "logic_operator", "AND"),
                },
            )
        )
        edges.append(EdgeSpec(previous, "conditions"))
        previous = "conditions"

    for index, action in enumerate(actions):
        key = f"action_{index}"
        nodes.append(
            NodeSpec(
                node_key=key,
                node_type="action",
                config=dict(action) if isinstance(action, dict) else {"action": action},
            )
        )
        edges.append(EdgeSpec(previous, key))
        previous = key

    order = tuple(n.node_key for n in nodes)
    return CompiledGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        order=order,
        trigger_key="trigger",
    )


def convert_rule_to_graph(db: Session, *, rule: AutomationRule) -> CompiledGraph:
    if int(getattr(rule, "graph_version", GRAPH_VERSION_FLAT)) == GRAPH_VERSION_DAG:
        return load_graph(db, rule=rule)

    compiled = flatten_legacy_rule(rule)
    saved = save_graph(
        db, rule=rule, nodes=list(compiled.nodes), edges=list(compiled.edges)
    )
    logger.info(
        "automation.rule_converted_to_graph",
        extra={"rule_id": str(rule.id), "nodes": len(compiled.nodes)},
    )
    return saved


__all__ = [
    "CompiledGraph",
    "EdgeSpec",
    "GraphValidationError",
    "NodeSpec",
    "compile_graph",
    "convert_rule_to_graph",
    "flatten_legacy_rule",
    "load_graph",
    "save_graph",
    "topological_order",
    "unreachable_nodes",
]