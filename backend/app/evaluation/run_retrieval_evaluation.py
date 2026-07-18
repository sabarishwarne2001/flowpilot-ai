"""
Runs the retrieval evaluation dataset.

Example

python -m app.evaluation.run_retrieval_evaluation
"""

from __future__ import annotations

from statistics import (
    mean,
    median,
)

import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

from app.evaluation.retrieval_dataset import (
    EVALUATION_DATASET,
)
from app.services.retrieval_evaluator import (
    RetrievalEvaluator,
)
from app.services.retrieval_service import (
    retrieval_service,
)

from app.services.embedding_service import embedding_service

EVALUATION_COLLECTION_NAME = "flowpilot_evaluation"


def run() -> None:
    """
    Execute the retrieval evaluation suite.
    """

    evaluator = RetrievalEvaluator(
        retrieval_service,
    )

    results = []

    # -----------------------------
    # Load searchable work items
    # -----------------------------
    work_item_ids = embedding_service.get_searchable_work_item_ids(
        collection_name=EVALUATION_COLLECTION_NAME,
    )

    logger.info(
        "Loaded %d searchable work item(s).",
        len(work_item_ids),
    )

    if not work_item_ids:

        logger.warning(
            "Knowledge base is empty."
        )

        return
    

    logger.info(
        "Running %d retrieval evaluation case(s).",
        len(EVALUATION_DATASET),
    )

    for case in EVALUATION_DATASET:

        result = evaluator.evaluate_case(
            case=case,
            work_item_ids=work_item_ids,
        )

        results.append(result)


    if not results:

        logger.warning(
            "No evaluation results generated."
        )

        return

    logger.info("")

    logger.info("=" * 80)

    passed = sum(
        result.passed
        for result in results
    )

    failed = (
        len(results)
        - passed
    )

    logger.info(
        "Passed Cases          : %d",
        passed,
    )

    logger.info(
        "Failed Cases          : %d",
        failed,
    )

    logger.info(
        "Success Rate          : %.2f%%",
        (
            passed
            / len(results)
        ) * 100,
    )

    logger.info("Retrieval Evaluation Summary")

    logger.info("=" * 80)

    logger.info(
        "Average Recall          : %.3f",
        mean(
            r.recall
            for r in results
        ),
    )

    logger.info(
        "Average Precision       : %.3f",
        mean(
            r.precision
            for r in results
        ),
    )

    logger.info(
        "Average MRR             : %.3f",
        mean(
            r.reciprocal_rank
            for r in results
        ),
    )

    logger.info(
        "Average Chunk Recall    : %.3f",
        mean(
            r.chunk_recall
            for r in results
        ),
    )

    logger.info(
        "Average Chunk Precision : %.3f",
        mean(
            r.chunk_precision
            for r in results
        ),
    )

    logger.info(
        "Average Contamination   : %.3f",
        mean(
            r.contamination
            for r in results
        ),
    )

    logger.info(
        "Average Latency         : %.2f ms",
        mean(
            r.latency_ms
            for r in results
        ),
    )

    logger.info(
        "Average Confidence      : %.3f",
        mean(
            result.average_confidence
            for result in results
        ),
    )

    logger.info(
        "Metadata Accuracy       : %.3f",
        mean(
            result.metadata_accuracy
            for result in results
        ),
    )

    #
    # Confidence statistics
    #
    confidence_values = [
        result.average_confidence
        for result in results
    ]

    logger.info(
        "Minimum Confidence      : %.3f",
        min(confidence_values),
    )

    logger.info(
        "Maximum Confidence      : %.3f",
        max(confidence_values),
    )

    logger.info(
        "Median Confidence       : %.3f",
        median(confidence_values),
    )

    #
    # Latency statistics
    #
    latencies = [
        result.latency_ms
        for result in results
    ]

    logger.info(
        "Minimum Latency         : %.2f ms",
        min(latencies),
    )

    logger.info(
        "Maximum Latency         : %.2f ms",
        max(latencies),
    )

    logger.info(
        "Median Latency          : %.2f ms",
        median(latencies),
    )

    #
    # Overall evaluation result
    #
    logger.info("")

    logger.info("=" * 80)

    logger.info(
        "Overall Evaluation"
    )

    logger.info("=" * 80)

    if failed == 0:

        logger.info(
            "Overall Status         : PASSED"
        )

    else:

        logger.info(
            "Overall Status         : FAILED"
        )

    logger.info(
        "Total Test Cases        : %d",
        len(results),
    )

    logger.info(
        "Passed Test Cases       : %d",
        passed,
    )

    logger.info(
        "Failed Test Cases       : %d",
        failed,
    )

    logger.info(
        "Overall Success Rate    : %.2f%%",
        (passed / len(results)) * 100,
    )

    logger.info("=" * 80)

    logger.info("=" * 80)

    logger.info("")

    logger.info("=" * 80)

    logger.info(
        "Failed Retrieval Cases"
    )

    logger.info("=" * 80)

    failed_results = [

        result

        for result in results

        if not result.passed

    ]

    if not failed_results:

        logger.info(
            "No failed retrieval cases."
        )

    else:

        for result in failed_results:

            logger.info("")

            logger.info(
                "Query: %s",
                result.query,
            )

            logger.info(
                "Recall: %.3f",
                result.recall,
            )

            logger.info(
                "Precision: %.3f",
                result.precision,
            )

            logger.info(
                "MRR: %.3f",
                result.reciprocal_rank,
            )

            logger.info(
                "Average Confidence: %.3f",
                result.average_confidence,
            )

            logger.info(
                "Metadata Accuracy: %.3f",
                result.metadata_accuracy,
            )

            logger.info(
                "Failures:"
            )

            for reason in result.failures:

                logger.info(
                    "  - %s",
                    reason,
                )


if __name__ == "__main__":
    run()