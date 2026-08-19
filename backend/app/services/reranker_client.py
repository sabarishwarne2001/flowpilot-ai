"""ARCH-11 Step 7 — the web tier's reranker client."""

from __future__ import annotations

import logging
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

        if not settings.RERANKER_ENABLED:
            for result in results:
                result["rerank_status"] = STATUS_DISABLED
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
                extra={"retry_after": round(exc.retry_after, 1)},
            )
            return _degrade(results, reason="breaker_open")
        except InternalServiceTimeout:
            logger.warning(
                "reranker.timeout", extra={"budget_s": settings.RERANKER_TIMEOUT}
            )
            return _degrade(results, reason="timeout")
        except InternalServiceError as exc:
            logger.warning("reranker.unavailable", extra={"error": str(exc)})
            return _degrade(results, reason="unavailable")

        scores = {
            entry["id"]: float(entry["score"])
            for entry in (response.payload or {}).get("scores", [])
        }
        if not scores:
            return _degrade(results, reason="empty_response")

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
        """For /health reporting."""
        return {
            "enabled": settings.RERANKER_ENABLED,
            "url": settings.RERANKER_URL,
            **self.breaker.snapshot().as_dict(),
        }


def _passage_text(result: dict[str, Any]) -> str:
    from app.services.context_assembly_service import neutralise_document_label

    metadata = result.get("metadata") or {}
    filename = neutralise_document_label(
        metadata.get("original_filename") or "Unknown Document"
    )
    return f"Document: {filename}\n\nContent:\n{result.get('text', '')}"


def _degrade(results: list[dict[str, Any]], *, reason: str) -> list[dict[str, Any]]:
    """Serve the RRF ordering upon failure."""
    for result in results:
        result["rerank_status"] = STATUS_DEGRADED
        result["rerank_degraded_reason"] = reason
    return results[: settings.RERANK_FINAL_RESULTS]


reranker_client = RerankerClient()


__all__ = [
    "BREAKER_NAME",
    "RerankerClient",
    "STATUS_DEGRADED",
    "STATUS_DISABLED",
    "STATUS_OK",
    "STATUS_SKIPPED",
    "reranker_client",
]