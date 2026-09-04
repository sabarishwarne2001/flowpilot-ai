"""ARCH-26 — warehouse destinations, export schedules, and sync runs.

Three tables rather than one, because they have three different lifetimes and
three different readers:

    warehouse_destinations  Holds a credential. Changes rarely. Read by a
                            security reviewer asking what keys we hold.
    export_schedules        Holds a cadence. Changes occasionally. Read by the
                            dispatcher on every tick.
    export_sync_runs        Holds an outcome. Written on every run, which for
                            a daily schedule is 365 rows a year per tenant.
                            Read by a support engineer answering "why is the
                            board deck stale?"

Collapsing them means every credential-history query also filters out run
rows, which arrive several orders of magnitude more often.

THE COGS CONFIDENTIALITY LINE RUNS THROUGH THIS MODULE
======================================================

None of these models references `cost_basis_micros`, and none of them ever
will. That is invariant I1: supplier cost is commercially sensitive, and a
tenant who can read our COGS can negotiate against it. The export engine reads
`usage_rollups.cost_micros` — what we charged them — and quantity columns.
`verify_arch26.py` G5 walks the AST of this module, the schema module and the
export engine and fails on any attribute access named `cost_basis_micros`,
`cost_basis_source`, `cost_basis_source_mix` or `unknown_cost_basis_event_count`.

WHY `__repr__` NAMES NOTHING SECRET
===================================

`encrypted_credential` is ciphertext, so printing it is not a disclosure in
the cryptographic sense. It is still absent from every `__repr__` here,
because a repr lands in exception tracebacks, in Sentry, and in the logs of
whatever aggregator the operator runs — and a ciphertext sitting in a log for
a year is a ciphertext available to whoever compromises the log after the next
key rotation makes everyone stop worrying about it. The fingerprint is
sufficient for the debugging the repr exists to support.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Closed vocabularies
#
# Mirrored by alembic/versions/arch26_step2_warehouse_sync.py. verify_arch26.py
# G3 asserts the two agree tuple-for-tuple; drift produces a row Postgres
# accepts and the service refuses, or the reverse.
# ---------------------------------------------------------------------------

DESTINATION_KIND_VALUES: tuple[str, ...] = (
    "SNOWFLAKE",
    "BIGQUERY",
    "DATABRICKS",
    "S3",
)

DESTINATION_STATUS_VALUES: tuple[str, ...] = ("ACTIVE", "DISABLED")

EXPORT_DATASET_VALUES: tuple[str, ...] = (
    "USAGE_ROLLUPS",
    "DOCUMENT_METADATA",
    "ASSISTANT_TURNS",
    "AUTOMATION_RUNS",
)

SCHEDULE_CADENCE_VALUES: tuple[str, ...] = ("DAILY", "WEEKLY", "MONTHLY")

SYNC_TRIGGER_VALUES: tuple[str, ...] = ("SCHEDULED", "MANUAL")

SYNC_STATUS_VALUES: tuple[str, ...] = (
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL",
    "FAILED",
)

#: Consecutive failures before a schedule's circuit opens and the dispatcher
#: stops picking it up. Hardening invariant 5.
CIRCUIT_FAILURE_THRESHOLD: int = 5

MAX_LABEL_LENGTH: int = 120
MAX_STORAGE_KEY_LENGTH: int = 512
MAX_DIGEST_LENGTH: int = 64

#: Terminal statuses. A run in any of these is finished and `finished_at` is
#: set; `ck_export_sync_runs_finished_matches_status` enforces the pairing.
TERMINAL_SYNC_STATUSES: frozenset[str] = frozenset(
    {"SUCCEEDED", "PARTIAL", "FAILED"}
)

_KIND_SQL = ", ".join(f"'{v}'" for v in DESTINATION_KIND_VALUES)
_DEST_STATUS_SQL = ", ".join(f"'{v}'" for v in DESTINATION_STATUS_VALUES)
_CADENCE_SQL = ", ".join(f"'{v}'" for v in SCHEDULE_CADENCE_VALUES)
_TRIGGER_SQL = ", ".join(f"'{v}'" for v in SYNC_TRIGGER_VALUES)
_SYNC_STATUS_SQL = ", ".join(f"'{v}'" for v in SYNC_STATUS_VALUES)


class WarehouseDestination(Base, UUIDMixin, TimestampMixin):
    """One tenant-owned warehouse we are permitted to write into."""

    __tablename__ = "warehouse_destinations"

    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_SQL})", name="kind_known"),
        CheckConstraint(
            f"status IN ({_DEST_STATUS_SQL})", name="status_known"
        ),
        CheckConstraint(
            "char_length(encrypted_credential) > 0", name="credential_present"
        ),
        CheckConstraint("char_length(label) > 0", name="label_present"),
        Index(
            "uq_warehouse_destinations_org_label",
            "organization_id",
            "label",
            unique=True,
        ),
        Index(
            "ix_warehouse_destinations_organization_id",
            "organization_id",
        ),
        Index(
            "ix_warehouse_destinations_active",
            "organization_id",
            "kind",
            postgresql_where=text("status = 'ACTIVE'"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    label: Mapped[str] = mapped_column(
        String(MAX_LABEL_LENGTH), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'ACTIVE'")
    )

    config: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )

    #: MultiFernet ciphertext from `app.core.encryption.encrypt_secret`.
    #: `Text` and not `String(512)`: a BigQuery service-account JSON is ~2.3KB.
    #: Never appears in a response schema — invariant I2, asserted by G7.
    encrypted_credential: Mapped[str] = mapped_column(Text, nullable=False)

    credential_fingerprint: Mapped[str] = mapped_column(
        String(12), nullable=False
    )

    last_tested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: NULL means never probed. False means probed and refused. Invariant 6
    #: applied to a boolean: the two are not the same fact.
    last_test_ok: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    last_test_error: Mapped[Optional[str]] = mapped_column(
        String(500), nullable=True
    )

    created_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    schedules: Mapped[list["ExportSchedule"]] = relationship(
        "ExportSchedule",
        back_populates="destination",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    @property
    def has_been_tested(self) -> bool:
        """True only once a probe has actually run.

        `last_test_ok is False` is a tested destination that refused us.
        `last_test_ok is None` is one nobody has tried. A console that
        collapses these shows a green tick for a warehouse that has never
        accepted a byte.
        """
        return self.last_test_ok is not None

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<WarehouseDestination {self.kind} {self.label!r} "
            f"org={self.organization_id} status={self.status} "
            f"cred={self.credential_fingerprint}>"
        )


class ExportSchedule(Base, UUIDMixin, TimestampMixin):
    """A cadence at which one destination receives one set of datasets."""

    __tablename__ = "export_schedules"

    __table_args__ = (
        CheckConstraint(f"cadence IN ({_CADENCE_SQL})", name="cadence_known"),
        CheckConstraint(
            "hour_utc >= 0 AND hour_utc <= 23", name="hour_in_range"
        ),
        CheckConstraint(
            "cadence <> 'WEEKLY' OR day_of_week IS NOT NULL",
            name="weekly_needs_day_of_week",
        ),
        CheckConstraint(
            "cadence <> 'MONTHLY' OR day_of_month IS NOT NULL",
            name="monthly_needs_day_of_month",
        ),
        CheckConstraint(
            "day_of_week IS NULL OR (day_of_week >= 0 AND day_of_week <= 6)",
            name="day_of_week_in_range",
        ),
        CheckConstraint(
            "day_of_month IS NULL OR (day_of_month >= 1 AND day_of_month <= 28)",
            name="day_of_month_in_range",
        ),
        CheckConstraint(
            "lookback_days >= 1 AND lookback_days <= 90",
            name="lookback_in_range",
        ),
        CheckConstraint(
            "consecutive_failure_count >= 0",
            name="failure_count_non_negative",
        ),
        CheckConstraint(
            "jsonb_typeof(datasets) = 'array' AND jsonb_array_length(datasets) > 0",
            name="datasets_non_empty_array",
        ),
        Index(
            "uq_export_schedules_destination_cadence",
            "destination_id",
            "cadence",
            unique=True,
        ),
        Index("ix_export_schedules_organization_id", "organization_id"),
        Index(
            "ix_export_schedules_due",
            "next_run_at",
            postgresql_where=text(
                "enabled = true AND circuit_opened_at IS NULL"
            ),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("warehouse_destinations.id", ondelete="CASCADE"),
        nullable=False,
    )

    datasets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    cadence: Mapped[str] = mapped_column(String(16), nullable=False)
    hour_utc: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("2")
    )
    day_of_week: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )
    day_of_month: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True
    )

    lookback_days: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    consecutive_failure_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    circuit_opened_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    last_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_run_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    destination: Mapped["WarehouseDestination"] = relationship(
        "WarehouseDestination", back_populates="schedules"
    )

    @property
    def circuit_is_open(self) -> bool:
        return self.circuit_opened_at is not None

    @property
    def is_dispatchable(self) -> bool:
        """Whether the dispatcher may pick this schedule up.

        Three conditions, all of them necessary. `enabled` is the tenant's
        switch, `circuit_is_open` is ours, and `next_run_at` being NULL means
        the cadence has never been computed — dispatching that would run the
        schedule immediately on the tick after it was created, which is not
        what "daily at 02:00" means.
        """
        return (
            self.enabled
            and not self.circuit_is_open
            and self.next_run_at is not None
        )

    def __repr__(self) -> str:  # pragma: no cover
        state = "enabled" if self.enabled else "disabled"
        if self.circuit_is_open:
            state = f"CIRCUIT-OPEN({self.consecutive_failure_count})"
        return (
            f"<ExportSchedule {self.cadence}@{self.hour_utc:02d}Z "
            f"org={self.organization_id} dest={self.destination_id} "
            f"datasets={len(self.datasets or [])} {state}>"
        )


class ExportSyncRun(Base, UUIDMixin, TimestampMixin):
    """One attempt to deliver a bundle. A delivery receipt, not a job row."""

    __tablename__ = "export_sync_runs"

    __table_args__ = (
        CheckConstraint(f"trigger IN ({_TRIGGER_SQL})", name="trigger_known"),
        CheckConstraint(
            f"status IN ({_SYNC_STATUS_SQL})", name="status_known"
        ),
        CheckConstraint(
            f"destination_kind IN ({_KIND_SQL})", name="kind_known"
        ),
        CheckConstraint("window_end > window_start", name="window_ordered"),
        CheckConstraint(
            "row_count IS NULL OR row_count >= 0",
            name="row_count_non_negative",
        ),
        CheckConstraint(
            "byte_count IS NULL OR byte_count >= 0",
            name="byte_count_non_negative",
        ),
        CheckConstraint(
            "part_count IS NULL OR part_count >= 0",
            name="part_count_non_negative",
        ),
        CheckConstraint("attempt >= 1", name="attempt_positive"),
        CheckConstraint(
            "(status = 'SUCCEEDED') = (bundle_digest IS NOT NULL)",
            name="succeeded_has_digest",
        ),
        CheckConstraint(
            "(status = 'RUNNING') = (finished_at IS NULL)",
            name="finished_matches_status",
        ),
        CheckConstraint(
            "status <> 'FAILED' OR error_code IS NOT NULL",
            name="failed_has_error_code",
        ),
        Index(
            "ix_export_sync_runs_org_started",
            "organization_id",
            text("started_at DESC"),
        ),
        Index(
            "ix_export_sync_runs_destination",
            "destination_id",
            postgresql_where=text("destination_id IS NOT NULL"),
        ),
        Index(
            "ix_export_sync_runs_failed",
            "organization_id",
            text("started_at DESC"),
            postgresql_where=text("status = 'FAILED'"),
        ),
        Index(
            "ix_export_sync_runs_schedule",
            "schedule_id",
            postgresql_where=text("schedule_id IS NOT NULL"),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )
    destination_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("warehouse_destinations.id", ondelete="SET NULL"),
        nullable=True,
    )
    schedule_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("export_schedules.id", ondelete="SET NULL"),
        nullable=True,
    )

    destination_label: Mapped[str] = mapped_column(
        String(MAX_LABEL_LENGTH), nullable=False
    )
    destination_kind: Mapped[str] = mapped_column(String(16), nullable=False)

    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text("'RUNNING'")
    )

    datasets: Mapped[list[str]] = mapped_column(JSONB, nullable=False)

    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    #: NULL is "not counted", which is what a crashed run has. Never 0.
    row_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    byte_count: Mapped[Optional[int]] = mapped_column(
        BigInteger, nullable=True
    )
    part_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    bundle_digest: Mapped[Optional[str]] = mapped_column(
        String(MAX_DIGEST_LENGTH), nullable=True
    )
    manifest_key: Mapped[Optional[str]] = mapped_column(
        String(MAX_STORAGE_KEY_LENGTH), nullable=True
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    error_code: Mapped[Optional[str]] = mapped_column(
        String(64), nullable=True
    )
    error_detail: Mapped[Optional[str]] = mapped_column(
        String(1000), nullable=True
    )

    attempt: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    triggered_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_SYNC_STATUSES

    @property
    def duration_seconds(self) -> Optional[float]:
        """NULL while running, and NULL is the honest answer.

        A caller that wants "elapsed so far" can compute it; this property
        refuses to hand back a partial duration that reads like a final one.
        """
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at).total_seconds()

    def __repr__(self) -> str:  # pragma: no cover
        rows = "rows=unknown" if self.row_count is None else f"rows={self.row_count}"
        digest = (
            "" if self.bundle_digest is None else f" digest={self.bundle_digest[:12]}"
        )
        return (
            f"<ExportSyncRun {self.status} {self.trigger} "
            f"org={self.organization_id} dest={self.destination_label!r} "
            f"{rows}{digest}>"
        )


__all__ = [
    "CIRCUIT_FAILURE_THRESHOLD",
    "DESTINATION_KIND_VALUES",
    "DESTINATION_STATUS_VALUES",
    "EXPORT_DATASET_VALUES",
    "ExportSchedule",
    "ExportSyncRun",
    "MAX_DIGEST_LENGTH",
    "MAX_LABEL_LENGTH",
    "MAX_STORAGE_KEY_LENGTH",
    "SCHEDULE_CADENCE_VALUES",
    "SYNC_STATUS_VALUES",
    "SYNC_TRIGGER_VALUES",
    "TERMINAL_SYNC_STATUSES",
    "WarehouseDestination",
]