"""
OCR text extraction service for FlowPilot AI.

ARCH-07 §B.10 (Option C) — import-time decoupling.

Invariants this module must preserve:
1. Importing this module MUST NOT import paddleocr (or its paddle dependency tree).
2. Constructing OCRService() MUST NOT construct PaddleOCR. The engine is built
   on first genuine use.
3. is_available() MUST NOT import paddleocr and MUST NOT make a network call.
"""

from __future__ import annotations

import importlib.util
import logging
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional, Union

if TYPE_CHECKING:  # pragma: no cover
    from paddleocr import PaddleOCR

logger = logging.getLogger("app.services.ocr_service")

_PADDLEOCR_MODULE = "paddleocr"
_INIT_RETRY_COOLDOWN_SECONDS = 60.0
MIN_OCR_CONFIDENCE = 0.0


class OCRUnavailableError(RuntimeError):
    """Raised when OCR is requested but the engine cannot be initialised."""


class OCRService:
    """
    Lazily-initialised PaddleOCR wrapper.

    Construction is free. The first call to an extraction method pays the import
    and model-load cost, once, under a lock.
    """

    _instance: Optional["OCRService"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "OCRService":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized_wrapper = False
        return cls._instance

    def __init__(
        self,
        *,
        lang: str = "en",
        use_angle_cls: bool = True,
        show_log: bool = False,
    ) -> None:
        if getattr(self, "_initialized_wrapper", False):
            return

        self._lang = lang
        self._use_angle_cls = use_angle_cls
        self._show_log = show_log

        self._engine: Optional["PaddleOCR"] = None
        self._lock = threading.Lock()
        self._last_init_error: Optional[BaseException] = None
        self._last_init_attempt: float = 0.0
        self._initialized_wrapper = True

    # ------------------------------------------------------------------
    # Readiness probe & State
    # ------------------------------------------------------------------

    @staticmethod
    def is_available() -> bool:
        """Return True if the paddleocr distribution is importable without loading it."""
        try:
            return importlib.util.find_spec(_PADDLEOCR_MODULE) is not None
        except (ImportError, ValueError):
            return False

    @property
    def is_initialized(self) -> bool:
        """True once the underlying engine has been successfully constructed."""
        return self._engine is not None

    # ------------------------------------------------------------------
    # Lazy engine construction
    # ------------------------------------------------------------------

    def _get_engine(self) -> "PaddleOCR":
        """Return the engine, constructing it lazily on first use."""
        engine = self._engine
        if engine is not None:
            return engine

        with self._lock:
            if self._engine is not None:
                return self._engine

            if self._last_init_error is not None:
                elapsed = time.monotonic() - self._last_init_attempt
                if elapsed < _INIT_RETRY_COOLDOWN_SECONDS:
                    raise OCRUnavailableError(
                        "OCR engine initialisation failed "
                        f"{elapsed:.0f}s ago; retrying in "
                        f"{_INIT_RETRY_COOLDOWN_SECONDS - elapsed:.0f}s"
                    ) from self._last_init_error

            self._last_init_attempt = time.monotonic()
            try:
                from paddleocr import PaddleOCR  # noqa: PLC0415

                logger.info(
                    "Initialising PaddleOCR engine (lang=%s, use_angle_cls=%s)",
                    self._lang,
                    self._use_angle_cls,
                )
                started = time.monotonic()
                self._engine = PaddleOCR(
                    use_angle_cls=self._use_angle_cls,
                    lang=self._lang,
                    show_log=self._show_log,
                )
                logger.info(
                    "PaddleOCR engine ready in %.2fs", time.monotonic() - started
                )
                self._last_init_error = None
                return self._engine

            except ImportError as exc:
                self._last_init_error = exc
                logger.error("paddleocr is not installed: %s", exc)
                raise OCRUnavailableError(
                    "OCR is not available: the 'paddleocr' package is not "
                    "installed in this environment."
                ) from exc
            except Exception as exc:
                self._last_init_error = exc
                logger.exception("PaddleOCR engine initialisation failed")
                raise OCRUnavailableError(
                    f"OCR engine could not be initialised: {exc}"
                ) from exc

    def warmup(self) -> bool:
        """Eagerly construct the engine. Returns success rather than raising."""
        try:
            self._get_engine()
            return True
        except OCRUnavailableError:
            return False

    def reset(self) -> None:
        """Drop the cached engine and any cached failure state."""
        with self._lock:
            self._engine = None
            self._last_init_error = None
            self._last_init_attempt = 0.0

    # ------------------------------------------------------------------
    # Extraction API
    # ------------------------------------------------------------------

    def extract_text(self, image_path: Union[str, Path]) -> str:
        """Extract plain text from an image file on disk."""
        path = Path(image_path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: '{path}'.")
        if not path.is_file():
            raise FileNotFoundError(f"Path is not a file: '{path}'.")

        try:
            logger.info("Running OCR on %s", path)
            engine = self._get_engine()
            raw = engine.ocr(str(path), cls=self._use_angle_cls)
            return self._flatten(raw)
        except OCRUnavailableError:
            raise
        except Exception as exc:
            logger.exception("OCR failed for '%s'.", path)
            raise RuntimeError(f"Failed to extract text from '{path}'.") from exc

    def extract_text_from_bytes(
        self, data: bytes, *, suffix: str = ".png"
    ) -> str:
        """Extract plain text from in-memory image bytes using a temp file."""
        import tempfile

        if not data:
            return ""

        engine = self._get_engine()
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as handle:
            handle.write(data)
            handle.flush()
            raw = engine.ocr(handle.name, cls=self._use_angle_cls)
        return self._flatten(raw)

    # ------------------------------------------------------------------
    # Normalisation
    # ------------------------------------------------------------------

    @staticmethod
    def _flatten(raw: Any) -> str:
        if not raw:
            return ""

        lines: list[str] = []
        for page in raw:
            if not page:
                continue
            for entry in page:
                try:
                    text_val, confidence = entry[1]
                except (IndexError, TypeError):
                    continue
                if confidence >= MIN_OCR_CONFIDENCE:
                    text_str = str(text_val).strip()
                    if text_str:
                        lines.append(text_str)
        return "\n".join(lines)


# Global singleton instance
ocr_service = OCRService()