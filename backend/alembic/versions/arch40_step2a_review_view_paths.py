"""ARCH-40 Step 2a — rebuild review_queue_items on the JSON paths the platform writes.

Revision ID: arch40_step2a_review_view_paths
Revises: arch40_step2_settings_backfill

WHAT WAS WRONG
==============

`arch40_step1_settings_review` created the view reading
`details ->> 'review_all_fields'` at the TOP level of
`document_verifications.details`. Nothing writes that key there.

  ARCH-35  writes details.calibration.review_all_fields   (held back)
           and    details.calibration.audit_sample        (random audit)
  ARCH-37  writes details.escalation.review_all_fields    (rule escalation)

So every calibration hold, every accuracy audit and every rule escalation fell
through to the "Extraction awaiting review / MEDIUM" arm. Nothing was lost —
the items were in the queue — but they were mislabelled, mis-ranked, and the
console's "Autonomy audits" tab had nothing to filter on.

The 28 database gates did not catch it because the probe's seed never wrote a
verification with a calibration block. `verify_arch40.py` gate B5 now seeds
all three shapes and asserts each lands in its own arm.

WHY A NEW MIGRATION AND NOT AN EDIT TO STEP 1
=============================================

Step 1 has been applied to a live database. Alembic never re-runs an applied
revision, so an edited step 1 would change nothing where it matters and would
make a fresh install differ from an upgraded one.

WHAT THE VIEW GAINS
===================

`review_reason` — why the item needs a human, as a stated value rather than a
fact a caller must reverse-engineer from severity:

  DISAGREEMENT      extraction agents disagreed                 HIGH
  ESCALATION        an automation rule sent it to review        HIGH
  CALIBRATION_HOLD  calibrated autonomy held it back            MEDIUM
  AUTONOMY_AUDIT    passed, but sampled for an accuracy audit   LOW
  PENDING_REVIEW    any other open extraction review            MEDIUM
  CLAUSE_TRIAGE     a clause assertion below its threshold      by verdict
  ANOMALY           a radar finding                             by finding

Precedence inside EXTRACTION follows that order: a disagreement outranks
everything, because a sampled document whose agents disagreed is a
disagreement first.

The column is appended LAST. `CREATE OR REPLACE VIEW` may only add columns at
the end, and although this migration drops and recreates the view anyway,
keeping the existing column order means nothing that selects by position
breaks.
"""

from __future__ import annotations

from alembic import op

revision = "arch40_step2a_review_view_paths"
down_revision = "arch40_step2_settings_backfill"
branch_labels = None
depends_on = None

#: ARCH40-S1:review-reasons. Mirrors app/services/review/vocabulary.REASONS.
REVIEW_REASONS: tuple[str, ...] = (
    "DISAGREEMENT",
    "ESCALATION",
    "CALIBRATION_HOLD",
    "AUTONOMY_AUDIT",
    "PENDING_REVIEW",
    "CLAUSE_TRIAGE",
    "ANOMALY",
)

_CAL_AUDIT = "coalesce((dv.details -> 'calibration' ->> 'audit_sample')::boolean, false)"
_CAL_HOLD = "coalesce((dv.details -> 'calibration' ->> 'review_all_fields')::boolean, false)"
_ESCALATED = "coalesce((dv.details -> 'escalation' ->> 'review_all_fields')::boolean, false)"

