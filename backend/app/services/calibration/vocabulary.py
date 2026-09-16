"""ARCH-35 — the closed vocabularies and every constant the guarantee rests on.

SAME PATTERN AS ARCH-31 THROUGH ARCH-34
=======================================

Standard library only. The pure engines import this module, the ORM model
reads it for its CHECK constraints, and `alembic/versions/
arch35_step1_calibration.py` carries literal copies that `verify_arch35.py`
asserts are equal. A decision type added here and not there fails a gate
rather than a production insert.

WHAT α MEANS, STATED ONCE AND PRECISELY
=======================================

The roadmap's formula is

    ( Σ_i 1[p_i ≥ λ and wrong_i] + 1 ) / ( n + 1 )  ≤  α

and n counts EVERY example in the calibration set, not only the ones that
would have passed. So α bounds the expected share of ALL documents that are
approved automatically AND wrong — "wrong automatic approvals per 100
documents". The roadmap's prose calls α "the error rate among automatic
passes", which is a different, always-larger quantity (divide by the share
that passes). The formula is implemented exactly as written, including the +1
finite-sample correction, and the console labels α for what it is.

The number an enterprise buyer asks for — "how often is what you do without a
human wrong?" — is `clopper_pearson_upper`: a one-sided upper bound, at
`CLOPPER_PEARSON_CONFIDENCE`, on the error rate among automatic passes. That is
§6.2's "1.0% with 95% confidence".

WHY THERE ARE THIRTEEN DECISION TYPES AND ONLY NINE ARE AUTOMATED
=================================================================

Every place a human confirms or overturns what the platform said is a label.
Only some of those places have an automatic decision behind them today:

  * `verification.document` — backs `document_verifications.auto_approved`.
  * `assertion.<family>` — backs ARCH-33's `pass` edge, one per family.

`verification.field`, `reconciliation.case` and `anomaly.finding` are
MEASURED: labels are harvested and a calibrated model is fitted, so the
console can state how often each engine is right on this tenant's documents,
but no code path approves anything on them. Pretending otherwise would put a
guarantee on the console with nothing behind it.
"""

from __future__ import annotations

__all__ = [
    "ENGINE_VERSION",
    "METHOD_ISOTONIC",
    "METHOD_PLATT",
    "METHOD_PRIOR",
    "METHODS",
    "STATUS_ACTIVE",
    "STATUS_SUPERSEDED",
    "STATUS_SUSPENDED",
    "STATUS_REJECTED",
    "STATUSES",
    "LIVE_STATUSES",
    "DECISION_VERIFICATION_DOCUMENT",
    "DECISION_VERIFICATION_FIELD",
    "DECISION_RECONCILIATION_CASE",
    "DECISION_ANOMALY_FINDING",
    "ASSERTION_DECISION_PREFIX",
    "ASSERTION_FAMILIES",
    "ASSERTION_DECISION_TYPES",
    "DECISION_TYPES",
    "AUTOMATED_DECISION_TYPES",
    "DECISION_LABELS",
    "SOURCE_DOCUMENT_VERIFICATIONS",
    "SOURCE_VERIFICATION_FIELDS",
    "SOURCE_ASSERTION_EVALUATIONS",
    "SOURCE_PROCUREMENT_CASES",
    "SOURCE_ANOMALY_FINDINGS",
    "SOURCE_TABLES",
    "MIN_LABELS_PLATT",
    "MIN_LABELS_ISOTONIC",
    "HOLDOUT_EVERY",
    "ECE_BINS",
    "RELIABILITY_BINS",
    "CURVE_MAX_POINTS",
    "FITTED_CURVE_POINTS",
    "PROBABILITY_FLOOR",
    "PROBABILITY_CEILING",
    "DEFAULT_TARGET_ERROR_RATE",
    "TARGET_ERROR_RATE_MAX",
    "DEFAULT_AUDIT_SAMPLE_RATE",
    "AUDIT_SAMPLE_RATE_MIN",
    "AUDIT_SAMPLE_RATE_MAX",
    "CLOPPER_PEARSON_CONFIDENCE",
    "PSI_THRESHOLD",
    "PSI_BINS",
    "PSI_EPSILON",
    "MIN_PSI_SAMPLES",
    "MONITOR_WINDOW",
    "MONITOR_SIGNIFICANCE",
    "REFERENCE_WINDOW_DAYS",
    "RECENT_WINDOW_DAYS",
    "STALE_AFTER_HOURS",
    "RESUME_MIN_NEW_LABELS",
    "MAX_LABELS_PER_FIT",
    "CAPABILITY_CALIBRATED_AUTONOMY",
    "JOB_CALIBRATION_HARVEST",
    "JOB_CALIBRATION_REFIT",
    "ASSERTION_FIELD_PATH_PREFIX",
    "assertion_decision_type",
    "family_of",
    "is_automated",
]

