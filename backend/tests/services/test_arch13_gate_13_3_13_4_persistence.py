"""ARCH-13 Gates 13.3 and 13.4 — persistence, the A6 budget, and the DAG."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.core.principal import PrincipalKind
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
)
from app.models.automation_graph import (
    GRAPH_VERSION_DAG,
    GRAPH_VERSION_FLAT,
    AutomationEdge,
    AutomationNode,
)
from app.models.usage_event import UsageEvent
from app.services.automation import budget, graph_service as gs

pytestmark = pytest.mark.usefixtures("test_database")


def _execution(db, *, tenant, rule, budget_micros: int = 1_000, spent: int = 0,
               outbox_event_id=None) -> AutomationExecution:
    execution = AutomationExecution(
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        rule_id=rule.id,
        correlation_id=uuid.uuid4(),
        depth=0,
        status=AutomationExecutionStatus.QUEUED,
        budget_cost_micros=budget_micros,
        spent_cost_micros=spent,
        outbox_event_id=outbox_event_id,
    )
    db.add(execution)
    db.flush()
    return execution


# =====================================================================
# Gate 13.3 — the CHECK constraint is the enforcement
# =====================================================================


def test_spend_within_budget_check_rejects_direct_sql_breach(
    db_session, tenant, rule_factory
) -> None:
    rule = rule_factory(name="budget-check")
    execution = _execution(db_session, tenant=tenant, rule=rule, budget_micros=500)
    db_session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(
            text(
                "UPDATE automation_executions SET spent_cost_micros = 501 "
                "WHERE id = :id"
            ),
            {"id": execution.id},
        )
        db_session.flush()
    assert "spend" in str(excinfo.value)
    db_session.rollback()


def test_spend_exactly_at_budget_is_allowed(db_session, tenant, rule_factory) -> None:
    rule = rule_factory(name="budget-boundary")
    execution = _execution(db_session, tenant=tenant, rule=rule, budget_micros=500)
    db_session.commit()

    db_session.execute(
        text(
            "UPDATE automation_executions SET spent_cost_micros = 500 "
            "WHERE id = :id"
        ),
        {"id": execution.id},
    )
    db_session.commit()
    db_session.refresh(execution)
    assert execution.spent_cost_micros == 500
    assert execution.remaining_budget_micros == 0


def test_running_requires_a_deadline(db_session, tenant, rule_factory) -> None:
    rule = rule_factory(name="deadline-check")
    execution = _execution(db_session, tenant=tenant, rule=rule)
    db_session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(
            text(
                "UPDATE automation_executions SET status = 'RUNNING' "
                "WHERE id = :id"
            ),
            {"id": execution.id},
        )
        db_session.flush()
    assert "deadl" in str(excinfo.value) or "deadline" in str(excinfo.value)
    db_session.rollback()


def test_replay_of_the_same_rule_and_event_collides(
    db_session, tenant, rule_factory
) -> None:
    from app.services import outbox_service

    rule = rule_factory(name="replay")
    event = outbox_service.emit_internal(
        db_session,
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        event_type="work_item.enriched",
    )
    db_session.commit()

    _execution(db_session, tenant=tenant, rule=rule, outbox_event_id=event.id)
    db_session.commit()

    with pytest.raises(IntegrityError) as excinfo:
        _execution(db_session, tenant=tenant, rule=rule, outbox_event_id=event.id)
        db_session.flush()
    assert "uq_automation_executions_rule_event" in str(excinfo.value)
    db_session.rollback()


def test_manual_executions_without_an_event_do_not_collide(
    db_session, tenant, rule_factory
) -> None:
    rule = rule_factory(name="manual")
    _execution(db_session, tenant=tenant, rule=rule, outbox_event_id=None)
    _execution(db_session, tenant=tenant, rule=rule, outbox_event_id=None)
    db_session.commit()

    rows = db_session.execute(
        select(AutomationExecution).where(AutomationExecution.rule_id == rule.id)
    ).scalars().all()
    assert len(rows) == 2


# =====================================================================
# Gate 13.3 — budget resolution and attribution
# =====================================================================


def test_rule_budget_overrides_the_platform_default(rule_factory) -> None:
    default_rule = rule_factory(name="default-budget")
    assert budget.resolve_budget_micros(default_rule) == (
        settings.AUTOMATION_DEFAULT_BUDGET_MICROS
    )

    override = rule_factory(name="override-budget", budget_cost_micros=123)
    assert budget.resolve_budget_micros(override) == 123


def test_zero_budget_is_honoured_not_treated_as_unset(rule_factory) -> None:
    rule = rule_factory(name="zero-budget", budget_cost_micros=0)
    assert budget.resolve_budget_micros(rule) == 0


def test_automation_runs_as_system_with_author_recorded_not_blamed(
    db_session, tenant, rule_factory
) -> None:
    rule = rule_factory(name="attribution", created_by_user_id=tenant.owner.user.id)
    execution = _execution(db_session, tenant=tenant, rule=rule)
    db_session.commit()

    principal = budget.system_principal(execution=execution, rule=rule)

    assert principal.kind is PrincipalKind.SYSTEM
    assert principal.actor_id is None
    assert principal.job_name == "jobs.automation.execute"
    assert principal.extra.get("created_by_user_id") == str(tenant.owner.user.id)
    assert principal.extra["rule_id"] == str(rule.id)
    assert principal.extra["execution_id"] == str(execution.id)


def test_reservation_scope_includes_the_node_key(
    db_session, tenant, rule_factory
) -> None:
    rule = rule_factory(name="scope")
    execution = _execution(db_session, tenant=tenant, rule=rule)
    db_session.commit()

    first = execution.scope_for("extract")
    second = execution.scope_for("classify")
    assert first != second
    assert first == f"llm:automation:{rule.id}:{execution.id}:extract"


def test_arbitrary_scopes_are_refused_by_the_metering_layer(db_session) -> None:
    from app.services import llm_metering

    with pytest.raises(llm_metering.LLMMeteringError, match="not a recognised"):
        llm_metering._assert_caller_scope("anything:at:all")

    llm_metering._assert_caller_scope("llm:automation:r:e:n")
    llm_metering._assert_caller_scope(f"llm:{uuid.uuid4()}:verify:0")


def test_usage_details_carry_rule_and_execution(
    db_session, tenant, rule_factory, monkeypatch
) -> None:
    from app.services import llm_metering

    rule = rule_factory(name="details")
    execution = _execution(db_session, tenant=tenant, rule=rule)
    db_session.commit()

    reservation = llm_metering.LLMReservation(
        organization_id=tenant.organization.id,
        workspace_id=tenant.workspace.id,
        scope=execution.scope_for("n1"),
        resource_type=llm_metering.WORK_ITEM_RESOURCE,
        resource_id=uuid.uuid4(),
        estimated_input_tokens=10,
        max_output_tokens=10,
        details_extra={
            "operation": "automation",
            "rule_id": str(rule.id),
            "execution_id": str(execution.id),
        },
    )
    assert reservation.details_extra["rule_id"] == str(rule.id)
    assert reservation.details_extra["execution_id"] == str(execution.id)


def test_budget_exhausted_carries_the_numbers(db_session, tenant, rule_factory) -> None:
    rule = rule_factory(name="exhausted")
    execution = _execution(
        db_session, tenant=tenant, rule=rule, budget_micros=100, spent=90
    )
    db_session.commit()

    exc = budget.BudgetExhausted(
        execution_id=execution.id,
        rule_id=rule.id,
        budget_micros=100,
        spent_micros=90,
        requested_micros=50,
        node_key="extract",
    )
    details = exc.as_details()
    assert details["remaining_cost_micros"] == 10
    assert details["requested_cost_micros"] == 50
    assert details["node_key"] == "extract"


# =====================================================================
# Gate 13.4 — the DAG
# =====================================================================


N = gs.NodeSpec
E = gs.EdgeSpec


def test_cycle_is_refused_at_save_and_names_the_cycle() -> None:
    with pytest.raises(gs.GraphValidationError) as excinfo:
        gs.compile_graph(
            [N("trigger", "trigger"), N("x", "action"), N("y", "action")],
            [E("trigger", "x"), E("x", "y"), E("y", "x")],
        )
    message = str(excinfo.value)
    assert "cycle" in message.lower()
    assert "x -> y -> x" in message
    assert set(excinfo.value.nodes) == {"x", "y"}


def test_topological_order_is_deterministic_for_a_diamond() -> None:
    nodes = [
        N("trigger", "trigger"),
        N("br", "branch"),
        N("left", "action"),
        N("right", "action"),
        N("join", "join"),
    ]
    edges = [
        E("trigger", "br"),
        E("br", "left", "true"),
        E("br", "right", "false"),
        E("left", "join"),
        E("right", "join"),
    ]

    first = gs.compile_graph(nodes, edges).order
    shuffled = gs.compile_graph(list(reversed(nodes)), list(reversed(edges))).order

    assert first == shuffled, "input order must not change the stored order"
    assert first[0] == "trigger"
    assert first[-1] == "join"
    assert first.index("left") < first.index("join")
    assert first.index("right") < first.index("join")


def test_unreachable_node_is_refused() -> None:
    with pytest.raises(gs.GraphValidationError, match="unreachable"):
        gs.compile_graph([N("trigger", "trigger"), N("orphan", "action")], [])


def test_branch_needs_both_outcomes() -> None:
    with pytest.raises(gs.GraphValidationError, match="no false edge"):
        gs.compile_graph(
            [N("trigger", "trigger"), N("br", "branch"), N("a", "action")],
            [E("trigger", "br"), E("br", "a", "true")],
        )


def test_exactly_one_trigger() -> None:
    with pytest.raises(gs.GraphValidationError, match="exactly one trigger"):
        gs.compile_graph([N("t1", "trigger"), N("t2", "trigger")], [])
    with pytest.raises(gs.GraphValidationError, match="exactly one trigger"):
        gs.compile_graph([N("a", "action")], [])


def test_nothing_may_point_at_the_trigger() -> None:
    with pytest.raises(gs.GraphValidationError, match="inbound edges"):
        gs.compile_graph(
            [N("trigger", "trigger"), N("a", "action")],
            [E("trigger", "a"), E("a", "trigger")],
        )


def test_max_nodes_ceiling_is_enforced_at_save(monkeypatch) -> None:
    monkeypatch.setattr(settings, "AUTOMATION_MAX_NODES", 3)
    nodes = [N("trigger", "trigger")] + [N(f"a{i}", "action") for i in range(4)]
    edges = [E("trigger", "a0")] + [E(f"a{i}", f"a{i+1}") for i in range(3)]
    with pytest.raises(gs.GraphValidationError, match="AUTOMATION_MAX_NODES"):
        gs.compile_graph(nodes, edges)


# =====================================================================
# Gate 13.4 — legacy compatibility
# =====================================================================


def test_legacy_flat_rule_flattens_to_a_linear_graph(rule_factory) -> None:
    rule = rule_factory(
        name="legacy",
        conditions=[{"field": "summary", "operator": "IS_NOT_EMPTY", "value": ""}],
        actions=[
            {"action_type": "email", "config": {"recipient": "a@b.com"}},
            {"action_type": "email", "config": {"recipient": "c@d.com"}},
        ],
    )
    compiled = gs.flatten_legacy_rule(rule)

    assert compiled.order == ("trigger", "conditions", "action_0", "action_1")
    assert compiled.trigger_key == "trigger"


def test_conditions_become_one_node_not_one_per_condition(rule_factory) -> None:
    rule = rule_factory(
        name="multi-condition",
        logic_operator="OR",
        conditions=[
            {"field": "a", "operator": "EQUALS", "value": "1"},
            {"field": "b", "operator": "EQUALS", "value": "2"},
            {"field": "c", "operator": "EQUALS", "value": "3"},
        ],
        actions=[{"action_type": "email", "config": {"recipient": "a@b.com"}}],
    )
    compiled = gs.flatten_legacy_rule(rule)

    condition_nodes = [n for n in compiled.nodes if n.node_type == "condition"]
    assert len(condition_nodes) == 1
    assert len(condition_nodes[0].config["conditions"]) == 3
    assert condition_nodes[0].config["logic_operator"] == "OR"


def test_conversion_is_lazy_not_eager(db_session, rule_factory) -> None:
    rule = rule_factory(
        name="lazy",
        actions=[{"action_type": "email", "config": {"recipient": "a@b.com"}}],
        conditions=[{"field": "summary", "operator": "EXISTS", "value": ""}],
    )
    db_session.commit()

    assert rule.graph_version == GRAPH_VERSION_FLAT
    assert db_session.execute(
        select(AutomationNode).where(AutomationNode.rule_id == rule.id)
    ).scalars().all() == []

    gs.load_graph(db_session, rule=rule)
    db_session.flush()
    assert rule.graph_version == GRAPH_VERSION_FLAT

    gs.convert_rule_to_graph(db_session, rule=rule)
    db_session.commit()
    assert rule.graph_version == GRAPH_VERSION_DAG
    persisted = db_session.execute(
        select(AutomationNode)
        .where(AutomationNode.rule_id == rule.id)
        .order_by(AutomationNode.topological_order)
    ).scalars().all()
    assert [n.node_key for n in persisted] == [
        "trigger", "conditions", "action_0"
    ]


def test_saved_graph_round_trips_with_the_same_order(
    db_session, rule_factory
) -> None:
    rule = rule_factory(name="round-trip")
    db_session.commit()

    nodes = [
        N("trigger", "trigger"),
        N("br", "branch", {"field": "total", "operator": "GREATER_THAN", "value": "100"}),
        N("big", "action", {"action_type": "email", "config": {"recipient": "a@b.com"}}),
        N("small", "action", {"action_type": "email", "config": {"recipient": "c@d.com"}}),
        N("done", "join"),
    ]
    edges = [
        E("trigger", "br"),
        E("br", "big", "true"),
        E("br", "small", "false"),
        E("big", "done"),
        E("small", "done"),
    ]
    saved = gs.save_graph(db_session, rule=rule, nodes=nodes, edges=edges)
    db_session.commit()

    loaded = gs.load_graph(db_session, rule=rule)
    assert loaded.order == saved.order
    assert loaded.trigger_key == "trigger"
    assert set(loaded.successors("br", branch="true")) == {"big"}
    assert set(loaded.successors("br", branch="false")) == {"small"}


def test_resaving_replaces_the_graph_wholesale(db_session, rule_factory) -> None:
    rule = rule_factory(name="resave")
    db_session.commit()

    gs.save_graph(
        db_session,
        rule=rule,
        nodes=[N("trigger", "trigger"), N("a", "action")],
        edges=[E("trigger", "a")],
    )
    db_session.commit()

    gs.save_graph(
        db_session,
        rule=rule,
        nodes=[N("trigger", "trigger"), N("b", "action")],
        edges=[E("trigger", "b")],
    )
    db_session.commit()

    keys = {
        n.node_key
        for n in db_session.execute(
            select(AutomationNode).where(AutomationNode.rule_id == rule.id)
        ).scalars().all()
    }
    assert keys == {"trigger", "b"}

    edges = db_session.execute(
        select(AutomationEdge).where(AutomationEdge.rule_id == rule.id)
    ).scalars().all()
    assert [(e.from_node_key, e.to_node_key) for e in edges] == [("trigger", "b")]


def test_edges_cannot_cross_rules(db_session, rule_factory) -> None:
    rule_a = rule_factory(name="cross-a")
    rule_b = rule_factory(name="cross-b")
    db_session.commit()

    gs.save_graph(
        db_session,
        rule=rule_a,
        nodes=[N("trigger", "trigger"), N("a", "action")],
        edges=[E("trigger", "a")],
    )
    gs.save_graph(
        db_session,
        rule=rule_b,
        nodes=[N("trigger", "trigger"), N("b", "action")],
        edges=[E("trigger", "b")],
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.add(
            AutomationEdge(
                rule_id=rule_a.id, from_node_key="trigger", to_node_key="b"
            )
        )
        db_session.flush()
    db_session.rollback()