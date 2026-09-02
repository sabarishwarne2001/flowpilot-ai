"""
Cross-document filtering service.

Ranks documents using the reranked chunk scores and removes
documents that are significantly less relevant than the best
matching document.

This improves multi-document retrieval quality before context
assembly.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.core.config import settings

logger = logging.getLogger("app.services.document_filter_service")


class DocumentFilterService:
    """
    Filters weak documents from hybrid retrieval results.
    """

    def filter_documents(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if len(results) <= 1:
            return results

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        for result in results:
            metadata = result.get("metadata", {})
            raw_id = metadata.get("work_item_id")
            if not raw_id:
                continue

            work_item_id = str(raw_id)
            grouped[work_item_id].append(result)

        if len(grouped) <= 1:
            logger.info(
                "Skipping cross-document filtering (%d document(s)).",
                len(grouped),
            )
            return results

        document_scores: dict[str, float] = {}

        for work_item_id, chunks in grouped.items():
            scores = sorted(
                (
                    float(
                        chunk.get(
                            "retrieval_confidence",
                            chunk.get(
                                "rerank_score",
                                chunk.get(
                                    "rrf_score",
                                    chunk.get(
                                        "similarity_score",
                                        -1.0,
                                    ),
                                ),
                            ),
                        )
                    )
                    for chunk in chunks
                ),
                reverse=True,
            )

            top_scores = scores[: settings.DOCUMENT_SCORE_TOP_K]

            if not top_scores:
                document_scores[work_item_id] = float("-inf")
                continue

            weighted_score = 0.0
            total_weight = 0.0
            weight = 1.0

            for score in top_scores:
                weighted_score += score * weight
                total_weight += weight
                weight *= 0.5

            document_scores[work_item_id] = weighted_score / total_weight

        highest_score = max(document_scores.values(), default=0.0)
        score_margin = float(settings.DOCUMENT_FILTER_MARGIN)
        minimum_score = highest_score - score_margin

        kept_documents = {
            str(wid)
            for wid, score in document_scores.items()
            if score >= minimum_score
        }

        filtered_results = [
            result
            for result in results
            if str(result.get("metadata", {}).get("work_item_id", "")) in kept_documents
        ]

        logger.info(
            "Cross-document filtering reduced %d chunks to %d.",
            len(results),
            len(filtered_results),
        )

        return filtered_results


document_filter_service = DocumentFilterService()

__all__ = ["DocumentFilterService", "document_filter_service"]
