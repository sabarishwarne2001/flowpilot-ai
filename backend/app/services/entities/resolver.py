"""ARCH42-S1:resolver — one document's fields become mentions of records.

For each entity a document names (a MentionSpec):

  1. Its HARD identifiers are looked up as HMACs under every key generation.
     One record holds them -> link (IDENTIFIER). Two or more records hold
     them -> the mention proves those records are one party: merge them,
     unless the conflict guard objects, in which case a reviewer decides.
  2. Otherwise, for names: blocking (pg_trgm + pgvector ANN) proposes
     records; each is scored with the workspace's Fellegi-Sunter model.
     AUTO links; REVIEW creates a separate record now and a MERGE review;
     below the review band it is a new record.
  3. The conflict guard runs on every path: a link or merge that would put
     two different EXCLUSIVE identifiers (two PANs, two Aadhaar numbers, two
     passports) in one cluster never happens without a human.

A mention in review is never lost and never guessed: it sits on its own
record, and the reviewer's MERGE / SEPARATE verdict either points that record
at the other or confirms it.

IDEMPOTENT
==========

Every mention carries a digest of what it was resolved FROM (kind, normalised
name, identifier HMACs, role). Re-running on an unchanged document changes
nothing; a reviewed document whose values changed re-resolves exactly the
fields that changed. Edges are rebuilt from the document's mentions each time.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import delete, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.entity_graph import Entity, EntityEdge, EntityIdentifier, EntityMention, EntityMergeCandidate
from app.services.entities import annotations as ann
from app.services.entities import blocking, crypto, gate, graph, match_models, presets
from app.services.entities import fellegi_sunter as fs
from app.services.entities import normalize as n
from app.services.entities import vocabulary as v
from app.services.entities.embedding import name_vector

logger = logging.getLogger("app.services.entities.resolver")


@dataclass
class Context:
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    work_item_id: uuid.UUID
    models: dict[str, fs.Model] = field(default_factory=dict)
    report: dict[str, int] = field(default_factory=lambda: {
        "mentions": 0, "unchanged": 0, "linked_by_identifier": 0, "linked_by_model": 0, "new_records": 0,
        "reviews": 0, "conflicts": 0, "identifier_merges": 0, "edges": 0, "removed": 0})

    def model(self, db: Session, kind: str) -> fs.Model:
        if kind not in self.models:
            self.models[kind] = match_models.active_model(db, workspace_id=self.workspace_id, kind=kind)
        return self.models[kind]


def spec_digest(spec: ann.MentionSpec) -> str:
    identifiers = sorted((kind, crypto.head_digest(kind, value)[1], derived) for kind, value, derived in spec.identifiers)
    blob = json.dumps([spec.kind, spec.normalized, spec.role, identifiers], sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def mention_profile(spec: ann.MentionSpec) -> fs.Profile:
    sets: dict[str, set[str]] = {}
    for kind, value, _ in spec.identifiers:
        sets.setdefault(kind, set()).add(crypto.head_digest(kind, value)[1])
    return fs.Profile(spec.kind, (spec.normalized,), {k: frozenset(s) for k, s in sets.items()})


def _new_entity(db: Session, ctx: Context, spec: ann.MentionSpec) -> Entity:
    entity = Entity(
        id=uuid.uuid4(), organization_id=ctx.organization_id, workspace_id=ctx.workspace_id, kind=spec.kind,
        status=v.ENTITY_ACTIVE, display_name=spec.surface[: v.MAX_NAME_LENGTH],
        normalized_name=spec.normalized[: v.MAX_NAME_LENGTH],
        name_embedding=name_vector(spec.normalized) if spec.kind in v.MODELLED_KINDS else None,
        mention_count=0,
    )
    db.add(entity)
    db.flush([entity])
    ctx.report["new_records"] += 1
    return entity


def _attach_identifiers(db: Session, ctx: Context, spec: ann.MentionSpec, target: Entity) -> list[uuid.UUID]:
    """Every identifier the mention evidences, as rows in the target's cluster.

    A value already in the cluster is reused (under any key generation). A
    hard value held by ANOTHER cluster stays there — the one-hard-value index
    is the schema's statement that it cannot be in two places — and the
    caller has already opened a conflict review for that pair.
    """
    members = graph.cluster_ids(db, graph.root_of(db, target.id).id)
    ids: list[uuid.UUID] = []
    for kind, value, derived in spec.identifiers:
        existing = blocking.existing_identifier_ids(db, entity_ids=members, kind=kind, value=value)
        if existing:
            ids += existing
            continue
        generation, digest = crypto.head_digest(kind, value)
        row = EntityIdentifier(
            id=uuid.uuid4(), workspace_id=ctx.workspace_id, entity_id=target.id, entity_kind=target.kind,
            kind=kind, value_hmac=digest, key_generation=generation,
            value_ciphertext=None if kind in v.NO_VALUE_KINDS else crypto.seal(value),
            display_ciphertext=crypto.seal(n.mask(kind, value)), derived=derived,
        )
        try:
            with db.begin_nested():
                db.add(row)
                db.flush([row])
        except IntegrityError:
            continue  # held by another cluster: a conflict review already names it
        ids.append(row.id)
    return list(dict.fromkeys(ids))


def _clusters_for(db: Session, matches: dict[uuid.UUID, set[str]]) -> dict[uuid.UUID, tuple[Entity, set[str]]]:
    roots: dict[uuid.UUID, tuple[Entity, set[str]]] = {}
    for entity_id, kinds in matches.items():
        root = graph.root_of(db, entity_id)
        entry = roots.setdefault(root.id, (root, set()))
        entry[1].update(kinds)
    return roots


def _priority(kinds: set[str]) -> int:
    return min((v.IDENTIFIER_PRIORITY.index(k) for k in kinds if k in v.IDENTIFIER_PRIORITY), default=99)


def _conflict_decision(p: float = 1.0) -> fs.Decision:
    return fs.Decision("REVIEW", v.REASON_CONFLICT, p, 99.0)


def resolve_spec(db: Session, ctx: Context, spec: ann.MentionSpec, digest: str) -> EntityMention:
    profile = mention_profile(spec)
    hard = [(k, value) for k, value, _ in spec.identifiers if k in v.HARD_IDENTIFIER_KINDS]
    roots = _clusters_for(db, blocking.identifier_matches(
        db, workspace_id=ctx.workspace_id, entity_kind=spec.kind, identifiers=hard))

    target: Optional[Entity] = None
    decision, method = v.DECISION_AUTO, v.METHOD_NEW
    probability: Optional[float] = None
    weight: Optional[float] = None
    reviews: list[tuple[Entity, fs.Decision, dict, list[str]]] = []

    if roots:
        ordered = sorted(roots.values(), key=lambda item: (_priority(item[1]), str(item[0].id)))
        primary = ordered[0][0]
        own_conflicts = fs.conflicts(profile.identifiers, graph.cluster_profile(db, primary).identifiers)
        if own_conflicts:
            # The mention itself disagrees with the record its identifier found.
            target = _new_entity(db, ctx, spec)
            decision, method = v.DECISION_REVIEW, v.METHOD_IDENTIFIER
            reviews.append((primary, _conflict_decision(), {"identifier": 1}, own_conflicts))
            ctx.report["conflicts"] += 1
        else:
            target, decision, method, probability = primary, v.DECISION_AUTO, v.METHOD_IDENTIFIER, 1.0
            ctx.report["linked_by_identifier"] += 1
            for other, _kinds in ordered[1:]:
                kinds = graph.cluster_conflicts(db, other, graph.root_of(db, primary.id))
                if kinds:
                    decision = v.DECISION_REVIEW
                    reviews.append((other, _conflict_decision(), {"identifier": 1}, kinds))
                    ctx.report["conflicts"] += 1
                else:
                    graph.merge(db, loser_id=other.id, winner_id=primary.id, actor_user_id=None,
                                reason=v.MERGE_REASON_IDENTIFIER)
                    ctx.report["identifier_merges"] += 1
    elif spec.kind in v.MODELLED_KINDS:
        model = ctx.model(db, spec.kind)
        best_auto: Optional[tuple[float, Entity, fs.Decision, dict]] = None
        best_review: Optional[tuple[float, Entity, fs.Decision, dict, list[str]]] = None
        seen: set[uuid.UUID] = set()
        for candidate_id in blocking.name_candidates(db, workspace_id=ctx.workspace_id, kind=spec.kind,
                                                     normalized=spec.normalized, vector=name_vector(spec.normalized)):
            root = graph.root_of(db, candidate_id)
            if root.id in seen:
                continue
            seen.add(root.id)
            other = graph.cluster_profile(db, root)
            gamma = fs.compare(profile, other)
            kinds = fs.conflicts(profile.identifiers, other.identifiers)
            outcome = fs.decide(model, gamma, kinds)
            if outcome.outcome == "AUTO" and (best_auto is None or outcome.probability > best_auto[0]):
                best_auto = (outcome.probability, root, outcome, gamma)
            elif outcome.outcome == "REVIEW" and (best_review is None or outcome.probability > best_review[0]):
                best_review = (outcome.probability, root, outcome, gamma, kinds)
        if best_auto is not None:
            probability, target, chosen, _ = best_auto
            weight = chosen.weight
            decision, method = v.DECISION_AUTO, v.METHOD_MODEL
            ctx.report["linked_by_model"] += 1
        else:
            target = _new_entity(db, ctx, spec)
            if best_review is not None:
                probability, root, chosen, gamma, kinds = best_review
                weight = chosen.weight
                decision, method = v.DECISION_REVIEW, v.METHOD_MODEL
                reviews.append((root, chosen, gamma, kinds))
                ctx.report["conflicts"] += 1 if kinds else 0
    if target is None:
        target = _new_entity(db, ctx, spec)

    identifier_ids = _attach_identifiers(db, ctx, spec, target)
    mention = EntityMention(
        id=uuid.uuid4(), workspace_id=ctx.workspace_id, work_item_id=ctx.work_item_id, entity_id=target.id,
        entity_kind=spec.kind, field_path=spec.field_path[:200], ordinal=spec.ordinal, role=spec.role,
        surface_name=spec.surface[: v.MAX_NAME_LENGTH], spec_digest=digest, identifier_ids=identifier_ids,
        decision=decision, method=method, source=spec.source,
        match_probability=None if probability is None else Decimal(str(round(min(1.0, max(0.0, probability)), 5))),
        match_weight=None if weight is None else Decimal(str(round(max(-999999.0, min(999999.0, weight)), 3))),
    )
    db.add(mention)
    db.flush([mention])
    for other, chosen, gamma, kinds in reviews:
        if graph.propose(db, left=target, right=other, decision=chosen, gamma=gamma, conflict_kinds=kinds,
                         mention_id=mention.id, work_item_id=ctx.work_item_id):
            ctx.report["reviews"] += 1
    graph.recount(db, [target.id])
    ctx.report["mentions"] += 1
    return mention


def _document_specs(db: Session, *, organization_id: uuid.UUID, work_item: Any) -> tuple[list, list]:
    extracted = dict(work_item.extracted_entities or {})
    extracted.pop("classification_details", None)
    chosen = presets.select_for_document(db, organization_id=organization_id, workspace_id=work_item.workspace_id,
                                         extracted=extracted)
    annotations, sources, detect = ann.merged_annotations((chosen[1], chosen[2]) if chosen else None)
    return ann.extract(extracted, annotations, sources=sources, detect=detect, text=work_item.extracted_text or "")


def resolve_work_item(db: Session, *, work_item: Any, check_capability: bool = True) -> dict[str, Any]:
    """Resolve one document. The caller owns the transaction."""
    organization_id = gate.organization_of(db, work_item)
    if check_capability and not gate.capability_held(db, organization_id):
        return {"resolved": False, "reason": "capability.entity_graph not held"}
    # One resolver per workspace at a time: two documents naming the same new
    # party concurrently would otherwise both create it.
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"entities:{work_item.workspace_id}"})
    ctx = Context(organization_id, work_item.workspace_id, work_item.id)
    specs, edges = _document_specs(db, organization_id=organization_id, work_item=work_item)

    existing = {(m.field_path, m.ordinal): m for m in db.execute(
        select(EntityMention).where(EntityMention.work_item_id == work_item.id)).scalars()}
    key_to_entity: dict[str, uuid.UUID] = {}
    kept: set[uuid.UUID] = set()
    touched: set[uuid.UUID] = set()
    for spec in specs:
        digest = spec_digest(spec)
        old = existing.get((spec.field_path, spec.ordinal))
        if old is not None and old.spec_digest == digest and old.entity_kind == spec.kind:
            key_to_entity[spec.key] = old.entity_id
            kept.add(old.id)
            ctx.report["unchanged"] += 1
            continue
        if old is not None:
            touched.add(old.entity_id)
            _retire_mention(db, old)
            existing.pop((spec.field_path, spec.ordinal))
        mention = resolve_spec(db, ctx, spec, digest)
        key_to_entity[spec.key] = mention.entity_id
        kept.add(mention.id)
    for old in existing.values():
        if old.id not in kept:
            touched.add(old.entity_id)
            _retire_mention(db, old)
            ctx.report["removed"] += 1

    db.execute(delete(EntityEdge).where(EntityEdge.evidence_work_item_id == work_item.id))
    for edge in edges:
        src, dst = key_to_entity.get(edge.src_key), key_to_entity.get(edge.dst_key)
        if src is None or dst is None or src == dst:
            continue
        db.execute(pg_insert(EntityEdge).values(
            id=uuid.uuid4(), workspace_id=work_item.workspace_id, src_entity_id=src, dst_entity_id=dst,
            relation=edge.relation, evidence_work_item_id=work_item.id,
        ).on_conflict_do_nothing(constraint="uq_entity_edges_src_dst_relation_evidence"))
        ctx.report["edges"] += 1
    db.flush()
    if touched:
        graph.recount(db, touched)
        graph.collect_orphans(db, workspace_id=work_item.workspace_id)
    return {"resolved": True, **ctx.report}


def _retire_mention(db: Session, mention: EntityMention) -> None:
    db.execute(update(EntityMergeCandidate).where(
        EntityMergeCandidate.mention_id == mention.id, EntityMergeCandidate.status == v.CANDIDATE_OPEN,
    ).values(status=v.CANDIDATE_OBSOLETE, resolved_at=graph.now()))
    db.delete(mention)
    db.flush()


def resolve_by_id(db: Session, *, work_item_id: uuid.UUID) -> dict[str, Any]:
    from app.models.work_item import WorkItem

    work_item = db.get(WorkItem, work_item_id)
    if work_item is None:
        return {"resolved": False, "reason": "work item not found"}
    return resolve_work_item(db, work_item=work_item)
