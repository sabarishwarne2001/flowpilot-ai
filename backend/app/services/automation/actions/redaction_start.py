"""ARCH-37 action `redaction.start` — open an ARCH-32 redaction job.

GUARDRAILS
  * capability.redaction, checked when the rule runs, not only when saved.
  * PDF only; `redaction_service.start_job` refuses anything else.
  * The job is left in REVIEW for a human. This action never approves or
    applies a redaction: burning pixels out of a contract is not something a
    rule decides on its own.
  * One open job per document. A second trigger while a job is still in
    detection or review returns that job instead of starting another.
  * Refused when the rule was triggered by `redaction.completed`, which would
    otherwise start a new job every time the previous one was published.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.core.entitlements import REDACTION_CAPABILITY
from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    has_capability,
    require_work_item,
)

ACTION_TYPE = "redaction.start"
OPEN_STATUSES = ("DETECTING", "REVIEW", "APPLYING")


class RedactionStartConfig(ActionConfig):
    profile_key: str = Field(description="Redaction profile.")

    @field_validator("profile_key")
    @classmethod
    def _known(cls, value: str) -> str:
        from app.services.redaction.vocabulary import PROFILE_KEYS

        if value not in PROFILE_KEYS:
            raise ValueError(f"Unknown redaction profile {value!r}.")
        return value


def perform(state: Any, spec: Any) -> ActionOutcome:
    from sqlalchemy import select

    from app.models.redaction import RedactionJob
    from app.services.redaction import redaction_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, RedactionStartConfig)
    if getattr(state.trigger_event, "event_type", None) == "trigger.redaction.completed":
        raise ActionFailure(
            "A redaction cannot be started by a redaction finishing.", recoverable=False
        )
    if not has_capability(state, REDACTION_CAPABILITY):
        raise ActionFailure("This plan does not include redaction.", recoverable=False)
    work_item = require_work_item(state, ACTION_TYPE)
    author = state.rule.created_by_user_id
    if author is None:
        raise ActionFailure(
            "A redaction job needs an owner, and this rule has no author.", recoverable=False
        )

    existing = state.db.execute(
        select(RedactionJob.id).where(
            RedactionJob.work_item_id == work_item.id,
            RedactionJob.workspace_id == state.execution.workspace_id,
            RedactionJob.status.in_(OPEN_STATUSES),
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return ActionOutcome(
            summary="redaction already open for this document",
            external_ref=str(existing),
            details={"reused": True},
        )

    try:
        job = redaction_service.start_job(
            state.db,
            organization_id=state.execution.organization_id,
            workspace_id=state.execution.workspace_id,
            work_item_id=work_item.id,
            user_id=author,
            profile_key=config.profile_key,
        )
    except redaction_service.RedactionError as exc:
        raise ActionFailure(str(exc), recoverable=False) from exc
    return ActionOutcome(
        summary=f"redaction started ({config.profile_key}); awaiting review",
        external_ref=str(job.id),
        details={"profile": config.profile_key},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Redact PII",
    description="Detect personal data in the PDF and open a redaction for a reviewer to approve.",
    category="Document intelligence",
    config_model=RedactionStartConfig,
    selector="automation.flow.redaction_start",
    perform=perform,
    capability=REDACTION_CAPABILITY,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
