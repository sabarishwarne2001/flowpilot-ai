"""ARCH-32 — redaction jobs and regions.

THE CONSTRAINT THAT CARRIES THE PRODUCT
=======================================

`ck_rj_completed_is_sealed`. A row cannot say COMPLETED unless it also holds
an output file, an output hash, a manifest, `leak_check_passed IS TRUE` and an
approval timestamp. All five, at the database.

This is not defence in depth around a service rule — it is the rule. Every
other way of expressing "a completed job is one that passed the leak check"
lives in Python that a later refactor can reorder: a status set before a
commit, an exception swallowed by a retry wrapper, a partial write from a
worker killed mid-apply. The CHECK cannot be reordered. `verify_arch32.py`
drives an UPDATE that sets COMPLETED with `leak_check_passed = false` and
requires the database to refuse it.

WHY `token_digest` AND NOT THE TOKEN
====================================

`redaction_regions` stores an HMAC-SHA256 of the matched text, keyed through
the ARCH-07 key lifecycle, and never the text. The digest is enough to answer
the two questions anyone actually asks — "is this the same value as that one?"
and "did this region's target change between detect and apply?" — and it is
not enough to reconstruct the value.

The consequence is deliberate and worth stating: after a detect run, NOBODY,
including an operator with database access, can read what a region covers.
The reviewer sees a box on a page and a detector name. That is the whole point
of a table that a breach cannot turn into the PII corpus the documents were
redacted to protect.

The leak check needs the plaintext, so the apply job re-derives detections
from the source in worker memory and discards them when it returns. See
`leakcheck.check_output`.

WHY STATUS AND PRECISION ARE STRINGS UNDER A CHECK, NOT PgEnum
==============================================================

Identical reasoning to `app/models/procurement.py`, and deliberately the same
mechanism so the tree has one pattern: a PgEnum needs an out-of-transaction
`ALTER TYPE ... ADD VALUE` to grow, and a newly added value cannot be used in
the transaction that added it. Both vocabularies here are closed by design —
a seventh status changes what the studio renders and what the apply job may
do — so it is a code change either way, and a CHECK gives the same refusal
without the migration hazard.

The constants come from `app/services/redaction/vocabulary.py`, which imports
nothing but the standard library. `verify_arch32.py` asserts they equal the
CHECK bodies in the Step 1 migration.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, TimestampMixin, UUIDMixin

# ---------------------------------------------------------------------------
# Vocabulary — re-exported, not declared. See the module header and
# app/services/redaction/vocabulary.py's own header for why it lives there.
# ---------------------------------------------------------------------------

from app.services.redaction.vocabulary import (  # noqa: E402
    DEFAULT_RENDER_DPI,
    DETECTOR_MANUAL,
    DETECTORS,
    GEOMETRY_PRECISIONS,
    JOB_STATUS_APPLYING,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_COMPLETED,
    JOB_STATUS_DETECTING,
    JOB_STATUS_FAILED,
    JOB_STATUS_REVIEW,
    JOB_STATUSES,
    LIVE_JOB_STATUSES,
    MAX_RENDER_DPI,
    MIN_RENDER_DPI,
    PRECISION_BLOCK,
    PRECISION_GLYPH,
    PRECISION_MANUAL,
    PROFILE_KEYS,
    TERMINAL_JOB_STATUSES,
)

__all__ = [
    "RedactionJob",
    "RedactionRegion",
    "JOB_STATUSES",
    "JOB_STATUS_DETECTING",
    "JOB_STATUS_REVIEW",
    "JOB_STATUS_APPLYING",
    "JOB_STATUS_COMPLETED",
    "JOB_STATUS_FAILED",
    "JOB_STATUS_CANCELLED",
    "LIVE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "GEOMETRY_PRECISIONS",
    "PRECISION_GLYPH",
    "PRECISION_BLOCK",
    "PRECISION_MANUAL",
    "DETECTORS",
    "DETECTOR_MANUAL",
    "PROFILE_KEYS",
    "DEFAULT_RENDER_DPI",
    "MIN_RENDER_DPI",
    "MAX_RENDER_DPI",
]

_STATUS_LIST = ", ".join(f"'{value}'" for value in JOB_STATUSES)
_PRECISION_LIST = ", ".join(f"'{value}'" for value in GEOMETRY_PRECISIONS)


class RedactionJob(Base, UUIDMixin, TimestampMixin):
    """One redaction of one work item under one profile."""

    __tablename__ = "redaction_jobs"

    __table_args__ = (
        CheckConstraint(
            f"status IN ({_STATUS_LIST})",
            name="ck_rj_status_known",
        ),
        CheckConstraint(
            f"render_dpi BETWEEN {MIN_RENDER_DPI} AND {MAX_RENDER_DPI}",
            name="ck_rj_dpi_bounded",
        ),
        # The one that carries the product. See the module header.
        CheckConstraint(
            "status <> 'COMPLETED' OR ("
            "output_file_id IS NOT NULL AND output_sha256 IS NOT NULL "
            "AND manifest_file_id IS NOT NULL AND leak_check_passed IS TRUE "
            "AND approved_at IS NOT NULL)",
            name="ck_rj_completed_is_sealed",
        ),
        # A failure the operator cannot read is a support ticket. FAILED
        # without a reason is the state a generic `except` clause produces,
        # so the database refuses it.
        CheckConstraint(
            "status <> 'FAILED' OR failure_reason IS NOT NULL",
            name="ck_rj_failed_has_reason",
        ),
        Index("ix_redaction_jobs_workspace_status", "workspace_id", "status"),
        Index("ix_redaction_jobs_work_item", "work_item_id"),
        Index("ix_redaction_jobs_organization", "organization_id"),
        # ARCH-02: every tenant-scoped read goes through both columns.
        Index(
            "ix_redaction_jobs_live",
            "workspace_id",
            "created_at",
            postgresql_where=text(
                "status IN ('DETECTING', 'REVIEW', 'APPLYING')"
            ),
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    work_item_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("work_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
        nullable=False,
    )
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=text(f"'{JOB_STATUS_DETECTING}'")
    )
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    render_dpi: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text(str(DEFAULT_RENDER_DPI))
    )
    restore_text_layer: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    output_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
        nullable=True,
    )
    output_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    manifest_file_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("uploaded_files.id", ondelete="RESTRICT"),
        nullable=True,
    )

    #: NULL until the check has run. Tri-state on purpose: FALSE is "it ran
    #: and found a leak", NULL is "it has not run", and collapsing the two
    #: into a boolean default of false would make an un-run check
    #: indistinguishable from a failed one on a FAILED row.
    leak_check_passed: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    #: Counts and page numbers from `LeakReport.summary()`. Never content.
    leak_check_detail: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )

    #: `manifest.input_digest`. Present from the moment apply starts, so a
    #: re-apply of an unchanged job is decidable without re-rendering.
    input_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    approved_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    regions: Mapped[list["RedactionRegion"]] = relationship(
        "RedactionRegion",
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def is_live(self) -> bool:
        return self.status in LIVE_JOB_STATUSES

    @property
    def is_sealed(self) -> bool:
        """True only when the row satisfies `ck_rj_completed_is_sealed`."""
        return (
            self.status == JOB_STATUS_COMPLETED
            and self.output_file_id is not None
            and bool(self.output_sha256)
            and self.manifest_file_id is not None
            and self.leak_check_passed is True
            and self.approved_at is not None
        )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RedactionJob {self.id} status={self.status} "
            f"profile={self.profile_key}>"
        )


class RedactionRegion(Base, UUIDMixin):
    """One rectangle. Carries geometry and provenance, never the matched text."""

    __tablename__ = "redaction_regions"

    __table_args__ = (
        CheckConstraint("x1 > x0 AND y1 > y0", name="ck_rr_box_ordered"),
        CheckConstraint("page_number >= 1", name="ck_rr_page_positive"),
        CheckConstraint(
            f"geometry_precision IN ({_PRECISION_LIST})",
            name="ck_rr_precision_known",
        ),
        # A manual box is an accountable human act. An anonymous one is a
        # rectangle nobody can be asked about, which is exactly the row an
        # audit wants and exactly the row a bug produces.
        CheckConstraint(
            "detector <> 'manual' OR created_by_user_id IS NOT NULL",
            name="ck_rr_manual_has_author",
        ),
        CheckConstraint(
            "confidence >= 0 AND confidence <= 1",
            name="ck_rr_confidence_unit_interval",
        ),
        Index("ix_rr_job_page", "job_id", "page_number"),
        Index("ix_redaction_regions_organization", "organization_id"),
        Index("ix_redaction_regions_workspace", "workspace_id"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("redaction_jobs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # ARCH-02. Denormalised onto the child deliberately: every isolation
    # predicate in this codebase is written against both columns, and a child
    # table that can only be scoped by joining its parent is a child table
    # somebody eventually queries without the join.
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    #: PDF points, origin bottom-left. numeric(10,4) matches the rounding
    #: `manifest.input_digest` applies before hashing; see its header for why
    #: the two must agree.
    x0: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    y0: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    x1: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    y1: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)

    detector: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    geometry_precision: Mapped[str] = mapped_column(String(8), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    #: HMAC-SHA256 of the matched text under the ARCH-07 key lifecycle.
    #: NULL for manual regions: there is no matched text, only a rectangle a
    #: person drew, and a digest of "" would be a constant that looks like
    #: evidence.
    token_digest: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    toggled_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    job: Mapped["RedactionJob"] = relationship(
        "RedactionJob", back_populates="regions"
    )

    @property
    def is_approximate(self) -> bool:
        """The box was widened to a whole OCR block. The reviewer must see this."""
        return self.geometry_precision == PRECISION_BLOCK

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<RedactionRegion {self.id} page={self.page_number} "
            f"detector={self.detector} precision={self.geometry_precision}>"
        )