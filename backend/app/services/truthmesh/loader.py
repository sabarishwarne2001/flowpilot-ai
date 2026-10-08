"""TruthMesh's reads from the database. Every query carries the workspace predicate.

Documents enter the mesh once extraction has finished (status COMPLETED with an extraction).
Centroids come from `document_chunks` through `app.db.chunk_scope.document_centroids` (the one
helper that may reach that table); semantic neighbours are found in memory for a full build (exact
cosine over the workspace's centroids) and in SQL (`<=>` over `mesh_nodes.centroid`) for one
document.
"""

from __future__ import annotations

import uuid
from typing import Any, Iterable, Optional, Sequence

import numpy as np
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.models.document_role import DocumentRole
from app.models.truthmesh import MeshLink, MeshNode
from app.models.work_item import WorkItem
from app.services.truthmesh import vocabulary as v
from app.services.truthmesh.facts import DocSource, Twin
from app.services.truthmesh.linker import LinkDraft

MESH_STATUSES = ("COMPLETED",)
#: A number a document's TEXT mentions is stored in `referenced_identifiers` with this mark, so one
#: array-overlap query finds both kinds of reference.
TEXT_MARK = "~"


def _classification(entities: Any) -> Optional[str]:
    if not isinstance(entities, dict):
        return None
    details = entities.get("classification_details")
    if isinstance(details, dict) and details.get("document_classification"):
        return str(details["document_classification"])
    value = entities.get("document_classification")
    return str(value) if value else None


def eligible_ids(db: Session, workspace_id: uuid.UUID, *, limit: int = v.MAX_NODES_PER_BUILD) -> list[uuid.UUID]:
    rows = db.execute(
        select(WorkItem.id).where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.status.in_(MESH_STATUSES),
            WorkItem.extracted_entities.is_not(None),
        ).order_by(WorkItem.created_at.desc(), WorkItem.id).limit(limit)
    ).scalars().all()
    return list(rows)


def sources(db: Session, workspace_id: uuid.UUID, work_item_ids: Sequence[uuid.UUID]) -> list[DocSource]:
    """What each document's twin is read from, centroids included."""
    from app.db.chunk_scope import document_centroids

    if not work_item_ids:
        return []
    rows = db.execute(
        select(
            WorkItem.id, WorkItem.original_filename, WorkItem.extracted_entities,
            func.substr(func.coalesce(WorkItem.extracted_text, ""), 1, v.TEXT_SCAN_CHARS).label("text"),
            DocumentRole.role, DocumentRole.document_number, DocumentRole.currency, DocumentRole.total_micros,
            DocumentRole.document_date,
        )
        .outerjoin(DocumentRole, (DocumentRole.work_item_id == WorkItem.id)
                   & (DocumentRole.workspace_id == WorkItem.workspace_id))
        .where(WorkItem.workspace_id == workspace_id, WorkItem.id.in_(list(work_item_ids)),
               WorkItem.status.in_(MESH_STATUSES))
    ).all()
    centroids = document_centroids(db, workspace_id=workspace_id, work_item_ids=[r.id for r in rows])
    out = []
    for r in rows:
        entities = r.extracted_entities if isinstance(r.extracted_entities, dict) else {}
        out.append(DocSource(
            work_item_id=r.id, filename=r.original_filename or str(r.id), entities=entities, text=r.text or "",
            classification=_classification(entities), role=r.role, role_number=r.document_number,
            role_currency=r.currency, role_total_micros=r.total_micros, role_date=r.document_date,
            centroid=centroids.get(r.id),
        ))
    return out


