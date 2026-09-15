"""ARCH-34 §5.3 — L1: the same vendor issued this document number twice.

NEAR-CERTAINTY, AND WHY IT IS NOT CERTAINTY
===========================================

A supplier's own numbering is meant to be unique within that supplier. Two
documents carrying the same `vendor_key` and the same normalised
`document_number` are, in almost every case, the same document — a re-send, a
re-upload, or a second copy that arrived by a different route.

It is not certain because numbering is a human system. A supplier who resets
their invoice series each financial year will legitimately issue INV-001
twice, twelve months apart. That case is real and this layer will flag it; the
answer is the suppression, not a weaker rule. A reviewer says "this is fine,
and here is why" once, `anomaly_suppressions` records the pair and the reason,
and the annual reset stops resurfacing.

Weakening the layer instead — say, by also requiring the dates to be close —
would trade a suppressible false positive for a silent false negative, and the
false negative is a duplicated payment.

BOTH SIDES OF THE COMPARISON COME FROM `app/core/normalize.py`
==============================================================

`vendor_key` and `document_number` are computed exactly once, by the
extraction that produced them, through the single normaliser. This module
compares the stored values and does not re-derive them.

That is the whole point of L1. "Acme Pvt. Ltd." and "ACME PRIVATE LIMITED"
resolve to one vendor key because one function says so; "INV/2025/0982" and
"inv-2025-982" resolve to one document number for the same reason. A second
derivation here would be a second opinion, and the layer would start
disagreeing with the index that found the candidate.

PURE
====

Standard library and this package's own vocabulary. No Session, and
deliberately no import of the normaliser either — an import would be an
invitation to re-derive.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from app.services.radar import vocabulary as vocab

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.radar.layers import Candidate, LayerHit, LayerSettings

__all__ = ["LAYER", "evaluate"]

LAYER: str = vocab.LAYER_L1


def evaluate(
    subject: "Candidate",
    counterpart: "Candidate",
    *,
    settings: "LayerSettings",
) -> Optional["LayerHit"]:
    """Fire when vendor key and document number both match and both exist."""
    from app.services.radar.layers import LayerHit  # noqa: PLC0415

    left = subject.fingerprint
    right = counterpart.fingerprint

    vendor = (left.vendor_key or "").strip()
    number = (left.document_number or "").strip()
    other_vendor = (right.vendor_key or "").strip()
    other_number = (right.document_number or "").strip()

    # BOTH must be present on BOTH sides. Two documents from which no vendor
    # could be extracted would otherwise match on `"" == ""`, and a workspace
    # whose supplier names sit in an image header would report every pair of
    # documents as a duplicate of every other. An unextracted field is missing
    # evidence, never matching evidence.
    if not vendor or not number or not other_vendor or not other_number:
        return None
    if vendor != other_vendor or number != other_number:
        return None

    evidence: tuple[dict[str, Any], ...] = (
        {
            "kind": vocab.EVIDENCE_IDENTIFIERS,
            "label": "Vendor",
            "subject": {"work_item_id": left.work_item_id, "value": vendor},
            "counterpart": {"work_item_id": right.work_item_id, "value": other_vendor},
            "note": "Normalized supplier identity is the same on both documents.",
        },
        {
            "kind": vocab.EVIDENCE_IDENTIFIERS,
            "label": "Document number",
            "subject": {"work_item_id": left.work_item_id, "value": number},
            "counterpart": {"work_item_id": right.work_item_id, "value": other_number},
            "note": "Normalized document number is the same on both documents.",
        },
    )

    return LayerHit(
        layer=LAYER,
        score=Decimal("1.00000"),
        evidence=evidence,
        metrics={"vendor_key": vendor, "document_number": number},
    )
