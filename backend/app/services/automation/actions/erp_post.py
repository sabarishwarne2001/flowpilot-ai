"""ARCH47-S1:action — `erp.post`: post the approved outcome a trigger names to an ERP target, exactly once.

On `procurement.approved` (a three-way match was approved: its case_id) and
`case.completed` (an ARCH-43 case completed: its case_id), plan one posting per
configured object kind on the configured target. Planning is the ledger's
idempotent insert: a rule that fires twice, two rules posting the same object
to the same target, or a person pressing "Post" as well all end with ONE
posting. Delivery happens on the worker (erp.deliver_posting), never inside the
automation run.

GUARDRAILS
  * The target is chosen by the author (config), never by the document; it must
    be an ACTIVE target of the rule's workspace when the rule is saved and runs.
  * Only the two triggers above carry an approved outcome; the action is
    refused on any other trigger when the rule is saved.
  * Needs capability.erp_posting; authoring needs a workspace admin.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import Field, field_validator

from app.core.entitlements import ERP_POSTING_CAPABILITY
from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    SaveContext,
    has_capability,
)

ACTION_TYPE = "erp.post"


class ErpPostConfig(ActionConfig):
    target_id: uuid.UUID
    object_kinds: list[str] = Field(min_length=1, max_length=5)

    @field_validator("object_kinds")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        from app.services.erp import vocabulary as v

        unknown = sorted(set(value) - set(v.OBJECT_KINDS))
        if unknown:
            raise ValueError(f"Unknown object kind(s): {unknown}.")
        if len(set(value)) != len(value):
            raise ValueError("object_kinds must not repeat.")
        return value


def _target(db: Any, *, workspace_id: uuid.UUID, target_id: uuid.UUID) -> tuple[Any, str | None]:
    from sqlalchemy import select

    from app.models.erp import ErpTarget
    from app.services.erp import vocabulary as v

    row = db.execute(select(ErpTarget).where(ErpTarget.id == target_id, ErpTarget.workspace_id == workspace_id)
                     ).scalar_one_or_none()
    if row is None:
        return None, "ERP target not found in this workspace."
    if row.status != v.TARGET_ACTIVE:
        return None, "This ERP target is disabled."
    return row, None


def _validate(ctx: SaveContext, config: ActionConfig) -> dict[str, str]:
    from app.services.erp import presets as PR
    from app.services.erp import vocabulary as v

    assert isinstance(config, ErpPostConfig)
    problems: dict[str, str] = {}
    unsupported = [k for k in ctx.trigger_keys if k not in v.ACTION_TRIGGER_SOURCES]
    if unsupported:
        problems["target_id"] = (f"erp.post runs on {', '.join(v.ACTION_TRIGGER_SOURCES)} (an approved outcome); "
                                 f"not on {', '.join(unsupported)}.")
    target, reason = _target(ctx.db, workspace_id=ctx.workspace_id, target_id=config.target_id)
    if target is None:
        problems["target_id"] = reason or "Target unavailable."
    else:
        missing = [k for k in config.object_kinds if k not in PR.supported_objects(target.format, target.preset)]
        if missing:
            problems["object_kinds"] = f"{target.name} does not take {', '.join(missing)}."
    return problems


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services.automation.triggers import TRIGGER_BY_EVENT
    from app.services.erp import service
    from app.services.erp import vocabulary as v

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, ErpPostConfig)
    if not has_capability(state, ERP_POSTING_CAPABILITY):
        raise ActionFailure("ERP posting is not included in this plan.", recoverable=False)
    event = getattr(state, "trigger_event", None)
    trigger = TRIGGER_BY_EVENT.get(getattr(event, "event_type", "") or "")
    source = v.ACTION_TRIGGER_SOURCES.get(trigger.key if trigger else "")
    if source is None:
        raise ActionFailure("erp.post needs a trigger that names an approved outcome.", recoverable=False)
    key, source_kind = source
    raw = (getattr(event, "payload", None) or {}).get(key)
    try:
        source_id = uuid.UUID(str(raw))
    except (TypeError, ValueError) as exc:
        raise ActionFailure(f"The trigger carries no {key}.", recoverable=False) from exc
    workspace_id = state.execution.workspace_id
    target, reason = _target(state.db, workspace_id=workspace_id, target_id=config.target_id)
    if target is None:
        raise ActionFailure(reason or "Target unavailable.", recoverable=False)
    planned, existing, failed, ids = 0, 0, [], []
    for kind in config.object_kinds:
        try:
            with state.db.begin_nested():
                posting, created = service.plan(state.db, target=target, source_kind=source_kind, source_id=source_id,
                                                object_kind=kind, origin=v.ORIGIN_FLOW, actor_user_id=None)
            ids.append(str(posting.id))
            planned += int(created)
            existing += int(not created)
        except service.ErpError as exc:
            failed.append(f"{kind}: {exc}")
    if failed and not ids:
        raise ActionFailure("; ".join(failed)[:500], recoverable=False)
    summary = f"{planned} posting(s) planned, {existing} already in the ledger" + (f", {len(failed)} refused" if failed else "")
    return ActionOutcome(summary=summary, external_ref=ids[0] if ids else None,
                         details={"target_id": str(target.id), "postings": ids, "refused": failed})


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Post to ERP",
    description="Post the approved outcome (a reconciled invoice, a completed case) to an ERP target, exactly once.",
    category="Integrations",
    config_model=ErpPostConfig,
    selector="automation.flow.erp_post",
    perform=perform,
    capability=ERP_POSTING_CAPABILITY,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    validate_resources=_validate,
)
