"""ARCH-35 — anchored, idempotent patches for every file this phase modifies.

New files are delivered whole. This script touches only files that already
exist, the way every `apply_<phase>.py` in this repo does:

  * ANCHORED. Every edit names an exact substring and how many times it must
    occur. A file not in the state the patch was written against fails loudly
    with nothing written.
  * IDEMPOTENT. Each patch carries a SENTINEL that is a substring of the text
    the patch itself writes. A second run reports `already applied`.
    `apply_patch` asserts that relationship after every edit.
  * BOM AND CRLF PRESERVING, because this repo is developed on Windows.

WHAT IS PATCHED, AND WHY
========================

ARCH-33 seam (the forward dependency this phase closes)
  app/services/assertions/calibration.py   the estimator behind the interface
  app/services/assertions/triage.py        stored model + audit demotion
  app/models/assertion.py                  the ORM half of fk_ae_calibration_model

ARCH-13 re-pointing
  app/services/document_verification_service.py
      `triage` asks `apply.decide_verification`; `resolve` accepts every field
      when calibrated autonomy held the verification for a full review.

Registration
  app/core/entitlements.py, app/api/capability_gate.py, app/models/__init__.py,
  app/api/v1/router.py, and the three-place worker rule:
  app/workers/handlers/__init__.py, app/workers/profiles.py,
  app/workers/scheduler.py.

TWO DEFECTS IN SHIPPED PHASES, FIXED HERE BECAUSE THIS PHASE STANDS ON THEM
==========================================================================

1. `capability.anomaly_radar` (ARCH-34) is in CAPABILITY_KEYS but was never
   registered in `_ENTITLEMENTS`. `capability_gate.has_capability` raises
   ValueError for an unregistered key, so every radar request failed with a
   500 instead of answering, and `shape_violation` refused the key in any tier
   version, so no plan could carry it. Registered below, beside ARCH-35's own.

2. `useCapabilityAccess` (ARCH-31 Step 4) reads a `capabilities` list from
   `GET /organizations/{id}/entitlements`, and the response never had one. So
   `granted` was false for every tenant and every capability-gated console
   page rendered its lock card, entitled or not. The response now carries the
   capabilities the tier grants, resolved once per request.

HISTORICAL GATES WHOSE HEAD ASSERTIONS MUST WIDEN
=================================================

`verify_arch34.py`, `verify_arch31.py` and `verify_arch31_step0.py` assert the
Alembic head against a literal list that ends at `arch34_step1_radar`. Adding
`arch35_step1_calibration` is the only change to those files, and it is the
same change ARCH-32, ARCH-33 and ARCH-34 each made. `verify_arch33.py` needs
no edit and is not touched.

Usage:

    python apply_arch35.py --check     # report, write nothing
    python apply_arch35.py             # apply
    python apply_arch35.py             # again: every line reads "already applied"
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"


# ---------------------------------------------------------------------------
# Sentinels. Each must be a substring of the text its own patch writes.
# ---------------------------------------------------------------------------

SENTINEL_CALIBRATION = "ARCH35-S1:estimator-replaced"
SENTINEL_TRIAGE = "ARCH35-S1:stored-calibration-model"
SENTINEL_ASSERTION_MODEL = "ARCH35-S1:calibration-model-fk"
SENTINEL_VERIFICATION = "ARCH35-S1:verification-calibrated-autonomy"
SENTINEL_ENTITLEMENTS = "ARCH35-S1:capability-calibrated-autonomy-key"
SENTINEL_GATE = "ARCH35-S1:capability-calibrated-autonomy-display"
SENTINEL_ENTITLEMENT_SCHEMA = "ARCH35-S1:capabilities-listed"
SENTINEL_ENTITLEMENT_API = "ARCH35-S1:capabilities-resolved"
SENTINEL_MODELS = "# ARCH-35 — calibrated autonomy and conformal risk control."
SENTINEL_ROUTER = "ARCH35-S1:autonomy-router"
SENTINEL_HANDLERS = "ARCH35-S1:calibration-handlers"
SENTINEL_PROFILE = "ARCH35-S1:calibration-light-profile"
SENTINEL_SCHEDULER = "ARCH35-S1:calibration-schedule"
SENTINEL_V34 = "ARCH35-S1:head-widened-34"
SENTINEL_V31 = "ARCH35-S1:head-widened-31"
SENTINEL_V31S0 = "ARCH35-S1:head-widened-31s0"
SENTINEL_FE_PATHS = "ARCH35-S3:autonomy-path"
SENTINEL_FE_ROUTE = "ARCH35-S3:autonomy-route"
SENTINEL_FE_NAV = "ARCH35-S3:autonomy-nav"
SENTINEL_FE_SECTION = "ARCH35-S3:autonomy-section"
SENTINEL_FE_KEYS = "ARCH35-S3:autonomy-keys"
SENTINEL_FE_ENDPOINTS = "ARCH35-S3:autonomy-endpoints"
SENTINEL_FE_ENTITLEMENTS = "ARCH35-S3:capabilities-typed"
SENTINEL_FE_REVIEW = "ARCH35-S3:review-all-fields"


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _read(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _write(path: Path, text: str, newline: str, had_bom: bool) -> None:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


# ===========================================================================
# 1. app/services/assertions/calibration.py — the estimator behind ARCH-33's
#    interface. `fit`, `calibrate`, `effective_threshold`, `consequence` and
#    `CalibrationModel` keep their signatures; every new field has a default.
# ===========================================================================

CAL_DOC_ANCHOR = """WHY ISOTONIC AND NOT PLATT
=========================="""

CAL_DOC_REPLACEMENT = """ARCH35-S1:estimator-replaced — WHAT ARCH-35 CHANGED BEHIND THIS INTERFACE
===========================================================================

`fit` now delegates to `app.services.calibration`: PLATT between 50 and 199
labels, ISOTONIC (scikit-learn, `out_of_bounds='clip'`) from 200, the
held-out ECE refusal, and nothing below 50 — exactly as before, an unfitted
model and a None from `calibrate`. `calibrate` evaluates the stored map for
its method. `effective_threshold` additionally honours a STORED model's
conformal threshold and suspension, never lowering the administrator's own
setting. Every caller of this module sees the same five names with the same
signatures; the new `CalibrationModel` fields all have defaults.

