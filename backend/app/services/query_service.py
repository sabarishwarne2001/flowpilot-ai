"""
Query preprocessing service for FlowPilot AI.
ARCH-11.5 Step 3: Linguistic expansion and workspace-scoped derived vocabulary expansion.
"""

from __future__ import annotations

import logging
import re
import uuid
from abc import ABC, abstractmethod
from collections import OrderedDict
from typing import Iterable, Optional

from sqlalchemy.orm import Session

logger = logging.getLogger("app.services.query_service")


class QueryExpansionStrategy(ABC):
    """
    Base class for query expansion strategies.
    """

    @abstractmethod
    def expand(self, query: str) -> str:
        pass


class LinguisticExpansionStrategy(QueryExpansionStrategy):
    """
    Lightweight deterministic linguistic expansion.
    """

    LINGUISTIC_EXPANSIONS = {
        "summarize": ["summary", "summarise", "overview", "explain"],
        "summary": ["summarize", "summarise", "overview"],
        "analyze": ["analysis", "analyse", "inspect", "evaluate"],
        "analysis": ["analyze", "analyse"],
        "find": ["locate", "search", "identify"],
        "search": ["find", "locate"],
        "explain": ["describe", "clarify", "define"],
        "compare": ["difference", "similarities", "contrast"],
        "list": ["show", "display", "enumerate"],
        "document": ["file", "record", "report"],
        "page": ["section", "content"],
        "chapter": ["section"],
        "topic": ["subject", "theme"],
    }

    def expand(self, query: str) -> str:
        tokens = query.split()
        expanded: list[str] = []
        seen: set[str] = set()

        for token in tokens:
            if token not in seen:
                expanded.append(token)
                seen.add(token)

            for synonym in self.LINGUISTIC_EXPANSIONS.get(token, []):
                if synonym not in seen:
                    expanded.append(synonym)
                    seen.add(synonym)

        return " ".join(expanded)


class QueryService:
    """
    Query preprocessing with linguistic and tenant-scoped expansion.
    """

    def __init__(self) -> None:
        self.linguistic_strategy = LinguisticExpansionStrategy()

    def normalize(self, query: str) -> str:
        query = query.lower().strip()
        query = re.sub(r"'s\b", "", query)
        query = re.sub(r"[^\w\s\-]", " ", query)
        query = re.sub(r"\bresume\b", " resume ", query)
        query = re.sub(r"\bcv\b", " cv ", query)
        query = re.sub(r"\s+", " ", query)
        return query.strip()

    def _deduplicate_tokens(self, tokens: Iterable[str]) -> list[str]:
        ordered = OrderedDict()
        for token in tokens:
            cleaned = token.strip()
            if cleaned:
                ordered.setdefault(cleaned, None)
        return list(ordered.keys())

    def preprocess(
        self,
        query: str,
        *,
        db: Session | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> str:
        from app.services.vocabulary_service import workspace_vocabulary_service

        normalized = self.normalize(query)
        expanded = self.linguistic_strategy.expand(normalized)
        workspace_terms = workspace_vocabulary_service.expand(
            db, workspace_id=workspace_id, query=normalized
        )
        tokens = self._deduplicate_tokens(expanded.split() + workspace_terms)
        return " ".join(tokens)

    def generate_search_queries(
        self,
        query: str,
        *,
        db: Session | None = None,
        workspace_id: uuid.UUID | None = None,
    ) -> list[str]:
        normalized = self.normalize(query)
        expanded = self.preprocess(query, db=db, workspace_id=workspace_id)
        queries = [query.strip(), normalized, expanded]
        return self._deduplicate_tokens(queries)


query_service = QueryService()

__all__ = ["LinguisticExpansionStrategy", "QueryExpansionStrategy", "QueryService", "query_service"]