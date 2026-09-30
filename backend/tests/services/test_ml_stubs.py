"""The ML test stubs: labelled, deterministic, and refused in production."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.services import ml_stubs
from app.services.embedding_metering import _count_tokens
from app.services.embedding_service import embedding_service
from app.services.ocr.paddle import get_provider

pytestmark = pytest.mark.no_db

CORPUS_PDF = Path(__file__).resolve().parents[2] / "evaluation" / "corpus" / "Expense_Policy.pdf"


def test_settings_refuse_ml_stubs_in_production() -> None:
    with pytest.raises(ValidationError, match="ML_STUBS"):
        Settings(ENVIRONMENT="production", ML_STUBS=True)


def test_settings_allow_ml_stubs_outside_production() -> None:
    assert Settings(ENVIRONMENT="development", ML_STUBS=True).ML_STUBS is True


def test_stub_refuses_at_runtime_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(RuntimeError, match="refused in production"):
        ml_stubs.get_stub_embedding_model()
    with pytest.raises(RuntimeError, match="refused in production"):
        ml_stubs.StubOCRProvider()


def test_stub_vectors_are_deterministic_normalised_and_lexical() -> None:
    model = ml_stubs.get_stub_embedding_model()
    vectors = model.encode(
        ["invoice total due", "invoice total due", "invoice amount due", "holiday rota", ""],
        normalize_embeddings=True,
    )
    assert vectors.shape == (5, 384)
    assert model.get_sentence_embedding_dimension() == 384
    np.testing.assert_allclose(np.linalg.norm(vectors, axis=1), 1.0, rtol=1e-5)
    np.testing.assert_array_equal(vectors[0], vectors[1])
    assert float(vectors[0] @ vectors[2]) > float(vectors[0] @ vectors[3])
    assert model.encode("single").shape == (384,)


def test_stub_tokenizer_matches_the_calls_the_app_makes() -> None:
    tokenizer = ml_stubs.get_stub_embedding_model().tokenizer
    text = "Net 30, due 2026-10-01."
    encoded = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)
    assert [text[s:e] for s, e in encoded["offset_mapping"]][:4] == ["Net", "30", ",", "due"]
    assert len(encoded["input_ids"]) == len(encoded["offset_mapping"])
    counts = _count_tokens(ml_stubs.get_stub_embedding_model(), [text, "a b"])
    assert counts == [len(encoded["input_ids"]) + 2, 4]


def test_embedding_service_uses_the_stub_only_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "ML_STUBS", True)
    monkeypatch.setattr(embedding_service, "_model", None)
    assert getattr(embedding_service._get_model(), "is_test_stub", False) is True
    assert len(embedding_service.generate_embeddings(["hello"])[0]) == 384


def test_stub_chunks_are_recorded_under_the_stub_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    import uuid

    from app.services import chunk_writer
    from app.services.document_models import ChunkCandidate

    written: list = []

    class _Session:
        def add_all(self, rows: list) -> None:
            written.extend(rows)

        def flush(self) -> None:
            pass

    monkeypatch.setattr(chunk_writer, "delete_chunks_for_work_item", lambda *a, **k: 0)
    candidate = ChunkCandidate(
        chunk_index=0, page_number=1, page_start_char=0, page_end_char=5,
        content="hello", token_count=1,
    )
    ids = {name: uuid.uuid4() for name in ("workspace_id", "organization_id", "work_item_id", "uploaded_file_id")}
    for enabled, expected in ((True, ml_stubs.STUB_MODEL_NAME), (False, "sentence-transformers/all-MiniLM-L6-v2")):
        monkeypatch.setattr(settings, "ML_STUBS", enabled)
        written.clear()
        chunk_writer.replace_document_chunks(
            _Session(), **ids, candidates=[candidate], embeddings=[[0.0] * 384],
            embedding_model="sentence-transformers/all-MiniLM-L6-v2",
        )
        assert written[0].embedding_model == expected


def test_ocr_stub_labels_scanned_pages_and_keeps_real_text_layers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import pypdfium2 as pdfium
    from PIL import Image

    monkeypatch.setattr(settings, "ML_STUBS", True)
    provider = get_provider()
    assert isinstance(provider, ml_stubs.StubOCRProvider)

    # A digital PDF never reaches the stub: its real text layer is used.
    digital = provider.extract(CORPUS_PDF, mime_type="application/pdf")
    document = pdfium.PdfDocument(str(CORPUS_PDF))
    try:
        assert digital.page_count == len(document)
    finally:
        document.close()
    assert not any(ml_stubs.STUB_OCR_LABEL in page.text for page in digital.pages)
    assert digital.billable_pages == 0

    # A scanned PDF (no text layer) and an image do, as labelled full-page blocks.
    scan = Image.new("RGB", (400, 200), "white")
    scanned_pdf = tmp_path / "scan.pdf"
    scan.save(scanned_pdf, format="PDF")
    image = tmp_path / "scan.png"
    scan.save(image, format="PNG")

    for path, mime in ((scanned_pdf, "application/pdf"), (image, "image/png")):
        result = provider.extract(path, mime_type=mime)
        assert result.provider == "test-stub-ocr"
        assert result.page_count == 1
        page = result.pages[0]
        assert page.ocr_applied is True
        assert page.text.startswith(ml_stubs.STUB_OCR_LABEL)
        assert page.blocks and page.blocks[0].box is not None
