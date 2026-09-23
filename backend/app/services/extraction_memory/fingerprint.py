"""ARCH41-S2:fingerprint — a document's layout signature. Pure; no I/O.

WHY HASHED FEATURES AND NOT THE SENTENCE-TRANSFORMER
=====================================================

A layout template is recognised by its BOILERPLATE: the same labels, in the
same order, on every invoice from one supplier. That is a lexical property.
A semantic embedding would call two different suppliers' invoices "similar"
(they mean the same thing) — exactly the confusion a template must avoid —
and would load a model on the enrichment hot path. Signed feature hashing of
the header skeleton is deterministic across processes, needs no model, and
separates layouts by their wording.

Digits are masked before hashing, so invoice numbers, dates and amounts
(which change on every document) do not move the signature.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from typing import Optional, Sequence

from app.services.extraction_memory.vocabulary import SIGNATURE_DIM

HEADER_LINES = 40
MAX_ANCHOR_TOKENS = 64
_DIGIT = re.compile(r"\d")
_SPACE = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z#][a-z#&/.\-']*")


def normalise_line(line: str) -> str:
    return _SPACE.sub(" ", _DIGIT.sub("#", (line or "").lower())).strip()


def skeleton_lines(text: str, limit: int = HEADER_LINES) -> list[str]:
    out: list[str] = []
    for raw in (text or "").splitlines():
        line = normalise_line(raw)
        if line:
            out.append(line)
            if len(out) >= limit:
                break
    return out


def _words(line: str) -> list[str]:
    return [t for t in _TOKEN.findall(line) if any(c.isalpha() for c in t) and len(t) >= 2]


def anchor_tokens(text: str, limit: int = MAX_ANCHOR_TOKENS) -> list[str]:
    """Label-like words of the header, in order, de-duplicated."""
    seen: list[str] = []
    for line in skeleton_lines(text):
        for word in _words(line):
            word = word.strip(".-'/")
            if len(word) >= 3 and word.isalpha() and word not in seen:
                seen.append(word)
                if len(seen) >= limit:
                    return seen
    return seen


def _bucket(feature: str) -> tuple[int, float]:
    digest = hashlib.sha1(feature.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % SIGNATURE_DIM
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def signature(text: str) -> Optional[list[float]]:
    """L2-normalised signed feature hash of the header skeleton, or None if empty."""
    vector = [0.0] * SIGNATURE_DIM
    for line in skeleton_lines(text):
        words = _words(line)
        features: list[tuple[str, float]] = [(f"w:{w}", 1.0) for w in words]
        features += [(f"b:{a} {b}", 1.0) for a, b in zip(words, words[1:])]
        compact = line.replace(" ", "")
        features += [(f"c:{compact[i:i + 3]}", 0.5) for i in range(max(0, len(compact) - 2))]
        for feature, weight in features:
            index, sign = _bucket(feature)
            vector[index] += sign * weight
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        return None
    return [v / norm for v in vector]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


@dataclass(frozen=True)
class Fingerprint:
    vector: list[float]
    anchor_tokens: list[str]


def fingerprint(text: str) -> Optional[Fingerprint]:
    vector = signature(text)
    if vector is None:
        return None
    return Fingerprint(vector=vector, anchor_tokens=anchor_tokens(text))