The section below is ARCH-33's original reasoning for choosing isotonic. It
still holds from 200 labels up; below that, ARCH-35 uses Platt, because an
isotonic map on eighty labels fits the noise (see `estimators.py`).

WHY ISOTONIC AND NOT PLATT (ARCH-33)
===================================="""

CAL_IMPORT_ANCHOR = """from app.services.assertions import vocabulary as vocab
"""

CAL_IMPORT_REPLACEMENT = """from app.services.assertions import vocabulary as vocab
from app.services.calibration import estimators as _estimators
from app.services.calibration import fit as _fitting
"""

CAL_MODEL_ANCHOR = """    engine_version: str = vocab.ENGINE_VERSION
    examples: tuple[LabeledExample, ...] = field(default_factory=tuple)

    def as_details(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "fitted": self.fitted,"""

CAL_MODEL_REPLACEMENT = """    engine_version: str = vocab.ENGINE_VERSION
    examples: tuple[LabeledExample, ...] = field(default_factory=tuple)
    #: ARCH-35. Which estimator produced `parameters`: ISOTONIC or PLATT.
    #: `points` stays populated for display (for Platt, a sampled curve).
    method: str = "ISOTONIC"
    #: The persisted map — `calibration_models.breakpoints`.
    parameters: Any = field(default_factory=dict)
    #: ARCH-35. Present only for a STORED model: its conformal threshold, its
    #: audit rate and whether it is suspended. None for ARCH-33's per-call fit.
    autonomy: Any = None
    #: Why a fit over enough labels was refused, when it was.
    rejected_reason: str = ""

    def as_details(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "method": self.method,
            "fitted": self.fitted,"""

CAL_FIT_DOC_ANCHOR = '''    """Pool-adjacent-violators isotonic fit. Deterministic, O(n log n).'''

CAL_FIT_DOC_REPLACEMENT = '''    """ARCH-35's estimator: Platt from 50 labels, isotonic from 200.'''

CAL_FIT_BODY_ANCHOR = """    # --- pool adjacent violators ------------------------------------------
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
"""

CAL_FIT_BODY_REPLACEMENT = """    # --- ARCH-35: the estimator behind the interface ------------------------
    #
    # `fit_decision` selects the method by label count, fits on 80% of the
    # examples, and refuses the fit when the held-out ECE is worse than the
    # raw score's. A refused fit is an UNFITTED model: `calibrate` returns
    # None and the evaluation goes to review, which is the same cold-start
    # path as too few labels, enforced at the same three levels.
    outcome = _fitting.fit_decision(
        [
            _fitting.Example(raw_score=float(e.raw_score), correct=bool(e.correct))
            for e in ordered
        ],
        target_error_rate=0.05,
    )
    if not outcome.usable:
        return CalibrationModel(
            family=family,
            points=(),
            label_count=len(ordered),
            fitted=False,
            model_id=None,
            examples=ordered,
            method=outcome.method,
            rejected_reason=outcome.rejected_reason,
        )

    points = tuple(
        CalibrationPoint(
            score=Decimal(repr(score)).quantize(
                PROBABILITY_PLACES, rounding=ROUND_HALF_UP
            ),
            probability=_quantize(Decimal(repr(probability))),
        )
        for score, probability in _estimators.sample_curve(outcome.fitted)
    )

    return CalibrationModel(
        family=family,
        points=points,
        label_count=len(ordered),
        fitted=True,
        model_id=model_id,
        examples=ordered,
        method=outcome.method,
        parameters=dict(outcome.fitted.parameters),
    )
"""

CAL_CALIBRATE_ANCHOR = """    if model is None or not model.fitted or not model.points:
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
"""

CAL_CALIBRATE_REPLACEMENT = """    if model is None or not model.fitted or not model.points:
        return None

    score = Decimal(str(raw))

    # ARCH-35: evaluate the persisted map for its method. Isotonic maps
    # interpolate linearly between breakpoints and clip at both ends — they
    # say nothing outside the range they saw, and extrapolating an increasing
    # trend past the last observation is how a calibrator invents confidence.
    # Platt maps are the fitted sigmoid with a non-negative slope.
    if model.parameters:
        value = _estimators.evaluate(model.method, model.parameters, score)
        if value is None:
            return None
        return _quantize(Decimal(repr(value)))

    # A model built by hand from points alone (no stored parameters): the
    # breakpoints ARE the isotonic map.
    breakpoints = {
        "x": [str(point.score) for point in model.points],
        "y": [str(point.probability) for point in model.points],
    }
    value = _estimators.evaluate("ISOTONIC", breakpoints, score)
    return _quantize(Decimal(repr(value)))
"""

CAL_THRESHOLD_ANCHOR = """    chosen = Decimal(str(configured))
    if model is None or not model.fitted:
        return max(chosen, Decimal(vocab.COLD_START_THRESHOLD))
    return chosen
"""

CAL_THRESHOLD_REPLACEMENT = """    chosen = Decimal(str(configured))
    if model is None or not model.fitted:
        return max(chosen, Decimal(vocab.COLD_START_THRESHOLD))
    terms = getattr(model, "autonomy", None)
    if terms is not None:
        # ARCH-35. A stored model that is paused, or whose error limit is not
        # achievable, reviews everything: probabilities are clamped below 1,
        # so a threshold of exactly 1 is never met. Otherwise the conformal
        # threshold RAISES the administrator's setting and never lowers it.
        if not terms.allows_automation:
            return Decimal("1")
        return max(chosen, Decimal(str(terms.threshold)))
    return chosen
"""


# ===========================================================================
# 2. app/services/assertions/triage.py
# ===========================================================================

TRIAGE_MODEL_ANCHOR = """    return calibration.fit(
        labels_for(db, organization_id=organization_id, family=family),
        family=family,
    )
"""

TRIAGE_MODEL_REPLACEMENT = """    # ARCH35-S1:stored-calibration-model. With capability.calibrated_autonomy
    # the stored, versioned model in `calibration_models` is the only source:
    # its id is what `calibration_model_id` now references. Without it this
    # returns None and ARCH-33's per-call fit applies, exactly as before.
    from app.services.calibration import apply as calibrated_autonomy

    stored = calibrated_autonomy.assertion_model(
        db, organization_id=organization_id, family=family
    )
    if stored is not None:
        return stored
    return calibration.fit(
        labels_for(db, organization_id=organization_id, family=family),
        family=family,
    )
"""

TRIAGE_DECIDE_ANCHOR = """    decision = routing.decide(
        verdict=evaluation_result.verdict,
        raw_score=evaluation_result.raw_score,
        calibrated_probability=probability,
        effective_threshold=threshold,
    )

    verification: Optional[DocumentVerification] = None
"""

TRIAGE_DECIDE_REPLACEMENT = """    decision = routing.decide(
        verdict=evaluation_result.verdict,
        raw_score=evaluation_result.raw_score,
        calibrated_probability=probability,
        effective_threshold=threshold,
    )

    # ARCH-35. A stored model may still hold back a PASS route: a paused
    # decision type, or an audit sample. `withhold_assertion` only ever turns
    # PASS into TRIAGE; `routing.decide` stays the only route to `pass`.
    from app.services.calibration import apply as calibrated_autonomy
    from app.services.calibration.labels import assertion_sample_key

    audit_key = assertion_sample_key(definition.id, work_item_id, node_run_id)
    decision, audit_rate = calibrated_autonomy.withhold_assertion(
        decision, model=model, sample_key=audit_key
    )

    verification: Optional[DocumentVerification] = None
"""

TRIAGE_AUDIT_ANCHOR = """            evidence=evaluation_result.evidence,
        )

    evaluation = AssertionEvaluation("""

TRIAGE_AUDIT_REPLACEMENT = """            evidence=evaluation_result.evidence,
        )
        if audit_rate is not None:
            # Recorded where the label harvester reads it, so the reviewer's
            # answer is labelled as an audit and weighted 1/r.
            calibrated_autonomy.record_assertion_audit(
                verification, sample_key=audit_key, audit_rate=audit_rate
            )

    evaluation = AssertionEvaluation("""


# ===========================================================================
# 3. app/models/assertion.py — the ORM half of fk_ae_calibration_model
# ===========================================================================

AE_FK_ANCHOR = """    #: ARCH-35's model version, when there is one. No foreign key, by design:
    #: the table it will reference does not exist yet, and ARCH-35 adds the
    #: constraint in its own migration rather than ARCH-33 inventing a table
    #: for a phase that has not been specified.
    calibration_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
"""

AE_FK_REPLACEMENT = """    #: ARCH-35's model version, when there is one.
    #:
    #: ARCH35-S1:calibration-model-fk. ARCH-33 created this column bare and
    #: nullable so ARCH-35 could attach the constraint in its own migration
    #: (`arch35_step1_calibration`) without touching ARCH-33's table. SET NULL:
    #: an evaluation is evidence of a decision and outlives the model version
    #: that informed it.
    calibration_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "calibration_models.id",
            ondelete="SET NULL",
            name="fk_ae_calibration_model",
        ),
        nullable=True,
    )
