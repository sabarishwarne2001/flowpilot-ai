"""
Citation ranking service.

Ranks retrieved chunks and selects the strongest evidence
to return alongside assistant responses.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class CitationService:
    """
    Select high-quality citations for assistant responses.
    """

    def _normalize_scores(
        self,
        values: list[float],
    ) -> list[float]:
        """
        Min-Max normalize scores into [0,1].
        """

        if not values:
            return []

        minimum = min(values)
        maximum = max(values)

        if maximum == minimum:
            return [1.0] * len(values)

        return [
            (value - minimum)
            / (maximum - minimum)
            for value in values
        ]

    def rank_citations(
        self,
        results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """
        Rank citations using multiple normalized retrieval signals.
        """

        if not results:
            return []

        rerank_scores = [
            result.get(
                "rerank_score",
                0.0,
            )
            for result in results
        ]

        rrf_scores = [
            result.get(
                "rrf_score",
                0.0,
            )
            for result in results
        ]

        semantic_scores = [
            result.get(
                "similarity_score",
                0.0,
            )
            for result in results
        ]

        rerank_scores = self._normalize_scores(
            rerank_scores,
        )

        rrf_scores = self._normalize_scores(
            rrf_scores,
        )

        semantic_scores = self._normalize_scores(
            semantic_scores,
        )

        ranked: list[dict[str, Any]] = []

        for (
            result,
            rerank,
            rrf,
            semantic,
        ) in zip(
            results,
            rerank_scores,
            rrf_scores,
            semantic_scores,
        ):

            confidence = (
                settings.CITATION_RERANK_WEIGHT
                * rerank
                +
                settings.CITATION_RRF_WEIGHT
                * rrf
                +
                settings.CITATION_SEMANTIC_WEIGHT
                * semantic
            )

            result["citation_score"] = confidence

            ranked.append(result)

        ranked.sort(
            key=lambda item: item[
                "citation_score"
            ],
            reverse=True,
        )

        ranked = ranked[
            : settings.MAX_RESPONSE_CITATIONS
        ]

        logger.info(
            "Selected %d citation(s).",
            len(ranked),
        )

        for result in ranked:

            metadata = result.get(
                "metadata",
                {},
            )

            logger.info(
                "%s | Page %s | Chunk %s | Citation %.4f",
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
                    "citation_score",
                    0.0,
                ),
            )

        return ranked


citation_service = CitationService()