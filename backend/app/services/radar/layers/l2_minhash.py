"""ARCH-34 §5.3 — L2: the line items are the same document, re-typed.

WHAT L2 IS ACTUALLY FOR
=======================

The rescanned invoice. Different bytes, so L0 says nothing. A fresh document
number, or none extracted, so L1 says nothing. But the twelve line items are
the same twelve line items, and a MinHash over shingled line text says so with
a number a human can check.

This is the layer §5.6's headline comes from: "98% of line items match" is the
Jaccard estimate, and it is presented as an estimate because that is what it
is.

THE THRESHOLD IS A COMPARISON AND THE DIRECTION IS A MUTANT
===========================================================

`estimate >= settings.l2_jaccard_min`. Inverting it to `<=` produces an engine
that reports every pair of UNRELATED documents as duplicates and stays silent
on the real ones, and — this is the part worth writing down — the finding
count would go UP, which looks like the detector working. `verify_arch34.py`
requires that mutant to die.

THE THREE GUARDS, AND WHY ABSENCE DOES NOT DISQUALIFY
=====================================================

§5.3 gates L2 on same vendor, totals within 0.5% and dates within 45 days, in
addition to the Jaccard threshold. Those guards exist because line text alone
is not enough: a supplier who bills the same recurring services every month
produces near-identical line items every month, legitimately, and without the
guards L2 would report each month's invoice as a duplicate of the last.

Every guard is applied ONLY WHEN BOTH SIDES CARRY THE VALUE. An invoice whose
total could not be extracted is not evidence of a different total. Treating it
as disqualifying would make L2's recall a function of extraction quality, so a
supplier whose PDF layout defeats total extraction would quietly stop
producing duplicate findings — and nothing anywhere would look broken.

Differing currencies DO disqualify, because integer micros make ₹50,000 and
$50,000 look like the same number.

PURE
====

Standard library, `vocabulary` and `fingerprint`. No Session.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from app.services.radar import fingerprint as fp
from app.services.radar import vocabulary as vocab

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.radar.layers import Candidate, LayerHit, LayerSettings

__all__ = ["LAYER", "evaluate"]

LAYER: str = vocab.LAYER_L2


def evaluate(
    subject: "Candidate",
    counterpart: "Candidate",
    *,
    settings: "LayerSettings",
) -> Optional["LayerHit"]:
    """Fire on a MinHash Jaccard estimate at or above the threshold."""
    from app.services.radar.layers import (  # noqa: PLC0415
        LayerHit,
        _dates_compatible,
        _totals_compatible,
    )

    left = subject.fingerprint
    right = counterpart.fingerprint

    # The empty-document trap. Two documents with no shingles have identical
    # all-sentinel signatures and would estimate to 1.0. `jaccard_estimate`
    # returns None rather than a number; this is the caller that must respect
    # it, and returning None here is what keeps failed OCR out of the queue.
    estimate = fp.jaccard_estimate(
        left.minhash,
        right.minhash,
        left_shingles=left.shingle_count,
        right_shingles=right.shingle_count,
    )
    if estimate is None:
        return None

    if estimate < settings.l2_jaccard_min:
        return None

    # Same-vendor guard. Only when both sides know their vendor: an
    # unextracted vendor is missing evidence, not contrary evidence.
    if left.vendor_key and right.vendor_key and left.vendor_key != right.vendor_key:
        return None

    totals_ok, total_gap = _totals_compatible(left, right, settings.total_tolerance)
    if not totals_ok:
        return None

    dates_ok, date_gap = _dates_compatible(left, right, settings.date_window_days)
    if not dates_ok:
        return None

    shared = fp.matched_shingles(subject.shingles, counterpart.shingles)

    evidence: list[dict[str, Any]] = [
        {
            "kind": vocab.EVIDENCE_SHINGLES,
            "label": "Matching line text",
            "jaccard_estimate": str(estimate),
            "permutations": vocab.MINHASH_PERMUTATIONS,
            "subject": {
                "work_item_id": left.work_item_id,
                "shingle_count": left.shingle_count,
                "line_count": left.line_count,
            },
            "counterpart": {
                "work_item_id": right.work_item_id,
                "shingle_count": right.shingle_count,
                "line_count": right.line_count,
            },
            "samples": list(shared),
            "note": (
                "Estimated share of line-item phrases present on both "
                f"documents, over {vocab.MINHASH_PERMUTATIONS} hash "
                "permutations."
            ),
        }
    ]

    # The guards that PASSED are evidence too, and the console renders them as
    # §5.6's plain-words line: "Same vendor, amount within 0.5%, dated 58 days
    # apart". A guard that was not measured is reported as not measured rather
    # than silently omitted, so a reviewer can tell "the totals agree" from
    # "there was no total to compare".
    guard: dict[str, Any] = {
        "kind": vocab.EVIDENCE_IDENTIFIERS,
        "label": "Corroborating header fields",
        "vendor_key": left.vendor_key or right.vendor_key,
        "vendor_compared": bool(left.vendor_key and right.vendor_key),
        "total_relative_gap": None if total_gap is None else str(total_gap),
        "total_tolerance": str(settings.total_tolerance),
        "date_gap_days": date_gap,
        "date_window_days": settings.date_window_days,
    }
    evidence.append(guard)

    return LayerHit(
        layer=LAYER,
        score=estimate,
        evidence=tuple(evidence),
        metrics={
            "jaccard_estimate": str(estimate),
            "permutations": vocab.MINHASH_PERMUTATIONS,
            "matched_samples": len(shared),
            "total_relative_gap": None if total_gap is None else str(total_gap),
            "date_gap_days": date_gap,
        },
    )
