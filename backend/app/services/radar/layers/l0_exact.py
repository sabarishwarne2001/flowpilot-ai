"""ARCH-34 §5.3 — L0: the same file arrived twice.

THE STRONGEST CLAIM THE RADAR MAKES, AND THE ONLY CERTAIN ONE
=============================================================

Two work items whose uploaded files have the same SHA-256 are the same bytes.
Not similar, not probably: the same. Every other layer produces an estimate
and this one produces a fact, which is why it is first in
`vocabulary.DUPLICATE_LAYER_ORDER` and why `severity_for` returns HIGH with no
band and no corroboration test.

WHAT THIS DELIBERATELY DOES NOT CATCH
=====================================

A rescan of the same paper invoice has different bytes. A re-download of the
same PDF through a portal that stamps a timestamp has different bytes. Neither
is an L0 match and neither should be — that is what L1, L2 and L3 are for.
§5.1's opening sentence is about the second copy arriving "renamed, rescanned,
or re-dated", and renamed is the only one of the three L0 sees.

The filename is not part of the comparison, which is what makes "renamed"
work: `uploaded_files.original_filename` is not hashed, only content is.

PURE
====

Standard library and this package's own vocabulary. No Session.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any, Optional

from app.services.radar import vocabulary as vocab

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.radar.layers import Candidate, LayerHit, LayerSettings

__all__ = ["LAYER", "evaluate"]

LAYER: str = vocab.LAYER_L0


def evaluate(
    subject: "Candidate",
    counterpart: "Candidate",
    *,
    settings: "LayerSettings",
) -> Optional["LayerHit"]:
    """Fire when both work items carry the same content hash."""
    from app.services.radar.layers import LayerHit  # noqa: PLC0415

    left = subject.fingerprint
    right = counterpart.fingerprint

    a = (left.content_sha256 or "").strip().lower()
    b = (right.content_sha256 or "").strip().lower()

    # An absent or malformed hash is not a match. `"" == ""` is True and would
    # pair every document whose upload predates ARCH-06's checksum column with
    # every other one — a backfill gap presenting itself as a fraud signal.
    if len(a) != 64 or len(b) != 64:
        return None
    if a != b:
        return None

    evidence: tuple[dict[str, Any], ...] = (
        {
            "kind": vocab.EVIDENCE_IDENTIFIERS,
            "label": "Content SHA-256",
            "subject": {
                "work_item_id": left.work_item_id,
                "value": a,
            },
            "counterpart": {
                "work_item_id": right.work_item_id,
                "value": b,
            },
            "note": "Both uploads are byte-for-byte identical files.",
        },
    )

    return LayerHit(
        layer=LAYER,
        score=Decimal("1.00000"),
        evidence=evidence,
        metrics={"content_sha256": a},
    )
