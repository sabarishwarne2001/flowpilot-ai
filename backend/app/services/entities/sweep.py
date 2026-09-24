"""ARCH42-S1:sweep — the nightly re-resolution pass.

Run by scripts/sweep_entities.py through deploy/bin/flowpilot-sweep
(`entities`), scheduled in deploy/cron.d/flowpilot-sweepers. Per entitled
workspace, in this order:

  1. RE-KEY identifiers written under an older key generation (decrypt the
     sealed value, HMAC under the head key). Aadhaar rows hold no value and
     cannot be re-keyed; they are counted, never guessed.
  2. CATCH UP: documents completed while the tenant lacked the capability, or
     whose extracted values changed (a review corrected them) since their
     mentions were written.
  3. FIT the Fellegi-Sunter model per kind by EM over the blocked record
     pairs; a converged fit on enough pairs becomes the next ACTIVE version.
  4. RE-RESOLVE record pairs under the (new) model: AUTO merges where the
     guard is silent, MERGE reviews for the uncertain band and for conflicts.
     A pair a person marked SEPARATE is never proposed again.
  5. TIDY: proposals made moot by merges become OBSOLETE; identifiers and
     records nothing evidences any more are removed.
"""

from __future__ import annotations

import itertools
import logging
import random
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.models.entity_graph import Entity, EntityIdentifier, EntityMention, EntityMergeCandidate
from app.services.entities import blocking, crypto, gate, graph, match_models, resolver
from app.services.entities import annotations as ann
from app.services.entities import fellegi_sunter as fs
from app.services.entities import vocabulary as v

logger = logging.getLogger("app.services.entities.sweep")


def rekey(db: Session, *, workspace_id: uuid.UUID) -> dict[str, int]:
    head = crypto.head_generation()
    counts = {"rekeyed": 0, "unrekeyable": 0}
    for row in db.execute(select(EntityIdentifier).where(
            EntityIdentifier.workspace_id == workspace_id, EntityIdentifier.key_generation != head)).scalars():
        value = crypto.open_sealed(row.value_ciphertext)
        if value is None:
            counts["unrekeyable"] += 1
            continue
        generation, digest = crypto.head_digest(row.kind, value)
        row.value_hmac, row.key_generation = digest, generation
        row.value_ciphertext = crypto.seal(value)
        row.display_ciphertext = crypto.seal(crypto.open_sealed(row.display_ciphertext) or "•")
        counts["rekeyed"] += 1
    db.flush()
    return counts


def _catch_up_ids(db: Session, workspace_id: uuid.UUID) -> list[uuid.UUID]:
    keys = sorted(set(ann.BUILTIN_ANNOTATIONS) | {
        k for entry in ann.PRESET_ENTITY_ANNOTATIONS.values() for k in entry["properties"]})
    return list(db.execute(text(
        "SELECT w.id FROM work_items w WHERE w.workspace_id = :ws AND w.extracted_entities IS NOT NULL "
        "AND json_typeof(w.extracted_entities::json) = 'object' AND w.extracted_entities::jsonb ?| CAST(:keys AS text[]) "
        "AND (NOT EXISTS (SELECT 1 FROM entity_mentions m WHERE m.work_item_id = w.id) "
        "     OR w.updated_at > (SELECT max(m.updated_at) FROM entity_mentions m WHERE m.work_item_id = w.id)) "
        "ORDER BY w.created_at LIMIT :limit"
    ), {"ws": workspace_id, "keys": keys, "limit": v.SWEEP_MAX_CATCHUP_DOCUMENTS}).scalars())


def catch_up(db: Session, *, workspace_id: uuid.UUID) -> int:
    from app.models.work_item import WorkItem

    done = 0
    for work_item_id in _catch_up_ids(db, workspace_id):
        work_item = db.get(WorkItem, work_item_id)
        if work_item is None:
            continue
        with db.begin_nested():
            resolver.resolve_work_item(db, work_item=work_item, check_capability=False)
        done += 1
    return done


def candidate_pairs(db: Session, *, workspace_id: uuid.UUID, kind: str) -> list[tuple[Entity, Entity]]:
    roots = list(db.execute(select(Entity).where(
        Entity.workspace_id == workspace_id, Entity.kind == kind, Entity.status == v.ENTITY_ACTIVE,
    ).order_by(Entity.created_at).limit(v.SWEEP_MAX_ENTITIES)).scalars())
    by_id = {e.id: e for e in roots}
    pairs: dict[tuple[uuid.UUID, uuid.UUID], tuple[Entity, Entity]] = {}
    for entity in roots:
        for other_id in blocking.name_candidates(db, workspace_id=workspace_id, kind=kind,
                                                 normalized=entity.normalized_name, vector=entity.name_embedding):
            other = by_id.get(other_id)
            if other is None or other.id == entity.id:
                continue
            key = tuple(sorted((entity.id, other.id), key=str))
            pairs.setdefault(key, (by_id[key[0]], by_id[key[1]]))
            if len(pairs) >= v.SWEEP_MAX_PAIRS:
                return list(pairs.values())
    return list(pairs.values())


