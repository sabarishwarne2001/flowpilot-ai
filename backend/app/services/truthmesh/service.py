"""TruthMesh orchestration: builds, persistence, the conflict lifecycle, simulations, read models.

BUILDS
======
`index_document` runs after a document is enriched (post-enrichment dispatch): its twin is read and
stored, it is linked to the documents that share an identifier, a party with an agreement, or a
close centroid, and the conflicts of its neighbourhood (two hops) are recomputed. `build_workspace`
does the same for every document of a workspace (the "Rebuild" button, and the first visit).

PEOPLE'S DECISIONS WIN
======================
A link a person CONFIRMED is kept even when the engine no longer proposes it; one they REJECTED is
never used for conflicts and never re-created as AUTO. A conflict a person DISMISSED stays
dismissed when it is detected again; one they RESOLVED reopens only if it is detected again after
having been auto-resolved. A conflict that is no longer detected (the document was corrected, the
link rejected) is auto-resolved, with the note saying so.
"""

from __future__ import annotations

import csv
import io
import logging
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.truthmesh import MeshConflict, MeshLink, MeshNode, MeshSimulation, MeshState
from app.models.work_item import WorkItem
from app.services.truthmesh import conflicts as C
from app.services.truthmesh import facts as F
from app.services.truthmesh import linker as L
from app.services.truthmesh import loader
from app.services.truthmesh import ripple as R
from app.services.truthmesh import risk as RK
from app.services.truthmesh import vocabulary as v

logger = logging.getLogger("app.services.truthmesh")

OPEN = ("OPEN", "ACKNOWLEDGED")


class MeshError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status_code = code, message, status_code


