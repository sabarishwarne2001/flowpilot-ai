"""ARCH-26 §3, §4 — sync orchestration, credential custody and failure alerting.

WHERE THE PLAINTEXT CREDENTIAL EXISTS, AND FOR HOW LONG
=======================================================

Exactly two places, both of them narrow:

  1. Inside `create_destination` / `rotate_credential`, between the Pydantic
     model and `encrypt_secret`. It is never assigned to the ORM object.
  2. Inside `_decrypted_credential`, for the duration of one connector call.

There is no accessor that returns a plaintext credential to a caller outside
this module, and no response schema that could carry one if there were. That
is invariant I2, and `verify_arch26.py` G7 asserts the second half of it by
walking the schema module's AST.

`_split_credential` is where the shape of the invariant lives: it partitions a
validated credential model into the non-secret half, which is stored as JSONB
and returned by the API, and the secret half, which is encrypted. The
partition is driven by `_SECRET_FIELD_NAMES` in the schema module rather than
by two hand-maintained lists, so a new secret field added to a credential
model is secret by default. The failure mode of the alternative — a new field
that nobody remembered to add to the secret list — is that it lands in
`config`, which is returned by `GET /warehouse/destinations`.

WHY A CIRCUIT AND NOT A RETRY BUDGET
====================================

Hardening invariant 5: a failed sync alerts, and never silently retries
forever. A retry budget per run does not deliver that — a daily schedule
against a warehouse whose credential was revoked in January produces one
failure a day until somebody looks at a dashboard in April.

`consecutive_failure_count` accumulates across runs. At
`CIRCUIT_FAILURE_THRESHOLD` the schedule stops being dispatchable,
`circuit_opened_at` is set, and an operator-visible error is logged with the
tenant and destination on it. Closing the circuit is an explicit act
(`reset_circuit`), deliberately separate from re-enabling the schedule,
because "run this again" and "I fixed the thing that was breaking it" are
different claims and only the second should clear a failure count that was
about to alert somebody.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.encryption import (
    DecryptionError,
    decrypt_secret,
    encrypt_secret,
    secret_fingerprint,
)
from app.core.storage import get_storage_driver
from app.core.storage.keys import StorageNamespace, tenant_key
from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
from app.models.warehouse_sync import (
    CIRCUIT_FAILURE_THRESHOLD,
    EXPORT_DATASET_VALUES,
    ExportSchedule,
    ExportSyncRun,
    WarehouseDestination,
)
from app.services import audit_service
from app.services.analytics import export_engine
from app.services.analytics.connectors import get_connector
from app.services.analytics.connectors.base import (
    BundlePart,
    ConnectorError,
    PushOutcome,
    scrub,
)
from app.schemas.warehouse_sync import _SECRET_FIELD_NAMES

logger = logging.getLogger("app.services.analytics.sync_service")

JOB_TYPE_EXPORT_SYNC = "analytics.export_sync"
JOB_TYPE_WAREHOUSE_PUSH = "analytics.warehouse_push"

#: How many runs the console shows by default. Bounded here rather than in the
#: endpoint so the worker's own queries share the ceiling.
DEFAULT_RUN_HISTORY_LIMIT: int = 50
MAX_RUN_HISTORY_LIMIT: int = 200


class SyncServiceError(RuntimeError):
    """A destination, schedule or run could not be operated on."""


class DestinationNotFoundError(SyncServiceError):
    pass


class ScheduleNotFoundError(SyncServiceError):
    pass


class CredentialUnavailableError(SyncServiceError):
    """The stored ciphertext does not decrypt under any configured key."""


@dataclass(frozen=True)
class RunResult:
    run_id: uuid.UUID
    status: str
    row_count: Optional[int]
    byte_count: Optional[int]
    part_count: Optional[int]
    bundle_digest: Optional[str]
    detail: Optional[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Credential custody
# ---------------------------------------------------------------------------


def _split_credential(credential: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Partition a validated credential model into (config, secret).

    Driven by `_SECRET_FIELD_NAMES` rather than by per-kind lists, so a field
    added to a credential model with a secret-shaped name is secret without
    anyone remembering to classify it. A field with an unrecognised name lands
    in `config` — which is why the names in `_SECRET_FIELD_NAMES` are the
    literal field names and not a pattern.
    """
    payload = credential.model_dump(mode="json")
    secret: dict[str, Any] = {}
    config: dict[str, Any] = {}
    for key, value in payload.items():
        if key in _SECRET_FIELD_NAMES:
            if value is not None:
                secret[key] = value
        else:
            config[key] = value
    if not secret:
        raise SyncServiceError(
            "Refusing to store a destination with no secret material. Every "
            "supported warehouse authenticates with something."
        )
    return config, secret


