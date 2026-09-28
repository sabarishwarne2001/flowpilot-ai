"""ARCH49-S1:agent-selectors — the exception agent's ACTION tools, on the far side of the R33 boundary.

Four typed tool selectors, registered through `register_tool_selector` (the one
ARCH-12 built for exactly this): at import time it refuses any selector whose
parameters could carry retrieved document content -- `FencedContext`, a bare
dict or Mapping, or an unannotated parameter. Every parameter here is an id, an
integer, a `Literal` of a closed vocabulary, or a typed reference
(`AgentScope`, `FieldChoice`). A field's VALUE is never a parameter: a
`FieldChoice` names the verification field row and where its value comes from,
and the value is read from that row when the action is applied.

This file imports neither the fence nor a retrieval service
(tests/services/test_arch12_budget_and_isolation.py walks this package).
"""

from __future__ import annotations

import logging
import uuid
from typing import Literal, Optional

from app.services.automation.contracts import register_tool_selector
from app.services.process_intel.agent import vocabulary as av
from app.services.process_intel.agent.contracts import ActionContractError, AgentAction, AgentScope, FieldChoice

logger = logging.getLogger("app.services.tools.agent_selectors")

ReviewKind = Literal["EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION", "OBLIGATION",
                     "POSTING"]
Verdict = Literal["PASS", "FAIL", "UNDETERMINED", "CONFIRM", "DISMISS", "MERGE", "SEPARATE", "APPROVE", "ACCEPT",
                  "RETRY"]
NoteTemplate = Literal["anomaly.dismiss.precedent", "anomaly.dismiss.calibrated", "anomaly.confirm.layer",
                       "anomaly.confirm.precedent", "anomaly.confirm.calibrated", "posting.retry.transient",
                       "posting.retry.remapped"]


def _kind_for(kind: str, verdict: Optional[str], tool: str) -> av.KindSpec:
    for spec in av.KINDS:
        if spec.tool != tool or spec.subject_kind not in (kind, "*"):
            continue
        if spec.verdict is not None and spec.verdict != verdict:
            continue
        if spec.verdict is None and spec.verdicts and verdict not in spec.verdicts:
            continue
        return spec
    raise ActionContractError(f"no proposal kind resolves {kind} with {verdict!r} through {tool}")


def _log(tool: str, scope: AgentScope, subject_id: uuid.UUID) -> None:
    logger.debug("tools.agent_action_selected", extra={"selector": tool, "workspace_id": str(scope.workspace_id),
                                                       "proposal_id": str(scope.proposal_id),
                                                       "subject_id": str(subject_id)})


@register_tool_selector(av.TOOL_RESOLVE)
def resolve_review_item(
    *,
    scope: AgentScope,
    kind: ReviewKind,
    item_id: uuid.UUID,
    expected_version: int,
    verdict: Optional[Verdict] = None,
    choices: tuple[FieldChoice, ...] = (),
    note_template: Optional[NoteTemplate] = None,
    note_numbers: tuple[int, ...] = (),
) -> AgentAction:
    """Decide a review-hub item through its owning service (resolution.resolve_item)."""
    spec = _kind_for(kind, verdict, av.TOOL_RESOLVE)
    if kind == "EXTRACTION" and not choices:
        raise ActionContractError("an extraction decision names every field it confirms")
    if kind != "EXTRACTION" and choices:
        raise ActionContractError("only an extraction decision carries field choices")
    if kind == "ANOMALY" and verdict == "DISMISS" and note_template is None:
        raise ActionContractError("dismissing a finding needs its reason (a note template)")
    action = AgentAction(tool=av.TOOL_RESOLVE, subject_kind=kind, subject_id=item_id, expected_version=expected_version,
                         verdict=verdict if spec.key != "extraction.approve_consensus" else None, choices=tuple(choices),
                         note_template=note_template, note_numbers=tuple(note_numbers))
    _log(av.TOOL_RESOLVE, scope, item_id)
    return action


@register_tool_selector(av.TOOL_ASSIGN)
def assign_review_item(
    *,
    scope: AgentScope,
    kind: ReviewKind,
    item_id: uuid.UUID,
    expected_version: int,
    assignee_user_id: uuid.UUID,
) -> AgentAction:
    """Give an item to a workspace member (resolution.assign; the membership FK decides who may hold it)."""
    action = AgentAction(tool=av.TOOL_ASSIGN, subject_kind=kind, subject_id=item_id, expected_version=expected_version,
                         assignee_user_id=assignee_user_id)
    _log(av.TOOL_ASSIGN, scope, item_id)
    return action


@register_tool_selector(av.TOOL_REEVALUATE_CASE)
def reevaluate_case(*, scope: AgentScope, case_id: uuid.UUID, expected_revision: int) -> AgentAction:
    """Re-run an ARCH-43 case's evaluation (deterministic and idempotent: completeness and every rule)."""
    action = AgentAction(tool=av.TOOL_REEVALUATE_CASE, subject_kind="CASE", subject_id=case_id,
                         expected_version=expected_revision)
    _log(av.TOOL_REEVALUATE_CASE, scope, case_id)
    return action


@register_tool_selector(av.TOOL_REQUEST_DOCUMENT)
def request_case_document(*, scope: AgentScope, case_id: uuid.UUID, expected_revision: int,
                          document_type: str) -> AgentAction:
    """Open a single-use ARCH-43 upload request for a slot of the case's own template.

    `document_type` is a slot key the template's author configured; it is checked against the
    template when the action is applied, so a value that is not one of its slots is refused."""
    action = AgentAction(tool=av.TOOL_REQUEST_DOCUMENT, subject_kind="CASE", subject_id=case_id,
                         expected_version=expected_revision, document_type=document_type)
    _log(av.TOOL_REQUEST_DOCUMENT, scope, case_id)
    return action


#: The selector per action tool (actions.py dispatches through this, never by string lookup elsewhere).
SELECTORS = {
    av.TOOL_RESOLVE: resolve_review_item,
    av.TOOL_ASSIGN: assign_review_item,
    av.TOOL_REEVALUATE_CASE: reevaluate_case,
    av.TOOL_REQUEST_DOCUMENT: request_case_document,
}

__all__ = ["NoteTemplate", "ReviewKind", "SELECTORS", "Verdict", "assign_review_item", "reevaluate_case",
           "request_case_document", "resolve_review_item"]
