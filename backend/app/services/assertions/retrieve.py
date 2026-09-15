"""ARCH-33 §4.5 — retrieval, restricted to one work item, seeded per family.

WHY A DEDICATED MODULE AND NOT A CALL TO `retrieval_service`
============================================================

`retrieval_service.hybrid_search` is the right engine and the wrong contract.
It takes a QUERY — one string, written by a human, describing what they want.
An assertion has no such string. It has a compiled plan, a family, and a
tenant's accumulated knowledge of how that family's clauses are worded in
their own paper.

Turning that into a query is the work this module does, and it is the half of
§4.3's self-healing that lives outside ARCH-35.

THE QUERY IS NOT THE ADMINISTRATOR'S SENTENCE
=============================================

This is the single most consequential decision here. An administrator writes

    "payment terms do not exceed Net 30"

and the paragraph that decides it says

    "payable within thirty (30) days of receipt of a correct invoice"

They share almost no vocabulary. Searching with the rule's own words finds the
definitions section, the schedule of fees, and the signature block — and the
parser then reports UNDETERMINED on a contract whose payment clause is two
pages away. Every symptom of that failure looks like "the documents got
harder", never like "retrieval asked the wrong question".

So the query is built from three sources, in this order:

  1. `vocabulary.SEED_PHRASES[family]` — how contracts say it, shipped.
  2. `plan.retrieval_seeds` — how contracts say it for this SUBJECT, from the
     compiler's own subject table.
  3. `assertion_retrieval_phrases` for (organization_id, family), ordered by
     `hits` — how contracts say it for THIS TENANT, learned from reviewers.

The administrator's sentence contributes only for the `llm` family, which by
definition has no typed subject to seed from.

WORK-ITEM RESTRICTION IS A CORRECTNESS PROPERTY, NOT A PERFORMANCE ONE
======================================================================

`work_item_ids=[work_item_id]` is passed on every call. An assertion answers a
question about ONE document, and a retriever that reached across the workspace
would answer it with a paragraph from a different supplier's contract — which
then passes or fails a document on evidence that is not in it.

`retrieval_service` already distinguishes `None` (whole workspace) from `[]`
(nothing) from a list, so the restriction is a single argument. What this
module adds is that the argument is never optional.

THE SESSION LIVES HERE
======================

This module holds a `Session`. `families/`, `compiler.py`, `quotecheck.py`,
`features.py`, `calibration.py` and `routing.py` do not. The boundary is
`to_chunks()` below: ARCH-11 result dictionaries go in, pure `families.Chunk`
values come out, and everything downstream of that line is gateable offline.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assertion import AssertionRetrievalPhrase
from app.services.assertions import vocabulary as vocab
from app.services.assertions.families import Chunk

logger = logging.getLogger("app.services.assertions.retrieve")

__all__ = [
    "RetrievalOutcome",
    "build_query",
    "learned_phrases",
    "retrieve",
    "to_chunks",
    "record_hits",
    "MAX_LEARNED_PHRASES",
    "DEFAULT_TOP_K",
]

#: How many tenant-learned phrases join the query. Unbounded, the query
#: becomes a bag of every phrase any reviewer ever picked, the lexical arm
#: matches everything, and the fused ranking collapses toward document order.
MAX_LEARNED_PHRASES: int = 12

#: Chunks retrieved per evaluation. Small on purpose: the parsers report
#: contradictions across chunks, and widening the window past the clause that
#: answers the question mostly adds paragraphs that mention the subject
#: without deciding it.
DEFAULT_TOP_K: int = 6

#: The `similarity_threshold` handed to ARCH-11. Deliberately permissive: the
#: parser is the filter, not the retriever. A clause that says "Net 30" is the
#: right answer even when the embedding barely matched, and discarding it here
#: means the assertion reports UNDETERMINED on a document that plainly
#: contained its own answer.
SIMILARITY_THRESHOLD: float = 0.0


@dataclass(frozen=True)
class RetrievalOutcome:
    chunks: tuple[Chunk, ...]
    query: str
    #: The phrases that built the query, in the order they were used. Recorded
    #: in evidence so a reviewer looking at a wrong paragraph can see what was
    #: actually searched for, and so "Wrong paragraph" has something to teach
    #: against.
    phrases: tuple[str, ...] = field(default_factory=tuple)
    learned_used: tuple[str, ...] = field(default_factory=tuple)

    @property
    def top_score(self) -> float:
        return max((chunk.retrieval_score for chunk in self.chunks), default=0.0)

    def as_details(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "chunks": len(self.chunks),
            "top_score": round(self.top_score, 5),
            "learned_phrases": list(self.learned_used),
        }


def learned_phrases(
    db: Session, *, organization_id: uuid.UUID, family: str, limit: int = MAX_LEARNED_PHRASES
) -> tuple[str, ...]:
    """Tenant-taught phrases for this family, most productive first.

    Ordered by `hits DESC`, which is what `ix_arp_org_family_hits` exists for.
    A phrase a reviewer taught once and that has never located a paragraph
    since ranks below one that works every week, and the cap then drops it —
    without deleting it, because a phrase that stops working on this year's
    template may be the one that works on the renewal.
    """
    rows = (
        db.execute(
            select(AssertionRetrievalPhrase.phrase)
            .where(
                AssertionRetrievalPhrase.organization_id == organization_id,
                AssertionRetrievalPhrase.family == family,
            )
            .order_by(
                AssertionRetrievalPhrase.hits.desc(),
                AssertionRetrievalPhrase.created_at.asc(),
            )
            .limit(max(0, int(limit)))
        )
        .scalars()
        .all()
    )
    return tuple(str(row) for row in rows if str(row).strip())


def build_query(
    plan: Any, *, learned: Sequence[str] = ()
) -> tuple[str, tuple[str, ...]]:
    """`(query, phrases)` for one plan. Pure — no Session, gated offline.

    Deduplicated case-insensitively while preserving order, because the seed
    table and a tenant's learned table overlap by design: a reviewer picking
    the paragraph that "payable within" already found teaches that phrase
    again, and counting it twice would double its weight in the lexical arm.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def add(phrase: str) -> None:
        cleaned = " ".join((phrase or "").split())
        if not cleaned:
            return
        key = cleaned.casefold()
        if key in seen:
            return
        seen.add(key)
        ordered.append(cleaned)

    for phrase in vocab.seed_phrases_for(plan.family):
        add(phrase)
    for phrase in getattr(plan, "retrieval_seeds", ()) or ():
        add(phrase)
    for phrase in learned:
        add(phrase)

    # The llm family has no typed subject and therefore no seeds. Its only
    # signal is the administrator's own sentence with the grammar stripped,
    # which `compiler._sentence_seeds` already put in `retrieval_seeds`.
    if not ordered:
        add(getattr(plan, "sentence", "") or "")

    return " ".join(ordered), tuple(ordered)


