"""
Optical Character Recognition (OCR) service for FlowPilot AI.

Utilizes PaddleOCR to extract plain text from image files while maintaining
a singleton OCR engine instance for efficient memory usage.
"""

from pathlib import Path
import logging

from paddleocr import PaddleOCR

from app.core.config import settings

logger = logging.getLogger("app.services.ocr_service")

# Minimum confidence reserved for future filtering logic
MIN_OCR_CONFIDENCE = 0.0


class OCRService:
    """
    Singleton wrapper around the PaddleOCR engine.

    The OCR model is loaded only once during the application's lifetime.
    """

    _instance: "OCRService | None" = None

    def __new__(cls) -> "OCRService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return

        try:
            logger.info("Loading PaddleOCR engine...")

            self.engine = PaddleOCR(
                use_angle_cls=True,
                lang=settings.OCR_LANGUAGE,
            )

            self._initialized = True

            logger.info("PaddleOCR initialized successfully.")

        except Exception:
            logger.exception("Failed to initialize PaddleOCR.")
            raise

    def extract_text(self, image_path: str | Path) -> str:
        """
        Extract text from an image file.

        Args:
            image_path: Path to an image.

        Returns:
            Extracted plain text.
        """
        image_path = Path(image_path)

        if not image_path.exists():
            raise FileNotFoundError(
                f"Image file not found: '{image_path}'."
            )

        if not image_path.is_file():
            raise FileNotFoundError(
                f"Path is not a file: '{image_path}'."
            )

        try:
            logger.info("Running OCR on %s", image_path)

            results = self.engine.ocr(str(image_path), cls=True)

            if not results or not results[0]:
                logger.warning(
                    "No text detected in image '%s'.",
                    image_path,
                )
                return ""

            extracted_lines: list[str] = []

            for block in results[0]:
                text, confidence = block[1]

                if confidence >= MIN_OCR_CONFIDENCE:
                    text = text.strip()
                    if text:
                        extracted_lines.append(text)

            plain_text = "\n".join(extracted_lines)

            logger.info(
                "OCR completed successfully. Extracted %d text lines.",
                len(extracted_lines),
            )

            return plain_text

        except Exception:
            logger.exception(
                "OCR failed for '%s'.",
                image_path,
            )
            raise RuntimeError(
                f"Failed to extract text from '{image_path}'."
            )


# Global singleton instance
ocr_service = OCRService()