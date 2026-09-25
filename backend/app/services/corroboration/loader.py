"""ARCH45-S1:loader — the documents of a comparison, read from the database.

Everything is read from what earlier milestones stored; nothing is
re-extracted, re-OCR'd or re-resolved:

  work_items.extraction_metadata["pages"]   page geometry (ARCH-12 text-layer
                                            lines for digital pages, PaddleOCR
                                            blocks for scans), else extracted_text
  work_items.extracted_entities             extracted values and line items
  extracted_tables (ARCH-44)                typed tables through tables.service.load
  entity_mentions (ARCH-42)                 live mentions (not superseded, not
                                            rejected), each followed through
                                            merged_into_id to its canonical root

`work_items` has no organization_id; the workspace is the scope (every query
below filters on it).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.services.corroboration import entities as E
from app.services.corroboration import inputs
from app.services.corroboration.engine import DocInput


@dataclass
class Loaded:
    work_item: Any
    tables: list[Any]            # (ExtractedTable row, engine.OutTable or None when rejected / not built)
    mentions: list[E.Mention]
    mention_keys: list[tuple]    # (mention id, root id, decision) for the fingerprint
    doc: Optional[DocInput] = None


def _roots(db: Session, entity_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[uuid.UUID, str]]:
    """entity id -> (canonical root id, the root's display name), one recursive query."""
    if not entity_ids:
        return {}
    rows = db.execute(text(
        "WITH RECURSIVE chain(start_id, id, merged_into_id, display_name, depth) AS ("
        " SELECT e.id, e.id, e.merged_into_id, e.display_name, 0 FROM entities e WHERE e.id = ANY(:ids)"
        " UNION ALL"
        " SELECT c.start_id, e.id, e.merged_into_id, e.display_name, c.depth + 1 FROM chain c"
        " JOIN entities e ON e.id = c.merged_into_id WHERE c.depth < 32)"
        " SELECT start_id, id, display_name FROM chain WHERE merged_into_id IS NULL"),
        {"ids": list(entity_ids)}).all()
    return {start: (root, name) for start, root, name in rows}


def mentions_for(db: Session, workspace_id: uuid.UUID,
                 work_item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, tuple[list[E.Mention], list[tuple]]]:
    from app.models.entity_graph import EntityMention

    rows = list(db.execute(select(EntityMention).where(
        EntityMention.workspace_id == workspace_id, EntityMention.work_item_id.in_(list(work_item_ids)),
        EntityMention.superseded_at.is_(None), EntityMention.decision != "REJECTED")
        .order_by(EntityMention.work_item_id, EntityMention.field_path, EntityMention.ordinal)).scalars())
    roots = _roots(db, sorted({m.entity_id for m in rows}))
    out: dict[uuid.UUID, tuple[list[E.Mention], list[tuple]]] = {wid: ([], []) for wid in work_item_ids}
    for m in rows:
        root, name = roots.get(m.entity_id, (m.entity_id, m.surface_name))
        role = (m.role or "").strip().lower() or m.entity_kind.lower()
        out[m.work_item_id][0].append(E.Mention(role, m.entity_kind, str(root), m.surface_name, name))
        out[m.work_item_id][1].append((str(m.id), str(root), m.decision))
    return out


def workspace_currency(db: Session, workspace_id: uuid.UUID) -> Optional[str]:
    from app.models.workspace import Workspace

    return db.execute(select(Workspace.currency).where(Workspace.id == workspace_id)).scalar_one_or_none()


def load(db: Session, workspace_id: uuid.UUID, work_item_ids: Sequence[uuid.UUID], *,
         build_inputs: bool = True) -> list[Loaded]:
    """The documents, in the order asked, with their tables and mentions."""
    from app.models.work_item import WorkItem
    from app.services.tables import service as table_service

    items = {w.id: w for w in db.execute(select(WorkItem).where(
        WorkItem.workspace_id == workspace_id, WorkItem.id.in_(list(work_item_ids)))).scalars()}
    mentions = mentions_for(db, workspace_id, list(items))
    currency = workspace_currency(db, workspace_id) if build_inputs else None
    out: list[Loaded] = []
    for wid in work_item_ids:
        item = items.get(wid)
        if item is None:
            raise LookupError(str(wid))
        tables = [(t, table_service.load(db, t) if build_inputs and t.status != "REJECTED" else None)
                  for t in table_service.tables_of(db, item.id)]
        ms, keys = mentions.get(wid, ([], []))
        loaded = Loaded(item, tables, ms, keys)
        if build_inputs:
            meta = item.extraction_metadata if isinstance(item.extraction_metadata, dict) else {}
            fields = item.extracted_entities if isinstance(item.extracted_entities, dict) else {}
            loaded.doc = inputs.doc_input(doc_id=str(item.id), label=item.original_filename,
                                          pages_meta=meta.get("pages") or [], text=item.extracted_text or "",
                                          fields=fields, tables=[o for _, o in tables if o is not None],
                                          mentions=ms,
                                          workspace_currency=currency)
        out.append(loaded)
    return out


def page_geometry(work_item: Any) -> list[dict]:
    """[{page, width, height}] for the viewer (200-DPI pixels, as the evidence boxes)."""
    meta = work_item.extraction_metadata if isinstance(work_item.extraction_metadata, dict) else {}
    out = []
    for i, p in enumerate(meta.get("pages") or [], start=1):
        out.append({"page": int(p.get("page_number") or i), "width": int(p.get("width") or 0),
                    "height": int(p.get("height") or 0)})
    if not out and work_item.page_count:
        out = [{"page": i, "width": 0, "height": 0} for i in range(1, int(work_item.page_count) + 1)]
    return out


__all__ = ["Loaded", "load", "mentions_for", "page_geometry", "workspace_currency"]
