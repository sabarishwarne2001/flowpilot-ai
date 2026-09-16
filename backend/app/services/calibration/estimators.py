"""ARCH-35 §6.4 — the three estimators, and the one rule that picks between them.

    labels < 50          PRIOR     no map at all; `evaluate` is never called
    50 <= labels < 200   PLATT     σ(a·s + b), a >= 0
    labels >= 200        ISOTONIC  sklearn IsotonicRegression(out_of_bounds='clip')

WHY PLATT BELOW 200 AND NOT ISOTONIC
====================================

Isotonic regression has one parameter per step and on eighty examples it fits
the noise: a block of four correct answers becomes a 100% plateau and the
conformal threshold then trusts it. Platt has two parameters and cannot
overfit that way. `ck_cm_method_needs_labels` puts the band in the database so
a code change cannot quietly widen it.

WHY THIS PLATT IS NEWTON'S METHOD AND NOT `LogisticRegression`
=============================================================

Three reasons, each sufficient:

  * Platt's method fits SMOOTHED targets — t+ = (N+ + 1)/(N+ + 2) and
    t- = 1/(N- + 2) — not the 0/1 labels. On separable data (every correct
    answer scored above every wrong one, which is exactly what a good engine
    produces) the 0/1 fit has no finite optimum. ARCH-33's own gate fixture is
    such a set. Smoothed targets need fractional labels, which scikit-learn's
    classifier does not take.
  * The slope must be constrained to a >= 0. A calibrator that maps a higher
    score to a lower probability violates the one property ARCH-33's raw score
    is built to guarantee, and on data where score and correctness are
    unrelated an unconstrained fit wanders either side of zero.
  * scikit-learn's penalty API changed between 1.8 and 1.9. A two-parameter
    Newton iteration (Lin, Lin & Weng 2007) is thirty lines, deterministic,
    and has no version surface.

The log-likelihood is concave in (a, b). When its unconstrained maximum has
a < 0, the maximum over the half-plane a >= 0 lies on the boundary a = 0,
where the optimum is closed-form: b = logit(mean target). That is what "the
score carries no evidence" looks like, and it is what this module returns.

WHY ISOTONIC IS EVALUATED IN PURE PYTHON
========================================

scikit-learn FITS the isotonic map; `evaluate` interpolates the stored
breakpoints itself. The breakpoints are what `calibration_models.breakpoints`
persists, and `apply.calibrated` runs on every automatic decision — reloading
an estimator object per request would put a pickle, a version dependency and
a second evaluation path between the stored row and the decision. Linear
interpolation between thresholds with clipping at the ends is exactly what
`IsotonicRegression.predict` does with `out_of_bounds='clip'`, and
`verify_arch35.py` asserts the two agree.

PURE
====

NumPy and scikit-learn, no Session, no clock, no random source.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence

import numpy as np

from app.services.calibration import vocabulary as vocab

__all__ = [
    "FittedMap",
    "select_method",
    "fit_isotonic",
    "fit_platt",
    "fit_map",
    "evaluate",
    "evaluate_many",
    "clamp",
    "parameters_are_valid",
    "sample_curve",
]

#: Breakpoints are persisted as decimal strings with this many places.
_PLACES: int = 7

_NEWTON_MAX_ITER: int = 100
_NEWTON_TOL: float = 1e-10
_NEWTON_RIDGE: float = 1e-12


@dataclass(frozen=True)
class FittedMap:
    """A monotone score -> probability map, as persisted.

    `parameters` is exactly what `calibration_models.breakpoints` holds:
    `{"x": [...], "y": [...]}` for ISOTONIC, `{"a": ..., "b": ...}` for PLATT,
    `{}` for PRIOR. Values are decimal strings.
    """

    method: str
    parameters: Mapping[str, Any]

    @property
    def is_prior(self) -> bool:
        return self.method == vocab.METHOD_PRIOR


def select_method(label_count: int) -> str:
    """The estimator a label count earns. The whole policy is these lines."""
    if label_count < vocab.MIN_LABELS_PLATT:
        return vocab.METHOD_PRIOR
    if label_count < vocab.MIN_LABELS_ISOTONIC:
        return vocab.METHOD_PLATT
    return vocab.METHOD_ISOTONIC


def clamp(value: float) -> float:
    """Strictly inside (0, 1). Monotone, so it never reorders a curve."""
    if value != value:  # NaN
        raise ValueError("a calibrated probability was NaN")
    return min(vocab.PROBABILITY_CEILING, max(vocab.PROBABILITY_FLOOR, float(value)))


def _fmt(value: float) -> str:
    return f"{float(value):.{_PLACES}f}"


def _as_arrays(
    scores: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]],
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    s = np.asarray([float(v) for v in scores], dtype=float)
    y = np.asarray([1.0 if bool(v) else 0.0 for v in correct], dtype=float)
    if weights is None:
        w = np.ones_like(s)
    else:
        w = np.asarray([float(v) for v in weights], dtype=float)
    if not (s.shape == y.shape == w.shape):
        raise ValueError("scores, labels and weights must have the same length")
    if s.size and (np.any(s < 0.0) or np.any(s > 1.0)):
        raise ValueError("raw scores must lie in [0, 1]")
    if w.size and np.any(w <= 0.0):
        raise ValueError("sample weights must be positive")
    return s, y, w


# ---------------------------------------------------------------------------
# Isotonic
# ---------------------------------------------------------------------------


def fit_isotonic(
    scores: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
) -> FittedMap:
    from sklearn.isotonic import IsotonicRegression

    s, y, w = _as_arrays(scores, correct, weights)
    if s.size == 0:
        raise ValueError("cannot fit an isotonic map on no examples")

    model = IsotonicRegression(
        y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip"
    )
    model.fit(s, y, sample_weight=w)

    xs = [float(v) for v in model.X_thresholds_]
    ys = [clamp(float(v)) for v in model.y_thresholds_]
    # A single-threshold fit is legal (every example had the same score); the
    # evaluator treats one point as a constant.
    return FittedMap(
        method=vocab.METHOD_ISOTONIC,
        parameters={"x": [_fmt(v) for v in xs], "y": [_fmt(v) for v in ys]},
    )


def _evaluate_isotonic(parameters: Mapping[str, Any], score: float) -> float:
    xs = [float(v) for v in parameters["x"]]
    ys = [float(v) for v in parameters["y"]]
    if not xs:
        raise ValueError("an isotonic map with no breakpoints")
    if score <= xs[0]:
        return clamp(ys[0])
    if score >= xs[-1]:
        return clamp(ys[-1])
    # Binary search for the right-hand breakpoint.
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= score:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span <= 0:
        return clamp(ys[hi])
    ratio = (score - xs[lo]) / span
    return clamp(ys[lo] + ratio * (ys[hi] - ys[lo]))


# ---------------------------------------------------------------------------
# Platt
# ---------------------------------------------------------------------------


def _sigmoid(z: np.ndarray) -> np.ndarray:
    out = np.empty_like(z)
    positive = z >= 0
    out[positive] = 1.0 / (1.0 + np.exp(-z[positive]))
    exp_z = np.exp(z[~positive])
    out[~positive] = exp_z / (1.0 + exp_z)
    return out


def _objective(a: float, b: float, s: np.ndarray, t: np.ndarray, w: np.ndarray) -> float:
    z = a * s + b
    # -[t log σ(z) + (1-t) log(1-σ(z))] = log(1+e^z) - t z, stable form.
    return float(np.sum(w * (np.logaddexp(0.0, z) - t * z)))


def _logit(p: float) -> float:
    p = min(1.0 - 1e-12, max(1e-12, p))
    return math.log(p / (1.0 - p))


def fit_platt(
    scores: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
) -> FittedMap:
    s, y, w = _as_arrays(scores, correct, weights)
    if s.size == 0:
        raise ValueError("cannot fit a Platt map on no examples")

    positive_mass = float(np.sum(w[y > 0.5]))
    negative_mass = float(np.sum(w[y <= 0.5]))
    t_pos = (positive_mass + 1.0) / (positive_mass + 2.0)
    t_neg = 1.0 / (negative_mass + 2.0)
    t = np.where(y > 0.5, t_pos, t_neg)

    target_mean = float(np.sum(w * t) / np.sum(w))
    a, b = 0.0, _logit(target_mean)
    value = _objective(a, b, s, t, w)

    for _ in range(_NEWTON_MAX_ITER):
        p = _sigmoid(a * s + b)
        diff = p - t
        g_a = float(np.sum(w * diff * s))
        g_b = float(np.sum(w * diff))
        if abs(g_a) < _NEWTON_TOL and abs(g_b) < _NEWTON_TOL:
            break
        d = w * p * (1.0 - p)
        h_aa = float(np.sum(d * s * s)) + _NEWTON_RIDGE
        h_ab = float(np.sum(d * s))
        h_bb = float(np.sum(d)) + _NEWTON_RIDGE
        det = h_aa * h_bb - h_ab * h_ab
        if det <= 0:
            break
        step_a = -(h_bb * g_a - h_ab * g_b) / det
        step_b = -(-h_ab * g_a + h_aa * g_b) / det

        size = 1.0
        improved = False
        while size >= 1e-10:
            na, nb = a + size * step_a, b + size * step_b
            candidate = _objective(na, nb, s, t, w)
            if candidate < value + 1e-4 * size * (g_a * step_a + g_b * step_b):
                a, b, value = na, nb, candidate
                improved = True
                break
            size /= 2.0
        if not improved:
            break
        if abs(size * step_a) < 1e-12 and abs(size * step_b) < 1e-12:
            break

    if a < 0.0:
        # The constrained optimum is on the boundary. See the module header.
        a, b = 0.0, _logit(target_mean)

    return FittedMap(
        method=vocab.METHOD_PLATT,
        parameters={"a": _fmt(a), "b": _fmt(b)},
    )


def _evaluate_platt(parameters: Mapping[str, Any], score: float) -> float:
    a = float(parameters["a"])
    b = float(parameters["b"])
    z = a * score + b
    if z >= 0:
        value = 1.0 / (1.0 + math.exp(-z))
    else:
        e = math.exp(z)
        value = e / (1.0 + e)
    return clamp(value)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


def fit_map(
    scores: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
    *,
    label_count: Optional[int] = None,
) -> FittedMap:
    """Fit the estimator the TOTAL label count earns.

    `label_count` is the number of labels the tenant has, which is larger than
    `len(scores)` when a held-out split was taken before fitting. Selection is
    by the total because that is what the database constraint reads.
    """
    method = select_method(len(scores) if label_count is None else int(label_count))
    if method == vocab.METHOD_PRIOR:
        return FittedMap(method=vocab.METHOD_PRIOR, parameters={})
    if method == vocab.METHOD_PLATT:
        return fit_platt(scores, correct, weights)
    return fit_isotonic(scores, correct, weights)


def evaluate(method: str, parameters: Mapping[str, Any], score: Any) -> Optional[float]:
    """The calibrated probability, or None for the PRIOR (no map at all)."""
    value = float(score)
    if value != value:
        raise ValueError("a raw score was NaN")
    value = min(1.0, max(0.0, value))
    if method == vocab.METHOD_PRIOR:
        return None
    if method == vocab.METHOD_PLATT:
        return _evaluate_platt(parameters, value)
    if method == vocab.METHOD_ISOTONIC:
        return _evaluate_isotonic(parameters, value)
    raise ValueError(f"{method!r} is not a calibration method")


def evaluate_many(
    fitted: FittedMap, scores: Sequence[float]
) -> list[float]:
    if fitted.is_prior:
        raise ValueError("the PRIOR has no map to evaluate")
    return [
        float(evaluate(fitted.method, fitted.parameters, score))  # type: ignore[arg-type]
        for score in scores
    ]


def parameters_are_valid(method: str, parameters: Mapping[str, Any]) -> bool:
    """The shape `ck_cm_breakpoints_shape` cannot express in SQL."""
    try:
        if method == vocab.METHOD_PRIOR:
            return dict(parameters) == {}
        if method == vocab.METHOD_PLATT:
            return float(parameters["a"]) >= 0.0 and math.isfinite(
                float(parameters["b"])
            )
        if method == vocab.METHOD_ISOTONIC:
            xs = [float(v) for v in parameters["x"]]
            ys = [float(v) for v in parameters["y"]]
            if not xs or len(xs) != len(ys):
                return False
            return all(b >= a for a, b in zip(xs, xs[1:])) and all(
                b >= a for a, b in zip(ys, ys[1:])
            )
    except (KeyError, TypeError, ValueError):
        return False
    return False


def sample_curve(
    fitted: FittedMap, points: int = vocab.FITTED_CURVE_POINTS
) -> list[tuple[float, float]]:
    """`(score, probability)` pairs across [0, 1], for the console."""
    if fitted.is_prior:
        return []
    count = max(2, int(points))
    return [
        (
            round(index / (count - 1), 6),
            float(evaluate(fitted.method, fitted.parameters, index / (count - 1))),  # type: ignore[arg-type]
        )
        for index in range(count)
    ]
