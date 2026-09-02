"""ARCH-11 Step 6, ARCH-21 §3.3 — hybrid retrieval fused in PostgreSQL, one round trip."""

from __future__ import annotations

import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import Float, func, literal, literal_column, select, union_all
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.chunk_scope import ensure_iterative_scan, scoped_chunk_query
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger("app.services.hybrid_search")

RRF_K = 60
ARM_DENSE = "dense"
ARM_FULL_TEXT = "full_text"
ARM_FUZZY = "fuzzy"
TS_RANK_NORMALIZATION = 2 | 32

_IDENTIFIER = re.compile(r"\b(?=\S*\d)\S*[-/_.]\S*\b|\b[A-Za-z]*\d[A-Za-z0-9]{3,}\b")
_TSQUERY_SAFE = re.compile(r"[^\w\s\-']", re.UNICODE)


def looks_like_identifier(query: str) -> bool:
    return bool(_IDENTIFIER.search(query or ""))


@dataclass(frozen=True)
class ArmStat:
    name: str
    candidates: int
    weight: float


@dataclass
class HybridSearchOutcome:
    results: list[dict[str, Any]]
    arms: list[ArmStat]
    latency_ms: float
    candidates_requested: int
    fused_candidates: int

    def as_details(self) -> dict[str, Any]:
        return {
            "latency_ms": round(self.latency_ms, 2),
            "returned": len(self.results),
            "fused_candidates": self.fused_candidates,
            "candidates_requested": self.candidates_requested,
            "arms": {
                stat.name: {"candidates": stat.candidates, "weight": stat.weight}
                for stat in self.arms
            },
        }


