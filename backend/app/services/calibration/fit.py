"""ARCH-35 §6.4 — one fit, from labels to everything a model row stores.

    labels ──► examples ──► split (80 / 20, stratified by score)
                              │            │
                              ▼            ▼
                        fit the map   ECE before / after, Brier
                                           │
                                           ▼
                           conformal threshold at α, Clopper-Pearson,
                           the error-versus-coverage curve
                                           │
                                           ▼
                       ACTIVE, or REJECTED when calibration made ECE worse

THE REFUSAL RULE
================

§6.4: "the fit is refused if calibration makes ECE worse than the identity
map". The identity map treats the raw score as a probability. If the fitted
map is less calibrated than that on held-out examples, the fit has learned
noise, and `ck_cm_improves` refuses to let such a model be anything but
REJECTED. The ARCH-33 path gets the same answer: a refused fit is an unfitted
model, `calibrate` returns None, and the document is reviewed.

THE LABEL IS ENGINE CORRECTNESS
===============================

`correct` means the reviewer agreed with what the platform said. An engine
that said FAIL and was confirmed is CORRECT. `examples_from_labels` carries
that flag through untouched; see ARCH-33 `calibration.LabeledExample`.

AUDIT SAMPLES ARE LABELS
========================

`examples_from_labels` keeps every row, audits included, and gives an audit
its 1/r weight. Dropping audits would remove the only evidence about the
region the platform approves without review — the one region the guarantee is
about. `verify_arch35.py` runs a mutant that drops them, and it must die.

PURE
====

NumPy through `estimators` and `risk`. No Session, no clock, no random source.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Iterable, Mapping, Optional, Sequence

from app.services.calibration import estimators, risk
from app.services.calibration import vocabulary as vocab

__all__ = [
    "Example",
    "FitOutcome",
    "examples_from_labels",
    "order_examples",
    "split",
    "expected_calibration_error",
    "brier_score",
    "reliability_bins",
    "fit_decision",
    "input_digest",
]


@dataclass(frozen=True)
class Example:
    raw_score: float
    correct: bool
    weight: float = 1.0
    #: Could the platform have acted on this example automatically?
    auto_eligible: bool = True
    was_audit_sample: bool = False


def examples_from_labels(rows: Iterable[Any]) -> list[Example]:
    """`calibration_labels` rows (or anything shaped like them) -> examples.

    Reads attributes `raw_score`, `correct`, `sample_weight`, `auto_eligible`
    and `was_audit_sample`. Every row is kept. See the module header.
    """
    examples: list[Example] = []
    for row in rows:
        weight = float(getattr(row, "sample_weight", 1) or 1)
        examples.append(
            Example(
                raw_score=float(getattr(row, "raw_score")),
                correct=bool(getattr(row, "correct")),
                weight=max(weight, 1.0),
                auto_eligible=bool(getattr(row, "auto_eligible", True)),
                was_audit_sample=bool(getattr(row, "was_audit_sample", False)),
            )
        )
    return examples


def order_examples(examples: Sequence[Example]) -> list[Example]:
    """Total order, so the split is a function of the examples alone."""
    return sorted(
        examples,
        key=lambda e: (
            float(e.raw_score),
            bool(e.correct),
            float(e.weight),
            bool(e.auto_eligible),
            bool(e.was_audit_sample),
        ),
    )


def split(examples: Sequence[Example]) -> tuple[list[Example], list[Example]]:
    """`(fit, holdout)`: every HOLDOUT_EVERY-th example in score order held out."""
    ordered = order_examples(examples)
    fit_part: list[Example] = []
    holdout: list[Example] = []
    for index, example in enumerate(ordered):
        if index % vocab.HOLDOUT_EVERY == vocab.HOLDOUT_EVERY - 1:
            holdout.append(example)
        else:
            fit_part.append(example)
    return fit_part, holdout


def expected_calibration_error(
    probabilities: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
    bins: int = vocab.ECE_BINS,
) -> float:
    """ECE over `bins` equal-MASS bins (ties broken by position)."""
    n = len(probabilities)
    if n == 0:
        return 0.0
    w = [1.0] * n if weights is None else [float(v) for v in weights]
    order = sorted(range(n), key=lambda i: (float(probabilities[i]), i))
    groups = max(1, min(int(bins), n))
    total_weight = sum(w)
    ece = 0.0
    base, extra = divmod(n, groups)
    start = 0
    for g in range(groups):
        size = base + (1 if g < extra else 0)
        members = order[start:start + size]
        start += size
        if not members:
            continue
        gw = sum(w[i] for i in members)
        mean_p = sum(w[i] * float(probabilities[i]) for i in members) / gw
        mean_y = sum(w[i] * (1.0 if correct[i] else 0.0) for i in members) / gw
        ece += (gw / total_weight) * abs(mean_p - mean_y)
    return ece


def brier_score(
    probabilities: Sequence[float],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
) -> float:
    n = len(probabilities)
    if n == 0:
        return 0.0
    w = [1.0] * n if weights is None else [float(v) for v in weights]
    total = sum(w)
    return sum(
        w[i] * (float(probabilities[i]) - (1.0 if correct[i] else 0.0)) ** 2
        for i in range(n)
    ) / total


def reliability_bins(
    raw_scores: Sequence[float],
    probabilities: Optional[Sequence[float]],
    correct: Sequence[bool],
    weights: Optional[Sequence[float]] = None,
    bins: int = vocab.RELIABILITY_BINS,
) -> list[dict[str, Any]]:
    """Equal-width bins of the CALIBRATED probability, for the diagram.

    When no map exists (PRIOR), binned by raw score — the diagram then shows
    what the raw score is worth, which is still the truth.
    """
    n = len(raw_scores)
    w = [1.0] * n if weights is None else [float(v) for v in weights]
    source = list(raw_scores) if probabilities is None else list(probabilities)
    out: list[dict[str, Any]] = []
    for b in range(bins):
        lo = b / bins
        hi = (b + 1) / bins
        members = [
            i
            for i in range(n)
            if (lo <= float(source[i]) < hi) or (b == bins - 1 and float(source[i]) == 1.0)
        ]
        if not members:
            out.append(
                {"lower": lo, "upper": hi, "count": 0, "mean_predicted": None,
                 "mean_raw": None, "observed": None}
            )
            continue
        gw = sum(w[i] for i in members)
        out.append(
            {
                "lower": lo,
                "upper": hi,
                "count": len(members),
                "mean_predicted": round(
                    sum(w[i] * float(source[i]) for i in members) / gw, 5
                ),
                "mean_raw": round(
                    sum(w[i] * float(raw_scores[i]) for i in members) / gw, 5
                ),
                "observed": round(
                    sum(w[i] * (1.0 if correct[i] else 0.0) for i in members) / gw, 5
                ),
            }
        )
    return out


@dataclass(frozen=True)
class FitOutcome:
    """Everything a `calibration_models` row stores, before the Session."""

    method: str
    status: str
    label_count: int
    fitted: estimators.FittedMap
    ece_before: float
    ece_after: float
    brier_after: float
    risk: risk.RiskControl
    holdout_count: int
    rejected_reason: str = ""
    diagnostics: Mapping[str, Any] = field(default_factory=dict)

    @property
    def usable(self) -> bool:
        """A map exists and passed the refusal rule."""
        return self.status == vocab.STATUS_ACTIVE and not self.fitted.is_prior

    @property
    def auto_allowed(self) -> bool:
        return self.usable and self.risk.achievable and self.risk.auto_share > 0


def _prior_outcome(
    examples: Sequence[Example], target_error_rate: float
) -> FitOutcome:
    scores = [e.raw_score for e in examples]
    labels = [e.correct for e in examples]
    weights = [e.weight for e in examples]
    ece = expected_calibration_error(scores, labels, weights) if examples else 0.0
    brier = brier_score(scores, labels, weights) if examples else 0.0
    count = len(examples)
    refusal = (
        "Not enough reviewed documents yet to estimate accuracy; everything "
        f"goes to review until {vocab.MIN_LABELS_PLATT} are reviewed "
        f"({count} so far)."
    )
    control = risk.RiskControl(
        target_error_rate=target_error_rate,
        achievable=False,
        threshold=1.0,
        conformal_bound=1.0,
        clopper_pearson_upper=1.0,
        auto_share=0.0,
        n=float(sum(weights)),
        k=0.0,
        passed=0,
        reason=refusal,
        curve=(),
    )
    return FitOutcome(
        method=vocab.METHOD_PRIOR,
        status=vocab.STATUS_ACTIVE,
        label_count=count,
        fitted=estimators.FittedMap(method=vocab.METHOD_PRIOR, parameters={}),
        ece_before=ece,
        ece_after=ece,
        brier_after=brier,
        risk=control,
        holdout_count=0,
        diagnostics={
            "engine_version": vocab.ENGINE_VERSION,
            "holdout_count": 0,
            "reliability": reliability_bins(scores, None, labels, weights),
            "fitted_curve": [],
            "coverage_curve": [],
            "risk_reason": refusal,
        },
    )


def fit_decision(
    examples: Sequence[Example],
    *,
    target_error_rate: float,
) -> FitOutcome:
    """The whole §6.4 pipeline for one (tenant, decision type)."""
    alpha = float(target_error_rate)
    count = len(examples)
    if estimators.select_method(count) == vocab.METHOD_PRIOR:
        return _prior_outcome(examples, alpha)

    fit_part, holdout = split(examples)
    fitted = estimators.fit_map(
        [e.raw_score for e in fit_part],
        [e.correct for e in fit_part],
        [e.weight for e in fit_part],
        label_count=count,
    )

    h_scores = [e.raw_score for e in holdout]
    h_correct = [e.correct for e in holdout]
    h_weights = [e.weight for e in holdout]
    h_eligible = [e.auto_eligible for e in holdout]
    h_probs = estimators.evaluate_many(fitted, h_scores)

    ece_before = expected_calibration_error(h_scores, h_correct, h_weights)
    ece_after = expected_calibration_error(h_probs, h_correct, h_weights)
    brier_after = brier_score(h_probs, h_correct, h_weights)

    control = risk.conformal_threshold(
        h_probs,
        [not c for c in h_correct],
        alpha,
        weights=h_weights,
        eligible=h_eligible,
        with_curve=True,
    )

    improved = ece_after <= ece_before
    status = vocab.STATUS_ACTIVE if improved else vocab.STATUS_REJECTED
    rejected_reason = (
        ""
        if improved
        else (
            f"Calibration made held-out accuracy estimates worse "
            f"(ECE {ece_after:.4f} against {ece_before:.4f} for the raw "
            "score), so the fit was refused."
        )
    )

    return FitOutcome(
        method=fitted.method,
        status=status,
        label_count=count,
        fitted=fitted,
        ece_before=ece_before,
        ece_after=ece_after,
        brier_after=brier_after,
        risk=control,
        holdout_count=len(holdout),
        rejected_reason=rejected_reason,
        diagnostics={
            "engine_version": vocab.ENGINE_VERSION,
            "holdout_count": len(holdout),
            "holdout_weight": round(sum(h_weights), 3),
            "audit_labels": sum(1 for e in examples if e.was_audit_sample),
            "reliability": reliability_bins(h_scores, h_probs, h_correct, h_weights),
            "reliability_raw": reliability_bins(h_scores, None, h_correct, h_weights),
            "fitted_curve": [
                {"score": s, "probability": round(p, 6)}
                for s, p in estimators.sample_curve(fitted)
            ],
            "coverage_curve": [point.as_payload() for point in control.curve],
            "risk_reason": control.reason,
            "rejected_reason": rejected_reason,
        },
    )


def input_digest(
    examples: Sequence[Example],
    *,
    target_error_rate: Any,
    audit_sample_rate: Any,
) -> str:
    """What a fit depends on. Unchanged digest, unchanged fit — skip it."""
    payload = {
        "engine": vocab.ENGINE_VERSION,
        "alpha": str(Decimal(str(target_error_rate)).normalize()),
        "audit": str(Decimal(str(audit_sample_rate)).normalize()),
        "examples": [
            [
                f"{e.raw_score:.7f}",
                int(e.correct),
                f"{e.weight:.3f}",
                int(e.auto_eligible),
                int(e.was_audit_sample),
            ]
            for e in order_examples(examples)
        ],
    }
    blob = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
