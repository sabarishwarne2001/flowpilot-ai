"""ARCH41-S2:drift — memory that stops earning its place is withdrawn.

Rules: an ACTIVE rule whose live precision (its candidate vs the reviewer's
value, counted at harvest) falls below 90% over at least 20 reviewed documents
is RETIRED. Layouts: an ACTIVE layout whose memory documents, over the last 60
days and at least 30 reviews, need MORE corrections than the control arm of the
trial that promoted it goes back to LEARNING — memory stops being applied until
a new trial proves it again.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.extraction_memory import (
    ExtractionAnchorRule,
    ExtractionMemoryApplication,
    ExtractionMemoryTrial,
    ExtractionTemplate,
)
from app.services.extraction_memory import vocabulary as v


def retire_drifting_rules(db: Session, *, workspace_id: uuid.UUID) -> list[str]:
    retired: list[str] = []
    now = datetime.now(timezone.utc)
    for rule in db.execute(select(ExtractionAnchorRule).where(
            ExtractionAnchorRule.workspace_id == workspace_id,
            ExtractionAnchorRule.state == v.RULE_ACTIVE)).scalars():
        if rule.live_total >= v.RULE_DRIFT_MIN_OBS and rule.live_hits / rule.live_total < v.RULE_DRIFT_PRECISION:
            rule.state, rule.retired_at, rule.retired_reason = v.RULE_RETIRED, now, "DRIFT"
            retired.append(str(rule.id))
    db.flush()
    return retired


def demote_drifting_templates(db: Session, *, workspace_id: uuid.UUID) -> list[str]:
    demoted: list[str] = []
    since = datetime.now(timezone.utc) - timedelta(days=v.DRIFT_WINDOW_DAYS)
    for template in db.execute(select(ExtractionTemplate).where(
            ExtractionTemplate.workspace_id == workspace_id,
            ExtractionTemplate.state == v.TEMPLATE_ACTIVE)).scalars():
        baseline = db.execute(
            select(ExtractionMemoryTrial.off_correction_rate).where(
                ExtractionMemoryTrial.template_id == template.id,
                ExtractionMemoryTrial.state == v.TRIAL_PROMOTED,
            ).order_by(ExtractionMemoryTrial.decided_at.desc()).limit(1)
        ).scalar_one_or_none()
        if baseline is None:
            continue
        rates = [
            a.fields_corrected / a.fields_total
            for a in db.execute(select(ExtractionMemoryApplication).where(
                ExtractionMemoryApplication.template_id == template.id,
                ExtractionMemoryApplication.arm == v.ARM_ACTIVE,
                ExtractionMemoryApplication.outcome_recorded_at >= since,
                ExtractionMemoryApplication.fields_total > 0)).scalars()
        ]
        if len(rates) >= v.TEMPLATE_DRIFT_MIN_DOCS and sum(rates) / len(rates) > float(baseline):
            template.state = v.TEMPLATE_LEARNING
            demoted.append(str(template.id))
    db.flush()
    return demoted