@dataclass
class BuildResult:
    nodes: int = 0
    links: int = 0
    conflicts_open: int = 0
    conflicts_new: int = 0
    conflicts_resolved: int = 0
    ms: int = 0

    def as_dict(self) -> dict[str, int]:
        return dict(self.__dict__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- nodes

def _node_values(twin: F.Twin) -> dict[str, Any]:
    meta = {"concept": "_twin", "filename": twin.filename, "party_names": twin.party_names,
            "payee_account": twin.payee_account, "due_date": twin.due_date.isoformat() if twin.due_date else None}
    return dict(
        kind=twin.kind, kind_label=twin.kind_label, rank=twin.rank, title=twin.title[:300],
        document_number=(twin.document_number or None) and twin.document_number[:128],
        counterparty=(twin.counterparty or None) and twin.counterparty[:300], currency=twin.currency,
        amount_micros=twin.amount_micros, net_amount_micros=twin.net_amount_micros,
        document_date=twin.document_date, effective_date=twin.effective_date, end_date=twin.end_date,
        facts=[meta, *twin.facts], terms=twin.terms, identifiers=twin.identifiers[:64],
        referenced_identifiers=(twin.referenced_identifiers + [loader.TEXT_MARK + t for t in twin.text_references])[:96],
        parties=[p[:300] for p in twin.parties[:32]], centroid=twin.centroid, fingerprint=twin.fingerprint(),
    )


def _store_twins(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID,
                 twins: Sequence[F.Twin]) -> None:
    if not twins:
        return
    existing = {n.work_item_id: n for n in loader.nodes(db, workspace_id, [t.work_item_id for t in twins])}
    now = _now()
    for twin in twins:
        values = _node_values(twin)
        node = existing.get(twin.work_item_id)
        if node is None:
            db.add(MeshNode(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                            work_item_id=twin.work_item_id, indexed_at=now, **values))
        else:
            for key, value in values.items():
                setattr(node, key, value)
            node.indexed_at = now
            node.updated_at = now
    db.flush()


def _remove_missing(db: Session, workspace_id: uuid.UUID, keep: set[uuid.UUID]) -> None:
    """Nodes whose documents left the mesh (failed reprocessing, deleted) go; their links go with them."""
    stale = [n for n in loader.nodes(db, workspace_id) if n.work_item_id not in keep]
    for node in stale:
        db.execute(delete(MeshLink).where(
            MeshLink.workspace_id == workspace_id,
            (MeshLink.source_work_item_id == node.work_item_id) | (MeshLink.target_work_item_id == node.work_item_id)))
        db.delete(node)
    db.flush()


# --------------------------------------------------------------------------- links

def _store_links(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, drafts: Sequence[L.LinkDraft],
                 replace_touching: Optional[set[uuid.UUID]], full: bool) -> None:
    """Upsert drafts by unordered pair; AUTO links of the scope that were not re-proposed are removed."""
    scope_links = loader.links(db, workspace_id, None if full else (replace_touching or set()))
    by_pair = {frozenset((l.source_work_item_id, l.target_work_item_id)): l for l in scope_links}
    proposed: set[frozenset] = set()
    now = _now()
    for draft in drafts:
        pair = frozenset(draft.pair)
        proposed.add(pair)
        row = by_pair.get(pair)
        if row is None:
            row = MeshLink(id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id,
                           source_work_item_id=draft.source, target_work_item_id=draft.target, status="AUTO")
            db.add(row)
            by_pair[pair] = row
        row.source_work_item_id, row.target_work_item_id = draft.source, draft.target
        row.relation, row.directed, row.method = draft.relation, draft.directed, draft.method
        row.strength = Decimal(str(round(draft.strength, 4)))
        row.signals = draft.signals
        row.updated_at = now
    for pair, row in by_pair.items():
        if pair not in proposed and row.status == "AUTO":
            if full or (replace_touching and pair & replace_touching):
                db.delete(row)
    db.flush()


# --------------------------------------------------------------------------- conflicts

def _reconcile(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID,
               detected: Sequence[C.ConflictDraft], scope: Optional[set[uuid.UUID]]) -> tuple[int, int]:
    query = select(MeshConflict).where(MeshConflict.workspace_id == workspace_id)
    if scope is not None:
        query = query.where(MeshConflict.work_item_ids.overlap(list(scope)))
    existing = {c.fingerprint: c for c in db.execute(query).scalars()}
    now = _now()
    new = 0
    seen: set[str] = set()
    for draft in detected:
        fingerprint = draft.fingerprint
        seen.add(fingerprint)
        row = existing.get(fingerprint)
        if row is None:
            row = db.execute(select(MeshConflict).where(MeshConflict.workspace_id == workspace_id,
                                                        MeshConflict.fingerprint == fingerprint)).scalar_one_or_none()
        if row is None:
            db.add(MeshConflict(
                id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, fingerprint=fingerprint,
                kind=draft.kind, concept=draft.concept, severity=draft.severity, status="OPEN",
                title=draft.title[:300], summary=draft.summary, relation=draft.relation,
                work_item_ids=sorted(set(draft.work_item_ids), key=str), document_values=draft.document_values,
                details=draft.details, exposure_micros=draft.exposure_micros, currency=draft.currency,
                first_seen_at=now, last_seen_at=now))
            new += 1
            continue
        row.title, row.summary, row.severity = draft.title[:300], draft.summary, draft.severity
        row.document_values, row.details = draft.document_values, draft.details
        row.exposure_micros, row.currency, row.relation = draft.exposure_micros, draft.currency, draft.relation
        row.last_seen_at = now
        row.updated_at = now
        if row.status == "RESOLVED" and row.auto_resolved:
            row.status, row.resolved_at, row.auto_resolved, row.resolution_note = "OPEN", None, False, None
            new += 1
    resolved = 0
    for fingerprint, row in existing.items():
        if fingerprint in seen or row.status not in OPEN:
            continue
        if scope is not None and not set(row.work_item_ids) <= scope:
            continue
        row.status, row.auto_resolved, row.resolved_at = "RESOLVED", True, now
        row.resolution_note = "No longer detected: the documents or the links between them changed."
        row.updated_at = now
        resolved += 1
    db.flush()
    return new, resolved


def _refresh_risk(db: Session, workspace_id: uuid.UUID, ids: Optional[set[uuid.UUID]]) -> None:
    query = select(MeshConflict).where(MeshConflict.workspace_id == workspace_id, MeshConflict.status.in_(OPEN))
    if ids is not None:
        query = query.where(MeshConflict.work_item_ids.overlap(list(ids)))
    risks = RK.node_risks(db.execute(query).scalars())
    degree: Counter[uuid.UUID] = Counter()
    for link in loader.links(db, workspace_id, ids):
        if link.status != "REJECTED":
            degree[link.source_work_item_id] += 1
            degree[link.target_work_item_id] += 1
    for node in loader.nodes(db, workspace_id, ids):
        node.risk_score = Decimal(str(risks.get(node.work_item_id, 0.0)))
        node.degree = int(degree.get(node.work_item_id, 0))
    db.flush()


def _refresh_state(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, ms: Optional[int],
                   status: str = "READY", error: Optional[str] = None) -> MeshState:
    nodes = db.execute(select(MeshNode.work_item_id, MeshNode.risk_score, MeshNode.amount_micros)
                       .where(MeshNode.workspace_id == workspace_id)).all()
    links = db.execute(select(func.count()).select_from(MeshLink).where(
        MeshLink.workspace_id == workspace_id, MeshLink.status != "REJECTED")).scalar_one()
    open_conflicts = db.execute(select(func.count()).select_from(MeshConflict).where(
        MeshConflict.workspace_id == workspace_id, MeshConflict.status.in_(OPEN))).scalar_one()
    index = RK.risk_index({n.work_item_id: float(n.risk_score) for n in nodes},
                          {n.work_item_id: n.amount_micros for n in nodes})
    state = db.get(MeshState, workspace_id)
    if state is None:
        state = MeshState(workspace_id=workspace_id, organization_id=organization_id)
        db.add(state)
    state.status = status if nodes or status != "READY" else "EMPTY"
    state.nodes, state.links, state.open_conflicts = len(nodes), int(links), int(open_conflicts)
    state.risk_index = Decimal(str(index))
    if ms is not None:
        state.build_ms = ms
        state.last_built_at = _now()
    state.engine_version = v.ENGINE_VERSION
    state.error = error
    state.updated_at = _now()
    db.flush()
    return state


def _status_map(rows: Iterable[MeshLink]) -> dict[tuple[uuid.UUID, uuid.UUID], str]:
    return {(l.source_work_item_id, l.target_work_item_id): l.status for l in rows}


# --------------------------------------------------------------------------- builds

def build_workspace(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> BuildResult:
    """Read every eligible document, link them all, recompute every conflict. Caller commits."""
    started = time.monotonic()
    ids = loader.eligible_ids(db, workspace_id)
    twins = [F.read(source) for source in loader.sources(db, workspace_id, ids)]
    _remove_missing(db, workspace_id, {t.work_item_id for t in twins})
    _store_twins(db, organization_id=organization_id, workspace_id=workspace_id, twins=twins)
    semantic = loader.semantic_neighbours_memory(twins)
    drafts = L.build_links(twins, semantic=semantic)
    # People's rejections are not re-created; confirmations are kept even if not re-proposed
    # (_store_links removes AUTO links only).
    _store_links(db, organization_id=organization_id, workspace_id=workspace_id, drafts=drafts,
                 replace_touching=None, full=True)
    all_links = loader.links(db, workspace_id)
    usable = [loader.draft_of(l) for l in all_links]
    detected = C.detect(twins, usable, status=_status_map(all_links))
    new, resolved = _reconcile(db, organization_id=organization_id, workspace_id=workspace_id,
                               detected=detected, scope=None)
    _refresh_risk(db, workspace_id, None)
    ms = int((time.monotonic() - started) * 1000)
    state = _refresh_state(db, workspace_id=workspace_id, organization_id=organization_id, ms=ms)
    return BuildResult(nodes=state.nodes, links=state.links, conflicts_open=state.open_conflicts,
                       conflicts_new=new, conflicts_resolved=resolved, ms=ms)


def index_document(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID,
                   work_item_id: uuid.UUID) -> BuildResult:
    """One document in, its links and its neighbourhood's conflicts recomputed. Caller commits."""
    started = time.monotonic()
    found = loader.sources(db, workspace_id, [work_item_id])
    if not found:
        _remove_missing_one(db, workspace_id, work_item_id)
        state = _refresh_state(db, workspace_id=workspace_id, organization_id=organization_id, ms=None)
        return BuildResult(nodes=state.nodes, links=state.links, conflicts_open=state.open_conflicts)
    twin = F.read(found[0])
    _store_twins(db, organization_id=organization_id, workspace_id=workspace_id, twins=[twin])
    candidates = loader.identifier_candidates(db, workspace_id, twin)
    semantic = loader.semantic_neighbours_sql(db, workspace_id, work_item_id)
    candidates |= {x for pair in semantic for x in pair}
    candidates.discard(work_item_id)
    others = [loader.twin_from_node(n) for n in loader.nodes(db, workspace_id, candidates)]
    party_df, n = loader.party_frequency(db, workspace_id)
    drafts = L.build_links([twin, *others], focus={work_item_id}, semantic=semantic, party_df=party_df, n=n)
    _store_links(db, organization_id=organization_id, workspace_id=workspace_id, drafts=drafts,
                 replace_touching={work_item_id}, full=False)
    scope = loader.neighbourhood(db, workspace_id, {work_item_id})
    scope_nodes = loader.nodes(db, workspace_id, scope)
    twins = [loader.twin_from_node(node) for node in scope_nodes]
    scope_links = [l for l in loader.links(db, workspace_id, scope)
                   if l.source_work_item_id in scope and l.target_work_item_id in scope]
    detected = C.detect(twins, [loader.draft_of(l) for l in scope_links], status=_status_map(scope_links),
                        scope=scope)
    new, resolved = _reconcile(db, organization_id=organization_id, workspace_id=workspace_id,
                               detected=detected, scope=scope)
    _refresh_risk(db, workspace_id, scope)
    ms = int((time.monotonic() - started) * 1000)
    state = _refresh_state(db, workspace_id=workspace_id, organization_id=organization_id, ms=ms)
    return BuildResult(nodes=state.nodes, links=state.links, conflicts_open=state.open_conflicts,
                       conflicts_new=new, conflicts_resolved=resolved, ms=ms)


def _remove_missing_one(db: Session, workspace_id: uuid.UUID, work_item_id: uuid.UUID) -> None:
    node = db.execute(select(MeshNode).where(MeshNode.workspace_id == workspace_id,
                                             MeshNode.work_item_id == work_item_id)).scalar_one_or_none()
    if node is not None:
        _remove_missing(db, workspace_id, {n.work_item_id for n in loader.nodes(db, workspace_id)} - {work_item_id})


def mark_building(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID) -> MeshState:
    state = db.get(MeshState, workspace_id)
    if state is None:
        state = MeshState(workspace_id=workspace_id, organization_id=organization_id)
        db.add(state)
    state.status = "BUILDING"
    state.updated_at = _now()
    db.flush()
    return state


# --------------------------------------------------------------------------- decisions

def decide_conflict(db: Session, *, conflict: MeshConflict, status: str, note: Optional[str],
                    actor_id: Optional[uuid.UUID]) -> MeshConflict:
    if status not in ("OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"):
        raise MeshError("INVALID_STATUS", "Status must be OPEN, ACKNOWLEDGED, RESOLVED or DISMISSED.")
    text = (note or "").strip()
    if status in ("RESOLVED", "DISMISSED") and len(text) < 3:
        raise MeshError("REASON_REQUIRED", "Say how it was resolved, or why it is not a conflict (a few words).")
    now = _now()
    conflict.status = status
    conflict.auto_resolved = False
    if status in ("RESOLVED", "DISMISSED"):
        conflict.resolved_at, conflict.resolved_by_user_id, conflict.resolution_note = now, actor_id, text[:2000]
    else:
        conflict.resolved_at, conflict.resolved_by_user_id = None, None
        conflict.resolution_note = text[:2000] or None
    conflict.updated_at = now
    db.flush()
    _refresh_risk(db, conflict.workspace_id, set(conflict.work_item_ids))
    _refresh_state(db, workspace_id=conflict.workspace_id, organization_id=conflict.organization_id, ms=None)
    return conflict


def decide_link(db: Session, *, link: MeshLink, decision: str, actor_id: Optional[uuid.UUID]) -> MeshLink:
    if decision not in ("CONFIRMED", "REJECTED", "AUTO"):
        raise MeshError("INVALID_DECISION", "Decision must be CONFIRMED, REJECTED or AUTO.")
    link.status = decision
    link.decided_by_user_id = actor_id if decision != "AUTO" else None
    link.decided_at = _now() if decision != "AUTO" else None
    link.updated_at = _now()
    db.flush()
    # The conflicts across this link are recomputed at once: a rejected link takes its conflicts away.
    scope = loader.neighbourhood(db, link.workspace_id, {link.source_work_item_id, link.target_work_item_id})
    twins = [loader.twin_from_node(n) for n in loader.nodes(db, link.workspace_id, scope)]
    scope_links = [l for l in loader.links(db, link.workspace_id, scope)
                   if l.source_work_item_id in scope and l.target_work_item_id in scope]
    detected = C.detect(twins, [loader.draft_of(l) for l in scope_links], status=_status_map(scope_links), scope=scope)
    _reconcile(db, organization_id=link.organization_id, workspace_id=link.workspace_id, detected=detected, scope=scope)
    _refresh_risk(db, link.workspace_id, scope)
    _refresh_state(db, workspace_id=link.workspace_id, organization_id=link.organization_id, ms=None)
    return link


# --------------------------------------------------------------------------- simulations

def _clause_context(db: Session, workspace_id: uuid.UUID, origin: uuid.UUID, clause: str) -> Optional[dict]:
    import re

    text = db.execute(select(func.substr(func.coalesce(WorkItem.extracted_text, ""), 1, v.TEXT_SCAN_CHARS))
                      .where(WorkItem.id == origin, WorkItem.workspace_id == workspace_id)).scalar_one_or_none() or ""
    wanted = (clause or "").strip()
    if not wanted:
        return None
    paragraphs = [p.strip() for p in re.split(r"\n(?=\s*(?:clause\s+|section\s+)?\d+(?:\.\d+)*[.)\s])", text, flags=re.I)
                  if p.strip()]
    number = re.match(r"^(?:clause|section|§)?\s*(\d+(?:\.\d+)*)", wanted, re.I)
    found = None
    if number:
        pattern = re.compile(rf"^\s*(?:clause\s+|section\s+)?{re.escape(number.group(1))}(?![\d.]\d)[.)\s]", re.I)
        found = next((p for p in paragraphs if pattern.match(p)), None)
    if found is None:
        words = [w for w in re.findall(r"[a-z]{4,}", wanted.lower())]
        scored = sorted(((sum(w in p.lower() for w in words), p) for p in paragraphs), key=lambda x: -x[0])
        found = scored[0][1] if scored and scored[0][0] > 0 else None
    label = f"Clause {number.group(1)}" if number else "The clause"
    if found is None:
        return {"quote": wanted[:240], "label": label, "clause_type": R.classify_clause(wanted), "found": False}
    return {"quote": " ".join(found.split())[:400], "label": label, "clause_type": R.classify_clause(found),
            "found": True}


def _related_clauses(db: Session, workspace_id: uuid.UUID, origin: uuid.UUID, quote: str,
                     reached: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[dict]]:
    """Passages of reached documents nearest to the invoked clause (stored embeddings, no model)."""
    from app.db.chunk_scope import chunks_of_document, nearest_chunks

    others = [r for r in reached if r != origin]
    if not others or not quote:
        return {}
    chunks = chunks_of_document(db, workspace_id=workspace_id, work_item_id=origin, limit=200)
    probe = quote[:60].lower()
    anchor = next((c for c in chunks if probe and probe in (c.content or "").lower()), chunks[0] if chunks else None)
    if anchor is None or anchor.embedding is None:
        return {}
    out: dict[uuid.UUID, list[dict]] = defaultdict(list)
    for chunk, distance in nearest_chunks(db, workspace_id=workspace_id, embedding=list(anchor.embedding), top_k=12,
                                          work_item_ids=others):
        similarity = 1.0 - float(distance)
        if similarity < 0.5:
            continue
        out[chunk.work_item_id].append({"quote": " ".join((chunk.content or "").split())[:240],
                                        "similarity": round(similarity, 3), "page": chunk.page_number})
    return dict(out)


def simulate(db: Session, *, workspace_id: uuid.UUID, organization_id: uuid.UUID, actor_id: Optional[uuid.UUID],
             work_item_id: uuid.UUID, scenario: str, parameters: dict[str, Any]) -> MeshSimulation:
    if scenario not in v.SCENARIOS:
        raise MeshError("UNKNOWN_SCENARIO", f"Scenario is one of {', '.join(v.SCENARIOS)}.")
    origin = db.execute(select(MeshNode).where(MeshNode.workspace_id == workspace_id,
                                               MeshNode.work_item_id == work_item_id)).scalar_one_or_none()
    if origin is None:
        raise MeshError("NOT_IN_MESH", "This document is not in the mesh yet. Build the mesh first.", 409)
    scope = loader.neighbourhood(db, workspace_id, {work_item_id}, hops=v.RIPPLE_MAX_DEPTH, cap=v.RIPPLE_MAX_NODES)
    twins = {n.work_item_id: loader.twin_from_node(n) for n in loader.nodes(db, workspace_id, scope)}
    scope_links = [l for l in loader.links(db, workspace_id, scope)
                   if l.status != "REJECTED" and l.source_work_item_id in twins and l.target_work_item_id in twins]
    drafts = [loader.draft_of(l) for l in scope_links]
    clause = None
    related: dict[uuid.UUID, list[dict]] = {}
    if scenario in (v.SCENARIO_CLAUSE_INVOKED, v.SCENARIO_TERMINATION):
        wanted = str(parameters.get("clause") or ("termination" if scenario == v.SCENARIO_TERMINATION else ""))
        clause = _clause_context(db, workspace_id, work_item_id, wanted)
        if clause and scenario == v.SCENARIO_CLAUSE_INVOKED:
            try:
                related = _related_clauses(db, workspace_id, work_item_id, clause.get("quote", ""), list(twins))
            except Exception:  # noqa: BLE001 - related passages are an aid; the simulation stands without them
                logger.exception("truthmesh.related_clauses_failed")
    result = R.simulate(origin=work_item_id, scenario=scenario, parameters=parameters, twins=twins, links=drafts,
                        obligations=loader.obligations_for(db, workspace_id, list(twins)), clause=clause,
                        related=related)
    if clause:
        result["clause"] = clause
    summary = result["summary"]
    title = _simulation_title(scenario, parameters, twins[work_item_id], clause)
    row = MeshSimulation(
        id=uuid.uuid4(), organization_id=organization_id, workspace_id=workspace_id, origin_work_item_id=work_item_id,
        created_by_user_id=actor_id, scenario=scenario, title=title[:300], parameters=dict(parameters),
        result=result, documents_affected=int(summary["documents_affected"]),
        exposure_micros=int(summary["financial_exposure_micros"] or 0) or None, currency=summary.get("currency"))
    db.add(row)
    db.flush()
    return row


def _simulation_title(scenario: str, parameters: dict[str, Any], origin: F.Twin, clause: Optional[dict]) -> str:
    if scenario == v.SCENARIO_DELAY:
        return f"{origin.title} delayed by {int(parameters.get('days', 14))} days"
    if scenario == v.SCENARIO_AMOUNT_CHANGE:
        return f"{origin.title} amount {float(parameters.get('percent', 10)):+g}%"
    if scenario == v.SCENARIO_TERMINATION:
        return f"{origin.title} terminated"
    if scenario == v.SCENARIO_PARTY_DEFAULT:
        return f"{parameters.get('party') or origin.counterparty or 'Counterparty'} defaults"
    return f"{(clause or {}).get('label') or 'A clause'} of {origin.title} invoked"


# --------------------------------------------------------------------------- read models

def state(db: Session, workspace_id: uuid.UUID) -> Optional[MeshState]:
    return db.get(MeshState, workspace_id)


def _node_out(node: MeshNode) -> dict[str, Any]:
    meta = next((f for f in (node.facts or []) if f.get("concept") == "_twin"), {})
    return {
        "work_item_id": str(node.work_item_id), "kind": node.kind, "kind_label": node.kind_label, "rank": node.rank,
        "title": node.title, "filename": meta.get("filename") or node.title, "document_number": node.document_number,
        "counterparty": node.counterparty, "currency": node.currency, "amount_micros": node.amount_micros,
        "net_amount_micros": node.net_amount_micros,
        "document_date": node.document_date.isoformat() if node.document_date else None,
        "effective_date": node.effective_date.isoformat() if node.effective_date else None,
        "end_date": node.end_date.isoformat() if node.end_date else None,
        "risk_score": float(node.risk_score), "risk_band": RK.band(float(node.risk_score)), "degree": node.degree,
    }


def _link_out(link: MeshLink) -> dict[str, Any]:
    return {"id": str(link.id), "source": str(link.source_work_item_id), "target": str(link.target_work_item_id),
            "relation": link.relation, "relation_label": v.RELATION_LABELS.get(link.relation, link.relation.lower()),
            "directed": link.directed, "strength": float(link.strength), "method": link.method,
            "signals": link.signals, "status": link.status}


def conflict_out(conflict: MeshConflict) -> dict[str, Any]:
    return {
        "id": str(conflict.id), "kind": conflict.kind, "kind_label": v.CONFLICT_LABELS.get(conflict.kind, conflict.kind),
        "concept": conflict.concept, "severity": conflict.severity, "status": conflict.status,
        "title": conflict.title, "summary": conflict.summary, "relation": conflict.relation,
        "work_item_ids": [str(i) for i in conflict.work_item_ids], "document_values": conflict.document_values,
        "exposure_micros": conflict.exposure_micros, "currency": conflict.currency,
        "auto_resolved": conflict.auto_resolved, "resolution_note": conflict.resolution_note,
        "resolved_at": conflict.resolved_at, "first_seen_at": conflict.first_seen_at,
        "last_seen_at": conflict.last_seen_at,
    }


def overview(db: Session, workspace_id: uuid.UUID) -> dict[str, Any]:
    current = state(db, workspace_id)
    nodes = loader.nodes(db, workspace_id)
    open_rows = list(db.execute(select(MeshConflict).where(
        MeshConflict.workspace_id == workspace_id, MeshConflict.status.in_(OPEN))).scalars())
    severities = Counter(c.severity for c in open_rows)
    kinds = Counter(c.kind for c in open_rows)
    currencies = {c.currency for c in open_rows if c.currency and c.kind in v.EXPOSURE_KINDS}
    links = [l for l in loader.links(db, workspace_id) if l.status != "REJECTED"]
    relations = Counter(l.relation for l in links)
    # Clusters: connected components over usable links.
    parent: dict[uuid.UUID, uuid.UUID] = {n.work_item_id: n.work_item_id for n in nodes}

    def find(x: uuid.UUID) -> uuid.UUID:
        while parent.get(x, x) != x:
            parent[x] = parent.get(parent[x], parent[x])
            x = parent[x]
        return x

    for link in links:
        a, b = find(link.source_work_item_id), find(link.target_work_item_id)
        if a != b:
            parent[a] = b
    clusters = len({find(n.work_item_id) for n in nodes}) if nodes else 0
    linked = {x for l in links for x in (l.source_work_item_id, l.target_work_item_id)}
    top = sorted(nodes, key=lambda n: (-float(n.risk_score), n.title))[:8]
    recent = sorted(open_rows, key=lambda c: (v.SEVERITY_ORDER[c.severity], -(c.exposure_micros or 0)))[:6]
    return {
        "state": {
            "status": current.status if current else "EMPTY",
            "last_built_at": current.last_built_at if current else None,
            "build_ms": current.build_ms if current else None,
            "engine_version": current.engine_version if current else None,
            "error": current.error if current else None,
        },
        "documents": len(nodes),
        "links": len(links),
        "clusters": clusters,
        "unlinked_documents": len([n for n in nodes if n.work_item_id not in linked]),
        "open_conflicts": len(open_rows),
        "by_severity": {s: int(severities.get(s, 0)) for s in v.SEVERITY_ORDER},
        "by_kind": [{"kind": k, "label": v.CONFLICT_LABELS.get(k, k), "count": n} for k, n in kinds.most_common()],
        "exposure_micros": C.exposure_total(open_rows),
        "exposure_currency": next(iter(currencies)) if len(currencies) == 1 else None,
        "mixed_currencies": len(currencies) > 1,
        "risk_index": float(current.risk_index) if current else 0.0,
        "risk_band": RK.band(float(current.risk_index) if current else 0.0),
        "relations": [{"relation": r, "label": v.RELATION_LABELS.get(r, r), "count": n}
                      for r, n in relations.most_common()],
        "kinds": [{"kind": k, "label": v.KINDS.get(k, ("Document", 0))[0], "count": n}
                  for k, n in Counter(n.kind for n in nodes).most_common()],
        "top_risks": [_node_out(n) for n in top if float(n.risk_score) > 0],
        "top_conflicts": [conflict_out(c) for c in recent],
    }


def graph(db: Session, workspace_id: uuid.UUID, *, focus: Optional[uuid.UUID] = None, depth: int = 2,
          min_strength: float = v.MIN_LINK_STRENGTH, limit: int = v.GRAPH_MAX_NODES) -> dict[str, Any]:
    if focus is not None:
        ids: Optional[set[uuid.UUID]] = loader.neighbourhood(db, workspace_id, {focus}, hops=max(1, min(depth, 4)),
                                                             cap=limit)
    else:
        ids = None
    nodes = loader.nodes(db, workspace_id, ids)
    nodes = sorted(nodes, key=lambda n: (-float(n.risk_score), -n.degree, n.title))[:limit]
    kept = {n.work_item_id for n in nodes}
    links = [l for l in loader.links(db, workspace_id, kept if ids is not None else None)
             if l.source_work_item_id in kept and l.target_work_item_id in kept and float(l.strength) >= min_strength]
    open_by_doc: Counter[uuid.UUID] = Counter()
    for c in db.execute(select(MeshConflict).where(MeshConflict.workspace_id == workspace_id,
                                                   MeshConflict.status.in_(OPEN))).scalars():
        for i in c.work_item_ids:
            open_by_doc[i] += 1
    out_nodes = []
    for n in nodes:
        item = _node_out(n)
        item["open_conflicts"] = int(open_by_doc.get(n.work_item_id, 0))
        out_nodes.append(item)
    return {"nodes": out_nodes, "links": [_link_out(l) for l in links], "truncated": len(kept) >= limit}


def document(db: Session, workspace_id: uuid.UUID, work_item_id: uuid.UUID) -> dict[str, Any]:
    node = db.execute(select(MeshNode).where(MeshNode.workspace_id == workspace_id,
                                             MeshNode.work_item_id == work_item_id)).scalar_one_or_none()
    if node is None:
        raise MeshError("NOT_IN_MESH", "This document is not in the mesh yet.", 404)
    links = loader.links(db, workspace_id, {work_item_id})
    others = {n.work_item_id: n for n in loader.nodes(db, workspace_id,
                                                       {x for l in links for x in (l.source_work_item_id,
                                                                                   l.target_work_item_id)})}
    conflicts = list(db.execute(select(MeshConflict).where(
        MeshConflict.workspace_id == workspace_id, MeshConflict.work_item_ids.overlap([work_item_id]))
        .order_by(MeshConflict.status, MeshConflict.severity)).scalars())
    twin = loader.twin_from_node(node)
    link_rows = []
    for l in links:
        other_id = l.target_work_item_id if l.source_work_item_id == work_item_id else l.source_work_item_id
        other = others.get(other_id)
        item = _link_out(l)
        item["other"] = _node_out(other) if other else None
        item["outgoing"] = l.source_work_item_id == work_item_id
        link_rows.append(item)
    link_rows.sort(key=lambda x: (-x["strength"], x["relation"]))
    return {
        "node": _node_out(node),
        "facts": [f for f in twin.facts if not str(f.get("concept", "")).startswith("_")],
        "terms": twin.terms,
        "identifiers": twin.identifiers,
        "references": twin.referenced_identifiers,
        "text_references": twin.text_references,
        "parties": [twin.party_names.get(p, p) for p in twin.parties],
        "links": link_rows,
        "conflicts": [conflict_out(c) for c in conflicts],
    }


def matrix(db: Session, workspace_id: uuid.UUID, *, limit_documents: int = 12) -> dict[str, Any]:
    """The discrepancy matrix: open conflicts (rows) against the documents they involve (columns)."""
    rows = list(db.execute(select(MeshConflict).where(
        MeshConflict.workspace_id == workspace_id, MeshConflict.status.in_(OPEN))).scalars())
    rows.sort(key=lambda c: (v.SEVERITY_ORDER[c.severity], -(c.exposure_micros or 0), c.title))
    counts: Counter[uuid.UUID] = Counter(i for c in rows for i in c.work_item_ids)
    columns_ids = [i for i, _ in counts.most_common(limit_documents)]
    nodes = {n.work_item_id: n for n in loader.nodes(db, workspace_id, columns_ids)}
    columns = [_node_out(nodes[i]) for i in columns_ids if i in nodes]
    out_rows = []
    for c in rows[:60]:
        cells = {}
        for value in c.document_values or []:
            cells[value.get("work_item_id")] = {"display": value.get("display"), "role": value.get("role"),
                                                "quote": value.get("quote")}
        for i in c.work_item_ids:
            cells.setdefault(str(i), {"display": "involved", "role": None, "quote": None})
        out_rows.append({"conflict": conflict_out(c), "cells": cells})
    return {"columns": columns, "rows": out_rows}


def export_csv(db: Session, workspace_id: uuid.UUID) -> str:
    """Audit-ready: every conflict with its documents, values, exposure and decision. Formula-safe."""
    from app.services.tables.export import safe_text

    titles = {n.work_item_id: n.title for n in loader.nodes(db, workspace_id)}
    rows = list(db.execute(select(MeshConflict).where(MeshConflict.workspace_id == workspace_id)
                           .order_by(MeshConflict.status, MeshConflict.severity, MeshConflict.first_seen_at)).scalars())
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["severity", "status", "kind", "title", "summary", "documents", "values", "exposure",
                     "currency", "first_seen", "last_seen", "resolved_at", "resolution_note"])
    for c in rows:
        amount = "" if c.exposure_micros is None else f"{Decimal(c.exposure_micros) / Decimal(1_000_000):.2f}"
        writer.writerow([safe_text(x) for x in (
            c.severity, c.status, v.CONFLICT_LABELS.get(c.kind, c.kind), c.title, c.summary,
            "; ".join(titles.get(i, str(i)) for i in c.work_item_ids),
            "; ".join(f"{d.get('title')}: {d.get('display')}" for d in (c.document_values or [])),
            amount, c.currency or "", c.first_seen_at.isoformat() if c.first_seen_at else "",
            c.last_seen_at.isoformat() if c.last_seen_at else "",
            c.resolved_at.isoformat() if c.resolved_at else "", c.resolution_note or "")])
    return buffer.getvalue()


__all__ = ["BuildResult", "MeshError", "build_workspace", "conflict_out", "decide_conflict", "decide_link",
           "document", "export_csv", "graph", "index_document", "mark_building", "matrix", "overview", "simulate",
           "state"]
