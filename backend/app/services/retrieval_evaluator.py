"""
Retrieval evaluation framework.

Measures retrieval quality independently from the assistant. This module is
never used during normal inference. It is intended for offline evaluation and
regression testing.
"""

from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

EVALUATION_COLLECTION_NAME = "flowpilot_evaluation"
DEFAULT_TOP_K = 10
DEFAULT_SIMILARITY_THRESHOLD = 0.12


@dataclass(slots=True)
class RetrievalEvaluationCase:
    """
    One production retrieval evaluation case.
    """

    # User query
    query: str
    # Expected documents.
    expected_documents: list[str]
    # Optional expected chunks.
    expected_chunks: list[str] | None = None
    # Optional retrieval intent.
    intent: str | None = None
    # Dataset category.
    category: str = "general"
    # easy / medium / hard
    difficulty: str = "medium"
    # Documents that MUST appear.
    must_retrieve: list[str] | None = None
    # Documents that MUST NOT appear.
    must_not_retrieve: list[str] | None = None
    # Expected metadata filters.
    expected_metadata: dict[str, str] | None = None
    # Acceptance thresholds.
    minimum_confidence: float = 0.60
    minimum_recall: float = 1.00
    minimum_precision: float = 0.80
    minimum_mrr: float = 1.00


@dataclass(slots=True)
class RetrievalEvaluationResult:
    """
    Production retrieval evaluation metrics.
    """

    # Query
    query: str
    # Performance
    latency_ms: float
    # Document metrics
    recall: float
    precision: float
    reciprocal_rank: float
    ndcg: float
    hit: bool
    contamination: float
    # Chunk metrics
    chunk_recall: float
    chunk_precision: float
    # Confidence metrics
    average_confidence: float
    minimum_confidence: float
    confidence_pass: bool
    # Metadata metrics
    metadata_accuracy: float
    # Overall result
    passed: bool
    # Failure reasons
    failures: list[str]


