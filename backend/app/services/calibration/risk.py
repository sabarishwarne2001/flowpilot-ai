"""ARCH-35 §6.4 — split conformal risk control, and the bound a buyer reads.

THE THRESHOLD
=============

Given n held-out examples with calibrated probabilities p_i, the automatic
approval threshold λ is the SMALLEST value for which

    ( Σ_i 1[eligible_i and p_i ≥ λ and wrong_i] + 1 ) / ( n + 1 )  ≤  α

Under exchangeability this bounds the expected share of future documents that
are approved automatically AND wrong by α (Angelopoulos et al., "Conformal
Risk Control"). The `+1` in numerator and denominator is the finite-sample
correction: it is the unseen test point, counted as a loss of 1 because we do
not know it is not one. Dropping it makes the bound hold on the calibration
set and fail on the next document — `verify_arch35.py` computes a table by
hand where the two answers differ, and a mutant that removes it must die.

The loss is non-increasing in λ, so the set of λ that satisfy the bound is an
upper set and "smallest" is well defined: scan the eligible probabilities from
the top down while the bound still holds.

WHAT THE GUARANTEE IS, AND WHAT IT IS NOT
=========================================

It is a bound on the EXPECTATION over calibration sets. It is not a statement
that holds with 95% probability for this particular calibration set. A gate
that demanded "realized risk ≤ α in 95 of 100 seeds" would be testing a
property this method does not have; `verify_arch35.py` gates the mean over
seeds for this bound, and gates 95-of-100 coverage on Clopper-Pearson, which
is the bound that does carry a confidence level.

ELIGIBILITY
===========

An assertion FAIL is a labelled example — the reviewer said whether the
engine was right — but the platform never approves a FAIL automatically, so
its loss is zero in production whatever its probability. `eligible_i` keeps
those examples in n (they are documents) and out of the numerator and the
auto share (they cannot pass).

WEIGHTS
=======

Items above the threshold are approved without review, so the only labels
from that region are AUDIT samples, taken at rate r. Counting each audit once
would under-represent the approved region by 1/r and make the bound
optimistic by the same factor. Each audit label carries weight 1/r — the
Horvitz-Thompson correction — and every sum above is a weighted sum. With
unit weights every formula reduces exactly to the textbook one, which is the
case the hand-computed gate checks.

Clopper-Pearson takes counts. With weights it uses the Kish effective sample
size, m_eff = (Σw)² / Σw², and the weighted error share; with unit weights
that is exactly the classical interval.

PURE
====

NumPy and SciPy. No Session, no clock, no random source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.services.calibration import vocabulary as vocab

__all__ = [
    "RiskControl",
    "CurvePoint",
    "conformal_bound",
    "conformal_threshold",
    "clopper_pearson_upper",
    "coverage_curve",
    "not_achievable_message",
]


@dataclass(frozen=True)
class CurvePoint:
    """One setting on the error-versus-coverage curve."""

    threshold: float
    #: Weighted share of documents that would pass automatically.
    auto_share: float
    #: The conformal bound at this threshold: (k + 1) / (n + 1).
    conformal_bound: float
    #: One-sided Clopper-Pearson upper bound on error among automatic passes.
    clopper_pearson_upper: float
    #: Weighted observed error among the examples that would pass.
    observed_error: float
    #: Unweighted count of held-out examples that would pass.
    passed: int

    def as_payload(self) -> dict[str, float | int]:
        return {
            "threshold": round(self.threshold, 7),
            "auto_share": round(self.auto_share, 5),
            "conformal_bound": round(self.conformal_bound, 5),
            "clopper_pearson_upper": round(self.clopper_pearson_upper, 5),
            "observed_error": round(self.observed_error, 5),
            "passed": int(self.passed),
        }


@dataclass(frozen=True)
class RiskControl:
    """The outcome of the threshold search at one α."""

    target_error_rate: float
    achievable: bool
    #: λ. 1.0 when nothing may pass: probabilities are clamped below 1.
    threshold: float
    #: (k + 1) / (n + 1) at λ; when not achievable, the tightest bound this
    #: evidence can support (k = 0), which is what the refusal quotes.
    conformal_bound: float
    clopper_pearson_upper: float
    auto_share: float
    #: Weighted n, weighted k, unweighted count passed.
    n: float
    k: float
    passed: int
    reason: str = ""
    curve: tuple[CurvePoint, ...] = field(default_factory=tuple)


def conformal_bound(k: float, n: float) -> float:
    """(k + 1) / (n + 1). The finite-sample correction is the two `+ 1`s."""
    return (float(k) + 1.0) / (float(n) + 1.0)


def clopper_pearson_upper(
    k: float,
    m: float,
    confidence: float = vocab.CLOPPER_PEARSON_CONFIDENCE,
) -> float:
    """One-sided upper bound on a binomial rate at `confidence`.

    `k` errors in `m` trials. Zero trials is no evidence at all and returns
    1.0. Zero errors in four trials returns about 0.527, not 0.
    """
    from scipy.stats import beta

    k = float(k)
    m = float(m)
    if m <= 0:
        return 1.0
    if k < 0 or k > m:
        raise ValueError(f"k={k} errors in m={m} trials is not a binomial count")
    if k >= m:
        return 1.0
    value = float(beta.ppf(confidence, k + 1.0, m - k))
    if value != value:
        return 1.0
    return min(1.0, max(0.0, value))


def _weighted_cp(wrong_weight: float, total_weight: float, sum_sq: float) -> float:
    if total_weight <= 0 or sum_sq <= 0:
        return 1.0
    m_eff = (total_weight * total_weight) / sum_sq
    k_eff = (wrong_weight / total_weight) * m_eff
    return clopper_pearson_upper(k_eff, m_eff)


def _normalise(
    probabilities: Sequence[float],
    wrong: Sequence[bool],
    weights: Optional[Sequence[float]],
    eligible: Optional[Sequence[bool]],
) -> tuple[list[float], list[bool], list[float], list[bool]]:
    p = [float(v) for v in probabilities]
    x = [bool(v) for v in wrong]
    w = [1.0] * len(p) if weights is None else [float(v) for v in weights]
    e = [True] * len(p) if eligible is None else [bool(v) for v in eligible]
    if not (len(p) == len(x) == len(w) == len(e)):
        raise ValueError("probabilities, labels, weights and eligibility differ in length")
    if any(v <= 0 for v in w):
        raise ValueError("weights must be positive")
    return p, x, w, e


def _sweep(
    p: list[float], x: list[bool], w: list[float], e: list[bool]
) -> list[tuple[float, float, float, float, float, int]]:
    """Per distinct eligible probability, descending:
    (λ, k_weight, pass_weight, pass_weight_sq, wrong_weight_among_passed, passed)."""
    order = sorted(
        (i for i in range(len(p)) if e[i]), key=lambda i: (-p[i], i)
    )
    points: list[tuple[float, float, float, float, float, int]] = []
    k_w = 0.0
    pass_w = 0.0
    pass_sq = 0.0
    passed = 0
    index = 0
    while index < len(order):
        level = p[order[index]]
        while index < len(order) and p[order[index]] == level:
            i = order[index]
            pass_w += w[i]
            pass_sq += w[i] * w[i]
            passed += 1
            if x[i]:
                k_w += w[i]
            index += 1
        points.append((level, k_w, pass_w, pass_sq, k_w, passed))
    return points


def conformal_threshold(
    probabilities: Sequence[float],
    wrong: Sequence[bool],
    target_error_rate: float,
    *,
    weights: Optional[Sequence[float]] = None,
    eligible: Optional[Sequence[bool]] = None,
    with_curve: bool = False,
) -> RiskControl:
    """The smallest λ whose conformal bound is at most α, or an honest refusal."""
    alpha = float(target_error_rate)
    if not (0.0 < alpha < 1.0):
        raise ValueError(f"α must lie in (0, 1); got {alpha}")

    p, x, w, e = _normalise(probabilities, wrong, weights, eligible)
    n = float(sum(w))
    sweep = _sweep(p, x, w, e)
    curve = coverage_curve_from_sweep(sweep, n) if with_curve else ()

    best: Optional[tuple[float, float, float, float, float, int]] = None
    for point in sweep:
        level, k_w = point[0], point[1]
        if conformal_bound(k_w, n) <= alpha:
            best = point
        else:
            break

    if best is None:
        tightest = conformal_bound(0.0, n)
        if not sweep:
            reason = (
                "No reviewed example in the calibration set could have passed "
                "automatically, so there is no evidence to base a promise on."
            )
        elif tightest <= alpha:
            reason = (
                f"Even the highest-confidence reviewed documents were wrong too "
                f"often for a {_pct(alpha)} limit, so everything goes to review "
                "until the engine's confident answers are reliably right."
            )
        else:
            reason = not_achievable_message(alpha, tightest)
        return RiskControl(
            target_error_rate=alpha,
            achievable=False,
            threshold=1.0,
            conformal_bound=min(1.0, tightest),
            clopper_pearson_upper=1.0,
            auto_share=0.0,
            n=n,
            k=0.0,
            passed=0,
            reason=reason,
            curve=tuple(curve),
        )

    level, k_w, pass_w, pass_sq, wrong_w, passed = best
    return RiskControl(
        target_error_rate=alpha,
        achievable=True,
        threshold=level,
        conformal_bound=conformal_bound(k_w, n),
        clopper_pearson_upper=_weighted_cp(wrong_w, pass_w, pass_sq),
        auto_share=(pass_w / n) if n > 0 else 0.0,
        n=n,
        k=k_w,
        passed=passed,
        reason="",
        curve=tuple(curve),
    )


def coverage_curve_from_sweep(
    sweep: Sequence[tuple[float, float, float, float, float, int]],
    n: float,
    max_points: int = vocab.CURVE_MAX_POINTS,
) -> list[CurvePoint]:
    points = [
        CurvePoint(
            threshold=level,
            auto_share=(pass_w / n) if n > 0 else 0.0,
            conformal_bound=conformal_bound(k_w, n),
            clopper_pearson_upper=_weighted_cp(wrong_w, pass_w, pass_sq),
            observed_error=(wrong_w / pass_w) if pass_w > 0 else 0.0,
            passed=passed,
        )
        for level, k_w, pass_w, pass_sq, wrong_w, passed in sweep
    ]
    if len(points) <= max_points:
        return points
    # Keep both ends; thin the middle evenly. Every kept point is exact.
    step = (len(points) - 1) / (max_points - 1)
    keep = sorted({int(round(i * step)) for i in range(max_points)})
    return [points[i] for i in keep]


def coverage_curve(
    probabilities: Sequence[float],
    wrong: Sequence[bool],
    *,
    weights: Optional[Sequence[float]] = None,
    eligible: Optional[Sequence[bool]] = None,
    max_points: int = vocab.CURVE_MAX_POINTS,
) -> list[CurvePoint]:
    p, x, w, e = _normalise(probabilities, wrong, weights, eligible)
    return coverage_curve_from_sweep(_sweep(p, x, w, e), float(sum(w)), max_points)


def not_achievable_message(alpha: float, tightest: float) -> str:
    """The refusal, in words an administrator can act on."""
    alpha_pct = _pct(alpha)
    tight_pct = _pct(tightest)
    return (
        f"A {alpha_pct} limit is not achievable on your current evidence. With "
        f"the documents reviewed so far, the tightest limit that can be "
        f"promised is {tight_pct}; everything goes to review until more "
        "documents are reviewed or the limit is raised."
    )


def _pct(value: float) -> str:
    percent = float(value) * 100.0
    if percent >= 10 or math.isclose(percent, round(percent)):
        return f"{percent:.0f}%"
    return f"{percent:.1f}%"
