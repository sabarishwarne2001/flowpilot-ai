"""
Semantic embedding generation and vector storage service for FlowPilot AI.

Provides SentenceTransformer embeddings together with persistent ChromaDB
storage, semantic similarity search, and vector lifecycle management.

This service is an infrastructure component. It owns:

- Embedding generation
- Vector persistence
- Semantic search
- Similarity score normalization
- ChromaDB interaction

Business logic belongs in higher service layers.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any
from app.services.document_models import DocumentChunk

import chromadb
from chromadb.config import Settings as ChromaSettings
from sentence_transformers import SentenceTransformer

from app.core.config import settings

logger = logging.getLogger("app.services.embedding_service")

Embedding = list[float]
EmbeddingList = list[Embedding]


class EmbeddingService:
    """
    Singleton service responsible for embedding generation and
    ChromaDB vector management.
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

            self.collection = self.client.get_or_create_collection(
                name=settings.CHROMA_COLLECTION_NAME,
                metadata={
                    "hnsw:space": "cosine",
                },
            )

            self._initialized = True

            logger.info(
                "Embedding service initialized successfully."
            )

        except Exception:
            logger.exception(
                "Embedding service initialization failed."
            )
            raise

    def generate_embeddings(
        self,
        texts: list[str],
    ) -> EmbeddingList:
        """
        Generate embeddings for one or more text strings.
        """

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
        work_item_id: uuid.UUID,
        original_filename: str,
        chunks: list[DocumentChunk],
        embeddings: EmbeddingList,
    ) -> None:
        """
        Persist document chunks together with their embeddings.

        Every chunk receives a deterministic identifier and metadata
        allowing future retrieval by WorkItem.
        """

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
                "work_item_id": str(work_item_id),
                "original_filename": original_filename,
                "chunk_index": chunk.chunk_index,
                "page_number": chunk.page_number,
            }
            for chunk in chunks
        ]

        logger.info(
            "Persisting %d vectors for WorkItem %s.",
            len(chunks),
            work_item_id,
        )

        try:

            logger.info(
                "Storing page-aware metadata for %d chunk(s).",
                len(chunks),
            )

            logger.debug(
                "Page numbers: %s",
                [
                    chunk.page_number
                    for chunk in chunks
                ],
            )

            self.collection.add(
                ids=ids,
                documents=[
                    chunk.text
                    for chunk in chunks
                ],
                embeddings=embeddings,
                metadatas=metadatas,
            )

            logger.info(
                "Successfully stored %d vectors.",
                len(chunks),
            )

        except Exception:
            logger.exception(
                "Failed to store vectors."
            )
            raise

    def delete_chunks(
        self,
        work_item_id: uuid.UUID,
    ) -> None:
        """
        Delete every vector belonging to a WorkItem.
        """

        logger.info(
            "Deleting vectors for WorkItem %s.",
            work_item_id,
        )

        try:
            self.collection.delete(
                where={
                    "work_item_id": str(work_item_id),
                }
            )

            logger.info(
                "Vectors deleted successfully."
            )

        except Exception:
            logger.exception(
                "Failed to delete vectors."
            )
            raise

    def _normalize_similarity_score(
        self,
        distance: float,
    ) -> float:
        """
        Convert the Chroma distance value into a normalized
        similarity score.

        Current implementation assumes cosine distance.

        Returns:
            float between 0.0 and 1.0
        """

        similarity = 1.0 - distance

        similarity = max(
            0.0,
            min(
                1.0,
                similarity,
            ),
        )

        return similarity

    def _build_search_filter(
        self,
        *,
        filter_work_item_id: uuid.UUID | None = None,
        filter_work_item_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Any] | None:
        """
        Construct the ChromaDB metadata filter.

        Supports both:

        - Document Assistant
        - Global Assistant
        """

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
        query: str,
        top_k: int = 5,
        filter_work_item_id: uuid.UUID | None = None,
        filter_work_item_ids: list[uuid.UUID] | None = None,
        similarity_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """
        Execute semantic similarity search.

        Supports both:

        - Document Assistant (single WorkItem)
        - Global Assistant (multiple WorkItems)

        Returns a normalized result structure independent of
        ChromaDB's native response format.
        """

        query = query.strip()

        if not query:
            return []

        if top_k <= 0:
            raise ValueError(
                "top_k must be greater than zero."
            )

        logger.info(
            "Executing semantic search (top_k=%d).",
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

            logger.info(
                "Chroma collection contains %d vectors.",
                self.collection.count(),
            )

            logger.info(
                "Search filter: %s",
                where,
            )

            results = self.collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
                where=where,
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
                    # ----------------------------------------------------------
                    # Retrieval
                    # ----------------------------------------------------------
                    "id": ids[index],
                    "text": documents[index],

                    # ----------------------------------------------------------
                    # Citation Information
                    # ----------------------------------------------------------
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

                    #
                    # Reserved for Production Hardening.
                    #
                    "page_number": metadata.get(
                        "page_number",
                    ),

                    # ----------------------------------------------------------
                    # Metadata
                    # ----------------------------------------------------------
                    "metadata": metadata,

                    # ----------------------------------------------------------
                    # Retrieval Metrics
                    # ----------------------------------------------------------
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
            "Semantic search returned %d result(s).",
            len(formatted_results),
        )

        return formatted_results
    
    def delete_vectors_by_work_item_id(
        self,
        work_item_id: uuid.UUID,
    ) -> None:
        """
        Delete every vector associated with a WorkItem.

        This operation is typically invoked when a document is
        removed from the platform to keep the vector store
        synchronized with PostgreSQL.
        """

        logger.info(
            "Deleting vectors for WorkItem %s.",
            work_item_id,
        )

        try:

            self.collection.delete(
                where={
                    "work_item_id": str(work_item_id)
                }
            )

            logger.info(
                "Successfully deleted vectors for WorkItem %s.",
                work_item_id,
            )

        except Exception:
            logger.exception(
                "Failed to delete vectors for WorkItem %s.",
                work_item_id,
            )
            raise

    def health_check(self) -> bool:
        """
        Verify that the embedding infrastructure is operational.

        Returns:
            True if the embedding model and ChromaDB collection
            are available.
        """

        try:

            _ = self.collection.count()

            return True

        except Exception:

            logger.exception(
                "Embedding service health check failed."
            )

            return False


embedding_service = EmbeddingService()