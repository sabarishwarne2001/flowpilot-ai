"""ARCH-31 Step 2 — description similarity, and the profile constraint behind it.

THE CONFLICT THIS MODULE RESOLVES, STATED PLAINLY
=================================================

ARCH-31 §2 asks for description cosine via a local SentenceTransformers model,
and ARCH-31 §3 puts `procurement.score` on the LIGHT worker profile. Those two
instructions cannot both be followed literally:

  * `app/workers/profiles.py` declares `LIGHT.allow_heavy = frozenset()`, and
    `sentence_transformers` is a member of `HEAVY_MODULES`.
  * `assert_imports_match_profile()` runs at EVERY worker's startup and raises
    `ProfileError` when a heavy module a profile does not permit is already in
    `sys.modules`.
  * The thin image does not install the package at all, so a lazy import
    inside the handler does not dodge the problem — it converts a startup
    failure into a first-run failure, which is strictly worse: the job
    enqueues cleanly, claims cleanly, and dies. That is the precise defect
    class the comments in `profiles.py` were written to prevent, twice.

So similarity is a backend, chosen at score time and IDENTIFIED IN THE DIGEST.
`LexicalBackend` is pure Python, has no heavy import, and is the default —
which is what lets the job live on LIGHT as specified. `SentenceTransformerBackend`
is available to any process whose profile permits `sentence_transformers`
(ENRICH, ALL), and refuses to construct anywhere else rather than importing
and tripping the startup guard for whatever runs next in that process.

WHY THE BACKEND ID GOES IN THE DIGEST
=====================================

Two workers on two profiles would otherwise score the same documents under
the same policy to different pair costs and write the same `input_digest`.
The second score would be treated as a duplicate of the first and discarded,
so which answer a tenant sees would depend on which queue drained first.
Folding `backend_id` into the digest makes those two different inputs, which
is what they are: a case scored under a different similarity model is
re-scored and the old one superseded, exactly as it is for a policy change.

DETERMINISM
===========

`verify_arch31.py` asserts identical output across 25 runs. Both backends
return a `Decimal` quantised to `SIMILARITY_PLACES`. Rounding is not
cosmetic — it happens BEFORE the value enters the cost matrix, because an
IEEE-754 cosine that differs in its last bit between two runs produces a
different integer cost, a different assignment under a tie, and a different
digest. Quantising at the boundary is what makes the 25-run gate passable at
all.

The lexical backend is deliberately not TF-IDF. An IDF term depends on the
corpus, which means the same two descriptions score differently depending on
what else was in the batch — a property that is fine for search ranking and
disqualifying for a number a human is asked to approve a payment against.
"""

from __future__ import annotations

import math
import re
import sys
from collections import Counter
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Optional, Protocol, Sequence

__all__ = [
    "SimilarityBackend",
    "LexicalBackend",
    "SentenceTransformerBackend",
    "get_backend",
    "SIMILARITY_PLACES",
    "LEXICAL_BACKEND_ID",
    "SENTENCE_TRANSFORMER_BACKEND_ID",
    "DEFAULT_BACKEND_ID",
    "BackendUnavailable",
]

#: Decimal places every backend quantises to before returning. Six is chosen
#: to match the codebase's micros convention: one unit of similarity at this
#: resolution is one unit of integer cost, so nothing is lost when the value
#: is scaled into the cost matrix and nothing survives that the float did not
#: reliably carry.
SIMILARITY_PLACES: int = 6
_QUANTUM = Decimal(1).scaleb(-SIMILARITY_PLACES)

LEXICAL_BACKEND_ID: str = "lexical-v1"
SENTENCE_TRANSFORMER_BACKEND_ID: str = "st-all-minilm-l6-v2"
DEFAULT_BACKEND_ID: str = LEXICAL_BACKEND_ID

_TOKEN = re.compile(r"[a-z0-9]+")
_TRIGRAM_WEIGHT = Decimal("0.4")
_TOKEN_WEIGHT = Decimal("0.6")


class BackendUnavailable(RuntimeError):
    """The requested backend cannot run in this process."""


class SimilarityBackend(Protocol):
    backend_id: str

    def similarity(self, left: str, right: str) -> Decimal:
        """Cosine in [0, 1], quantised to SIMILARITY_PLACES."""


def _quantise(value: float | Decimal) -> Decimal:
    result = Decimal(str(value)).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN)
    if result < 0:
        return Decimal(0).quantize(_QUANTUM)
    if result > 1:
        return Decimal(1).quantize(_QUANTUM)
    return result


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _trigrams(text: str) -> list[str]:
    folded = " ".join(_tokens(text))
    if len(folded) < 3:
        return [folded] if folded else []
    return [folded[i : i + 3] for i in range(len(folded) - 2)]


