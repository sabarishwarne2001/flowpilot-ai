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


def test_ocr_provider_is_the_labelled_stub_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    import pypdfium2 as pdfium

    monkeypatch.setattr(settings, "ML_STUBS", True)
    provider = get_provider()
    assert isinstance(provider, ml_stubs.StubOCRProvider)

    result = provider.extract(CORPUS_PDF, mime_type="application/pdf")
    document = pdfium.PdfDocument(str(CORPUS_PDF))
    try:
        assert result.page_count == len(document)
    finally:
        document.close()
    assert all(page.text.startswith(ml_stubs.STUB_OCR_LABEL) for page in result.pages)
    assert result.provider == "test-stub-ocr"
    assert provider.extract(CORPUS_PDF, mime_type="application/pdf", max_pages=1).page_count == 1
