"""ARCH42-S1:embedding — a name's vector for the ANN blocking pass. Pure.

WHY NOT THE SENTENCE-TRANSFORMER
================================

Two reasons, one practical and one about what a name is.

Practical: resolution runs as `entities.resolve_document` on the LIGHT worker
profile, and `assert_imports_match_profile()` refuses to boot a LIGHT worker
that imports sentence_transformers or torch (app/workers/profiles.py). ARCH-31
recorded the same constraint for procurement similarity.

What a name is: "Acme Supplies" and "Globex Supplies" are, to a semantic
model, near neighbours (two supply companies). To entity resolution they are
different parties, and "Accme Suplies" is the neighbour that matters. Signed
feature hashing over character trigrams and whole tokens is deterministic
across processes, needs no model, puts typo variants and reordered tokens
("Kumar Ravi") close, and keeps unrelated names apart. pgvector's HNSW index
over these vectors is the approximate-nearest-neighbour pass the roadmap asks
for; `hnsw.iterative_scan` (pgvector >= 0.8) keeps the workspace filter from
starving the result.
"""

from __future__ import annotations

import hashlib
import math
from typing import Optional, Sequence

from app.services.entities.vocabulary import EMBEDDING_DIM


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.sha1(feature.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big") % EMBEDDING_DIM, (1.0 if digest[4] & 1 else -1.0)


def name_vector(normalized_name: str) -> Optional[list[float]]:
    """L2-normalised hashed trigram + token vector, or None for an empty name."""
    text = (normalized_name or "").strip()
    if not text:
        return None
    vector = [0.0] * EMBEDDING_DIM
    features: list[tuple[str, float]] = []
    for token in text.split():
        padded = f" {token} "
        features.append((f"t:{token}", 2.0))
        features += [(f"g:{padded[i:i + 3]}", 1.0) for i in range(len(padded) - 2)]
    for feature, weight in features:
        index, sign = _bucket(feature)
        vector[index] += sign * weight
    norm = math.sqrt(sum(x * x for x in vector))
    return [x / norm for x in vector] if norm else None


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0
