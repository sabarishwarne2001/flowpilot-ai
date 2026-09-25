"""ARCH45-S1:encoders — clause embeddings for alignment.

The roadmap asks for sentence embeddings from the SentenceTransformer the
platform already runs (all-MiniLM-L6-v2, 384 dimensions, cached in the enrich
image since ARCH-29). That model may only be imported where the worker profile
permits it (app/workers/profiles.py: ENRICH and ALL), which is why the
corroboration job runs on the ENRICH profile.

The same reasoning as ARCH-31's similarity backends applies here, and the same
answer: the encoder is a BACKEND, identified in the run fingerprint.

  SentenceTransformerEncoder  the production encoder (semantic: a paraphrase
                              still aligns with its original)
  LexicalEncoder              deterministic hashed token and character-trigram
                              vectors, no dependency; used where the model
                              cannot load (no network for a first download, a
                              profile that forbids it) and in the offline gates

Alignment does not depend on which one ran for exact and near-exact text: the
clause score blends the encoder cosine with a token Dice coefficient over the
canonical text (align.py), so identical clauses always score 1.0.
"""

from __future__ import annotations

import hashlib
import importlib.util
import logging
import os
import sys
from typing import Optional, Protocol, Sequence

import numpy as np

from app.services.corroboration import vocabulary as v

logger = logging.getLogger("app.services.corroboration.encoders")

#: Service roles whose profile forbids sentence_transformers (docker-compose SERVICE_ROLE).
_FORBIDDEN_ROLES = frozenset({"worker-light", "worker-ocr", "worker-relay", "worker-delivery", "worker-stripe",
                              "sweeper", "scheduler"})


class EncoderUnavailable(RuntimeError):
    """The requested encoder cannot run in this process."""


class Encoder(Protocol):
    encoder_id: str
    canonical_input: bool

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        """(len(texts), dim) float64, rows L2-normalised (a zero row for empty text)."""


def _bucket(feature: str, dim: int) -> tuple[int, float]:
    digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
    value = int.from_bytes(digest, "little")
    return value % dim, (1.0 if (value >> 63) & 1 else -1.0)


class LexicalEncoder:
    """Hashed features: every token (weight 1) and character trigram (0.5) of
    the canonical text, signed-hashed into LEXICAL_DIM buckets. Deterministic
    across processes and platforms (blake2b, not Python's salted hash)."""

    encoder_id = v.ENCODER_LEXICAL
    #: Encodes the canonical text (typed value tokens, folded words).
    canonical_input = True

    def __init__(self, dim: int = v.LEXICAL_DIM) -> None:
        self.dim = dim

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float64)
        from app.services.corroboration.normalize import tokens as content_tokens

        for row, text in enumerate(texts):
            words = content_tokens(text or "")
            for w in words:
                i, s = _bucket("t:" + w, self.dim)
                out[row, i] += s
            joined = " ".join(words)
            for k in range(max(0, len(joined) - 2)):
                i, s = _bucket("c:" + joined[k:k + 3], self.dim)
                out[row, i] += 0.5 * s
            norm = float(np.linalg.norm(out[row]))
            if norm > 0:
                out[row] /= norm
        return out


def _role() -> str:
    return (os.environ.get("SERVICE_ROLE") or "").strip().lower()


def sentence_transformers_permitted() -> bool:
    if "sentence_transformers" in sys.modules:
        return True
    if _role() in _FORBIDDEN_ROLES:
        return False
    try:
        return importlib.util.find_spec("sentence_transformers") is not None
    except (ImportError, ValueError):
        return False


class SentenceTransformerEncoder:
    """The platform's SentenceTransformer (app.services.embedding_service), used
    only where the worker profile permits it. Rows are normalised; values are
    rounded to 6 places so the same text encodes to the same vector bit for bit
    on a re-run (the ARCH-31 determinism rule)."""

    encoder_id = v.ENCODER_SENTENCE_TRANSFORMER
    #: Encodes the clause as written: the model was trained on natural text.
    canonical_input = False

    def __init__(self) -> None:
        if not sentence_transformers_permitted():
            raise EncoderUnavailable("sentence_transformers is not permitted or not installed in this process")
        from app.services.embedding_service import embedding_service

        try:
            self._model = embedding_service.model
        except Exception as exc:  # noqa: BLE001 - a model that cannot load is an unavailable encoder
            raise EncoderUnavailable(f"the embedding model could not be loaded: {exc}") from exc

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1))
        vectors = self._model.encode(list(texts), batch_size=32, convert_to_numpy=True, normalize_embeddings=True,
                                     show_progress_bar=False)
        array = np.round(np.asarray(vectors, dtype=np.float64), 6)
        norms = np.linalg.norm(array, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return array / norms


def expected_encoder_id() -> str:
    """The encoder a run requested now would use: the SentenceTransformer where
    it is installed and permitted, else the lexical encoder. Decided without
    importing anything heavy (find_spec only), so the web process can put it in
    the fingerprint."""
    return v.ENCODER_SENTENCE_TRANSFORMER if sentence_transformers_permitted() else v.ENCODER_LEXICAL


def get_encoder(requested: Optional[str] = None) -> tuple[Encoder, Optional[str]]:
    """(encoder, note). Falls back to the lexical encoder, with a note saying
    why, when the SentenceTransformer cannot run here."""
    wanted = requested or expected_encoder_id()
    if wanted == v.ENCODER_SENTENCE_TRANSFORMER:
        try:
            return SentenceTransformerEncoder(), None
        except EncoderUnavailable as exc:
            logger.warning("corroboration.encoder_fallback", extra={"reason": str(exc)})
            return LexicalEncoder(), f"SentenceTransformer unavailable ({exc}); aligned with the lexical encoder"
    if wanted != v.ENCODER_LEXICAL:
        raise EncoderUnavailable(f"unknown encoder {wanted!r}")
    return LexicalEncoder(), None


__all__ = ["Encoder", "EncoderUnavailable", "LexicalEncoder", "SentenceTransformerEncoder", "expected_encoder_id",
           "get_encoder", "sentence_transformers_permitted"]
