"""
Document text extraction and format routing service for FlowPilot AI.

Extracts text from PDF and image documents. Electronic PDFs are parsed using
pypdf, while scanned PDFs automatically fall back to PaddleOCR.
"""

from pathlib import Path
import logging

from pypdf import PdfReader

from app.services.ocr_service import ocr_service

logger = logging.getLogger("app.services.extraction_service")

IMAGE_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
}


def _extract_from_pdf(pdf_path: Path) -> str:
    """
    Extract text from an electronic PDF.

    If no selectable text is found, automatically fall back to OCR.
    """
    logger.info("Extracting text from PDF: %s", pdf_path)

    try:
        reader = PdfReader(str(pdf_path))

        extracted_pages: list[str] = []

        for page in reader.pages:
            page_text = page.extract_text()

            if page_text:
                page_text = page_text.strip()

                if page_text:
                    extracted_pages.append(page_text)

        full_text = "\n".join(extracted_pages)

        if not full_text.strip():
            logger.warning(
                "No selectable text found in '%s'. Falling back to OCR.",
                pdf_path,
            )

            return ocr_service.extract_text(pdf_path)

        logger.info(
            "PDF extraction completed. Pages with text: %d | Characters: %d",
            len(extracted_pages),
            len(full_text),
        )

        return full_text

    except Exception:
        logger.exception("Failed while processing PDF '%s'.", pdf_path)
        raise RuntimeError(f"Failed to extract text from PDF: '{pdf_path}'.")


def extract_text_from_document(
    file_path: str | Path,
    mime_type: str,
) -> str:
    """
    Route document extraction based on MIME type.

    Args:
        file_path: Path to the document.
        mime_type: MIME type of the uploaded document.

    Returns:
        Extracted plain text.
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(
            f"Document not found: '{file_path}'."
        )

    if not file_path.is_file():
        raise FileNotFoundError(
            f"Path is not a file: '{file_path}'."
        )

    logger.info(
        "Starting extraction pipeline for '%s' (%s)",
        file_path,
        mime_type,
    )

    if mime_type == "application/pdf":
        return _extract_from_pdf(file_path)

    if mime_type in IMAGE_MIME_TYPES:
        return ocr_service.extract_text(file_path)

    logger.error("Unsupported MIME type: %s", mime_type)

    raise ValueError(
        f"Unsupported MIME type: '{mime_type}'."
    )