"""ARCH-42 — Entity Resolution & Document Knowledge Graph endpoints.

    GET    /workspaces/{wid}/entities/potential                 upsell count   [VIEWER]  (not gated)
    GET    /workspaces/{wid}/entities/summary                   headline       [VIEWER]
    GET    /workspaces/{wid}/entities                           records        [VIEWER]
    GET    /workspaces/{wid}/entities/merge-candidates          proposals      [VIEWER]
    GET    /workspaces/{wid}/entities/{eid}                     Entity 360     [VIEWER]
    GET    /workspaces/{wid}/entities/{eid}/graph               explorer       [VIEWER]
    PATCH  /workspaces/{wid}/entities/{eid}                     rename         [CONTRIBUTOR]
    POST   /workspaces/{wid}/entities/{eid}/merge               merge          [CONTRIBUTOR]
    POST   /workspaces/{wid}/entities/{eid}/unmerge             undo a merge   [CONTRIBUTOR]
    POST   /workspaces/{wid}/entities/{eid}/split               split          [CONTRIBUTOR]
    DELETE /workspaces/{wid}/entities/{eid}                     erase record   [ADMIN]
    GET    /workspaces/{wid}/work-items/{id}/entities           chips          [VIEWER]
    POST   /workspaces/{wid}/work-items/{id}/entities/resolve   re-resolve     [CONTRIBUTOR]

ARCH42-S1:api. Every route but /potential is gated on capability.entity_graph
-> 402 CAPABILITY_REQUIRED through `capability_gate.require_capability`.
/potential returns one count computed from the tenant's own documents.

SEARCH BY IDENTIFIER WITHOUT STORING IT
=======================================

`?q=` that validates as an email, PAN, GSTIN, IBAN, passport, Aadhaar or phone
is looked up by HMAC under every key generation: "every document tied to PAN
AAACR5055K" works although the PAN is stored nowhere in plaintext. Anything
else is a trigram name search. Identifiers leave this API only in their
MASKED form.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.api import capability_gate
from app.api.deps import RequireWorkspaceRole, TenantContext, get_db
from app.core import entitlements
from app.models.entity_graph import (
    Entity,
    EntityEdge,
    EntityIdentifier,
    EntityMatchModel,
    EntityMention,
    EntityMergeCandidate,
)
from app.models.work_item import WorkItem
from app.models.workspace import WorkspaceRole
from app.schemas.entities import (
    CandidateRow,
    DocumentRow,
    Entity360,
    EntityChip,
    EntityGraph,
    EntityList,
    EntityPotential,
    EntityRow,
    EntitySummary,
    GraphEdge,
    GraphNode,
    IdentifierRow,
    MemberRow,
    MergeRequest,
    ModelRow,
    ObligationsPlaceholder,
    RelationshipRow,
    RenameRequest,
    ResolveResult,
    SplitRequest,
    WorkItemEntities,
)
from app.services.entities import annotations as ann
from app.services.entities import crypto, erasure, graph, resolver
from app.services.entities import normalize as n
from app.services.entities import vocabulary as v

router = APIRouter(tags=["Entity Graph"])

RequireViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
CAPABILITY = entitlements.ENTITY_GRAPH_CAPABILITY
MAX_GRAPH_NODES = 150
SEARCH_KINDS = (v.ID_EMAIL, v.ID_PAN, v.ID_GSTIN, v.ID_IBAN, v.ID_AADHAAR, v.ID_PASSPORT, v.ID_PHONE)

ROOTS_SQL = """
WITH RECURSIVE r(id, root) AS (
    SELECT id, id FROM entities WHERE workspace_id = :ws AND status = 'ACTIVE'
    UNION ALL
    SELECT e.id, r.root FROM entities e JOIN r ON e.merged_into_id = r.id
)
"""


def _gate(db: Session, context: TenantContext, operation: str) -> None:
    capability_gate.require_capability(db, context=context, capability_key=CAPABILITY, operation=operation)


def _assert_workspace(context: TenantContext, workspace_id: uuid.UUID) -> None:
    if context.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workspace not found.")


def _root_or_404(db: Session, workspace_id: uuid.UUID, entity_id: uuid.UUID) -> Entity:
    entity = db.get(Entity, entity_id)
    if entity is None or entity.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")
    return graph.root_of(db, entity.id)


def _rows(db: Session, workspace_id: uuid.UUID, root_ids: list[uuid.UUID]) -> dict[uuid.UUID, EntityRow]:
    if not root_ids:
        return {}
    params = {"ws": workspace_id, "roots": root_ids}
    stats = {r.root: r for r in db.execute(text(ROOTS_SQL + """
        SELECT r.root, count(m.id) AS mentions, count(DISTINCT m.work_item_id) AS documents,
               count(DISTINCT r.id) - 1 AS merged
        FROM r LEFT JOIN entity_mentions m ON m.entity_id = r.id AND m.superseded_at IS NULL
        WHERE r.root = ANY(:roots) GROUP BY r.root"""), params).all()}
    kinds: dict[uuid.UUID, set[str]] = defaultdict(set)
    for root, kind in db.execute(text(ROOTS_SQL + """
        SELECT DISTINCT r.root, i.kind FROM r JOIN entity_identifiers i ON i.entity_id = r.id
        WHERE r.root = ANY(:roots)"""), params).all():
        kinds[root].add(kind)
    out: dict[uuid.UUID, EntityRow] = {}
    for entity in db.execute(select(Entity).where(Entity.id.in_(root_ids))).scalars():
        s = stats.get(entity.id)
        out[entity.id] = EntityRow(
            id=entity.id, kind=entity.kind, display_name=entity.display_name,
            documents=int(s.documents) if s else 0, mentions=int(s.mentions) if s else 0,
            identifier_kinds=sorted(kinds.get(entity.id, set())), merged_records=int(s.merged) if s else 0,
            last_seen_at=entity.last_seen_at,
        )
    return out


def _identifier_query(q: str) -> list[tuple[str, str]]:
    return [(kind, value) for kind in SEARCH_KINDS if (value := n.normalize_identifier(kind, q)) is not None]


def _candidate_rows(db: Session, candidates: list[EntityMergeCandidate]) -> list[CandidateRow]:
    ids = {c.left_entity_id for c in candidates} | {c.right_entity_id for c in candidates}
    names = {e.id: e.display_name for e in db.execute(select(Entity).where(Entity.id.in_(ids))).scalars()} if ids else {}
    return [CandidateRow(
        id=c.id, left_id=c.left_entity_id, left_name=names.get(c.left_entity_id, "—"),
        right_id=c.right_entity_id, right_name=names.get(c.right_entity_id, "—"), kind=c.entity_kind,
        reason=c.reason, probability=float(c.match_probability), weight=float(c.match_weight),
        conflict_kinds=list(c.conflict_kinds or []), status=c.status,
        comparison={k: (int(x) if x is not None else None) for k, x in (c.comparison or {}).items()},
        created_at=c.created_at,
    ) for c in candidates]


# ---------------------------------------------------------------------------


@router.get("/workspaces/{workspace_id}/entities/potential", response_model=EntityPotential)
def potential(workspace_id: uuid.UUID, db: Session = Depends(get_db),
              context: TenantContext = Depends(RequireViewer)) -> EntityPotential:
    _assert_workspace(context, workspace_id)
    keys = sorted(set(ann.BUILTIN_ANNOTATIONS) | {
        k for entry in ann.PRESET_ENTITY_ANNOTATIONS.values() for k in entry["properties"]})
    count = db.execute(text(
        "SELECT count(*) FROM work_items WHERE workspace_id = :ws AND extracted_entities IS NOT NULL "
        "AND json_typeof(extracted_entities::json) = 'object' AND extracted_entities::jsonb ?| CAST(:keys AS text[])"
    ), {"ws": workspace_id, "keys": keys}).scalar_one()
    return EntityPotential(documents=int(count))


@router.get("/workspaces/{workspace_id}/entities/summary", response_model=EntitySummary)
def summary(workspace_id: uuid.UUID, db: Session = Depends(get_db),
            context: TenantContext = Depends(RequireViewer)) -> EntitySummary:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.summary")
    counts = {k: 0 for k in v.ENTITY_KINDS}
    for kind, count in db.execute(select(Entity.kind, func.count()).where(
            Entity.workspace_id == workspace_id, Entity.status == v.ENTITY_ACTIVE).group_by(Entity.kind)).all():
        counts[kind] = int(count)
    documents = db.execute(select(func.count(func.distinct(EntityMention.work_item_id))).where(
        EntityMention.workspace_id == workspace_id, EntityMention.superseded_at.is_(None))).scalar_one()  # ARCH43-S1:live-mentions
    open_rows = db.execute(select(EntityMergeCandidate.reason, func.count()).where(
        EntityMergeCandidate.workspace_id == workspace_id, EntityMergeCandidate.status == v.CANDIDATE_OPEN,
    ).group_by(EntityMergeCandidate.reason)).all()
    by_reason = {r: int(c) for r, c in open_rows}
    models = []
    for kind in v.MODELLED_KINDS:
        row = db.execute(select(EntityMatchModel).where(
            EntityMatchModel.workspace_id == workspace_id, EntityMatchModel.entity_kind == kind,
            EntityMatchModel.status == v.MODEL_ACTIVE)).scalar_one_or_none()
        models.append(ModelRow(kind=kind, version=row.version if row else None,
                               source=row.source if row else v.MODEL_SOURCE_PRIOR,
                               pair_count=row.pair_count if row else 0, converged=row.converged if row else False,
                               fitted_at=row.fitted_at if row else None))
    return EntitySummary(counts_by_kind=counts, records=sum(counts.values()), documents_linked=int(documents),
                         open_reviews=sum(by_reason.values()), open_conflicts=by_reason.get(v.REASON_CONFLICT, 0),
                         models=models)


@router.get("/workspaces/{workspace_id}/entities", response_model=EntityList)
def list_entities(workspace_id: uuid.UUID, kind: Optional[str] = Query(default=None), q: Optional[str] = Query(default=None, max_length=300),
                  page: int = Query(default=1, ge=1), page_size: int = Query(default=25, ge=1, le=100),
                  db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> EntityList:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.list")
    if kind is not None and kind not in v.ENTITY_KINDS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"kind must be one of {', '.join(v.ENTITY_KINDS)}.")
    by_identifier = False
    statement = select(Entity.id).where(Entity.workspace_id == workspace_id, Entity.status == v.ENTITY_ACTIVE)
    if kind:
        statement = statement.where(Entity.kind == kind)
    if q and q.strip():
        identifiers = _identifier_query(q.strip())
        if identifiers:
            holders = set()
            for id_kind, value in identifiers:
                holders |= set(db.execute(select(EntityIdentifier.entity_id).where(
                    EntityIdentifier.workspace_id == workspace_id, EntityIdentifier.kind == id_kind,
                    EntityIdentifier.value_hmac.in_(crypto.lookup_digests(id_kind, value)))).scalars())
            roots = {graph.root_of(db, h).id for h in holders}
            statement = statement.where(Entity.id.in_(roots or [uuid.uuid4()]))
            by_identifier = True
        else:
            folded = n.fold(q.strip())
            statement = statement.where(or_(Entity.normalized_name.ilike(f"%{folded}%"),
                                            func.similarity(Entity.normalized_name, folded) >= v.TRIGRAM_THRESHOLD))
    total = db.execute(select(func.count()).select_from(statement.subquery())).scalar_one()
    ids = list(db.execute(statement.order_by(Entity.last_seen_at.desc().nulls_last(), Entity.id)
                          .offset((page - 1) * page_size).limit(page_size)).scalars())
    rows = _rows(db, workspace_id, ids)
    return EntityList(items=[rows[i] for i in ids if i in rows], total=int(total), page=page, page_size=page_size,
                      matched_by_identifier=by_identifier)


@router.get("/workspaces/{workspace_id}/entities/merge-candidates", response_model=list[CandidateRow])
def merge_candidates(workspace_id: uuid.UUID, candidate_status: str = Query(default=v.CANDIDATE_OPEN, alias="status"),
                     db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> list[CandidateRow]:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.merge_candidates")
    if candidate_status not in v.CANDIDATE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unknown status.")
    rows = list(db.execute(select(EntityMergeCandidate).where(
        EntityMergeCandidate.workspace_id == workspace_id, EntityMergeCandidate.status == candidate_status,
    ).order_by(EntityMergeCandidate.created_at.desc()).limit(200)).scalars())
    return _candidate_rows(db, rows)


@router.get("/workspaces/{workspace_id}/entities/{entity_id}", response_model=Entity360)
def entity_360(workspace_id: uuid.UUID, entity_id: uuid.UUID, db: Session = Depends(get_db),
               context: TenantContext = Depends(RequireViewer)) -> Entity360:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.detail")
    root = _root_or_404(db, workspace_id, entity_id)
    members = graph.cluster_ids(db, root.id)
    row = _rows(db, workspace_id, [root.id])[root.id]
    identifiers = [IdentifierRow(id=i.id, kind=i.kind, display=crypto.open_sealed(i.display_ciphertext) or "•••",
                                 derived=i.derived, entity_id=i.entity_id)
                   for i in db.execute(select(EntityIdentifier).where(EntityIdentifier.entity_id.in_(members))
                                       .order_by(EntityIdentifier.kind)).scalars()]
    documents = [DocumentRow(
        mention_id=m.id, work_item_id=m.work_item_id, filename=w.original_filename, role=m.role, field_path=m.field_path,
        decision=m.decision, method=m.method, probability=float(m.match_probability) if m.match_probability is not None else None,
        entity_id=m.entity_id, created_at=w.created_at)
        for m, w in db.execute(select(EntityMention, WorkItem).join(WorkItem, WorkItem.id == EntityMention.work_item_id)
                               .where(EntityMention.entity_id.in_(members), EntityMention.superseded_at.is_(None))  # ARCH43-S1:live-mentions
                               .order_by(WorkItem.created_at.desc()).limit(500)).all()]
    rel: dict[tuple[str, str, uuid.UUID], set[uuid.UUID]] = defaultdict(set)
    for edge in db.execute(select(EntityEdge).where(or_(EntityEdge.src_entity_id.in_(members),
                                                        EntityEdge.dst_entity_id.in_(members)))).scalars():
        outgoing = edge.src_entity_id in members
        other = graph.root_of(db, edge.dst_entity_id if outgoing else edge.src_entity_id)
        if other.id != root.id:
            rel[(edge.relation, "out" if outgoing else "in", other.id)].add(edge.evidence_work_item_id)
    others = {e.id: e for e in db.execute(select(Entity).where(Entity.id.in_({k[2] for k in rel}))).scalars()} if rel else {}
    relationships = [RelationshipRow(relation=r, direction=d, other_id=o, other_kind=others[o].kind,
                                     other_name=others[o].display_name, documents=len(docs))
                     for (r, d, o), docs in sorted(rel.items(), key=lambda x: (-len(x[1]), x[0][0])) if o in others]
    mention_counts = dict(db.execute(select(EntityMention.entity_id, func.count()).where(
        EntityMention.entity_id.in_(members), EntityMention.superseded_at.is_(None)).group_by(EntityMention.entity_id)).all())
    member_rows = [MemberRow(id=e.id, display_name=e.display_name, merged_at=e.merged_at, merge_reason=e.merge_reason,
                             mentions=int(mention_counts.get(e.id, 0)))
                   for e in db.execute(select(Entity).where(Entity.id.in_(members), Entity.id != root.id)).scalars()]
    candidates = list(db.execute(select(EntityMergeCandidate).where(
        EntityMergeCandidate.status == v.CANDIDATE_OPEN,
        or_(EntityMergeCandidate.left_entity_id.in_(members), EntityMergeCandidate.right_entity_id.in_(members)))).scalars())
    return Entity360(entity=row, first_seen_at=root.first_seen_at, split_from_id=root.split_from_id,
                     identifiers=identifiers, documents=documents, relationships=relationships, members=member_rows,
                     open_candidates=_candidate_rows(db, candidates),
                     obligations=ObligationsPlaceholder(milestone=v.OBLIGATIONS_MILESTONE))


@router.get("/workspaces/{workspace_id}/entities/{entity_id}/graph", response_model=EntityGraph)
def entity_graph(workspace_id: uuid.UUID, entity_id: uuid.UUID, depth: int = Query(default=2, ge=1, le=3),
                 db: Session = Depends(get_db), context: TenantContext = Depends(RequireViewer)) -> EntityGraph:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.graph")
    root = _root_or_404(db, workspace_id, entity_id)
    root_of = {r.id: r.root for r in db.execute(text(ROOTS_SQL + "SELECT id, root FROM r"), {"ws": workspace_id}).all()}
    adjacency: dict[uuid.UUID, dict[tuple[uuid.UUID, str], set]] = defaultdict(lambda: defaultdict(set))
    weights: dict[tuple[uuid.UUID, uuid.UUID, str], set] = defaultdict(set)
    for edge in db.execute(select(EntityEdge).where(EntityEdge.workspace_id == workspace_id)).scalars():
        s, d = root_of.get(edge.src_entity_id), root_of.get(edge.dst_entity_id)
        if s is None or d is None or s == d:
            continue
        weights[(s, d, edge.relation)].add(edge.evidence_work_item_id)
        adjacency[s][(d, edge.relation)].add(edge.evidence_work_item_id)
        adjacency[d][(s, edge.relation)].add(edge.evidence_work_item_id)
    level = {root.id: 0}
    frontier, truncated = [root.id], False
    for d in range(1, depth + 1):
        nxt = []
        for node in frontier:
            for (other, _rel) in adjacency.get(node, {}):
                if other not in level:
                    if len(level) >= MAX_GRAPH_NODES:
                        truncated = True
                        break
                    level[other] = d
                    nxt.append(other)
        frontier = nxt
    rows = _rows(db, workspace_id, list(level))
    nodes = [GraphNode(id=i, kind=rows[i].kind, label=rows[i].display_name, documents=rows[i].documents, depth=level[i])
             for i in level if i in rows]
    edges = [GraphEdge(source=s, target=d, relation=r, weight=len(docs))
             for (s, d, r), docs in weights.items() if s in level and d in level]
    return EntityGraph(root_id=root.id, nodes=nodes, edges=edges, truncated=truncated)


@router.patch("/workspaces/{workspace_id}/entities/{entity_id}", response_model=EntityRow)
def rename_entity(workspace_id: uuid.UUID, entity_id: uuid.UUID, body: RenameRequest, db: Session = Depends(get_db),
                  context: TenantContext = Depends(RequireContributor)) -> EntityRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.rename")
    root = _root_or_404(db, workspace_id, entity_id)
    try:
        graph.rename(db, entity_id=root.id, display_name=body.display_name, actor_user_id=context.user_id)
    except graph.GraphError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _rows(db, workspace_id, [root.id])[root.id]


@router.post("/workspaces/{workspace_id}/entities/{entity_id}/merge", response_model=EntityRow)
def merge_entity(workspace_id: uuid.UUID, entity_id: uuid.UUID, body: MergeRequest, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireContributor)) -> EntityRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.merge")
    loser = _root_or_404(db, workspace_id, entity_id)
    winner = _root_or_404(db, workspace_id, body.into_entity_id)
    try:
        survivor = graph.merge(db, loser_id=loser.id, winner_id=winner.id, actor_user_id=context.user_id,
                               reason=v.MERGE_REASON_MANUAL)
    except graph.MergeConflict as exc:
        from app.services.entities import fellegi_sunter as fs

        candidate_id = graph.propose(db, left=loser, right=winner, decision=fs.Decision("REVIEW", v.REASON_CONFLICT, 1.0, 0.0),
                                     gamma={}, conflict_kinds=exc.kinds)
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "code": "ENTITY_MERGE_CONFLICT", "message": str(exc), "conflict_kinds": exc.kinds,
            "candidate_id": str(candidate_id) if candidate_id else None}) from exc
    except graph.GraphError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _rows(db, workspace_id, [survivor.id])[survivor.id]


@router.post("/workspaces/{workspace_id}/entities/{entity_id}/unmerge", response_model=EntityRow)
def unmerge_entity(workspace_id: uuid.UUID, entity_id: uuid.UUID, db: Session = Depends(get_db),
                   context: TenantContext = Depends(RequireContributor)) -> EntityRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.unmerge")
    entity = db.get(Entity, entity_id)
    if entity is None or entity.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Record not found.")
    try:
        graph.unmerge(db, entity_id=entity.id, actor_user_id=context.user_id)
    except graph.GraphError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _rows(db, workspace_id, [entity.id])[entity.id]


@router.post("/workspaces/{workspace_id}/entities/{entity_id}/split", response_model=EntityRow)
def split_entity(workspace_id: uuid.UUID, entity_id: uuid.UUID, body: SplitRequest, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireContributor)) -> EntityRow:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.split")
    root = _root_or_404(db, workspace_id, entity_id)
    try:
        new = graph.split(db, root_id=root.id, mention_ids=body.mention_ids, actor_user_id=context.user_id)
    except graph.GraphError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    db.commit()
    return _rows(db, workspace_id, [new.id])[new.id]


@router.delete("/workspaces/{workspace_id}/entities/{entity_id}", status_code=status.HTTP_200_OK, response_model=dict[str, int])
def erase_entity(workspace_id: uuid.UUID, entity_id: uuid.UUID, db: Session = Depends(get_db),
                 context: TenantContext = Depends(RequireAdmin)) -> dict[str, int]:
    from app.models.audit_log import AuditAction, AuditOutcome, AuditResourceType
    from app.services import audit_service

    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.erase")
    root = _root_or_404(db, workspace_id, entity_id)
    counts = erasure.erase_cluster(db, entity_id=root.id)
    audit_service.record(db, organization_id=context.organization_id, workspace_id=workspace_id, actor_id=context.user_id,
                         resource_type=AuditResourceType.WORKSPACE, resource_id=workspace_id, action=AuditAction.ERASED,
                         outcome=AuditOutcome.ALLOWED,
                         details={"entity_graph": {"operation": "erase", "entity": str(root.id), **counts}})
    db.commit()
    return counts


@router.get("/workspaces/{workspace_id}/work-items/{work_item_id}/entities", response_model=WorkItemEntities)
def work_item_entities(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                       context: TenantContext = Depends(RequireViewer)) -> WorkItemEntities:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.work_item")
    mentions = list(db.execute(select(EntityMention).where(
        EntityMention.work_item_id == work_item_id, EntityMention.workspace_id == workspace_id)
        .order_by(EntityMention.field_path, EntityMention.ordinal)).scalars())
    chips = []
    for m in mentions:
        root = graph.root_of(db, m.entity_id)
        chips.append(EntityChip(entity_id=root.id, kind=root.kind, display_name=root.display_name, role=m.role,
                                decision=m.decision, field_path=m.field_path))
    return WorkItemEntities(chips=chips, resolved=bool(mentions))


@router.post("/workspaces/{workspace_id}/work-items/{work_item_id}/entities/resolve", response_model=ResolveResult)
def resolve_work_item(workspace_id: uuid.UUID, work_item_id: uuid.UUID, db: Session = Depends(get_db),
                      context: TenantContext = Depends(RequireContributor)) -> ResolveResult:
    _assert_workspace(context, workspace_id)
    _gate(db, context, "entities.resolve")
    work_item = db.get(WorkItem, work_item_id)
    if work_item is None or work_item.workspace_id != workspace_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found.")
    result = resolver.resolve_work_item(db, work_item=work_item, check_capability=False)
    db.commit()
    return ResolveResult(resolved=bool(result.pop("resolved", False)), detail=result)


__all__ = ["router"]
