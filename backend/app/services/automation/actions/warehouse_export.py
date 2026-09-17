"""ARCH-37 action `warehouse.export` — run an ARCH-26 export now.

Enqueues `analytics.export_sync`, the job the "Sync now" button enqueues, so
the push runs on the worker and never blocks the automation.

GUARDRAILS
  * The destination is an OWNER-registered `warehouse_destinations` row in
    the rule's organization (the destination API is OWNER-only), and it must
    be active when the rule runs.
  * The warehouse-sync add-on must be maintainable (active or in grace), the
    same policy the manual sync applies.
  * Authoring needs an organization OWNER or ADMIN: the datasets are
    organization-wide.
  * Debounced per destination. The idempotency key is the destination and a
    time bucket, so a burst of documents produces one export per window.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import Field, field_validator

from app.core.entitlements import WAREHOUSE_SYNC_ADDON
from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    SaveContext,
)

ACTION_TYPE = "warehouse.export"
JOB_TYPE = "analytics.export_sync"


class WarehouseExportConfig(ActionConfig):
    destination_id: uuid.UUID
    datasets: list[str] = Field(min_length=1, max_length=8)
    lookback_days: int = Field(default=1, ge=1, le=90)
    debounce_minutes: int = Field(default=60, ge=5, le=1440)

    @field_validator("datasets")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        from app.models.warehouse_sync import EXPORT_DATASET_VALUES

        unknown = sorted(set(value) - set(EXPORT_DATASET_VALUES))
        if unknown:
            raise ValueError(f"Unknown dataset(s): {unknown}.")
        if len(set(value)) != len(value):
            raise ValueError("datasets must not repeat.")
        return value


def _destination(db: Any, *, organization_id: uuid.UUID, destination_id: uuid.UUID) -> tuple[Any, str | None]:
    from app.services.analytics import sync_service

    try:
        destination = sync_service.get_destination(
            db, organization_id=organization_id, destination_id=destination_id
        )
    except sync_service.DestinationNotFoundError:
        return None, "Warehouse destination not found in this organization."
    if not destination.is_active:
        return None, "This warehouse destination is disabled."
    return destination, None


def _validate(ctx: SaveContext, config: ActionConfig) -> dict[str, str]:
    assert isinstance(config, WarehouseExportConfig)
    _, reason = _destination(
        ctx.db, organization_id=ctx.organization_id, destination_id=config.destination_id
    )
    return {"destination_id": reason} if reason else {}


def debounce_key(destination_id: uuid.UUID, minutes: int, *, now: float | None = None) -> str:
    bucket = int((now if now is not None else time.time()) // (minutes * 60))
    return f"automation:warehouse:{destination_id}:{minutes}:{bucket}"


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services import job_service
    from app.services.billing import entitlement_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WarehouseExportConfig)
    organization_id = state.execution.organization_id

    access = entitlement_service.addon_access(
        state.db, organization_id=organization_id, addon_key=WAREHOUSE_SYNC_ADDON
    )
    if not access.can_maintain:
        raise ActionFailure("The warehouse sync add-on is not active.", recoverable=False)
    destination, reason = _destination(
        state.db, organization_id=organization_id, destination_id=config.destination_id
    )
    if destination is None:
        raise ActionFailure(reason or "Destination unavailable.", recoverable=False)

    job = job_service.enqueue(
        state.db,
        job_type=JOB_TYPE,
        organization_id=organization_id,
        payload={
            "organization_id": str(organization_id),
            "destination_id": str(destination.id),
            "datasets": list(config.datasets),
            "lookback_days": int(config.lookback_days),
            "trigger": "MANUAL",
        },
        max_attempts=1,
        idempotency_key=debounce_key(destination.id, config.debounce_minutes),
    )
    return ActionOutcome(
        summary=f"export queued to {destination.label}",
        external_ref=str(job.id),
        details={"destination_id": str(destination.id), "datasets": list(config.datasets)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Export to warehouse",
    description="Push the selected datasets to a registered warehouse destination, at most once per window.",
    category="Integrations",
    config_model=WarehouseExportConfig,
    selector="automation.flow.warehouse_export",
    perform=perform,
    capability=None,
    addon=WAREHOUSE_SYNC_ADDON,
    minimum_role=ROLE_ORGANIZATION_ADMIN,
    validate_resources=_validate,
)