"""


# ===========================================================================
# 4. app/services/document_verification_service.py
# ===========================================================================

DV_TRIAGE_ANCHOR = """    if consensus.all_agreed and consensus.confidence >= threshold:
        verification.status = VerificationStatus.AGREED
        verification.auto_approved = True
    elif consensus.confidence >= threshold:"""

DV_TRIAGE_REPLACEMENT = """    # ARCH35-S1:verification-calibrated-autonomy. With
    # capability.calibrated_autonomy, `auto_approved` is decided by the
    # tenant's calibrated model for `verification.document`: the calibrated
    # probability against the conformal threshold, with suspension and audit
    # sampling. Without it `decide_verification` returns None and the fixed
    # threshold below applies, exactly as before.
    #
    # A verification held back by calibration is marked `review_all_fields`:
    # the reviewer confirms EVERY field, because a document-level label is only
    # evidence if every field on the document was looked at.
    from app.services.calibration import apply as calibrated_autonomy

    autonomy = calibrated_autonomy.decide_verification(
        db, verification=verification, confidence=consensus.confidence
    )
    if autonomy is not None:
        verification.details = {
            **(verification.details or {}),
            "calibration": {
                **autonomy.as_details(),
                "decision_type": "verification.document",
                "review_all_fields": not autonomy.auto_allowed,
            },
        }
        if autonomy.auto_allowed:
            verification.status = (
                VerificationStatus.AGREED
                if consensus.all_agreed
                else VerificationStatus.AUTO_APPROVED
            )
            verification.auto_approved = True
        else:
            verification.status = VerificationStatus.DISAGREED
            verification.auto_approved = False
    elif consensus.all_agreed and consensus.confidence >= threshold:
        verification.status = VerificationStatus.AGREED
        verification.auto_approved = True
    elif consensus.confidence >= threshold:"""

DV_RESOLVE_ANCHOR = """    disagreed = {
        f.field_path: f
        for f in verification.fields
        if not f.agreed and not f.field_path.startswith(FIELD_PATH_PREFIX)
    }
"""

DV_RESOLVE_REPLACEMENT = """    # ARCH-35. A verification calibrated autonomy held back asks the reviewer
    # about EVERY extracted field, agreed or not; see `triage` above.
    review_all = bool(
        ((verification.details or {}).get("calibration") or {}).get(
            "review_all_fields"
        )
    )
    disagreed = {
        f.field_path: f
        for f in verification.fields
        if (review_all or not f.agreed)
        and not f.field_path.startswith(FIELD_PATH_PREFIX)
    }
"""


# ===========================================================================
# 5. app/core/entitlements.py
# ===========================================================================

ENT_ALL_ANCHOR = """    "ANOMALY_RADAR_CAPABILITY",
"""

ENT_ALL_REPLACEMENT = """    "ANOMALY_RADAR_CAPABILITY",
    "CALIBRATED_AUTONOMY_CAPABILITY",
"""

ENT_KEY_ANCHOR = """ANOMALY_RADAR_CAPABILITY: str = "capability.anomaly_radar"
"""

ENT_KEY_REPLACEMENT = """ANOMALY_RADAR_CAPABILITY: str = "capability.anomaly_radar"