def to_chunks(results: Sequence[dict[str, Any]]) -> tuple[Chunk, ...]:
    """The boundary. ARCH-11 dictionaries in, pure `Chunk` values out.

    Everything above this line may hold a Session. Everything below it is
    gated offline against curated clause text. Keeping the conversion in one
    named function is what makes that claim checkable rather than aspirational.
    """
    chunks: list[Chunk] = []
    for result in results:
        metadata = result.get("metadata") or {}
        text = result.get("text") or ""
        if not str(text).strip():
            continue
        chunks.append(
            Chunk(
                chunk_id=str(result.get("id") or metadata.get("chunk_id") or ""),
                chunk_index=int(
                    result.get("chunk_index") or metadata.get("chunk_index") or 0
                ),
                text=str(text),
                page_number=(
                    int(result["page_number"])
                    if result.get("page_number") is not None
                    else None
                ),
                # ARCH-11 returns several scores depending on which arms ran.
                # `similarity_score` is the one normalised to [0, 1] on every
                # path, so it is the one the feature vector can use without
                # meaning something different per query shape.
                retrieval_score=float(result.get("similarity_score") or 0.0),
                work_item_id=str(
                    result.get("work_item_id") or metadata.get("work_item_id") or ""
                ),
                page_start_char=metadata.get("page_start_char"),
                bbox=metadata.get("bbox"),
            )
        )
    return tuple(chunks)


def retrieve(
    db: Session,
    *,
    plan: Any,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    work_item_id: uuid.UUID,
    top_k: int = DEFAULT_TOP_K,
    request_id: Optional[str] = None,
) -> RetrievalOutcome:
    """ARCH-11 hybrid retrieval, restricted to this work item's chunks."""
    from app.services.retrieval_service import retrieval_service

    learned = learned_phrases(
        db, organization_id=organization_id, family=plan.family
    )
    query, phrases = build_query(plan, learned=learned)

    results = retrieval_service.hybrid_search(
        db=db,
        workspace_id=workspace_id,
        query=query,
        # NEVER None. See the module header: an assertion answers a question
        # about one document, and a retriever reaching across the workspace
        # answers it with somebody else's contract.
        work_item_ids=[str(work_item_id)],
        top_k=max(1, int(top_k)),
        similarity_threshold=SIMILARITY_THRESHOLD,
        request_id=request_id,
    )

    chunks = to_chunks(results)

    logger.info(
        "assertion.retrieved",
        extra={
            "workspace_id": str(workspace_id),
            "work_item_id": str(work_item_id),
            "family": plan.family,
            "chunks": len(chunks),
            "learned_phrases": len(learned),
        },
    )

    return RetrievalOutcome(
        chunks=chunks, query=query, phrases=phrases, learned_used=learned
    )


def record_hits(
    db: Session,
    *,
    organization_id: uuid.UUID,
    family: str,
    phrases: Sequence[str],
) -> int:
    """Credit the phrases that appeared in the paragraph actually relied on.

    Called after an evaluation lands on a quote, with the subset of the query
    phrases that literally occur in it. That is a deliberately strict test: a
    phrase gets credit for FINDING the paragraph, not for having been in the
    query when some other phrase found it. Crediting every phrase on every
    successful evaluation would make `hits` a count of evaluations and the
    ordering meaningless.
    """
    wanted = {phrase.casefold() for phrase in phrases if phrase.strip()}
    if not wanted:
        return 0

    rows = (
        db.execute(
            select(AssertionRetrievalPhrase).where(
                AssertionRetrievalPhrase.organization_id == organization_id,
                AssertionRetrievalPhrase.family == family,
            )
        )
        .scalars()
        .all()
    )
    credited = 0
    for row in rows:
        if row.phrase.casefold() in wanted:
            row.hits = int(row.hits or 0) + 1
            credited += 1
    if credited:
        db.flush()
    return credited