def twin_from_node(node: MeshNode) -> Twin:
    """A stored node read back as a twin, for linking and conflicts without re-reading documents."""
    from datetime import date

    facts = list(node.facts or [])
    meta = next((f for f in facts if f.get("concept") == "_twin"), {})
    due = meta.get("due_date")
    return Twin(
        work_item_id=node.work_item_id, kind=node.kind, kind_label=node.kind_label, rank=node.rank,
        title=node.title, filename=meta.get("filename") or node.title, document_number=node.document_number,
        counterparty=node.counterparty, currency=node.currency, amount_micros=node.amount_micros,
        net_amount_micros=node.net_amount_micros, document_date=node.document_date,
        effective_date=node.effective_date, end_date=node.end_date,
        due_date=date.fromisoformat(due) if due else None, identifiers=list(node.identifiers or []),
        referenced_identifiers=[r for r in (node.referenced_identifiers or []) if not r.startswith(TEXT_MARK)],
        text_references=[r[1:] for r in (node.referenced_identifiers or []) if r.startswith(TEXT_MARK)],
        parties=list(node.parties or []),
        party_names=dict(meta.get("party_names") or {}), payee_account=meta.get("payee_account"),
        facts=[f for f in facts if f.get("concept") != "_twin"], terms=dict(node.terms or {}),
        centroid=None,
    )


def nodes(db: Session, workspace_id: uuid.UUID, work_item_ids: Optional[Iterable[uuid.UUID]] = None) -> list[MeshNode]:
    query = select(MeshNode).where(MeshNode.workspace_id == workspace_id)
    if work_item_ids is not None:
        wanted = list(work_item_ids)
        if not wanted:
            return []
        query = query.where(MeshNode.work_item_id.in_(wanted))
    return list(db.execute(query).scalars())


def links(db: Session, workspace_id: uuid.UUID, touching: Optional[Iterable[uuid.UUID]] = None) -> list[MeshLink]:
    query = select(MeshLink).where(MeshLink.workspace_id == workspace_id)
    if touching is not None:
        wanted = list(touching)
        if not wanted:
            return []
        query = query.where(MeshLink.source_work_item_id.in_(wanted) | MeshLink.target_work_item_id.in_(wanted))
    return list(db.execute(query).scalars())


def draft_of(link: MeshLink) -> LinkDraft:
    return LinkDraft(source=link.source_work_item_id, target=link.target_work_item_id, relation=link.relation,
                     directed=link.directed, strength=float(link.strength), method=link.method,
                     signals=list(link.signals or []))


def neighbourhood(db: Session, workspace_id: uuid.UUID, start: Iterable[uuid.UUID], *,
                  hops: int = v.NEIGHBOURHOOD_HOPS, cap: int = v.NEIGHBOURHOOD_MAX) -> set[uuid.UUID]:
    """Documents within `hops` links of `start` (bounded)."""
    seen = set(start)
    frontier = set(start)
    for _ in range(hops):
        if not frontier or len(seen) >= cap:
            break
        rows = db.execute(
            select(MeshLink.source_work_item_id, MeshLink.target_work_item_id).where(
                MeshLink.workspace_id == workspace_id,
                MeshLink.source_work_item_id.in_(frontier) | MeshLink.target_work_item_id.in_(frontier),
            )
        ).all()
        nxt = {x for row in rows for x in row} - seen
        seen |= set(list(nxt)[: max(0, cap - len(seen))])
        frontier = nxt
    return seen


def identifier_candidates(db: Session, workspace_id: uuid.UUID, twin: Twin) -> set[uuid.UUID]:
    """Nodes that share an identifier with `twin` in either direction, or a party with an agreement."""
    found: set[uuid.UUID] = set()
    mine = list(twin.identifiers)
    refs = list(set(twin.referenced_identifiers) | set(twin.text_references))
    conditions = []
    if mine:
        conditions.append(MeshNode.identifiers.overlap(mine))
        conditions.append(MeshNode.referenced_identifiers.overlap(mine + [TEXT_MARK + m for m in mine]))
    if refs:
        conditions.append(MeshNode.identifiers.overlap(refs))
    if twin.referenced_identifiers:
        conditions.append(MeshNode.referenced_identifiers.overlap(list(twin.referenced_identifiers)))
    if twin.parties:
        conditions.append(MeshNode.parties.overlap(list(twin.parties)) & MeshNode.kind.in_(list(v.AGREEMENT_KINDS)))
        if twin.kind in v.AGREEMENT_KINDS:
            conditions.append(MeshNode.parties.overlap(list(twin.parties)))
    for condition in conditions:
        found |= set(db.execute(
            select(MeshNode.work_item_id).where(MeshNode.workspace_id == workspace_id, condition).limit(500)
        ).scalars())
    found.discard(twin.work_item_id)
    return found


