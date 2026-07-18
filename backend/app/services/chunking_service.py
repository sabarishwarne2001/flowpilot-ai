"""
Production semantic chunking service.

This service transforms extracted document pages into coherent,
page-aware chunks suitable for embedding generation.

Design goals:

- Preserve paragraph boundaries
- Preserve page numbers
- Avoid sentence truncation whenever possible
- Support configurable overlap
- Produce semantically meaningful chunks
"""

from __future__ import annotations

import logging
import re

from app.core.config import settings
from app.services.document_models import (
    DocumentChunk,
    DocumentPage,
)

logger = logging.getLogger(
    "app.services.chunking_service"
)

DEFAULT_CHUNK_SIZE = 750
DEFAULT_CHUNK_OVERLAP = 150


def _normalize_text(
    text: str,
) -> str:
    """
    Normalize extracted document text.

    - Remove excessive whitespace
    - Normalize newlines
    - Remove duplicate blank lines
    """

    text = text.replace("\r\n", "\n")

    text = re.sub(
        r"[ \t]+",
        " ",
        text,
    )

    text = re.sub(
        r"\n{3,}",
        "\n\n",
        text,
    )

    return text.strip()


def _extract_paragraphs(
    text: str,
) -> list[str]:
    """
    Split text into logical paragraphs.

    Empty paragraphs are discarded.
    """

    paragraphs = [

        paragraph.strip()

        for paragraph in re.split(
            r"\n\s*\n",
            text,
        )

        if paragraph.strip()

    ]

    return paragraphs


def _split_large_paragraph(
    paragraph: str,
    chunk_size: int,
) -> list[str]:
    """
    Split only paragraphs that exceed the configured limit.

    Sentence boundaries are preferred.
    """

    if len(paragraph) <= chunk_size:
        return [paragraph]

    sentences = re.split(

        r"(?<=[.!?])\s+",

        paragraph,

    )

    chunks: list[str] = []

    current = ""

    for sentence in sentences:

        sentence = sentence.strip()

        if not sentence:
            continue

        if (
            len(current)
            + len(sentence)
            + 1
            <= chunk_size
        ):

            if current:

                current += " "

            current += sentence

        else:

            if current:

                chunks.append(
                    current
                )

            current = sentence

    if current:

        chunks.append(
            current
        )

    return chunks


def _merge_small_paragraphs(
    paragraphs: list[str],
    minimum_size: int = 120,
) -> list[str]:
    """
    Merge very small paragraphs into neighboring paragraphs.

    Prevents tiny headings, short bullet points,
    and isolated labels from becoming standalone chunks.
    """

    if not paragraphs:
        return []

    merged: list[str] = []

    buffer = ""

    for paragraph in paragraphs:

        paragraph = paragraph.strip()

        if not paragraph:
            continue

        #
        # Accumulate very small paragraphs.
        #
        if len(paragraph) < minimum_size:

            if buffer:

                buffer += "\n\n"

            buffer += paragraph

            continue

        #
        # Flush buffered small paragraphs.
        #
        if buffer:

            paragraph = buffer + "\n\n" + paragraph

            buffer = ""

        merged.append(
            paragraph
        )

    #
    # Remaining buffer.
    #
    if buffer:

        if merged:

            merged[-1] += "\n\n" + buffer

        else:

            merged.append(
                buffer
            )

    return merged


def _build_semantic_chunks(
    paragraphs: list[str],
    *,
    chunk_size: int,
    chunk_overlap: int,
) -> list[str]:
    """
    Build semantic chunks while preserving paragraph boundaries.

    Paragraphs remain intact whenever possible.

    Only paragraphs larger than chunk_size are
    sentence-split.
    """

    chunks: list[str] = []

    current_chunk: list[str] = []

    current_size = 0

    for paragraph in paragraphs:

        #
        # Large paragraph.
        #
        if len(paragraph) > chunk_size:

            #
            # Flush existing chunk.
            #
            if current_chunk:

                chunks.append(
                    "\n\n".join(
                        current_chunk
                    )
                )

                current_chunk = []

                current_size = 0

            #
            # Sentence-aware split.
            #
            chunks.extend(

                _split_large_paragraph(
                    paragraph,
                    chunk_size,
                )

            )

            continue

        #
        # Fits current chunk.
        #
        if (
            current_size
            + len(paragraph)
            + 2
            <= chunk_size
        ):

            current_chunk.append(
                paragraph
            )

            current_size += (
                len(paragraph)
                + 2
            )

            continue

        #
        # Flush current chunk.
        #
        chunks.append(
            "\n\n".join(
                current_chunk
            )
        )

        #
        # Controlled overlap.
        #
        overlap_paragraphs: list[str] = []

        if (
            chunk_overlap > 0
            and current_chunk
        ):

            overlap_length = 0

            for previous in reversed(current_chunk):

                overlap_paragraphs.insert(
                    0,
                    previous,
                )

                overlap_length += (
                    len(previous)
                    + 2
                )

                if overlap_length >= chunk_overlap:
                    break

        current_chunk = overlap_paragraphs.copy()

        current_size = sum(
            len(item) + 2
            for item in current_chunk
        )

        current_chunk.append(
            paragraph
        )

        current_size += (
            len(paragraph)
            + 2
        )

    #
    # Final chunk.
    #
    if current_chunk:

        chunks.append(
            "\n\n".join(
                current_chunk
            )
        )

    return chunks


def split_text(
    pages: list[DocumentPage],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[DocumentChunk]:
    """
    Convert extracted pages into semantic chunks.

    Pipeline

    Page
        ↓
    Normalize text
        ↓
    Paragraph extraction
        ↓
    Merge tiny paragraphs
        ↓
    Semantic chunk builder
        ↓
    DocumentChunk objects
    """

    chunk_size = chunk_size or getattr(
        settings,
        "CHUNK_SIZE",
        DEFAULT_CHUNK_SIZE,
    )

    chunk_overlap = chunk_overlap or getattr(
        settings,
        "CHUNK_OVERLAP",
        DEFAULT_CHUNK_OVERLAP,
    )

    if chunk_size <= 0:
        raise ValueError(
            "CHUNK_SIZE must be greater than zero."
        )

    if chunk_overlap < 0:
        raise ValueError(
            "CHUNK_OVERLAP cannot be negative."
        )

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "CHUNK_OVERLAP must be smaller than CHUNK_SIZE."
        )

    if not pages:

        logger.warning(
            "Received empty page collection."
        )

        return []

    total_characters = sum(
        len(page.text)
        for page in pages
    )

    logger.info(
        "Semantic chunking %d page(s) (%d characters).",
        len(pages),
        total_characters,
    )

    chunks: list[DocumentChunk] = []

    chunk_index = 0

    for page in pages:

        normalized_text = _normalize_text(
            page.text
        )

        if not normalized_text:
            continue

        paragraphs = _extract_paragraphs(
            normalized_text
        )

        paragraphs = _merge_small_paragraphs(
            paragraphs
        )

        semantic_chunks = _build_semantic_chunks(
            paragraphs,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

        for chunk in semantic_chunks:

            chunks.append(

                DocumentChunk(

                    text=chunk,

                    page_number=page.page_number,

                    chunk_index=chunk_index,

                )

            )

            chunk_index += 1

    logger.info(
        "Generated %d semantic chunks.",
        len(chunks),
    )

    return chunks