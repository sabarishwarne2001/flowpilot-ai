from __future__ import annotations

from abc import ABC, abstractmethod

import logging
import re
import unicodedata

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

class DocumentVocabularyService(
    VocabularyProvider,
):
    """
    Production document vocabulary service.

    Responsibilities

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
        "chromadb",
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

    

    def __init__(
        self,
    ) -> None:

        #
        # WorkItem vocabulary
        #
        # {
        #     work_item_id: {
        #         "token1",
        #         "token2",
        #     }
        # }
        #
        self._document_terms: dict[
            UUID,
            set[str],
        ] = {}

        #
        # Expansion map consumed by
        # DocumentExpansionStrategy
        #
        # {
        #     token:
        #         [
        #             related_term1,
        #             related_term2,
        #         ]
        # }
        #
        self._expansion_map: dict[
            str,
            list[str],
        ] = {}

        
    #
    # Interface methods
    #

    def update_document(
        self,
        work_item_id: UUID,
        *,
        original_filename: str,
        title: str | None,
        full_text: str,
    ) -> None:
        """
        Build and store the vocabulary for a single document.

        If the document already exists, its vocabulary is replaced.
        The global expansion map is rebuilt after every update.
        """

        vocabulary = self._build_document_terms(
            original_filename=original_filename,
            title=title,
            full_text=full_text,
        )

        self._document_terms[
            work_item_id
        ] = vocabulary

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
        """
        Remove a document from the vocabulary store.

        The global expansion map is rebuilt after
        removal to prevent stale expansion terms.
        """

        removed = self._document_terms.pop(
            work_item_id,
            None,
        )

        if removed is None:

            logger.warning(
                "Vocabulary for document %s does not exist.",
                work_item_id,
            )

            return

        self._rebuild_expansion_map()

        logger.info(
            "Removed vocabulary for document %s.",
            work_item_id,
        )

    def clear(
        self,
    ) -> None:
        """
        Remove all stored document vocabulary and
        reset the expansion map.

        This should be called whenever the entire
        knowledge base is cleared.
        """

        document_count = len(
            self._document_terms
        )

        self._document_terms.clear()

        self._expansion_map.clear()

        logger.info(
            "Cleared vocabulary service (%d document(s)).",
            document_count,
        )

    def rebuild(
        self,
    ) -> None:
        """
        Rebuild the global expansion map from the
        currently stored document vocabularies.

        This method does not recreate individual
        document vocabularies. It regenerates the
        global lookup after documents have already
        been loaded into the service.
        """

        self._rebuild_expansion_map()

        logger.info(
            "Vocabulary rebuild completed (%d document(s), %d expansion entries).",
            len(
                self._document_terms,
            ),
            len(
                self._expansion_map,
            ),
        )

    def get_expansion_map(
        self,
    ) -> dict[str, list[str]]:
        """
        Return the current document-derived expansion map.

        A defensive copy is returned to prevent callers from
        mutating the internal state.
        """

        return {
            token: related_terms.copy()
            for token, related_terms
            in self._expansion_map.items()
        }
    

    def _is_valid_token(
        self,
        token: str,
    ) -> bool:
        """
        Determine whether a normalized token should
        be kept in the vocabulary.
        """

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

        if not any(
            character.isalnum()
            for character in token
        ):
            return False

        return True


    def _normalize_token(
        self,
        token: str,
    ) -> str:
        """
        Normalize a single token.

        The normalization behavior mirrors QueryService
        so both indexing and querying use the same rules.
        """

        token = unicodedata.normalize(
            "NFKC",
            token,
        )

        token = token.lower().strip()

        token = re.sub(
            r"'s\b",
            "",
            token,
        )

        #
        # Preserve technical identifiers such as
        # gpt-4, llama-3, oauth2.
        #
        if token not in self._TECHNICAL_TERMS:

            token = re.sub(
                r"[^\w\s]",
                " ",
                token,
            )

        token = re.sub(
            r"\s+",
            " ",
            token,
        )

        return token.strip()


    def _extract_text_terms(self, full_text: str) -> set[str]:
        """
        Internal pipeline to extract high-value vocabulary from document body.
        
        Args:
            full_text: Raw extracted text from the document.
            
        Returns:
            A set of selected keywords based on statistical frequency.
        """
        if not full_text or not full_text.strip():
            logger.warning("Empty text provided for vocabulary extraction.")
            return set()

        # 1. Paragraph and Sentence Extraction
        full_text = full_text[:250000]

        full_text = " ".join(
            full_text.split()
        )

        paragraphs = self._PARAGRAPH_SPLIT_PATTERN.split(
            full_text,
        )

        # 2. Tokenization and Normalization 
        # (like POS tagging) are added, but use the base normalize_tokens for now.
        all_valid_tokens: list[str] = []
        for paragraph in paragraphs:

            paragraph = paragraph.strip()

            if not paragraph:
                continue
            raw_tokens = paragraph.split()
            normalized = self._normalize_tokens(
                raw_tokens,
            )
            all_valid_tokens.extend(normalized)

        # 3. Frequency Calculation
        frequencies = self._compute_term_frequency(all_valid_tokens)

        # 4. Keyword Selection (Top-K)
        selected_terms = self._select_keywords(frequencies)
        
        logger.info(
            "Extracted %d keywords from %d normalized tokens.",
            len(selected_terms),
            len(all_valid_tokens),
        )
        return selected_terms
    

    def _extract_filename_terms(
        self,
        filename: str,
    ) -> set[str]:
        """
        Extract normalized vocabulary from a document
        filename.

        Example

        Resume_Sabarish_Final.pdf

            ↓

        {
            "resume",
            "sabarish",
        }
        """

        if not filename:
            return set()

        stem = Path(
            filename,
        ).stem

        raw_tokens = (
            self._FILENAME_SPLIT_PATTERN.split(
                stem,
            )
        )

        return self._normalize_tokens(
            raw_tokens,
        )


    def _extract_title_terms(
        self,
        title: str | None,
    ) -> set[str]:
        """
        Extract normalized vocabulary from a document
        title.
        """

        if not title:
            return set()

        raw_tokens = title.split()

        return self._normalize_tokens(
            raw_tokens,
        )
    
    def _compute_term_frequency(
        self,
        tokens: list[str],
    ) -> dict[str, int]:
        """
        Build a frequency table for normalized tokens.
        """

        frequencies: dict[str, int] = {}

        for token in tokens:

            frequencies[token] = (
                frequencies.get(
                    token,
                    0,
                )
                + 1
            )

        return frequencies

    def _select_keywords(
        self,
        frequencies: dict[str, int],
    ) -> set[str]:
        """
        Select the strongest keywords from the
        frequency table.
        """

        if not frequencies:
            return set()

        ranked = sorted(
            frequencies.items(),
            key=lambda item: (
                -item[1],
                item[0],
            ),
        )

        return {

            token

            for token, _ in ranked[
                : self._MAX_KEYWORDS            ]

        }

    def _build_document_terms(
        self,
        *,
        original_filename: str,
        title: str | None,
        full_text: str,
    ) -> set[str]:
        """
        Build the complete document vocabulary.
        """

        filename_terms = (
            self._extract_filename_terms(
                original_filename,
            )
        )

        title_terms = (
            self._extract_title_terms(
                title,
            )
        )

        body_terms = (
            self._extract_text_terms(
                full_text,
            )
        )

        vocabulary = (
            filename_terms
            | title_terms
            | body_terms
        )

        logger.info(
            "Document vocabulary built (%d terms).",
            len(vocabulary),
        )

        return vocabulary
    

    def _rebuild_expansion_map(
        self,
    ) -> None:
        """
        Rebuild the global expansion map from every
        stored document vocabulary.

        Each normalized term maps to the remaining
        terms belonging to the same document.

        Example

        Resume document

            resume
            python
            fastapi
            docker

        Produces

        resume ->
            python
            fastapi
            docker

        python ->
            resume
            fastapi
            docker
        """

        expansion_map: dict[
            str,
            set[str],
        ] = {}

        for vocabulary in self._document_terms.values():

            for token in vocabulary:

                related_terms = (
                    vocabulary
                    - {token}
                )

                if not related_terms:
                    continue

                expansion_map.setdefault(
                    token,
                    set(),
                ).update(
                    related_terms,
                )

        self._expansion_map = {

            token: sorted(related_terms)

            for token, related_terms
            in expansion_map.items()

        }

        logger.info(
            "Expansion map rebuilt (%d terms).",
            len(
                self._expansion_map,
            ),
        )