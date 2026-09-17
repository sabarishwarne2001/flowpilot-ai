"""ARCH-37 — reading and writing `automation_rule_triggers`.

The automation handler asks one question: which active rules in this
workspace listen to this event? The rule API asks the reverse when it saves.
Both live here so the join and the write cannot disagree.
"""

from __future__ import annotations

import uuid
from typing import Iterable, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.automation import AutomationRule
from app.models.automation_trigger import AutomationRuleTrigger
from app.services.automation.triggers import CATALOG_EVENT_TYPES


def active_rules_for_event(
    db: Session, *, workspace_id: uuid.UUID, event_type: str
) -> list[AutomationRule]:
    if event_type not in CATALOG_EVENT_TYPES:
        return []
    stmt = (
        select(AutomationRule)
        .join(AutomationRuleTrigger, AutomationRuleTrigger.rule_id == AutomationRule.id)
        .where(
            AutomationRuleTrigger.workspace_id == workspace_id,
            AutomationRuleTrigger.event_type == event_type,
            AutomationRule.workspace_id == workspace_id,
            AutomationRule.is_active.is_(True),
        )
        .order_by(AutomationRule.priority.asc(), AutomationRule.created_at.asc())
    )
    return list(db.execute(stmt).scalars().unique().all())


def set_event_types(db: Session, *, rule: AutomationRule, event_types: Iterable[str]) -> list[str]:
    """Replace the rule's trigger rows. Unknown events are refused."""
    wanted = sorted(set(event_types))
    unknown = [e for e in wanted if e not in CATALOG_EVENT_TYPES]
    if unknown:
        raise ValueError(f"Not trigger events: {unknown}")
    db.execute(delete(AutomationRuleTrigger).where(AutomationRuleTrigger.rule_id == rule.id))
    db.flush()
    for event_type in wanted:
        db.add(
            AutomationRuleTrigger(
                rule_id=rule.id, event_type=event_type, workspace_id=rule.workspace_id
            )
        )
    db.flush()
    db.expire(rule, ["trigger_rows"])
    return wanted


def event_types_of(db: Session, *, rule_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    if not rule_ids:
        return {}
    rows = db.execute(
        select(AutomationRuleTrigger.rule_id, AutomationRuleTrigger.event_type)
        .where(AutomationRuleTrigger.rule_id.in_(list(rule_ids)))
        .order_by(AutomationRuleTrigger.event_type)
    ).all()
    result: dict[uuid.UUID, list[str]] = {rid: [] for rid in rule_ids}
    for rule_id, event_type in rows:
        result.setdefault(rule_id, []).append(event_type)
    return result


__all__ = ["active_rules_for_event", "event_types_of", "set_event_types"]
