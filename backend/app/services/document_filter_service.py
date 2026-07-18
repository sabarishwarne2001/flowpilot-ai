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

logger = logging.getLogger(__name__)


class DocumentFilterService:
    """
    Filters weak documents from hybrid retrieval results.
    """

    def filter_documents(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Remove weak documents while preserving all chunks from
        the strongest documents.

        Documents are scored using their highest rerank score.
        Documents whose score is sufficiently close to the best
        document are retained.

        This adaptive strategy avoids brittle fixed thresholds.
        """

        if len(results) <= 1:
            return results

        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)

        #
        # Group chunks by document.
        #
        for result in results:

            metadata = result.get(
                "metadata",
                {},
            )

            work_item_id = metadata.get(
                "work_item_id",
            )

            grouped[work_item_id].append(result)

        #
        # Cross-document filtering is only meaningful when
        # multiple documents are present.
        #
        if len(grouped) <= 1:

            logger.info(
                "Skipping cross-document filtering (%d document(s)).",
                len(grouped),
            )

            return results

        #
        # Compute a production-grade score for each document.
        #
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

            top_scores = scores[
                : settings.DOCUMENT_SCORE_TOP_K
            ]

            if not top_scores:

                document_scores[
                    work_item_id
                ] = float("-inf")

                continue

            weighted_score = 0.0
            total_weight = 0.0
            weight = 1.0

            for score in top_scores:

                weighted_score += score * weight
                total_weight += weight
                weight *= 0.5

            document_scores[
                work_item_id
            ] = (
                weighted_score
                / total_weight
            )

            logger.info(
                "Document %s score = %.4f",
                work_item_id,
                document_scores[
                    work_item_id
                ],
            )

        logger.info(
            "Document ranking:"
        )

        for work_item_id, score in sorted(
            document_scores.items(),
            key=lambda item: item[1],
            reverse=True,
        ):

            logger.info(
                "%s -> %.4f",
                work_item_id,
                score,
            )

        #
        # Determine the strongest document.
        #
        highest_score = max(
            document_scores.values()
        )

        #
        # Production-grade adaptive threshold.
        #
        # CrossEncoder scores may be positive or negative.
        # Instead of multiplying the best score (which breaks
        # for negative values), retain every document whose
        # score is within a configurable margin of the best.
        #
        score_margin = settings.DOCUMENT_FILTER_MARGIN

        minimum_score = (
            highest_score - score_margin
        )

        logger.info(
            "Best document score: %.4f",
            highest_score,
        )

        logger.info(
            "Document filter margin: %.4f",
            score_margin,
        )

        logger.info(
            "Document filter threshold: %.4f",
            minimum_score,
        )

        kept_documents = {

            work_item_id

            for work_item_id, score in document_scores.items()

            if score >= minimum_score
        }

        logger.info(
            "Keeping %d of %d document(s).",
            len(kept_documents),
            len(document_scores),
        )

        filtered_results = [

            result

            for result in results

            if result.get(
                "metadata",
                {},
            ).get(
                "work_item_id",
            ) in kept_documents

        ]

        logger.info(
            "Cross-document filtering reduced %d chunks to %d.",
            len(results),
            len(filtered_results),
        )

        for result in filtered_results:

            metadata = result.get(
                "metadata",
                {},
            )

            logger.info(
                "Selected: %s | Page %s | Chunk %s | %.4f",
                metadata.get(
                    "original_filename",
                    "Unknown",
                ),
                metadata.get(
                    "page_number",
                    "-",
                ),
                metadata.get(
                    "chunk_index",
                    "-",
                ),
                result.get(
                    "rerank_score",
                    0.0,
                ),
            )

        return filtered_results

document_filter_service = DocumentFilterService()