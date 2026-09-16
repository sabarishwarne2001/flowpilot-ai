"""ARCH-35 §6.7 — what the Autonomy settings console reads.

Every sentence a tenant reads about a promise is assembled here from stored
numbers, never generated. The console renders these strings verbatim, as the
ARCH-34 radar renders its headlines, so the wording that states a guarantee is
one module away from the arithmetic that backs it — not a component away.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models.calibration import CalibrationModelVersion
from app.services.calibration import labels as labels_module
from app.services.calibration import monitor, refit
from app.services.calibration import vocabulary as vocab

__all__ = ["entry_for", "overview", "reliability", "summary_sentence", "pct"]


def pct(value: Any, places: int = 1) -> str:
    number = Decimal(str(value)) * 100
    if number == number.to_integral_value():
        return f"{int(number)}%"
    return f"{number:.{places}f}%"


def summary_sentence(
    row: Optional[CalibrationModelVersion],
    *,
    label_count: int,
    decision_type: str,
    stale: bool,
) -> str:
    automated = vocab.is_automated(decision_type)
    if row is None:
        if label_count == 0:
            return (
                "No reviewed documents yet. Everything goes to review until "
                f"{vocab.MIN_LABELS_PLATT} have been reviewed."
            )
        return (
            f"{label_count} reviewed so far. The first accuracy check runs within "
            "the hour; until then everything goes to review."
        )
    if row.method == vocab.METHOD_PRIOR:
        return (
            "Not enough reviewed documents yet to estimate accuracy; everything "
            f"goes to review until {vocab.MIN_LABELS_PLATT} are reviewed "
            f"({row.label_count} so far)."
        )
    if row.status == vocab.STATUS_SUSPENDED:
        return row.suspended_reason or "Automatic approval is paused."
    if stale:
        return (
            "The nightly accuracy check has not run recently, so no limit can be "
            "vouched for and everything goes to review."
        )
    diagnostics = row.diagnostics or {}
    if not automated:
        return (
            f"Measured on {row.label_count} reviewed items. Nothing is approved "
            "automatically on this decision; the figures show how often the "
            "engine is right on your documents."
        )
    if not diagnostics.get("achievable"):
        reason = diagnostics.get("risk_reason") or ""
        return reason or (
            f"A {pct(row.target_error_rate)} limit is not achievable on the "
            "reviewed documents so far; everything goes to review."
        )
    return (
        f"Over the last {row.label_count} reviewed documents like these, at your "
        f"current setting about {pct(row.auto_share)} would be approved "
        f"automatically, with wrong automatic approvals held to at most "
        f"{pct(row.target_error_rate)} of all documents. Among automatic "
        f"approvals, the error rate is at most {pct(row.clopper_pearson_upper)} "
        f"with {int(vocab.CLOPPER_PEARSON_CONFIDENCE * 100)}% confidence — on "
        "documents like the ones you have reviewed."
    )


def entry_for(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    row = refit.live_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    latest = refit.latest_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    count = labels_module.label_count(
        db, organization_id=organization_id, decision_type=decision_type
    )
    alpha, rate = refit.settings_for(
        db, organization_id=organization_id, decision_type=decision_type
    )
    stale = row is not None and monitor.is_stale(row.last_checked_at, now)
    diagnostics = (row.diagnostics if row is not None else {}) or {}
    last_rejection = (
        (latest.diagnostics or {}).get("rejected_reason")
        if latest is not None and latest.status == vocab.STATUS_REJECTED
        else None
    )

    return {
        "decision_type": decision_type,
        "display_name": vocab.DECISION_LABELS.get(decision_type, decision_type),
        "automated": vocab.is_automated(decision_type),
        "label_count": count,
        "model_id": None if row is None else row.id,
        "method": None if row is None else row.method,
        "status": None if row is None else row.status,
        "target_error_rate": alpha,
        "audit_sample_rate": rate,
        "threshold": None if row is None else row.threshold,
        "conformal_bound": None if row is None else row.conformal_bound,
        "clopper_pearson_upper": None if row is None else row.clopper_pearson_upper,
        "auto_share": None if row is None else row.auto_share,
        "ece_before": None if row is None else row.ece_before,
        "ece_after": None if row is None else row.ece_after,
        "brier_after": None if row is None else row.brier_after,
        "achievable": bool(diagnostics.get("achievable")),
        "stale": bool(stale),
        "fitted_at": None if row is None else row.fitted_at,
        "last_checked_at": None if row is None else row.last_checked_at,
        "suspended_at": None if row is None else row.suspended_at,
        "suspended_reason": None if row is None else row.suspended_reason,
        "last_rejection": last_rejection,
        "summary": summary_sentence(
            row, label_count=count, decision_type=decision_type, stale=bool(stale)
        ),
    }


def overview(
    db: Session, *, organization_id: uuid.UUID, now: Optional[datetime] = None
) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    return [
        entry_for(db, organization_id=organization_id, decision_type=dt, now=now)
        for dt in vocab.DECISION_TYPES
    ]


def reliability(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    entry = entry_for(
        db, organization_id=organization_id, decision_type=decision_type, now=now
    )
    row = refit.live_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    diagnostics = (row.diagnostics if row is not None else {}) or {}
    return {
        "entry": entry,
        "holdout_count": int(diagnostics.get("holdout_count") or 0),
        "audit_labels": int(diagnostics.get("audit_labels") or 0),
        "reliability": list(diagnostics.get("reliability") or []),
        "reliability_raw": list(diagnostics.get("reliability_raw") or []),
        "fitted_curve": list(diagnostics.get("fitted_curve") or []),
        "coverage_curve": list(diagnostics.get("coverage_curve") or []),
        "last_check": diagnostics.get("last_check"),
        "psi_threshold": vocab.PSI_THRESHOLD,
        "confidence": vocab.CLOPPER_PEARSON_CONFIDENCE,
        "min_labels": vocab.MIN_LABELS_PLATT,
        "min_labels_isotonic": vocab.MIN_LABELS_ISOTONIC,
        "target_error_rate_max": Decimal(vocab.TARGET_ERROR_RATE_MAX),
        "audit_sample_rate_min": Decimal(vocab.AUDIT_SAMPLE_RATE_MIN),
        "audit_sample_rate_max": Decimal(vocab.AUDIT_SAMPLE_RATE_MAX),
    }

