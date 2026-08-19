"""ARCH-11 Step 1 — retrieval metrics, computed against span labels.

Separate from `retrieval_evaluator.py` on purpose. That module measures
*document*-level retrieval against filename lists, calls `hybrid_search` with a
`collection_name=` argument the current signature does not accept, and reads a
`flowpilot_evaluation` collection that the workspace-partitioned store no longer
writes to. It is Sprint-5 era and it will be retired in Step 9. Rewriting it
now would mean rewriting it again after the migration.

What this module measures, and why each one:

- **`span_recall`** — of the answer spans this question needs, how many appear
  in some retrieved chunk. This is the number the phase lives or dies on, and
  it is re-chunking-invariant.
- **`document_recall`** — did the right *document* appear at all. Coarser, and
  much less sensitive to chunk boundaries, which makes it the useful sanity
  check when span recall moves: if span recall falls and document recall holds,
  the chunker changed; if both fall, retrieval changed.
- **`chunk_precision`** — of the chunks returned, how many carried a span.
  Low precision at high recall means the assistant's context window is being
  filled with filler, which is an ARCH-12 cost problem before it is a quality
  problem.
- **`mrr`** — reciprocal rank of the first span-bearing chunk. Position
  matters because the context window truncates and the reranker's input is
  capped at `RERANK_MAX_CANDIDATES`.
- **`contamination`** — fraction of retrieved chunks from a forbidden
  document, **or from any work item outside the asking workspace**. The second
  clause is not a quality metric. It is a leak detector, it is reported
  separately as `cross_tenant_hits`, and the only acceptable value is zero.
- **`latency_ms`** — measured around the retrieval call only, so it is
  comparable across steps even as the surrounding pipeline changes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional, Sequence

from app.evaluation.golden_set import (
    GoldenQuestion,
    GoldenSet,
    normalize,
    spans_covered,
)


@dataclass
class QuestionResult:
    question_id: str
    workspace_alias: str
    query: str
    latency_ms: float
    retrieved: int
    span_recall: float
    document_recall: float
    chunk_precision: float
    document_precision: float
    mrr: float
    contamination: float
    cross_tenant_hits: int
    covered_spans: list[str] = field(default_factory=list)
    missed_spans: list[str] = field(default_factory=list)
    retrieved_chunk_ids: list[str] = field(default_factory=list)
    retrieved_work_item_ids: list[str] = field(default_factory=list)
    error: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "workspace_alias": self.workspace_alias,
            "query": self.query,
            "latency_ms": round(self.latency_ms, 3),
            "retrieved": self.retrieved,
            "span_recall": round(self.span_recall, 4),
            "document_recall": round(self.document_recall, 4),
            "chunk_precision": round(self.chunk_precision, 4),
            "document_precision": round(self.document_precision, 4),
            "mrr": round(self.mrr, 4),
            "contamination": round(self.contamination, 4),
            "cross_tenant_hits": self.cross_tenant_hits,
            "covered_spans": self.covered_spans,
            "missed_spans": self.missed_spans,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "retrieved_work_item_ids": self.retrieved_work_item_ids,
            "error": self.error,
        }


def _text_of(result: dict[str, Any]) -> str:
    return result.get("text") or result.get("document") or ""


def _work_item_of(result: dict[str, Any]) -> str:
    metadata = result.get("metadata") or {}
    return str(result.get("work_item_id") or metadata.get("work_item_id") or "")


def _chunk_id_of(result: dict[str, Any]) -> str:
    return str(result.get("id") or "")


def score_question(
    question: GoldenQuestion,
    results: Sequence[dict[str, Any]],
    *,
    golden: GoldenSet,
    latency_ms: float,
    error: Optional[str] = None,
) -> QuestionResult:
    spans = question.normalized_spans
    texts = [_text_of(result) for result in results]
    work_item_ids = [_work_item_of(result) for result in results]
    chunk_ids = [_chunk_id_of(result) for result in results]

    relevant_ids = {
        str(golden.document(alias).work_item_id)
        for alias in question.relevant_document_aliases
    }
    forbidden_ids = {
        str(golden.document(alias).work_item_id)
        for alias in question.forbidden_document_aliases
    }
    workspace_ids = {
        str(document.work_item_id)
        for document in golden.documents_for_workspace(question.workspace_alias)
    }

    covered = spans_covered(texts, spans)
    span_recall = len(covered) / len(spans) if spans else 0.0

    document_recall = (
        len(relevant_ids & set(work_item_ids)) / len(relevant_ids)
        if relevant_ids
        else 0.0
    )

    span_bearing = [
        index
        for index, text in enumerate(texts)
        if any(span in normalize(text) for span in spans)
    ]
    chunk_precision = len(span_bearing) / len(results) if results else 0.0
    document_precision = (
        sum(1 for wid in work_item_ids if wid in relevant_ids) / len(results)
        if results
        else 0.0
    )
    mrr = 1.0 / (span_bearing[0] + 1) if span_bearing else 0.0

    # Anything from a work item that is not part of this workspace's golden
    # documents is either an unlabelled document in the same tenant or a leak.
    # `known_ids` is every golden document across every workspace, so a hit
    # outside `workspace_ids` but inside `known_ids` is unambiguously foreign.
    known_ids = {str(document.work_item_id) for document in golden.documents}
    cross_tenant_hits = sum(
        1 for wid in work_item_ids if wid in known_ids and wid not in workspace_ids
    )
    contaminated = sum(1 for wid in work_item_ids if wid in forbidden_ids)
    contamination = (
        (contaminated + cross_tenant_hits) / len(results) if results else 0.0
    )

    return QuestionResult(
        question_id=question.id,
        workspace_alias=question.workspace_alias,
        query=question.query,
        latency_ms=latency_ms,
        retrieved=len(results),
        span_recall=span_recall,
        document_recall=document_recall,
        chunk_precision=chunk_precision,
        document_precision=document_precision,
        mrr=mrr,
        contamination=contamination,
        cross_tenant_hits=cross_tenant_hits,
        covered_spans=sorted(covered),
        missed_spans=sorted(set(spans) - covered),
        retrieved_chunk_ids=chunk_ids,
        retrieved_work_item_ids=work_item_ids,
        error=error,
    )


def _percentile(values: Sequence[float], fraction: float) -> float:
    """Nearest-rank percentile. Explicit because `statistics.quantiles`
    interpolates, and an interpolated p95 over 50 samples is a number nobody
    can reproduce by hand from the per-question list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-fraction * len(ordered) // 1))))
    return ordered[rank - 1]


