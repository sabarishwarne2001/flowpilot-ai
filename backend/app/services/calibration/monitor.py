"""ARCH-35 §6.4 — drift monitoring: when a promise stops being a promise.

Two checks, either of which suspends automatic approval for a decision type:

  1. POPULATION STABILITY. PSI between the production score distribution in
     the window before the fit (the reference, stored on the model) and the
     one since. Above 0.25 the documents arriving are not the documents the
     guarantee was computed on, and exchangeability — the one assumption the
     conformal bound rests on — is gone.

  2. REALIZED ERROR. Among the last 100 reviewed automatic passes (audit
     samples, in practice) since the model was fitted, is the error rate
     significantly above the model's own Clopper-Pearson upper bound? A
     one-sided binomial test at 5%, not a raw comparison: one wrong audit out
     of one is a realized rate of 100%, and suspending a tenant's automation
     on a single draw is noise dressed as vigilance.

Suspension is not an error state. It is the model in force saying "no", and
it is lifted only by a fresh fit on NEW reviewed evidence — see
`refit.resume` and `RESUME_MIN_NEW_LABELS`. Autonomy is earned back, not waited
out.

WHY THE REFERENCE IS PRODUCTION SCORES AND NOT LABELLED SCORES
==============================================================

Labels come overwhelmingly from documents that were sent to review, which are
the LOW-scoring ones. Comparing that biased reference against every document
scored this week would report drift on every tenant, every night, forever.
Reference and sample are both drawn from all decisions of the type, so PSI
compares like with like.

PURE
====

NumPy and SciPy. `now` and `fitted_at` are arguments, never read from a clock.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from app.services.calibration import vocabulary as vocab

__all__ = [
    "DriftVerdict",
    "histogram",
    "psi",
    "psi_from_scores",
    "realized_rate_breach",
    "drift_verdict",
    "is_stale",
]


def histogram(
    scores: Sequence[float], bins: int = vocab.PSI_BINS
) -> list[float]:
    """Proportions over `bins` equal-width bins on [0, 1]. Empty -> zeros."""
    count = max(1, int(bins))
    counts = [0] * count
    total = 0
    for raw in scores:
        value = float(raw)
        if value != value:
            continue
        value = min(1.0, max(0.0, value))
        index = min(count - 1, int(value * count))
        counts[index] += 1
        total += 1
    if total == 0:
        return [0.0] * count
    return [c / total for c in counts]


def psi(
    expected: Sequence[float],
    actual: Sequence[float],
    epsilon: float = vocab.PSI_EPSILON,
) -> float:
    """Σ (a - e) · ln(a / e) over bins, with empty bins floored at `epsilon`.

    Symmetric in its arguments and zero only when the two distributions agree
    bin for bin. `[0.5, 0.5]` against `[0.25, 0.75]` is 0.274653.
    """
    if len(expected) != len(actual):
        raise ValueError("PSI needs two histograms over the same bins")
    total = 0.0
    for e, a in zip(expected, actual):
        e = max(float(e), epsilon)
        a = max(float(a), epsilon)
        total += (a - e) * math.log(a / e)
    return total


def psi_from_scores(
    reference: Sequence[float],
    recent: Sequence[float],
    bins: int = vocab.PSI_BINS,
) -> float:
    return psi(histogram(reference, bins), histogram(recent, bins))


def realized_rate_breach(
    wrong: int,
    total: int,
    bound: float,
    significance: float = vocab.MONITOR_SIGNIFICANCE,
) -> tuple[bool, float]:
    """`(breached, p_value)` for H0: true error rate ≤ bound.

    Breached only when the observed rate is above the bound AND the chance of
    seeing at least this many errors at the bound is below `significance`.
    """
    from scipy.stats import binom

    wrong = int(wrong)
    total = int(total)
    bound = float(bound)
    if total <= 0 or wrong <= 0 or bound >= 1.0:
        return False, 1.0
    bound = max(bound, 0.0)
    p_value = float(binom.sf(wrong - 1, total, bound))
    breached = (wrong / total) > bound and p_value < significance
    return breached, p_value


@dataclass(frozen=True)
class DriftVerdict:
    suspend: bool
    reason: str = ""
    psi: Optional[float] = None
    psi_checked: bool = False
    realized_wrong: int = 0
    realized_total: int = 0
    realized_p_value: float = 1.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def as_payload(self) -> dict[str, Any]:
        return {
            "suspend": self.suspend,
            "reason": self.reason,
            "psi": None if self.psi is None else round(self.psi, 6),
            "psi_checked": self.psi_checked,
            "realized_wrong": self.realized_wrong,
            "realized_total": self.realized_total,
            "realized_p_value": round(self.realized_p_value, 6),
            "notes": list(self.notes),
        }


def drift_verdict(
    *,
    reference_histogram: Sequence[float],
    reference_count: int,
    recent_scores: Sequence[float],
    realized_wrong: int,
    realized_total: int,
    bound: float,
    when: str,
) -> DriftVerdict:
    """Both checks, and the one-sentence reason the console shows."""
    notes: list[str] = []
    psi_value: Optional[float] = None
    psi_checked = False

    if (
        reference_count >= vocab.MIN_PSI_SAMPLES
        and len(recent_scores) >= vocab.MIN_PSI_SAMPLES
        and len(reference_histogram) == vocab.PSI_BINS
    ):
        psi_value = psi(reference_histogram, histogram(recent_scores))
        psi_checked = True
    else:
        notes.append(
            "score-shift check skipped: fewer than "
            f"{vocab.MIN_PSI_SAMPLES} scores on one side"
        )

    breached, p_value = realized_rate_breach(realized_wrong, realized_total, bound)

    if psi_checked and psi_value is not None and psi_value > vocab.PSI_THRESHOLD:
        return DriftVerdict(
            suspend=True,
            reason=(
                f"Paused on {when}: the confidence scores of recent documents "
                f"shifted away from the ones the limit was measured on "
                f"(stability index {psi_value:.2f}, limit "
                f"{vocab.PSI_THRESHOLD:.2f}). Everything goes to review until "
                "the next check on newly reviewed documents passes."
            ),
            psi=psi_value,
            psi_checked=True,
            realized_wrong=realized_wrong,
            realized_total=realized_total,
            realized_p_value=p_value,
            notes=tuple(notes),
        )

    if breached:
        return DriftVerdict(
            suspend=True,
            reason=(
                f"Paused on {when}: {realized_wrong} of the last "
                f"{realized_total} audited automatic approvals were wrong, "
                f"more than the {bound * 100:.1f}% the limit allows. "
                "Everything goes to review until the next check on newly "
                "reviewed documents passes."
            ),
            psi=psi_value,
            psi_checked=psi_checked,
            realized_wrong=realized_wrong,
            realized_total=realized_total,
            realized_p_value=p_value,
            notes=tuple(notes),
        )

    return DriftVerdict(
        suspend=False,
        psi=psi_value,
        psi_checked=psi_checked,
        realized_wrong=realized_wrong,
        realized_total=realized_total,
        realized_p_value=p_value,
        notes=tuple(notes),
    )


def is_stale(
    last_checked_at: Optional[datetime],
    now: datetime,
    max_age_hours: int = vocab.STALE_AFTER_HOURS,
) -> bool:
    """A model nobody has monitored recently promises nothing."""
    if last_checked_at is None:
        return True
    return (now - last_checked_at) > timedelta(hours=max_age_hours)