#: ARCH35-S1:capability-calibrated-autonomy-key. Calibrated autonomy and
#: conformal risk control. A CAPABILITY, not an ADDON, for the reason every
#: phase since ARCH-31 records: ADDON_KEYS is asserted equal to
#: entitlement_service's priced catalog at import. Not metered either:
#: refitting is platform maintenance, and the value is the reviews a tenant
#: no longer has to do. Tenants without it keep the fixed thresholds.
CALIBRATED_AUTONOMY_CAPABILITY: str = "capability.calibrated_autonomy"
"""

ENT_TUPLE_ANCHOR = """    SEMANTIC_ASSERTIONS_CAPABILITY,
    ANOMALY_RADAR_CAPABILITY,
)"""

ENT_TUPLE_REPLACEMENT = """    SEMANTIC_ASSERTIONS_CAPABILITY,
    ANOMALY_RADAR_CAPABILITY,
    CALIBRATED_AUTONOMY_CAPABILITY,
)"""

ENT_REGISTRY_ANCHOR = """            "uncertain or failing to review with the paragraph attached. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
)"""

ENT_REGISTRY_REPLACEMENT = """            "uncertain or failing to review with the paragraph attached. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
    # ARCH-34 defect, fixed by ARCH-35: the radar key was declared and put in
    # CAPABILITY_KEYS but never registered here, so has_capability raised on
    # every radar request and no tier version could carry the key.
    Entitlement(
        name=ANOMALY_RADAR_CAPABILITY,
        description=(
            "Forensic audit radar: duplicate documents, unit-price surges and "
            "contract drift, each raised with the evidence side by side. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
    # ARCH-35
    Entitlement(
        name=CALIBRATED_AUTONOMY_CAPABILITY,
        description=(
            "Calibrated autonomy: automatic approval decided by each tenant's "
            "own reviewed outcomes, with a stated, measured bound on the error "
            "rate of what is approved without a human. Bundled into a tier."
        ),
    ),
)"""


# ===========================================================================
# 6. app/api/capability_gate.py
# ===========================================================================

GATE_DISPLAY_ANCHOR = """    entitlements.ANOMALY_RADAR_CAPABILITY: "Forensic audit radar",
}"""

GATE_DISPLAY_REPLACEMENT = """    entitlements.ANOMALY_RADAR_CAPABILITY: "Forensic audit radar",
    # ARCH35-S1:capability-calibrated-autonomy-display.
    entitlements.CALIBRATED_AUTONOMY_CAPABILITY: "Calibrated autonomy",
}"""

GATE_FN_ANCHOR = """def require_capability(
"""

GATE_FN_REPLACEMENT = """def granted_capabilities(db: Session, *, organization_id: Any) -> list[str]:
    \"\"\"Every capability key the organization's tier carries, in key order.

    Resolves the tier ONCE, through the same reading `has_capability` uses —
    `tier.entries[].limit_key` — so the console's list and the request-path
    gate cannot disagree.
    \"\"\"
    from app.services import quota_service

    tier = quota_service.resolve_tier(db, organization_id=organization_id)
    if tier is None:
        return []
    held = {
        getattr(entry, "limit_key", None)
        for entry in getattr(tier, "entries", ()) or ()
    }
    return [key for key in entitlements.CAPABILITY_KEYS if key in held]


def require_capability(
"""

GATE_ALL_ANCHOR = """__all__ = ["CAPABILITY_REQUIRED_CODE", "has_capability", "require_capability"]"""

GATE_ALL_REPLACEMENT = """__all__ = [
    "CAPABILITY_REQUIRED_CODE",
    "granted_capabilities",
    "has_capability",
    "require_capability",
]"""


# ===========================================================================
# 7. Entitlements response — the list the console's hook always expected
# ===========================================================================

ENT_SCHEMA_ANCHOR = """    addons: list[AddonAccessResponse]
"""

ENT_SCHEMA_REPLACEMENT = """    addons: list[AddonAccessResponse]
    # ARCH35-S1:capabilities-listed. `useCapabilityAccess` has always read this
    # field; until ARCH-35 it did not exist, so every capability-gated page
    # showed its lock card to entitled tenants too.
    capabilities: list[str] = Field(
        default_factory=list,
        description="Tier-bundled capability keys this organization holds.",
    )
"""

ENT_API_ANCHOR = """    return OrganizationEntitlementsResponse(
        organization_id=organization_id, as_of=now, addons=addons
    )"""

ENT_API_REPLACEMENT = """    # ARCH35-S1:capabilities-resolved. Tier resolved once for the list.
    from app.api import capability_gate

    return OrganizationEntitlementsResponse(
        organization_id=organization_id,
        as_of=now,
        addons=addons,
        capabilities=capability_gate.granted_capabilities(
            db, organization_id=organization_id
        ),
    )"""


# ===========================================================================
# 8. app/models/__init__.py
# ===========================================================================

MODELS_IMPORT_ANCHOR = """from app.models.radar import (  # noqa: F401
    AnomalyFinding,
    AnomalySuppression,
    DocumentFingerprint,
)"""

MODELS_IMPORT_REPLACEMENT = """from app.models.radar import (  # noqa: F401
    AnomalyFinding,
    AnomalySuppression,
    DocumentFingerprint,
)
from app.models.calibration import (  # noqa: F401
    CalibrationLabel,
    CalibrationModelVersion,
)"""

MODELS_ALL_ANCHOR = """    # ARCH-34 — cross-document anomaly and duplicate radar.
    "AnomalyFinding",
    "AnomalySuppression",
    "DocumentFingerprint","""

MODELS_ALL_REPLACEMENT = """    # ARCH-34 — cross-document anomaly and duplicate radar.
    "AnomalyFinding",
    "AnomalySuppression",
    "DocumentFingerprint",
    # ARCH-35 — calibrated autonomy and conformal risk control.
    "CalibrationLabel",
    "CalibrationModelVersion","""


# ===========================================================================
# 9. app/api/v1/router.py
# ===========================================================================

ROUTER_IMPORT_ANCHOR = """    anomalies,
    assertions,"""

ROUTER_IMPORT_REPLACEMENT = """    anomalies,
    assertions,
    autonomy,"""

ROUTER_INCLUDE_ANCHOR = """api_router.include_router(anomalies.router)"""

ROUTER_INCLUDE_REPLACEMENT = """api_router.include_router(anomalies.router)
# ARCH35-S1:autonomy-router. ARCH-35 calibrated autonomy. Every route is
# capability-gated, including the reads: the reliability diagram is the
# platform's measured accuracy on this tenant's documents, which is the product.
api_router.include_router(autonomy.router)"""


# ===========================================================================
# 10-12. The three-place worker rule
# ===========================================================================

HANDLERS_PHASE_ANCHOR = """ARCH34_JOB_TYPES: frozenset[str] = frozenset(
    {"anomaly.scan_document", "anomaly.nightly"}
)
"""

HANDLERS_PHASE_REPLACEMENT = """ARCH34_JOB_TYPES: frozenset[str] = frozenset(
    {"anomaly.scan_document", "anomaly.nightly"}
)
ARCH35_JOB_TYPES: frozenset[str] = frozenset(
    {"calibration.harvest", "calibration.refit"}
)
"""

HANDLERS_UNION_ANCHOR = """    | ARCH34_JOB_TYPES
)"""

HANDLERS_UNION_REPLACEMENT = """    | ARCH34_JOB_TYPES
    | ARCH35_JOB_TYPES
)"""

HANDLERS_FN_ANCHOR = """_HANDLERS = {
    "document.extract": _document_extract,"""

HANDLERS_FN_REPLACEMENT = '''def _calibration_harvest(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.calibration import handle_calibration_harvest
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        # Commits per organization itself: one tenant's failure must not roll
        # back another tenant's labels.
        return handle_calibration_harvest(db, payload)


def _calibration_refit(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.calibration import handle_calibration_refit
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_calibration_refit(db, payload)


_HANDLERS = {
    "document.extract": _document_extract,'''

HANDLERS_MAP_ANCHOR = """    "anomaly.scan_document": _anomaly_scan_document,
    "anomaly.nightly": _anomaly_nightly,
}"""

HANDLERS_MAP_REPLACEMENT = """    "anomaly.scan_document": _anomaly_scan_document,
    "anomaly.nightly": _anomaly_nightly,
    # ARCH35-S1:calibration-handlers. Both are also on the LIGHT profile in
    # app/workers/profiles.py and in DEFAULT_SCHEDULE in
    # app/workers/scheduler.py. assert_imports_match_profile() raises
    # ProfileError at every worker's startup on a handler no profile claims.
    "calibration.harvest": _calibration_harvest,
    "calibration.refit": _calibration_refit,
}"""

HANDLERS_EXPORT_ANCHOR = """    "ARCH34_JOB_TYPES",
"""

HANDLERS_EXPORT_REPLACEMENT = """    "ARCH34_JOB_TYPES",
    "ARCH35_JOB_TYPES",
