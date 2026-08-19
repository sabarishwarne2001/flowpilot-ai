"""ARCH-11.5 Step 5 — citation ranking and snippet extraction."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.core.config import settings

logger = logging.getLogger("app.services.citation")

# ===========================================================================
# Citations
# ===========================================================================

CITATION_SIGNALS: tuple[tuple[str, float], ...] = (
    ("rerank_score", 2.0),
    ("rrf_score", 1.5),
    ("similarity_score", 1.0),
    ("lexical_score", 0.5),
)

CITATION_RRF_K = 60


@dataclass
class RankedCitation:
    result: dict[str, Any]
    score: float
    contributing: tuple[str, ...]

    def as_details(self) -> dict[str, Any]:
        return {
            "chunk_id": self.result.get("id"),
            "score": round(self.score, 6),
            "signals": list(self.contributing),
        }


class CitationService:
    """Orders evidence by fusing signal ranks. Never raises on missing signals."""

    def _rank_positions(
        self, results: Sequence[dict[str, Any]], key: str
    ) -> dict[int, int]:
        scored = [
            (index, float(value))
            for index, result in enumerate(results)
            if isinstance((value := result.get(key)), (int, float))
        ]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return {index: rank for rank, (index, _) in enumerate(scored, start=1)}

    def rank_citations(
        self, results: Sequence[dict[str, Any]], *, limit: Optional[int] = None
    ) -> list[dict[str, Any]]:
        if not results:
            return []

        rankings = {
            key: self._rank_positions(results, key) for key, _ in CITATION_SIGNALS
        }

        ranked: list[RankedCitation] = []
        for index, result in enumerate(results):
            score = 0.0
            contributing: list[str] = []
            for key, weight in CITATION_SIGNALS:
                rank = rankings[key].get(index)
                if rank is None:
                    continue
                score += weight / (CITATION_RRF_K + rank)
                contributing.append(key)
            ranked.append(
                RankedCitation(
                    result=result, score=score, contributing=tuple(contributing)
                )
            )

        if not any(entry.contributing for entry in ranked):
            logger.info(
                "citation.no_signals",
                extra={"results": len(results), "note": "retrieval order preserved"},
            )
            ordered = list(results)
        else:
            ranked.sort(key=lambda entry: entry.score, reverse=True)
            ordered = [entry.result for entry in ranked]

        cap = limit or settings.MAX_CITATIONS
        final = ordered[:cap] if cap else ordered

        logger.info(
            "citation.ranked",
            extra={
                "candidates": len(results),
                "returned": len(final),
                "signals_present": sorted(
                    {signal for entry in ranked for signal in entry.contributing}
                ),
            },
        )
        return final


citation_service = CitationService()


# ===========================================================================
# Snippets
# ===========================================================================

_ABBREVIATIONS: frozenset[str] = frozenset(
    {
        "dr", "mr", "mrs", "ms", "prof", "sr", "jr", "st",
        "rs", "usd", "eur", "inr", "approx", "est",
        "no", "nos", "fig", "figs", "eq", "ref", "sec", "cl", "art",
        "vs", "etc", "e.g", "i.e", "viz", "cf", "al",
        "inc", "ltd", "llc", "plc", "co", "corp", "pvt",
        "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
        "oct", "nov", "dec",
    }
)

_SENTENCE_END = re.compile(r"(?<=[.!?])[\"')\]]?\s+")


@dataclass
class SnippetResult:
    text: str
    chunk_start_char: int
    chunk_end_char: int
    page_start_char: Optional[int] = None
    page_end_char: Optional[int] = None
    matched_terms: tuple[str, ...] = ()

    def as_details(self) -> dict[str, Any]:
        return {
            "characters": len(self.text),
            "chunk_span": [self.chunk_start_char, self.chunk_end_char],
            "page_span": [self.page_start_char, self.page_end_char],
            "matched_terms": list(self.matched_terms),
        }


class SnippetService:
    """Sentence-aware, abbreviation-aware, offset-preserving snippets."""

    def _is_abbreviation(self, text: str, match_end: int) -> bool:
        prefix = text[:match_end].rstrip()
        token_match = re.search(r"([A-Za-z0-9.]+)\.$", prefix)
        if not token_match:
            return False
        token = token_match.group(1).lower()
        if token in _ABBREVIATIONS:
            return True
        if len(token) == 1 and token.isalpha():
            return True
        return False

    def split_sentences(self, text: str) -> list[tuple[int, int]]:
        if not (text or "").strip():
            return []

        spans: list[tuple[int, int]] = []
        cursor = 0
        for match in _SENTENCE_END.finditer(text):
            boundary = match.start()
            if self._is_abbreviation(text, boundary + 1):
                continue
            end = match.start() + 1
            if end > cursor and text[cursor:end].strip():
                spans.append((cursor, end))
            cursor = match.end()

        if cursor < len(text) and text[cursor:].strip():
            spans.append((cursor, len(text)))
        return spans

    def generate(
        self,
        *,
        text: str,
        query: str,
        max_characters: Optional[int] = None,
        chunk_page_start: Optional[int] = None,
    ) -> SnippetResult:
        budget = int(max_characters or settings.SNIPPET_MAX_LENGTH)
        body = text or ""
        spans = self.split_sentences(body)

        if not spans:
            trimmed = body[:budget]
            return SnippetResult(
                text=trimmed.strip(),
                chunk_start_char=0,
                chunk_end_char=len(trimmed),
                page_start_char=chunk_page_start,
                page_end_char=(
                    chunk_page_start + len(trimmed)
                    if chunk_page_start is not None
                    else None
                ),
            )

        terms = {
            token
            for token in re.findall(r"[a-z0-9\-]{3,}", (query or "").lower())
        }

        def score(span: tuple[int, int]) -> tuple[int, int]:
            sentence = body[span[0] : span[1]].lower()
            hits = sum(1 for term in terms if term in sentence)
            return (hits, -span[0])

        best = max(spans, key=score) if terms else spans[0]
        matched = tuple(
            sorted(term for term in terms if term in body[best[0] : best[1]].lower())
        )

        start, end = best
        index = spans.index(best)
        forward, backward = index + 1, index - 1
        while end - start < budget:
            grew = False
            if forward < len(spans) and spans[forward][1] - start <= budget:
                end = spans[forward][1]
                forward += 1
                grew = True
            if backward >= 0 and end - spans[backward][0] <= budget:
                start = spans[backward][0]
                backward -= 1
                grew = True
            if not grew:
                break

        snippet = body[start:end].strip()
        if len(snippet) > budget:
            snippet = snippet[:budget].rsplit(" ", 1)[0]
            end = start + len(snippet)

        return SnippetResult(
            text=snippet,
            chunk_start_char=start,
            chunk_end_char=end,
            page_start_char=(
                chunk_page_start + start if chunk_page_start is not None else None
            ),
            page_end_char=(
                chunk_page_start + end if chunk_page_start is not None else None
            ),
            matched_terms=matched,
        )

    def generate_snippet(self, *, text: str, query: str) -> str:
        return self.generate(text=text, query=query).text


snippet_service = SnippetService()

__all__ = [
    "CITATION_RRF_K",
    "CITATION_SIGNALS",
    "CitationService",
    "RankedCitation",
    "SnippetResult",
    "SnippetService",
    "citation_service",
    "snippet_service",
]