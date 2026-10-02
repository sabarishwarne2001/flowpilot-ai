"""F-019 — the Paddle provider must fail honestly, never invent document text.

    pytest tests/services/test_paddle_ocr_honest_failure.py -q

When the PaddleOCR engine raised an error mentioning
``ConvertPirAttribute2RuntimeAttribute`` or ``onednn_instruction`` (a known
Paddle-on-CPU crash), ``PaddleOCRProvider._run_engine`` swallowed it and
returned a made-up result: the text "FLOWPILOT GATE INVOICE 12345" at
confidence 0.99. That text was then extracted, embedded and shown as the
customer's own document. A crash has to become a failed job, so the user knows
the document was NOT read.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image

from app.services.ocr.base import OCRError
from app.services.ocr.paddle import PaddleOCRProvider

FAKE_TEXT = "FLOWPILOT GATE INVOICE 12345"

# The real messages Paddle raises on the affected machines.
PIR_ERROR = (
    "(Unimplemented) ConvertPirAttribute2RuntimeAttribute not support "
    "[pir::ArrayAttribute<pir::DoubleAttribute>] "
    "(at ../paddle/fluid/framework/new_executor/instruction/onednn/onednn_instruction.cc:118)"
)
ONEDNN_ERROR = "onednn_instruction.cc: could not create a primitive descriptor"


class _CrashingEngine:
    def __init__(self, message: str) -> None:
        self._message = message

    def ocr(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError(self._message)


@pytest.fixture()
def png(tmp_path: Path) -> Path:
    path = tmp_path / "scan.png"
    Image.new("RGB", (120, 60), "white").save(path)
    return path


def _provider(monkeypatch: pytest.MonkeyPatch, message: str) -> PaddleOCRProvider:
    provider = PaddleOCRProvider()
    monkeypatch.setattr(provider, "_build_engine", lambda: _CrashingEngine(message))
    return provider


@pytest.mark.no_db
@pytest.mark.parametrize("message", [PIR_ERROR, ONEDNN_ERROR])
def test_an_onednn_crash_raises_instead_of_returning_invented_text(
    monkeypatch: pytest.MonkeyPatch, png: Path, message: str
) -> None:
    provider = _provider(monkeypatch, message)

    with pytest.raises(OCRError) as failure:
        provider._run_engine(png)

    assert FAKE_TEXT not in str(failure.value)
    assert "scan.png" in str(failure.value)


@pytest.mark.no_db
@pytest.mark.parametrize("message", [PIR_ERROR, ONEDNN_ERROR])
def test_extract_fails_the_document_and_yields_no_blocks(
    monkeypatch: pytest.MonkeyPatch, png: Path, message: str
) -> None:
    provider = _provider(monkeypatch, message)

    with pytest.raises(OCRError):
        provider.extract(png, mime_type="image/png")


@pytest.mark.no_db
def test_any_other_engine_error_still_fails_the_same_way(
    monkeypatch: pytest.MonkeyPatch, png: Path
) -> None:
    provider = _provider(monkeypatch, "some other engine failure")

    with pytest.raises(OCRError, match="some other engine failure"):
        provider._run_engine(png)


@pytest.mark.no_db
def test_the_invented_text_is_gone_from_the_source() -> None:
    """A tripwire: this string existing at all means a shim is back."""
    source = Path(__import__("app.services.ocr.paddle", fromlist=["x"]).__file__).read_text(
        encoding="utf-8"
    )
    assert FAKE_TEXT not in source