"""

PROFILE_ANCHOR = """            "anomaly.scan_document",
            "anomaly.nightly",
        }
    ),
    allow_heavy=frozenset(),"""

PROFILE_REPLACEMENT = """            "anomaly.scan_document",
            "anomaly.nightly",
            # ARCH35-S1:calibration-light-profile. ARCH-35 calibration. A fit
            # is at most 20,000 labels through scikit-learn's isotonic
            # regression or a two-parameter Newton iteration, plus one SciPy
            # quantile — milliseconds, and nothing under
            # app/services/calibration/ imports a model, OCR or a PDF engine.
            "calibration.harvest",
            "calibration.refit",
        }
    ),
    allow_heavy=frozenset(),"""

SCHEDULER_ANCHOR = """    ScheduledJob(
        job_type="identity.sweep_replay_guard",
        interval_seconds=3_600,
        description="Prune expired SAML replay-guard entries (ARCH-16).",
    ),"""

SCHEDULER_REPLACEMENT = """    # ARCH35-S1:calibration-schedule. Harvest hourly, so a newly entitled
    # tenant waits at most an hour for its first fitted model. Refit and
    # monitor nightly at 05:00, after ARCH-34's 04:00 sweep: the monitor also
    # refreshes last_checked_at, and a model nobody checks for three days
    # falls back to the cold start.
    ScheduledJob(
        job_type="calibration.harvest",
        interval_seconds=3_600,
        description="Harvest reviewer outcomes into calibration labels (ARCH-35).",
    ),
    ScheduledJob(
        job_type="calibration.refit",
        interval_seconds=86_400,
        at_hour=5,
        description="Refit calibration models and run drift monitoring (ARCH-35).",
    ),
    ScheduledJob(
        job_type="identity.sweep_replay_guard",
        interval_seconds=3_600,
        description="Prune expired SAML replay-guard entries (ARCH-16).",
    ),"""


# ===========================================================================
# 13. Historical head assertions
# ===========================================================================

V34_ANCHOR = """        check("alembic head is arch34_step1_radar", head == "arch34_step1_radar", str(head))"""

V34_REPLACEMENT = """        # ARCH35-S1:head-widened-34. ARCH-35 moves the head forward; this gate
        # certifies that ARCH-34's schema is present at or after its own head.
        check(
            "alembic head is arch34_step1_radar or later",
            head in ("arch34_step1_radar", "arch35_step1_calibration"),
            str(head),
        )"""

V31_ANCHOR = """        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"]), heads"""

V31_REPLACEMENT = """        # ARCH35-S1:head-widened-31
        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"]), heads"""

V31S0_ANCHOR = """        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"]), heads"""

V31S0_REPLACEMENT = """        # ARCH35-S1:head-widened-31s0
        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"]), heads"""


# ===========================================================================
# 14. Frontend
# ===========================================================================

FE_PATHS_PATTERN_ANCHOR = """  organizationMarketplace: "marketplace","""

FE_PATHS_PATTERN_REPLACEMENT = """  organizationMarketplace: "marketplace",
  // ARCH35-S3:autonomy-path. Organization-scoped: a calibrated model is fitted
  // per (organization, decision type), across every workspace's reviews.
  // "organizations" is already a reserved segment, so no new entry is needed.
  organizationAutonomy: "autonomy","""

FE_PATHS_HELPER_ANCHOR = """export const organizationMarketplacePath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/marketplace`;
"""

FE_PATHS_HELPER_REPLACEMENT = """export const organizationMarketplacePath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/marketplace`;

export const organizationAutonomyPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/autonomy`;
"""