def random_u(db: Session, *, workspace_id: uuid.UUID, kind: str, profile) -> Optional[dict[str, list[float]]]:
    """u from random pairs of this workspace's records (see fs.estimate_u)."""
    entities = list(db.execute(select(Entity).where(
        Entity.workspace_id == workspace_id, Entity.kind == kind, Entity.status == v.ENTITY_ACTIVE,
    ).order_by(Entity.id)).scalars())
    if len(entities) < 2:
        return None
    rnd = random.Random(str(workspace_id))  # the same sample on every run
    sample = rnd.sample(entities, min(len(entities), v.U_SAMPLE_ENTITIES))
    pairs = list(itertools.combinations(sample, 2))
    if len(pairs) > v.U_SAMPLE_PAIRS:
        pairs = rnd.sample(pairs, v.U_SAMPLE_PAIRS)
    u = fs.estimate_u([fs.compare(profile(a), profile(b)) for a, b in pairs], fs.prior(kind))
    return {c: x for c, x in u.items() if c not in v.BLOCKED_COMPARISONS}


def fit_and_reresolve(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, kind: str) -> dict[str, Any]:
    pairs = candidate_pairs(db, workspace_id=workspace_id, kind=kind)
    profiles: dict[uuid.UUID, fs.Profile] = {}

    def profile(entity: Entity) -> fs.Profile:
        if entity.id not in profiles:
            profiles[entity.id] = graph.cluster_profile(db, entity)
        return profiles[entity.id]

    scored = [(a, b, fs.compare(profile(a), profile(b))) for a, b in pairs]
    fixed_u = random_u(db, workspace_id=workspace_id, kind=kind, profile=profile)
    entry: dict[str, Any] = {"pairs": len(scored), "fitted_version": None, "merged": 0, "proposed": 0}
    anchored = sum(any(level is not None for c, level in g.items() if c not in v.BLOCKED_COMPARISONS) for _, _, g in scored)
    entry["anchored_pairs"] = anchored
    # Names alone cannot identify the two classes: without a comparison the
    # blocking did not select on, the platform prior stays in force.
    if len(scored) >= v.MIN_PAIRS_TO_FIT and fixed_u is not None and anchored >= v.MIN_ANCHORED_PAIRS:
        fit = fs.fit_em([g for _, _, g in scored], match_models.active_model(db, workspace_id=workspace_id, kind=kind),
                        fixed_u=fixed_u)
        entry.update(converged=fit.converged, iterations=fit.iterations, refused=list(fit.problems))
        row = match_models.save_fit(db, organization_id=organization_id, workspace_id=workspace_id, kind=kind, fit=fit)
        entry["fitted_version"] = row.version if row else None
    model = match_models.active_model(db, workspace_id=workspace_id, kind=kind)
    for a, b, gamma in scored:
        ra, rb = graph.root_of(db, a.id), graph.root_of(db, b.id)
        if ra.id == rb.id or graph.separate_recorded(db, ra.id, rb.id):
            continue
        kinds = graph.cluster_conflicts(db, ra, rb)
        decision = fs.decide(model, gamma, kinds)
        if decision.outcome == "AUTO":
            loser, winner = (ra, rb) if (ra.created_at or 0) > (rb.created_at or 0) else (rb, ra)
            graph.merge(db, loser_id=loser.id, winner_id=winner.id, actor_user_id=None, reason=v.MERGE_REASON_MODEL)
            entry["merged"] += 1
        elif decision.outcome == "REVIEW":
            if graph.propose(db, left=ra, right=rb, decision=decision, gamma=gamma, conflict_kinds=kinds):
                entry["proposed"] += 1
    return entry


def tidy(db: Session, *, workspace_id: uuid.UUID) -> dict[str, int]:
    obsolete = 0
    for candidate in db.execute(select(EntityMergeCandidate).where(
            EntityMergeCandidate.workspace_id == workspace_id, EntityMergeCandidate.status == v.CANDIDATE_OPEN)).scalars():
        if graph.root_of(db, candidate.left_entity_id).id == graph.root_of(db, candidate.right_entity_id).id:
            candidate.status, candidate.resolved_at = v.CANDIDATE_OBSOLETE, graph.now()
            obsolete += 1
    db.flush()
    return {"obsolete_candidates": obsolete, **graph.collect_orphans(db, workspace_id=workspace_id)}


def _workspaces(db: Session, only: Optional[Sequence[uuid.UUID]]) -> list[tuple[uuid.UUID, uuid.UUID]]:
    from app.models.workspace import Workspace

    statement = select(Workspace.id, Workspace.organization_id)
    if only:
        statement = statement.where(Workspace.id.in_(list(only)))
    return sorted(db.execute(statement).all(), key=lambda r: str(r[0]))


def run(db: Session, *, workspace_ids: Optional[Sequence[uuid.UUID]] = None, check_capability: bool = True) -> dict[str, Any]:
    report: dict[str, Any] = {"workspaces": []}
    for workspace_id, organization_id in _workspaces(db, workspace_ids):
        if check_capability and not gate.capability_held(db, organization_id):
            continue
        entry: dict[str, Any] = {"workspace_id": str(workspace_id)}
        entry["rekey"] = rekey(db, workspace_id=workspace_id)
        entry["caught_up"] = catch_up(db, workspace_id=workspace_id)
        entry["kinds"] = {kind: fit_and_reresolve(db, organization_id=organization_id, workspace_id=workspace_id, kind=kind)
                          for kind in v.MODELLED_KINDS}
        entry["tidy"] = tidy(db, workspace_id=workspace_id)
        report["workspaces"].append(entry)
    return report