def _serialise_secret(secret: Mapping[str, Any]) -> str:
    import json

    return json.dumps(secret, sort_keys=True, separators=(",", ":"))


def _decrypted_credential(
    destination: WarehouseDestination,
) -> dict[str, Any]:
    """Plaintext credential, for the duration of one connector call.

    Never returned to an API layer. Raises rather than returning an empty dict
    on failure: a connector handed `{}` produces a confusing auth error
    instead of the accurate "we cannot read this credential any more", which
    is what a key rotation gone wrong actually looks like.
    """
    import json

    try:
        raw = decrypt_secret(destination.encrypted_credential)
    except DecryptionError as exc:
        raise CredentialUnavailableError(
            f"Destination {destination.id} cannot be decrypted under any "
            "configured key. It must be re-entered."
        ) from exc
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise CredentialUnavailableError(
            f"Destination {destination.id} decrypted to a non-JSON payload."
        ) from exc
    if not isinstance(parsed, dict):
        raise CredentialUnavailableError(
            f"Destination {destination.id} decrypted to a non-object payload."
        )
    return parsed


# ---------------------------------------------------------------------------
# Destinations
# ---------------------------------------------------------------------------


def list_destinations(
    db: Session, *, organization_id: uuid.UUID
) -> list[WarehouseDestination]:
    stmt = (
        select(WarehouseDestination)
        .where(WarehouseDestination.organization_id == organization_id)
        .order_by(WarehouseDestination.label.asc())
    )
    return list(db.execute(stmt).scalars())


def get_destination(
    db: Session, *, organization_id: uuid.UUID, destination_id: uuid.UUID
) -> WarehouseDestination:
    """Fetch one destination inside the tenant.

    The organization predicate is part of the query and not a check applied to
    the result. A `get by id` that filters afterwards is one refactor away
    from a cross-tenant read, and this is a row holding a credential.
    """
    stmt = (
        select(WarehouseDestination)
        .where(WarehouseDestination.id == destination_id)
        .where(WarehouseDestination.organization_id == organization_id)
    )
    found = db.execute(stmt).scalar_one_or_none()
    if found is None:
        raise DestinationNotFoundError(str(destination_id))
    return found


