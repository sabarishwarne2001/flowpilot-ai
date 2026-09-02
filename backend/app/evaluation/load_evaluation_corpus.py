"""
Load the retrieval evaluation corpus directly into the database.
"""

from __future__ import annotations

import logging
import mimetypes
import uuid
from pathlib import Path

from app.db.session import SessionLocal
from app.services.chunk_writer import replace_document_chunks
from app.services.chunking_service import split_pages
from app.services.document_models import DocumentPage
from app.services.embedding_service import embedding_service
from app.services.extraction_service import extract_text_from_document

logger = logging.getLogger("app.evaluation.load_evaluation_corpus")

CORPUS_DIRECTORY = (
    Path(__file__)
    .resolve()
    .parents[2]
    / "evaluation"
    / "corpus"
)


def get_mime_type(file_path: Path) -> str:
    mime_type, _ = mimetypes.guess_type(file_path)
    if mime_type is None:
        raise ValueError(f"Unable to determine MIME type for '{file_path.name}'.")
    return mime_type


def load_evaluation_corpus(workspace_id: uuid.UUID, organization_id: uuid.UUID) -> None:
    if not CORPUS_DIRECTORY.exists():
        logger.warning("Corpus directory not found: %s", CORPUS_DIRECTORY)
        return

    pdf_files = sorted(CORPUS_DIRECTORY.glob("*.pdf"))
    if not pdf_files:
        logger.warning("No PDF files found in '%s'.", CORPUS_DIRECTORY)
        return

    model = embedding_service._get_model()
    with SessionLocal() as db:
        for pdf_file in pdf_files:
            try:
                mime_type = get_mime_type(pdf_file)
                pages_data = extract_text_from_document(
                    file_path=pdf_file,
                    mime_type=mime_type,
                )
                pages = [
                    DocumentPage(page_number=p.page_number, text=p.text)
                    for p in pages_data
                ]
                candidates = split_pages(pages, tokenizer=model.tokenizer)
                if not candidates:
                    continue

                embeddings = embedding_service.generate_embeddings(
                    [c.content for c in candidates]
                )
                work_item_id = uuid.uuid5(uuid.NAMESPACE_URL, pdf_file.name)

                replace_document_chunks(
                    db,
                    workspace_id=workspace_id,
                    organization_id=organization_id,
                    work_item_id=work_item_id,
                    uploaded_file_id=None,
                    candidates=candidates,
                    embeddings=embeddings,
                )
                db.commit()
                logger.info("Loaded '%s' (%d chunks).", pdf_file.name, len(candidates))
            except Exception:
                db.rollback()
                logger.exception("Failed to process '%s'.", pdf_file.name)


__all__ = ["load_evaluation_corpus"]
