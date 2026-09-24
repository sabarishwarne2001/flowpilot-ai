"""ARCH42-S1:erasure — ARCH-20 erasure leaves no identifier behind.

Three entry points, one rule: when content is erased, every identifier row
that content evidenced goes with it, and a record left with nothing is removed.

  erase_for_work_items   ARCH-20 `_destroy_documents`: the subject's documents
                         lose their mentions and the edges they evidenced;
                         identifiers and records only they supported go too.
  erase_by_identifier    ARCH-20 `erase_subject`: the record(s) holding the
                         subject's own email (matched by HMAC under every key
                         generation — the value itself is never stored) are
                         removed whole: the subject IS that record.
  erase_cluster          the console's "erase this record" (ADMIN): a patient
                         or a person asks to be forgotten.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.entity_graph import Entity, EntityEdge, EntityIdentifier, EntityMention
from app.services.entities import crypto, graph
from app.services.entities import normalize as n


def erase_for_work_items(db: Session, work_item_ids: Iterable[uuid.UUID]) -> dict[str, int]:
    ids = list(work_item_ids)
    if not ids:
        return {"mentions": 0, "edges": 0, "identifiers": 0, "entities": 0}
    workspaces = set(db.execute(select(EntityMention.workspace_id).where(
        EntityMention.work_item_id.in_(ids)).distinct()).scalars())
    counts = {
        "mentions": db.execute(delete(EntityMention).where(EntityMention.work_item_id.in_(ids))).rowcount or 0,
        "edges": db.execute(delete(EntityEdge).where(EntityEdge.evidence_work_item_id.in_(ids))).rowcount or 0,
        "identifiers": 0,
        "entities": 0,
    }
    for workspace_id in workspaces:
        orphans = graph.collect_orphans(db, workspace_id=workspace_id)
        counts["identifiers"] += orphans["identifiers"]
        counts["entities"] += orphans["entities"]
    db.flush()
    return counts


def erase_cluster(db: Session, *, entity_id: uuid.UUID) -> dict[str, int]:
    root = graph.root_of(db, entity_id)
    return graph.delete_cluster(db, root.id)


def erase_by_identifier(
    db: Session, *, workspace_ids: Iterable[uuid.UUID], kind: str, raw_value: str
) -> dict[str, int]:
    value = n.normalize_identifier(kind, raw_value)
    totals = {"entities": 0, "identifiers": 0, "mentions": 0, "edges": 0, "candidates": 0}
    if value is None:
        return totals
    holders = set(db.execute(select(EntityIdentifier.entity_id).where(
        EntityIdentifier.workspace_id.in_(list(workspace_ids)), EntityIdentifier.kind == kind,
        EntityIdentifier.value_hmac.in_(crypto.lookup_digests(kind, value)))).scalars())
    roots: set[uuid.UUID] = set()
    for entity_id in holders:
        if db.get(Entity, entity_id) is not None:
            roots.add(graph.root_of(db, entity_id).id)
    for root_id in roots:
        for key, count in graph.delete_cluster(db, root_id).items():
            totals[key] = totals.get(key, 0) + count
    return totals
