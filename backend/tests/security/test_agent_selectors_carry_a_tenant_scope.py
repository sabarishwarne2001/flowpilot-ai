"""F-022 — every agent action tool is bound to a workspace through its scope.

    pytest tests/security/test_agent_selectors_carry_a_tenant_scope.py -q

Gate 0V-G13 flagged `resolve_review_item` because it has no parameter called
`tenant`. Reading the code shows the tenancy is carried by `scope: AgentScope`
(organization id, workspace id, proposal id) and, more importantly, ENFORCED
where an action is applied: `actions._execute` loads the review item with
`workspace_id=proposal.workspace_id` and the case with
`Case.workspace_id == proposal.workspace_id`, so a proposal stored in workspace A
that names an item id from workspace B finds nothing and is marked SUPERSEDED.

These tests do not replace a full cross-workspace apply (which needs review
fixtures, Phase 3); they pin the two structural facts that make it safe.
"""

from __future__ import annotations

import inspect

import pytest

from app.services.process_intel.agent import actions
from app.services.process_intel.agent.contracts import AgentScope
from app.services.tools.agent_selectors import SELECTORS

pytestmark = pytest.mark.no_db


@pytest.mark.parametrize("tool", sorted(SELECTORS))
def test_every_selector_takes_a_typed_workspace_scope(tool: str) -> None:
    parameters = inspect.signature(SELECTORS[tool]).parameters
    assert "scope" in parameters, f"{tool} has no scope parameter"
    assert parameters["scope"].annotation in (AgentScope, "AgentScope"), parameters["scope"].annotation


def test_the_scope_names_an_organization_and_a_workspace() -> None:
    fields = {name for name in getattr(AgentScope, "__dataclass_fields__", {})}
    assert {"organization_id", "workspace_id"} <= fields, fields


def test_applying_an_action_loads_its_subject_inside_the_proposals_workspace() -> None:
    source = inspect.getsource(actions._execute)
    assert "workspace_id=proposal.workspace_id" in source
    assert "Case.workspace_id == proposal.workspace_id" in source


def test_a_proposal_is_locked_only_inside_its_own_workspace() -> None:
    source = inspect.getsource(actions.lock_proposal)
    assert "AgentProposal.workspace_id == workspace_id" in source