FE_ROUTE_IMPORT_ANCHOR = """const MarketplaceCatalog = lazy("""

FE_ROUTE_IMPORT_REPLACEMENT = """// ARCH35-S3:autonomy-route
const AutonomySettings = lazy(
  () => import("@/pages/autonomy/AutonomySettings"),
);
const MarketplaceCatalog = lazy("""

FE_ROUTE_ELEMENT_ANCHOR = """                      element={<MarketplaceCatalog />}
                    />"""

FE_ROUTE_ELEMENT_REPLACEMENT = """                      element={<MarketplaceCatalog />}
                    />
                    {/* ARCH-35. The page resolves the organization and the
                        capability from the same hooks every gated page
                        uses, so the gate cannot be forgotten at a call site. */}
                    <Route
                      path={ROUTE_PATTERNS.organizationAutonomy}
                      element={<AutonomySettings />}
                    />"""

FE_NAV_ICON_ANCHOR = """  Store,
  Sliders,"""

FE_NAV_ICON_REPLACEMENT = """  Store,
  Sliders,
  Target,"""

FE_NAV_PATH_ANCHOR = """  organizationMarketplacePath,
"""

FE_NAV_PATH_REPLACEMENT = """  organizationMarketplacePath,
  organizationAutonomyPath,
"""

FE_NAV_ITEM_ANCHOR = """    items.push({
      name: "Partner marketplace",
      path: organizationMarketplacePath(orgSlug),
      icon: Store,
    });
"""

FE_NAV_ITEM_REPLACEMENT = """    items.push({
      name: "Partner marketplace",
      path: organizationMarketplacePath(orgSlug),
      icon: Store,
    });
    // ARCH35-S3:autonomy-nav. ADMIN reads why a decision type is paused and
    // how accurate the platform has been; changing the error limit and
    // resuming are OWNER-gated by RequireOrgOwner on the endpoints.
    items.push({
      name: "Calibrated autonomy",
      path: organizationAutonomyPath(orgSlug),
      icon: Target,
    });
"""

FE_SECTION_ANCHOR = """  "Audit log": "Governance & security","""

FE_SECTION_REPLACEMENT = """  "Audit log": "Governance & security",
  // ARCH35-S3:autonomy-section
  "Calibrated autonomy": "Governance & security","""

FE_KEYS_ANCHOR = """/** ARCH-31 — procurement matching. Workspace-scoped, like work items. */"""

FE_KEYS_REPLACEMENT = """/**
 * ARCH35-S3:autonomy-keys. Organization-scoped: one calibrated model per
 * (organization, decision type).
 */
export const autonomyKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "autonomy"] as const,
  overview: (organizationId: string) =>
    [...autonomyKeys.all(organizationId), "overview"] as const,
  reliability: (organizationId: string, decisionType: string) =>
    [...autonomyKeys.all(organizationId), "reliability", decisionType] as const,
};

/** ARCH-31 — procurement matching. Workspace-scoped, like work items. */"""

FE_ENDPOINTS_ANCHOR = """/** ARCH-30 Tranche 2 (D-8) — add-on entitlements and add-on checkout. */"""

FE_ENDPOINTS_REPLACEMENT = """/** ARCH35-S3:autonomy-endpoints — calibrated autonomy. */
export const AUTONOMY_ENDPOINTS = {
  overview: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/autonomy`,
  settings: (organizationId: string, decisionType: string): string =>
    `/organizations/${org(organizationId)}/autonomy/${seg(decisionType)}`,
  reliability: (organizationId: string, decisionType: string): string =>
    `/organizations/${org(organizationId)}/autonomy/${seg(decisionType)}/reliability`,
  resume: (organizationId: string, decisionType: string): string =>
    `/organizations/${org(organizationId)}/autonomy/${seg(decisionType)}/resume`,
} as const;

/** ARCH-30 Tranche 2 (D-8) — add-on entitlements and add-on checkout. */"""

FE_ENTITLEMENTS_ANCHOR = """export interface OrganizationEntitlements {
  readonly organization_id: string;
  readonly as_of: string;
  readonly addons: readonly AddonAccess[];
}"""

FE_ENTITLEMENTS_REPLACEMENT = """export interface OrganizationEntitlements {
  readonly organization_id: string;
  readonly as_of: string;
  readonly addons: readonly AddonAccess[];
  /** ARCH35-S3:capabilities-typed. Tier-bundled capability keys held. */
  readonly capabilities: readonly string[];
}"""

FE_REVIEW_HOOK_ANCHOR = """  const detail = detailQuery.data ?? null;
"""

FE_REVIEW_HOOK_REPLACEMENT = """  const detail = detailQuery.data ?? null;

  // ARCH35-S3:review-all-fields. A verification calibrated autonomy held back
  // asks about EVERY extracted field, and an audit sample was one the platform
  // would have approved on its own. Assertion fields are never answered here:
  // they are resolved in the clause review queue, and the server refuses a
  // value for them on this endpoint.
  const calibrationDetails = (detail?.details?.["calibration"] ?? null) as {
    readonly review_all_fields?: boolean;
    readonly audit_sample?: boolean;
  } | null;
  const reviewAll = Boolean(calibrationDetails?.review_all_fields);
  const isAudit = Boolean(calibrationDetails?.audit_sample);
  const isReviewable = useCallback(
    (field: VerificationFieldResponse): boolean =>
      !field.field_path.startsWith("assertion:") && (reviewAll || !field.agreed),
    [reviewAll],
  );
"""

FE_REVIEW_ACCEPT_ANCHOR = """      if (!field.agreed) {
        values[field.field_path] = field.consensus_value;
      }
    });
    resolve.mutate(values);
  }, [detail, resolve]);"""

FE_REVIEW_ACCEPT_REPLACEMENT = """      if (isReviewable(field)) {
        values[field.field_path] = field.consensus_value;
      }
    });
    resolve.mutate(values);
  }, [detail, resolve, isReviewable]);"""

FE_REVIEW_SUBMIT_ANCHOR = """      if (field.agreed) {
        return;
      }"""

FE_REVIEW_SUBMIT_REPLACEMENT = """      if (!isReviewable(field)) {
        return;
      }"""

FE_REVIEW_SUBMIT_DEPS_ANCHOR = """  }, [detail, edits, resolve]);"""

