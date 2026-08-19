"""
Embedding Service for FlowPilot AI.
Produces sentence embeddings using SentenceTransformers.

ARCH-07 & ARCH-11: Import-time decoupling. Defers loading SentenceTransformer
until methods requiring it are called.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from app.core.config import settings
from app.core.embeddings import active_model_name

logger = logging.getLogger("app.services.embedding")

Embedding = list[float]
EmbeddingList = list[Embedding]


class EmbeddingService:
    """
    Core embedding generation service with thread-safe model caching.
    """

    _instance: "EmbeddingService | None" = None

    def __new__(cls) -> "EmbeddingService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if getattr(self, "_initialized", False):
            return

        self._model: Optional[Any] = None
        self._lock = threading.Lock()
        self._initialized = True

    def _get_model(self) -> Any:
        """Lazily load SentenceTransformer model on first vector generation."""
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is not None:
                return self._model
            try:
                from sentence_transformers import SentenceTransformer

                model_name = active_model_name()
                logger.info("embedding.loading_model", extra={"model": model_name})
                self._model = SentenceTransformer(model_name)
                return self._model
            except Exception as exc:
                logger.exception("embedding.model_load_failed")
                raise RuntimeError(f"Failed to load embedding model: {exc}") from exc

    @property
    def model(self) -> Any:
        return self._get_model()

    def generate_embeddings(self, texts: list[str]) -> EmbeddingList:
        if not texts:
            return []

        logger.info("embedding.generate", extra={"count": len(texts)})
        model = self._get_model()
        try:
            embeddings = model.encode(
                texts,
                batch_size=settings.EMBEDDING_BATCH_SIZE,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [embedding.tolist() for embedding in embeddings]
        except Exception as exc:
            logger.exception("embedding.generation_failed")
            raise RuntimeError(f"Failed to generate embeddings: {exc}") from exc


embedding_service = EmbeddingService()

__all__ = ["Embedding", "EmbeddingList", "EmbeddingService", "embedding_service"]