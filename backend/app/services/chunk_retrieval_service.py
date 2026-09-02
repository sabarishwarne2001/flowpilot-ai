"""ARCH-11 Steps 4, 6 & 9 — dense retrieval out of PostgreSQL and router."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.chunk_scope import nearest_chunks
from app.models.document_chunk import DocumentChunk
from app.models.work_item import WorkItem

logger = logging.getLogger("app.services.chunk_retrieval")


def chunk_public_id(work_item_id: uuid.UUID | str, chunk_index: int) -> str:
    return f"{work_item_id}_chunk_{chunk_index}"


def _as_result(
    chunk: DocumentChunk, distance: float, filename: str
) -> dict[str, Any]:
    similarity = max(0.0, min(1.0, 1.0 - float(distance)))
    return {
        "id": chunk_public_id(chunk.work_item_id, chunk.chunk_index),
        "text": chunk.content,
        "document_name": filename or "Unknown Document",
        "work_item_id": str(chunk.work_item_id),
        "chunk_index": chunk.chunk_index,
        "page_number": chunk.page_number,
        "metadata": {
            "workspace_id": str(chunk.workspace_id),
            "work_item_id": str(chunk.work_item_id),
            "original_filename": filename,
            "chunk_index": chunk.chunk_index,
            "page_number": chunk.page_number,
            "page_start_char": chunk.page_start_char,
            "page_end_char": chunk.page_end_char,
            "bbox": chunk.bbox,
            "token_count": chunk.token_count,
            "embedding_model": chunk.embedding_model,
            "source": "pgvector",
        },
        "distance": float(distance),
        "similarity_score": similarity,
    }


def filenames_for(
    db: Session, *, workspace_id: uuid.UUID, work_item_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    if not work_item_ids:
        return {}
    rows = db.execute(
        select(WorkItem.id, WorkItem.original_filename).where(
            WorkItem.workspace_id == workspace_id,
            WorkItem.id.in_(list(work_item_ids)),
        )
    ).all()
    return {row[0]: row[1] for row in rows}


class ChunkRetrievalService:
    def semantic_search(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
        similarity_threshold: Optional[float] = None,
        organization_id: Optional[uuid.UUID] = None,
    ) -> list[dict[str, Any]]:
        from app.services.embedding_service import embedding_service
        from app.services.query_service import query_service

        cleaned = query_service.preprocess(query)
        if not cleaned:
            return []
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero.")

        embedding = embedding_service.generate_embeddings([cleaned])[0]

        max_distance = (
            1.0 - float(similarity_threshold)
            if similarity_threshold is not None
            else None
        )

        hits = nearest_chunks(
            db,
            workspace_id=workspace_id,
            organization_id=organization_id,
            embedding=embedding,
            top_k=top_k,
            work_item_ids=list(work_item_ids) if work_item_ids is not None else None,
            max_distance=max_distance,
        )

        names = filenames_for(
            db,
            workspace_id=workspace_id,
            work_item_ids=[chunk.work_item_id for chunk, _ in hits],
        )
        results = [
            _as_result(chunk, distance, names.get(chunk.work_item_id, ""))
            for chunk, distance in hits
        ]

        logger.info(
            "pgvector.semantic_search",
            extra={
                "workspace_id": str(workspace_id),
                "asked": top_k,
                "returned": len(results),
                "threshold": similarity_threshold,
            },
        )
        return results

    def rows_to_results(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        rows: Sequence[tuple[DocumentChunk, float]],
        score_key: str,
    ) -> list[dict[str, Any]]:
        names = filenames_for(
            db,
            workspace_id=workspace_id,
            work_item_ids=[chunk.work_item_id for chunk, _ in rows],
        )
        results = []
        for chunk, score in rows:
            payload = _as_result(chunk, 1.0, names.get(chunk.work_item_id, ""))
            payload.pop("distance", None)
            payload.pop("similarity_score", None)
            payload[score_key] = float(score)
            results.append(payload)
        return results


chunk_retrieval_service = ChunkRetrievalService()


@dataclass(frozen=True)
class RoutingPlan:
    indexed: tuple[str, ...]
    legacy: tuple[str, ...]

    @property
    def is_mixed(self) -> bool:
        return bool(self.indexed) and bool(self.legacy)

    def as_details(self) -> dict[str, Any]:
        return {
            "pgvector_documents": len(self.indexed),
            "chroma_documents": len(self.legacy),
            "mixed": self.is_mixed,
        }


class KnowledgeRouter:
    def plan(
        self,
        db: Optional[Session],
        *,
        workspace_id: uuid.UUID,
        work_item_ids: Sequence[str],
    ) -> RoutingPlan:
        requested = tuple(str(value) for value in work_item_ids)
        if not requested:
            return RoutingPlan(indexed=(), legacy=())

        dual_read = getattr(settings, "KNOWLEDGE_DUAL_READ", True)
        if db is None or not dual_read:
            return RoutingPlan(indexed=(), legacy=requested)

        from app.services.chunk_writer import indexed_work_item_ids

        try:
            present = {
                str(value)
                for value in indexed_work_item_ids(db, workspace_id=workspace_id)
            }
        except Exception:  # noqa: BLE001
            return RoutingPlan(indexed=(), legacy=requested)

        indexed = tuple(value for value in requested if value in present)
        legacy = tuple(value for value in requested if value not in present)
        return RoutingPlan(indexed=indexed, legacy=legacy)


knowledge_router = KnowledgeRouter()


__all__ = [
    "ChunkRetrievalService",
    "KnowledgeRouter",
    "RoutingPlan",
    "chunk_public_id",
    "chunk_retrieval_service",
    "knowledge_router",
]
