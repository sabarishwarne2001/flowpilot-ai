"""ARCH-34 §5.3 — L3: the two documents mean the same thing.

THE WEAKEST LAYER, AND THE ONE THAT NEEDS THE MOST DISCIPLINE
=============================================================

A cosine of 0.97 between two mean document embeddings says the documents
occupy nearly the same position in a 384-dimensional space learned by a model
that was never shown either of them. That is real evidence and it is not
proof, which is §5.9's point and the reason `severity_for` refuses to return
HIGH for an uncorroborated L3 finding however close to 1.0 the cosine gets.

Two monthly retainer invoices from the same supplier are legitimately near
identical in embedding space. So are two purchase orders that differ only in
quantity. L3 exists for the case L2 cannot reach — a duplicate whose vendor
name is spelled differently and whose line descriptions were re-typed — and it
pays for that reach with a lower ceiling on what it is allowed to claim.

THE EVIDENCE IS THE TWO CLOSEST PARAGRAPHS, NOT THE COSINE
==========================================================

A number between 0 and 1 is not something a finance reviewer can check. The
mean embedding that produced it is not either — nobody has ever looked at a
384-vector and formed a view.

So L3's evidence is the pair of CHUNKS closest to each other in embedding
space, with their text. That is the thing a human can read and agree or
disagree with, and it is what makes an L3 finding actionable rather than an
appeal to the model's authority.

Finding that pair costs one pass over the cross product of the two documents'
chunks. For two twenty-chunk invoices that is four hundred dot products over
384 floats, computed once per finding rather than once per candidate pair —
this runs only after the threshold has already been cleared.

VENDOR DISAGREEMENT DISQUALIFIES
================================

§5.3 scopes L3 to the same `vendor_key` "or with no vendor extracted". Two
documents that positively identify DIFFERENT suppliers are not duplicates of
each other, whatever the geometry says, and the geometry says a lot: invoices
share a shape, and every invoice is closer to every other invoice than to a
contract.

PURE
====

Standard library, `vocabulary` and `fingerprint`. No Session.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from app.services.radar import fingerprint as fp
from app.services.radar import vocabulary as vocab

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.radar.layers import Candidate, LayerHit, LayerSettings

__all__ = ["LAYER", "evaluate", "closest_chunk_pair"]

LAYER: str = vocab.LAYER_L3


def closest_chunk_pair(
    subject: "Candidate", counterpart: "Candidate"
) -> Optional[tuple[Any, Any, Decimal]]:
    """The two chunks, one from each document, closest in embedding space.

    Ties break on `(chunk_index, chunk_id)` so that reopening a finding shows
    the same paragraphs it showed yesterday. Without the tiebreak, two chunks
    at identical cosine would be ordered by whatever the loader returned, and
    the evidence a reviewer approved would not be the evidence the next reader
    sees.
    """
    best: Optional[tuple[Any, Any, Decimal]] = None
    for left in subject.chunks:
        for right in counterpart.chunks:
            similarity = fp.cosine(left.vector, right.vector)
            if best is None or similarity > best[2]:
                best = (left, right, similarity)
            elif similarity == best[2]:
                current = (best[0].chunk_index, best[0].chunk_id,
                           best[1].chunk_index, best[1].chunk_id)
                contender = (left.chunk_index, left.chunk_id,
                             right.chunk_index, right.chunk_id)
                if contender < current:
                    best = (left, right, similarity)
    return best


def evaluate(
    subject: "Candidate",
    counterpart: "Candidate",
    *,
    settings: "LayerSettings",
) -> Optional["LayerHit"]:
    """Fire on a mean-embedding cosine at or above the threshold."""
    from app.services.radar.layers import LayerHit, _clip  # noqa: PLC0415

    left = subject.fingerprint
    right = counterpart.fingerprint

    if not left.embedding or not right.embedding:
        return None

    # Two documents that positively identify different suppliers are out,
    # however close the geometry. An absent vendor on either side is missing
    # evidence, not contrary evidence, and §5.3 keeps those in scope.
    if left.vendor_key and right.vendor_key and left.vendor_key != right.vendor_key:
        return None

    similarity = fp.cosine(left.embedding, right.embedding)
    if similarity < settings.l3_cosine_min:
        return None

    evidence: list[dict[str, Any]] = [
        {
            "kind": vocab.EVIDENCE_IDENTIFIERS,
            "label": "Document similarity",
            "cosine": str(similarity),
            "threshold": str(settings.l3_cosine_min),
            "embedding_model": left.embedding_model or right.embedding_model,
            "subject": {"work_item_id": left.work_item_id},
            "counterpart": {"work_item_id": right.work_item_id},
            "note": (
                "Mean of each document's chunk embeddings, L2-normalized. "
                "Similarity is evidence, not proof: this layer alone is "
                "capped at MEDIUM severity."
            ),
        }
    ]

    pair = closest_chunk_pair(subject, counterpart)
    if pair is not None:
        near_left, near_right, near_similarity = pair
        evidence.append(
            {
                "kind": vocab.EVIDENCE_CHUNK_PAIR,
                "label": "Closest matching passage",
                "cosine": str(near_similarity),
                "subject": {
                    "work_item_id": left.work_item_id,
                    "chunk_id": near_left.chunk_id,
                    "chunk_index": near_left.chunk_index,
                    "page_number": near_left.page_number,
                    "text": _clip(near_left.text),
                },
                "counterpart": {
                    "work_item_id": right.work_item_id,
                    "chunk_id": near_right.chunk_id,
                    "chunk_index": near_right.chunk_index,
                    "page_number": near_right.page_number,
                    "text": _clip(near_right.text),
                },
            }
        )

    return LayerHit(
        layer=LAYER,
        score=similarity,
        evidence=tuple(evidence),
        metrics={
            "cosine": str(similarity),
            "threshold": str(settings.l3_cosine_min),
            "chunk_pair_found": pair is not None,
        },
    )
