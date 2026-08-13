"""
Semantic embedding generation and vector storage service for FlowPilot AI.
Partitioned strictly into collections per workspace.

ARCH-07 Step 1 — import-time decoupling (§B.10).
Defers loading SentenceTransformer and ChromaDB until methods requiring them are called.
"""

from __future__ import annotations

import logging
import uuid
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, List
from collections import OrderedDict

from app.core.config import settings
from app.services.document_models import DocumentChunk
from app.services.query_service import query_service

if TYPE_CHECKING:
    import chromadb
    from sentence_transformers import SentenceTransformer

logger = logging.getLogger("app.services.embedding_service")

_COLLECTION_NAME_RE = re.compile(r"^ws_[0-9a-f-]{36}$")
_MAX_CACHED_COLLECTIONS = 64

Embedding = list[float]
EmbeddingList = list[Embedding]


def workspace_collection_name(workspace_id: uuid.UUID) -> str:
    """
    The only way a collection name is produced.
    """
    return f"ws_{workspace_id}"


class EmbeddingService:
    """
    Singleton service responsible for embedding generation and
    ChromaDB vector management, strictly partitioned by workspace.
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

        self._model: Any = None
        self._client: Any = None
        self._lock = threading.Lock()

        # Bounded LRU Cache for workspace collections
        self.collections: OrderedDict[str, Any] = OrderedDict()

        self._initialized = True

    def _get_model(self) -> Any:
        """Lazily load SentenceTransformer model on first vector generation."""
        if self._model is not None:
            return self._model

        with self._lock:
            if self._model is not None:
                return self._model

            from sentence_transformers import SentenceTransformer

            logger.info("Loading embedding model '%s'.", settings.EMBEDDING_MODEL_NAME)
            self._model = SentenceTransformer(settings.EMBEDDING_MODEL_NAME)
            return self._model

    def _get_client(self) -> Any:
        """Lazily initialize PersistentClient on first vector collection query."""
        if self._client is not None:
            return self._client

        with self._lock:
            if self._client is not None:
                return self._client

            import chromadb
            from chromadb.config import Settings as ChromaSettings

            chroma_path = Path(settings.CHROMA_PERSIST_DIRECTORY)
            chroma_path.mkdir(parents=True, exist_ok=True)

            logger.info("Opening ChromaDB database at '%s'.", chroma_path)
            self._client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=ChromaSettings(
                    anonymized_telemetry=settings.CHROMA_TELEMETRY_ENABLED,
                ),
            )
            return self._client

    @property
    def model(self) -> Any:
        return self._get_model()

    @property
    def client(self) -> Any:
        return self._get_client()

    def _get_collection(self, name: str) -> Any:
        """
        Private. The public surface takes workspace_id.
        """
        if name in self.collections:
            self.collections.move_to_end(name)
            return self.collections[name]

        client = self._get_client()
        collection = client.get_or_create_collection(
            name=name,
            metadata={
                "hnsw:space": "cosine",
            },
        )
        self.collections[name] = collection
        while len(self.collections) > _MAX_CACHED_COLLECTIONS:
            self.collections.popitem(last=False)
        return collection

    def get_workspace_collection(self, workspace_id: uuid.UUID) -> Any:
        return self._get_collection(workspace_collection_name(workspace_id))

    def get_evaluation_collection(self) -> Any:
        return self._get_collection(settings.CHROMA_EVALUATION_COLLECTION)

    def generate_embeddings(
        self,
        texts: list[str],
    ) -> EmbeddingList:
        if not texts:
            return []

        logger.info(
            "Generating embeddings for %d texts.",
            len(texts),
        )

        try:
            model = self._get_model()
            embeddings = model.encode(
                texts,
                batch_size=settings.EMBEDDING_BATCH_SIZE,
                convert_to_numpy=True,
                normalize_embeddings=True,
                show_progress_bar=False,
            )
            return [
                embedding.tolist()
                for embedding in embeddings
            ]
        except Exception:
            logger.exception(
                "Embedding generation failed."
            )
            raise

    def store_chunks(
        self,
        *,
        workspace_id: uuid.UUID,
        work_item_id: uuid.UUID,
        original_filename: str,
        chunks: list[DocumentChunk],
        embeddings: EmbeddingList,
    ) -> None:
        if not chunks:
            logger.warning(
                "No chunks supplied for storage."
            )
            return

        if len(chunks) != len(embeddings):
            raise ValueError(
                "Chunks and embeddings must have identical lengths."
            )

        ids = [
            f"{work_item_id}_chunk_{chunk.chunk_index}"
            for chunk in chunks
        ]

        metadatas = [
            {
                "workspace_id": str(workspace_id),
                "work_item_id": str(work_item_id),
                "original_filename": original_filename,
                "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number,
            }
            for chunk in chunks
        ]

        logger.info(
            "Persisting %d vectors for WorkItem %s inside workspace %s.",
            len(chunks),
            work_item_id,
            workspace_id,
        )

        try:
            collection = self.get_workspace_collection(workspace_id)
            collection.add(
                ids=ids,
                documents=[
                    chunk.text
                    for chunk in chunks
                ],
                embeddings=embeddings,
                metadatas=metadatas,
            )
            logger.info(
                "Successfully stored %d vectors in workspace %s.",
                len(chunks),
                workspace_id,
            )
        except Exception:
            logger.exception(
                "Failed to store vectors."
            )
            raise

    def _normalize_similarity_score(
        self,
        distance: float,
    ) -> float:
        similarity = 1.0 - distance
        return max(0.0, min(1.0, similarity))

    def _build_search_filter(
        self,
        *,
        filter_work_item_id: uuid.UUID | None = None,
        filter_work_item_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Any] | None:
        if filter_work_item_id is not None:
            return {
                "work_item_id": str(
                    filter_work_item_id
                )
            }

        if filter_work_item_ids:
            return {
                "work_item_id": {
                    "$in": [
                        str(item_id)
                        for item_id in filter_work_item_ids
                    ]
                }
            }
        return None

    def similarity_search(
        self,
        *,
        workspace_id: uuid.UUID,
        query: str,
        top_k: int = 5,
        filter_work_item_id: uuid.UUID | None = None,
        filter_work_item_ids: list[uuid.UUID] | None = None,
        similarity_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        query = query_service.preprocess(
            query,
        )

        if not query:
            return []
        
        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than zero."
            )

        logger.info(
            "Executing semantic search inside workspace %s (top_k=%d).",
            workspace_id,
            top_k,
        )

        query_embedding = self.generate_embeddings(
            [query]
        )[0]

        where = self._build_search_filter(
            filter_work_item_id=filter_work_item_id,
            filter_work_item_ids=filter_work_item_ids,
        )

        try:
            collection = self.get_workspace_collection(workspace_id)
            logger.info(
                "Workspace collection contains %d vectors.",
                collection.count(),
            )
            logger.info(
                "Search filter: %s",
                where,
            )

            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
                include=[
                    "documents",
                    "metadatas",
                    "distances",
                ],
            )

        except Exception:
            logger.exception(
                "Semantic similarity search failed."
            )
            raise

        formatted_results: list[dict[str, Any]] = []

        if not results["documents"]:
            logger.info(
                "Semantic search returned no results."
            )
            return formatted_results

        documents = results["documents"][0]
        metadatas = results["metadatas"][0]
        ids = results["ids"][0]
        distances = results["distances"][0]

        for index in range(len(documents)):
            distance = float(distances[index])
            similarity_score = (
                self._normalize_similarity_score(
                    distance
                )
            )

            if (
                similarity_threshold is not None
                and similarity_score < similarity_threshold
            ):
                continue

            metadata = metadatas[index] or {}

            formatted_results.append(
                {
                    "id": ids[index],
                    "text": documents[index],
                    "document_name": metadata.get(
                        "original_filename",
                        "Unknown Document",
                    ),
                    "work_item_id": metadata.get(
                        "work_item_id",
                    ),
                    "chunk_index": metadata.get(
                        "chunk_index",
                    ),
                    "page_number": metadata.get(
                        "page_number",
                    ),
                    "metadata": metadata,
                    "distance": distance,
                    "similarity_score": similarity_score,
                }
            )

        return formatted_results
            
    def delete_vectors_by_work_item_id(
        self,
        *,
        workspace_id: uuid.UUID,
        work_item_id: uuid.UUID,
    ) -> None:
        try:
            collection = self.get_workspace_collection(workspace_id)
            collection.delete(
                where={
                    "work_item_id": str(work_item_id)
                }
            )
        except Exception:
            logger.exception(
                "Failed to delete vectors for WorkItem %s inside workspace %s.",
                work_item_id,
                workspace_id,
            )
            raise

    def clear_workspace_collection(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> int:
        collection = self.get_workspace_collection(workspace_id)
        existing = collection.get()
        ids = existing.get("ids", [])

        if not ids:
            return 0

        collection.delete(ids=ids)
        return len(ids)

    def delete_workspace_collection(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> None:
        name = workspace_collection_name(workspace_id)
        self.collections.pop(name, None)
        try:
            client = self._get_client()
            client.delete_collection(name)
        except Exception:
            logger.exception("Failed to delete collection %s.", name)
            raise

    def get_searchable_work_item_ids(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> list[str]:
        collection = self.get_workspace_collection(workspace_id)
        results = collection.get(include=["metadatas"])

        if not results["metadatas"]:
            return []

        ids = set()
        for metadata in results["metadatas"]:
            work_item_id = metadata.get("work_item_id")
            if work_item_id:
                ids.add(work_item_id)

        return sorted(ids)

    def health_check(self) -> bool:
        try:
            client = self._get_client()
            client.heartbeat()
            return True
        except Exception:
            logger.exception("Embedding service health check failed.")
            return False


embedding_service = EmbeddingService()