def semantic_neighbours_sql(db: Session, workspace_id: uuid.UUID, work_item_id: uuid.UUID,
                            k: int = v.SEMANTIC_NEIGHBOURS) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    rows = db.execute(text(
        "SELECT n.work_item_id, 1 - (n.centroid <=> o.centroid) AS sim FROM mesh_nodes n, mesh_nodes o "
        "WHERE o.work_item_id = :id AND o.workspace_id = :w AND n.workspace_id = :w AND n.work_item_id <> :id "
        "AND n.centroid IS NOT NULL AND o.centroid IS NOT NULL ORDER BY n.centroid <=> o.centroid LIMIT :k"
    ), {"id": work_item_id, "w": workspace_id, "k": k}).all()
    return {tuple(sorted((work_item_id, r.work_item_id), key=str)): float(r.sim)  # type: ignore[misc]
            for r in rows if r.sim is not None and float(r.sim) >= v.SEMANTIC_FLOOR}


def semantic_neighbours_memory(twins: Sequence[Twin], k: int = v.SEMANTIC_NEIGHBOURS
                               ) -> dict[tuple[uuid.UUID, uuid.UUID], float]:
    """Exact top-k cosine neighbours among twins with centroids, in blocks (no n^2 Python loop)."""
    with_vectors = [t for t in twins if t.centroid is not None]
    if len(with_vectors) < 2:
        return {}
    matrix = np.asarray([t.centroid for t in with_vectors], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    unit = matrix / norms
    out: dict[tuple[uuid.UUID, uuid.UUID], float] = {}
    block = 512
    top = min(k + 1, len(with_vectors))
    for start in range(0, len(with_vectors), block):
        sims = unit[start:start + block] @ unit.T
        for row in range(sims.shape[0]):
            i = start + row
            sims[row, i] = -1.0
            best = np.argpartition(-sims[row], top - 1)[:top]
            for j in best:
                similarity = float(sims[row, j])
                if j == i or similarity < v.SEMANTIC_FLOOR:
                    continue
                key = tuple(sorted((with_vectors[i].work_item_id, with_vectors[j].work_item_id), key=str))
                out[key] = max(out.get(key, 0.0), round(similarity, 4))  # type: ignore[index]
    return out


def party_frequency(db: Session, workspace_id: uuid.UUID) -> tuple[dict[str, int], int]:
    rows = db.execute(text(
        "SELECT p, count(*) FROM mesh_nodes, unnest(parties) AS p WHERE workspace_id = :w GROUP BY p"
    ), {"w": workspace_id}).all()
    total = db.execute(select(func.count()).select_from(MeshNode).where(MeshNode.workspace_id == workspace_id)
                       ).scalar_one()
    return {r[0]: int(r[1]) for r in rows}, int(total)


def obligations_for(db: Session, workspace_id: uuid.UUID, work_item_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    from app.models.obligations import Obligation

    if not work_item_ids:
        return {}
    rows = db.execute(
        select(Obligation.id, Obligation.work_item_id, Obligation.title, Obligation.due_date, Obligation.kind)
        .where(Obligation.workspace_id == workspace_id, Obligation.work_item_id.in_(list(work_item_ids)),
               Obligation.state == "OPEN")
        .limit(500)
    ).all()
    out: dict[uuid.UUID, list[dict]] = {}
    for r in rows:
        out.setdefault(r.work_item_id, []).append(
            {"id": r.id, "title": r.title, "due_date": r.due_date.isoformat() if r.due_date else None, "kind": r.kind})
    return out


__all__ = ["TEXT_MARK", "draft_of", "eligible_ids", "identifier_candidates", "links", "neighbourhood", "nodes",
           "obligations_for", "party_frequency", "semantic_neighbours_memory", "semantic_neighbours_sql",
           "sources", "twin_from_node"]
