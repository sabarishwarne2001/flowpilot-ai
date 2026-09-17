"""ARCH-37 action `review.escalate` — put the document in the review queue.

Writes a DISAGREED `document_verifications` row, which is what the review
queue lists and what the automation handler treats as blocking: later
triggers on this document wait for the reviewer.

Every extracted field is attached, agreed, so the reviewer confirms the
document rather than one field. The row is marked
`escalation.review_all_fields`, which `document_verification_service.resolve`
honours. It deliberately does NOT use ARCH-35's `calibration.review_all_fields`:
the calibration harvester learns from rows carrying that key, and a rule's
escalation is not a calibration audit.

One open verification per document is a unique index, so a repeated escalation
returns the open row.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionOutcome,
    require_work_item,
)

ACTION_TYPE = "review.escalate"
MAX_FIELDS = 50


class ReviewEscalateConfig(ActionConfig):
    reason: str = Field(
        default="Sent to review by an automation rule.",
        min_length=1,
        max_length=240,
    )


def escalate(state: Any, *, reason: str, source: str) -> ActionOutcome:
    from sqlalchemy import select

    from app.models.verification import (
        BLOCKING_STATUSES,
        DocumentVerification,
        DocumentVerificationField,
        VerificationStatus,
    )
    from app.services.document_verification_service import EXCLUDED_FIELDS

    work_item = require_work_item(state, ACTION_TYPE)
    open_row = state.db.execute(
        select(DocumentVerification.id).where(
            DocumentVerification.work_item_id == work_item.id,
            DocumentVerification.status.in_(BLOCKING_STATUSES),
        ).limit(1)
    ).scalar_one_or_none()
    if open_row is not None:
        return ActionOutcome(
            summary="document already awaiting review",
            external_ref=str(open_row),
            details={"reused": True},
        )

    verification = DocumentVerification(
        work_item_id=work_item.id,
        workspace_id=state.execution.workspace_id,
        organization_id=state.execution.organization_id,
        status=VerificationStatus.DISAGREED,
        agent_count=2,
        agreement_score=Decimal("0"),
        confidence=None,
        cost_micros=0,
        auto_approved=False,
        details={
            "escalation": {
                "source": source,
                "reason": reason,
                "rule_id": str(state.rule.id),
                "execution_id": str(state.execution.id),
                "trigger_event_id": (
                    str(state.trigger_event.id) if state.trigger_event is not None else None
                ),
                # The fact contract carries keys and a digest, never values.
                "evidence": state.facts.as_details(),
                "review_all_fields": True,
            }
        },
    )
    state.db.add(verification)
    state.db.flush([verification])

    entities = dict(work_item.extracted_entities or {})
    written = 0
    for key in sorted(entities):
        if key in EXCLUDED_FIELDS or written >= MAX_FIELDS:
            continue
        value = entities[key]
        state.db.add(
            DocumentVerificationField(
                verification_id=verification.id,
                field_path=str(key)[:200],
                agreed=True,
                confidence=Decimal("1"),
                consensus_value=value,
                agent_values=[value],
                disagreement_kind=None,
            )
        )
        written += 1
    state.db.flush()
    return ActionOutcome(
        summary=f"sent to review ({written} fields)",
        external_ref=str(verification.id),
        details={"fields": written},
    )


def perform(state: Any, spec: Any) -> ActionOutcome:
    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, ReviewEscalateConfig)
    return escalate(state, reason=config.reason, source=ACTION_TYPE)


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send to review",
    description="Hold the document for a reviewer. Later rules on it wait for the decision.",
    category="Human review",
    config_model=ReviewEscalateConfig,
    selector="automation.flow.review_escalate",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
