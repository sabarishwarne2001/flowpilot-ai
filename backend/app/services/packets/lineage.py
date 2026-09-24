"""ARCH43-S1:lineage — parents, children and mention supersession.

Roadmap adjustment 4: ARCH-42 resolved the PACKET's fields into entity
mentions before anyone knew it was a packet. When the packet is split, those
mentions are SUPERSEDED (stamped with the split that replaced them), never
deleted: the evidence stays auditable, Entity 360 stops counting it, and the
children -- each enriched on its own -- are resolved into their own mentions
by the ordinary ARCH-42 path. resolver.resolve_work_item refuses to
re-resolve a split parent, so the nightly sweep cannot resurrect them.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import exists, select, update
from sqlalchemy.orm import Session

from app.models.packets import PacketSplit, PacketSplitSegment
from app.services.packets import vocabulary as v


def is_split_parent(db: Session, work_item_id: uuid.UUID) -> bool:
    return bool(db.execute(select(exists().where(
        PacketSplit.work_item_id == work_item_id, PacketSplit.status == v.STATUS_APPLIED))).scalar())


def supersede_parent_mentions(db: Session, *, split: PacketSplit) -> int:
    from app.models.entity_graph import EntityMention

    result = db.execute(
        update(EntityMention)
        .where(EntityMention.work_item_id == split.work_item_id, EntityMention.workspace_id == split.workspace_id,
               EntityMention.superseded_at.is_(None))
        .values(superseded_at=datetime.now(timezone.utc), superseded_by_split_id=split.id)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount or 0)


def lineage_of(db: Session, *, workspace_id: uuid.UUID, work_item_id: uuid.UUID) -> dict[str, Any]:
    from app.models.work_item import WorkItem

    item = db.execute(select(WorkItem).where(WorkItem.id == work_item_id, WorkItem.workspace_id == workspace_id)).scalar_one_or_none()
    if item is None:
        raise LookupError("work item not found")
    parent: Optional[WorkItem] = db.get(WorkItem, item.parent_work_item_id) if item.parent_work_item_id else None
    split = db.execute(select(PacketSplit).where(PacketSplit.work_item_id == work_item_id,
                                                 PacketSplit.status.in_(v.LIVE_STATUSES))).scalar_one_or_none()
    children = []
    if split is not None:
        rows = db.execute(select(PacketSplitSegment, WorkItem).outerjoin(
            WorkItem, WorkItem.id == PacketSplitSegment.child_work_item_id).where(
            PacketSplitSegment.split_id == split.id).order_by(PacketSplitSegment.ordinal)).all()
        children = [{"ordinal": s.ordinal, "page_start": s.page_start, "page_end": s.page_end,
                     "document_type": s.document_type, "work_item_id": w.id if w else None,
                     "original_filename": w.original_filename if w else None,
                     "pipeline_stage": w.pipeline_stage if w else None} for s, w in rows]
    return {
        "work_item_id": item.id,
        "parent": None if parent is None else {"work_item_id": parent.id, "original_filename": parent.original_filename,
                                               "page_start": item.parent_page_start, "page_end": item.parent_page_end},
        "split": None if split is None else {"split_id": split.id, "status": split.status},
        "children": children,
    }


__all__ = ["is_split_parent", "lineage_of", "supersede_parent_mentions"]
