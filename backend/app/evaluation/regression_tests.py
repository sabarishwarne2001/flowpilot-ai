"""
Retrieval regression tests.
Runs the evaluation suite and validates that retrieval quality remains above production thresholds.
"""
from __future__ import annotations
from dataclasses import dataclass
from app.core.config import settings
from app.services.retrieval_evaluator import RetrievalEvaluationResult
from app.core.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RegressionThresholds:
    """
    Production retrieval quality thresholds.
    """
    recall: float = settings.RETRIEVAL_MIN_RECALL
    precision: float = settings.RETRIEVAL_MIN_PRECISION
    reciprocal_rank: float = settings.RETRIEVAL_MIN_MRR
    contamination: float = settings.RETRIEVAL_MAX_CONTAMINATION
    latency_ms: float = settings.RETRIEVAL_MAX_LATENCY_MS


class RetrievalRegressionTester:
    """
    Validates retrieval metrics against production thresholds.
    """
    def __init__(self, thresholds: RegressionThresholds | None = None):
        self.thresholds = thresholds or RegressionThresholds()

    def validate(self, results: list[RetrievalEvaluationResult]) -> bool:
        """
        Validate retrieval evaluation results.

        Returns
        -------
        bool
            True if every metric satisfies production thresholds.
        """
        passed = True

        for result in results:
            logger.info("-" * 80)
            logger.info("Query: %s", result.query)

            passed = passed and self._check_minimum("Recall", result.recall, self.thresholds.recall)
            passed = passed and self._check_minimum("Precision", result.precision, self.thresholds.precision)
            passed = passed and self._check_minimum("MRR", result.reciprocal_rank, self.thresholds.reciprocal_rank)
            passed = passed and self._check_maximum("Contamination", result.contamination, self.thresholds.contamination)
            passed = passed and self._check_maximum("Latency (ms)", result.latency_ms, self.thresholds.latency_ms)
            
            logger.info("-" * 80)

            # Fail-fast logic inside the loop checks immediately after evaluating the query
            if settings.RETRIEVAL_FAIL_FAST and not passed:
                logger.error("Stopping regression after first failure.")
                return False

        if passed:
            logger.info("✅ Retrieval regression PASSED.")
        else:
            logger.error("❌ Retrieval regression FAILED.")

        logger.info("=" * 80)
        logger.info("Regression Summary")
        logger.info("Queries Tested : %d", len(results))
        logger.info("Overall Status : %s", "PASS" if passed else "FAIL")
        logger.info("=" * 80)

        return passed

    def _check_minimum(self, name: str, value: float, threshold: float) -> bool:
        if value >= threshold:
            logger.info("PASS %-18s %.3f >= %.3f", name, value, threshold)
            return True
        logger.error("FAIL %-18s %.3f < %.3f", name, value, threshold)
        return False

    def _check_maximum(self, name: str, value: float, threshold: float) -> bool:
        if value <= threshold:
            logger.info("PASS %-18s %.3f <= %.3f", name, value, threshold)
            return True
        logger.error("FAIL %-18s %.3f > %.3f", name, value, threshold)
        return False
