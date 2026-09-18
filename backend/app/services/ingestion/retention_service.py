"""ARCH-38 — retention holds, and the ARCH-20 retention floor.

WHAT THE BLUEPRINT ASKED FOR AND WHAT EXISTED
=============================================

The specification says "deletes respect ARCH-20 retention holds". ARCH-20 ships
`retention_policies` -- a minimum age below which the compliance sweeper will
not purge -- `erased_subjects`, `compliance_exports` and `data_residency_region`.
It ships no hold of any kind, and a repository-wide search finds no model whose
name ends in Hold. The phrase had no referent, so ARCH-38 gives it one rather
than quietly reinterpreting it.

TWO DIFFERENT THINGS, BOTH ENFORCED
===================================

**A hold** is a named, audited, indefinite refusal to delete a specific
document or everything in a workspace, placed by a person because of
litigation, an audit or an investigation. It is released by a person. It is
`retention_holds`.

**The policy floor** is `retention_policies.work_item_retention_days`: the
organization has told the platform to keep documents for at least N days. A
bulk delete that removed a two-day-old document from an organization with a
90-day retention policy would contradict the policy the tenant set, so
`blocking_reason` refuses that too.

The two are reported separately, because "a legal hold is on this document" and
"your organization keeps documents for 90 days" are different answers requiring
different actions from whoever hit Delete.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.compliance import RetentionPolicy
from app.models.ingestion import RetentionHold
from app.models.work_item import WorkItem

HOLD_BLOCK_CODE = "RETENTION_HOLD"
POLICY_BLOCK_CODE = "RETENTION_POLICY"


@dataclass(frozen=True)
class DeletionBlock:
    """Why one document may not be deleted."""

    work_item_id: uuid.UUID
    code: str
    reason: str


def _now() -> datetime:
    return datetime.now(timezone.utc)


def active_holds(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_ids: Iterable[uuid.UUID],
) -> dict[uuid.UUID, RetentionHold]:
    """Holds covering these documents, by document id.

    A workspace-wide hold covers every document in it, so it is expanded here
    rather than being something each caller has to remember to check.
    """
    ids = list(work_item_ids)
    if not ids:
        return {}

    rows = list(
        db.execute(
            select(RetentionHold).where(
                RetentionHold.organization_id == organization_id,
                RetentionHold.released_at.is_(None),
            )
        ).scalars()
    )

    covered: dict[uuid.UUID, RetentionHold] = {}
    workspace_wide = [
        row
        for row in rows
        if row.work_item_id is None and row.workspace_id == workspace_id
    ]
    if workspace_wide:
        for work_item_id in ids:
            covered[work_item_id] = workspace_wide[0]

    id_set = set(ids)
    for row in rows:
        if row.work_item_id is not None and row.work_item_id in id_set:
            covered[row.work_item_id] = row
    return covered


def retention_floor_days(
    db: Session, *, organization_id: uuid.UUID
) -> Optional[int]:
    policy = db.execute(
        select(RetentionPolicy).where(
            RetentionPolicy.organization_id == organization_id
        )
    ).scalar_one_or_none()
    if policy is None:
        return None
    return policy.work_item_retention_days


def blocking_reasons(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_items: Iterable[WorkItem],
) -> dict[uuid.UUID, DeletionBlock]:
    """Every reason a delete must be refused, by document id.

    Empty means every document in the set may be deleted.
    """
    items = list(work_items)
    if not items:
        return {}

    blocks: dict[uuid.UUID, DeletionBlock] = {}

    holds = active_holds(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_ids=[item.id for item in items],
    )
    for work_item_id, hold in holds.items():
        blocks[work_item_id] = DeletionBlock(
            work_item_id=work_item_id,
            code=HOLD_BLOCK_CODE,
            reason=f"A retention hold is in place: {hold.reason}",
        )

    floor = retention_floor_days(db, organization_id=organization_id)
    if floor:
        cutoff = _now() - timedelta(days=floor)
        for item in items:
            if item.id in blocks:
                continue
            created = item.created_at
            if created is None:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created > cutoff:
                blocks[item.id] = DeletionBlock(
                    work_item_id=item.id,
                    code=POLICY_BLOCK_CODE,
                    reason=(
                        f"Your organization's retention policy keeps documents "
                        f"for {floor} days; this one is not old enough."
                    ),
                )
    return blocks


def place_hold(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    work_item_id: Optional[uuid.UUID],
    reason: str,
    reference: Optional[str],
    user_id: Optional[uuid.UUID],
) -> RetentionHold:
    if workspace_id is None and work_item_id is None:
        raise ValueError("A hold must name a workspace or a document.")
    hold = RetentionHold(
        id=uuid.uuid4(),
        organization_id=organization_id,
        workspace_id=workspace_id,
        work_item_id=work_item_id,
        reason=reason[:255],
        reference=(reference[:128] if reference else None),
        placed_by_user_id=user_id,
    )
    db.add(hold)
    db.flush([hold])
    return hold


def release_hold(
    db: Session,
    *,
    organization_id: uuid.UUID,
    hold_id: uuid.UUID,
    user_id: Optional[uuid.UUID],
) -> Optional[RetentionHold]:
    hold = db.execute(
        select(RetentionHold).where(
            RetentionHold.id == hold_id,
            RetentionHold.organization_id == organization_id,
        )
    ).scalar_one_or_none()
    if hold is None:
        return None
    if hold.released_at is None:
        hold.released_at = _now()
        hold.released_by_user_id = user_id
        db.flush([hold])
    return hold


__all__ = [
    "DeletionBlock",
    "HOLD_BLOCK_CODE",
    "POLICY_BLOCK_CODE",
    "active_holds",
    "blocking_reasons",
    "place_hold",
    "release_hold",
    "retention_floor_days",
]