#: Bumped when the fit, the split or the risk arithmetic changes. Part of every
#: model's input digest, so a changed engine refits instead of being skipped.
ENGINE_VERSION: str = "arch35.1"

# ---------------------------------------------------------------------------
# Methods and statuses
# ---------------------------------------------------------------------------

METHOD_ISOTONIC: str = "ISOTONIC"
METHOD_PLATT: str = "PLATT"
METHOD_PRIOR: str = "PRIOR"

METHODS: tuple[str, ...] = (METHOD_ISOTONIC, METHOD_PLATT, METHOD_PRIOR)

STATUS_ACTIVE: str = "ACTIVE"
STATUS_SUPERSEDED: str = "SUPERSEDED"
STATUS_SUSPENDED: str = "SUSPENDED"
STATUS_REJECTED: str = "REJECTED"

STATUSES: tuple[str, ...] = (
    STATUS_ACTIVE,
    STATUS_SUPERSEDED,
    STATUS_SUSPENDED,
    STATUS_REJECTED,
)

#: The statuses `uq_cm_active` makes unique per (organization, decision type).
#: SUSPENDED is live: it is the model in force, and what it says is "no".
LIVE_STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_SUSPENDED)

# ---------------------------------------------------------------------------
# Decision types
# ---------------------------------------------------------------------------

DECISION_VERIFICATION_DOCUMENT: str = "verification.document"
DECISION_VERIFICATION_FIELD: str = "verification.field"
DECISION_RECONCILIATION_CASE: str = "reconciliation.case"
DECISION_ANOMALY_FINDING: str = "anomaly.finding"

ASSERTION_DECISION_PREFIX: str = "assertion."

#: A copy of `app.services.assertions.vocabulary.FAMILIES`, not an import:
#: the migration copies this module's tuples, and a migration cannot follow an
#: import into a second package. `verify_arch35.py` asserts the two are equal.
ASSERTION_FAMILIES: tuple[str, ...] = (
    "duration_bound",
    "notice_period",
    "money_multiple_bound",
    "money_bound",
    "enumerated",
    "presence",
    "absence",
    "llm",
)

ASSERTION_DECISION_TYPES: tuple[str, ...] = tuple(
    f"{ASSERTION_DECISION_PREFIX}{family}" for family in ASSERTION_FAMILIES
)

DECISION_TYPES: tuple[str, ...] = (
    DECISION_VERIFICATION_DOCUMENT,
    DECISION_VERIFICATION_FIELD,
    DECISION_RECONCILIATION_CASE,
    DECISION_ANOMALY_FINDING,
) + ASSERTION_DECISION_TYPES

AUTOMATED_DECISION_TYPES: tuple[str, ...] = (
    DECISION_VERIFICATION_DOCUMENT,
) + ASSERTION_DECISION_TYPES

#: What each decision type is, in words for the console.
DECISION_LABELS: dict[str, str] = {
    DECISION_VERIFICATION_DOCUMENT: "Extraction approval",
    DECISION_VERIFICATION_FIELD: "Extracted field values",
    DECISION_RECONCILIATION_CASE: "Three-way matching",
    DECISION_ANOMALY_FINDING: "Audit radar findings",
    "assertion.duration_bound": "Clause check: duration limits",
    "assertion.notice_period": "Clause check: notice periods",
    "assertion.money_multiple_bound": "Clause check: liability multiples",
    "assertion.money_bound": "Clause check: amounts",
    "assertion.enumerated": "Clause check: allowed values",
    "assertion.presence": "Clause check: required clauses",
    "assertion.absence": "Clause check: prohibited clauses",
    "assertion.llm": "Clause check: AI-read requirements",
}

# ---------------------------------------------------------------------------
# Label sources
# ---------------------------------------------------------------------------

SOURCE_DOCUMENT_VERIFICATIONS: str = "document_verifications"
SOURCE_VERIFICATION_FIELDS: str = "document_verification_fields"
SOURCE_ASSERTION_EVALUATIONS: str = "assertion_evaluations"
SOURCE_PROCUREMENT_CASES: str = "procurement_cases"
SOURCE_ANOMALY_FINDINGS: str = "anomaly_findings"

SOURCE_TABLES: tuple[str, ...] = (
    SOURCE_DOCUMENT_VERIFICATIONS,
    SOURCE_VERIFICATION_FIELDS,
    SOURCE_ASSERTION_EVALUATIONS,
    SOURCE_PROCUREMENT_CASES,
    SOURCE_ANOMALY_FINDINGS,
)

#: ARCH-33's `FIELD_PATH_PREFIX`, copied for the same reason as the families.
#: A verification field under this prefix is an assertion, labelled through
#: `assertion_evaluations`, and must never be harvested twice.
ASSERTION_FIELD_PATH_PREFIX: str = "assertion:"

