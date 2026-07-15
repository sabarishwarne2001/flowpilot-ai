"""
Document text chunking and segmentation service for FlowPilot AI.

Segments extracted document text into overlapping chunks suitable for
embedding generation and semantic retrieval.
"""

import logging

from app.core.config import settings

from app.services.document_models import (
    DocumentChunk,
    DocumentPage,
)

logger = logging.getLogger("app.services.chunking_service")

DEFAULT_CHUNK_SIZE = 800
DEFAULT_CHUNK_OVERLAP = 100


def split_text(
    pages: list[DocumentPage],
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> list[DocumentChunk]:
    """
    Split extracted document pages into overlapping semantic chunks.

    Each generated chunk preserves its originating page number,
    enabling page-aware citations throughout the RAG pipeline.
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

    if not pages:

        logger.warning(
            "Received empty page collection for chunking."
        )

        return []


    total_characters = sum(
        len(page.text)
        for page in pages
    )

    logger.info(
        "Chunking %d page(s) (%d characters).",
        len(pages),
        total_characters,
    )

    chunks: list[DocumentChunk] = []

    step = chunk_size - chunk_overlap

    chunk_index = 0


    for page in pages:

        text = page.text.strip()

        if not text:
            continue

        if len(text) <= chunk_size:

            chunks.append(
                DocumentChunk(
                    text=text,
                    page_number=page.page_number,
                    chunk_index=chunk_index,
                )
            )

            chunk_index += 1

            continue

        for start in range(
            0,
            len(text),
            step,
        ):

            chunk = text[
                start:start + chunk_size
            ].strip()

            if not chunk:
                continue

            chunks.append(
                DocumentChunk(
                    text=chunk,
                    page_number=page.page_number,
                    chunk_index=chunk_index,
                )
            )

            chunk_index += 1


    logger.info(
        "Generated %d page-aware chunks.",
        len(chunks),
    )

    return chunks