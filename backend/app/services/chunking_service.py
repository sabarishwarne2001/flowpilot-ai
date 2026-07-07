"""
Document text chunking and segmentation service for FlowPilot AI.

Segments extracted document text into overlapping chunks suitable for
embedding generation and semantic retrieval.
"""

import logging

from app.core.config import settings

logger = logging.getLogger("app.services.chunking_service")

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100


def split_text(
    text: str,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[str]:
    """
    Split text into overlapping chunks.

    Args:
        text: Raw extracted document text.
        chunk_size: Maximum characters per chunk.
        chunk_overlap: Number of overlapping characters.

    Returns:
        List of ordered text chunks.
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
        raise ValueError("CHUNK_SIZE must be greater than zero.")

    if chunk_overlap < 0:
        raise ValueError("CHUNK_OVERLAP cannot be negative.")

    if chunk_overlap >= chunk_size:
        raise ValueError(
            "CHUNK_OVERLAP must be smaller than CHUNK_SIZE."
        )

    text = text.strip()

    if not text:
        logger.warning("Received empty text for chunking.")
        return []

    if len(text) <= chunk_size:
        logger.info("Document fits into a single chunk.")
        return [text]

    logger.info(
        "Chunking document (%d characters) | Chunk Size=%d | Overlap=%d",
        len(text),
        chunk_size,
        chunk_overlap,
    )

    chunks: list[str] = []
    step = chunk_size - chunk_overlap

    for start in range(0, len(text), step):
        chunk = text[start:start + chunk_size].strip()

        if chunk:
            chunks.append(chunk)

    logger.info(
        "Generated %d text chunks.",
        len(chunks),
    )

    return chunks