def create_destination(
    db: Session,
    *,
    organization_id: uuid.UUID,
    label: str,
    credential: Any,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> WarehouseDestination:
    config, secret = _split_credential(credential)
    plaintext = _serialise_secret(secret)

    destination = WarehouseDestination(
        organization_id=organization_id,
        label=label,
        kind=str(credential.kind),
        status="ACTIVE",
        config=config,
        encrypted_credential=encrypt_secret(plaintext),
        credential_fingerprint=secret_fingerprint(plaintext),
        created_by_id=actor_id,
    )
    db.add(destination)
    db.flush([destination])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.WAREHOUSE_DESTINATION,
        resource_id=destination.id,
        action=AuditAction.DESTINATION_CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "kind": destination.kind,
            "label": destination.label,
            "credential_fingerprint": destination.credential_fingerprint,
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return destination


def update_destination(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    label: Optional[str] = None,
    status: Optional[str] = None,
    credential: Optional[Any] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> WarehouseDestination:
    destination = get_destination(
        db, organization_id=organization_id, destination_id=destination_id
    )

    changed: dict[str, Any] = {}
    if label is not None and label != destination.label:
        destination.label = label
        changed["label"] = label
    if status is not None and status != destination.status:
        destination.status = status
        changed["status"] = status

    if credential is not None:
        if str(credential.kind) != destination.kind:
            raise SyncServiceError(
                f"Destination {destination_id} is a {destination.kind} "
                f"destination; a {credential.kind} credential cannot replace "
                "its credential. Create a new destination instead."
            )
        config, secret = _split_credential(credential)
        plaintext = _serialise_secret(secret)
        destination.config = config
        destination.encrypted_credential = encrypt_secret(plaintext)
        destination.credential_fingerprint = secret_fingerprint(plaintext)
        # A rotated credential invalidates the previous probe result. Leaving
        # the old green tick in place would show a verified destination for a
        # key nobody has tried.
        destination.last_tested_at = None
        destination.last_test_ok = None
        destination.last_test_error = None
        changed["credential_fingerprint"] = destination.credential_fingerprint

    db.flush([destination])

    if changed:
        audit_service.record(
            db,
            organization_id=organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.WAREHOUSE_DESTINATION,
            resource_id=destination.id,
            action=(
                AuditAction.ROTATED
                if "credential_fingerprint" in changed
                else AuditAction.UPDATED
            ),
            outcome=AuditOutcome.ALLOWED,
            details=changed,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return destination


def delete_destination(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    destination = get_destination(
        db, organization_id=organization_id, destination_id=destination_id
    )
    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.WAREHOUSE_DESTINATION,
        resource_id=destination.id,
        action=AuditAction.DELETED,
        outcome=AuditOutcome.ALLOWED,
        details={"kind": destination.kind, "label": destination.label},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.delete(destination)
    db.flush()


def test_destination(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> dict[str, Any]:
    """Probe a destination and record the outcome on both paths.

    A failing probe is audited as DENIED rather than skipped. A burst of them
    against varying hostnames is what credential spraying through our egress
    looks like, and it is only visible if the failures are written down.
    """
    destination = get_destination(
        db, organization_id=organization_id, destination_id=destination_id
    )
    connector = get_connector(destination.kind)

    tested_at = _now()
    try:
        credential = _decrypted_credential(destination)
        outcome = connector.test_connection(
            config=dict(destination.config or {}), credential=credential
        )
        ok = bool(outcome.ok)
        detail = outcome.detail
        latency_ms = outcome.latency_ms
    except (ConnectorError, CredentialUnavailableError) as exc:
        ok = False
        detail = scrub(exc)
        latency_ms = None

    destination.last_tested_at = tested_at
    destination.last_test_ok = ok
    destination.last_test_error = None if ok else (detail or "unknown failure")
    db.flush([destination])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.WAREHOUSE_DESTINATION,
        resource_id=destination.id,
        action=AuditAction.DESTINATION_TESTED,
        outcome=AuditOutcome.ALLOWED if ok else AuditOutcome.DENIED,
        details={"kind": destination.kind, "detail": detail},
        ip_address=ip_address,
        user_agent=user_agent,
    )

    return {
        "ok": ok,
        "kind": destination.kind,
        "latency_ms": latency_ms,
        "detail": detail,
        "tested_at": tested_at,
    }


# ---------------------------------------------------------------------------
# Schedules
# ---------------------------------------------------------------------------


def compute_next_run(
    *,
    cadence: str,
    hour_utc: int,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    after: Optional[datetime] = None,
) -> datetime:
    """The next UTC instant this schedule should fire, strictly after `after`.

    Hand-rolled rather than pulled from croniter, which is not pinned. The
    cadence vocabulary is three values with one time-of-day each; a cron
    parser would be a dependency bought to express `DAILY`.
    """
    base = (after or _now()).astimezone(timezone.utc)
    candidate = base.replace(
        hour=hour_utc, minute=0, second=0, microsecond=0
    )

    if cadence == "DAILY":
        if candidate <= base:
            candidate += timedelta(days=1)
        return candidate

    if cadence == "WEEKLY":
        if day_of_week is None:
            raise SyncServiceError("A WEEKLY schedule requires day_of_week.")
        delta = (int(day_of_week) - candidate.weekday()) % 7
        candidate += timedelta(days=delta)
        if candidate <= base:
            candidate += timedelta(days=7)
        return candidate

    if cadence == "MONTHLY":
        if day_of_month is None:
            raise SyncServiceError("A MONTHLY schedule requires day_of_month.")
        candidate = candidate.replace(day=int(day_of_month))
        if candidate <= base:
            year = candidate.year + (1 if candidate.month == 12 else 0)
            month = 1 if candidate.month == 12 else candidate.month + 1
            candidate = candidate.replace(year=year, month=month)
        return candidate

    raise SyncServiceError(f"Unknown cadence {cadence!r}.")


def list_schedules(
    db: Session, *, organization_id: uuid.UUID
) -> list[ExportSchedule]:
    stmt = (
        select(ExportSchedule)
        .where(ExportSchedule.organization_id == organization_id)
        .order_by(ExportSchedule.created_at.asc())
    )
    return list(db.execute(stmt).scalars())


def get_schedule(
    db: Session, *, organization_id: uuid.UUID, schedule_id: uuid.UUID
) -> ExportSchedule:
    stmt = (
        select(ExportSchedule)
        .where(ExportSchedule.id == schedule_id)
        .where(ExportSchedule.organization_id == organization_id)
    )
    found = db.execute(stmt).scalar_one_or_none()
    if found is None:
        raise ScheduleNotFoundError(str(schedule_id))
    return found


def _validate_datasets(datasets: Sequence[str]) -> list[str]:
    unknown = sorted(set(datasets) - set(EXPORT_DATASET_VALUES))
    if unknown:
        raise SyncServiceError(
            f"Unknown dataset(s) {unknown}. Known: "
            f"{sorted(EXPORT_DATASET_VALUES)}."
        )
    if not datasets:
        raise SyncServiceError("At least one dataset is required.")
    return list(datasets)


def create_schedule(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    datasets: Sequence[str],
    cadence: str,
    hour_utc: int,
    day_of_week: Optional[int],
    day_of_month: Optional[int],
    lookback_days: int,
    enabled: bool,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ExportSchedule:
    # Resolving through get_destination is what scopes this: a schedule
    # pointing at another tenant's destination would otherwise be creatable by
    # guessing a UUID.
    destination = get_destination(
        db, organization_id=organization_id, destination_id=destination_id
    )

    schedule = ExportSchedule(
        organization_id=organization_id,
        destination_id=destination.id,
        datasets=_validate_datasets(datasets),
        cadence=cadence,
        hour_utc=hour_utc,
        day_of_week=day_of_week,
        day_of_month=day_of_month,
        lookback_days=lookback_days,
        enabled=enabled,
        next_run_at=compute_next_run(
            cadence=cadence,
            hour_utc=hour_utc,
            day_of_week=day_of_week,
            day_of_month=day_of_month,
        ),
    )
    db.add(schedule)
    db.flush([schedule])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.EXPORT_SCHEDULE,
        resource_id=schedule.id,
        action=AuditAction.CREATED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "destination_id": str(destination.id),
            "cadence": cadence,
            "datasets": list(schedule.datasets),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )
    return schedule


def update_schedule(
    db: Session,
    *,
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    datasets: Optional[Sequence[str]] = None,
    cadence: Optional[str] = None,
    hour_utc: Optional[int] = None,
    day_of_week: Optional[int] = None,
    day_of_month: Optional[int] = None,
    lookback_days: Optional[int] = None,
    enabled: Optional[bool] = None,
    reset_circuit: bool = False,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> ExportSchedule:
    schedule = get_schedule(
        db, organization_id=organization_id, schedule_id=schedule_id
    )

    changed: dict[str, Any] = {}
    if datasets is not None:
        schedule.datasets = _validate_datasets(datasets)
        changed["datasets"] = list(schedule.datasets)
    if cadence is not None:
        schedule.cadence = cadence
        changed["cadence"] = cadence
    if hour_utc is not None:
        schedule.hour_utc = hour_utc
        changed["hour_utc"] = hour_utc
    if day_of_week is not None:
        schedule.day_of_week = day_of_week
        changed["day_of_week"] = day_of_week
    if day_of_month is not None:
        schedule.day_of_month = day_of_month
        changed["day_of_month"] = day_of_month
    if lookback_days is not None:
        schedule.lookback_days = lookback_days
        changed["lookback_days"] = lookback_days
    if enabled is not None:
        schedule.enabled = enabled
        changed["enabled"] = enabled

    if reset_circuit:
        # Explicit, and separate from `enabled`. Collapsing the two means
        # every pause/resume silently clears a failure count that was about to
        # alert somebody.
        schedule.consecutive_failure_count = 0
        schedule.circuit_opened_at = None
        changed["circuit_reset"] = True

    if any(
        key in changed
        for key in ("cadence", "hour_utc", "day_of_week", "day_of_month")
    ):
        schedule.next_run_at = compute_next_run(
            cadence=schedule.cadence,
            hour_utc=schedule.hour_utc,
            day_of_week=schedule.day_of_week,
            day_of_month=schedule.day_of_month,
        )
        changed["next_run_at"] = schedule.next_run_at.isoformat()

    db.flush([schedule])

    if changed:
        audit_service.record(
            db,
            organization_id=organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.EXPORT_SCHEDULE,
            resource_id=schedule.id,
            action=(
                AuditAction.ENABLED
                if changed.get("enabled") is True
                else AuditAction.DISABLED
                if changed.get("enabled") is False
                else AuditAction.UPDATED
            ),
            outcome=AuditOutcome.ALLOWED,
            details=changed,
            ip_address=ip_address,
            user_agent=user_agent,
        )
    return schedule


def delete_schedule(
    db: Session,
    *,
    organization_id: uuid.UUID,
    schedule_id: uuid.UUID,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    schedule = get_schedule(
        db, organization_id=organization_id, schedule_id=schedule_id
    )
    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.EXPORT_SCHEDULE,
        resource_id=schedule.id,
        action=AuditAction.DELETED,
        outcome=AuditOutcome.ALLOWED,
        details={"cadence": schedule.cadence},
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.delete(schedule)
    db.flush()


def due_schedules(db: Session, *, now: Optional[datetime] = None) -> list[ExportSchedule]:
    """Schedules the dispatcher should pick up on this tick.

    Cross-tenant by necessity — this is the platform's own sweeper, not a
    tenant-facing read — and it is the only query in this module without an
    organization predicate. Every row it returns carries its own
    `organization_id`, which is what every downstream call is scoped by.
    """
    moment = now or _now()
    stmt = (
        select(ExportSchedule)
        .where(ExportSchedule.enabled.is_(True))
        .where(ExportSchedule.circuit_opened_at.is_(None))
        .where(ExportSchedule.next_run_at.is_not(None))
        .where(ExportSchedule.next_run_at <= moment)
        .order_by(ExportSchedule.next_run_at.asc())
        .limit(500)
    )
    return list(db.execute(stmt).scalars())


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def list_runs(
    db: Session,
    *,
    organization_id: uuid.UUID,
    limit: int = DEFAULT_RUN_HISTORY_LIMIT,
    destination_id: Optional[uuid.UUID] = None,
) -> list[ExportSyncRun]:
    bounded = max(1, min(int(limit), MAX_RUN_HISTORY_LIMIT))
    stmt = (
        select(ExportSyncRun)
        .where(ExportSyncRun.organization_id == organization_id)
        .order_by(ExportSyncRun.started_at.desc())
        .limit(bounded)
    )
    if destination_id is not None:
        stmt = stmt.where(ExportSyncRun.destination_id == destination_id)
    return list(db.execute(stmt).scalars())


def _record_failure(
    db: Session,
    *,
    schedule: Optional[ExportSchedule],
    organization_id: uuid.UUID,
    destination_label: str,
) -> None:
    """Advance the circuit and alert when it trips.

    The alert is a structured `logger.error`, matching the convention ARCH-25
    established for operator-facing conditions: it reaches the ARCH-17
    pipeline with tenant and destination attached, which is what an on-call
    engineer filters on. An audit row is written too, but the audit log is the
    record that it happened, not the mechanism that surfaces it.
    """
    if schedule is None:
        return
    schedule.consecutive_failure_count = int(
        schedule.consecutive_failure_count or 0
    ) + 1
    if (
        schedule.consecutive_failure_count >= CIRCUIT_FAILURE_THRESHOLD
        and schedule.circuit_opened_at is None
    ):
        schedule.circuit_opened_at = _now()
        logger.error(
            "analytics.sync.circuit_opened",
            extra={
                "organization_id": str(organization_id),
                "schedule_id": str(schedule.id),
                "destination_label": destination_label,
                "consecutive_failures": schedule.consecutive_failure_count,
                "threshold": CIRCUIT_FAILURE_THRESHOLD,
            },
        )
    db.flush([schedule])


def execute_sync(
    db: Session,
    *,
    organization_id: uuid.UUID,
    destination_id: uuid.UUID,
    datasets: Sequence[str],
    lookback_days: int,
    trigger: str,
    schedule: Optional[ExportSchedule] = None,
    actor_id: Optional[uuid.UUID] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> RunResult:
    """Extract, digest, archive, push. One run, one receipt.

    The order matters. Parts are written to OUR tenant-isolated storage and
    digested BEFORE the push, so that a run which fails at the destination
    still leaves a verifiable artefact we can re-push without re-querying —
    and so the digest in the manifest is over bytes that exist rather than
    over bytes we are about to send.
    """
    destination = get_destination(
        db, organization_id=organization_id, destination_id=destination_id
    )
    _validate_datasets(datasets)

    window_end = _now()
    window_start = window_end - timedelta(days=max(1, int(lookback_days)))

    run = ExportSyncRun(
        organization_id=organization_id,
        destination_id=destination.id,
        schedule_id=schedule.id if schedule is not None else None,
        destination_label=destination.label,
        destination_kind=destination.kind,
        trigger=trigger,
        status="RUNNING",
        datasets=list(datasets),
        window_start=window_start,
        window_end=window_end,
        started_at=window_end,
        triggered_by_id=actor_id,
    )
    db.add(run)
    db.flush([run])

    audit_service.record(
        db,
        organization_id=organization_id,
        actor_id=actor_id,
        resource_type=AuditResourceType.EXPORT_SYNC_RUN,
        resource_id=run.id,
        action=AuditAction.SYNC_TRIGGERED,
        outcome=AuditOutcome.ALLOWED,
        details={
            "trigger": trigger,
            "destination_id": str(destination.id),
            "datasets": list(datasets),
            "window_start": window_start.isoformat(),
            "window_end": window_end.isoformat(),
        },
        ip_address=ip_address,
        user_agent=user_agent,
    )

    driver = get_storage_driver()
    parts: list[BundlePart] = []
    engine_parts: list[export_engine.ExportPart] = []
    total_rows = 0
    total_bytes = 0

    try:
        for dataset in datasets:
            spec = export_engine.DATASET_SPECS[dataset]
            rows = export_engine.extract_dataset(
                db,
                dataset,
                organization_id=organization_id,
                window_start=window_start,
                window_end=window_end,
            )
            payload = export_engine.write_parquet(rows, spec)
            digest = export_engine.digest_bytes(payload)

            # B5-a: flat UUID part files under the EXPORTS namespace. The
            # three-segment key grammar in app/core/storage/keys.py forbids a
            # nested bundle directory, and widening it for a reporting feature
            # would loosen a tenant-isolation guard used across ARCH-10 and
            # ARCH-20.
            key = tenant_key(
                organization_id=organization_id,
                namespace=StorageNamespace.EXPORTS,
                file_id=uuid.uuid4(),
                suffix="parquet",
            )
            driver.put(key, payload, "application/vnd.apache.parquet")

            truncated = len(rows) >= export_engine.MAX_ROWS_PER_DATASET
            parts.append(
                BundlePart(
                    dataset=dataset,
                    version=spec.version,
                    filename=f"{dataset.lower()}_v{spec.version}.parquet",
                    payload=payload,
                    sha256=digest,
                    row_count=len(rows),
                )
            )
            engine_parts.append(
                export_engine.ExportPart(
                    dataset=dataset,
                    version=spec.version,
                    row_count=len(rows),
                    byte_count=len(payload),
                    sha256=digest,
                    storage_key=key,
                    truncated=truncated,
                )
            )
            total_rows += len(rows)
            total_bytes += len(payload)

        manifest = export_engine.build_manifest(
            run_id=run.id,
            organization_id=organization_id,
            window_start=window_start,
            window_end=window_end,
            parts=engine_parts,
        )
        import json

        manifest_key = tenant_key(
            organization_id=organization_id,
            namespace=StorageNamespace.EXPORTS,
            file_id=uuid.uuid4(),
            suffix="json",
        )
        driver.put(
            manifest_key,
            json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            "application/json",
        )

        connector = get_connector(destination.kind)
        credential = _decrypted_credential(destination)
        outcome: PushOutcome = connector.push(
            config=dict(destination.config or {}),
            credential=credential,
            parts=parts,
            run_id=str(run.id),
        )

        if outcome.ok:
            status = "SUCCEEDED"
        elif outcome.partial:
            status = "PARTIAL"
        else:
            status = "FAILED"

        run.status = status
        run.row_count = total_rows
        run.byte_count = total_bytes
        run.part_count = len(parts)
        run.manifest_key = manifest_key
        run.finished_at = _now()
        # The CHECK constraint pairs SUCCEEDED with a digest. A PARTIAL run
        # deliberately does not carry one: some of what the manifest digests
        # never arrived, and a digest on that row would assert a delivery that
        # did not happen.
        run.bundle_digest = (
            str(manifest["bundle_digest"]) if status == "SUCCEEDED" else None
        )
        if status != "SUCCEEDED":
            run.error_code = "PUSH_INCOMPLETE"
            run.error_detail = scrub(
                outcome.detail
                or f"undelivered: {', '.join(outcome.failed_datasets)}"
            )
        db.flush([run])

        if status == "SUCCEEDED":
            if schedule is not None:
                schedule.consecutive_failure_count = 0
                schedule.last_run_at = run.finished_at
                schedule.next_run_at = compute_next_run(
                    cadence=schedule.cadence,
                    hour_utc=schedule.hour_utc,
                    day_of_week=schedule.day_of_week,
                    day_of_month=schedule.day_of_month,
                )
                db.flush([schedule])
            audit_service.record(
                db,
                organization_id=organization_id,
                actor_id=actor_id,
                resource_type=AuditResourceType.EXPORT_SYNC_RUN,
                resource_id=run.id,
                action=AuditAction.SYNC_COMPLETED,
                outcome=AuditOutcome.ALLOWED,
                details={
                    "bundle_digest": run.bundle_digest,
                    "row_count": total_rows,
                    "part_count": len(parts),
                    "destination_id": str(destination.id),
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )
        else:
            _record_failure(
                db,
                schedule=schedule,
                organization_id=organization_id,
                destination_label=destination.label,
            )
            audit_service.record(
                db,
                organization_id=organization_id,
                actor_id=actor_id,
                resource_type=AuditResourceType.EXPORT_SYNC_RUN,
                resource_id=run.id,
                action=AuditAction.SYNC_FAILED,
                outcome=AuditOutcome.DENIED,
                details={
                    "delivered": list(outcome.delivered_datasets),
                    "failed": list(outcome.failed_datasets),
                    "detail": run.error_detail,
                },
                ip_address=ip_address,
                user_agent=user_agent,
            )

        return RunResult(
            run_id=run.id,
            status=status,
            row_count=run.row_count,
            byte_count=run.byte_count,
            part_count=run.part_count,
            bundle_digest=run.bundle_digest,
            detail=run.error_detail,
        )

    except (
        ConnectorError,
        CredentialUnavailableError,
        export_engine.ExportEngineError,
        SyncServiceError,
    ) as exc:
        code = getattr(exc, "code", None) or type(exc).__name__
        run.status = "FAILED"
        run.finished_at = _now()
        run.error_code = str(code)[:64]
        run.error_detail = scrub(exc)
        # row_count and byte_count stay NULL. The run died before it could
        # count, and 0 would make that indistinguishable from an empty window.
        db.flush([run])

        _record_failure(
            db,
            schedule=schedule,
            organization_id=organization_id,
            destination_label=destination.label,
        )
        audit_service.record(
            db,
            organization_id=organization_id,
            actor_id=actor_id,
            resource_type=AuditResourceType.EXPORT_SYNC_RUN,
            resource_id=run.id,
            action=AuditAction.SYNC_FAILED,
            outcome=AuditOutcome.DENIED,
            details={"error_code": run.error_code, "detail": run.error_detail},
            ip_address=ip_address,
            user_agent=user_agent,
        )
        logger.warning(
            "analytics.sync.run_failed",
            extra={
                "organization_id": str(organization_id),
                "run_id": str(run.id),
                "error_code": run.error_code,
            },
        )
        return RunResult(
            run_id=run.id,
            status="FAILED",
            row_count=None,
            byte_count=None,
            part_count=None,
            bundle_digest=None,
            detail=run.error_detail,
        )


__all__ = [
    "CredentialUnavailableError",
    "DEFAULT_RUN_HISTORY_LIMIT",
    "DestinationNotFoundError",
    "JOB_TYPE_EXPORT_SYNC",
    "JOB_TYPE_WAREHOUSE_PUSH",
    "MAX_RUN_HISTORY_LIMIT",
    "RunResult",
    "ScheduleNotFoundError",
    "SyncServiceError",
    "compute_next_run",
    "create_destination",
    "create_schedule",
    "delete_destination",
    "delete_schedule",
    "due_schedules",
    "execute_sync",
    "get_destination",
    "get_schedule",
    "list_destinations",
    "list_runs",
    "list_schedules",
    "test_destination",
    "update_destination",
    "update_schedule",
]