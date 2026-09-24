"""ARCH42-S1:blocking — which existing records a mention could be.

Comparing a mention with every record in a workspace is quadratic and wrong
twice over: slow, and a model scoring a million implausible pairs finds some
that look plausible. Three passes produce the candidates, and only they are
scored:

  1. HARD IDENTIFIER: the mention's hard identifiers as HMACs under every
     configured key generation; an exact hit is a link, not a candidate.
  2. pg_trgm: `normalized_name % :q` over the GIN trigram index — typo and
     spelling variants ("Accme Suplies").
  3. pgvector ANN: cosine distance over the HNSW index of hashed name vectors
     — reordered and partial names ("Kumar Ravi", "R Kumar"). The workspace
     and kind filter runs with `hnsw.iterative_scan = relaxed_order`
     (pgvector >= 0.8), so a filter that discards most of the nearest
     neighbours scans further instead of returning nothing.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Optional, Sequence

from sqlalchemy import select, text, tuple_
from sqlalchemy.orm import Session

from app.models.entity_graph import Entity, EntityIdentifier
from app.services.entities import crypto
from app.services.entities import vocabulary as v

ANN_MIN_COSINE = 0.45


def identifier_matches(
    db: Session, *, workspace_id: uuid.UUID, entity_kind: str, identifiers: Iterable[tuple[str, str]]
) -> dict[uuid.UUID, set[str]]:
    """entity id -> the hard identifier kinds it shares with the mention."""
    pairs: list[tuple[str, str]] = []
    for kind, value in identifiers:
        if kind in v.HARD_IDENTIFIER_KINDS:
            pairs += [(kind, digest) for digest in crypto.lookup_digests(kind, value)]
    if not pairs:
        return {}
    rows = db.execute(
        select(EntityIdentifier.entity_id, EntityIdentifier.kind).where(
            EntityIdentifier.workspace_id == workspace_id,
            EntityIdentifier.entity_kind == entity_kind,
            tuple_(EntityIdentifier.kind, EntityIdentifier.value_hmac).in_(pairs),
        )
    ).all()
    found: dict[uuid.UUID, set[str]] = {}
    for entity_id, kind in rows:
        found.setdefault(entity_id, set()).add(kind)
    return found


def existing_identifier_ids(
    db: Session, *, entity_ids: Sequence[uuid.UUID], kind: str, value: str
) -> list[uuid.UUID]:
    digests = crypto.lookup_digests(kind, value)
    if not entity_ids:
        return []
    return list(db.execute(select(EntityIdentifier.id).where(
        EntityIdentifier.entity_id.in_(list(entity_ids)),
        EntityIdentifier.kind == kind,
        EntityIdentifier.value_hmac.in_(digests),
    )).scalars())


def trigram_candidates(db: Session, *, workspace_id: uuid.UUID, kind: str, normalized: str) -> list[uuid.UUID]:
    if not normalized:
        return []
    db.execute(text("SELECT set_config('pg_trgm.similarity_threshold', :t, true)"), {"t": str(v.TRIGRAM_THRESHOLD)})
    return list(db.execute(
        text(
            "SELECT id FROM entities WHERE workspace_id = :ws AND kind = :kind AND normalized_name % :q "
            "ORDER BY similarity(normalized_name, :q) DESC LIMIT :limit"
        ),
        {"ws": workspace_id, "kind": kind, "q": normalized, "limit": v.TRIGRAM_LIMIT},
    ).scalars())


def ann_candidates(db: Session, *, workspace_id: uuid.UUID, kind: str, vector: Optional[Sequence[float]]) -> list[uuid.UUID]:
    if vector is None or len(vector) == 0:
        return []
    vector = [float(x) for x in vector]
    db.execute(text("SELECT set_config('hnsw.iterative_scan', 'relaxed_order', true)"))
    distance = Entity.name_embedding.cosine_distance(vector)
    rows = db.execute(
        select(Entity.id, distance.label("d")).where(
            Entity.workspace_id == workspace_id, Entity.kind == kind, Entity.name_embedding.is_not(None)
        ).order_by(distance).limit(v.ANN_LIMIT)
    ).all()
    return [row.id for row in rows if 1.0 - float(row.d) >= ANN_MIN_COSINE]


def name_candidates(
    db: Session, *, workspace_id: uuid.UUID, kind: str, normalized: str, vector: Optional[Sequence[float]]
) -> list[uuid.UUID]:
    seen: dict[uuid.UUID, None] = {}
    for entity_id in trigram_candidates(db, workspace_id=workspace_id, kind=kind, normalized=normalized):
        seen.setdefault(entity_id)
    for entity_id in ann_candidates(db, workspace_id=workspace_id, kind=kind, vector=vector):
        seen.setdefault(entity_id)
    return list(seen)
