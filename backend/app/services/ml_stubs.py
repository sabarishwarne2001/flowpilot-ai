"""TEST STUBS for the ML models. NOT real OCR and NOT real embeddings.

Active only when ML_STUBS=true. Settings refuses that flag when
ENVIRONMENT=production, and get_stub_embedding_model() / StubOCRProvider
re-check it, so a stub can never answer a customer.

WHY THEY EXIST

Some environments cannot download the model weights. In the hardening
campaign's cloud sandbox, huggingface.co (SentenceTransformers) and
aistudio.baidu.com, modelscope.cn and bcebos.com (PaddleOCR) are blocked, so
the real models cannot load, and every upload would stop at OCR or embedding.
The stubs let the rest of the pipeline (chunking, metering, storage, search,
review, workflows) run there.

WHAT THEY RETURN

StubSentenceTransformer
    384-dimensional, L2-normalised hashed bag-of-words vectors: each lowercase
    word adds +1 or -1 to one SHA-256-chosen dimension. Output is
    deterministic, and texts that share words score higher than texts that do
    not. That is enough for retrieval tests to be meaningful, and it is not
    semantic search.
StubTokenizer
    A word-and-punctuation tokenizer with the call signature the chunker and
    the metering code use (input_ids, offset_mapping, add_special_tokens).
StubOCRProvider
    The real PaddleOCRProvider with only the model call replaced. Digital PDF
    pages keep their real text layer. Each image or scanned page becomes one
    full-page block whose text is the label below plus the first 12 hex
    digits of the page image's SHA-256. It never reads the pixels.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Sequence, Union

import numpy as np

from app.services.ocr.base import OCRUnavailableError
from app.services.ocr.paddle import PaddleOCRProvider

STUB_OCR_LABEL = "[FLOWPILOT TEST STUB OCR - NOT REAL TEXT]"
STUB_EMBEDDING_DIMENSION = 384
STUB_MODEL_NAME = "flowpilot-test-stub-embedding"

_TOKEN = re.compile(r"\w+|[^\w\s]", re.UNICODE)
_CLS_ID, _SEP_ID = 101, 102


def _refuse_in_production() -> None:
    from app.core.config import settings

    if (settings.ENVIRONMENT or "").strip().lower() == "production":
        raise RuntimeError(
            "ML test stubs are refused in production. Unset ML_STUBS and give "
            "the workers the real model weights."
        )


def _token_id(token: str) -> int:
    digest = hashlib.sha256(token.lower().encode("utf-8")).digest()
    return 1000 + int.from_bytes(digest[:4], "big") % 29_000


class StubTokenizer:
    """Callable like a Hugging Face fast tokenizer, for the calls this app makes."""

    def _encode_one(
        self, text: str, *, add_special_tokens: bool, return_offsets_mapping: bool
    ) -> tuple[list[int], list[tuple[int, int]]]:
        matches = list(_TOKEN.finditer(text or ""))
        ids = [_token_id(m.group(0)) for m in matches]
        offsets = [(m.start(), m.end()) for m in matches]
        if add_special_tokens:
            ids = [_CLS_ID, *ids, _SEP_ID]
            offsets = [(0, 0), *offsets, (0, 0)]
        return ids, offsets

    def __call__(
        self,
        text: Union[str, Sequence[str]],
        *,
        add_special_tokens: bool = True,
        return_offsets_mapping: bool = False,
        **_ignored: Any,
    ) -> dict[str, Any]:
        single = isinstance(text, str)
        items = [text] if single else list(text)
        encoded = [
            self._encode_one(
                item,
                add_special_tokens=add_special_tokens,
                return_offsets_mapping=return_offsets_mapping,
            )
            for item in items
        ]
        result: dict[str, Any] = {
            "input_ids": encoded[0][0] if single else [ids for ids, _ in encoded]
        }
        if return_offsets_mapping:
            result["offset_mapping"] = (
                encoded[0][1] if single else [offsets for _, offsets in encoded]
            )
        return result


class StubSentenceTransformer:
    """Stands in for sentence_transformers.SentenceTransformer."""

    is_test_stub = True

    def __init__(self, dimension: int = STUB_EMBEDDING_DIMENSION, max_seq_length: int = 256) -> None:
        self.dimension = dimension
        self.max_seq_length = max_seq_length
        self.tokenizer = StubTokenizer()

    def get_sentence_embedding_dimension(self) -> int:
        return self.dimension

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dimension, dtype=np.float32)
        for match in _TOKEN.finditer((text or "").lower()):
            digest = hashlib.sha256(match.group(0).encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimension
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = float(np.linalg.norm(vector))
        if norm == 0.0:
            vector[0] = 1.0
            return vector
        return vector / norm

    def encode(
        self,
        sentences: Union[str, Sequence[str]],
        *,
        convert_to_numpy: bool = True,
        **_ignored: Any,
    ) -> Any:
        _refuse_in_production()
        single = isinstance(sentences, str)
        items = [sentences] if single else list(sentences)
        matrix = np.stack([self._vector(item) for item in items]) if items else np.zeros(
            (0, self.dimension), dtype=np.float32
        )
        if not convert_to_numpy:
            matrix = [row for row in matrix]
        return matrix[0] if single else matrix


def get_stub_embedding_model() -> StubSentenceTransformer:
    _refuse_in_production()
    return StubSentenceTransformer()


class StubOCRProvider(PaddleOCRProvider):
    """PaddleOCRProvider with only the model call replaced.

    Everything else is the real provider: the PDF text layer (digital pages
    keep their real text and boxes and never reach the stub), page
    rasterisation, and block parsing. Each image or scanned page the model
    would have read becomes one full-page block whose text is the stub label.
    """

    name = "test-stub-ocr"

    def __init__(self, *, language: str = "en") -> None:
        _refuse_in_production()
        super().__init__(language=language)
        self._model_name = "stub"

    def is_available(self) -> bool:
        return True

    def _build_engine(self) -> Any:
        raise OCRUnavailableError("The test stub has no engine to build.")

    def _run_engine(self, image_path: Path) -> Any:
        _refuse_in_production()
        from PIL import Image  # noqa: PLC0415

        path = Path(image_path)
        with Image.open(path) as image:
            width, height = image.size
        fingerprint = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
        polygon = [[0.0, 0.0], [float(width), 0.0], [float(width), float(height)], [0.0, float(height)]]
        return [[[polygon, (f"{STUB_OCR_LABEL} sha256={fingerprint}", 1.0)]]]
