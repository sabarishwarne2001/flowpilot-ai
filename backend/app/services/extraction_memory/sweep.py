"""ARCH41-S2:sweep — the nightly pass: merge layouts, learn and promote rules,
start and judge trials, withdraw what drifted. Run by
scripts/sweep_extraction_memory.py through deploy/bin/flowpilot-sweep."""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_secret
from app.models.extraction_memory import (
    ExtractionAnchorRule,
    ExtractionExemplar,
    ExtractionMemorySettings,
    ExtractionMemoryTrial,
    ExtractionTemplate,
)
from app.models.work_item import WorkItem
from app.services.extraction_memory import anchors, drift, gate, templates, trials
from app.services.extraction_memory import vocabulary as v

logger = logging.getLogger("app.services.extraction_memory.sweep")


def learn_rules(db: Session, template: ExtractionTemplate, *, promote: bool) -> dict[str, int]:
    rows = list(db.execute(select(ExtractionExemplar).where(
        ExtractionExemplar.template_id == template.id, ExtractionExemplar.value_form == v.FORM_RAW)).scalars())
    if not rows:
        return {"rules": 0, "promoted": 0, "retired": 0}
    items = {w.id: anchors.document_lines(w.extracted_text or "") for w in db.execute(
        select(WorkItem).where(WorkItem.id.in_({r.work_item_id for r in rows}))).scalars()}
    truth: dict[str, list[tuple[Any, str]]] = defaultdict(list)
    examples = []
    for row in rows:
        lines = items.get(row.work_item_id)
        if lines is None:
            continue
        try:
            value = decrypt_secret(row.value_ciphertext)
        except Exception:  # noqa: BLE001
            continue
        examples.append((lines, row.field_path, value))
        truth[row.field_path].append((lines, value))

    support = anchors.learn(examples)
    existing = {
        (r.field_path, r.anchor_norm, r.offset_dx, r.offset_dy): r
        for r in db.execute(select(ExtractionAnchorRule).where(ExtractionAnchorRule.template_id == template.id)).scalars()
    }
    best: dict[str, tuple[float, ExtractionAnchorRule]] = {}
    now = datetime.now(timezone.utc)
    counts = {"rules": 0, "promoted": 0, "retired": 0}
    for key, count in support.items():
        if count < v.RULE_MIN_SUPPORT:
            continue
        hits, total = anchors.replay(key, truth[key.field_path])
        lower = anchors.wilson_lower(hits, total)
        rule = existing.get((key.field_path, key.anchor_norm, key.offset_dx, key.offset_dy))
        if rule is None:
            rule = ExtractionAnchorRule(
                id=uuid.uuid4(), workspace_id=template.workspace_id, template_id=template.id,
                field_path=key.field_path, anchor_norm=key.anchor_norm, offset_dx=key.offset_dx,
                offset_dy=key.offset_dy, state=v.RULE_SHADOW, live_hits=0, live_total=0,
            )
            db.add(rule)
        if rule.state == v.RULE_ACTIVE and lower < v.RULE_PROMOTION_WILSON:
            rule.state, rule.retired_at, rule.retired_reason = v.RULE_RETIRED, now, "REPLAY_BELOW_BOUND"
            counts["retired"] += 1
        rule.value_token_count = key.value_token_count
        rule.support, rule.replay_hits, rule.replay_total = count, hits, total
        rule.wilson_lower = Decimal(str(round(lower, 4)))
        rule.updated_at = now
        counts["rules"] += 1
        if rule.state != v.RULE_RETIRED and (key.field_path not in best or lower > best[key.field_path][0]):
            best[key.field_path] = (lower, rule)
    db.flush()
    if promote:
        for lower, rule in best.values():
            if rule.state == v.RULE_SHADOW and lower >= v.RULE_PROMOTION_WILSON:
                rule.state, rule.activated_at = v.RULE_ACTIVE, now
                counts["promoted"] += 1
    db.flush()
    return counts


def _workspaces(db: Session, only: Optional[Sequence[uuid.UUID]]) -> list[tuple[uuid.UUID, uuid.UUID]]:
    pairs = set(db.execute(select(ExtractionTemplate.workspace_id, ExtractionTemplate.organization_id).distinct()).all())
    pairs |= set(db.execute(select(ExtractionMemorySettings.workspace_id, ExtractionMemorySettings.organization_id)).all())
    return sorted((w, o) for w, o in pairs if not only or w in only)


def run(db: Session, *, workspace_ids: Optional[Sequence[uuid.UUID]] = None) -> dict[str, Any]:
    report: dict[str, Any] = {"workspaces": []}
    for workspace_id, organization_id in _workspaces(db, workspace_ids):
        mode = gate.active_mode(db, organization_id=organization_id, workspace_id=workspace_id)
        entry: dict[str, Any] = {"workspace_id": str(workspace_id), "mode": mode}
        if mode == v.MODE_OFF:
            report["workspaces"].append(entry)
            continue
        entry["merged"] = templates.merge_similar(db, workspace_id=workspace_id)
        auto = mode == v.MODE_AUTO
        learned = {"rules": 0, "promoted": 0, "retired": 0}
        started = decided = 0
        for template in list(db.execute(select(ExtractionTemplate).where(
                ExtractionTemplate.workspace_id == workspace_id)).scalars()):
            for k, n in learn_rules(db, template, promote=auto).items():
                learned[k] += n
            if not auto:
                continue
            if trials.start_if_ready(db, template) is not None:
                started += 1
            running = db.execute(select(ExtractionMemoryTrial).where(
                ExtractionMemoryTrial.template_id == template.id,
                ExtractionMemoryTrial.state == v.TRIAL_RUNNING)).scalar_one_or_none()
            if running is not None and trials.evaluate(db, running).state != v.TRIAL_RUNNING:
                decided += 1
        entry.update(learned=learned, trials_started=started, trials_decided=decided,
                     rules_retired_for_drift=drift.retire_drifting_rules(db, workspace_id=workspace_id),
                     templates_demoted=drift.demote_drifting_templates(db, workspace_id=workspace_id))
        report["workspaces"].append(entry)
    return report
