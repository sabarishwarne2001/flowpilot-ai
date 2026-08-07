"""
Semantic embedding generation and vector storage service for FlowPilot AI.
Partitioned strictly into collections per workspace.
"""

from __future__ import annotations

import logging
import uuid
import re
from pathlib import Path
from typing import Any, List
from collections import OrderedDict

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from app.core.config import settings
from app.services.document_models import DocumentChunk
from app.services.query_service import query_service

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
        if self._initialized:
            return

        try:
            logger.info(
                "Loading embedding model '%s'.",
                settings.EMBEDDING_MODEL_NAME,
            )

            self.model = SentenceTransformer(
                settings.EMBEDDING_MODEL_NAME
            )

            chroma_path = Path(
                settings.CHROMA_PERSIST_DIRECTORY
            )
            chroma_path.mkdir(
                parents=True,
                exist_ok=True,
            )

            logger.info(
                "Opening ChromaDB database at '%s'.",
                chroma_path,
            )

            self.client = chromadb.PersistentClient(
                path=str(chroma_path),
                settings=ChromaSettings(
                    anonymized_telemetry=settings.CHROMA_TELEMETRY_ENABLED,
                ),
            )

            # Bounded LRU Cache for workspace collections
            self.collections: OrderedDict[str, Any] = OrderedDict()

            self._initialized = True
            logger.info(
                "Embedding service initialized successfully."
            )

        except Exception:
            logger.exception(
                "Embedding service initialization failed."
            )
            raise

    def _get_collection(self, name: str) -> Any:
        """
        Private. The public surface takes workspace_id.
        """
        if name in self.collections:
            self.collections.move_to_end(name)
            return self.collections[name]

        collection = self.client.get_or_create_collection(
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
            embeddings = self.model.encode(
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

            logger.info(
                "Distance=%f | Similarity=%f | Threshold=%s",
                distance,
                similarity_score,
                similarity_threshold,
            )

            if (
                similarity_threshold is not None
                and similarity_score < similarity_threshold
            ):
                continue

            metadata = metadatas[index] or {}
            logger.info(
                "Retrieved metadata: %s",
                metadata,
            )

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

            logger.debug(
                "Retrieved '%s' | Chunk=%s | Similarity=%.3f",
                metadata.get("original_filename"),
                metadata.get("chunk_index"),
                similarity_score,
            )

        logger.info(
            "Semantic search returned %d result(s) inside workspace %s.",
            len(formatted_results),
            workspace_id,
                )

        return formatted_results
            
    def delete_vectors_by_work_item_id(
        self,
        *,
        workspace_id: uuid.UUID,
        work_item_id: uuid.UUID,
    ) -> None:
        logger.info(
            "Deleting vectors for WorkItem %s inside workspace %s.",
            work_item_id,
            workspace_id,
        )

        try:
            collection = self.get_workspace_collection(workspace_id)
            collection.delete(
                where={
                    "work_item_id": str(work_item_id)
                }
            )
            logger.info(
                "Successfully deleted vectors for WorkItem %s inside workspace %s.",
                work_item_id,
                workspace_id,
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
        logger.info(
            "Clearing workspace vector collection: %s",
            workspace_id,
        )

        collection = self.get_workspace_collection(workspace_id)
        existing = collection.get()
        ids = existing.get(
            "ids",
            [],
        )

        if not ids:
            logger.info(
                "Workspace vector collection already empty."
            )
            return 0

        collection.delete(
            ids=ids,
        )
        logger.info(
            "Deleted %d vector(s) inside workspace %s.",
            len(ids),
            workspace_id,
        )
        return len(ids)

    def delete_workspace_collection(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> None:
        name = workspace_collection_name(workspace_id)
        self.collections.pop(name, None)
        try:
            self.client.delete_collection(name)
        except Exception:
            logger.exception("Failed to delete collection %s.", name)
            raise

    def get_searchable_work_item_ids(
        self,
        *,
        workspace_id: uuid.UUID,
    ) -> list[str]:
        collection = self.get_workspace_collection(workspace_id)
        results = collection.get(
            include=["metadatas"],
        )

        if not results["metadatas"]:
            return []

        ids = set()
        for metadata in results["metadatas"]:
            work_item_id = metadata.get(
                "work_item_id",
            )
            if work_item_id:
                ids.add(work_item_id)

        return sorted(ids)

    def health_check(self) -> bool:
        try:
            self.client.heartbeat()
            return True
        except Exception:
            logger.exception(
                "Embedding service health check failed."
            )
            return False


embedding_service = EmbeddingService()