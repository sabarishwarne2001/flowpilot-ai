"""OCR provider abstraction and self-hosted engine adapters."""

from app.services.ocr.base import (
    BoundingBox,
    OCRBlock,
    OCRError,
    OCRPage,
    OCRProvider,
    OCRResult,
    OCRUnavailableError,
    OCRUnsupportedError,
)
from app.services.ocr.paddle import PaddleOCRProvider, get_provider

__all__ = [
    "BoundingBox",
    "OCRBlock",
    "OCRError",
    "OCRPage",
    "OCRProvider",
    "OCRResult",
    "OCRUnavailableError",
    "OCRUnsupportedError",
    "PaddleOCRProvider",
    "get_provider",
]