class RetrievalEvaluator:
    """
    Offline retrieval evaluator.
    """

    def __init__(self, retrieval_service):
        self.retrieval_service = retrieval_service

    def evaluate_case(
        self,
        case: RetrievalEvaluationCase,
        work_item_ids: list[str],
    ) -> RetrievalEvaluationResult:
        """
        Evaluate one retrieval query using a unified evaluation case configuration.
        """
        start = time.perf_counter()
        results = self.retrieval_service.hybrid_search(
            query=case.query,
            work_item_ids=work_item_ids,
            top_k=DEFAULT_TOP_K,
            similarity_threshold=DEFAULT_SIMILARITY_THRESHOLD,
            collection_name=EVALUATION_COLLECTION_NAME,
        )
        latency = (time.perf_counter() - start) * 1000

        retrieved_chunks = [result["id"] for result in results]
        retrieved_documents = [
            result["metadata"]["original_filename"] for result in results
        ]

        recall = self._calculate_recall(case.expected_documents, retrieved_documents)
        precision = self._calculate_precision(case.expected_documents, retrieved_documents)
        reciprocal_rank = self._calculate_mrr(case.expected_documents, retrieved_documents)
        ndcg = self._calculate_ndcg(case.expected_documents, retrieved_documents)
        contamination = self._calculate_contamination(case.expected_documents, retrieved_documents)

        chunk_recall = 0.0
        chunk_precision = 0.0
        average_confidence = 0.0
        minimum_confidence = 0.0
        confidence_pass = True
        metadata_accuracy = 1.0
        failures: list[str] = []

        if case.expected_chunks:
            chunk_recall = self._calculate_recall(case.expected_chunks, retrieved_chunks)
            chunk_precision = self._calculate_precision(case.expected_chunks, retrieved_chunks)

        # -----------------------------
        # Confidence evaluation
        # -----------------------------
        confidences = [
            float(result.get("retrieval_confidence", 0.0)) for result in results
        ]
        if confidences:
            average_confidence = sum(confidences) / len(confidences)
            minimum_confidence = min(confidences)
            # confidence_pass = minimum_confidence >= case.minimum_confidence
            average_confidence >= case.minimum_confidence
        else:
            confidence_pass = False

        # -----------------------------
        # Metadata validation
        # -----------------------------
        if case.expected_metadata:
            metadata_matches = 0
            for result in results:
                metadata = result.get("metadata", {})
                matched = True
                for key, expected_value in case.expected_metadata.items():
                    if metadata.get(key) != expected_value:
                        matched = False
                        break
                if matched:
                    metadata_matches += 1

            if results:
                metadata_accuracy = metadata_matches / len(results)
            else:
                metadata_accuracy = 0.0

        # -----------------------------
        # Must retrieve validation
        # -----------------------------
        if case.must_retrieve:
            missing_documents = [
                document for document in case.must_retrieve if document not in retrieved_documents
            ]
            if missing_documents:
                failures.append("Missing required document(s): " + ", ".join(missing_documents))

        # -----------------------------
        # Must NOT retrieve validation
        # -----------------------------
        if case.must_not_retrieve:
            unexpected_documents = [
                document for document in retrieved_documents if document in case.must_not_retrieve
            ]
            if unexpected_documents:
                failures.append(
                    "Unexpected document(s) retrieved: "
                    + ", ".join(sorted(set(unexpected_documents)))
                )

        # -----------------------------
        # Ranking quality validation
        # -----------------------------
        if retrieved_documents and case.expected_documents:
            top_document = retrieved_documents[0]
            if top_document not in case.expected_documents:
                failures.append("Top-ranked document is incorrect.")

        # -----------------------------
        # Citation validation
        # -----------------------------
        if results:
            citation_matches = 0
            for result in results:
                metadata = result.get("metadata", {})
                if (
                    metadata.get("original_filename")
                    and metadata.get("page_number") is not None
                    and metadata.get("chunk_index") is not None
                ):
                    citation_matches += 1

            citation_accuracy = citation_matches / len(results)
            if citation_accuracy < 1.0:
                failures.append("Citation metadata incomplete.")

        # -----------------------------
        # Pass / Fail
        # -----------------------------
        if recall < case.minimum_recall:
            failures.append("Recall below threshold.")
        if precision < case.minimum_precision:
            failures.append("Precision below threshold.")
        if reciprocal_rank < case.minimum_mrr:
            failures.append("MRR below threshold.")
        if not confidence_pass:
            failures.append("Confidence below threshold.")

        passed = len(failures) == 0
        hit = recall > 0

        logger.info(
            (
                "Query='%s' | "
                "Recall=%.3f | "
                "Precision=%.3f | "
                "MRR=%.3f | "
                "Chunk Recall=%.3f | "
                "Chunk Precision=%.3f | "
                "Contamination=%.3f | "
                "Latency=%.2f ms"
            ),
            case.query,
            recall,
            precision,
            reciprocal_rank,
            chunk_recall,
            chunk_precision,
            contamination,
            latency,
        )

        return RetrievalEvaluationResult(
            query=case.query,
            latency_ms=latency,
            recall=recall,
            precision=precision,
            reciprocal_rank=reciprocal_rank,
            ndcg=ndcg,
            hit=hit,
            contamination=contamination,
            chunk_recall=chunk_recall,
            chunk_precision=chunk_precision,
            average_confidence=average_confidence,
            minimum_confidence=minimum_confidence,
            confidence_pass=confidence_pass,
            metadata_accuracy=metadata_accuracy,
            passed=passed,
            failures=failures,
        )

    def _calculate_recall(self, expected: list[str], retrieved: list[str]) -> float:
        """
        Recall@K Measures how many expected documents were successfully retrieved.
        """
        if not expected:
            return 0.0
        expected_set = set(expected)
        retrieved_set = set(retrieved)
        hits = len(expected_set.intersection(retrieved_set))
        return hits / len(expected_set)

    def _calculate_precision(self, expected: list[str], retrieved: list[str]) -> float:
        """
        Precision@K Measures how many retrieved documents are actually relevant.
        """
        if not retrieved:
            return 0.0
        expected_set = set(expected)
        retrieved_set = set(retrieved)
        hits = len(expected_set.intersection(retrieved_set))
        return hits / len(retrieved_set)

    def _calculate_mrr(self, expected: list[str], retrieved: list[str]) -> float:
        """
        Mean Reciprocal Rank.
        """
        expected_set = set(expected)
        for rank, document in enumerate(retrieved, start=1):
            if document in expected_set:
                return 1.0 / rank
        return 0.0
    
    def _calculate_ndcg(self, expected: list[str], retrieved: list[str]) -> float:
        """
        Compute Normalized Discounted Cumulative Gain (NDCG).

        Assumes binary relevance: relevant = 1, non-relevant = 0
        """
        if not expected:
            return 0.0

        expected_set = set(expected)
        dcg = 0.0
        for rank, document in enumerate(retrieved, start=1):
            if document in expected_set:
                dcg += 1.0 / math.log2(rank + 1)

        ideal_hits = min(len(expected), len(retrieved))
        if ideal_hits == 0:
            return 0.0

        idcg = 0.0
        for rank in range(1, ideal_hits + 1):
            idcg += 1.0 / math.log2(rank + 1)

        if idcg == 0:
            return 0.0

        return dcg / idcg

    def _calculate_contamination(self, expected: list[str], retrieved: list[str]) -> float:
        """
        Cross-document contamination.

        Percentage of retrieved documents that should NOT have appeared.
        """
        if not retrieved:
            return 0.0

        expected_set = set(expected)
        contamination = sum(1 for document in retrieved if document not in expected_set)
        return contamination / len(retrieved)


retrieval_evaluator = None  # To be bound with instance initialization


    
    
