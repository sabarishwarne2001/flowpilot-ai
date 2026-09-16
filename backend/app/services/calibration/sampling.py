"""ARCH-35 §6.4 — audit sampling: the reviews that keep the guarantee checkable.

WHY IT EXISTS
=============

Everything above the threshold is approved without a human. If nothing above
the threshold were ever reviewed, every new label would come from below it,
the calibration set would stop describing what is actually approved, and the
realized-rate check in `monitor.py` would have nothing to read. The guarantee
would not become false; it would become unfalsifiable, which is worse.

So a share of automatic passes (default 2%, never below 1%) goes to review
anyway, labelled as an audit in the queue.

WHY IT IS DETERMINISTIC
=======================

"Random" here means "unpredictable to the tenant and uncorrelated with the
document", not "different every time it is asked". The decision is a hash of
the model version and a stable key for the decision (the verification id, or
an assertion's definition, work item and node run). Three consequences:

  * a retried worker makes the same choice, so a document cannot be approved
    on the first attempt and audited on the second;
  * the harvester can recompute the choice when it labels the review;
  * `verify_arch35.py` can check the rate against a binomial tolerance on a
    large synthetic set without a random seed anywhere.

A new model version re-draws the sample, which is intended: the audit is a
property of the promise in force.

PURE
====

hashlib only.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal
from typing import Any

from app.services.calibration import vocabulary as vocab

__all__ = ["is_audit_sample", "audit_weight", "unit_draw"]

_SCALE: int = 1 << 64


def unit_draw(model_id: Any, sample_key: Any) -> float:
    """A uniform number in [0, 1) derived from the pair, and nothing else."""
    digest = hashlib.sha256(
        f"arch35-audit|{model_id}|{sample_key}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big") / _SCALE


def is_audit_sample(model_id: Any, sample_key: Any, rate: Any) -> bool:
    """Whether this automatic pass is sent to review as an audit."""
    value = float(Decimal(str(rate)))
    floor = float(Decimal(vocab.AUDIT_SAMPLE_RATE_MIN))
    ceiling = float(Decimal(vocab.AUDIT_SAMPLE_RATE_MAX))
    value = min(ceiling, max(floor, value))
    return unit_draw(model_id, sample_key) < value


def audit_weight(rate: Any) -> Decimal:
    """1 / r: how many automatic passes one audit label stands for."""
    value = Decimal(str(rate))
    if value <= 0:
        raise ValueError("an audit rate must be positive")
    return (Decimal(1) / value).quantize(Decimal("0.001"))