def _counter_cosine(left: Sequence[str], right: Sequence[str]) -> Decimal:
    if not left or not right:
        return Decimal(0)
    a = Counter(left)
    b = Counter(right)
    shared = set(a) & set(b)
    if not shared:
        return Decimal(0)
    dot = sum(a[key] * b[key] for key in shared)
    norm_a = math.sqrt(sum(value * value for value in a.values()))
    norm_b = math.sqrt(sum(value * value for value in b.values()))
    if norm_a == 0 or norm_b == 0:
        return Decimal(0)
    return _quantise(dot / (norm_a * norm_b))


@dataclass(frozen=True)
class LexicalBackend:
    """Deterministic, dependency-free, corpus-independent description cosine.

    Two views combined: token multiset cosine catches reordering and
    restatement ("M8 hex bolt, zinc" vs "zinc hex bolt M8"), character
    trigram cosine catches the abbreviations and typos that dominate real
    purchase-order text ("HEX BOLT M8 ZP" vs "Hex Bolt M8 Zinc Plated").

    Neither view alone is enough. Tokens alone score the abbreviation pair at
    0.5 because "zp" and "zinc" share nothing; trigrams alone score unrelated
    lines from one supplier too highly because their descriptions share a
    house style. The weighting favours tokens because a token agreement is a
    stronger claim than a substring agreement.
    """

    backend_id: str = LEXICAL_BACKEND_ID

    def similarity(self, left: str, right: str) -> Decimal:
        left_text = (left or "").strip()
        right_text = (right or "").strip()
        if not left_text or not right_text:
            return Decimal(0).quantize(_QUANTUM)
        if left_text.lower() == right_text.lower():
            return Decimal(1).quantize(_QUANTUM)

        token_score = _counter_cosine(_tokens(left_text), _tokens(right_text))
        trigram_score = _counter_cosine(_trigrams(left_text), _trigrams(right_text))
        blended = token_score * _TOKEN_WEIGHT + trigram_score * _TRIGRAM_WEIGHT
        return _quantise(blended)


class SentenceTransformerBackend:
    """Local SentenceTransformers cosine, for processes allowed heavy modules.

    Constructing this on a profile that does not permit `sentence_transformers`
    raises rather than importing. The import itself is the harm: once the
    module is in `sys.modules`, the next call to
    `assert_imports_match_profile()` in that process fails and takes the
    worker down, so a well-meaning fallback here would break a fleet.
    """

    backend_id = SENTENCE_TRANSFORMER_BACKEND_ID

    def __init__(self, model_name: str = "all-MiniLM-L6-v2") -> None:
        _assert_heavy_permitted()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:  # pragma: no cover - image-dependent
            raise BackendUnavailable(
                "sentence-transformers is not installed in this image. The "
                "thin worker image does not ship it; run procurement.score "
                "on a profile that does, or use the lexical backend."
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.backend_id = f"st-{model_name.lower()}"

    def similarity(self, left: str, right: str) -> Decimal:
        left_text = (left or "").strip()
        right_text = (right or "").strip()
        if not left_text or not right_text:
            return Decimal(0).quantize(_QUANTUM)

        vectors = self._model.encode(
            [left_text, right_text],
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        dot = float(sum(float(a) * float(b) for a, b in zip(vectors[0], vectors[1])))
        # Quantise BEFORE the caller sees it. See the module header: an
        # un-rounded float here is the whole reason a 25-run determinism gate
        # would fail intermittently rather than never.
        return _quantise(dot)


def _assert_heavy_permitted() -> None:
    from app.workers import profiles

    permitted = any(
        "sentence_transformers" in profile.allow_heavy
        for profile in (profiles.ENRICH, profiles.ALL)
    )
    if not permitted:  # pragma: no cover - defends against a profile edit
        raise BackendUnavailable(
            "no worker profile permits sentence_transformers; the backend "
            "cannot be constructed anywhere."
        )

    active = getattr(profiles, "ACTIVE_PROFILE", None)
    if active is not None and "sentence_transformers" not in active.allow_heavy:
        raise BackendUnavailable(
            f"worker profile {active.name!r} does not permit "
            "sentence_transformers. Importing it here would put the module "
            "in sys.modules and make the next assert_imports_match_profile() "
            "call fail, taking this worker down. Use the lexical backend on "
            "LIGHT."
        )


def _heavy_already_imported() -> bool:
    return "sentence_transformers" in sys.modules


def get_backend(backend_id: Optional[str] = None) -> SimilarityBackend:
    """Resolve a backend by id, defaulting to the LIGHT-safe lexical one.

    An unknown id raises rather than falling back. A silent fallback would
    change the digest without changing anything a reader can see, and the
    tenant would be told a case had been re-scored under a model it was not.
    """
    requested = (backend_id or DEFAULT_BACKEND_ID).strip().lower()

    if requested == LEXICAL_BACKEND_ID:
        return LexicalBackend()

    if requested.startswith("st-"):
        model = requested[3:] or "all-MiniLM-L6-v2"
        return SentenceTransformerBackend(model_name=model)

    raise BackendUnavailable(
        f"unknown similarity backend {backend_id!r}. Known: "
        f"{LEXICAL_BACKEND_ID!r}, or 'st-<model-name>' on a profile that "
        "permits sentence_transformers."
    )