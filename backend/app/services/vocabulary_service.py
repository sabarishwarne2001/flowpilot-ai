"""ARCH-11.5 Step 3 — workspace-scoped query vocabulary."""

from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.chunk_scope import scoped_chunk_query
from app.models.document_chunk import DocumentChunk

logger = logging.getLogger("app.services.vocabulary")

MIN_TERM_LENGTH = 3
MAX_DOCUMENT_FREQUENCY = 0.35
MIN_CHUNK_COUNT = 2

_WORD = re.compile(r"^[a-z][a-z0-9\-]{2,}$")


@dataclass(frozen=True)
class VocabularyEntry:
    term: str
    chunk_count: int
    document_frequency: float


@dataclass
class _CacheEntry:
    terms: dict[str, VocabularyEntry]
    built_at: float
    chunk_count: int


class WorkspaceVocabularyService:
    """Per-workspace expansion terms, derived from the workspace's own corpus."""

    def __init__(self) -> None:
        self._cache: dict[uuid.UUID, _CacheEntry] = {}
        self._lock = threading.Lock()

    def invalidate(self, workspace_id: uuid.UUID) -> None:
        with self._lock:
            self._cache.pop(workspace_id, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def _cached(self, workspace_id: uuid.UUID) -> Optional[_CacheEntry]:
        with self._lock:
            entry = self._cache.get(workspace_id)
        if entry is None:
            return None
        if time.monotonic() - entry.built_at > settings.VOCABULARY_CACHE_TTL_SECONDS:
            self.invalidate(workspace_id)
            return None
        return entry

    def _store(self, workspace_id: uuid.UUID, entry: _CacheEntry) -> None:
        with self._lock:
            self._cache[workspace_id] = entry
            while len(self._cache) > settings.VOCABULARY_CACHE_MAX_WORKSPACES:
                oldest = min(self._cache.items(), key=lambda kv: kv[1].built_at)[0]
                self._cache.pop(oldest, None)

    def _build(self, db: Session, workspace_id: uuid.UUID) -> _CacheEntry:
        total = db.execute(
            scoped_chunk_query(db, workspace_id, entity=func.count())
        ).scalar_one()
        if not total:
            return _CacheEntry(terms={}, built_at=time.monotonic(), chunk_count=0)

        rows = db.execute(
            text(
                """
                SELECT word, count(*) AS chunk_count
                FROM document_chunks c,
                     LATERAL unnest(tsvector_to_array(c.content_tsv)) AS word
                WHERE c.workspace_id = :workspace_id
                GROUP BY word
                HAVING count(*) >= :min_chunks
                ORDER BY count(*) DESC
                LIMIT :limit
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "min_chunks": MIN_CHUNK_COUNT,
                "limit": settings.VOCABULARY_MAX_TERMS * 4,
            },
        ).all()

        terms: dict[str, VocabularyEntry] = {}
        for word, chunk_count in rows:
            term = str(word)
            if not _WORD.match(term) or len(term) < MIN_TERM_LENGTH:
                continue
            frequency = chunk_count / total
            # Discard as stop word only when the workspace has enough chunks to measure frequency
            if total >= 10 and frequency > MAX_DOCUMENT_FREQUENCY:
                continue
            terms[term] = VocabularyEntry(
                term=term, chunk_count=int(chunk_count), document_frequency=frequency
            )
            if len(terms) >= settings.VOCABULARY_MAX_TERMS:
                break

        logger.info(
            "vocabulary.built",
            extra={
                "workspace_id": str(workspace_id),
                "chunks": int(total),
                "terms": len(terms),
            },
        )
        return _CacheEntry(
            terms=terms, built_at=time.monotonic(), chunk_count=int(total)
        )

    def terms_for(
        self, db: Optional[Session], workspace_id: Optional[uuid.UUID]
    ) -> dict[str, VocabularyEntry]:
        if db is None or workspace_id is None:
            return {}
        entry = self._cached(workspace_id)
        if entry is None:
            entry = self._build(db, workspace_id)
            self._store(workspace_id, entry)
        return entry.terms

    def expand(
        self,
        db: Optional[Session],
        *,
        workspace_id: Optional[uuid.UUID],
        query: str,
        max_terms: int = 4,
    ) -> list[str]:
        vocabulary = self.terms_for(db, workspace_id)
        if not vocabulary:
            return []

        tokens = {
            token
            for token in re.findall(r"[a-z0-9\-]{3,}", (query or "").lower())
        }
        if not tokens:
            return []

        matches: list[VocabularyEntry] = []
        for token in tokens:
            for term, entry in vocabulary.items():
                if term == token or term in tokens:
                    continue
                if term.startswith(token[: max(3, len(token) - 2)]):
                    matches.append(entry)

        matches.sort(key=lambda entry: entry.document_frequency)
        seen: set[str] = set()
        expanded: list[str] = []
        for entry in matches:
            if entry.term in seen:
                continue
            seen.add(entry.term)
            expanded.append(entry.term)
            if len(expanded) >= max_terms:
                break
        return expanded

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "cached_workspaces": len(self._cache),
                "ttl_seconds": settings.VOCABULARY_CACHE_TTL_SECONDS,
                "entries": {
                    str(key): {
                        "terms": len(entry.terms),
                        "chunks": entry.chunk_count,
                        "age_seconds": round(time.monotonic() - entry.built_at, 1),
                    }
                    for key, entry in self._cache.items()
                },
            }


workspace_vocabulary_service = WorkspaceVocabularyService()

__all__ = [
    "MAX_DOCUMENT_FREQUENCY",
    "MIN_CHUNK_COUNT",
    "MIN_TERM_LENGTH",
    "VocabularyEntry",
    "WorkspaceVocabularyService",
    "workspace_vocabulary_service",
]