class HybridSearchService:
    def _scoped(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        organization_id: Optional[uuid.UUID],
        work_item_ids: Optional[Sequence[str]],
        entity: Any,
    ):
        return scoped_chunk_query(
            db,
            workspace_id,
            organization_id=organization_id,
            work_item_ids=list(work_item_ids) if work_item_ids is not None else None,
            entity=entity,
        )

    def _ranked_arm(self, statement, order_expr, *, arm: str, limit: int, weight: float):
        limited = statement.order_by(order_expr).limit(limit).subquery()
        rank = func.row_number().over(order_by=literal_column("ord")).label("rank")
        return select(
            limited.c.id.label("chunk_id"),
            literal(arm).label("arm"),
            rank,
            literal(weight).label("weight"),
        ).select_from(limited)

    def _dense_arm(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        organization_id: Optional[uuid.UUID],
        work_item_ids: Optional[Sequence[str]],
        embedding: Sequence[float],
        limit: int,
    ):
        distance = DocumentChunk.embedding.cosine_distance(list(embedding))
        statement = self._scoped(
            db,
            workspace_id=workspace_id,
            organization_id=organization_id,
            work_item_ids=work_item_ids,
            entity=(DocumentChunk.id, distance.label("ord")),
        )
        return self._ranked_arm(
            statement,
            distance,
            arm=ARM_DENSE,
            limit=limit,
            weight=float(settings.HYBRID_WEIGHT_DENSE),
        )

    def _full_text_arm(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        organization_id: Optional[uuid.UUID],
        work_item_ids: Optional[Sequence[str]],
        tsquery,
        limit: int,
    ):
        rank = func.ts_rank_cd(
            DocumentChunk.content_tsv, tsquery, TS_RANK_NORMALIZATION
        ).cast(Float)
        statement = self._scoped(
            db,
            workspace_id=workspace_id,
            organization_id=organization_id,
            work_item_ids=work_item_ids,
            entity=(DocumentChunk.id, rank.label("ord")),
        ).where(DocumentChunk.content_tsv.op("@@")(tsquery))
        return self._ranked_arm(
            statement,
            rank.desc(),
            arm=ARM_FULL_TEXT,
            limit=limit,
            weight=float(settings.HYBRID_WEIGHT_FULL_TEXT),
        )

    def _fuzzy_arm(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        organization_id: Optional[uuid.UUID],
        work_item_ids: Optional[Sequence[str]],
        query: str,
        limit: int,
    ):
        similarity = func.word_similarity(
            literal(query), DocumentChunk.content
        ).cast(Float)
        statement = self._scoped(
            db,
            workspace_id=workspace_id,
            organization_id=organization_id,
            work_item_ids=work_item_ids,
            entity=(DocumentChunk.id, similarity.label("ord")),
        ).where(similarity >= float(settings.LEXICAL_TRIGRAM_THRESHOLD))
        return self._ranked_arm(
            statement,
            similarity.desc(),
            arm=ARM_FUZZY,
            limit=limit,
            weight=float(settings.HYBRID_WEIGHT_FUZZY),
        )

    def search(
        self,
        db: Session,
        *,
        workspace_id: uuid.UUID,
        query: str,
        work_item_ids: Optional[Sequence[str]] = None,
        top_k: int = 10,
        similarity_threshold: Optional[float] = None,
        organization_id: Optional[uuid.UUID] = None,
        candidates: Optional[int] = None,
        ef_search: Optional[int] = None,
    ) -> HybridSearchOutcome:
        from app.services.chunk_retrieval_service import _as_result, filenames_for
        from app.services.embedding_service import embedding_service
        from app.services.query_service import query_service

        started = time.perf_counter()

        cleaned = (query_service.preprocess(query) or "").strip()
        if not cleaned or top_k <= 0:
            return HybridSearchOutcome(
                results=[], arms=[], latency_ms=0.0,
                candidates_requested=0, fused_candidates=0,
            )

        depth = int(candidates or settings.HYBRID_CANDIDATES)
        ensure_iterative_scan(db, ef_search=ef_search)

        embedding = embedding_service.generate_embeddings([cleaned])[0]

        arms = [
            self._dense_arm(
                db,
                workspace_id=workspace_id,
                organization_id=organization_id,
                work_item_ids=work_item_ids,
                embedding=embedding,
                limit=depth,
            )
        ]
        arm_names = [ARM_DENSE]

        sanitised = _TSQUERY_SAFE.sub(" ", cleaned).strip()
        if sanitised:
            tsquery = func.plainto_tsquery(
                literal(settings.LEXICAL_TSVECTOR_CONFIG), literal(sanitised)
            )
            arms.append(
                self._full_text_arm(
                    db,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                    work_item_ids=work_item_ids,
                    tsquery=tsquery,
                    limit=depth,
                )
            )
            arm_names.append(ARM_FULL_TEXT)

        if looks_like_identifier(cleaned) and len(cleaned) >= 4:
            arms.append(
                self._fuzzy_arm(
                    db,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                    work_item_ids=work_item_ids,
                    query=cleaned,
                    limit=depth,
                )
            )
            arm_names.append(ARM_FUZZY)

        combined = union_all(*arms).subquery("arms")

        fused = (
            select(
                combined.c.chunk_id,
                func.sum(combined.c.weight / (RRF_K + combined.c.rank)).label(
                    "rrf_score"
                ),
                func.count().label("arm_hits"),
                func.min(combined.c.rank).label("best_rank"),
                func.string_agg(combined.c.arm, literal(",")).label("matched_arms"),
            )
            .group_by(combined.c.chunk_id)
            .subquery("fused")
        )

        final = (
            self._scoped(
                db,
                workspace_id=workspace_id,
                organization_id=organization_id,
                work_item_ids=None,
                entity=(
                    DocumentChunk,
                    fused.c.rrf_score,
                    fused.c.arm_hits,
                    fused.c.best_rank,
                    fused.c.matched_arms,
                ),
            )
            .join(fused, fused.c.chunk_id == DocumentChunk.id)
            .order_by(fused.c.rrf_score.desc(), DocumentChunk.chunk_index.asc())
            .limit(top_k)
        )

        rows = db.execute(final).all()

        names = filenames_for(
            db,
            workspace_id=workspace_id,
            work_item_ids=[row[0].work_item_id for row in rows],
        )

        results: list[dict[str, Any]] = []
        for chunk, rrf_score, arm_hits, best_rank, matched in rows:
            payload = _as_result(chunk, 0.0, names.get(chunk.work_item_id, ""))
            payload.pop("distance", None)
            payload["rrf_score"] = float(rrf_score)
            payload["arm_hits"] = int(arm_hits)
            payload["matched_arms"] = sorted(set((matched or "").split(",")))
            payload["similarity_score"] = min(1.0, float(rrf_score) * RRF_K)
            payload["score_basis"] = "rrf"
            results.append(payload)

        latency_ms = (time.perf_counter() - started) * 1000.0
        outcome = HybridSearchOutcome(
            results=results,
            arms=[
                ArmStat(name=name, candidates=depth, weight=weight)
                for name, weight in zip(
                    arm_names,
                    (
                        settings.HYBRID_WEIGHT_DENSE,
                        settings.HYBRID_WEIGHT_FULL_TEXT,
                        settings.HYBRID_WEIGHT_FUZZY,
                    ),
                )
            ],
            latency_ms=latency_ms,
            candidates_requested=depth,
            fused_candidates=len(rows),
        )

        logger.info("hybrid.search", extra={
            "workspace_id": str(workspace_id),
            **outcome.as_details(),
        })
        return outcome


hybrid_search_service = HybridSearchService()


__all__ = [
    "ARM_DENSE",
    "ARM_FULL_TEXT",
    "ARM_FUZZY",
    "ArmStat",
    "HybridSearchOutcome",
    "HybridSearchService",
    "RRF_K",
    "hybrid_search_service",
    "looks_like_identifier",
]
