"""ARCH44-S1:memory — column roles learned per table layout (ARCH-41 extraction memory, extended).

A reviewer who sets a column's role ("Withdrawal Amt." is DEBIT) teaches the
workspace: the (layout, header, role) row gains a confirmation and every other
role recorded for that header gains a contradiction. A learned role is applied
to new tables of the same layout -- the same header labels in the same order --
only once its Wilson lower bound (ARCH-41's z = 1.6449) reaches 0.5 with at
least three confirmations: three unanimous reviewers, never one.

The layout key is the table's own header signature, so learning works whether
or not the document also belongs to an ARCH-41 layout template; when it does,
the template is recorded on the mapping so the two memories can be read
together.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Optional

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.tables import TableColumnMapping
from app.services.extraction_memory.anchors import wilson_lower
from app.services.tables import vocabulary as v


def decide(rows: Iterable[tuple[str, int, int]]) -> Optional[str]:
    """(role, confirmations, contradictions) rows of ONE header -> the role to apply, or None."""
    best: Optional[tuple[float, int, str]] = None
    for role, confirmations, contradictions in rows:
        if confirmations < v.MAPPING_MIN_SUPPORT:
            continue
        lower = wilson_lower(confirmations, confirmations + contradictions)
        if lower >= v.MAPPING_APPLY_WILSON and (best is None or (lower, confirmations) > best[:2]):
            best = (lower, confirmations, role)
    return best[2] if best else None


def learned_for(db: Session, workspace_id: uuid.UUID, layout_key: str) -> dict[str, str]:
    rows = db.execute(select(TableColumnMapping.header_key, TableColumnMapping.role, TableColumnMapping.confirmations,
                             TableColumnMapping.contradictions)
                      .where(TableColumnMapping.workspace_id == workspace_id,
                             TableColumnMapping.layout_key == layout_key)).all()
    by_header: dict[str, list[tuple[str, int, int]]] = {}
    for header, role, confirmations, contradictions in rows:
        by_header.setdefault(header, []).append((role, int(confirmations), int(contradictions)))
    out = {}
    for header, candidates in by_header.items():
        role = decide(candidates)
        if role:
            out[header] = role
    return out


def template_of(db: Session, work_item_id: uuid.UUID) -> Optional[uuid.UUID]:
    from app.models.extraction_memory import ExtractionTemplateMember

    member = db.get(ExtractionTemplateMember, work_item_id)
    return member.template_id if member is not None else None


def learn(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, layout_key: str, header_key: str,
          role: str, actor_user_id: Optional[uuid.UUID], template_id: Optional[uuid.UUID] = None) -> TableColumnMapping:
    if role not in v.ROLES:
        raise ValueError(f"unknown column role {role!r}")
    if not header_key.strip():
        raise ValueError("a column without a header label cannot be learned")
    now = datetime.now(timezone.utc)
    row = db.execute(select(TableColumnMapping).where(
        TableColumnMapping.workspace_id == workspace_id, TableColumnMapping.layout_key == layout_key,
        TableColumnMapping.header_key == header_key, TableColumnMapping.role == role).with_for_update()).scalar_one_or_none()
    if row is None:
        row = TableColumnMapping(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                                 layout_key=layout_key, header_key=header_key, role=role, confirmations=0,
                                 contradictions=0, template_id=template_id)
        db.add(row)
    row.confirmations = int(row.confirmations or 0) + 1
    row.updated_by_user_id = actor_user_id
    row.updated_at = now
    if template_id and not row.template_id:
        row.template_id = template_id
    db.execute(update(TableColumnMapping).where(
        TableColumnMapping.workspace_id == workspace_id, TableColumnMapping.layout_key == layout_key,
        TableColumnMapping.header_key == header_key, TableColumnMapping.role != role)
        .values(contradictions=TableColumnMapping.contradictions + 1, updated_at=now)
        .execution_options(synchronize_session=False))
    db.flush()
    return row


def mappings_for(db: Session, workspace_id: uuid.UUID, layout_key: str) -> list[TableColumnMapping]:
    return list(db.execute(select(TableColumnMapping).where(
        TableColumnMapping.workspace_id == workspace_id, TableColumnMapping.layout_key == layout_key)
        .order_by(TableColumnMapping.header_key, TableColumnMapping.confirmations.desc())).scalars())


__all__ = ["decide", "learn", "learned_for", "mappings_for", "template_of"]
