from __future__ import annotations

import logging
import re
import unicodedata
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Final
from uuid import UUID

logger = logging.getLogger(__name__)


class VocabularyProvider(ABC):
    """
    Base interface for all document vocabulary providers.
    """

    @abstractmethod
    def update_document(
        self,
        work_item_id: UUID,
        *,
        original_filename: str,
        title: str | None,
        full_text: str,
    ) -> None:
        ...

    @abstractmethod
    def remove_document(
        self,
        work_item_id: UUID,
    ) -> None:
        ...

    @abstractmethod
    def clear(
        self,
    ) -> None:
        ...

    @abstractmethod
    def rebuild(
        self,
    ) -> None:
        ...

    @abstractmethod
    def get_expansion_map(
        self,
    ) -> dict[str, list[str]]:
        ...


class DocumentVocabularyService(VocabularyProvider):
    """
    Production document vocabulary service.

    Responsibilities:
    - Maintain vocabulary for every WorkItem
    - Build document-derived expansion terms
    - Serve expansion map to QueryService
    - Support rebuild after startup
    """

    _MIN_TOKEN_LENGTH: Final[int] = 2
    _MAX_KEYWORDS: Final[int] = 50

    _FILENAME_NOISE: Final[set[str]] = {
        "copy",
        "draft",
        "final",
        "new",
        "old",
        "temp",
        "test",
        "version",
        "ver",
        "v1",
        "v2",
        "v3",
        "v4",
    }

    _STOP_WORDS: Final[set[str]] = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "it",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
    }

    _TECHNICAL_TERMS: Final[set[str]] = {
        "fastapi",
        "postgresql",
        "mongodb",
        "redis",
        "docker",
        "kubernetes",
        "oauth2",
        "jwt",
        "rest",
        "grpc",
        "api",
        "sdk",
        "python",
        "java",
        "typescript",
        "javascript",
        "llama-3",
        "gpt-4",
        "gemini",
        "claude",
    }
    _URL_PATTERN = re.compile(
        r"https?://\S+|www\.\S+",
        re.IGNORECASE,
    )

    _EMAIL_PATTERN = re.compile(
        r"\S+@\S+\.\S+",
        re.IGNORECASE,
    )

    _NUMERIC_PATTERN = re.compile(
        r"^\d+$",
    )

    _FILENAME_SPLIT_PATTERN = re.compile(
        r"[_\-\s\.]+",
    )

    _NON_WORD_PATTERN = re.compile(
        r"[^\w\-]+",
    )

    _PARAGRAPH_SPLIT_PATTERN = re.compile(
        r"\n\s*\n",
    )

    def __init__(self) -> None:
        self._document_terms: dict[UUID, set[str]] = {}
        self._expansion_map: dict[str, list[str]] = {}

    def update_document(
        self,
        work_item_id: UUID,
        *,
        original_filename: str,
        title: str | None,
        full_text: str,
    ) -> None:
        vocabulary = self._build_document_terms(
            original_filename=original_filename,
            title=title,
            full_text=full_text,
        )

        self._document_terms[work_item_id] = vocabulary
        self._rebuild_expansion_map()

        logger.info(
            "Vocabulary updated for document %s (%d terms).",
            work_item_id,
            len(vocabulary),
        )

    def remove_document(
        self,
        work_item_id: UUID,
    ) -> None:
        removed = self._document_terms.pop(work_item_id, None)
        if removed is None:
            logger.warning(
                "Document vocabulary cleanup completed for WorkItem %s.",
                work_item_id,
            )
            return

        self._rebuild_expansion_map()
        logger.info(
            "Removed vocabulary for document %s.",
            work_item_id,
        )

    def clear(self) -> None:
        document_count = len(self._document_terms)
        self._document_terms.clear()
        self._expansion_map.clear()
        logger.info(
            "Cleared vocabulary service (%d document(s)).",
            document_count,
        )

    def rebuild(self) -> None:
        self._rebuild_expansion_map()
        logger.info(
            "Vocabulary rebuild completed (%d document(s), %d expansion entries).",
            len(self._document_terms),
            len(self._expansion_map),
        )

    def get_expansion_map(self) -> dict[str, list[str]]:
        return {
            token: related_terms.copy()
            for token, related_terms in self._expansion_map.items()
        }

    def _is_valid_token(self, token: str) -> bool:
        if not token:
            return False
        if token in self._TECHNICAL_TERMS:
            return True
        if len(token) < self._MIN_TOKEN_LENGTH:
            return False
        if token in self._STOP_WORDS:
            return False
        if token in self._FILENAME_NOISE:
            return False
        if self._URL_PATTERN.fullmatch(token):
            return False
        if self._EMAIL_PATTERN.fullmatch(token):
            return False
        if self._NUMERIC_PATTERN.fullmatch(token):
            return False
        if not any(character.isalnum() for character in token):
            return False
        return True

    def _normalize_token(self, token: str) -> str:
        token = unicodedata.normalize("NFKC", token)
        token = token.lower().strip()
        token = re.sub(r"'s\b", "", token)

        if token not in self._TECHNICAL_TERMS:
            token = re.sub(r"[^\w\s]", " ", token)

        token = re.sub(r"\s+", " ", token)
        return token.strip()

    def _normalize_tokens(self, tokens: list[str]) -> list[str]:
        normalized_tokens: list[str] = []
        for token in tokens:
            normalized = self._normalize_token(token)
            if not normalized:
                continue
            for sub_token in normalized.split():
                if self._is_valid_token(sub_token):
                    normalized_tokens.append(sub_token)
        return normalized_tokens

    def _extract_text_terms(self, full_text: str) -> set[str]:
        if not full_text or not full_text.strip():
            logger.warning("Empty text provided for vocabulary extraction.")
            return set()

        full_text = full_text[:250000]
        full_text = " ".join(full_text.split())
        paragraphs = self._PARAGRAPH_SPLIT_PATTERN.split(full_text)

        all_valid_tokens: list[str] = []
        for paragraph in paragraphs:
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            raw_tokens = paragraph.split()
            normalized = self._normalize_tokens(raw_tokens)
            all_valid_tokens.extend(normalized)

        frequencies = self._compute_term_frequency(all_valid_tokens)
        selected_terms = self._select_keywords(frequencies)
        
        logger.info(
            "Extracted %d keywords from %d normalized tokens.",
            len(selected_terms),
            len(all_valid_tokens),
        )
        return selected_terms

    def _extract_filename_terms(self, filename: str) -> set[str]:
        if not filename:
            return set()

        stem = Path(filename).stem
        raw_tokens = self._FILENAME_SPLIT_PATTERN.split(stem)
        return set(self._normalize_tokens(raw_tokens))

    def _extract_title_terms(self, title: str | None) -> set[str]:
        if not title:
            return set()

        raw_tokens = title.split()
        return set(self._normalize_tokens(raw_tokens))

    def _compute_term_frequency(self, tokens: list[str]) -> dict[str, int]:
        frequencies: dict[str, int] = {}
        for token in tokens:
            frequencies[token] = frequencies.get(token, 0) + 1
        return frequencies

    def _select_keywords(self, frequencies: dict[str, int]) -> set[str]:
        if not frequencies:
            return set()

        ranked = sorted(
            frequencies.items(),
            key=lambda item: (-item[1], item[0]),
        )

        return {token for token, _ in ranked[: self._MAX_KEYWORDS]}

    def _build_document_terms(
        self,
        *,
        original_filename: str,
        title: str | None,
        full_text: str,
    ) -> set[str]:
        filename_terms = self._extract_filename_terms(original_filename)
        title_terms = self._extract_title_terms(title)
        body_terms = self._extract_text_terms(full_text)

        vocabulary = filename_terms | title_terms | body_terms

        logger.info(
            "Document vocabulary built (%d terms).",
            len(vocabulary),
        )
        return vocabulary

    def _rebuild_expansion_map(self) -> None:
        expansion_map: dict[str, set[str]] = {}

        for vocabulary in self._document_terms.values():
            for token in vocabulary:
                related_terms = vocabulary - {token}
                if not related_terms:
                    continue
                expansion_map.setdefault(token, set()).update(related_terms)

        self._expansion_map = {
            token: sorted(related_terms)
            for token, related_terms in expansion_map.items()
        }

        logger.info(
            "Expansion map rebuilt (%d terms).",
            len(self._expansion_map),
        )


document_vocabulary_service = DocumentVocabularyService()

__all__ = ["DocumentVocabularyService", "document_vocabulary_service"]