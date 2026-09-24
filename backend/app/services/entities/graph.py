"""ARCH42-S1:graph — clusters, the conflict guard, and reversible edits.

A MERGE IS A POINTER
====================

Merging A into B sets A.status = MERGED and A.merged_into_id = B. A's
mentions, identifiers and edges stay A's. Reads resolve every record to its
ROOT (follow merged_into_id until an ACTIVE record) and treat the root's
cluster as one entity. So:

  * unmerge is exact: clear the pointer, and A is what it was, with exactly
    the documents it had — nothing to reconstruct;
  * hard identifiers never collide: A's PAN row stays on A, so the
    one-hard-value-per-workspace index cannot fire because of a merge;
  * a merge into another workspace's record is refused by the composite
    foreign key (merged_into_id, workspace_id) -> entities (id, workspace_id).

A SPLIT moves chosen mentions (and the identifiers only they evidence) to a
new record, and records the pair as SEPARATE so no sweep re-merges them.
Every merge, unmerge, split, rename and erasure is audited; audit details
carry record ids and identifier KINDS, never names or identifier values.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.entity_graph import (
    Entity,
    EntityEdge,
    EntityIdentifier,
    EntityMention,
    EntityMergeCandidate,
)
from app.services.entities import crypto
from app.services.entities import fellegi_sunter as fs
from app.services.entities import vocabulary as v

MAX_CHAIN = 64


class GraphError(ValueError):
    """A request the graph cannot honour. The message is for the reader."""


class MergeConflict(GraphError):
    def __init__(self, kinds: Sequence[str]) -> None:
        super().__init__(
            "These records hold different "
            + ", ".join(k.replace("_", " ").lower() for k in kinds)
            + " values, so merging them needs a reviewer's decision."
        )
        self.kinds = list(kinds)


def now() -> datetime:
    return datetime.now(timezone.utc)


def root_of(db: Session, entity_id: uuid.UUID) -> Entity:
    entity = db.get(Entity, entity_id)
    for _ in range(MAX_CHAIN):
        if entity is None:
            raise GraphError("Record not found.")
        if entity.merged_into_id is None:
            return entity
        entity = db.get(Entity, entity.merged_into_id)
    raise GraphError("Merge chain too long; the graph is inconsistent.")


def cluster_ids(db: Session, root_id: uuid.UUID) -> list[uuid.UUID]:
    """The root and every record merged into it, at any depth."""
    return list(db.execute(text(
        "WITH RECURSIVE c(id) AS (SELECT CAST(:root AS uuid) UNION "
        "SELECT e.id FROM entities e JOIN c ON e.merged_into_id = c.id) SELECT id FROM c"
    ), {"root": root_id}).scalars())


def identifier_sets(db: Session, entity_ids: Sequence[uuid.UUID]) -> dict[str, frozenset[str]]:
    """identifier kind -> digests under the HEAD key generation (comparable)."""
    if not entity_ids:
        return {}
    head = crypto.head_generation()
    sets: dict[str, set[str]] = {}
    for kind, digest in db.execute(select(EntityIdentifier.kind, EntityIdentifier.value_hmac).where(
            EntityIdentifier.entity_id.in_(list(entity_ids)), EntityIdentifier.key_generation == head)).all():
        sets.setdefault(kind, set()).add(digest)
    return {k: frozenset(s) for k, s in sets.items()}


def cluster_profile(db: Session, root: Entity) -> fs.Profile:
    ids = cluster_ids(db, root.id)
    names = tuple(dict.fromkeys(db.execute(select(Entity.normalized_name).where(Entity.id.in_(ids))).scalars()))
    return fs.Profile(root.kind, names, identifier_sets(db, ids))


def cluster_conflicts(db: Session, a_root: Entity, b_root: Entity) -> list[str]:
    return fs.conflicts(identifier_sets(db, cluster_ids(db, a_root.id)), identifier_sets(db, cluster_ids(db, b_root.id)))


def _audit(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID, actor_user_id: Optional[uuid.UUID],
           action: Any, details: Mapping[str, Any], resource_id: Optional[uuid.UUID] = None) -> None:
    from app.models.audit_log import AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(
        db, organization_id=organization_id, workspace_id=workspace_id, actor_id=actor_user_id,
        resource_type=AuditResourceType.WORKSPACE, resource_id=resource_id or workspace_id,
        action=action, outcome=AuditOutcome.ALLOWED, details={"entity_graph": dict(details)},
    )


def obsolete_candidates_within(db: Session, root_id: uuid.UUID) -> int:
    """OPEN proposals whose two sides are now one cluster have nothing to decide."""
    members = cluster_ids(db, root_id)
    result = db.execute(update(EntityMergeCandidate).where(
        EntityMergeCandidate.status == v.CANDIDATE_OPEN,
        EntityMergeCandidate.left_entity_id.in_(members),
        EntityMergeCandidate.right_entity_id.in_(members),
    ).values(status=v.CANDIDATE_OBSOLETE, resolved_at=now()))
    return result.rowcount or 0


def separate_recorded(db: Session, a: uuid.UUID, b: uuid.UUID) -> bool:
    low, high = sorted((a, b), key=str)
    return db.execute(select(EntityMergeCandidate.id).where(
        EntityMergeCandidate.low_entity_id == low, EntityMergeCandidate.high_entity_id == high,
        EntityMergeCandidate.status == v.CANDIDATE_SEPARATE).limit(1)).first() is not None


def merge(
    db: Session,
    *,
    loser_id: uuid.UUID,
    winner_id: uuid.UUID,
    actor_user_id: Optional[uuid.UUID],
    reason: str,
    allow_conflict: bool = False,
) -> Entity:
    """Point loser's cluster at winner's. Returns the surviving root."""
    from app.models.audit_log import AuditAction

    loser, winner = root_of(db, loser_id), root_of(db, winner_id)
    if loser.id == winner.id:
        return winner
    if loser.workspace_id != winner.workspace_id:
        raise GraphError("Records in different workspaces cannot be merged.")
    if loser.kind != winner.kind:
        raise GraphError(f"A {loser.kind.lower()} cannot be merged into a {winner.kind.lower()}.")
    kinds = cluster_conflicts(db, loser, winner)
    if kinds and not allow_conflict:
        raise MergeConflict(kinds)
    loser.status, loser.merged_into_id = v.ENTITY_MERGED, winner.id
    loser.merged_at, loser.merged_by_user_id, loser.merge_reason = now(), actor_user_id, reason
    loser.updated_at = winner.updated_at = now()
    if loser.last_seen_at and (winner.last_seen_at is None or loser.last_seen_at > winner.last_seen_at):
        winner.last_seen_at = loser.last_seen_at
    if loser.first_seen_at and (winner.first_seen_at is None or loser.first_seen_at < winner.first_seen_at):
        winner.first_seen_at = loser.first_seen_at
    db.flush([loser, winner])
    obsolete_candidates_within(db, winner.id)
    _audit(db, organization_id=winner.organization_id, workspace_id=winner.workspace_id, actor_user_id=actor_user_id,
           action=AuditAction.UPDATED, resource_id=winner.id,
           details={"operation": "merge", "merged": str(loser.id), "into": str(winner.id), "reason": reason,
                    "conflict_kinds": kinds, "by": "user" if actor_user_id else "system"})
    return winner


def _record_separate(db: Session, a: Entity, b: Entity, actor_user_id: Optional[uuid.UUID]) -> None:
    if separate_recorded(db, a.id, b.id):
        return
    db.add(EntityMergeCandidate(
        id=uuid.uuid4(), organization_id=a.organization_id, workspace_id=a.workspace_id,
        left_entity_id=a.id, right_entity_id=b.id, entity_kind=a.kind, reason=v.REASON_MANUAL,
        match_probability=Decimal("0"), match_weight=Decimal("0"), status=v.CANDIDATE_SEPARATE,
        resolved_at=now(), resolved_by_user_id=actor_user_id,
    ))
    db.flush()


def unmerge(db: Session, *, entity_id: uuid.UUID, actor_user_id: Optional[uuid.UUID]) -> Entity:
    """Reverse one merge exactly. The record becomes a root again."""
    from app.models.audit_log import AuditAction

    entity = db.get(Entity, entity_id)
    if entity is None or entity.merged_into_id is None:
        raise GraphError("That record is not merged into another.")
    parent = db.get(Entity, entity.merged_into_id)
    entity.status, entity.merged_into_id = v.ENTITY_ACTIVE, None
    entity.merged_at = entity.merged_by_user_id = entity.merge_reason = None
    entity.updated_at = now()
    db.flush([entity])
    if parent is not None:
        _record_separate(db, entity, root_of(db, parent.id), actor_user_id)
    _audit(db, organization_id=entity.organization_id, workspace_id=entity.workspace_id, actor_user_id=actor_user_id,
           action=AuditAction.UPDATED, resource_id=entity.id,
           details={"operation": "unmerge", "entity": str(entity.id), "from": str(parent.id) if parent else None})
    return entity


def recount(db: Session, entity_ids: Iterable[uuid.UUID]) -> None:
    for entity_id in set(entity_ids):
        entity = db.get(Entity, entity_id)
        if entity is None:
            continue
        stats = db.execute(select(func.count(), func.min(EntityMention.created_at), func.max(EntityMention.created_at))
                           .where(EntityMention.entity_id == entity_id)).one()
        entity.mention_count = int(stats[0])
        entity.first_seen_at, entity.last_seen_at = stats[1], stats[2]
    db.flush()


def split(
    db: Session, *, root_id: uuid.UUID, mention_ids: Sequence[uuid.UUID], actor_user_id: Optional[uuid.UUID]
) -> Entity:
    """Move chosen mentions of a cluster to a new record."""
    from app.models.audit_log import AuditAction
    from app.services.entities.embedding import name_vector

    root = root_of(db, root_id)
    members = set(cluster_ids(db, root.id))
    mentions = list(db.execute(select(EntityMention).where(EntityMention.id.in_(list(mention_ids)))).scalars())
    if not mentions or len(mentions) != len(set(mention_ids)) or any(m.entity_id not in members for m in mentions):
        raise GraphError("Choose mentions that belong to this record.")
    remaining = db.execute(select(func.count()).select_from(EntityMention).where(
        EntityMention.entity_id.in_(members), EntityMention.id.notin_([m.id for m in mentions]))).scalar_one()
    if remaining == 0:
        raise GraphError("A split must leave at least one document on the original record.")
    first = mentions[0]
    source_entity = db.get(Entity, first.entity_id)
    new = Entity(
        id=uuid.uuid4(), organization_id=root.organization_id, workspace_id=root.workspace_id, kind=root.kind,
        status=v.ENTITY_ACTIVE, display_name=first.surface_name, split_from_id=root.id,
        normalized_name=source_entity.normalized_name if source_entity else root.normalized_name,
    )
    from app.services.entities import normalize as n

    if root.kind in v.MODELLED_KINDS:
        new.normalized_name = n.normalize_name(root.kind, first.surface_name) or new.normalized_name
        new.name_embedding = name_vector(new.normalized_name)
    db.add(new)
    db.flush([new])

    moved_ids = {m.id for m in mentions}
    touched = {m.entity_id for m in mentions}
    evidence = {i for m in mentions for i in (m.identifier_ids or [])}
    kept_evidence = {i for ids in db.execute(select(EntityMention.identifier_ids).where(
        EntityMention.entity_id.in_(members), EntityMention.id.notin_(moved_ids))).scalars() for i in (ids or [])}
    exclusive_only = evidence - kept_evidence
    if exclusive_only:
        db.execute(update(EntityIdentifier).where(EntityIdentifier.id.in_(list(exclusive_only)))
                   .values(entity_id=new.id, updated_at=now()))
    for mention in mentions:
        old = mention.entity_id
        for column in (EntityEdge.src_entity_id, EntityEdge.dst_entity_id):
            db.execute(update(EntityEdge).where(column == old, EntityEdge.evidence_work_item_id == mention.work_item_id)
                       .values({column.key: new.id}).execution_options(synchronize_session=False))
        mention.entity_id = new.id
        mention.decision, mention.method, mention.decided_by_user_id = v.DECISION_CONFIRMED, v.METHOD_MANUAL, actor_user_id
        mention.updated_at = now()
    db.execute(delete(EntityEdge).where(EntityEdge.src_entity_id == EntityEdge.dst_entity_id))
    db.flush()
    recount(db, touched | {new.id})
    _record_separate(db, new, root, actor_user_id)
    _audit(db, organization_id=root.organization_id, workspace_id=root.workspace_id, actor_user_id=actor_user_id,
           action=AuditAction.UPDATED, resource_id=new.id,
           details={"operation": "split", "from": str(root.id), "new": str(new.id), "mentions": len(mentions),
                    "identifiers_moved": len(exclusive_only)})
    return new


def rename(db: Session, *, entity_id: uuid.UUID, display_name: str, actor_user_id: Optional[uuid.UUID]) -> Entity:
    from app.models.audit_log import AuditAction
    from app.services.entities import normalize as n

    root = root_of(db, entity_id)
    if root.kind not in v.MODELLED_KINDS:
        raise GraphError("Accounts, assets and shipments are named by their reference.")
    cleaned = n.clean_display(display_name)
    if not cleaned:
        raise GraphError("A name is required.")
    root.display_name, root.updated_at = cleaned, now()
    db.flush([root])
    _audit(db, organization_id=root.organization_id, workspace_id=root.workspace_id, actor_user_id=actor_user_id,
           action=AuditAction.UPDATED, resource_id=root.id, details={"operation": "rename", "entity": str(root.id)})
    return root


def propose(
    db: Session,
    *,
    left: Entity,
    right: Entity,
    decision: fs.Decision,
    gamma: Mapping[str, Optional[int]],
    conflict_kinds: Sequence[str],
    mention_id: Optional[uuid.UUID] = None,
    work_item_id: Optional[uuid.UUID] = None,
) -> Optional[uuid.UUID]:
    """Open a MERGE review for a pair, unless one is open or a person said SEPARATE."""
    if left.id == right.id or separate_recorded(db, left.id, right.id):
        return None
    reason = v.REASON_CONFLICT if conflict_kinds else (decision.reason or v.REASON_UNCERTAIN)
    statement = pg_insert(EntityMergeCandidate).values(
        id=uuid.uuid4(), organization_id=left.organization_id, workspace_id=left.workspace_id,
        left_entity_id=left.id, right_entity_id=right.id, entity_kind=left.kind, mention_id=mention_id,
        work_item_id=work_item_id, reason=reason,
        match_probability=Decimal(str(round(min(1.0, max(0.0, decision.probability)), 5))),
        match_weight=Decimal(str(round(max(-999999.0, min(999999.0, decision.weight)), 3))),
        comparison={k: val for k, val in gamma.items()}, conflict_kinds=list(conflict_kinds),
        status=v.CANDIDATE_OPEN,
    ).on_conflict_do_nothing(
        index_elements=["workspace_id", "low_entity_id", "high_entity_id"],
        index_where=text("status = 'OPEN'"),
    ).returning(EntityMergeCandidate.id)
    return db.execute(statement).scalar_one_or_none()


def delete_cluster(db: Session, root_id: uuid.UUID) -> dict[str, int]:
    """Remove a record and everything merged into it. Used by erasure."""
    members = cluster_ids(db, root_id)
    counts = {
        "candidates": db.execute(delete(EntityMergeCandidate).where(or_(
            EntityMergeCandidate.left_entity_id.in_(members), EntityMergeCandidate.right_entity_id.in_(members)))).rowcount or 0,
        "edges": db.execute(delete(EntityEdge).where(or_(
            EntityEdge.src_entity_id.in_(members), EntityEdge.dst_entity_id.in_(members)))).rowcount or 0,
        "mentions": db.execute(delete(EntityMention).where(EntityMention.entity_id.in_(members))).rowcount or 0,
        "identifiers": db.execute(delete(EntityIdentifier).where(EntityIdentifier.entity_id.in_(members))).rowcount or 0,
    }
    db.execute(update(Entity).where(Entity.id.in_(members)).values(split_from_id=None))
    remaining = list(members)
    # Children first: the merged_into foreign key forbids orphaning a pointer.
    while remaining:
        leaves = [m for m in remaining if db.execute(select(func.count()).select_from(Entity).where(
            Entity.merged_into_id == m)).scalar_one() == 0]
        if not leaves:
            raise GraphError("Merge cycle detected.")
        db.execute(delete(Entity).where(Entity.id.in_(leaves)))
        remaining = [m for m in remaining if m not in leaves]
    counts["entities"] = len(members)
    db.flush()
    return counts


def collect_orphans(db: Session, *, workspace_id: uuid.UUID) -> dict[str, int]:
    """Identifiers no mention evidences, and ACTIVE roots with nothing left.

    Deleting a document cascades its mentions and edges; this removes what
    those mentions alone were holding up. Erasure calls it immediately; the
    nightly sweep calls it for documents deleted any other way.
    """
    orphan_ids = db.execute(text(
        "DELETE FROM entity_identifiers i WHERE i.workspace_id = :ws AND NOT EXISTS ("
        "  SELECT 1 FROM entity_mentions m WHERE m.workspace_id = :ws AND i.id = ANY(m.identifier_ids))"
    ), {"ws": workspace_id}).rowcount or 0
    candidates = list(db.execute(text(
        "SELECT e.id FROM entities e WHERE e.workspace_id = :ws AND e.status = 'ACTIVE' "
        "AND NOT EXISTS (SELECT 1 FROM entity_mentions m WHERE m.entity_id = e.id)"
    ), {"ws": workspace_id}).scalars())
    removed = 0
    for entity_id in candidates:
        members = cluster_ids(db, entity_id)
        held = db.execute(select(func.count()).select_from(EntityMention).where(
            EntityMention.entity_id.in_(members))).scalar_one()
        if held == 0:
            removed += delete_cluster(db, entity_id)["entities"]
    return {"identifiers": orphan_ids, "entities": removed}
