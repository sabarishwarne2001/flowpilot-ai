"""ARCH-34 §5.3 — duplicate detection, layered exact to fuzzy, stopping early.

THE RULE THIS PACKAGE EXISTS TO ENFORCE
=======================================

**A finding reports the STRONGEST layer that fired, not every layer that
fired.**

Every byte-identical pair is also a vendor+number match, also a near-duplicate
by MinHash, and also a semantic match by cosine. All four are true. Reporting
all four produces four rows about one pair of documents, and a person who
confirms the first still has three accusations left to dismiss — so the
feature that was supposed to catch a duplicated payment instead generates
three units of work per real finding.

`strongest()` walks `vocabulary.DUPLICATE_LAYER_ORDER` and returns the FIRST
hit. `all_hits()` exists for exactly two callers: the corroboration test that
lifts an L3 finding to HIGH, and `verify_arch34.py`, which needs to prove that
a byte-identical pair WOULD have fired at L2 and L3 and was nevertheless
reported as L0 only. A gate that only checked "L0 fired" would pass against an
engine with no early exit at all.

PURE
====

Standard library, `app/services/radar/vocabulary.py` and
`app/services/radar/fingerprint.py`. No `Session`, no clock, no settings. The
impure sweep loads rows, converts them to `Candidate` values at its boundary,
and everything from there down is gateable offline.

WHY EVERY LAYER RETURNS EVIDENCE AND NOT JUST A SCORE
=====================================================

`anomaly_findings.evidence` is `NOT NULL` with `CHECK (jsonb_array_length(
evidence) > 0)`, and the reason is in the product rather than the schema: this
radar tells a finance person that an invoice they are about to pay may already
have been paid. A finding with a score and no evidence is an accusation, and
the person receiving it has no way to check it except to open both documents
and read them — which is the work the radar was supposed to do.

So each layer returns what a human would look at to decide the same question:
the two identifiers for L0 and L1, the matching phrases for L2, the two most
similar paragraphs for L3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Optional, Sequence

from app.services.radar import vocabulary as vocab
from app.services.radar.fingerprint import ChunkVector, DocumentFingerprint

__all__ = [
    "Candidate",
    "LayerHit",
    "LayerSettings",
    "DEFAULT_SETTINGS",
    "evaluate_layer",
    "all_hits",
    "strongest",
    "LAYER_MODULES",
]


# ===========================================================================
# Pure carriers
# ===========================================================================


@dataclass(frozen=True)
class Candidate:
    """One document, in the form the layers compare.

    `shingles` and `chunks` are OPTIONAL and default to empty. A sweep that
    has not loaded them still gets L0 and L1 — which are index lookups and the
    two strongest layers — and L2/L3 simply do not fire. Degrading to fewer
    layers is correct; inventing an empty shingle set and comparing it is not,
    and is exactly the empty-document trap `fingerprint.py` documents.
    """

    fingerprint: DocumentFingerprint
    shingles: frozenset[str] = frozenset()
    chunks: tuple[ChunkVector, ...] = ()

    @property
    def work_item_id(self) -> str:
        return self.fingerprint.work_item_id


@dataclass(frozen=True)
class LayerHit:
    """What one layer concluded about one pair.

    `score` is the layer's own natural quantity, in [0, 1]: 1 for the exact
    layers, the Jaccard estimate for L2, the cosine for L3. It lands in
    `anomaly_findings.score numeric(6,5)` unchanged, so the number a reviewer
    sees is the number the layer computed rather than a rescaled version of
    it that nobody can reproduce.
    """

    layer: str
    score: Decimal
    evidence: tuple[dict[str, Any], ...]
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.layer not in vocab.LAYERS:
            raise ValueError(f"{self.layer!r} is not a known radar layer.")
        if not self.evidence:
            raise ValueError(
                f"A {self.layer} hit was constructed with no evidence. "
                "`ck_af_evidence_present` would refuse the row, and the "
                "console has nothing to render. A layer that cannot show its "
                "work must return None rather than a bare score — see the "
                "'evidence assembled but not attached' mutant in "
                "verify_arch34.py."
            )
        if not (Decimal("0") <= self.score <= Decimal("1")):
            raise ValueError(
                f"A layer score must be in [0, 1]; got {self.score}. "
                "`anomaly_findings.score` is numeric(6,5)."
            )


@dataclass(frozen=True)
class LayerSettings:
    """Thresholds, in one frozen value that goes into the `input_digest`.

    Passed explicitly rather than read from `vocabulary` inside each layer, so
    that a tenant override and a gate fixture travel the same path. A layer
    that reached for the module constant directly would be untunable and, more
    importantly, would not change the digest when a tenant tuned it — and a
    sweep whose thresholds changed but whose digest did not is a sweep that
    returns yesterday's answer forever.
    """

    l2_jaccard_min: Decimal = vocab.L2_JACCARD_MIN
    l3_cosine_min: Decimal = vocab.L3_COSINE_MIN
    total_tolerance: Decimal = vocab.L2_TOTAL_TOLERANCE
    date_window_days: int = vocab.L2_DATE_WINDOW_DAYS
    #: §5.7: Business plans receive L0..L2 only; L3 is Enterprise. The sweep
    #: passes the tenant's resolved set, so the entitlement decision is made
    #: once, by the caller that can see the tier, rather than by four layers
    #: each re-asking.
    enabled_layers: tuple[str, ...] = vocab.DUPLICATE_LAYER_ORDER

    def as_digest_payload(self) -> dict[str, Any]:
        return {
            "l2_jaccard_min": self.l2_jaccard_min,
            "l3_cosine_min": self.l3_cosine_min,
            "total_tolerance": self.total_tolerance,
            "date_window_days": self.date_window_days,
            "enabled_layers": list(self.enabled_layers),
        }


DEFAULT_SETTINGS: LayerSettings = LayerSettings()


# ===========================================================================
# Dispatch
# ===========================================================================


def _module_for(layer: str) -> Any:
    """The module implementing `layer`, refusing an unknown one loudly."""
    from app.services.radar.layers import (  # noqa: PLC0415
        l0_exact,
        l1_identifier,
        l2_minhash,
        l3_embedding,
    )

    registry = {
        vocab.LAYER_L0: l0_exact,
        vocab.LAYER_L1: l1_identifier,
        vocab.LAYER_L2: l2_minhash,
        vocab.LAYER_L3: l3_embedding,
    }
    if layer not in registry:
        raise ValueError(
            f"{layer!r} has no duplicate layer implementation. PRICE_SURGE is "
            "in price_surge.py and CONTRACT_DRIFT is in drift.py; neither is "
            "a duplicate layer and neither is ranked against these four."
        )
    return registry[layer]


#: Every layer this package implements. Asserted equal to
#: `vocabulary.DUPLICATE_LAYER_ORDER` by `verify_arch34.py`, so a fifth layer
#: added to the vocabulary without a module fails a gate rather than raising a
#: ValueError inside a worker at 02:00.
LAYER_MODULES: tuple[str, ...] = vocab.DUPLICATE_LAYER_ORDER


def evaluate_layer(
    layer: str,
    subject: Candidate,
    counterpart: Candidate,
    *,
    settings: LayerSettings = DEFAULT_SETTINGS,
) -> Optional[LayerHit]:
    """Run one layer against one pair."""
    return _module_for(layer).evaluate(subject, counterpart, settings=settings)


def all_hits(
    subject: Candidate,
    counterpart: Candidate,
    *,
    settings: LayerSettings = DEFAULT_SETTINGS,
) -> tuple[LayerHit, ...]:
    """Every layer that fires, strongest first. NOT what gets reported.

    Two callers only: the corroboration test in `severity_for`, and the gate.
    Anything that writes a finding calls `strongest()`.
    """
    hits: list[LayerHit] = []
    for layer in vocab.DUPLICATE_LAYER_ORDER:
        if layer not in settings.enabled_layers:
            continue
        hit = evaluate_layer(layer, subject, counterpart, settings=settings)
        if hit is not None:
            hits.append(hit)
    return tuple(hits)


def strongest(
    subject: Candidate,
    counterpart: Candidate,
    *,
    settings: LayerSettings = DEFAULT_SETTINGS,
) -> Optional[LayerHit]:
    """The one finding this pair produces, or None.

    Walks strongest to weakest and RETURNS ON THE FIRST HIT. The loop below is
    the early exit: removing the `return` and collecting instead is the mutant
    `verify_arch34.py` requires to die, and it is a mutant that would pass
    every test that only asserted "a duplicate was found".

    A pair is never compared with itself. `subject is counterpart` would
    produce a perfect L0 match on every document in the workspace, and the
    unique index would not catch it because `(A, A)` is a distinct pair from
    every `(A, B)`.
    """
    if subject.work_item_id == counterpart.work_item_id:
        return None

    for layer in vocab.DUPLICATE_LAYER_ORDER:
        if layer not in settings.enabled_layers:
            continue
        hit = evaluate_layer(layer, subject, counterpart, settings=settings)
        if hit is not None:
            return hit
    return None


# ===========================================================================
# Shared helpers
# ===========================================================================


def _clip(text: Optional[str], limit: int = vocab.MAX_EVIDENCE_TEXT) -> str:
    """Trim a quote to something a finding can carry and a person can read."""
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[: limit - 1].rstrip() + "\u2026"


def _totals_compatible(
    left: DocumentFingerprint, right: DocumentFingerprint, tolerance: Decimal
) -> tuple[bool, Optional[Decimal]]:
    """Whether two totals are within tolerance, and the relative gap.

    ABSENCE IS NOT DISQUALIFYING. When either side has no extracted total, the
    answer is `(True, None)` — compatible, unmeasured. The alternative makes
    L2 depend on how well extraction did rather than on what the documents
    say, so a supplier whose PDFs defeat total extraction would quietly never
    produce a duplicate finding, and nothing would look broken.

    Differing currencies ARE disqualifying. ₹50,000 and $50,000 are not
    within 0.5% of each other; they are not comparable at all, and the integer
    micros make them look identical.
    """
    if left.total_micros is None or right.total_micros is None:
        return True, None
    if left.currency and right.currency and left.currency != right.currency:
        return False, None

    a = Decimal(left.total_micros)
    b = Decimal(right.total_micros)
    largest = max(abs(a), abs(b))
    if largest == 0:
        return True, Decimal("0")
    gap = (abs(a - b) / largest).quantize(Decimal("0.00001"))
    return gap <= tolerance, gap


def _dates_compatible(
    left: DocumentFingerprint, right: DocumentFingerprint, window_days: int
) -> tuple[bool, Optional[int]]:
    """Whether two document dates are within the window, and the gap in days.

    Same absence rule as totals, for the same reason.
    """
    if left.document_date is None or right.document_date is None:
        return True, None
    gap = abs((left.document_date - right.document_date).days)
    return gap <= window_days, gap
