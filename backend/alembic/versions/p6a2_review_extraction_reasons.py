"""FINAL RELEASE (F-111) — say WHY an extraction is in the review queue.

Revision ID: p6a2_review_extraction_reasons
Revises: p6a1_work_item_field_corrections
Create Date: 2026-10-06

THE DEFECT
==========

`triage` (document_verification_service) parks a verification as DISAGREED
for four different reasons: the agents disagreed on a field, a calibrated-
autonomy hold (the tenant's model is not confident enough yet), an accuracy-
audit sample, or an extraction-memory trial. The hub's read model,
`review_queue_items`, tested `status = 'DISAGREED'` FIRST, so every hold and
every audit sample was shown as "Extracted fields disagree", reason
DISAGREEMENT, severity HIGH - even when every agent agreed on every field.
The "Autonomy audits" tab (reasons CALIBRATION_HOLD / AUTONOMY_AUDIT) was
therefore always empty, and a reviewer could not tell a real disagreement
from a routine audit. Found by the browser suite with a model attached: all
14 extractions of an Enterprise tenant (calibrated autonomy, no model fitted
yet) were listed as disagreements.

THE FIX
=======

Only the EXTRACTION arm changes; it now decides in this order:

  escalated by a rule        -> ESCALATION         HIGH
  a field really disagrees   -> DISAGREEMENT       HIGH   (details.disagreed_fields
                                                           non-empty, or unresolved_conflicts)
  calibration audit sample   -> AUTONOMY_AUDIT     LOW
  calibration hold           -> CALIBRATION_HOLD   MEDIUM
  extraction-memory trial    -> PENDING_REVIEW     MEDIUM ("Extraction memory trial")
  any other DISAGREED        -> DISAGREEMENT       HIGH   (as before)
  otherwise                  -> PENDING_REVIEW     MEDIUM

The view text is the ARCH-47 definition (v8) with exactly these three CASE
expressions replaced; the replacement refuses to run if v8 does not contain
them, so a drifted chain fails loudly instead of building a half-edited view.
Downgrade rebuilds v8.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "p6a2_review_extraction_reasons"
down_revision = "p6a1_work_item_field_corrections"
branch_labels = None
depends_on = None

_ESC = "coalesce((dv.details -> 'escalation' ->> 'review_all_fields')::boolean, false)"
_AUD = "coalesce((dv.details -> 'calibration' ->> 'audit_sample')::boolean, false)"
_HOLD = "coalesce((dv.details -> 'calibration' ->> 'review_all_fields')::boolean, false)"
_MEM = "coalesce((dv.details -> 'extraction_memory' ->> 'review_all_fields')::boolean, false)"
_DIS = (
    "(coalesce(jsonb_array_length(CASE WHEN jsonb_typeof(dv.details -> 'disagreed_fields') = 'array' "
    "THEN dv.details -> 'disagreed_fields' END), 0) > 0 OR dv.details ? 'unresolved_conflicts')"
)

_OLD_HEADLINE = f"""        CASE
            WHEN dv.status = 'DISAGREED' AND NOT {_ESC}
                THEN 'Extracted fields disagree'
            WHEN {_ESC}
                THEN 'Escalated by an automation rule'
            WHEN {_AUD}
                THEN 'Accuracy audit: confirm every field'
            WHEN {_HOLD}
                THEN 'Held for review: confirm every field'
            ELSE 'Extraction awaiting review'
        END::text                                       AS headline,"""

_NEW_HEADLINE = f"""        CASE
            WHEN {_ESC}
                THEN 'Escalated by an automation rule'
            WHEN {_DIS}
                THEN 'Extracted fields disagree'
            WHEN {_AUD}
                THEN 'Accuracy audit: confirm every field'
            WHEN {_HOLD}
                THEN 'Held for review: confirm every field'
            WHEN {_MEM}
                THEN 'Extraction memory trial: confirm every field'
            WHEN dv.status = 'DISAGREED'
                THEN 'Extracted fields disagree'
            ELSE 'Extraction awaiting review'
        END::text                                       AS headline,"""

_OLD_SEVERITY = f"""        CASE
            WHEN dv.status = 'DISAGREED' OR {_ESC} THEN 'HIGH'
            WHEN {_AUD} THEN 'LOW'
            ELSE 'MEDIUM'
        END::varchar(8)                                 AS severity,
        CASE
            WHEN dv.status = 'DISAGREED' OR {_ESC} THEN 2
            WHEN {_AUD} THEN 4
            ELSE 3
        END::integer                                    AS severity_rank,"""

_NEW_SEVERITY = f"""        CASE
            WHEN {_ESC} OR {_DIS} THEN 'HIGH'
            WHEN {_AUD} THEN 'LOW'
            WHEN {_HOLD} OR {_MEM} THEN 'MEDIUM'
            WHEN dv.status = 'DISAGREED' THEN 'HIGH'
            ELSE 'MEDIUM'
        END::varchar(8)                                 AS severity,
        CASE
            WHEN {_ESC} OR {_DIS} THEN 2
            WHEN {_AUD} THEN 4
            WHEN {_HOLD} OR {_MEM} THEN 3
            WHEN dv.status = 'DISAGREED' THEN 2
            ELSE 3
        END::integer                                    AS severity_rank,"""

_OLD_REASON = f"""        CASE
            WHEN {_ESC} THEN 'ESCALATION'
            WHEN dv.status = 'DISAGREED' THEN 'DISAGREEMENT'
            WHEN {_AUD} THEN 'AUTONOMY_AUDIT'
            WHEN {_HOLD} THEN 'CALIBRATION_HOLD'
            ELSE 'PENDING_REVIEW'
        END::varchar(24)                                AS review_reason"""

_NEW_REASON = f"""        CASE
            WHEN {_ESC} THEN 'ESCALATION'
            WHEN {_DIS} THEN 'DISAGREEMENT'
            WHEN {_AUD} THEN 'AUTONOMY_AUDIT'
            WHEN {_HOLD} THEN 'CALIBRATION_HOLD'
            WHEN {_MEM} THEN 'PENDING_REVIEW'
            WHEN dv.status = 'DISAGREED' THEN 'DISAGREEMENT'
            ELSE 'PENDING_REVIEW'
        END::varchar(24)                                AS review_reason"""


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _v8() -> str:
    return _load("arch47_step1_erp_posting").review_queue_view_v8()


def review_queue_view_v9() -> str:
    view = _v8()
    for old, new in ((_OLD_HEADLINE, _NEW_HEADLINE), (_OLD_SEVERITY, _NEW_SEVERITY), (_OLD_REASON, _NEW_REASON)):
        if view.count(old) != 1:
            raise RuntimeError("review_queue_items v8 is not the definition this migration edits")
        view = view.replace(old, new)
    return view


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v9())


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_v8())
