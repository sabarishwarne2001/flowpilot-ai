"""ARCH-33 §4.3 step 4 / §4.6 — the calibrated probability, and its cold start.

THE SCOPE CONFLICT THIS MODULE RESOLVES, STATED PLAINLY
=======================================================

§4.3 routes step 4 through "calibrated probability via ARCH-35 for (tenant,
family)" and §4.6 attributes the slider's consequence text to ARCH-35 as well.
ARCH-35 is the last unbuilt milestone on the roadmap. ARCH-33 cannot depend on
it and ship, and it cannot skip it either: `ck_ae_routing_consistent` makes a
calibrated probability mandatory on every row that reaches the `pass` edge, so
a stub returning None would make the phase's happy path unreachable.

The resolution, made explicitly rather than silently:

    ARCH-33 owns the INTERFACE. ARCH-35 replaces the ESTIMATOR behind it.

Everything a caller touches — `fit`, `calibrate`, `effective_threshold`,
`consequence`, `CalibrationModel` — is the shape ARCH-35 inherits.
`assertion_evaluations.calibration_model_id` is a bare `uuid` with NO foreign
key, exactly as §4.4's DDL writes it, so ARCH-35 adds its own table and its
own FK in its own migration and adopts every row this phase wrote.

WHY ISOTONIC AND NOT PLATT
==========================

Platt scaling fits a sigmoid: two parameters, well behaved on little data, and
wrong in a specific way here. It assumes the relationship between score and
correctness is sigmoidal, and this score's relationship is not — it has a step
at "the parser found nothing" and another at "the readings contradict each
other", because those are categorical facts wearing numeric clothes.

Isotonic regression assumes only MONOTONICITY: a higher raw score is never
less likely to be correct. That is the one property `features.raw_score` is
built to guarantee and the only one it can honestly claim. Pool-adjacent-
violators fits it exactly, in O(n), with no parameters and no optimiser.

The cost is variance on small samples, which is precisely what
`MIN_LABELS_FOR_CALIBRATION` and the cold-start threshold exist to handle. An
unfitted model refuses to produce a probability at all rather than producing a
confident one from twelve examples.

WHY A PROBABILITY IS NEVER EXACTLY 1
====================================

`_CEILING` clamps the output below 1.0. A calibrator that returns 1.0 is
claiming certainty, and `ck_ad_threshold_bounded` already forbids a threshold
of 1 for the mirror-image reason. Between them, "always passes" and "never
passes" are both unreachable states, which means every assertion in the system
has a live `pass` edge and a live `triage` edge.

PURE
====

Standard library only. No Session, no clock, no random. `fit` over the same
examples in the same order returns the same model forever, which is what lets
`calibration_model_id` mean something.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional, Sequence

from app.services.assertions import vocabulary as vocab

__all__ = [
    "LabeledExample",
    "CalibrationPoint",
    "CalibrationModel",
    "Consequence",
    "fit",
    "calibrate",
    "effective_threshold",
    "consequence",
    "PROBABILITY_PLACES",
    "MIN_LABELS",
]

#: `assertion_evaluations.calibrated_probability numeric(6,5)`.
PROBABILITY_PLACES: Decimal = Decimal("0.00001")

MIN_LABELS: int = vocab.MIN_LABELS_FOR_CALIBRATION

#: Never certain, never impossible. See the module header.
_CEILING: Decimal = Decimal("0.99999")
_FLOOR: Decimal = Decimal("0.00001")


@dataclass(frozen=True)
class LabeledExample:
    """One reviewer resolution, reduced to what the calibrator can learn from.

    `correct` is whether the ENGINE'S verdict matched the reviewer's, not
    whether the document passed. A reviewer clicking "It fails" on a FAIL the
    engine produced is a CORRECT example: the engine was right, and the
    calibrator should become more willing to trust that shape of evidence.
    Conflating the two teaches the calibrator to distrust every rule that
    mostly catches violations, which is every rule worth writing.
    """

    raw_score: Decimal
    correct: bool


@dataclass(frozen=True)
class CalibrationPoint:
    score: Decimal
    probability: Decimal


@dataclass(frozen=True)
class CalibrationModel:
    """A fitted, or deliberately unfitted, score -> probability mapping."""

    family: str
    points: tuple[CalibrationPoint, ...] = ()
    label_count: int = 0
    #: False until `label_count >= MIN_LABELS`. An unfitted model returns None
    #: from `calibrate`, which routes everything to triage.
    fitted: bool = False
    #: Stable identity for this fit, written to
    #: `assertion_evaluations.calibration_model_id`. None while unfitted,
    #: which is correct: there is no model to point at.
    model_id: Optional[str] = None
    engine_version: str = vocab.ENGINE_VERSION
    examples: tuple[LabeledExample, ...] = field(default_factory=tuple)

    def as_details(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "fitted": self.fitted,
            "label_count": self.label_count,
            "model_id": self.model_id,
            "engine_version": self.engine_version,
            "breakpoints": len(self.points),
        }


def _quantize(value: Decimal) -> Decimal:
    clamped = max(_FLOOR, min(_CEILING, value))
    return clamped.quantize(PROBABILITY_PLACES, rounding=ROUND_HALF_UP)


def fit(
    examples: Sequence[LabeledExample],
    *,
    family: str,
    model_id: Optional[str] = None,
) -> CalibrationModel:
    """Pool-adjacent-violators isotonic fit. Deterministic, O(n log n).

    Below `MIN_LABELS` this returns an UNFITTED model rather than a model
    fitted on too little. The difference is not cosmetic: an unfitted model
    makes `calibrate` return None, `routing.decide` then routes to TRIAGE, and
    `ck_ae_routing_consistent` refuses any row that claims otherwise. The
    cold-start behaviour is enforced at three levels and originates here.
    """
    if family not in vocab.FAMILIES:
        raise ValueError(
            f"{family!r} is not a known assertion family; a calibration model "
            "is fitted per (tenant, family) and an unknown family has no "
            "population to fit against."
        )

    ordered = tuple(
        sorted(examples, key=lambda e: (Decimal(str(e.raw_score)), e.correct))
    )
    if len(ordered) < MIN_LABELS:
        return CalibrationModel(
            family=family,
            points=(),
            label_count=len(ordered),
            fitted=False,
            model_id=None,
            examples=ordered,
        )

    # --- pool adjacent violators ------------------------------------------
    #
    # Each block holds (sum of labels, count, max score in the block). Blocks
    # are merged left while the running mean would decrease, which is exactly
    # the monotonicity constraint.
    blocks: list[list[Decimal]] = []
    for example in ordered:
        label = Decimal("1") if example.correct else Decimal("0")
        blocks.append([label, Decimal("1"), Decimal(str(example.raw_score))])
        while len(blocks) > 1:
            last = blocks[-1]
            previous = blocks[-2]
            if previous[0] / previous[1] <= last[0] / last[1]:
                break
            previous[0] += last[0]
            previous[1] += last[1]
            previous[2] = last[2]
            blocks.pop()

    points = tuple(
        CalibrationPoint(
            score=block[2].quantize(PROBABILITY_PLACES, rounding=ROUND_HALF_UP),
            probability=_quantize(block[0] / block[1]),
        )
        for block in blocks
    )

    return CalibrationModel(
        family=family,
        points=points,
        label_count=len(ordered),
        fitted=True,
        model_id=model_id,
        examples=ordered,
    )


def calibrate(
    model: Optional[CalibrationModel], raw: Any
) -> Optional[Decimal]:
    """Map a raw score to a probability, or None when there is no model.

    None is the honest answer for an unfitted model and it is a LOAD-BEARING
    None: it is what makes the cold start route to triage rather than pass at
    some invented default. Returning 0.5 instead would be a number with no
    meaning that nonetheless satisfies `calibrated_probability IS NOT NULL`,
    and the SQL invariant would then accept a row nothing had calibrated.
    """
    if model is None or not model.fitted or not model.points:
        return None

    score = Decimal(str(raw))

    # Below the first breakpoint: the lowest fitted probability. Above the
    # last: the highest. Isotonic regression says nothing outside the range it
    # saw, and extrapolating an increasing trend past the last observation is
    # how a calibrator invents confidence it has no evidence for.
    if score <= model.points[0].score:
        return _quantize(model.points[0].probability)
    if score >= model.points[-1].score:
        return _quantize(model.points[-1].probability)

    previous = model.points[0]
    for point in model.points[1:]:
        if score <= point.score:
            span = point.score - previous.score
            if span <= 0:
                return _quantize(point.probability)
            # Linear interpolation between breakpoints. The fit itself is a
            # step function; interpolating between steps keeps the output
            # monotone and stops a one-unit change in the raw score from
            # moving the probability by twenty points.
            ratio = (score - previous.score) / span
            value = previous.probability + ratio * (
                point.probability - previous.probability
            )
            return _quantize(value)
        previous = point

    return _quantize(model.points[-1].probability)  # pragma: no cover


def effective_threshold(
    configured: Any, model: Optional[CalibrationModel]
) -> Decimal:
    """The threshold actually applied, raised during the cold start.

    §4.6: "Not enough reviewed documents yet to estimate accuracy; everything
    below 99% goes to review until 50 are reviewed." The administrator's
    setting is never lowered and never overwritten — it is what they will get
    once the labels exist — but until then the floor applies.
    """
    chosen = Decimal(str(configured))
    if model is None or not model.fitted:
        return max(chosen, Decimal(vocab.COLD_START_THRESHOLD))
    return chosen


@dataclass(frozen=True)
class Consequence:
    """What §4.6's slider says UNDER the percentage.

    "At 95%, about 3 in 100 recent documents would go to review, and the
    measured error rate among automatic passes was 0.4%."

    A bare percentage is a number an administrator has no way to choose
    between. These two are what turn it into a decision, and both are measured
    on this tenant's own reviewed documents rather than asserted.
    """

    enough_labels: bool
    label_count: int
    threshold: Decimal
    #: Share of recent evaluations that would be routed to review, in [0, 1].
    triage_share: Optional[Decimal] = None
    #: Share of AUTOMATIC PASSES that a reviewer later called wrong.
    observed_error_rate: Optional[Decimal] = None
    #: How many automatic passes the error rate is measured over. A 0% error
    #: rate over four passes is not a 0% error rate, and the console shows
    #: this so the number can be read honestly.
    automatic_pass_count: int = 0

    def sentence(self) -> str:
        """The line rendered under the slider. Plain words, no jargon."""
        if not self.enough_labels:
            floor = (Decimal(vocab.COLD_START_THRESHOLD) * 100).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
            return (
                "Not enough reviewed documents yet to estimate accuracy; "
                f"everything below {floor}% goes to review until "
                f"{MIN_LABELS} are reviewed ({self.label_count} so far)."
            )

        share = self.triage_share or Decimal("0")
        per_hundred = int((share * 100).to_integral_value(rounding=ROUND_HALF_UP))
        percent = (self.threshold * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        if self.observed_error_rate is None or self.automatic_pass_count == 0:
            return (
                f"At {percent}%, about {per_hundred} in 100 recent documents "
                "would go to review. No document has passed automatically yet, "
                "so there is no error rate to report."
            )

        error = (self.observed_error_rate * 100).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
        return (
            f"At {percent}%, about {per_hundred} in 100 recent documents would "
            f"go to review, and the measured error rate among automatic passes "
            f"was {error}% over {self.automatic_pass_count}."
        )


def consequence(
    model: Optional[CalibrationModel],
    *,
    recent_raw_scores: Sequence[Any],
    threshold: Any,
) -> Consequence:
    """The two numbers §4.6's slider shows, measured on this tenant's data."""
    applied = effective_threshold(threshold, model)

    if model is None or not model.fitted:
        return Consequence(
            enough_labels=False,
            label_count=0 if model is None else model.label_count,
            threshold=applied,
        )

    scores = [Decimal(str(value)) for value in recent_raw_scores]
    if scores:
        triaged = sum(
            1
            for score in scores
            if (calibrate(model, score) or Decimal("0")) < applied
        )
        triage_share = (Decimal(triaged) / Decimal(len(scores))).quantize(
            PROBABILITY_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        triage_share = None

    passed = [
        example
        for example in model.examples
        if (calibrate(model, example.raw_score) or Decimal("0")) >= applied
    ]
    if passed:
        wrong = sum(1 for example in passed if not example.correct)
        error_rate = (Decimal(wrong) / Decimal(len(passed))).quantize(
            PROBABILITY_PLACES, rounding=ROUND_HALF_UP
        )
    else:
        error_rate = None

    return Consequence(
        enough_labels=True,
        label_count=model.label_count,
        threshold=applied,
        triage_share=triage_share,
        observed_error_rate=error_rate,
        automatic_pass_count=len(passed),
    )