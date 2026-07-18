"""
Query preprocessing service.

Normalizes and expands user queries before semantic retrieval.

Pipeline

Raw Query
      ↓
Normalization
      ↓
Expansion
      ↓
Embedding
"""

from __future__ import annotations
import re

from collections import OrderedDict
from typing import Iterable

from abc import ABC
from abc import abstractmethod

import logging

logger = logging.getLogger(__name__)


class QueryExpansionStrategy(ABC):
    """
    Base class for query expansion strategies.
    """

    @abstractmethod
    def expand(
        self,
        query: str,
    ) -> str:
        pass


class LinguisticExpansionStrategy(
    QueryExpansionStrategy,
):
    """
    Lightweight deterministic expansion.
    """

    LINGUISTIC_EXPANSIONS = {

        #
        # Summarization
        #
        "summarize": [
            "summary",
            "summarise",
            "overview",
            "explain",
        ],

        "summary": [
            "summarize",
            "summarise",
            "overview",
        ],

        #
        # Analysis
        #
        "analyze": [
            "analysis",
            "analyse",
            "inspect",
            "evaluate",
        ],

        "analysis": [
            "analyze",
            "analyse",
        ],

        #
        # Search
        #
        "find": [
            "locate",
            "search",
            "identify",
        ],

        "search": [
            "find",
            "locate",
        ],

        #
        # Explain
        #
        "explain": [
            "describe",
            "clarify",
            "define",
        ],

        #
        # Compare
        #
        "compare": [
            "difference",
            "similarities",
            "contrast",
        ],

        #
        # List
        #
        "list": [
            "show",
            "display",
            "enumerate",
        ],

        #
        # Generic document nouns
        #
        "document": [
            "file",
            "record",
            "report",
        ],

        "page": [
            "section",
            "content",
        ],

        "chapter": [
            "section",
        ],

        "topic": [
            "subject",
            "theme",
        ],
    }


    def expand(
        self,
        query: str,
    ) -> str:

        tokens = query.split()

        expanded: list[str] = []

        seen: set[str] = set()

        for token in tokens:

            if token not in seen:

                expanded.append(
                    token
                )

                seen.add(
                    token
                )

            for synonym in self.LINGUISTIC_EXPANSIONS.get(
                token,
                [],
            ):

                if synonym not in seen:

                    expanded.append(
                        synonym
                    )

                    seen.add(
                        synonym
                    )

        return " ".join(
            expanded
        )


class DocumentExpansionStrategy(
    QueryExpansionStrategy,
):
    """
    Production document-aware expansion.

    Expands queries using vocabulary extracted
    from uploaded documents.

    The vocabulary is populated during document
    ingestion and remains completely
    domain-agnostic.
    """

    def __init__(
        self,
    ) -> None:

        self._vocabulary: dict[
            str,
            list[str],
        ] = {}

    
    def update_document_vocabulary(
        self,
        vocabulary: dict[
            str,
            list[str],
        ],
    ) -> None:
        """
        Update the document-aware expansion vocabulary.
        """

        self.update_vocabulary(
            vocabulary,
        )

        logger.info(
            "QueryService document vocabulary updated (%d entries).",
            len(
                vocabulary,
            ),
        )

    def update_vocabulary(
        self,
        vocabulary: dict[
            str,
            list[str],
        ],
    ) -> None:

        self._vocabulary = {
            key.lower(): [
                value.lower()
                for value in values
            ]
            for key, values in vocabulary.items()
        }

        logger.info(
            "Document vocabulary updated (%d entries).",
            len(
                self._vocabulary,
            ),
        )

    def clear(
        self,
    ) -> None:

        self._vocabulary.clear()

    def expand(
        self,
        query: str,
    ) -> str:

        tokens = query.split()

        expanded: list[str] = []

        seen: set[str] = set()

        for token in tokens:

            if token not in seen:

                expanded.append(
                    token
                )

                seen.add(
                    token
                )

            for value in self._vocabulary.get(
                token,
                [],
            ):

                if value not in seen:

                    expanded.append(
                        value
                    )

                    seen.add(
                        value
                    )

        return " ".join(
            expanded
        )
    

