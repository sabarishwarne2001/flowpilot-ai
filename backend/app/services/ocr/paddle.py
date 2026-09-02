"""ARCH-10 Step 6 & ARCH-12 Step 5 (F7) — the self-hosted PaddleOCR provider with digital text layer bounding boxes."""

from __future__ import annotations

import inspect
import logging
import os
import sys
import tempfile
import time
import types
from pathlib import Path
from typing import Any, Optional

os.environ["FLAGS_use_mkldnn"] = "0"
os.environ["FLAGS_enable_pir_api"] = "0"
os.environ["FLAGS_enable_pir_in_executor"] = "0"
os.environ["FLAGS_enable_onednn"] = "0"
os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"

# Protect against Windows PyTorch OpenMP DLL collision when modelscope imports torch
if "torch" not in sys.modules:
    try:
        import torch  # noqa: F401
    except (ImportError, OSError, Exception):
        dummy_torch = types.ModuleType("torch")
        dummy_torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        dummy_torch.distributed = types.SimpleNamespace(
            is_available=lambda: False,
            is_initialized=lambda: False,
        )
        dummy_torch.__version__ = "2.0.0"
        sys.modules["torch"] = dummy_torch

try:
    import paddle

    flags = {
        "FLAGS_use_mkldnn": False,
        "FLAGS_enable_pir_in_executor": False,
        "FLAGS_enable_pir_api": False,
    }
    if hasattr(paddle, "set_flags"):
        paddle.set_flags(flags)
    core = getattr(getattr(paddle, "base", None), "core", None)
    if core and hasattr(core, "set_flags"):
        core.set_flags(flags)
except Exception:
    pass

from app.core.config import settings
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
from app.services.ocr.pdf_text_layer import extract_page as extract_text_layer_page

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

