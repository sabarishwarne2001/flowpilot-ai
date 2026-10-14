"""Campaign session 4 (F-335) — a deleted clause check's reviews leave the review hub.

Revision ID: s4a3_review_view_drops_deleted_checks
Revises: s4a2_operator_alerts
Create Date: 2026-10-13

`review_queue_items` (v10) is v9 (`p6a2_review_extraction_reasons`) with the clause-assertion branch
joined to its rule: an evaluation held for a person by a check (or a workflow step) that has since
been deleted is no longer listed. Deleting a check is a soft delete (F-150's rule, applied to clause
checks by F-335), so the evaluations and their history stay; deciding one would only have resumed a
workflow that no longer exists.

Downgrade restores v9.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "s4a3_review_view_drops_deleted_checks"
down_revision = "s4a2_operator_alerts"
branch_labels = None
depends_on = None

_OLD = """    FROM assertion_evaluations ae
    JOIN assertion_definitions ad ON ad.id = ae.definition_id
    WHERE ae.routed_to = 'TRIAGE'"""
_NEW = """    FROM assertion_evaluations ae
    JOIN assertion_definitions ad ON ad.id = ae.definition_id
    JOIN automation_nodes an ON an.id = ad.node_id
    JOIN automation_rules ar ON ar.id = an.rule_id
    WHERE ae.routed_to = 'TRIAGE'
      AND ar.deleted_at IS NULL"""


def _v9() -> str:
    path = Path(__file__).with_name("p6a2_review_extraction_reasons.py")
    spec = importlib.util.spec_from_file_location("p6a2_review_extraction_reasons", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.review_queue_view_v9()


def review_queue_view_v10() -> str:
    view = _v9()
    if view.count(_OLD) != 1:
        raise RuntimeError("review_queue_items v9 is not the definition this migration edits")
    return view.replace(_OLD, _NEW)


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v10())


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_v9())