def aggregate(results: Iterable[QuestionResult]) -> dict[str, Any]:
    rows = [row for row in results]
    scored = [row for row in rows if row.error is None]
    if not scored:
        return {
            "questions": len(rows),
            "scored": 0,
            "errors": len(rows),
            "note": "no question produced a scorable result",
        }

    latencies = [row.latency_ms for row in scored]
    return {
        "questions": len(rows),
        "scored": len(scored),
        "errors": len(rows) - len(scored),
        "span_recall": round(statistics.fmean(r.span_recall for r in scored), 4),
        "document_recall": round(
            statistics.fmean(r.document_recall for r in scored), 4
        ),
        "chunk_precision": round(
            statistics.fmean(r.chunk_precision for r in scored), 4
        ),
        "document_precision": round(
            statistics.fmean(r.document_precision for r in scored), 4
        ),
        "mrr": round(statistics.fmean(r.mrr for r in scored), 4),
        "contamination": round(statistics.fmean(r.contamination for r in scored), 4),
        "cross_tenant_hits_total": sum(r.cross_tenant_hits for r in scored),
        "zero_result_questions": sum(1 for r in scored if r.retrieved == 0),
        "latency_ms": {
            "mean": round(statistics.fmean(latencies), 3),
            "p50": round(_percentile(latencies, 0.50), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "p99": round(_percentile(latencies, 0.99), 3),
            "max": round(max(latencies), 3),
        },
    }


__all__ = ["QuestionResult", "aggregate", "score_question"]