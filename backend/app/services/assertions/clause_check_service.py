"""HARDENING-T3:D21 — clause checks authored from the console.

ARCH-33 built the whole assertion engine (compiler, evaluation, triage into
the Review Hub) but the only way to put an assertion node into a rule was a
marketplace manifest install: the Flow Builder's flat rules compile on the
fly, while an assertion definition must attach to a PERSISTED node, and
`ClauseAssertionBlock.tsx` was mounted nowhere.

A clause check is therefore its own small rule with a persisted graph:

    trigger --default--> clause_check (assertion) --pass----> done (join)
                                                   --triage--> done (join)

* It fires on `work_item.enriched` through an AutomationRuleTrigger row,
  exactly like every catalog rule.
* `graph_service.save_graph` persists the three nodes and three edges and
  marks the rule `graph_version = DAG`, so the executor loads THIS graph and
  never flattens it. Edge uniqueness includes the branch label, so `pass` and
  `triage` may both end at the one join, which executes as a pass-through.
* The sentence and threshold are saved by the existing
  `PUT /assertions/rules/{rule_id}/nodes/clause_check` route (what
  ClauseAssertionBlock calls), so compilation and versioning stay in
  definition_service — the plan is never taken from the client.
* Triage creates the Review Hub item (assertions/triage.py); pass does nothing
  further. Flow Builder rules are untouched: they stay flat.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.automation import AutomationRule
from app.models.automation_graph import AutomationNode

CLAUSE_NODE_KEY = "clause_check"
TRIGGER_EVENT_TYPES = ("work_item.enriched",)
RULE_EVENT_LABEL = "document.ready"
_PRIORITY = 100


class ClauseCheckError(ValueError):
    """A request that cannot become a clause check (message is user-facing)."""


@dataclass(frozen=True)
class ClauseCheck:
    rule: AutomationRule
    node: AutomationNode
    definition: Any


def _graph_specs():
    from app.services.automation.graph_service import EdgeSpec, NodeSpec

    nodes = [
        NodeSpec(node_key="trigger", node_type="trigger"),
        NodeSpec(node_key=CLAUSE_NODE_KEY, node_type="assertion"),
        NodeSpec(node_key="done", node_type="join"),
    ]
    edges = [
        EdgeSpec("trigger", CLAUSE_NODE_KEY, "default"),
        EdgeSpec(CLAUSE_NODE_KEY, "done", "pass"),
        EdgeSpec(CLAUSE_NODE_KEY, "done", "triage"),
    ]
    return nodes, edges


def create(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    created_by_user_id: Optional[uuid.UUID],
    name: str,
    is_active: bool = False,
) -> ClauseCheck:
    """Create the rule, its trigger row and its persisted graph. Flushes."""
    from app.services.automation import graph_service, rule_triggers

    clean = (name or "").strip()
    if not clean:
        raise ClauseCheckError("Give the clause check a name.")
    rule = AutomationRule(
        name=clean[:120],
        priority=_PRIORITY,
        event=RULE_EVENT_LABEL,
        conditions=[],
        logic_operator="AND",
        actions=[],
        # Inactive until a definition is saved: an assertion node with no
        # definition has nothing to evaluate.
        is_active=bool(is_active),
        workspace_id=workspace_id,
        created_by_user_id=created_by_user_id,
    )
    db.add(rule)
    db.flush()
    rule_triggers.set_event_types(db, rule=rule, event_types=TRIGGER_EVENT_TYPES)
    nodes, edges = _graph_specs()
    graph_service.save_graph(db, rule=rule, nodes=nodes, edges=edges)
    node = _node(db, rule.id)
    return ClauseCheck(rule=rule, node=node, definition=None)


def _node(db: Session, rule_id: uuid.UUID) -> AutomationNode:
    return db.execute(
        select(AutomationNode).where(
            AutomationNode.rule_id == rule_id,
            AutomationNode.node_key == CLAUSE_NODE_KEY,
            AutomationNode.node_type == "assertion",
        )
    ).scalar_one()


def list_for_workspace(db: Session, *, workspace_id: uuid.UUID) -> list[ClauseCheck]:
    from app.services.assertions import definition_service

    rows = db.execute(
        select(AutomationRule, AutomationNode)
        .join(AutomationNode, AutomationNode.rule_id == AutomationRule.id)
        .where(
            AutomationRule.workspace_id == workspace_id,
            AutomationNode.node_key == CLAUSE_NODE_KEY,
            AutomationNode.node_type == "assertion",
        )
        .order_by(AutomationRule.created_at.desc())
    ).all()
    return [
        ClauseCheck(rule=rule, node=node, definition=definition_service.latest_for_node(db, node_id=node.id))
        for rule, node in rows
    ]


def get(db: Session, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> ClauseCheck:
    for check in list_for_workspace(db, workspace_id=workspace_id):
        if check.rule.id == rule_id:
            return check
    raise ClauseCheckError("That clause check does not exist in this workspace.")


def set_active(db: Session, *, workspace_id: uuid.UUID, rule_id: uuid.UUID, is_active: bool) -> ClauseCheck:
    check = get(db, workspace_id=workspace_id, rule_id=rule_id)
    if is_active and check.definition is None:
        raise ClauseCheckError(
            "Save the clause (sentence and threshold) before turning the check on: "
            "an empty check has nothing to evaluate."
        )
    check.rule.is_active = bool(is_active)
    db.flush()
    return check


def delete(db: Session, *, workspace_id: uuid.UUID, rule_id: uuid.UUID) -> None:
    check = get(db, workspace_id=workspace_id, rule_id=rule_id)
    db.delete(check.rule)
    db.flush()


__all__ = [
    "CLAUSE_NODE_KEY",
    "ClauseCheck",
    "ClauseCheckError",
    "create",
    "delete",
    "get",
    "list_for_workspace",
    "set_active",
]
