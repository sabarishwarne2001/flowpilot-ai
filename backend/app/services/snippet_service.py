"""
Snippet extraction service.

Generates concise, high-quality snippets from retrieved chunks
for citations and search previews.

Future extensions:

- keyword highlighting
- semantic sentence ranking
- PDF highlight coordinates
- frontend search previews
"""

from __future__ import annotations

import re

from app.core.config import settings

import logging

logger = logging.getLogger(__name__)


class SnippetService:
    """
    Generates concise evidence snippets.
    """

    def split_sentences(
        self,
        text: str,
    ) -> list[str]:

        text = text.strip()

        if not text:
            return []

        return [

            sentence.strip()

            for sentence in re.split(
                r"(?<=[.!?])\s+",
                text,
            )

            if sentence.strip()

        ]

    def _score_sentence(
        self,
        sentence: str,
        query: str,
    ) -> float:
        """
        Lightweight lexical relevance score.

        Can later be replaced with embedding similarity
        without changing callers.
        """

        sentence_words = {

            word.lower()

            for word in re.findall(
                r"\w+",
                sentence,
            )

        }

        query_words = {

            word.lower()

            for word in re.findall(
                r"\w+",
                query,
            )

        }

        if not sentence_words:

            return 0.0

        overlap = sentence_words.intersection(
            query_words,
        )

        return len(overlap) / len(query_words or {"_"})

    def generate_snippet(
        self,
        *,
        text: str,
        query: str,
    ) -> str:
        """
        Return the strongest evidence sentences.
        """

        sentences = self.split_sentences(
            text,
        )

        if not sentences:

            return text.strip()

        ranked = sorted(

            sentences,

            key=lambda sentence: self._score_sentence(
                sentence,
                query,
            ),

            reverse=True,

        )

        snippet = " ".join(

            ranked[
                : settings.SNIPPET_MAX_SENTENCES
            ]

        )

        snippet = snippet.strip()

        if len(snippet) > settings.MAX_SNIPPET_LENGTH:

            snippet = (
                snippet[
                    : settings.MAX_SNIPPET_LENGTH
                ].rstrip()
                + "..."
            )

        logger.debug(
            "Generated snippet (%d chars).",
            len(snippet),
        )

        return snippet


snippet_service = SnippetService()