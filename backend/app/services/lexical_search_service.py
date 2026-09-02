"""ARCH-11 Step 5 — lexical retrieval inside PostgreSQL.

Replaces process-local BM25 with shared, database-backed full-text search (ts_rank_cd
over generated content_tsv) and fuzzy matching (pg_trgm word_similarity over content).
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Optional, Sequence

from sqlalchemy import Float, func, literal
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.chunk_scope import scoped_chunk_query
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger("app.services.lexical_search")

#: `ts_rank_cd` normalisation bitmask: 2 (divide by length) | 32 (bounds to [0, 1)).
TS_RANK_NORMALIZATION = 2 | 32

_TSQUERY_SAFE = re.compile(r"[^\w\s\-']", re.UNICODE)
MIN_TRIGRAM_QUERY_CHARS = 4


class LexicalSearchService:
    """Full-text and fuzzy retrieval over `document_chunks`."""

    def _tsquery(self, query: str) -> Optional[Any]:
        cleaned = _TSQUERY_SAFE.sub(" ", query or "").strip()
        if not cleaned:
            return None
        return func.websearch_to_tsquery(
            literal(settings.LEXICAL_TSVECTOR_CONFIG), literal(cleaned)
        )

    def full_text_search(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
        organization_id: Optional[uuid.UUID] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        tsquery = self._tsquery(query)
        if tsquery is None:
            return []

        rank = func.ts_rank_cd(
            DocumentChunk.content_tsv, tsquery, TS_RANK_NORMALIZATION
        ).cast(Float)

        statement = scoped_chunk_query(
            db,
            workspace_id,
            organization_id=organization_id,
            work_item_ids=list(work_item_ids) if work_item_ids is not None else None,
            entity=(DocumentChunk, rank.label("rank")),
        ).where(DocumentChunk.content_tsv.op("@@")(tsquery))

        rows = db.execute(
            statement.order_by(rank.desc()).limit(top_k)
        ).all()
        return [(row[0], float(row[1])) for row in rows]

    def trigram_search(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
        organization_id: Optional[uuid.UUID] = None,
        threshold: Optional[float] = None,
    ) -> list[tuple[DocumentChunk, float]]:
        term = (query or "").strip()
        if len(term) < MIN_TRIGRAM_QUERY_CHARS:
            return []

        floor = (
            float(threshold)
            if threshold is not None
            else float(settings.LEXICAL_TRIGRAM_THRESHOLD)
        )
        similarity = func.word_similarity(literal(term), DocumentChunk.content).cast(
            Float
        )

        statement = scoped_chunk_query(
            db,
            workspace_id,
            organization_id=organization_id,
            work_item_ids=list(work_item_ids) if work_item_ids is not None else None,
            entity=(DocumentChunk, similarity.label("similarity")),
        ).where(similarity >= floor)

        rows = db.execute(
            statement.order_by(similarity.desc()).limit(top_k)
        ).all()
        return [(row[0], float(row[1])) for row in rows]

    def search(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
        organization_id: Optional[uuid.UUID] = None,
    ) -> list[dict[str, Any]]:
        """Full text first, trigram to fill. Returns retrieval-shaped dicts."""
        from app.services.chunk_retrieval_service import chunk_retrieval_service

        primary = self.full_text_search(
            db,
            workspace_id=workspace_id,
            query=query,
            work_item_ids=work_item_ids,
            top_k=top_k,
            organization_id=organization_id,
        )
        seen = {(chunk.work_item_id, chunk.chunk_index) for chunk, _ in primary}

        fill: list[tuple[DocumentChunk, float]] = []
        if len(primary) < top_k:
            for chunk, score in self.trigram_search(
                db,
                workspace_id=workspace_id,
                query=query,
                work_item_ids=work_item_ids,
                top_k=top_k - len(primary),
                organization_id=organization_id,
            ):
                key = (chunk.work_item_id, chunk.chunk_index)
                if key in seen:
                    continue
                seen.add(key)
                fill.append((chunk, score))

        results = chunk_retrieval_service.rows_to_results(
            db, workspace_id=workspace_id, rows=primary, score_key="lexical_score"
        )
        for payload, (_, score) in zip(
            chunk_retrieval_service.rows_to_results(
                db, workspace_id=workspace_id, rows=fill, score_key="lexical_score"
            ),
            fill,
        ):
            payload["lexical_match"] = "trigram"
            payload["lexical_score"] = float(score)
            results.append(payload)

        for payload in results[: len(primary)]:
            payload["lexical_match"] = "full_text"

        logger.info(
            "lexical.search",
            extra={
                "workspace_id": str(workspace_id),
                "full_text": len(primary),
                "trigram_fill": len(fill),
                "asked": top_k,
            },
        )
        return results

    def lexical_ranking_report(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
    ) -> dict[str, Any]:
        """Side-by-side comparison for R34 diagnostics."""
        full_text = self.full_text_search(
            db,
            workspace_id=workspace_id,
            query=query,
            work_item_ids=work_item_ids,
            top_k=top_k,
        )
        trigram = self.trigram_search(
            db,
            workspace_id=workspace_id,
            query=query,
            work_item_ids=work_item_ids,
            top_k=top_k,
        )
        return {
            "query": query,
            "full_text": [
                {
                    "work_item_id": str(chunk.work_item_id),
                    "chunk_index": chunk.chunk_index,
                    "rank": round(score, 6),
                }
                for chunk, score in full_text
            ],
            "trigram": [
                {
                    "work_item_id": str(chunk.work_item_id),
                    "chunk_index": chunk.chunk_index,
                    "similarity": round(score, 6),
                }
                for chunk, score in trigram
            ],
        }


lexical_search_service = LexicalSearchService()


__all__ = [
    "LexicalSearchService",
    "MIN_TRIGRAM_QUERY_CHARS",
    "TS_RANK_NORMALIZATION",
    "lexical_search_service",
]
