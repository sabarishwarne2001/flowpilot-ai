"""ARCH-10 Step 6 — the self-hosted PaddleOCR provider."""

from __future__ import annotations

import inspect
import logging
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

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

logger = logging.getLogger("app.services.ocr.paddle")

SUPPORTED_MIME_TYPES = frozenset(
    {
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/tiff",
        "image/webp",
        "image/bmp",
    }
)

MIN_TEXT_LAYER_CHARS = 24
RASTER_DPI = 200


def _paddleocr_installed() -> bool:
    import importlib.util

    try:
        return importlib.util.find_spec("paddleocr") is not None
    except (ImportError, ValueError):
        return False


class PaddleOCRProvider(OCRProvider):
    """Self-hosted PaddleOCR. Zero per-page provider cost, heavy image."""

    name = "paddleocr"
    cost_micros_per_page = 0

    def __init__(self, *, language: str = "en") -> None:
        self._language = language
        self._engine: Any = None
        self._predict_method: Optional[str] = None
        self._model_name: Optional[str] = None

    def is_available(self) -> bool:
        return _paddleocr_installed()

    def supports(self, mime_type: str) -> bool:
        return mime_type.split(";")[0].strip().lower() in SUPPORTED_MIME_TYPES

    def _build_engine(self) -> Any:
        if self._engine is not None:
            return self._engine

        try:
            from paddleocr import PaddleOCR  # noqa: PLC0415
        except ImportError as exc:
            raise OCRUnavailableError(
                "paddleocr is not installed in this process. This provider is "
                "only expected to run inside the OCR worker image."
            ) from exc

        try:
            parameters = set(inspect.signature(PaddleOCR.__init__).parameters)
        except (TypeError, ValueError):
            parameters = set()

        kwargs: dict[str, Any] = {"lang": self._language}
        if "use_textline_orientation" in parameters:
            kwargs["use_textline_orientation"] = True  # 3.x
        elif "use_angle_cls" in parameters:
            kwargs["use_angle_cls"] = True  # 2.x
        if "show_log" in parameters:
            kwargs["show_log"] = False  # removed in 3.x

        try:
            engine = PaddleOCR(**kwargs)
        except Exception as exc:
            raise OCRUnavailableError(
                f"PaddleOCR engine could not be constructed with {kwargs}: {exc}"
            ) from exc

        if hasattr(engine, "predict"):
            self._predict_method = "predict"
        elif hasattr(engine, "ocr"):
            self._predict_method = "ocr"
        else:
            raise OCRUnavailableError(
                "PaddleOCR instance exposes neither predict() nor ocr(); "
                "this build is not one this adapter understands."
            )

        try:
            from paddleocr import __version__ as paddle_version
            self._model_name = f"paddleocr-{paddle_version}"
        except Exception:
            self._model_name = "paddleocr"

        logger.info(
            "ocr.engine_ready",
            extra={
                "kwargs": sorted(kwargs),
                "method": self._predict_method,
                "model": self._model_name,
            },
        )
        self._engine = engine
        return engine

    def _run_engine(self, image_path: Path) -> Any:
        engine = self._build_engine()
        method = getattr(engine, self._predict_method or "ocr")
        try:
            if self._predict_method == "ocr":
                try:
                    return method(str(image_path), cls=True)
                except TypeError:
                    return method(str(image_path))
            return method(str(image_path))
        except Exception as exc:
            raise OCRError(f"OCR failed for {image_path.name}: {exc}") from exc

    @staticmethod
    def _blocks_from_raw(raw: Any) -> list[OCRBlock]:
        blocks: list[OCRBlock] = []
        if not raw:
            return blocks

        first = raw[0] if isinstance(raw, (list, tuple)) and raw else None
        payload = None
        if isinstance(first, dict):
            payload = first
        elif hasattr(first, "get") and not isinstance(first, (list, tuple)):
            payload = first
        elif hasattr(first, "json"):
            candidate = getattr(first, "json", None)
            if isinstance(candidate, dict):
                payload = candidate.get("res", candidate)

        if payload is not None:
            texts = payload.get("rec_texts") or payload.get("dt_texts") or []
            scores = payload.get("rec_scores") or []
            polygons = payload.get("rec_polys") or payload.get("dt_polys") or []
            for index, text in enumerate(texts):
                text_str = str(text).strip()
                if not text_str:
                    continue
                score = float(scores[index]) if index < len(scores) else 0.0
                polygon = (
                    [[float(p[0]), float(p[1])] for p in polygons[index]]
                    if index < len(polygons)
                    else None
                )
                blocks.append(
                    OCRBlock(
                        text=text_str,
                        confidence=score,
                        box=BoundingBox.from_polygon(polygon) if polygon else None,
                        polygon=polygon,
                    )
                )
            return blocks

        for page in raw:
            if not page:
                continue
            for entry in page:
                try:
                    polygon_raw, (text, score) = entry[0], entry[1]
                except (IndexError, TypeError, ValueError):
                    continue
                text_str = str(text).strip()
                if not text_str:
                    continue
                polygon = (
                    [[float(p[0]), float(p[1])] for p in polygon_raw]
                    if polygon_raw
                    else None
                )
                blocks.append(
                    OCRBlock(
                        text=text_str,
                        confidence=float(score),
                        box=BoundingBox.from_polygon(polygon) if polygon else None,
                        polygon=polygon,
                    )
                )
        return blocks

    def extract(
        self,
        path: Path,
        *,
        mime_type: str,
        language: str = "en",
        max_pages: Optional[int] = None,
    ) -> OCRResult:
        if not self.supports(mime_type):
            raise OCRUnsupportedError(
                f"{self.name} cannot process {mime_type!r}."
            )
        if not path.is_file():
            raise OCRUnsupportedError(f"No such file: {path}")

        started = time.monotonic()
        if mime_type == "application/pdf":
            pages = self._extract_pdf(path, max_pages=max_pages)
        else:
            pages = self._extract_image(path)

        return OCRResult(
            pages=pages,
            provider=self.name,
            model=self._model_name,
            duration_seconds=time.monotonic() - started,
        )

    def _extract_image(self, path: Path) -> list[OCRPage]:
        blocks = self._blocks_from_raw(self._run_engine(path))
        return [
            OCRPage(
                page_number=1,
                text="\n".join(b.text for b in blocks),
                blocks=blocks,
                ocr_applied=True,
            )
        ]

    def _extract_pdf(
        self, path: Path, *, max_pages: Optional[int] = None
    ) -> list[OCRPage]:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        total = len(reader.pages)
        limit = min(total, max_pages) if max_pages else total

        pages: list[OCRPage] = []
        needs_ocr: list[int] = []

        for index in range(limit):
            try:
                layer = (reader.pages[index].extract_text() or "").strip()
            except Exception:
                layer = ""
            if len(layer) >= MIN_TEXT_LAYER_CHARS:
                pages.append(
                    OCRPage(
                        page_number=index + 1,
                        text=layer,
                        blocks=[],
                        ocr_applied=False,
                    )
                )
            else:
                pages.append(
                    OCRPage(
                        page_number=index + 1, text="", blocks=[], ocr_applied=True
                    )
                )
                needs_ocr.append(index)

        if needs_ocr:
            self._ocr_pdf_pages(path, pages, needs_ocr)

        logger.info(
            "ocr.pdf_extracted",
            extra={
                "pages": len(pages),
                "text_layer_pages": len(pages) - len(needs_ocr),
                "ocr_pages": len(needs_ocr),
            },
        )
        return pages

    def _ocr_pdf_pages(
        self, path: Path, pages: list[OCRPage], indices: list[int]
    ) -> None:
        try:
            import pypdfium2 as pdfium  # noqa: PLC0415
        except ImportError as exc:
            raise OCRUnavailableError(
                "pypdfium2 is required to rasterise scanned PDF pages. Add it "
                "to the OCR worker image (it is a self-contained wheel)."
            ) from exc

        document = pdfium.PdfDocument(str(path))
        try:
            scale = RASTER_DPI / 72.0
            with tempfile.TemporaryDirectory(prefix="fp-ocr-") as workdir:
                for index in indices:
                    page = document[index]
                    bitmap = page.render(scale=scale)
                    image = bitmap.to_pil()
                    frame = Path(workdir) / f"page-{index + 1:05d}.png"
                    image.save(frame, format="PNG")

                    blocks = self._blocks_from_raw(self._run_engine(frame))
                    target = pages[index]
                    target.text = "\n".join(b.text for b in blocks)
                    target.blocks = blocks
                    target.ocr_applied = True
                    target.width, target.height = image.size
        finally:
            close = getattr(document, "close", None)
            if callable(close):
                close()


def get_provider(*, language: str = "en") -> OCRProvider:
    from app.core.config import settings

    configured = (settings.OCR_PROVIDER or "paddleocr").strip().lower()
    if configured != "paddleocr":
        raise OCRUnavailableError(
            f"OCR_PROVIDER={configured!r} has no registered implementation. "
            "Only 'paddleocr' is available."
        )
    return PaddleOCRProvider(language=language or settings.OCR_LANGUAGE)


def _probe() -> int:
    provider = PaddleOCRProvider()
    if not provider.is_available():
        print("paddleocr: NOT INSTALLED")
        return 1
    try:
        provider._build_engine()
    except OCRUnavailableError as exc:
        print(f"paddleocr: INSTALLED but engine construction FAILED\n  {exc}")
        return 1
    print(
        f"paddleocr: OK\n"
        f"  model  : {provider._model_name}\n"
        f"  method : {provider._predict_method}()"
    )
    return 0


if __name__ == "__main__":
    import sys

    raise SystemExit(_probe())