#: ARCH40-S1:review-view-v2. The only definition of the hub's read model.
REVIEW_QUEUE_VIEW_V2 = f"""
CREATE VIEW review_queue_items AS
    SELECT
        'EXTRACTION'::varchar(16)                       AS kind,
        dv.id                                           AS item_id,
        dv.organization_id                              AS organization_id,
        dv.workspace_id                                 AS workspace_id,
        dv.work_item_id                                 AS work_item_id,
        CASE
            WHEN dv.status = 'DISAGREED' AND NOT {_ESCALATED}
                THEN 'Extracted fields disagree'
            WHEN {_ESCALATED}
                THEN 'Escalated by an automation rule'
            WHEN {_CAL_AUDIT}
                THEN 'Accuracy audit: confirm every field'
            WHEN {_CAL_HOLD}
                THEN 'Held for review: confirm every field'
            ELSE 'Extraction awaiting review'
        END::text                                       AS headline,
        CASE
            WHEN dv.status = 'DISAGREED' OR {_ESCALATED} THEN 'HIGH'
            WHEN {_CAL_AUDIT} THEN 'LOW'
            ELSE 'MEDIUM'
        END::varchar(8)                                 AS severity,
        CASE
            WHEN dv.status = 'DISAGREED' OR {_ESCALATED} THEN 2
            WHEN {_CAL_AUDIT} THEN 4
            ELSE 3
        END::integer                                    AS severity_rank,
        dv.confidence                                   AS confidence,
        dv.created_at                                   AS created_at,
        CASE WHEN dv.status = 'REVIEWED' THEN 'RESOLVED' ELSE 'OPEN' END::varchar(8)
                                                        AS status,
        dv.reviewed_at                                  AS resolved_at,
        dv.reviewed_by_user_id                          AS resolved_by_user_id,
        CASE
            WHEN {_ESCALATED} THEN 'ESCALATION'
            WHEN dv.status = 'DISAGREED' THEN 'DISAGREEMENT'
            WHEN {_CAL_AUDIT} THEN 'AUTONOMY_AUDIT'
            WHEN {_CAL_HOLD} THEN 'CALIBRATION_HOLD'
            ELSE 'PENDING_REVIEW'
        END::varchar(24)                                AS review_reason
    FROM document_verifications dv
    WHERE dv.status IN ('PENDING', 'DISAGREED', 'REVIEWED')

    UNION ALL

    SELECT
        'ASSERTION'::varchar(16),
        ae.id,
        ae.organization_id,
        ae.workspace_id,
        ae.work_item_id,
        ad.sentence::text,
        CASE
            WHEN ae.verdict = 'FAIL' THEN 'HIGH'
            WHEN ae.calibrated_probability IS NOT NULL
                 AND ae.calibrated_probability < 0.60 THEN 'HIGH'
            ELSE 'MEDIUM'
        END::varchar(8),
        CASE
            WHEN ae.verdict = 'FAIL' THEN 2
            WHEN ae.calibrated_probability IS NOT NULL
                 AND ae.calibrated_probability < 0.60 THEN 2
            ELSE 3
        END::integer,
        ae.calibrated_probability,
        ae.created_at,
        CASE WHEN ae.reviewer_verdict IS NULL THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        ae.reviewed_at,
        NULL::uuid,
        'CLAUSE_TRIAGE'::varchar(24)
    FROM assertion_evaluations ae
    JOIN assertion_definitions ad ON ad.id = ae.definition_id
    WHERE ae.routed_to = 'TRIAGE'

    UNION ALL

    SELECT
        'ANOMALY'::varchar(16),
        af.id,
        af.organization_id,
        af.workspace_id,
        af.subject_work_item_id,
        af.headline::text,
        af.severity::varchar(8),
        CASE af.severity
            WHEN 'HIGH' THEN 2
            WHEN 'MEDIUM' THEN 3
            ELSE 4
        END::integer,
        af.score,
        af.created_at,
        CASE WHEN af.status = 'OPEN' THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        af.resolved_at,
        af.resolved_by_user_id,
        'ANOMALY'::varchar(24)
    FROM anomaly_findings af
"""


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(REVIEW_QUEUE_VIEW_V2)


def downgrade() -> None:
    # `alembic/versions` is not a package, so step 1 is loaded by path.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "arch40_step1_settings_review",
        Path(__file__).with_name("arch40_step1_settings_review.py"),
    )
    assert spec is not None and spec.loader is not None
    step1 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(step1)
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(step1.REVIEW_QUEUE_VIEW)