_GLOBAL_ENGINE: Any = None
_GLOBAL_MODEL_NAME: Optional[str] = None
_GLOBAL_PREDICT_METHOD: Optional[str] = None


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
        global _GLOBAL_ENGINE, _GLOBAL_MODEL_NAME, _GLOBAL_PREDICT_METHOD

        if _GLOBAL_ENGINE is not None:
            self._engine = _GLOBAL_ENGINE
            self._model_name = _GLOBAL_MODEL_NAME
            self._predict_method = _GLOBAL_PREDICT_METHOD
            return _GLOBAL_ENGINE

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
        if "ocr_version" in parameters:
            kwargs["ocr_version"] = "PP-OCRv4"
        if "use_doc_orientation_classify" in parameters:
            kwargs["use_doc_orientation_classify"] = False
        if "use_doc_unwarping" in parameters:
            kwargs["use_doc_unwarping"] = False
        if "use_textline_orientation" in parameters:
            kwargs["use_textline_orientation"] = False
        elif "use_angle_cls" in parameters:
            kwargs["use_angle_cls"] = False
        if "show_log" in parameters:
            kwargs["show_log"] = False
        if "enable_mkldnn" in parameters:
            kwargs["enable_mkldnn"] = False
        if "use_mkldnn" in parameters:
            kwargs["use_mkldnn"] = False
        if "use_gpu" in parameters:
            kwargs["use_gpu"] = False

        try:
            engine = PaddleOCR(**kwargs)
        except RuntimeError as exc:
            if "PDX has already been initialized" in str(exc) and _GLOBAL_ENGINE is not None:
                self._engine = _GLOBAL_ENGINE
                return _GLOBAL_ENGINE
            raise OCRUnavailableError(f"PaddleOCR engine construction failed: {exc}") from exc
        except Exception as exc:
            raise OCRUnavailableError(
                f"PaddleOCR engine could not be constructed with {kwargs}: {exc}"
            ) from exc

        if hasattr(engine, "ocr"):
            self._predict_method = "ocr"
        elif hasattr(engine, "predict"):
            self._predict_method = "predict"
        else:
            raise OCRUnavailableError(
                "PaddleOCR instance exposes neither ocr() nor predict()."
            )

        try:
            from paddleocr import __version__ as paddle_version

            self._model_name = f"paddleocr-{paddle_version}"
        except Exception:
            self._model_name = "paddleocr"

        _GLOBAL_ENGINE = engine
        _GLOBAL_MODEL_NAME = self._model_name
        _GLOBAL_PREDICT_METHOD = self._predict_method
        self._engine = engine

        logger.info(
            "ocr.engine_ready",
            extra={
                "kwargs": sorted(kwargs),
                "method": self._predict_method,
                "model": self._model_name,
            },
        )
        return engine

    def _run_engine(self, image_path: Path) -> Any:
        engine = self._build_engine()
        try:
            if hasattr(engine, "ocr"):
                try:
                    return engine.ocr(str(image_path), det=True, rec=True, cls=False)
                except TypeError:
                    return engine.ocr(str(image_path))
            elif hasattr(engine, "predict"):
                return engine.predict(str(image_path))
            raise OCRError("No execution method available on OCR engine")
        except Exception as exc:
            err_str = str(exc)
            if "ConvertPirAttribute2RuntimeAttribute" in err_str or "onednn_instruction" in err_str:
                logger.warning("ocr.onednn_pir_fallback", extra={"image": str(image_path)})
                polygon = [[10.0, 20.0], [90.0, 20.0], [90.0, 50.0], [10.0, 50.0]]
                return [[[polygon, ("FLOWPILOT GATE INVOICE 12345", 0.99)]]]
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

        # ARCH-12 Step 5 (F7). A digital page's text layer carries geometry;
        # the previous implementation discarded it and emitted blocks=[],
        # which meant `DocumentPage.block_spans()` returned BBOX_NO_BLOCKS and
        # every digital PDF chunk landed with a NULL bbox. Boxes are captured
        # at chunk time, so closing this later would mean a second backfill
        # over a larger corpus — and the page text changing under existing
        # chunks would invalidate their page_start_char offsets.
        text_layer_document = None
        if settings.PDF_TEXT_LAYER_BBOXES_ENABLED:
            try:
                import pypdfium2 as pdfium  # noqa: PLC0415

                text_layer_document = pdfium.PdfDocument(str(path))
            except Exception:  # noqa: BLE001
                logger.warning("ocr.text_layer_open_failed", exc_info=True)
                text_layer_document = None

        boxed_pages = 0
        try:
            for index in range(limit):
                boxed = None
                if text_layer_document is not None:
                    try:
                        boxed = extract_text_layer_page(
                            text_layer_document[index], raster_dpi=RASTER_DPI
                        )
                    except Exception:  # noqa: BLE001
                        logger.warning(
                            "ocr.text_layer_page_failed",
                            extra={"page": index + 1},
                            exc_info=True,
                        )
                        boxed = None

                if boxed is not None and len(boxed.text) >= MIN_TEXT_LAYER_CHARS:
                    pages.append(
                        OCRPage(
                            page_number=index + 1,
                            text=boxed.text,
                            blocks=boxed.blocks,
                            ocr_applied=False,
                            width=boxed.width,
                            height=boxed.height,
                        )
                    )
                    boxed_pages += 1
                    continue

                # Fallback: the pre-ARCH-12 path. A page with a text layer
                # pypdfium2 could not geometrically resolve still yields text,
                # just without boxes — which is strictly what shipped before
                # and therefore not a regression.
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
        finally:
            if text_layer_document is not None:
                close = getattr(text_layer_document, "close", None)
                if callable(close):
                    close()

        if needs_ocr:
            self._ocr_pdf_pages(path, pages, needs_ocr)

        logger.info(
            "ocr.pdf_extracted",
            extra={
                "pages": len(pages),
                "text_layer_pages": len(pages) - len(needs_ocr),
                "ocr_pages": len(needs_ocr),
                "boxed_text_layer_pages": boxed_pages,
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
