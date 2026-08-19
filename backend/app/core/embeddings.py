"""ARCH-11 Step 1 — the embedding model registry, and the §0.4 decision.

`document_settings.embedding_model` has existed since Sprint 3, defaults to
`"sentence-transformers/all-MiniLM-L6-v2"`, is settable per workspace, and has
never had a reader: `embedding_service._get_model()` reads
`settings.EMBEDDING_MODEL_NAME`, which is the *bare* alias
`"all-MiniLM-L6-v2"`. Two strings, one model, no relationship between them.

That is survivable while vectors live in Chroma, where a collection has no
declared width. It stops being survivable in Step 2, because `vector(n)` is a
fixed-width column: honouring a per-workspace model would mean one table per
dimension.

**The decision (matching your §0.4 answer):** the model is a *platform*
setting, not a workspace setting. It is recorded **per chunk** so a future
migration can be incremental, and `document_settings.embedding_model` is
deprecated — frozen at the canonical value now, dropped in a CONTRACT
migration once no code reads it.

The thing this module exists to prevent is subtler than either of those: two
spellings of the same model. Once `document_chunks.embedding_model` is a real
column that a retrieval query compares against, `"all-MiniLM-L6-v2"` and
`"sentence-transformers/all-MiniLM-L6-v2"` are different strings, and a
comparison between them fails on rows that are perfectly valid. Every write and
every comparison goes through `canonical_model_name()`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app.core.config import settings

#: The Hugging Face namespace that `sentence-transformers` implies when a bare
#: model name is given. `SentenceTransformer("all-MiniLM-L6-v2")` and
#: `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")` load the
#: same weights; only the strings differ.
_DEFAULT_NAMESPACE = "sentence-transformers"


@dataclass(frozen=True)
class EmbeddingModelSpec:
    """Facts about an embedding model that the schema depends on."""

    #: Fully-qualified name. This is the value stored in
    #: `document_chunks.embedding_model`.
    name: str
    #: Output dimension. This is the `n` in `vector(n)` and cannot be changed
    #: without a migration.
    dimension: int
    #: Maximum input sequence length in word-piece tokens. Input beyond this is
    #: **silently truncated** by the encoder. See `EMBEDDING_MAX_SEQUENCE_TOKENS`
    #: and the chunk-sizing note in `docs/arch11-step1.md`.
    max_sequence_tokens: int
    #: Free-text note carried into the baseline JSON so a later reader knows
    #: what was in force when the numbers were captured.
    note: str = ""


#: Models this deployment is allowed to embed with. Adding one is a deliberate
#: act: a model whose `dimension` differs from the deployed `vector(n)` column
#: cannot be used without a schema migration, and this table is where that is
#: made visible rather than discovered at insert time.
KNOWN_EMBEDDING_MODELS: dict[str, EmbeddingModelSpec] = {
    "sentence-transformers/all-MiniLM-L6-v2": EmbeddingModelSpec(
        name="sentence-transformers/all-MiniLM-L6-v2",
        dimension=384,
        max_sequence_tokens=256,
        note=(
            "The incumbent. 256-token window — shorter than the 300-500 token "
            "chunk target in the masterplan. See ARCH-11 Step 1 finding F1."
        ),
    ),
    "BAAI/bge-small-en-v1.5": EmbeddingModelSpec(
        name="BAAI/bge-small-en-v1.5",
        dimension=384,
        max_sequence_tokens=512,
        note=(
            "Same dimension as the incumbent, double the window. Listed here "
            "because it is the one swap that does NOT invalidate vector(384), "
            "which makes it the cheap answer to F1 if you take it before the "
            "Step 4 backfill."
        ),
    ),
}


class UnknownEmbeddingModelError(ValueError):
    """The configured model is not in `KNOWN_EMBEDDING_MODELS`."""


def canonical_model_name(raw: Optional[str]) -> str:
    """Normalise a model name to the single spelling stored in the database.

    Bare names are namespaced; anything already containing a `/` is returned
    with only whitespace stripped. Empty input resolves to the configured
    platform model rather than raising, so a NULL column never becomes a crash
    on the retrieval path.
    """
    if raw is None or not raw.strip():
        return canonical_model_name(settings.EMBEDDING_MODEL_NAME)
    name = raw.strip()
    if "/" not in name:
        name = f"{_DEFAULT_NAMESPACE}/{name}"
    return name


def active_model_name() -> str:
    """The canonical name of the model this process will actually load."""
    return canonical_model_name(settings.EMBEDDING_MODEL_NAME)


def resolve_spec(raw: Optional[str] = None) -> EmbeddingModelSpec:
    """Return the spec for `raw`, or for the configured model when omitted."""
    name = canonical_model_name(raw)
    try:
        return KNOWN_EMBEDDING_MODELS[name]
    except KeyError as exc:
        raise UnknownEmbeddingModelError(
            f"{name!r} is not a known embedding model. Known: "
            f"{', '.join(sorted(KNOWN_EMBEDDING_MODELS))}. Add it to "
            "app/core/embeddings.py with its dimension and sequence window "
            "before configuring it — an unknown dimension is a silent "
            "insert failure against vector(n)."
        ) from exc


def active_dimension() -> int:
    """The `n` that `document_chunks.embedding` must be declared with."""
    return resolve_spec().dimension


def active_max_sequence_tokens() -> int:
    """Tokens the encoder will actually consume; the rest are truncated.

    `settings.EMBEDDING_MAX_SEQUENCE_TOKENS` overrides the registry when set,
    for the case where the deployed model has been reconfigured at load time.
    """
    override = getattr(settings, "EMBEDDING_MAX_SEQUENCE_TOKENS", None)
    if override:
        return int(override)
    return resolve_spec().max_sequence_tokens


def assert_settings_are_coherent() -> None:
    """Fail fast at startup rather than at the first insert.

    Called from the Step 2 model import and from `verify_arch11_step2.py`.
    """
    spec = resolve_spec()
    configured_dimension = getattr(settings, "EMBEDDING_DIMENSION", None)
    if configured_dimension and int(configured_dimension) != spec.dimension:
        raise UnknownEmbeddingModelError(
            f"EMBEDDING_DIMENSION={configured_dimension} contradicts "
            f"{spec.name} (dimension {spec.dimension}). `vector(n)` is "
            "fixed-width; one of these is wrong and inserts will fail."
        )


__all__ = [
    "EmbeddingModelSpec",
    "KNOWN_EMBEDDING_MODELS",
    "UnknownEmbeddingModelError",
    "active_dimension",
    "active_max_sequence_tokens",
    "active_model_name",
    "assert_settings_are_coherent",
    "canonical_model_name",
    "resolve_spec",
]