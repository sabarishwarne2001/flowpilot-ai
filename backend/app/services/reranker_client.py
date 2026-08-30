"""ARCH-11 Step 7 — the web tier's reranker client.
ARCH-19 §3.3 — degradation vocabulary, counters, and the disabled path.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Optional, Sequence

from app.core.breaker import BreakerOpen, get_breaker
from app.core.config import settings
from app.core.internal_http import (
    InternalServiceClient,
    InternalServiceError,
    InternalServiceTimeout,
)

logger = logging.getLogger("app.services.reranker_client")

BREAKER_NAME = "reranker"
STATUS_OK = "reranked"
STATUS_SKIPPED = "skipped"
STATUS_DISABLED = "disabled"
STATUS_DEGRADED = "degraded"

REASON_BREAKER_OPEN = "breaker_open"
REASON_TIMEOUT = "timeout"
REASON_UNAVAILABLE = "unavailable"
REASON_UNEXPECTED = "unexpected_error"
REASON_MALFORMED = "malformed_response"
REASON_EMPTY = "empty_response"
REASON_DISABLED = "disabled"

DEGRADE_REASONS: frozenset[str] = frozenset(
    {
        REASON_BREAKER_OPEN,
        REASON_TIMEOUT,
        REASON_UNAVAILABLE,
        REASON_UNEXPECTED,
        REASON_MALFORMED,
        REASON_EMPTY,
        REASON_DISABLED,
    }
)

DEGRADED_REASON_LABELS: dict[str, str] = {
    REASON_DISABLED: "RERANKER_DISABLED",
    REASON_TIMEOUT: "TIMEOUT",
    REASON_BREAKER_OPEN: "CIRCUIT_OPEN",
    REASON_UNAVAILABLE: "UNAVAILABLE",
    REASON_MALFORMED: "MALFORMED_RESPONSE",
    REASON_EMPTY: "EMPTY_RESPONSE",
    REASON_UNEXPECTED: "UNEXPECTED_ERROR",
}

DEGRADED_REASON_LABEL_VALUES: frozenset[str] = frozenset(
    DEGRADED_REASON_LABELS.values()
)


def degraded_label(reason: str) -> str:
    return DEGRADED_REASON_LABELS.get(reason, "UNKNOWN")


_metrics_lock = threading.Lock()
_degradation_counts: dict[str, int] = {}
_rerank_calls: dict[str, int] = {"total": 0, "reranked": 0, "degraded": 0}


def _count_degradation(reason: str) -> None:
    label = degraded_label(reason)
    with _metrics_lock:
        _degradation_counts[label] = _degradation_counts.get(label, 0) + 1
        _rerank_calls["degraded"] += 1


def _count_success() -> None:
    with _metrics_lock:
        _rerank_calls["reranked"] += 1


def _count_call() -> None:
    with _metrics_lock:
        _rerank_calls["total"] += 1


def degradation_metrics() -> dict[str, Any]:
    with _metrics_lock:
        by_reason = dict(_degradation_counts)
        calls = dict(_rerank_calls)

    total = calls["total"] or 0
    return {
        "calls_total": total,
        "reranked": calls["reranked"],
        "degraded": calls["degraded"],
        "degraded_ratio": (calls["degraded"] / total) if total else 0.0,
        "by_reason": by_reason,
    }


def reset_degradation_metrics() -> None:
    with _metrics_lock:
        _degradation_counts.clear()
        _rerank_calls.update({"total": 0, "reranked": 0, "degraded": 0})


class RerankerClient:
    def __init__(self) -> None:
        self._client: Optional[InternalServiceClient] = None

    def _get_client(self) -> InternalServiceClient:
        if self._client is None:
            self._client = InternalServiceClient(
                name="reranker",
                base_url=settings.RERANKER_URL,
                token=(
                    settings.RERANKER_INTERNAL_TOKEN.get_secret_value()
                    if settings.RERANKER_INTERNAL_TOKEN
                    else None
                ),
                connect_timeout=settings.RERANKER_CONNECT_TIMEOUT,
                total_timeout=settings.RERANKER_TIMEOUT,
            )
        return self._client

    @property
    def breaker(self):
        return get_breaker(
            BREAKER_NAME,
            failure_threshold=settings.RERANKER_BREAKER_THRESHOLD,
            reset_after=settings.RERANKER_BREAKER_RESET_SECONDS,
        )

    def rerank(
        self,
        *,
        query: str,
        results: list[dict[str, Any]],
        request_id: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        if not results:
            return results

        _count_call()

        if not settings.RERANKER_ENABLED:
            for result in results:
                result["rerank_status"] = STATUS_DISABLED
                result["rerank_degraded_reason"] = REASON_DISABLED
            _count_degradation(REASON_DISABLED)
            logger.info(
                "reranker.degraded",
                extra={
                    "degraded_reason": degraded_label(REASON_DISABLED),
                    "candidates": len(results),
                },
            )
            return results

        if len(results) < settings.RERANK_MIN_RESULTS:
            for result in results:
                result["rerank_status"] = STATUS_SKIPPED
            return results

        cap = min(len(results), settings.RERANK_MAX_CANDIDATES)
        dropped = len(results) - cap
        if dropped:
            logger.info(
                "reranker.candidates_dropped",
                extra={"dropped": dropped, "cap": cap},
            )
        candidates = results[:cap]

        payload = {
            "query": query,
            "passages": [
                {
                    "id": str(result.get("id") or index),
                    "text": _passage_text(result),
                }
                for index, result in enumerate(candidates)
            ],
        }

        try:
            response = self.breaker.call(
                self._get_client().post_json,
                "/rerank",
                payload,
                request_id=request_id or str(uuid.uuid4()),
            )
        except BreakerOpen as exc:
            logger.warning(
                "reranker.short_circuited",
                extra={
                    "retry_after": round(exc.retry_after, 1),
                    "degraded_reason": degraded_label(REASON_BREAKER_OPEN),
                },
            )
            return _degrade(results, reason=REASON_BREAKER_OPEN)
        except InternalServiceTimeout:
            logger.warning(
                "reranker.timeout",
                extra={
                    "budget_s": settings.RERANKER_TIMEOUT,
                    "degraded_reason": degraded_label(REASON_TIMEOUT),
                },
            )
            return _degrade(results, reason=REASON_TIMEOUT)
        except InternalServiceError as exc:
            logger.warning(
                "reranker.unavailable",
                extra={
                    "error": str(exc),
                    "degraded_reason": degraded_label(REASON_UNAVAILABLE),
                },
            )
            return _degrade(results, reason=REASON_UNAVAILABLE)
        except Exception as exc:  # noqa: BLE001
            logger.exception(
                "reranker.unexpected_error",
                extra={
                    "error_type": type(exc).__name__,
                    "degraded_reason": degraded_label(REASON_UNEXPECTED),
                },
            )
            return _degrade(results, reason=REASON_UNEXPECTED)

        scores = _extract_scores(response.payload)
        if scores is None:
            return _degrade(results, reason=REASON_MALFORMED)
        if not scores:
            return _degrade(results, reason=REASON_EMPTY)

        for index, result in enumerate(candidates):
            key = str(result.get("id") or index)
            result["rerank_score"] = scores.get(key)
            result["rerank_status"] = (
                STATUS_OK if key in scores else STATUS_DEGRADED
            )

        candidates.sort(
            key=lambda item: (
                item.get("rerank_score") is not None,
                item.get("rerank_score", float("-inf")),
            ),
            reverse=True,
        )
        final = candidates[: settings.RERANK_FINAL_RESULTS]

        _count_success()

        logger.info(
            "reranker.complete",
            extra={
                "scored": len(scores),
                "returned": len(final),
                "elapsed_ms": round(response.elapsed_ms, 1),
                "breaker": self.breaker.state.value,
            },
        )
        return final

    def health(self) -> dict[str, Any]:
        return {
            "enabled": settings.RERANKER_ENABLED,
            "url": settings.RERANKER_URL,
            **self.breaker.snapshot().as_dict(),
            "degradation": degradation_metrics(),
        }


def _extract_scores(payload: Any) -> Optional[dict[str, float]]:
    if not isinstance(payload, dict):
        return None

    raw = payload.get("scores")
    if not isinstance(raw, list):
        return None

    scores: dict[str, float] = {}
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("id")
        value = entry.get("score")
        if identifier is None or value is None:
            continue
        try:
            scores[str(identifier)] = float(value)
        except (TypeError, ValueError):
            continue

    if raw and not scores:
        return None

    return scores


def _passage_text(result: dict[str, Any]) -> str:
    from app.services.context_assembly_service import neutralise_document_label

    metadata = result.get("metadata") or {}
    filename = neutralise_document_label(
        metadata.get("original_filename") or "Unknown Document"
    )
    return f"Document: {filename}\n\nContent:\n{result.get('text', '')}"


def _degrade(results: list[dict[str, Any]], *, reason: str) -> list[dict[str, Any]]:
    for result in results:
        result["rerank_status"] = STATUS_DEGRADED
        result["rerank_degraded_reason"] = reason

    _count_degradation(reason)

    logger.warning(
        "reranker.degraded",
        extra={
            "degraded_reason": degraded_label(reason),
            "candidates": len(results),
            "returned": min(len(results), settings.RERANK_FINAL_RESULTS),
        },
    )
    return results[: settings.RERANK_FINAL_RESULTS]


reranker_client = RerankerClient()


__all__ = [
    "BREAKER_NAME",
    "DEGRADED_REASON_LABELS",
    "DEGRADED_REASON_LABEL_VALUES",
    "DEGRADE_REASONS",
    "REASON_BREAKER_OPEN",
    "REASON_DISABLED",
    "REASON_EMPTY",
    "REASON_MALFORMED",
    "REASON_TIMEOUT",
    "REASON_UNAVAILABLE",
    "REASON_UNEXPECTED",
    "RerankerClient",
    "STATUS_DEGRADED",
    "STATUS_DISABLED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "degradation_metrics",
    "degraded_label",
    "reranker_client",
    "reset_degradation_metrics",
]