"""ARCH-40 — review assignment. The hub's only table.

WHY THERE IS EXACTLY ONE TABLE HERE
===================================

The unified review hub is a projection over `document_verifications`,
`assertion_evaluations` and `anomaly_findings`, served by the SQL view
`review_queue_items`. It stores no items of its own: a fourth store would be a
write path that can drift from its sources, and a drifted queue hands a
reviewer an item that no longer exists.

Assignment is the one fact the three sources cannot hold. None of them has an
assignee column, adding one to each would put the same concept in three places
with three migrations, and the hub needs to filter and sort on it in one query.

THE COMPOSITE FOREIGN KEY IS THE ACCESS SWEEP
=============================================

`(assignee_user_id, workspace_id)` references
`workspace_members (user_id, workspace_id)` — the pair
`uq_user_workspace_membership` already makes unique — with ON DELETE CASCADE.

That is the answer to "what happens when an assignee loses workspace access":
the membership row is deleted, and the assignment goes with it, in the same
statement, with no job to schedule and nothing to remember. It is a sweep
performed by the database.

The alternative, a query-time filter, was rejected. A filtered-out assignment
is still a row asserting that a person owns an item they cannot open. Every
report, export and support query that reads the table directly would then
disagree with the hub about who owns what, and the disagreement would only
surface when it mattered.

The same constraint carries a second invariant for free: an assignee must be a
member of the workspace the item belongs to. A CHECK cannot express that — it
sees one row and cannot consult `workspace_members` — which is exactly the
reasoning ARCH-38 recorded when it gave `work_items` its
`(id, workspace_id)` unique key.

WHY `item_id` HAS NO FOREIGN KEY
================================

It is polymorphic across three tables, so no single FK can cover it.
`uq_review_assignments_kind_item` keeps one assignment per item, the CHECK on
`kind` keeps the discriminator honest, and `review_service` resolves the item
through the view — which is workspace-scoped — before writing. An assignment
whose item was deleted is invisible to the hub because the view no longer
produces that row, and `sweep_orphans` removes it on the next pass.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, UUIDMixin

#: ARCH40-S1:review-kinds. Mirrors ck_review_assignments_kind_known and the
#: `kind` column of the review_queue_items view. verify_arch40 gate A6 asserts
#: the three agree.
REVIEW_KIND_EXTRACTION: str = "EXTRACTION"
REVIEW_KIND_ASSERTION: str = "ASSERTION"
REVIEW_KIND_ANOMALY: str = "ANOMALY"
#: ARCH42-S1:review-kind-merge. Entity merge proposals (entity_merge_candidates);
#: arch42_step1_entity_graph widens the CHECK and the view to match.
REVIEW_KIND_MERGE: str = "MERGE"
#: ARCH43-S1:review-kind-split. Packet split plans (packet_splits);
#: arch43_step1_case_intelligence widens the CHECK and the view to match.
REVIEW_KIND_SPLIT: str = "SPLIT"
#: ARCH44-S1:review-kind-table. Extracted tables whose figures do not reconcile
#: (extracted_tables); arch44_step1_table_intelligence widens the CHECK and the view.
REVIEW_KIND_TABLE: str = "TABLE"
#: ARCH45-S1:review-kind-corroboration. Comparisons with open material
#: discrepancies (corroboration_runs); arch45_step1_corroboration widens the
#: CHECK and the view.
REVIEW_KIND_CORROBORATION: str = "CORROBORATION"
#: ARCH46-S1:review-kind-obligation. Extracted obligations read with a doubt
#: (obligations.review = PENDING); arch46_step1_obligations widens the CHECK
#: and the view.
REVIEW_KIND_OBLIGATION: str = "OBLIGATION"
#: ARCH47-S1:review-kind-posting. ERP postings that failed, were rejected, were
#: acknowledged with different figures, or whose outcome is unknown
#: (erp_postings.state); arch47_step1_erp_posting widens the CHECK and the view.
REVIEW_KIND_POSTING: str = "POSTING"

REVIEW_KINDS: tuple[str, ...] = (
    REVIEW_KIND_EXTRACTION,
    REVIEW_KIND_ASSERTION,
    REVIEW_KIND_ANOMALY,
    REVIEW_KIND_MERGE,
    REVIEW_KIND_SPLIT,
    REVIEW_KIND_TABLE,
    REVIEW_KIND_CORROBORATION,
    REVIEW_KIND_OBLIGATION,
    REVIEW_KIND_POSTING,
)

_KIND_SQL_IN = ", ".join(f"'{kind}'" for kind in REVIEW_KINDS)


class ReviewAssignment(Base, UUIDMixin):
    """One review item owned by one workspace member."""

    __tablename__ = "review_assignments"

    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_SQL_IN})", name="ck_review_assignments_kind_known"),
        UniqueConstraint("kind", "item_id", name="uq_review_assignments_kind_item"),
        Index(
            "ix_review_assignments_workspace_assignee",
            "workspace_id",
            "assignee_user_id",
        ),
        # Declared in arch40_step1_settings_review with raw DDL so the name is
        # exact. Repeating it here as a ForeignKeyConstraint would make
        # SQLAlchemy emit a second, differently-named constraint on any
        # create_all path.
        {"comment": "ARCH-40 unified review hub: assignment only, never items."},
    )

    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    #: No single-column FK to users. The composite key onto workspace_members
    #: is stronger: it asserts membership, not merely existence.
    assignee_user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), nullable=False
    )

    assigned_by_user_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<ReviewAssignment {self.kind}/{self.item_id} "
            f"-> {self.assignee_user_id}>"
        )


__all__ = [
    "REVIEW_KINDS",
    "REVIEW_KIND_ANOMALY",
    "REVIEW_KIND_ASSERTION",
    "REVIEW_KIND_EXTRACTION",
    "ReviewAssignment",
]
