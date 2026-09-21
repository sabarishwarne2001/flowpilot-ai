"""ARCH-40 — the review hub's read model.

One query against the `review_queue_items` view, joined to the assignment
table, the work item (for a filename and a retention hold) and
`work_item_tags`. It returns `ReviewItem` rows; nothing else in the product
reads the view.

WHY A VIEW AND NOT A TABLE
==========================

The hub is a projection. Its three sources already hold every fact it shows,
and a fourth store would be a write path that drifts — the first time it
drifted, a reviewer would resolve an item that no longer exists.

The prompt allows a materialized view or a narrow index as the fallback if a
union cannot be made fast enough. Step 1 took the index route:
`ix_dv_workspace_status_created`, the partial
`ix_ae_workspace_triage_created` and
`ix_anomaly_findings_workspace_status_created` make each arm of the union an
index scan bounded by `workspace_id`. A materialized view would need a refresh
job and would serve a reviewer a queue that is minutes stale, which is exactly
wrong for a surface whose whole purpose is that two people do not resolve the
same item.

THE WORKSPACE PREDICATE
=======================

`workspace_id = :workspace_id` is applied in `_base_select`, once, on the
outer query. Every caller in this module goes through it, including the count
query and the single-item load used by `resolve`. Gate B2 removes it as a
mutant; mutant M3 is the same removal in the resolve path.

The view itself is NOT workspace-filtered — it cannot be, having no caller
context — which is why nothing outside this module may read it.

RETENTION HOLDS
===============

A document under an ARCH-20/38 retention hold is flagged on the item. A
reviewer resolving an item on a held document needs to know, because the
document cannot be deleted or exported while the hold stands, and a resolution
that assumes otherwise produces a promise the product will not keep.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import Integer, String, and_, func, literal_column, select, text
from sqlalchemy.orm import Session

from app.services.review import vocabulary as vocab


@dataclass
class ReviewItem:
    """The stable projection every source is flattened into."""

    kind: str
    item_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    work_item_id: Optional[uuid.UUID]
    document_name: Optional[str]
    headline: str
    severity: str
    severity_rank: int
    confidence: Optional[Decimal]
    created_at: datetime
    age_seconds: int
    status: str
    resolved_at: Optional[datetime]
    resolved_by_user_id: Optional[uuid.UUID]
    assignee_user_id: Optional[uuid.UUID]
    assignee_email: Optional[str]
    under_retention_hold: bool
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "item_id": str(self.item_id),
            "work_item_id": str(self.work_item_id) if self.work_item_id else None,
            "document_name": self.document_name,
            "headline": self.headline,
            "severity": self.severity,
            "confidence": float(self.confidence) if self.confidence is not None else None,
            "created_at": self.created_at.isoformat(),
            "age_seconds": self.age_seconds,
            "status": self.status,
            "assignee_user_id": (
                str(self.assignee_user_id) if self.assignee_user_id else None
            ),
            "assignee_email": self.assignee_email,
            "under_retention_hold": self.under_retention_hold,
            "tags": list(self.tags),
        }


@dataclass
class ReviewPage:
    items: list[ReviewItem]
    total: int
    counts_by_kind: dict[str, int]
    page: int
    page_size: int


class ReviewQueryError(ValueError):
    """A filter value the hub will not accept."""


_VIEW = text(vocab.VIEW_NAME)


def _validate(
    *,
    kinds: Optional[Sequence[str]],
    severities: Optional[Sequence[str]],
    status: str,
) -> None:
    for kind in kinds or ():
        if kind not in vocab.KINDS:
            raise ReviewQueryError(
                f"'{kind}' is not a review kind. Expected one of: "
                f"{', '.join(vocab.KINDS)}."
            )
    for severity in severities or ():
        if severity not in vocab.SEVERITIES:
            raise ReviewQueryError(
                f"'{severity}' is not a severity. Expected one of: "
                f"{', '.join(vocab.SEVERITIES)}."
            )
    if status not in vocab.STATUSES:
        raise ReviewQueryError(
            f"'{status}' is not a review status. Expected one of: "
            f"{', '.join(vocab.STATUSES)}."
        )


def _queue_cte(workspace_id: uuid.UUID):
    """The view, bounded to one workspace. The only entry point to it."""
    from sqlalchemy import column, table

    queue = table(
        vocab.VIEW_NAME,
        column("kind", String),
        column("item_id"),
        column("organization_id"),
        column("workspace_id"),
        column("work_item_id"),
        column("headline", String),
        column("severity", String),
        column("severity_rank", Integer),
        column("confidence"),
        column("created_at"),
        column("status", String),
        column("resolved_at"),
        column("resolved_by_user_id"),
    )
    # ARCH40-S1:hub-workspace-predicate. Removing this is mutant M3 and gate
    # B2; it is the only thing standing between one tenant's queue and
    # another's.
    return (
        select(queue)
        .where(queue.c.workspace_id == workspace_id)
        .cte("scoped_review_queue")
    )


def query_reviews(
    db: Session,
    *,
    workspace_id: uuid.UUID,
    kinds: Optional[Sequence[str]] = None,
    severities: Optional[Sequence[str]] = None,
    status: str = vocab.STATUS_OPEN,
    work_item_id: Optional[uuid.UUID] = None,
    tag: Optional[str] = None,
    assignee_user_id: Optional[uuid.UUID] = None,
    unassigned_only: bool = False,
    min_age_seconds: Optional[int] = None,
    page: int = 1,
    page_size: int = vocab.DEFAULT_PAGE_SIZE,
) -> ReviewPage:
    """One page of the queue, plus the per-kind counts for its header."""
    _validate(kinds=kinds, severities=severities, status=status)

    page = max(1, int(page))
    page_size = max(1, min(int(page_size), vocab.MAX_PAGE_SIZE))

    from app.models.ingestion import RetentionHold, WorkItemTag
    from app.models.review import ReviewAssignment
    from app.models.user import User
    from app.models.work_item import WorkItem

    scoped = _queue_cte(workspace_id)

    assignment = (
        select(
            ReviewAssignment.kind.label("a_kind"),
            ReviewAssignment.item_id.label("a_item_id"),
            ReviewAssignment.assignee_user_id.label("a_user_id"),
        )
        .where(ReviewAssignment.workspace_id == workspace_id)
        .subquery()
    )

    held = (
        select(RetentionHold.work_item_id.label("h_work_item_id"))
        .where(
            RetentionHold.workspace_id == workspace_id,
            RetentionHold.released_at.is_(None),
        )
        .distinct()
        .subquery()
    )

    stmt = (
        select(
            scoped.c.kind,
            scoped.c.item_id,
            scoped.c.organization_id,
            scoped.c.workspace_id,
            scoped.c.work_item_id,
            scoped.c.headline,
            scoped.c.severity,
            scoped.c.severity_rank,
            scoped.c.confidence,
            scoped.c.created_at,
            scoped.c.status,
            scoped.c.resolved_at,
            scoped.c.resolved_by_user_id,
            WorkItem.original_filename.label("document_name"),
            assignment.c.a_user_id.label("assignee_user_id"),
            User.email.label("assignee_email"),
            held.c.h_work_item_id.isnot(None).label("under_retention_hold"),
        )
        .select_from(scoped)
        .outerjoin(WorkItem, WorkItem.id == scoped.c.work_item_id)
        .outerjoin(
            assignment,
            and_(
                assignment.c.a_kind == scoped.c.kind,
                assignment.c.a_item_id == scoped.c.item_id,
            ),
        )
        .outerjoin(User, User.id == assignment.c.a_user_id)
        .outerjoin(held, held.c.h_work_item_id == scoped.c.work_item_id)
        .where(scoped.c.status == status)
    )

    if kinds:
        stmt = stmt.where(scoped.c.kind.in_(list(kinds)))
    if severities:
        stmt = stmt.where(scoped.c.severity.in_(list(severities)))
    if work_item_id is not None:
        stmt = stmt.where(scoped.c.work_item_id == work_item_id)
    if assignee_user_id is not None:
        stmt = stmt.where(assignment.c.a_user_id == assignee_user_id)
    if unassigned_only:
        stmt = stmt.where(assignment.c.a_user_id.is_(None))
    if min_age_seconds is not None:
        stmt = stmt.where(
            scoped.c.created_at
            <= func.now() - func.make_interval(0, 0, 0, 0, 0, 0, int(min_age_seconds))
        )
    if tag:
        tagged = (
            select(WorkItemTag.work_item_id)
            .where(
                WorkItemTag.workspace_id == workspace_id,
                WorkItemTag.tag == tag,
            )
            .scalar_subquery()
        )
        stmt = stmt.where(scoped.c.work_item_id.in_(tagged))

    counted = stmt.subquery()
    total = int(
        db.execute(select(func.count()).select_from(counted)).scalar_one() or 0
    )

    counts_rows = db.execute(
        select(counted.c.kind, func.count()).select_from(counted).group_by(counted.c.kind)
    ).all()
    counts_by_kind = {kind: 0 for kind in vocab.KINDS}
    for kind, count in counts_rows:
        counts_by_kind[str(kind)] = int(count)

    # ARCH40-S1:hub-severity-sort. Severity first, then age, so the oldest
    # high-severity item is at the top. item_id breaks ties so paging is
    # stable across requests. Reversing this is mutant M4.
    ordered = (
        stmt.order_by(
            scoped.c.severity_rank.asc(),
            scoped.c.created_at.asc(),
            scoped.c.item_id.asc(),
        )
        .limit(page_size)
        .offset((page - 1) * page_size)
    )

    rows = db.execute(ordered).all()

    item_ids = [row.item_id for row in rows]
    work_item_ids = [row.work_item_id for row in rows if row.work_item_id]
    tags_by_work_item: dict[uuid.UUID, list[str]] = {}
    if work_item_ids:
        for work_item_id_value, tag_value in db.execute(
            select(WorkItemTag.work_item_id, WorkItemTag.tag).where(
                WorkItemTag.workspace_id == workspace_id,
                WorkItemTag.work_item_id.in_(work_item_ids),
            )
        ).all():
            tags_by_work_item.setdefault(work_item_id_value, []).append(str(tag_value))

    now = db.execute(select(func.now())).scalar_one()
    items = [
        ReviewItem(
            kind=str(row.kind),
            item_id=row.item_id,
            organization_id=row.organization_id,
            workspace_id=row.workspace_id,
            work_item_id=row.work_item_id,
            document_name=row.document_name,
            headline=str(row.headline),
            severity=str(row.severity),
            severity_rank=int(row.severity_rank),
            confidence=row.confidence,
            created_at=row.created_at,
            age_seconds=max(0, int((now - row.created_at).total_seconds())),
            status=str(row.status),
            resolved_at=row.resolved_at,
            resolved_by_user_id=row.resolved_by_user_id,
            assignee_user_id=row.assignee_user_id,
            assignee_email=row.assignee_email,
            under_retention_hold=bool(row.under_retention_hold),
            tags=sorted(tags_by_work_item.get(row.work_item_id, [])),
        )
        for row in rows
    ]
    assert len(items) == len(item_ids)
    return ReviewPage(
        items=items,
        total=total,
        counts_by_kind=counts_by_kind,
        page=page,
        page_size=page_size,
    )


def load_item(
    db: Session, *, workspace_id: uuid.UUID, kind: str, item_id: uuid.UUID
) -> Optional[ReviewItem]:
    """One item, workspace-scoped. Used by resolve and by assignment.

    Goes through the same `_queue_cte`, so an item from another workspace is
    not merely hidden from the list — it does not exist to any hub operation.
    """
    if kind not in vocab.KINDS:
        raise ReviewQueryError(f"'{kind}' is not a review kind.")

    scoped = _queue_cte(workspace_id)
    row = db.execute(
        select(scoped).where(scoped.c.kind == kind, scoped.c.item_id == item_id)
    ).first()
    if row is None:
        return None

    return ReviewItem(
        kind=str(row.kind),
        item_id=row.item_id,
        organization_id=row.organization_id,
        workspace_id=row.workspace_id,
        work_item_id=row.work_item_id,
        document_name=None,
        headline=str(row.headline),
        severity=str(row.severity),
        severity_rank=int(row.severity_rank),
        confidence=row.confidence,
        created_at=row.created_at,
        age_seconds=0,
        status=str(row.status),
        resolved_at=row.resolved_at,
        resolved_by_user_id=row.resolved_by_user_id,
        assignee_user_id=None,
        assignee_email=None,
        under_retention_hold=False,
        tags=[],
    )


__all__ = [
    "ReviewItem",
    "ReviewPage",
    "ReviewQueryError",
    "load_item",
    "query_reviews",
]
