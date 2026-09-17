"""ARCH-37 action `autonomy.decide` — ask ARCH-35 whether to proceed.

Calls `calibration.apply.decide` for `verification.document`, which honours
the tenant's calibrated model, its suspension and its audit sampling. The
action never grants autonomy a tenant has not bought: without
capability.calibrated_autonomy `decide` returns None and the action fails.

THE BRANCH ON OUTCOME
=====================

Allowed: the actions after this one run.
Held (below threshold, suspended, audit-sampled, or no score): every action
after this one is skipped, and with `on_hold = ESCALATE` the document goes to
the review queue first. That is what "only push to the ERP when the platform
is confident" means in a rule.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from app.core.entitlements import CALIBRATED_AUTONOMY_CAPABILITY
from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    has_capability,
    require_work_item,
)

ACTION_TYPE = "autonomy.decide"
DECISION_TYPE = "verification.document"


class AutonomyDecideConfig(ActionConfig):
    score_source: Literal[
        "VERIFICATION_CONFIDENCE", "CLASSIFICATION_CONFIDENCE", "EVENT_SCORE"
    ] = "VERIFICATION_CONFIDENCE"
    on_hold: Literal["ESCALATE", "STOP"] = "ESCALATE"
    hold_reason: str = Field(
        default="Held by calibrated autonomy.", min_length=1, max_length=240
    )


def _score(state: Any, source: str) -> Optional[Any]:
    from sqlalchemy import select

    work_item = state.work_item
    if source == "EVENT_SCORE":
        return (getattr(state.trigger_event, "payload", None) or {}).get("score")
    if source == "CLASSIFICATION_CONFIDENCE":
        details = (work_item.extracted_entities or {}).get("classification_details")
        return details.get("confidence") if isinstance(details, dict) else None

    from app.models.verification import DocumentVerification

    return state.db.execute(
        select(DocumentVerification.confidence)
        .where(DocumentVerification.work_item_id == work_item.id)
        .order_by(DocumentVerification.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services.calibration import apply as calibrated_autonomy
    from app.services.automation.actions.review_escalate import escalate

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, AutonomyDecideConfig)
    if not has_capability(state, CALIBRATED_AUTONOMY_CAPABILITY):
        raise ActionFailure("This plan does not include calibrated autonomy.", recoverable=False)
    work_item = require_work_item(state, ACTION_TYPE)

    score = _score(state, config.score_source)
    decision = None
    if score is not None:
        decision = calibrated_autonomy.decide(
            state.db,
            organization_id=state.execution.organization_id,
            decision_type=DECISION_TYPE,
            raw_score=score,
            sample_key=f"{state.execution.id}:{work_item.id}",
            check_capability=False,
        )
    allowed = bool(decision is not None and decision.auto_allowed)
    details: dict[str, Any] = {
        "score_source": config.score_source,
        "has_score": score is not None,
        "decision": decision.as_details() if decision is not None else None,
    }
    if allowed:
        return ActionOutcome(
            summary="calibrated autonomy allowed the document",
            external_ref=(decision.model_id if decision and decision.model_id else None),
            details=details,
        )

    reason = config.hold_reason
    if decision is not None and decision.reason:
        reason = f"{reason} ({decision.reason})"[:240]
    elif score is None:
        reason = f"{reason} (no score available)"[:240]
    ref = decision.model_id if decision and decision.model_id else None
    if config.on_hold == "ESCALATE":
        escalated = escalate(state, reason=reason, source=ACTION_TYPE)
        ref = escalated.external_ref
        details["escalated"] = True
    return ActionOutcome(
        summary="held by calibrated autonomy; later actions skipped",
        external_ref=ref,
        continue_downstream=False,
        details=details,
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Decide with calibrated autonomy",
    description=(
        "Continue only when the calibrated model allows the document; "
        "otherwise stop, and optionally send it to review."
    ),
    category="Human review",
    config_model=AutonomyDecideConfig,
    selector="automation.flow.autonomy_decide",
    perform=perform,
    capability=CALIBRATED_AUTONOMY_CAPABILITY,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
