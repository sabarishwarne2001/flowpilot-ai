"""ARCH-34 §5.3 — what a document looks like, reduced to four comparable things.

PURE
====

Standard library only. No `Session`, no clock, no settings, no numpy, no
`datasketch`. `verify_arch34.py` drives every function here against curated
text with no database and no configuration at all, and the AST purity gate
asserts the import list.

WHY MINHASH IS HAND-ROLLED RATHER THAN `datasketch`
===================================================

`datasketch` is MIT and would have been acceptable. It is not used, and the
reason is not licensing:

  * The permutation count is FIXED at 128 forever. The library's value is in
    the parts that vary — LSH indexes, weighted MinHash, HyperLogLog — none of
    which this phase has a use for.

  * A stored signature must be reproducible for the lifetime of the row. That
    makes the permutation family a SCHEMA-LIKE commitment, and a schema-like
    commitment whose definition lives in a pinned version of a third party is
    one `pip install -U` away from silently recomputing into a different
    space. Sixty lines that never change are the safer dependency.

  * It would pull numpy into a module the purity gate wants to be stdlib-only.
    numpy is already in the image for the embedding stack, but "already
    installed" and "inside the pure boundary" are different questions.

The construction is the standard one and deliberately the same one
`datasketch` implements: universal hashing `h_i(x) = ((a_i·x + b_i) mod p)`
over the Mersenne prime 2^61−1, folded to 32 bits, minimum taken per
permutation. Arbitrary-precision integers make it exact; there is no floating
point anywhere in the hash path, so the same shingle set produces bit-identical
signatures on any machine and any Python 3.

THE EMPTY-DOCUMENT TRAP
=======================

A document with no extractable text has an empty shingle set, and the minimum
over an empty set has to be *something*. The conventional sentinel is the
maximum hash value, which means two empty documents produce two IDENTICAL
signatures and a Jaccard estimate of exactly 1.0.

Two documents that failed OCR are not duplicates of each other. Left alone,
the first bad scan in a workspace would pair with every subsequent bad scan,
and the radar would fill with HIGH findings about blank pages.

So `shingle_count` is carried ON the fingerprint, `jaccard_estimate()` refuses
a comparison where either side is empty, and `L2` refuses the pair. The
sentinel is still used, because a fingerprint row must exist for every work
item; it is just never allowed to mean similarity.

WHAT IS AND IS NOT HASHED
=========================

`content_sha256` is over the STORED BYTES of the uploaded file, taken from
`uploaded_files.checksum_sha256` — not recomputed here and not computed over
extracted text. L0 claims "these are the same file", and the only evidence for
that is the file. Text-level identity is what L2 is for, and conflating them
would let a re-OCR of the same scan with a newer PaddleOCR build present
itself as byte-identical.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Optional, Sequence

from app.services.radar import vocabulary as vocab

__all__ = [
    "ChunkVector",
    "DocumentFingerprint",
    "normalise_for_shingling",
    "shingle_source_from_lines",
    "shingles",
    "minhash_signature",
    "permutations",
    "jaccard_estimate",
    "exact_jaccard",
    "matched_shingles",
    "mean_embedding",
    "l2_normalise",
    "cosine",
    "content_sha256",
    "build",
    "input_digest",
    "EMPTY_SIGNATURE",
]


# ===========================================================================
# Pure carriers
# ===========================================================================


@dataclass(frozen=True)
class ChunkVector:
    """One ARCH-11 chunk, decoupled from `DocumentChunk`.

    The same boundary decision `app/services/assertions/families.Chunk` makes,
    for the same reason: the impure loader converts ORM rows into these, and
    everything downstream is gateable offline.
    """

    chunk_id: str
    chunk_index: int
    text: str
    #: Already L2-normalised by ARCH-11? No — normalisation is applied here.
    #: Assuming it upstream is how a cosine quietly becomes a dot product of
    #: unnormalised vectors and starts exceeding 1.
    vector: tuple[float, ...]
    page_number: Optional[int] = None


@dataclass(frozen=True)
class DocumentFingerprint:
    """The four comparable things, plus what is needed to refuse a comparison.

    Frozen because a fingerprint is computed once, digested and stored. A
    mutable fingerprint is one that can be edited after its digest was taken.
    """

    work_item_id: str
    content_sha256: str
    vendor_key: Optional[str]
    document_number: Optional[str]
    minhash: tuple[int, ...]
    embedding: tuple[float, ...]
    #: Zero means the document produced no shingles. Load-bearing: see the
    #: empty-document trap in the module docstring.
    shingle_count: int
    line_count: int
    page_count: Optional[int] = None
    document_date: Optional[date] = None
    total_micros: Optional[int] = None
    currency: Optional[str] = None
    embedding_model: str = ""
    engine_version: str = vocab.ENGINE_VERSION

    def __post_init__(self) -> None:
        if len(self.minhash) != vocab.MINHASH_PERMUTATIONS:
            raise ValueError(
                f"A signature must have exactly {vocab.MINHASH_PERMUTATIONS} "
                f"values; got {len(self.minhash)}. `ck_df_minhash_length` "
                "would refuse the insert, and a short signature compared "
                "against a full one would not error — `zip` would silently "
                "truncate and return a plausible Jaccard over fewer "
                "permutations."
            )


# ===========================================================================
# Shingling
# ===========================================================================

_WHITESPACE = re.compile(r"\s+")
_NOISE = re.compile(r"[^0-9a-z\u0900-\u097f]+")


def normalise_for_shingling(text: str) -> str:
    """Fold text to the form shingles are taken over.

    UNLIKE `families.normalize_clause_text`, this is NOT length-preserving and
    does not need to be: nothing downstream reports a character offset into a
    shingled document. That frees it to do the aggressive folding that makes a
    rescan of the same invoice produce the same shingles — case, punctuation,
    and the runs of whitespace an OCR pass sprays through a table.

    Devanagari is kept alongside ASCII alphanumerics because supplier names
    and addresses on Indian invoices are routinely bilingual, and stripping
    the non-Latin half would make two copies of the same document differ by
    exactly the part OCR is least consistent about.
    """
    folded = unicodedata.normalize("NFKC", text or "").casefold()
    folded = _NOISE.sub(" ", folded)
    return _WHITESPACE.sub(" ", folded).strip()


def shingle_source_from_lines(
    lines: Sequence[tuple[Optional[str], Optional[Decimal], Optional[int]]],
) -> str:
    """Build the shingle source from line items rather than from page text.

    §5.3 shingles "normalized line descriptions with quantities and prices",
    and the difference from raw page text matters more than it looks. Page
    text carries the header, the footer, the terms block and the bank details
    — text that is IDENTICAL on every invoice a supplier ever issues. Shingled
    whole, two unrelated invoices from the same supplier share hundreds of
    shingles before a single line item is compared, and the Jaccard floor
    across a vendor's entire history sits somewhere around 0.5 instead of near
    zero.

    Each line contributes `description qty price`, so a reprint that changes
    only the quantity stops matching that line rather than matching it
    anyway.

    Callers with no extracted lines pass the page text to `shingles()`
    directly; the fingerprint records `line_count = 0` and the console says
    the comparison was made on page text.
    """
    parts: list[str] = []
    for description, quantity, unit_price_micros in lines:
        pieces = [normalise_for_shingling(description or "")]
        if quantity is not None:
            # Normalise the Decimal so that 2 and 2.00 shingle identically.
            pieces.append(format(quantity.normalize(), "f"))
        if unit_price_micros is not None:
            pieces.append(str(int(unit_price_micros)))
        joined = " ".join(piece for piece in pieces if piece)
        if joined:
            parts.append(joined)
    return " \u00b6 ".join(parts)


def shingles(text: str, *, size: int = vocab.SHINGLE_WORDS) -> frozenset[str]:
    """The set of word `size`-grams of `text`, already normalised.

    A SET, not a multiset. Jaccard is defined on sets, and a document that
    repeats "payment due on receipt" eleven times is not eleven times more
    similar to one that says it once.

    Documents shorter than `size` words produce one shingle containing the
    whole document rather than none, so a two-line delivery note still has an
    identity. Returning the empty set there would put every short document
    into the empty-document path and make them all mutually "identical".
    """
    if size < 1:
        raise ValueError("Shingle size must be at least 1.")

    words = normalise_for_shingling(text).split()
    if not words:
        return frozenset()
    if len(words) < size:
        return frozenset({" ".join(words)})
    return frozenset(
        " ".join(words[index : index + size])
        for index in range(len(words) - size + 1)
    )


# ===========================================================================
# MinHash
# ===========================================================================


def _coefficient_stream(index: int) -> tuple[int, int]:
    """`(a, b)` for permutation `index`, derived from the fixed seed.

    Derived rather than stored: 128 pairs of 61-bit integers is 2 KB of
    literal in a source file that nobody could review for correctness, and a
    single mistyped digit would be undetectable by reading and catastrophic by
    running. A keyed hash of the index is verifiable by construction.
    """
    material = vocab.MINHASH_SEED.to_bytes(8, "big") + index.to_bytes(4, "big")
    digest = hashlib.blake2b(material, digest_size=32).digest()
    a = int.from_bytes(digest[:16], "big") % vocab.MINHASH_PRIME
    b = int.from_bytes(digest[16:], "big") % vocab.MINHASH_PRIME
    # `a = 0` collapses the permutation to the constant `b`, which would make
    # that slot carry no information and bias every estimate upward. It cannot
    # happen for any real digest, and the guard costs nothing.
    if a == 0:
        a = 1
    return a, b


def permutations() -> tuple[tuple[int, int], ...]:
    """The full permutation family. Deterministic, seeded, fixed at 128."""
    return tuple(
        _coefficient_stream(index) for index in range(vocab.MINHASH_PERMUTATIONS)
    )


_PERMUTATIONS: tuple[tuple[int, int], ...] = permutations()

#: The signature of a document with no shingles. Never allowed to mean
#: similarity — see the module docstring.
EMPTY_SIGNATURE: tuple[int, ...] = tuple(
    vocab.MINHASH_MAX_HASH for _ in range(vocab.MINHASH_PERMUTATIONS)
)


def _base_hash(shingle: str) -> int:
    """A stable 64-bit hash of one shingle, reduced into the prime field.

    `hash()` is deliberately not used: Python randomises string hashing per
    process, so a signature computed in the worker would differ from the one
    computed in the API process, and both would differ tomorrow.
    """
    digest = hashlib.blake2b(shingle.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big") % vocab.MINHASH_PRIME


def minhash_signature(shingle_set: Iterable[str]) -> tuple[int, ...]:
    """The 128-value signature of a shingle set."""
    members = sorted(set(shingle_set))
    if not members:
        return EMPTY_SIGNATURE

    bases = [_base_hash(shingle) for shingle in members]
    prime = vocab.MINHASH_PRIME
    mask = vocab.MINHASH_MAX_HASH

    signature: list[int] = []
    for a, b in _PERMUTATIONS:
        best = mask
        for base in bases:
            value = ((a * base + b) % prime) & mask
            if value < best:
                best = value
        signature.append(best)
    return tuple(signature)


def jaccard_estimate(
    left: Sequence[int],
    right: Sequence[int],
    *,
    left_shingles: Optional[int] = None,
    right_shingles: Optional[int] = None,
) -> Optional[Decimal]:
    """Estimated Jaccard from two signatures, or None when it is meaningless.

    None, not 0.0, when either side is empty. Zero is a MEASUREMENT — "these
    documents share nothing" — and an empty document has not been measured. A
    caller that treats the two the same would report every unreadable scan as
    maximally dissimilar to everything, which is harmless, right up until
    somebody inverts a comparison.
    """
    if len(left) != vocab.MINHASH_PERMUTATIONS or len(
        right
    ) != vocab.MINHASH_PERMUTATIONS:
        raise ValueError(
            "Both signatures must have exactly "
            f"{vocab.MINHASH_PERMUTATIONS} values; got {len(left)} and "
            f"{len(right)}. Comparing signatures of different lengths is "
            "always a bug, and `zip` would hide it."
        )

    if left_shingles == 0 or right_shingles == 0:
        return None

    matches = sum(1 for a, b in zip(left, right) if a == b)
    return (
        Decimal(matches) / Decimal(vocab.MINHASH_PERMUTATIONS)
    ).quantize(Decimal("0.00001"))


def exact_jaccard(left: Iterable[str], right: Iterable[str]) -> Decimal:
    """The TRUE Jaccard over two shingle sets. The gate's oracle.

    This exists so `verify_arch34.py` can compare the ESTIMATE against the
    truth. A gate that compared one MinHash signature to another MinHash
    signature would pass with a broken permutation family, a broken shingler,
    and a hash function that returned a constant.

    It is never called on the hot path — it materialises both sets.
    """
    a = set(left)
    b = set(right)
    union = a | b
    if not union:
        return Decimal("0")
    return (Decimal(len(a & b)) / Decimal(len(union))).quantize(Decimal("0.00001"))


def matched_shingles(
    left: Iterable[str], right: Iterable[str], *, limit: int = vocab.MAX_SHINGLE_SAMPLES
) -> tuple[str, ...]:
    """A deterministic sample of the shingles both documents contain.

    This is the L2 evidence a human checks, so it is SORTED rather than taken
    in iteration order: a reviewer who reopens a finding must see the same
    twelve phrases they saw yesterday, and Python set iteration order is not a
    promise across processes.

    Longest first, because a long matching phrase is more convincing evidence
    than a short one and the reviewer only reads the first few.
    """
    shared = sorted(set(left) & set(right), key=lambda s: (-len(s), s))
    return tuple(shared[:limit])


# ===========================================================================
# Embeddings
# ===========================================================================


def l2_normalise(vector: Sequence[float]) -> tuple[float, ...]:
    """Scale to unit length, so cosine is a dot product.

    A zero vector normalises to a zero vector rather than raising. It cannot
    come from a real embedding model and it can come from a corrupt row, and a
    ZeroDivisionError inside a nightly sweep takes down the sweep for every
    other document in the workspace. Cosine against a zero vector is 0, which
    fires nothing.
    """
    norm = math.sqrt(math.fsum(component * component for component in vector))
    if norm == 0.0:
        return tuple(0.0 for _ in vector)
    return tuple(component / norm for component in vector)


def mean_embedding(vectors: Sequence[Sequence[float]]) -> tuple[float, ...]:
    """The L2-normalised mean of a work item's chunk embeddings.

    §5.3: no new model call. ARCH-11 already embedded every chunk, and the
    mean of those is the document's position in the same space — free, and
    already paid for.

    The mean is taken over UNNORMALISED chunk vectors and normalised once at
    the end. Normalising each chunk first would weight a two-word heading
    exactly as heavily as a full page of terms, which is not what "where is
    this document" means.

    `math.fsum` rather than `sum`: 384 dimensions across a few hundred chunks
    accumulates enough error in binary floating point to move a cosine in the
    fourth decimal place, and the L3 threshold is 0.97 with findings clustered
    just above it.
    """
    if not vectors:
        return ()

    width = len(vectors[0])
    if width == 0:
        return ()
    for vector in vectors:
        if len(vector) != width:
            raise ValueError(
                "Chunk embeddings of differing width cannot be averaged. This "
                "means two embedding models have written into one work item's "
                "chunks — re-embed the document rather than averaging across "
                "two vector spaces, which produces a number with no meaning."
            )

    totals = [
        math.fsum(vector[index] for vector in vectors) / len(vectors)
        for index in range(width)
    ]
    return l2_normalise(totals)


def cosine(left: Sequence[float], right: Sequence[float]) -> Decimal:
    """Cosine similarity, via normalise-then-dot.

    Both sides are normalised defensively rather than trusted. The cost is two
    passes over 384 floats; the alternative is a similarity above 1.0 reaching
    a `numeric(6,5)` column and Postgres refusing the insert at the end of a
    sweep that has already done all its work.
    """
    if not left or not right:
        return Decimal("0")
    if len(left) != len(right):
        raise ValueError(
            f"Cannot compare embeddings of width {len(left)} and {len(right)}."
        )

    a = l2_normalise(left)
    b = l2_normalise(right)
    dot = math.fsum(x * y for x, y in zip(a, b))
    # Clamp: fsum over unit vectors can land a hair above 1.0.
    dot = max(-1.0, min(1.0, dot))
    return Decimal(str(dot)).quantize(Decimal("0.00001"))


# ===========================================================================
# Hashing and digests
# ===========================================================================


def content_sha256(payload: bytes) -> str:
    """SHA-256 of raw bytes, lower-case hex, 64 characters.

    Present so the gate has a callable, and so nothing in this package reaches
    for `hashlib` inline. Production reads the value ARCH-06 already stored on
    `uploaded_files.checksum_sha256` rather than re-reading the object out of
    storage to hash it again.
    """
    return hashlib.sha256(payload).hexdigest()


def _canonical(value: Any) -> Any:
    """Make a value JSON-canonical. Decimals become STRINGS.

    `float(Decimal("0.85"))` is 0.8500000000000000888..., and two machines
    that serialise it differently produce two digests for one input. The same
    rule ARCH-31 and ARCH-33 apply to their plans.
    """
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return [_canonical(item) for item in sorted(value)]
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value)}
    return value


def input_digest(payload: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON, including `ENGINE_VERSION`.

    The `input_digest` pattern, unchanged from ARCH-31 and ARCH-33. This is
    what makes the sweep idempotent: a re-run whose candidate set, thresholds
    and engine version are unchanged produces the same digest, finds the
    existing row, writes nothing and — the part that matters commercially —
    emits no usage event.

    `ENGINE_VERSION` is inside the digest rather than beside it so that
    bumping it invalidates every cached answer automatically. A version that
    sat outside would let a re-sweep under a new engine hit a stale row and
    report last month's conclusion as this month's.
    """
    body = dict(payload)
    body["engine_version"] = vocab.ENGINE_VERSION
    encoded = json.dumps(
        _canonical(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


# ===========================================================================
# Assembly
# ===========================================================================


def build(
    *,
    work_item_id: str,
    content_sha256_hex: str,
    vendor_key: Optional[str],
    document_number: Optional[str],
    shingle_text: str,
    chunks: Sequence[ChunkVector],
    line_count: int = 0,
    page_count: Optional[int] = None,
    document_date: Optional[date] = None,
    total_micros: Optional[int] = None,
    currency: Optional[str] = None,
    embedding_model: str = "",
) -> DocumentFingerprint:
    """Assemble one fingerprint. Pure: every input is already a plain value.

    `vendor_key` and `document_number` arrive ALREADY NORMALISED, through
    `app/core/normalize.py`, from the extraction that produced them. They are
    not re-derived here and this module does not import the normaliser — a
    second derivation is a second answer, and the whole point of L1 is that
    two documents agree on an identifier that exactly one function computes.
    """
    shingle_set = shingles(shingle_text)
    return DocumentFingerprint(
        work_item_id=str(work_item_id),
        content_sha256=str(content_sha256_hex or "").lower(),
        vendor_key=vendor_key or None,
        document_number=document_number or None,
        minhash=minhash_signature(shingle_set),
        embedding=mean_embedding([chunk.vector for chunk in chunks]),
        shingle_count=len(shingle_set),
        line_count=int(line_count),
        page_count=page_count,
        document_date=document_date,
        total_micros=total_micros,
        currency=currency,
        embedding_model=embedding_model,
    )
