"""
Cross-Encoder Re-ranking Service.

Provides production-grade semantic reranking of retrieved
document chunks using a CrossEncoder model.
"""

from __future__ import annotations

from app.core.config import settings

from sentence_transformers import CrossEncoder

import logging

logger = logging.getLogger(__name__)


class RerankerService:

    """
    Cross-encoder based reranking.
    """

    MODEL_NAME = "BAAI/bge-reranker-base"


    def __init__(self) -> None:

        logger.info(
            "Loading CrossEncoder: %s",
            self.MODEL_NAME,
        )

        self.model = CrossEncoder(
            self.MODEL_NAME,
        )


    def rerank(
        self,
        *,
        query: str,
        results: list[dict],
    ) -> list[dict]:
        """
        Re-rank retrieved chunks using the CrossEncoder.

        The CrossEncoder jointly evaluates the user query and
        each retrieved chunk, producing a relevance score that
        is significantly more accurate than embedding similarity.

        Returns the same result structure with an additional
        `rerank_score` field.
        """

        if not results:
            return results
        
        if len(results) < settings.RERANK_MIN_RESULTS:

            logger.info(
                "Skipping reranker (%d results).",
                len(results),
            )

            return results

        candidate_count = min(
            len(results),
            settings.RERANK_MAX_CANDIDATES,
        )

        logger.info(
            "CrossEncoder reranking %d candidate(s).",
            candidate_count,
        )

        results = results[
            : candidate_count
        ]

        #
        # Build context-aware query-document pairs.
        #
        pairs = []

        for result in results:

            metadata = result.get(
                "metadata",
                {},
            )

            filename = metadata.get(
                "original_filename",
                "Unknown Document",
            )

            enriched_text = (
                f"Document: {filename}\n\n"
                f"Content:\n"
                f"{result['text']}"
            )

            pairs.append(
                (
                    query,
                    enriched_text,
                )
            )
            

        #
        # Predict relevance scores.
        #
        scores = self.model.predict(
            pairs,
        )


        #
        # Attach scores.
        #
        for result, score in zip(
            results,
            scores,
        ):

            result["rerank_score"] = float(
                score
            )

        #
        # Highest score first.
        #
        results.sort(
            key=lambda item: (
                item.get(
                    "rerank_score",
                    float("-inf"),
                )
                +
                item.get(
                    "rrf_score",
                    0.0,
                )
            ),
            reverse=True,
        )

        results = results[
            : settings.RERANK_FINAL_RESULTS
        ]

        logger.info(
            "CrossEncoder selected top %d chunk(s).",
            len(results),
        )

        return results
    

    def log_top_results(
        self,
        results: list[dict],
        top_n: int | None = None,
    ) -> None:
        """
        Log the highest ranked chunks after reranking.
        """

        logger.info(
            "Top %d reranked chunks:",
            top_n or len(results),
        )

        for index, result in enumerate(
            results[
                :
                top_n
                or len(results)
            ],
            start=1,
        ):

            metadata = result.get(
                "metadata",
                {},
            )

            logger.info(
                "%d | %.4f | %s | Page %s | Chunk %s",
                index,
                result.get(
                    "rerank_score",
                    0.0,
                ),
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
            )
            

reranker_service = RerankerService()