class CompositeExpansionStrategy(
    QueryExpansionStrategy,
):
    """
    Production expansion pipeline.

    Expansion order

    1. Linguistic Expansion
    2. Document Expansion
    3. Optional LLM Expansion (future)

    Every strategy operates on the output of the
    previous strategy.
    """

    def __init__(
        self,
        *strategies: QueryExpansionStrategy,
    ) -> None:

        self._strategies = list(
            strategies
        )

    def expand(
        self,
        query: str,
    ) -> str:

        expanded = query

        for strategy in self._strategies:

            expanded = strategy.expand(
                expanded,
            )

        return expanded


class QueryService:
    """
    Query preprocessing.
    """

    def __init__(
        self,
    ):

        self.linguistic_strategy = (
            LinguisticExpansionStrategy()
        )

        self.document_strategy = (
            DocumentExpansionStrategy()
        )

        #
        # Future:
        #
        # self.llm_strategy = ...
        #

        self.strategy = (
            CompositeExpansionStrategy(
                self.linguistic_strategy,
                self.document_strategy,
            )
        )

    def normalize(
        self,
        query: str,
    ) -> str:
        """
        Production-grade normalization.

        - lowercase
        - remove punctuation
        - normalize whitespace
        - normalize common abbreviations
        - normalize possessives
        """

        query = query.lower().strip()

        query = re.sub(
            r"'s\b",
            "",
            query,
        )

        query = re.sub(
            r"[^\w\s]",
            " ",
            query,
        )

        query = re.sub(
            r"\bresume\b",
            " resume ",
            query,
        )

        query = re.sub(
            r"\bcv\b",
            " cv ",
            query,
        )

        query = re.sub(
            r"\bupsc\b",
            " upsc ",
            query,
        )

        query = re.sub(
            r"\s+",
            " ",
            query,
        )

        return query.strip()
    

    def _deduplicate_tokens(
        self,
        tokens: Iterable[str],
    ) -> list[str]:
        """
        Remove duplicate tokens while preserving order.
        """

        ordered = OrderedDict()

        for token in tokens:

            token = token.strip()

            if not token:
                continue

            ordered.setdefault(
                token,
                None,
            )

        return list(
            ordered.keys()
        )
    

    def generate_search_queries(
        self,
        query: str,
    ) -> list[str]:
        """
        Generate diversified search queries for
        multi-query retrieval.

        Returns queries ordered from highest to
        lowest precision.
        """

        normalized = self.normalize(
            query,
        )

        expanded = self.preprocess(
            query,
        )

        queries = [

            #
            # Original query
            #
            query.strip(),

            #
            # Normalized query
            #
            normalized,

            #
            # Expanded query
            #
            expanded,
        ]

        queries = self._deduplicate_tokens(
            queries
        )

        logger.info(
            "Generated %d search quer%s.",
            len(queries),
            "y" if len(queries) == 1 else "ies",
        )

        for index, generated_query in enumerate(
            queries,
            start=1,
        ):

            logger.info(
                "Query %d: %s",
                index,
                generated_query,
            )

        return queries


    def preprocess(
        self,
        query: str,
    ) -> str:
        """
        Production preprocessing.

        Pipeline

        Normalize
            ↓
        Expand
            ↓
        Remove duplicates
            ↓
        Stable ordering
        """

        normalized = self.normalize(
            query,
        )

        expanded = self.strategy.expand(
            normalized,
        )

        tokens = self._deduplicate_tokens(
            expanded.split()
        )

        expanded = " ".join(
            tokens
        )

        logger.info(
            "Expanded query: %s",
            expanded,
        )

        return expanded


query_service = QueryService()