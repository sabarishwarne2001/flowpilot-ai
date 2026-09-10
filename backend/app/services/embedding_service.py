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
                self._model = self._load(SentenceTransformer, model_name)
                return self._model
            except Exception as exc:
                logger.exception("embedding.model_load_failed")
                raise RuntimeError(f"Failed to load embedding model: {exc}") from exc

    @staticmethod
    def _load(loader: Any, model_name: str) -> Any:
        """Load from the local cache first, network only as a fallback.

        ARCH-29 Tranche 3. `SentenceTransformer(name)` contacts the Hugging
        Face Hub before touching local weights — roughly fifteen HEAD requests
        (`modules.json`, `config.json`, `tokenizer.json`, each pooling config)
        to check whether the cached copy is stale. On a cold or throttled
        connection that is the observed ~57-second stall, and it happens even
        though the weights are already on disk and are what will ultimately be
        used.

        `local_files_only=True` skips those checks entirely. It RAISES when the
        model is not cached, which is why this is a two-phase load rather than
        a flag: the first run on a fresh machine, and any run of an image built
        before the ARCH-29 Tranche 1 pre-bake, still needs the network. Falling
        back keeps those working while making the steady state offline.

        The fallback logs at WARNING, not INFO. Reaching it in production means
        the pre-bake did not take — the image was built without it, or under a
        different model spelling than the runtime resolves (see
        `app/core/embeddings.py` on why those are two different cache
        directories) — and that is a deployment defect that will otherwise only
        ever present as latency.
        """
        try:
            return loader(model_name, local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "embedding.cache_miss_falling_back_to_network",
                extra={"model": model_name, "error": str(exc)},
            )
            return loader(model_name)

    def warmup(self) -> None:
        """Load the model now rather than on the first user request.

        Called from application startup. Without it the cost — even the reduced
        offline cost — lands on whichever customer happens to send the first
        chat message after a deploy, and `uvicorn --reload` in development means
        that is every time a file is touched.
        """
        self._get_model()

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