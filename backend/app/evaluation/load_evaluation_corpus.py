"""
Load the retrieval evaluation corpus into a dedicated ChromaDB collection.

This script reuses FlowPilot AI's production extraction, chunking,
and embedding pipeline while keeping evaluation data completely
isolated from production data.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path

from app.services.embedding_service import embedding_service
from app.services.extraction_service import (
    extract_text_from_document,
)
from app.services.chunking_service import (
    split_text,
)

logger = logging.getLogger(
    "app.evaluation.load_evaluation_corpus"
)

EVALUATION_COLLECTION_NAME = "flowpilot_evaluation"

CORPUS_DIRECTORY = (
    Path(__file__)
    .resolve()
    .parents[2]
    / "evaluation"
    / "corpus"
)

def get_mime_type(
    file_path: Path,
) -> str:
    """
    Determine the MIME type for a corpus document.
    """

    mime_type, _ = mimetypes.guess_type(
        file_path,
    )

    if mime_type is None:
        raise ValueError(
            f"Unable to determine MIME type for '{file_path.name}'."
        )

    return mime_type

def load_evaluation_corpus() -> None:
    """
    Load every evaluation document into the dedicated
    evaluation ChromaDB collection.
    """

    logger.info(
        "Loading evaluation corpus from '%s'.",
        CORPUS_DIRECTORY,
    )

    logger.info(
        "Clearing evaluation collection.",
    )

    deleted = embedding_service.clear_collection(
        collection_name=EVALUATION_COLLECTION_NAME,
    )

    logger.info(
        "Removed %d existing evaluation vector(s).",
        deleted,
    )

    if not CORPUS_DIRECTORY.exists():
        raise FileNotFoundError(
            f"Corpus directory not found: {CORPUS_DIRECTORY}"
        )

    pdf_files = sorted(
        CORPUS_DIRECTORY.glob("*.pdf")
    )

    if not pdf_files:

        logger.warning(
            "No PDF files found in '%s'.",
            CORPUS_DIRECTORY,
        )

        return

    logger.info(
        "Found %d evaluation document(s).",
        len(pdf_files),
    )

    for pdf_file in pdf_files:

        try:

            logger.info(
                "Loading '%s'.",
                pdf_file.name,
            )

            mime_type = get_mime_type(
                pdf_file,
            )

            pages = extract_text_from_document(
                file_path=pdf_file,
                mime_type=mime_type,
            )

            chunks = split_text(
                pages,
            )

            if not chunks:

                logger.warning(
                    "Skipping '%s' because no chunks were produced.",
                    pdf_file.name,
                )

                continue

            embeddings = embedding_service.generate_embeddings(
                [
                    chunk.text
                    for chunk in chunks
                ]
            )

            work_item_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                pdf_file.name,
            )

            embedding_service.store_chunks(
                work_item_id=work_item_id,
                original_filename=pdf_file.name,
                chunks=chunks,
                embeddings=embeddings,
                collection_name=EVALUATION_COLLECTION_NAME,
            )

            logger.info(
                "Loaded '%s' (%d chunks).",
                pdf_file.name,
                len(chunks),
            )

        except Exception:

            logger.exception(
                "Failed to process '%s'.",
                pdf_file.name,
            )

if __name__ == "__main__":

    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | "
            "%(levelname)s | "
            "%(name)s | "
            "%(message)s"
        ),
    )

    try:

        load_evaluation_corpus()

        logger.info(
            "Evaluation corpus loaded successfully."
        )

    except Exception:

        logger.exception(
            "Failed to load evaluation corpus."
        )

        raise

