"""ARCH-35 §6.4 / §6.6 — persisting model versions, drift suspension, resume.

THE LIFECYCLE OF ONE (TENANT, DECISION TYPE)
============================================

    labels ──refit──► ACTIVE ──monitor──► ACTIVE (last_checked_at moves)
                         │          └───► SUSPENDED ──fresh evidence + refit──► ACTIVE
                         └─refit with new labels──► SUPERSEDED (a new ACTIVE replaces it)
    a fit that made ECE worse ──► REJECTED (the live model stays in force)

`uq_cm_active` allows one ACTIVE-or-SUSPENDED row per pair, so replacing a
live model is two statements in one transaction: the old row becomes
SUPERSEDED, then the new row is inserted.

SUSPENSION IS INHERITED UNTIL IT IS EARNED BACK
===============================================

A suspended pair can still be refitted — an owner can change α, new labels
can arrive — but the new version inherits the suspension unless at least
`RESUME_MIN_NEW_LABELS` reviews were recorded after the pause. Those reviews
exist quickly, because while a pair is paused every decision of that type goes
to a person. A fit over the same evidence that caused the pause is the same
fit, and it must not quietly lift it.

ROUNDING IS ALWAYS IN THE CONSERVATIVE DIRECTION
================================================

λ and both bounds are rounded UP to the column's precision. A bound rounded
down could read as within α while the unrounded bound was not, and a threshold
rounded down admits documents the search did not admit. ECE and Brier use
half-up rounding, which is monotone, so `ck_cm_improves` still holds after it.

THIS MODULE HOLDS THE SESSION. It flushes; the caller commits.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.calibration import CalibrationModelVersion
from app.services.calibration import fit as fitting
from app.services.calibration import labels as labels_module
from app.services.calibration import monitor
from app.services.calibration import vocabulary as vocab

logger = logging.getLogger("app.services.calibration.refit")

__all__ = [
    "RefitResult",
    "ResumeResult",
    "SettingsError",
    "live_model",
    "latest_model",
    "settings_for",
    "validate_settings",
    "refit",
    "check",
    "resume",
    "update_settings",
]


class SettingsError(ValueError):
    """α or the audit rate is outside what the database will accept."""


@dataclass(frozen=True)
class RefitResult:
    outcome: str  # "fitted" | "unchanged" | "rejected" | "held"
    model: Optional[CalibrationModelVersion]
    live: Optional[CalibrationModelVersion]
    message: str = ""


@dataclass(frozen=True)
class ResumeResult:
    resumed: bool
    message: str
    model: Optional[CalibrationModelVersion]


def _q(value: float, places: str, rounding: str) -> Decimal:
    number = Decimal(repr(float(value)))
    number = min(Decimal("1"), max(Decimal("0"), number))
    return number.quantize(Decimal(places), rounding=rounding)


def live_model(
    db: Session, *, organization_id: uuid.UUID, decision_type: str
) -> Optional[CalibrationModelVersion]:
    return db.execute(
        select(CalibrationModelVersion).where(
            CalibrationModelVersion.organization_id == organization_id,
            CalibrationModelVersion.decision_type == decision_type,
            CalibrationModelVersion.status.in_(vocab.LIVE_STATUSES),
        )
    ).scalar_one_or_none()


def latest_model(
    db: Session, *, organization_id: uuid.UUID, decision_type: str
) -> Optional[CalibrationModelVersion]:
    return db.execute(
        select(CalibrationModelVersion)
        .where(
            CalibrationModelVersion.organization_id == organization_id,
            CalibrationModelVersion.decision_type == decision_type,
        )
        .order_by(CalibrationModelVersion.fitted_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def settings_for(
    db: Session, *, organization_id: uuid.UUID, decision_type: str
) -> tuple[Decimal, Decimal]:
    """`(α, audit rate)` most recently chosen for the pair, or the defaults.

    Read from the LATEST version of any status, not the live one: an owner who
    changes α and gets a refused fit has still made that choice, and the next
    refit must honour it rather than silently reverting to the old limit. The
    live model keeps enforcing the α it was fitted at until a fit passes.
    """
    source = latest_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    if source is None:
        return (
            Decimal(vocab.DEFAULT_TARGET_ERROR_RATE),
            Decimal(vocab.DEFAULT_AUDIT_SAMPLE_RATE),
        )
    return Decimal(source.target_error_rate), Decimal(source.audit_sample_rate)


def validate_settings(target_error_rate: Any, audit_sample_rate: Any) -> tuple[Decimal, Decimal]:
    try:
        alpha = Decimal(str(target_error_rate))
        rate = Decimal(str(audit_sample_rate))
    except Exception as exc:  # noqa: BLE001
        raise SettingsError("Both values must be decimal numbers.") from exc
    if not (Decimal("0") < alpha <= Decimal(vocab.TARGET_ERROR_RATE_MAX)):
        raise SettingsError(
            "The error limit must be above 0% and at most "
            f"{Decimal(vocab.TARGET_ERROR_RATE_MAX) * 100:.0f}%."
        )
    if alpha != alpha.quantize(Decimal("0.00001")):
        raise SettingsError("The error limit can have at most five decimal places.")
    low = Decimal(vocab.AUDIT_SAMPLE_RATE_MIN)
    high = Decimal(vocab.AUDIT_SAMPLE_RATE_MAX)
    if not (low <= rate <= high):
        raise SettingsError(
            f"The audit share must be between {low * 100:.0f}% and "
            f"{high * 100:.0f}%. Audits cannot be switched off: without them "
            "the limit stops being checkable."
        )
    if rate != rate.quantize(Decimal("0.0001")):
        raise SettingsError("The audit share can have at most four decimal places.")
    return alpha, rate


def _reference(
    db: Session, *, organization_id: uuid.UUID, decision_type: str, now: datetime
) -> dict[str, Any]:
    scores = labels_module.recent_scores(
        db,
        organization_id=organization_id,
        decision_type=decision_type,
        since=now - timedelta(days=vocab.REFERENCE_WINDOW_DAYS),
        until=now,
    )
    return {
        "histogram": [round(v, 6) for v in monitor.histogram(scores)],
        "count": len(scores),
        "window_days": vocab.REFERENCE_WINDOW_DAYS,
        "bins": vocab.PSI_BINS,
    }


def _build_row(
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    outcome: fitting.FitOutcome,
    alpha: Decimal,
    rate: Decimal,
    digest: str,
    reference: dict[str, Any],
    now: datetime,
) -> CalibrationModelVersion:
    control = outcome.risk
    usable = outcome.status == vocab.STATUS_ACTIVE and not outcome.fitted.is_prior
    achievable = usable and control.achievable and control.auto_share > 0

    threshold = (
        _q(control.threshold, "0.00001", ROUND_CEILING) if achievable else Decimal("1")
    )
    auto_share = (
        _q(control.auto_share, "0.00001", ROUND_FLOOR) if achievable else Decimal("0")
    )
    if achievable and auto_share <= 0:
        threshold = Decimal("1")
    diagnostics = dict(outcome.diagnostics)
    diagnostics["psi_reference"] = reference
    diagnostics["achievable"] = bool(achievable and auto_share > 0)

    return CalibrationModelVersion(
        id=uuid.uuid4(),
        organization_id=organization_id,
        decision_type=decision_type,
        method=outcome.method,
        label_count=outcome.label_count,
        breakpoints=dict(outcome.fitted.parameters),
        ece_before=_q(outcome.ece_before, "0.000001", ROUND_HALF_UP),
        ece_after=_q(outcome.ece_after, "0.000001", ROUND_HALF_UP),
        brier_after=_q(outcome.brier_after, "0.000001", ROUND_HALF_UP),
        target_error_rate=alpha,
        threshold=threshold,
        conformal_bound=_q(control.conformal_bound, "0.00001", ROUND_CEILING),
        clopper_pearson_upper=_q(control.clopper_pearson_upper, "0.00001", ROUND_CEILING),
        auto_share=auto_share,
        audit_sample_rate=rate,
        status=outcome.status,
        input_digest=digest,
        diagnostics=diagnostics,
        fitted_at=now,
        last_checked_at=now if outcome.status == vocab.STATUS_ACTIVE else None,
    )


def refit(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    now: Optional[datetime] = None,
    target_error_rate: Any = None,
    audit_sample_rate: Any = None,
    force: bool = False,
    resuming: bool = False,
) -> RefitResult:
    """Fit a new version from stored labels, unless nothing it depends on moved."""
    if decision_type not in vocab.DECISION_TYPES:
        raise ValueError(f"{decision_type!r} is not a decision type")
    now = now or datetime.now(timezone.utc)

    current_alpha, current_rate = settings_for(
        db, organization_id=organization_id, decision_type=decision_type
    )
    alpha, rate = validate_settings(
        current_alpha if target_error_rate is None else target_error_rate,
        current_rate if audit_sample_rate is None else audit_sample_rate,
    )

    rows = labels_module.labels_for_fit(
        db, organization_id=organization_id, decision_type=decision_type
    )
    examples = fitting.examples_from_labels(rows)
    digest = fitting.input_digest(
        examples, target_error_rate=alpha, audit_sample_rate=rate
    )

    live = live_model(db, organization_id=organization_id, decision_type=decision_type)
    latest = latest_model(
        db, organization_id=organization_id, decision_type=decision_type
    )
    if not force and latest is not None and latest.input_digest == digest:
        return RefitResult(outcome="unchanged", model=latest, live=live)

    inherit_suspension = False
    if live is not None and live.status == vocab.STATUS_SUSPENDED and not resuming:
        fresh = (
            labels_module.labels_since(
                db,
                organization_id=organization_id,
                decision_type=decision_type,
                since=live.suspended_at or live.fitted_at,
            )
            if live.suspended_at is not None
            else 0
        )
        inherit_suspension = fresh < vocab.RESUME_MIN_NEW_LABELS

    outcome = fitting.fit_decision(examples, target_error_rate=float(alpha))
    reference = _reference(
        db, organization_id=organization_id, decision_type=decision_type, now=now
    )
    row = _build_row(
        organization_id=organization_id,
        decision_type=decision_type,
        outcome=outcome,
        alpha=alpha,
        rate=rate,
        digest=digest,
        reference=reference,
        now=now,
    )

    if outcome.status == vocab.STATUS_REJECTED:
        # Never live. The model in force, if any, stays in force.
        row.last_checked_at = None
        db.add(row)
        db.flush()
        logger.warning(
            "calibration.fit_rejected",
            extra={
                "organization_id": str(organization_id),
                "decision_type": decision_type,
                "ece_before": str(row.ece_before),
                "ece_after": str(row.ece_after),
            },
        )
        return RefitResult(
            outcome="rejected",
            model=row,
            live=live,
            message=outcome.rejected_reason,
        )

    if inherit_suspension and live is not None:
        row.status = vocab.STATUS_SUSPENDED
        row.suspended_reason = live.suspended_reason
        row.suspended_at = live.suspended_at

    if live is not None:
        live.status = vocab.STATUS_SUPERSEDED
        db.flush([live])
    db.add(row)
    db.flush()

    logger.info(
        "calibration.fitted",
        extra={
            "organization_id": str(organization_id),
            "decision_type": decision_type,
            "method": row.method,
            "label_count": row.label_count,
            "status": row.status,
            "threshold": str(row.threshold),
            "auto_share": str(row.auto_share),
        },
    )
    return RefitResult(
        outcome="held" if inherit_suspension else "fitted",
        model=row,
        live=row,
        message=(
            "A new version was fitted and stays paused until newly reviewed "
            "documents show the limit holds."
            if inherit_suspension
            else ""
        ),
    )


def _notify_suspended(
    db: Session, *, model: CalibrationModelVersion
) -> None:
    from app.models.notification import NotificationPriority, NotificationType
    from app.services import organization_notification_service as notices

    label = vocab.DECISION_LABELS.get(model.decision_type, model.decision_type)
    notices.emit_to_roles(
        db,
        organization_id=model.organization_id,
        roles=notices.SECURITY_ROLES,
        title=f"Automatic approval paused: {label}",
        message=model.suspended_reason or "Automatic approval was paused.",
        notification_type=NotificationType.AUTOMATION,
        priority=NotificationPriority.WARNING,
    )


def check(
    db: Session,
    *,
    model: CalibrationModelVersion,
    now: Optional[datetime] = None,
    notify: bool = True,
) -> monitor.DriftVerdict:
    """Run both drift checks on a live model and suspend it if either fires."""
    now = now or datetime.now(timezone.utc)
    if model.status not in vocab.LIVE_STATUSES:
        raise ValueError("only a live model is monitored")

    if model.method == vocab.METHOD_PRIOR or model.status == vocab.STATUS_SUSPENDED:
        verdict = monitor.DriftVerdict(
            suspend=False,
            notes=(
                "no map in force"
                if model.method == vocab.METHOD_PRIOR
                else "already paused",
            ),
        )
    else:
        reference = (model.diagnostics or {}).get("psi_reference") or {}
        recent = labels_module.recent_scores(
            db,
            organization_id=model.organization_id,
            decision_type=model.decision_type,
            since=now - timedelta(days=vocab.RECENT_WINDOW_DAYS),
            until=now,
        )
        wrong, total = labels_module.realized_since(
            db,
            organization_id=model.organization_id,
            decision_type=model.decision_type,
            since=model.fitted_at,
        )
        verdict = monitor.drift_verdict(
            reference_histogram=[float(v) for v in reference.get("histogram") or []],
            reference_count=int(reference.get("count") or 0),
            recent_scores=recent,
            realized_wrong=wrong,
            realized_total=total,
            bound=float(model.clopper_pearson_upper),
            when=now.strftime("%d %b %Y"),
        )

    diagnostics = dict(model.diagnostics or {})
    diagnostics["last_check"] = {**verdict.as_payload(), "at": now.isoformat()}
    model.diagnostics = diagnostics
    model.last_checked_at = now

    if verdict.suspend and model.status == vocab.STATUS_ACTIVE:
        model.status = vocab.STATUS_SUSPENDED
        model.suspended_reason = verdict.reason
        model.suspended_at = now
        db.flush([model])
        if notify:
            _notify_suspended(db, model=model)
        logger.warning(
            "calibration.suspended",
            extra={
                "organization_id": str(model.organization_id),
                "decision_type": model.decision_type,
                **verdict.as_payload(),
            },
        )
    else:
        db.flush([model])
    return verdict


def resume(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    now: Optional[datetime] = None,
) -> ResumeResult:
    """§6.6: refused unless a fresh fit passes."""
    now = now or datetime.now(timezone.utc)
    live = live_model(db, organization_id=organization_id, decision_type=decision_type)
    if live is None or live.status != vocab.STATUS_SUSPENDED:
        return ResumeResult(
            resumed=False,
            message="Automatic approval is not paused for this decision.",
            model=live,
        )

    fresh = labels_module.labels_since(
        db,
        organization_id=organization_id,
        decision_type=decision_type,
        since=live.suspended_at or live.fitted_at,
    )
    if fresh < vocab.RESUME_MIN_NEW_LABELS:
        return ResumeResult(
            resumed=False,
            message=(
                f"Not yet: {fresh} document(s) have been reviewed since the "
                f"pause, and {vocab.RESUME_MIN_NEW_LABELS} are needed to check "
                "the limit again on fresh evidence."
            ),
            model=live,
        )

    result = refit(
        db,
        organization_id=organization_id,
        decision_type=decision_type,
        now=now,
        force=True,
        resuming=True,
    )
    if result.outcome == "rejected" or result.live is None:
        return ResumeResult(
            resumed=False,
            message=(
                "The fresh check did not pass: "
                + (result.message or "calibration did not improve on the raw score.")
            ),
            model=live,
        )

    model = result.live
    if model.method == vocab.METHOD_PRIOR:
        return ResumeResult(
            resumed=True,
            message=(
                "Resumed, but there are not yet enough reviewed documents for "
                "automatic approval; everything still goes to review."
            ),
            model=model,
        )

    verdict = check(db, model=model, now=now, notify=False)
    if verdict.suspend:
        return ResumeResult(
            resumed=False,
            message=verdict.reason,
            model=model,
        )
    achieved = bool((model.diagnostics or {}).get("achievable"))
    return ResumeResult(
        resumed=True,
        message=(
            "Resumed. The limit was checked again on newly reviewed documents."
            if achieved
            else "Resumed, but the chosen limit is not achievable on the "
            "current evidence, so everything still goes to review."
        ),
        model=model,
    )


def update_settings(
    db: Session,
    *,
    organization_id: uuid.UUID,
    decision_type: str,
    target_error_rate: Any,
    audit_sample_rate: Any,
    now: Optional[datetime] = None,
) -> RefitResult:
    alpha, rate = validate_settings(target_error_rate, audit_sample_rate)
    return refit(
        db,
        organization_id=organization_id,
        decision_type=decision_type,
        now=now,
        target_error_rate=alpha,
        audit_sample_rate=rate,
        force=False,
    )
