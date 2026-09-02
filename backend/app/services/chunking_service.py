"""ARCH-11 Step 3 — token-aware chunking with provenance.

Measures tokens with the embedding model tokenizer (targeting 220 tokens with 10% overlap).
Snaps boundaries backwards to paragraphs, sentences, and lines while strictly preserving
character offset invariants for bounding box provenance.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from app.services.document_models import (
    BBOX_FROM_BLOCKS,
    BBOX_NO_BLOCKS,
    BBOX_NO_INTERSECTION,
    BBOX_SPAN_MISMATCH,
    BlockSpan,
    ChunkCandidate,
    DocumentPage,
    union_box,
)

logger = logging.getLogger("app.services.chunking_service")

DEFAULT_CHUNK_SIZE_TOKENS = 220
DEFAULT_CHUNK_OVERLAP_PCT = 10

#: Hard floor for chunk sizing.
MIN_CHUNK_TOKENS = 32

#: Minimum fraction of budget required to accept a boundary snap.
MIN_SNAP_RATIO = 0.6

#: Break preference hierarchy.
_BREAK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("paragraph", re.compile(r"\n[ \t]*\n")),
    ("sentence", re.compile(r"(?<=[.!?])[\"')\]]?[ \t]*(?:\n|$|(?=[ \t]))")),
    ("line", re.compile(r"\n")),
)


class ChunkingError(RuntimeError):
    """Chunking could not proceed."""


def _encode_with_offsets(
    tokenizer: Any, text: str
) -> tuple[list[tuple[int, int]], bool]:
    """Return `[(char_start, char_end), ...]` per token, and whether it is exact."""
    try:
        encoding = tokenizer(
            text,
            add_special_tokens=False,
            truncation=False,
            padding=False,
            return_offsets_mapping=True,
            verbose=False,
        )
        offsets = list(encoding["offset_mapping"])
        if offsets:
            return [(int(s), int(e)) for s, e in offsets], True
        return [], True
    except (TypeError, KeyError, NotImplementedError, ValueError):
        return _offsets_by_word(tokenizer, text), False


def _offsets_by_word(tokenizer: Any, text: str) -> list[tuple[int, int]]:
    """Fallback for tokenizers without offset mapping."""
    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if not words:
        return []
    counts = tokenizer(
        [text[s:e] for s, e in words],
        add_special_tokens=False,
        truncation=False,
        padding=False,
        verbose=False,
    )["input_ids"]
    offsets: list[tuple[int, int]] = []
    for (start, end), ids in zip(words, counts):
        offsets.extend([(start, end)] * max(1, len(ids)))
    return offsets


def _break_positions(text: str) -> dict[str, list[int]]:
    """Character positions at which a chunk may cleanly end."""
    found: dict[str, list[int]] = {}
    for name, pattern in _BREAK_PATTERNS:
        found[name] = sorted({m.end() for m in pattern.finditer(text)})
    return found


def _snap_end(
    *,
    text: str,
    breaks: dict[str, list[int]],
    hard_end_char: int,
    start_char: int,
    min_chars: int,
) -> int:
    """Largest break position at or before hard_end_char, or the hard end."""
    import bisect

    for name, _ in _BREAK_PATTERNS:
        positions = breaks.get(name) or []
        index = bisect.bisect_right(positions, hard_end_char) - 1
        while index >= 0:
            candidate = positions[index]
            if candidate - start_char >= min_chars and candidate > start_char:
                return candidate
            index -= 1
    return hard_end_char


def _token_index_for_char(offsets: Sequence[tuple[int, int]], char: int, lo: int) -> int:
    """First token index at or after `char`."""
    index = lo
    while index < len(offsets) and offsets[index][0] < char:
        index += 1
    return index


def _bbox_for_span(
    page: DocumentPage,
    spans: Sequence[BlockSpan],
    span_status: str,
    start: int,
    end: int,
) -> tuple[Optional[dict[str, Any]], str]:
    if span_status != BBOX_FROM_BLOCKS:
        return None, span_status
    overlapping = [span for span in spans if span.overlaps(start, end)]
    if not overlapping:
        return None, BBOX_NO_INTERSECTION
    box = union_box(
        overlapping,
        page_number=page.page_number,
        page_width=page.width,
        page_height=page.height,
    )
    return (box, BBOX_FROM_BLOCKS) if box else (None, BBOX_NO_INTERSECTION)


def split_pages(
    pages: Sequence[DocumentPage],
    *,
    tokenizer: Any,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_pct: int = DEFAULT_CHUNK_OVERLAP_PCT,
) -> list[ChunkCandidate]:
    """Chunk document pages. Chunks never span across page boundaries."""
    if chunk_size_tokens < MIN_CHUNK_TOKENS:
        raise ChunkingError(
            f"chunk_size_tokens={chunk_size_tokens} is below the {MIN_CHUNK_TOKENS}-token floor."
        )
    if not 0 <= chunk_overlap_pct < 50:
        raise ChunkingError(
            f"chunk_overlap_pct={chunk_overlap_pct} must be in [0, 50)."
        )

    overlap_tokens = int(chunk_size_tokens * chunk_overlap_pct / 100)
    candidates: list[ChunkCandidate] = []
    chunk_index = 0

    for page in pages:
        text = page.text
        if not text.strip():
            continue

        offsets, exact = _encode_with_offsets(tokenizer, text)
        if not offsets:
            continue
        if not exact:
            logger.info(
                "chunking.word_offset_fallback",
                extra={"page": page.page_number, "tokens": len(offsets)},
            )

        spans, span_status = page.block_spans()
        if span_status == BBOX_SPAN_MISMATCH:
            logger.warning(
                "chunking.block_span_mismatch",
                extra={"page": page.page_number, "blocks": len(page.blocks)},
            )

        breaks = _break_positions(text)
        mean_chars = max(1.0, len(text) / len(offsets))
        min_snap_chars = int(chunk_size_tokens * MIN_SNAP_RATIO * mean_chars)

        token_cursor = 0
        total_tokens = len(offsets)

        while token_cursor < total_tokens:
            start_char = offsets[token_cursor][0]
            hard_token_end = min(token_cursor + chunk_size_tokens, total_tokens)
            hard_end_char = offsets[hard_token_end - 1][1]

            if hard_token_end >= total_tokens:
                end_char = len(text)
                end_token = total_tokens
            else:
                end_char = _snap_end(
                    text=text,
                    breaks=breaks,
                    hard_end_char=hard_end_char,
                    start_char=start_char,
                    min_chars=min_snap_chars,
                )
                end_token = _token_index_for_char(offsets, end_char, token_cursor)
                if end_token <= token_cursor:
                    end_token = hard_token_end
                    end_char = hard_end_char

            content = text[start_char:end_char]
            token_count = end_token - token_cursor

            if content.strip() and token_count > 0:
                bbox, bbox_source = _bbox_for_span(
                    page, spans, span_status, start_char, end_char
                )
                candidates.append(
                    ChunkCandidate(
                        content=content,
                        page_number=page.page_number,
                        chunk_index=chunk_index,
                        page_start_char=start_char,
                        page_end_char=end_char,
                        token_count=token_count,
                        bbox=bbox,
                        bbox_source=bbox_source,
                    )
                )
                chunk_index += 1

            if end_token >= total_tokens:
                break

            advance = max(1, token_count - overlap_tokens)
            token_cursor += advance

    logger.info(
        "chunking.complete",
        extra={
            "pages": len(pages),
            "chunks": len(candidates),
            "boxed_chunks": sum(1 for c in candidates if c.bbox),
            "mean_tokens": (
                round(sum(c.token_count for c in candidates) / len(candidates), 1)
                if candidates
                else 0
            ),
        },
    )
    return candidates


def split_document(
    *,
    extraction_metadata: Optional[dict[str, Any]],
    extracted_text: Optional[str],
    tokenizer: Any,
    chunk_size_tokens: int = DEFAULT_CHUNK_SIZE_TOKENS,
    chunk_overlap_pct: int = DEFAULT_CHUNK_OVERLAP_PCT,
) -> list[ChunkCandidate]:
    """Convenience entry point used by document.enrich and knowledge.reindex."""
    from app.services.document_models import pages_from_work_item

    pages = pages_from_work_item(extraction_metadata, extracted_text)
    if not pages:
        return []
    return split_pages(
        pages,
        tokenizer=tokenizer,
        chunk_size_tokens=chunk_size_tokens,
        chunk_overlap_pct=chunk_overlap_pct,
    )


def split_text(
    pages: Sequence[DocumentPage],
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    tokenizer: Optional[Any] = None,
) -> list[ChunkCandidate]:
    """Legacy backward-compatible adapter for existing split_text call sites."""
    from app.services.embedding_service import embedding_service

    tok = tokenizer or embedding_service._get_model().tokenizer
    size = chunk_size if (chunk_size is not None and chunk_size < 300) else DEFAULT_CHUNK_SIZE_TOKENS
    overlap_pct = (
        round(100.0 * chunk_overlap / size)
        if (chunk_overlap is not None and size > 0 and chunk_overlap < size)
        else DEFAULT_CHUNK_OVERLAP_PCT
    )
    return split_pages(
        pages,
        tokenizer=tok,
        chunk_size_tokens=size,
        chunk_overlap_pct=overlap_pct,
    )


def chunking_summary(candidates: Sequence[ChunkCandidate]) -> dict[str, Any]:
    """Telemetry summary for pipeline events."""
    if not candidates:
        return {"chunks": 0}
    sources: dict[str, int] = {}
    for candidate in candidates:
        sources[candidate.bbox_source] = sources.get(candidate.bbox_source, 0) + 1
    tokens = [candidate.token_count for candidate in candidates]
    return {
        "chunks": len(candidates),
        "tokens_total": sum(tokens),
        "tokens_mean": round(sum(tokens) / len(tokens), 1),
        "tokens_max": max(tokens),
        "boxed_chunks": sum(1 for c in candidates if c.bbox),
        "bbox_sources": sources,
    }


__all__ = [
    "DEFAULT_CHUNK_OVERLAP_PCT",
    "DEFAULT_CHUNK_SIZE_TOKENS",
    "MIN_CHUNK_TOKENS",
    "ChunkingError",
    "chunking_summary",
    "split_document",
    "split_pages",
    "split_text",
]
