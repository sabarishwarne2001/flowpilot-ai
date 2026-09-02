"""ARCH-11 Step 7 — the reranker microservice FastAPI application.

    uvicorn app.reranker.main:app --host 0.0.0.0 --port 8081
"""

from __future__ import annotations

import hmac
import logging
import os
import threading
import time
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger("app.reranker")

MODEL_NAME = os.getenv("RERANKER_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
INTERNAL_TOKEN = os.getenv("RERANKER_INTERNAL_TOKEN", "")
MAX_PAIRS = int(os.getenv("RERANKER_MAX_PAIRS", "100"))
MAX_TEXT_CHARS = int(os.getenv("RERANKER_MAX_TEXT_CHARS", "4000"))
MAX_QUERY_CHARS = int(os.getenv("RERANKER_MAX_QUERY_CHARS", "1000"))
PRELOAD = os.getenv("RERANKER_PRELOAD", "1") == "1"


class _ModelHolder:
    """Loads once. `ready` is what the readiness probe reports."""

    def __init__(self) -> None:
        self._model: Optional[Any] = None
        self._lock = threading.Lock()
        self._error: Optional[str] = None
        self._loaded_at: Optional[float] = None

    @property
    def ready(self) -> bool:
        return self._model is not None

    @property
    def error(self) -> Optional[str]:
        return self._error

    def get(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            started = time.perf_counter()
            try:
                from sentence_transformers import CrossEncoder

                logger.info("reranker.loading", extra={"model": MODEL_NAME})
                model = CrossEncoder(MODEL_NAME, max_length=512)
            except Exception as exc:  # noqa: BLE001
                self._error = f"{type(exc).__name__}: {exc}"
                logger.exception("reranker.load_failed")
                raise
            self._model = model
            self._error = None
            self._loaded_at = time.perf_counter()
            logger.info(
                "reranker.loaded",
                extra={
                    "model": MODEL_NAME,
                    "seconds": round(time.perf_counter() - started, 2),
                },
            )
            return model


holder = _ModelHolder()


def require_token(
    x_internal_token: str = Header(default="", alias="X-Internal-Token")
) -> None:
    if not INTERNAL_TOKEN:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="RERANKER_INTERNAL_TOKEN is not configured",
        )
    if not hmac.compare_digest(x_internal_token, INTERNAL_TOKEN):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal token"
        )


class RerankPair(BaseModel):
    id: str = Field(max_length=200)
    text: str

    @field_validator("text")
    @classmethod
    def _cap(cls, value: str) -> str:
        return value[:MAX_TEXT_CHARS]


class RerankRequest(BaseModel):
    query: str = Field(min_length=1)
    passages: list[RerankPair] = Field(min_length=1)

    @field_validator("query")
    @classmethod
    def _cap_query(cls, value: str) -> str:
        return value[:MAX_QUERY_CHARS]

    @field_validator("passages")
    @classmethod
    def _cap_passages(cls, value: list[RerankPair]) -> list[RerankPair]:
        if len(value) > MAX_PAIRS:
            return value[:MAX_PAIRS]
        return value


class RerankScore(BaseModel):
    id: str
    score: float


class RerankResponse(BaseModel):
    model: str
    scores: list[RerankScore]
    elapsed_ms: float
    truncated: bool = False


app = FastAPI(title="FlowPilot Reranker", version="arch11.7", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _startup() -> None:
    if not INTERNAL_TOKEN:
        logger.warning("RERANKER_INTERNAL_TOKEN not set at startup.")
    if PRELOAD:
        try:
            holder.get()
        except Exception:  # noqa: BLE001
            pass


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe."""
    return {"status": "ok", "model": MODEL_NAME}


@app.get("/ready")
def ready() -> dict[str, Any]:
    """Readiness probe."""
    if not holder.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "loading", "error": holder.error},
        )
    return {"status": "ready", "model": MODEL_NAME}


@app.post("/rerank", response_model=RerankResponse, dependencies=[Depends(require_token)])
def rerank(request: RerankRequest) -> RerankResponse:
    started = time.perf_counter()
    try:
        model = holder.get()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"model unavailable: {exc}",
        ) from exc

    pairs = [(request.query, passage.text) for passage in request.passages]
    try:
        raw = model.predict(pairs)
    except Exception as exc:  # noqa: BLE001
        logger.exception("reranker.predict_failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"scoring failed: {type(exc).__name__}",
        ) from exc

    elapsed_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        "reranker.scored",
        extra={"pairs": len(pairs), "elapsed_ms": round(elapsed_ms, 1)},
    )
    return RerankResponse(
        model=MODEL_NAME,
        scores=[
            RerankScore(id=passage.id, score=float(score))
            for passage, score in zip(request.passages, raw)
        ],
        elapsed_ms=elapsed_ms,
        truncated=len(request.passages) >= MAX_PAIRS,
    )