FE_REVIEW_SUBMIT_DEPS_REPLACEMENT = """  }, [detail, edits, resolve, isReviewable]);"""

FE_REVIEW_LIST_ANCHOR = """            <ul className="mt-3 space-y-3">
              {detail.fields
                .filter((field) => !field.agreed)"""

FE_REVIEW_LIST_REPLACEMENT = """            {reviewAll ? (
              <p className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
                {isAudit
                  ? "Accuracy audit: this document would have been approved automatically. Confirm or correct every field — audits are how the error limit stays checkable."
                  : "Held for review by calibrated autonomy. Confirm or correct every field, including the ones the agents agreed on."}
              </p>
            ) : null}

            <ul className="mt-3 space-y-3">
              {detail.fields
                .filter(isReviewable)"""

FE_REVIEW_EMPTY_ANCHOR = """            {detail.fields.filter((f) => !f.agreed).length === 0 && ("""

FE_REVIEW_EMPTY_REPLACEMENT = """            {detail.fields.filter(isReviewable).length === 0 && ("""


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def _frontend_present(_: str) -> Optional[str]:
    return None


PATCHES: list[FilePatch] = [
    FilePatch(
        root=BACKEND,
        relpath="app/services/assertions/calibration.py",
        sentinel=SENTINEL_CALIBRATION,
        edits=[
            Edit(CAL_DOC_ANCHOR, CAL_DOC_REPLACEMENT, 1, "module header"),
            Edit(CAL_IMPORT_ANCHOR, CAL_IMPORT_REPLACEMENT, 1, "imports"),
            Edit(CAL_MODEL_ANCHOR, CAL_MODEL_REPLACEMENT, 1, "CalibrationModel fields"),
            Edit(CAL_FIT_DOC_ANCHOR, CAL_FIT_DOC_REPLACEMENT, 1, "fit docstring"),
            Edit(CAL_FIT_BODY_ANCHOR, CAL_FIT_BODY_REPLACEMENT, 1, "fit body"),
            Edit(CAL_CALIBRATE_ANCHOR, CAL_CALIBRATE_REPLACEMENT, 1, "calibrate body"),
            Edit(CAL_THRESHOLD_ANCHOR, CAL_THRESHOLD_REPLACEMENT, 1, "effective_threshold"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/services/assertions/triage.py",
        sentinel=SENTINEL_TRIAGE,
        edits=[
            Edit(TRIAGE_MODEL_ANCHOR, TRIAGE_MODEL_REPLACEMENT, 1, "calibration_model_for"),
            Edit(TRIAGE_DECIDE_ANCHOR, TRIAGE_DECIDE_REPLACEMENT, 1, "audit demotion"),
            Edit(TRIAGE_AUDIT_ANCHOR, TRIAGE_AUDIT_REPLACEMENT, 1, "audit record"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/models/assertion.py",
        sentinel=SENTINEL_ASSERTION_MODEL,
        edits=[Edit(AE_FK_ANCHOR, AE_FK_REPLACEMENT, 1, "calibration_model_id FK")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/services/document_verification_service.py",
        sentinel=SENTINEL_VERIFICATION,
        edits=[
            Edit(DV_TRIAGE_ANCHOR, DV_TRIAGE_REPLACEMENT, 1, "triage"),
            Edit(DV_RESOLVE_ANCHOR, DV_RESOLVE_REPLACEMENT, 1, "resolve"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/core/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENTS,
        edits=[
            Edit(ENT_ALL_ANCHOR, ENT_ALL_REPLACEMENT, 1, "__all__"),
            Edit(ENT_KEY_ANCHOR, ENT_KEY_REPLACEMENT, 1, "key"),
            Edit(ENT_TUPLE_ANCHOR, ENT_TUPLE_REPLACEMENT, 1, "CAPABILITY_KEYS"),
            Edit(ENT_REGISTRY_ANCHOR, ENT_REGISTRY_REPLACEMENT, 1, "_ENTITLEMENTS"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/capability_gate.py",
        sentinel=SENTINEL_GATE,
        edits=[
            Edit(GATE_DISPLAY_ANCHOR, GATE_DISPLAY_REPLACEMENT, 1, "_DISPLAY_NAMES"),
            Edit(GATE_FN_ANCHOR, GATE_FN_REPLACEMENT, 1, "granted_capabilities"),
            Edit(GATE_ALL_ANCHOR, GATE_ALL_REPLACEMENT, 1, "__all__"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/schemas/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENT_SCHEMA,
        edits=[Edit(ENT_SCHEMA_ANCHOR, ENT_SCHEMA_REPLACEMENT, 1, "capabilities field")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/v1/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENT_API,
        edits=[Edit(ENT_API_ANCHOR, ENT_API_REPLACEMENT, 1, "capabilities resolved")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/models/__init__.py",
        sentinel=SENTINEL_MODELS,
        edits=[
            Edit(MODELS_IMPORT_ANCHOR, MODELS_IMPORT_REPLACEMENT, 1, "model import"),
            Edit(MODELS_ALL_ANCHOR, MODELS_ALL_REPLACEMENT, 1, "model __all__"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/api/v1/router.py",
        sentinel=SENTINEL_ROUTER,
        edits=[
            Edit(ROUTER_IMPORT_ANCHOR, ROUTER_IMPORT_REPLACEMENT, 1, "router import"),
            Edit(ROUTER_INCLUDE_ANCHOR, ROUTER_INCLUDE_REPLACEMENT, 1, "include_router"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/handlers/__init__.py",
        sentinel=SENTINEL_HANDLERS,
        edits=[
            Edit(HANDLERS_PHASE_ANCHOR, HANDLERS_PHASE_REPLACEMENT, 1, "phase set"),
            Edit(HANDLERS_UNION_ANCHOR, HANDLERS_UNION_REPLACEMENT, 1, "phase union"),
            Edit(HANDLERS_FN_ANCHOR, HANDLERS_FN_REPLACEMENT, 1, "handler thunks"),
            Edit(HANDLERS_MAP_ANCHOR, HANDLERS_MAP_REPLACEMENT, 1, "handler map"),
            Edit(HANDLERS_EXPORT_ANCHOR, HANDLERS_EXPORT_REPLACEMENT, 1, "__all__"),
        ],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/profiles.py",
        sentinel=SENTINEL_PROFILE,
        edits=[Edit(PROFILE_ANCHOR, PROFILE_REPLACEMENT, 1, "LIGHT job types")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="app/workers/scheduler.py",
        sentinel=SENTINEL_SCHEDULER,
        edits=[Edit(SCHEDULER_ANCHOR, SCHEDULER_REPLACEMENT, 1, "DEFAULT_SCHEDULE")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="verify_arch34.py",
        sentinel=SENTINEL_V34,
        edits=[Edit(V34_ANCHOR, V34_REPLACEMENT, 1, "head assertion")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="verify_arch31.py",
        sentinel=SENTINEL_V31,
        edits=[Edit(V31_ANCHOR, V31_REPLACEMENT, 1, "head assertion")],
    ),
    FilePatch(
        root=BACKEND,
        relpath="verify_arch31_step0.py",
        sentinel=SENTINEL_V31S0,
        edits=[Edit(V31S0_ANCHOR, V31S0_REPLACEMENT, 1, "head assertion")],
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/routes/tenantPaths.ts",
        sentinel=SENTINEL_FE_PATHS,
        edits=[
            Edit(FE_PATHS_PATTERN_ANCHOR, FE_PATHS_PATTERN_REPLACEMENT, 1, "pattern"),
            Edit(FE_PATHS_HELPER_ANCHOR, FE_PATHS_HELPER_REPLACEMENT, 1, "path helper"),
        ],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/App.tsx",
        sentinel=SENTINEL_FE_ROUTE,
        edits=[
            Edit(FE_ROUTE_IMPORT_ANCHOR, FE_ROUTE_IMPORT_REPLACEMENT, 1, "lazy import"),
            Edit(FE_ROUTE_ELEMENT_ANCHOR, FE_ROUTE_ELEMENT_REPLACEMENT, 1, "route"),
        ],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/components/layout/navigation.ts",
        sentinel=SENTINEL_FE_NAV,
        edits=[
            Edit(FE_NAV_ICON_ANCHOR, FE_NAV_ICON_REPLACEMENT, 1, "icon import"),
            Edit(FE_NAV_PATH_ANCHOR, FE_NAV_PATH_REPLACEMENT, 1, "path import"),
            Edit(FE_NAV_ITEM_ANCHOR, FE_NAV_ITEM_REPLACEMENT, 1, "nav item"),
        ],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/components/layout/OrganizationSidebarNavigation.tsx",
        sentinel=SENTINEL_FE_SECTION,
        edits=[Edit(FE_SECTION_ANCHOR, FE_SECTION_REPLACEMENT, 1, "section map")],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/services/api/queryKeys.ts",
        sentinel=SENTINEL_FE_KEYS,
        edits=[Edit(FE_KEYS_ANCHOR, FE_KEYS_REPLACEMENT, 1, "autonomyKeys")],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/services/api/endpoints.ts",
        sentinel=SENTINEL_FE_ENDPOINTS,
        edits=[Edit(FE_ENDPOINTS_ANCHOR, FE_ENDPOINTS_REPLACEMENT, 1, "AUTONOMY_ENDPOINTS")],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/types/entitlements.ts",
        sentinel=SENTINEL_FE_ENTITLEMENTS,
        edits=[Edit(FE_ENTITLEMENTS_ANCHOR, FE_ENTITLEMENTS_REPLACEMENT, 1, "capabilities")],
        precondition=_frontend_present,
    ),
    FilePatch(
        root=FRONTEND,
        relpath="src/pages/Verification/VerificationReviewQueue.tsx",
        sentinel=SENTINEL_FE_REVIEW,
        edits=[
            Edit(FE_REVIEW_HOOK_ANCHOR, FE_REVIEW_HOOK_REPLACEMENT, 1, "reviewable fields"),
            Edit(FE_REVIEW_ACCEPT_ANCHOR, FE_REVIEW_ACCEPT_REPLACEMENT, 1, "accept all"),
            Edit(FE_REVIEW_SUBMIT_ANCHOR, FE_REVIEW_SUBMIT_REPLACEMENT, 1, "submit edits"),
            Edit(FE_REVIEW_SUBMIT_DEPS_ANCHOR, FE_REVIEW_SUBMIT_DEPS_REPLACEMENT, 1, "submit deps"),
            Edit(FE_REVIEW_LIST_ANCHOR, FE_REVIEW_LIST_REPLACEMENT, 1, "field list"),
            Edit(FE_REVIEW_EMPTY_ANCHOR, FE_REVIEW_EMPTY_REPLACEMENT, 1, "empty state"),
        ],
        precondition=_frontend_present,
    ),
]


class PatchError(RuntimeError):
    pass


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = patch.root / patch.relpath
    if not path.exists():
        raise PatchError(f"{patch.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"SKIP  {patch.relpath}: {reason}"

    if patch.sentinel in text:
        return f"OK    {patch.relpath}: already applied"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{patch.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if patch.sentinel not in updated:
        raise PatchError(
            f"{patch.relpath}: sentinel {patch.sentinel!r} is absent from the "
            "patched text. A sentinel must be a substring of what its own "
            "patch writes, or the next run re-applies the edit."
        )

    if check_only:
        return f"WOULD {patch.relpath}: {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"WROTE {patch.relpath}: {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-35 modified-file patches")
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    print("ARCH-35 — Calibrated Autonomy & Conformal Risk Control: modified-file patches")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()

    # Validate every patch before writing any, so a failure half-way through
    # never leaves the tree partially patched.
    try:
        for patch in PATCHES:
            apply_patch(patch, check_only=True)
    except PatchError as exc:
        print(f"  FAIL  {exc}")
        return 1

    results: list[str] = []
    try:
        for patch in PATCHES:
            results.append(apply_patch(patch, check_only=args.check))
    except PatchError as exc:
        for line in results:
            print(f"  {line}")
        print(f"\n  FAIL  {exc}")
        return 1

    for line in results:
        print(f"  {line}")

    pending = sum(1 for line in results if line.startswith(("WOULD", "WROTE")))
    print()
    if args.check:
        print(f"{pending} file(s) would change. Nothing was written.")
    else:
        print(f"{pending} file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
