"""ARCH41-S2:trials — memory proves itself before it is trusted.

WHY BOTH ARMS ARE HELD FROM AUTO-APPROVAL
=========================================

A trial compares correction rates, and a correction rate exists only for a
document a human reviewed. If the control arm could auto-approve its easy
documents, the reviewed control documents would be the hard ones and memory
would "win" against a biased baseline. So while a layout is on trial, BOTH
arms go to review. It costs reviews on one layout for the length of the trial;
the alternative is a number that means nothing.

WHY AN ACTIVE LAYOUT IS HELD UNTIL RECALIBRATION
================================================

ARCH-35's conformal bound is a statement about the score distribution it was
fitted on. Memory changes that distribution. A layout that went ACTIVE after
the tenant's calibration model was fitted is held from calibrated autonomy
until the model is refitted on post-activation labels. Tenants without
calibrated autonomy use the fixed threshold, which makes no such promise, and
are not held.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.extraction_memory import (
    ExtractionExemplar,
    ExtractionMemoryApplication,
    ExtractionMemoryTrial,
    ExtractionTemplate,
)
from app.services.extraction_memory import stats
from app.services.extraction_memory import vocabulary as v


def exemplar_document_count(db: Session, template_id: uuid.UUID) -> int:
    rows = db.execute(
        select(ExtractionExemplar.work_item_id).where(ExtractionExemplar.template_id == template_id).distinct()
    ).all()
    return len(rows)


def start_if_ready(db: Session, template: ExtractionTemplate) -> Optional[ExtractionMemoryTrial]:
    if template.state != v.TEMPLATE_LEARNING:
        return None
    if exemplar_document_count(db, template.id) < v.MIN_TRIAL_EXEMPLAR_DOCS:
        return None
    trial = ExtractionMemoryTrial(id=uuid.uuid4(), workspace_id=template.workspace_id, template_id=template.id,
                                  state=v.TRIAL_RUNNING)
    db.add(trial)
    template.state = v.TEMPLATE_TRIAL
    db.flush()
    return trial


def evaluate(db: Session, trial: ExtractionMemoryTrial) -> stats.TrialDecision:
    apps = list(db.execute(
        select(ExtractionMemoryApplication).where(
            ExtractionMemoryApplication.trial_id == trial.id,
            ExtractionMemoryApplication.outcome_recorded_at.is_not(None),
            ExtractionMemoryApplication.fields_total > 0,
        )
    ).scalars())
    on = [a.fields_corrected / a.fields_total for a in apps if a.arm == v.ARM_TRIAL_ON]
    off = [a.fields_corrected / a.fields_total for a in apps if a.arm == v.ARM_TRIAL_OFF]

    reviewed: dict[uuid.UUID, list[str]] = defaultdict(list)
    if apps:
        for work_item_id, field_path in db.execute(
            select(ExtractionExemplar.work_item_id, ExtractionExemplar.field_path)
            .where(ExtractionExemplar.work_item_id.in_([a.work_item_id for a in apps]))
        ).all():
            reviewed[work_item_id].append(field_path)
    per_field: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for app in apps:
        on_arm = app.arm == v.ARM_TRIAL_ON
        for field in reviewed.get(app.work_item_id, []):
            counts = per_field[field]
            counts[1 if on_arm else 3] += 1
            if field in (app.corrected_fields or []):
                counts[0 if on_arm else 2] += 1

    decision = stats.decide(on, off, {k: tuple(c) for k, c in per_field.items()})
    trial.on_docs, trial.off_docs = len(on), len(off)
    trial.on_correction_rate = None if decision.on_rate is None else Decimal(str(round(decision.on_rate, 5)))
    trial.off_correction_rate = None if decision.off_rate is None else Decimal(str(round(decision.off_rate, 5)))
    trial.u_statistic = None if decision.u_statistic is None else Decimal(str(round(decision.u_statistic, 2)))
    trial.p_value = None if decision.p_value is None else Decimal(str(round(min(1.0, max(0.0, decision.p_value)), 6)))
    trial.decision_reason = decision.reason[:200]
    if decision.state != v.TRIAL_RUNNING:
        now = datetime.now(timezone.utc)
        trial.state, trial.decided_at = decision.state, now
        template = db.get(ExtractionTemplate, trial.template_id)
        if template is not None:
            if decision.state == v.TRIAL_PROMOTED:
                template.state, template.activated_at = v.TEMPLATE_ACTIVE, now
            else:
                template.state = v.TEMPLATE_REJECTED
    db.flush()
    return decision


def autonomy_hold(db: Session, *, work_item_id: uuid.UUID, organization_id: uuid.UUID, calibrated: bool) -> Optional[str]:
    """Why this document may not be auto-approved, or None."""
    application = db.get(ExtractionMemoryApplication, work_item_id)
    if application is None:
        return None
    if application.arm in (v.ARM_TRIAL_ON, v.ARM_TRIAL_OFF):
        return v.HOLD_TRIAL
    if application.arm != v.ARM_ACTIVE or not application.injected or not calibrated:
        return None
    template = db.get(ExtractionTemplate, application.template_id) if application.template_id else None
    if template is None or template.activated_at is None:
        return v.HOLD_RECALIBRATION
    from app.models.calibration import CalibrationModelVersion as CalibrationModel
    from app.services.calibration import vocabulary as cal_vocab

    refit = db.execute(
        select(CalibrationModel.id).where(
            CalibrationModel.organization_id == organization_id,
            CalibrationModel.decision_type == v.AUTONOMY_DECISION_TYPE,
            CalibrationModel.status.in_(cal_vocab.LIVE_STATUSES),
            CalibrationModel.fitted_at >= template.activated_at,
        ).limit(1)
    ).first()
    return None if refit is not None else v.HOLD_RECALIBRATION
