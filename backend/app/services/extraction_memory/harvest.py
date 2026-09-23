"""ARCH41-S2:harvest — a resolved extraction review becomes memory.

Called from `document_verification_service.resolve` after the reviewer's values
are written. Only HUMAN-reviewed values are ever harvested: an auto-approved
document never passes through here, so memory can never learn from, and then
reinforce, the model's own unreviewed output.

CORRECTED means the reviewer chose a value different from the consensus the
agents produced; CONFIRMED means the consensus stood. The document's outcome
(fields reviewed, fields corrected) is written to its application row — that
is the evidence trials and drift are judged on.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.encryption import encrypt_secret, head_key_fingerprint
from app.models.extraction_memory import (
    ExtractionAnchorRule,
    ExtractionExemplar,
    ExtractionMemoryApplication,
)
from app.services.extraction_memory import anchors, gate, templates
from app.services.extraction_memory import vocabulary as v
from app.services.extraction_memory.fingerprint import fingerprint

logger = logging.getLogger("app.services.extraction_memory.harvest")


def _as_text(value: Any) -> str:
    return json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value)


def _raw_lines(text: str) -> list[str]:
    return [line.strip() for line in (text or "").splitlines() if line.split()]


def label_for(consensus: Any, resolved: Any) -> str:
    from app.services.document_verification_service import loose_equal

    if consensus is None:
        return v.LABEL_CORRECTED
    return v.LABEL_CONFIRMED if loose_equal(consensus, resolved) else v.LABEL_CORRECTED


def on_review_resolved(db: Session, *, verification: Any, work_item: Any) -> Optional[int]:
    try:
        with db.begin_nested():
            return _harvest(db, verification=verification, work_item=work_item)
    except Exception:  # noqa: BLE001 - a memory fault must never fail a human's review
        logger.exception("extraction_memory.harvest_failed", extra={"verification_id": str(verification.id)})
        return None


def _harvest(db: Session, *, verification: Any, work_item: Any) -> Optional[int]:
    mode = gate.active_mode(db, organization_id=verification.organization_id, workspace_id=verification.workspace_id)
    if mode == v.MODE_OFF:
        return None
    text = work_item.extracted_text or ""
    fp = fingerprint(text)
    if fp is None:
        return None

    application = db.get(ExtractionMemoryApplication, work_item.id)
    template = templates.get(db, application.template_id) if application is not None else None
    if template is None:
        template, _ = templates.assign(
            db, organization_id=verification.organization_id, workspace_id=verification.workspace_id,
            work_item_id=work_item.id, document_type=templates.document_type_of(work_item), fp=fp,
        )
    if application is None:
        application = ExtractionMemoryApplication(
            work_item_id=work_item.id, workspace_id=work_item.workspace_id,
            template_id=template.id, arm=v.ARM_NONE,
        )
        db.add(application)

    lines = anchors.document_lines(text)
    raw = _raw_lines(text)
    key_fp = head_key_fingerprint()
    candidates = application.anchor_candidates or {}
    live_rules = {
        r.id: r for r in db.execute(
            select(ExtractionAnchorRule).where(
                ExtractionAnchorRule.template_id == template.id,
                ExtractionAnchorRule.state == v.RULE_ACTIVE,
            )
        ).scalars()
    }

    total = corrected = 0
    corrected_fields: list[str] = []
    for row in verification.fields:
        path = row.field_path
        if path.startswith(v.ASSERTION_FIELD_PREFIX):
            continue
        if row.resolved_value is not None:
            value, label = row.resolved_value, label_for(row.consensus_value, row.resolved_value)
        elif row.agreed and row.consensus_value is not None:
            value, label = row.consensus_value, v.LABEL_CONFIRMED
        else:
            continue
        value_text = _as_text(value).strip()
        if not value_text:
            continue
        total += 1
        if label == v.LABEL_CORRECTED:
            corrected += 1
            corrected_fields.append(path)

        sensitive = bool(v.SENSITIVE_FIELD_PATTERN.search(path))
        stored = anchors.value_shape(value_text) if sensitive else value_text
        tokens = anchors.value_tokens(value_text)
        hits = anchors.find_value(lines, tokens)
        line_index, token_index = hits[0] if hits else (None, None)
        evidence = None
        if line_index is not None and line_index < len(raw):
            evidence = raw[line_index][: v.EVIDENCE_MAX_CHARS]
            if sensitive:
                evidence = evidence.replace(value_text, stored) if value_text in evidence else None

        db.execute(delete(ExtractionExemplar).where(
            ExtractionExemplar.work_item_id == work_item.id, ExtractionExemplar.field_path == path))
        db.execute(delete(ExtractionExemplar).where(ExtractionExemplar.verification_field_id == row.id))
        db.add(ExtractionExemplar(
            id=uuid.uuid4(),
            workspace_id=work_item.workspace_id,
            template_id=template.id,
            work_item_id=work_item.id,
            verification_field_id=row.id,
            field_path=path[:200],
            label_source=label,
            value_form=v.FORM_SHAPE if sensitive else v.FORM_RAW,
            value_ciphertext=encrypt_secret(stored),
            evidence_ciphertext=encrypt_secret(evidence) if evidence else None,
            key_fingerprint=key_fp,
            value_token_count=max(1, len(tokens)),
            line_index=line_index,
            token_index=token_index,
        ))

        candidate = candidates.get(path)
        if candidate:
            try:
                rule = live_rules.get(uuid.UUID(str(candidate.get("rule_id"))))
            except ValueError:
                rule = None
            if rule is not None:
                rule.live_total += 1
                if anchors.values_equal(candidate.get("value"), value_text):
                    rule.live_hits += 1

    application.fields_total = total
    application.fields_corrected = corrected
    application.corrected_fields = corrected_fields
    application.outcome_recorded_at = datetime.now(timezone.utc)
    db.flush()
    return total