# ---------------------------------------------------------------------------
# The statistics
# ---------------------------------------------------------------------------

#: Below this, no model: `calibrate` returns None and everything is reviewed.
#: Equal to ARCH-33's MIN_LABELS_FOR_CALIBRATION; `verify_arch35.py` gates it.
MIN_LABELS_PLATT: int = 50

#: Isotonic regression has no parameters and fits noise on small samples.
MIN_LABELS_ISOTONIC: int = 200

#: Every fifth example (in score order) is held out: a 20% split that is
#: deterministic and stratified by score, so the held-out set covers the whole
#: range rather than whichever end a time-ordered cut happened to land on.
HOLDOUT_EVERY: int = 5

#: §6.4: expected calibration error over 15 equal-mass bins.
ECE_BINS: int = 15

#: Equal-width bins for the console's reliability diagram.
RELIABILITY_BINS: int = 10

#: The error-versus-coverage curve is thinned to at most this many points.
#: Thinning loses optimality, never validity: every point kept is exact.
CURVE_MAX_POINTS: int = 120

#: Points sampled along the fitted map for the console's curve.
FITTED_CURVE_POINTS: int = 41

#: Never certain, never impossible — ARCH-33's `_FLOOR` / `_CEILING`.
PROBABILITY_FLOOR: float = 0.00001
PROBABILITY_CEILING: float = 0.99999

#: α when a tenant has not chosen one. Decimal strings, never floats: these
#: land in numeric columns and a float 0.05 is not 0.05.
DEFAULT_TARGET_ERROR_RATE: str = "0.05"
#: `ck_cm_alpha_bounded`: 0 < α ≤ 0.2.
TARGET_ERROR_RATE_MAX: str = "0.2"

DEFAULT_AUDIT_SAMPLE_RATE: str = "0.02"
#: Audit sampling cannot be switched off. Without it the labels come only from
#: items below the threshold and the guarantee silently stops being checkable.
AUDIT_SAMPLE_RATE_MIN: str = "0.01"
AUDIT_SAMPLE_RATE_MAX: str = "0.25"

#: One-sided confidence for the Clopper-Pearson upper bound.
CLOPPER_PEARSON_CONFIDENCE: float = 0.95

#: §6.4: PSI above this suspends automatic approval.
PSI_THRESHOLD: float = 0.25
#: Equal-width bins over [0, 1]. Every score this platform produces is a unit
#: interval number, so fixed edges are always valid and never degenerate.
PSI_BINS: int = 20
#: Floor applied to an empty bin, so PSI stays finite.
PSI_EPSILON: float = 0.0001
#: Below this many scores on either side PSI is noise, and noise must not
#: suspend a tenant's automation.
MIN_PSI_SAMPLES: int = 100

#: §6.4: the realized-rate check reads the last 100 reviewed automatic passes.
MONITOR_WINDOW: int = 100
#: One-sided binomial test level for "the realized rate is above the bound".
MONITOR_SIGNIFICANCE: float = 0.05

#: Production scores in the days before a fit form the PSI reference.
REFERENCE_WINDOW_DAYS: int = 30
#: Production scores in the days before a check form the PSI sample.
RECENT_WINDOW_DAYS: int = 7

#: A model nobody has checked in this long falls back to the cold start. The
#: guarantee includes the monitoring, so an unmonitored model has none.
STALE_AFTER_HOURS: int = 72

#: Resuming a suspended decision type needs this much NEW reviewed evidence.
#: A refit on the same labels is not a fresh fit; it is the same fit.
RESUME_MIN_NEW_LABELS: int = 20

#: The most recent labels one fit reads.
MAX_LABELS_PER_FIT: int = 20000

# ---------------------------------------------------------------------------
# Wiring names
# ---------------------------------------------------------------------------

CAPABILITY_CALIBRATED_AUTONOMY: str = "capability.calibrated_autonomy"
JOB_CALIBRATION_HARVEST: str = "calibration.harvest"
JOB_CALIBRATION_REFIT: str = "calibration.refit"


def assertion_decision_type(family: str) -> str:
    """`assertion.<family>`, refusing a family ARCH-33 does not know."""
    if family not in ASSERTION_FAMILIES:
        raise ValueError(
            f"{family!r} is not an assertion family; known: "
            f"{', '.join(ASSERTION_FAMILIES)}."
        )
    return f"{ASSERTION_DECISION_PREFIX}{family}"


def family_of(decision_type: str) -> str | None:
    """The assertion family a decision type names, or None."""
    if decision_type.startswith(ASSERTION_DECISION_PREFIX):
        family = decision_type[len(ASSERTION_DECISION_PREFIX):]
        return family if family in ASSERTION_FAMILIES else None
    return None


def is_automated(decision_type: str) -> bool:
    """Whether any code path approves anything on this decision type."""
    return decision_type in AUTOMATED_DECISION_TYPES
