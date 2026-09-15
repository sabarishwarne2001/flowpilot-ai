"""ARCH-34 §5.3 — price surge, on a median that a single outlier cannot move.

WHY MEDIAN AND MAD RATHER THAN MEAN AND STANDARD DEVIATION
==========================================================

Because the thing being detected is the thing that breaks the baseline.

A supplier bills ₹2,100 for toner nine times, then ₹9,000 once, then ₹2,249.
Mean and standard deviation absorb the ₹9,000: the mean rises to ₹2,790 and
the standard deviation explodes to roughly ₹2,100, so the ₹2,249 sits well
under one sigma and nothing is reported. The ₹9,000 has hidden the next
anomaly, and it has hidden every anomaly for the next year.

Median and MAD do not move. The median stays at ₹2,100, the MAD stays small,
and both the ₹9,000 and the ₹2,249 are visible. That is the entire argument,
and it is why `verify_arch34.py` requires the mutant that swaps in mean and
standard deviation to die — a mutant that would pass any test that only
checked "an obvious spike is detected".

MAD = 0 IS THE CASE THAT MATTERS
================================

A supplier who billed exactly ₹50,000 nine times has a median absolute
deviation of exactly zero. The robust z of the tenth price, whatever it is, is
a division by zero: infinitely many deviations from the median. That is
mathematically true and operationally useless, and it is not a rare edge —
fixed-price retainers, licence renewals and rate-card items produce it
constantly, which means it is the SHAPE OF THE MOST COMMON SERIES the detector
will see.

Three wrong answers and the right one:

  * Emitting `inf`. It does not fit `numeric(6,5)`, and the sweep discovers
    that at INSERT time after it has done all its work.
  * Adding an epsilon to the denominator. This produces a finite, enormous,
    entirely arbitrary z. A reviewer reads it as a measured quantity, because
    it looks exactly like one.
  * Skipping the series. Silently ignoring every fixed-price item in the
    workspace is the worst of the three, because nothing anywhere looks wrong.

What this engine does: falls back to the RELATIVE-CHANGE TEST ALONE, records
`basis = 'RELATIVE_ONLY'` on the finding, sets `z = None`, and says so in the
evidence. The console renders "no historical variation to compare against;
flagged on a 12.0% increase" instead of a z-score. A weaker claim, stated as a
weaker claim.

WHY THE RELATIVE-CHANGE FLOOR EXISTS AT ALL
===========================================

Because a robust z-score on a very tight series is trivially large. A ₹2
change on a ₹20 item whose historical spread is ₹0.50 is a z of about 2.7 and
a 10% change; a ₹0.20 change on the same item is a z of 0.27. Without the
floor, a tight series would page somebody over rounding. §5.3 sets the default
at 10%, tenant-tunable.

ALL MONEY IS INTEGER MICROS
===========================

Every price here is `int`, millionths of a currency unit, exactly as
`procurement_case_lines.invoice_unit_price_micros` stores it. Statistics are
computed in `Decimal`, never `float`: a median of two integers can be a half,
and `float` would make two runs over the same nine prices produce two
different z-scores in the sixth decimal place — which is the difference
between a finding and no finding when |z| sits at 3.5000001.

PURE
====

Standard library and `vocabulary`. No Session, no clock — `as_of` is passed
in, because a detector that read the wall clock could not be replayed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional, Sequence

from app.services.radar import vocabulary as vocab

__all__ = [
    "PriceObservation",
    "SurgeSettings",
    "SurgeResult",
    "DEFAULT_SETTINGS",
    "median",
    "median_absolute_deviation",
    "interquartile_range",
    "robust_z",
    "relative_change",
    "trailing_window_start",
    "confidence",
    "evaluate",
]

_FIVE_DP = Decimal("0.00001")


@dataclass(frozen=True)
class PriceObservation:
    """One unit price on one document, on one date.

    `series_key` is the `(vendor_key, sku)` this observation belongs to,
    already normalised through `app/core/normalize.py` by the caller. It is
    carried rather than derived so that the engine never has to decide which
    series a price belongs to — a decision that, made twice in two places,
    produces two different baselines for the same item.
    """

    work_item_id: str
    observed_on: date
    unit_price_micros: int
    currency: str
    vendor_key: str
    sku: str
    description: Optional[str] = None
    line_number: Optional[int] = None


@dataclass(frozen=True)
class SurgeSettings:
    """Tenant-tunable thresholds, frozen, and part of the `input_digest`.

    A tenant that lowers `min_relative_change` has changed what the detector
    reports, so the digest must change with it or the next sweep hits the old
    answer and the new setting appears to do nothing.
    """

    min_observations: int = vocab.PRICE_MIN_OBSERVATIONS
    trailing_months: int = vocab.PRICE_TRAILING_MONTHS
    z_abs_min: Decimal = vocab.PRICE_Z_ABS_MIN
    min_relative_change: Decimal = vocab.PRICE_MIN_RELATIVE_CHANGE

    def as_digest_payload(self) -> dict[str, Any]:
        return {
            "min_observations": self.min_observations,
            "trailing_months": self.trailing_months,
            "z_abs_min": self.z_abs_min,
            "min_relative_change": self.min_relative_change,
        }


DEFAULT_SETTINGS: SurgeSettings = SurgeSettings()


@dataclass(frozen=True)
class SurgeResult:
    """What the detector concluded, whether or not it fired.

    Returned even when nothing fires, with `fired=False` and a `reason`. The
    sweep discards it; the console's inspection view and `verify_arch34.py`
    both need to see WHY a series produced nothing, and "returned None" is not
    an explanation anybody can act on.
    """

    fired: bool
    basis: str
    reason: str
    observed_price_micros: int
    median_micros: Optional[Decimal] = None
    mad_micros: Optional[Decimal] = None
    iqr_micros: Optional[Decimal] = None
    z: Optional[Decimal] = None
    relative_change: Optional[Decimal] = None
    observation_count: int = 0
    score: Decimal = Decimal("0")
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)


# ===========================================================================
# Statistics
# ===========================================================================


def median(values: Sequence[int]) -> Decimal:
    """Exact median in Decimal. Even-length series average the two middles."""
    if not values:
        raise ValueError("The median of an empty series is not defined.")
    ordered = sorted(Decimal(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def median_absolute_deviation(
    values: Sequence[int], centre: Optional[Decimal] = None
) -> Decimal:
    """MAD: the median of the absolute deviations from the median.

    Returns an exact zero when every observation equals the median — the
    caller MUST branch on it rather than dividing. See the module docstring.
    """
    if not values:
        raise ValueError("The MAD of an empty series is not defined.")
    middle = median(values) if centre is None else centre
    deviations = sorted(abs(Decimal(value) - middle) for value in values)
    index = len(deviations) // 2
    if len(deviations) % 2 == 1:
        return deviations[index]
    return (deviations[index - 1] + deviations[index]) / Decimal(2)


def interquartile_range(values: Sequence[int]) -> Decimal:
    """IQR, shown alongside the z-score as §5.3's second opinion.

    Uses the "exclusive median" convention: the lower half is everything
    strictly below the median position, the upper half everything strictly
    above. It is not used for the decision — only for the inspection view —
    so the choice of convention matters less than stating which one it is.
    """
    if len(values) < 2:
        return Decimal("0")
    ordered = sorted(values)
    half = len(ordered) // 2
    lower = ordered[:half]
    upper = ordered[half + 1 :] if len(ordered) % 2 == 1 else ordered[half:]
    if not lower or not upper:
        return Decimal("0")
    return median(upper) - median(lower)


def robust_z(
    price_micros: int, centre: Decimal, mad: Decimal
) -> Optional[Decimal]:
    """`0.6745 · (price − median) / MAD`, or None when MAD is zero.

    None, not infinity and not a large number. The caller branches on it, and
    the branch is the whole point of this function's contract.
    """
    if mad == 0:
        return None
    numerator = vocab.MAD_CONSISTENCY * (Decimal(price_micros) - centre)
    return (numerator / mad).quantize(_FIVE_DP, rounding=ROUND_HALF_UP)


def relative_change(price_micros: int, centre: Decimal) -> Optional[Decimal]:
    """Signed relative change against the median, or None if the median is 0.

    A median of zero means a series of free line items, which is real —
    zero-rated shipping lines appear on invoices constantly — and against
    which no relative change is defined. None rather than a
    ZeroDivisionError inside a nightly job.
    """
    if centre == 0:
        return None
    return ((Decimal(price_micros) - centre) / centre).quantize(
        _FIVE_DP, rounding=ROUND_HALF_UP
    )


def trailing_window_start(as_of: date, months: int) -> date:
    """The first day of the trailing window, by calendar months.

    Calendar months rather than `as_of - timedelta(days=365)`, because a
    monthly series has twelve observations in twelve calendar months and
    eleven or twelve in 365 days depending on leap years and which day of the
    month the supplier invoices on. A window whose observation count depends
    on the leap cycle makes `min_observations` fire intermittently.

    Day-of-month clamping: 31 March minus one month is 28 February (or the
    29th), not an invalid date.
    """
    if months < 1:
        raise ValueError("The trailing window must be at least one month.")
    total = as_of.year * 12 + (as_of.month - 1) - months
    year, month = divmod(total, 12)
    month += 1
    if month == 12:
        next_month_start = date(year + 1, 1, 1)
    else:
        next_month_start = date(year, month + 1, 1)
    last_day = (next_month_start.toordinal() - 1) - date(year, month, 1).toordinal() + 1
    return date(year, month, min(as_of.day, last_day))


def confidence(
    z: Optional[Decimal], change: Optional[Decimal], settings: SurgeSettings
) -> Decimal:
    """Map the decision onto `anomaly_findings.score numeric(6,5)` in [0, 1].

    The column needs a number in [0, 1] and |z| is unbounded, so something has
    to map one onto the other. This does it by SATURATION at twice the
    threshold rather than by a sigmoid: a z of 7 and a z of 70 are both
    "as strong as this detector can say", and a curve that kept climbing would
    invite a reader to compare two findings' scores as if the difference
    meant something.

    On the `RELATIVE_ONLY` path there is no z, so the score is driven by the
    relative change against the same saturation rule, and it CANNOT reach the
    0.90 that `severity_for` requires for HIGH. A finding with no measurable
    historical spread behind it does not get to be the strongest thing in the
    queue.
    """
    if z is not None:
        magnitude = abs(z)
        ceiling = settings.z_abs_min * 2
        if ceiling == 0:
            return Decimal("1.00000")
        ratio = magnitude / ceiling
        return min(Decimal("1"), ratio).quantize(_FIVE_DP, rounding=ROUND_HALF_UP)

    if change is None:
        return Decimal("0.50000")
    magnitude = abs(change)
    ceiling = settings.min_relative_change * 4
    if ceiling == 0:
        return Decimal("0.85000")
    ratio = magnitude / ceiling
    # Capped below the HIGH band on purpose.
    return min(Decimal("0.85"), ratio * Decimal("0.85")).quantize(
        _FIVE_DP, rounding=ROUND_HALF_UP
    )


# ===========================================================================
# The detector
# ===========================================================================


def evaluate(
    observation: PriceObservation,
    history: Sequence[PriceObservation],
    *,
    as_of: Optional[date] = None,
    settings: SurgeSettings = DEFAULT_SETTINGS,
) -> SurgeResult:
    """Decide whether one unit price is a surge against its own history.

    `history` is every PRIOR observation for the same `(vendor_key, sku)`.
    Filtering to the trailing window happens here rather than in SQL so that
    the window is part of the pure, gateable decision — a SQL `WHERE` clause
    is not something `verify_arch34.py` can drive with a curated series.

    The observation itself is excluded from its own baseline. Including it
    would pull the median toward the very price being tested, which is a
    small effect on a long series and a decisive one on a series of five.
    """
    anchor = as_of or observation.observed_on
    cutoff = trailing_window_start(anchor, settings.trailing_months)

    # Same currency only. A supplier who switched from USD to INR has two
    # series, not one, and integer micros make them look like one.
    window = [
        item
        for item in history
        if item.work_item_id != observation.work_item_id
        and item.observed_on >= cutoff
        and item.observed_on <= anchor
        and item.currency == observation.currency
    ]
    prices = [item.unit_price_micros for item in window]

    if len(prices) < settings.min_observations:
        return SurgeResult(
            fired=False,
            basis=vocab.BASIS_ROBUST_Z,
            reason=(
                f"{len(prices)} prior observations in the trailing "
                f"{settings.trailing_months} months; "
                f"{settings.min_observations} are required."
            ),
            observed_price_micros=observation.unit_price_micros,
            observation_count=len(prices),
        )

    centre = median(prices)
    mad = median_absolute_deviation(prices, centre)
    iqr = interquartile_range(prices)
    change = relative_change(observation.unit_price_micros, centre)
    z = robust_z(observation.unit_price_micros, centre, mad)

    basis = vocab.BASIS_ROBUST_Z if z is not None else vocab.BASIS_RELATIVE_ONLY

    # The relative-change floor applies on BOTH paths. It is the only test on
    # the RELATIVE_ONLY path and a co-requirement on the ROBUST_Z path.
    if change is None:
        return SurgeResult(
            fired=False,
            basis=basis,
            reason=(
                "The trailing median is zero, so relative change is undefined. "
                "A zero-rated line has no price to surge from."
            ),
            observed_price_micros=observation.unit_price_micros,
            median_micros=centre,
            mad_micros=mad,
            iqr_micros=iqr,
            z=z,
            observation_count=len(prices),
        )

    if abs(change) < settings.min_relative_change:
        return SurgeResult(
            fired=False,
            basis=basis,
            reason=(
                f"Relative change of {change} is below the "
                f"{settings.min_relative_change} minimum. A tight series makes "
                "a trivial change look statistically large."
            ),
            observed_price_micros=observation.unit_price_micros,
            median_micros=centre,
            mad_micros=mad,
            iqr_micros=iqr,
            z=z,
            relative_change=change,
            observation_count=len(prices),
        )

    if z is not None and abs(z) < settings.z_abs_min:
        return SurgeResult(
            fired=False,
            basis=basis,
            reason=(
                f"|z| of {abs(z)} is below the {settings.z_abs_min} minimum "
                "against this item's own historical spread."
            ),
            observed_price_micros=observation.unit_price_micros,
            median_micros=centre,
            mad_micros=mad,
            iqr_micros=iqr,
            z=z,
            relative_change=change,
            observation_count=len(prices),
        )

    score = confidence(z, change, settings)

    series_points = [
        {
            "work_item_id": item.work_item_id,
            "observed_on": item.observed_on.isoformat(),
            "unit_price_micros": item.unit_price_micros,
        }
        for item in sorted(window, key=lambda i: (i.observed_on, i.work_item_id))
    ]

    evidence: list[dict[str, Any]] = [
        {
            "kind": vocab.EVIDENCE_PRICE_SERIES,
            "label": "Unit price history",
            "vendor_key": observation.vendor_key,
            "sku": observation.sku,
            "currency": observation.currency,
            "description": observation.description,
            "basis": basis,
            "median_micros": str(centre),
            "mad_micros": str(mad),
            "iqr_micros": str(iqr),
            "z": None if z is None else str(z),
            "relative_change": str(change),
            "observation_count": len(prices),
            "trailing_months": settings.trailing_months,
            "window_start": cutoff.isoformat(),
            "observed": {
                "work_item_id": observation.work_item_id,
                "observed_on": observation.observed_on.isoformat(),
                "unit_price_micros": observation.unit_price_micros,
            },
            "series": series_points,
            "note": (
                "Flagged on relative change alone: every prior price in the "
                "window was identical, so the median absolute deviation is "
                "zero and no robust z-score exists for this series."
                if basis == vocab.BASIS_RELATIVE_ONLY
                else (
                    "Robust z-score against the trailing median, scaled by "
                    "the median absolute deviation. IQR is shown as a second "
                    "opinion and does not affect the decision."
                )
            ),
        }
    ]

    return SurgeResult(
        fired=True,
        basis=basis,
        reason=(
            f"{change} change against a median of {centre} micros over "
            f"{len(prices)} observations."
        ),
        observed_price_micros=observation.unit_price_micros,
        median_micros=centre,
        mad_micros=mad,
        iqr_micros=iqr,
        z=z,
        relative_change=change,
        observation_count=len(prices),
        score=score,
        evidence=tuple(evidence),
        metrics={
            "basis": basis,
            "median_micros": str(centre),
            "mad_micros": str(mad),
            "iqr_micros": str(iqr),
            "z": None if z is None else str(z),
            "relative_change": str(change),
            "observation_count": len(prices),
        },
    )
