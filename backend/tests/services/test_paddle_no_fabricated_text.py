"""F-019 regression: OCR must fail honestly, never invent document text.

``PaddleOCRProvider._run_engine`` used to catch one Paddle/CPU error class
(``ConvertPirAttribute2RuntimeAttribute`` / ``onednn_instruction``) and return a
made-up result containing the text "FLOWPILOT GATE INVOICE 12345". That text was
then extracted, embedded and shown as the customer's document content.

An engine crash must surface as an ``OCRError`` so the job fails visibly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.ocr.base import OCRError
from app.services.ocr.paddle import PaddleOCRProvider

FAKE_TEXT = "FLOWPILOT GATE INVOICE 12345"

ONEDNN_ERRORS = [
    "(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support "
    "[pir::ArrayAttribute<pir::DoubleAttribute>]",
    "onednn_instruction.cc:118: unsupported attribute",
]


class _CrashingOcrEngine:
    def __init__(self, message: str) -> None:
        self._message = message

    def ocr(self, *_args, **_kwargs):
        raise RuntimeError(self._message)


class _CrashingPredictEngine:
    def __init__(self, message: str) -> None:
        self._message = message

    def predict(self, *_args, **_kwargs):
        raise RuntimeError(self._message)


def _provider_with(monkeypatch, engine) -> PaddleOCRProvider:
    provider = PaddleOCRProvider()
    monkeypatch.setattr(provider, "_build_engine", lambda: engine)
    return provider


@pytest.mark.no_db
@pytest.mark.parametrize("message", ONEDNN_ERRORS)
@pytest.mark.parametrize("engine_cls", [_CrashingOcrEngine, _CrashingPredictEngine])
def test_engine_crash_raises_instead_of_returning_invented_text(
    monkeypatch, tmp_path: Path, message: str, engine_cls
):
    provider = _provider_with(monkeypatch, engine_cls(message))
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(OCRError) as excinfo:
        provider._run_engine(image)

    assert "scan.png" in str(excinfo.value)
    assert FAKE_TEXT not in str(excinfo.value)


@pytest.mark.no_db
@pytest.mark.parametrize("message", ONEDNN_ERRORS)
def test_extract_fails_the_document_and_never_yields_the_fake_page(
    monkeypatch, tmp_path: Path, message: str
):
    provider = _provider_with(monkeypatch, _CrashingOcrEngine(message))
    image = tmp_path / "scan.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(OCRError):
        provider.extract(image, mime_type="image/png")


@pytest.mark.no_db
def test_fabricated_text_is_gone_from_the_provider_source():
    import app.services.ocr.paddle as paddle_module

    assert FAKE_TEXT not in Path(paddle_module.__file__).read_text(encoding="utf-8")
