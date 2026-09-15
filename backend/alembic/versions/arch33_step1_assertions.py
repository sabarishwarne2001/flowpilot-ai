"""ARCH-33 Step 1 — assertion nodes, definitions, evaluations, phrases (EXPAND)

Revision ID: arch33_step1_assertions
Revises: arch32_step1_redaction
Create Date: 2026-09-15

EXPAND-SHAPED, WITH TWO EXCEPTIONS THAT ARE STILL SAFE
======================================================

Three new tables, plus two CHECK constraints WIDENED on existing tables. A
widened CHECK accepts everything it accepted before, so every row already in
`automation_nodes` and `automation_edges` stays valid and every code path that
does not know about assertions keeps working. The migration is safe to run
before the code that reads it ships.

Reverting is the asymmetric direction, and `downgrade()` says so: narrowing
`ck_automation_nodes_type_known` back to five types fails if any assertion
node exists. That is correct. A downgrade that dropped those rows would delete
an administrator's rules to make a constraint fit.

THIRD-PARTY LICENSES INTRODUCED BY THIS PHASE
=============================================

None. Recorded explicitly, because a migration header is the one document in a
repository nobody deletes, and "did ARCH-33 add a dependency" is a question
that gets asked during diligence.

Every module in `app/services/assertions/` imports the standard library,
`app/core/normalize.py`, and its own siblings. The compiler is regular
expressions; the calibrator is pool-adjacent-violators in twelve lines of
`Decimal` arithmetic — deliberately not scikit-learn's `IsotonicRegression`,
which would add scipy and numpy build weight for an O(n) loop. The LLM family
runs on the tenant's EXISTING model route under existing spend limits, so §4.7
introduces no new cost category and no new supplier.

THE CONSTRAINT THAT CARRIES THE PHASE
=====================================

`ck_ae_routing_consistent`. A row cannot say it reached the `pass` edge unless
its verdict is PASS and a calibrated probability was written. The service
mirrors the rule in `routing.py` so the refusal arrives with an explanation,
but the database is the authority, because the service's version survives only
as long as nobody reorders the statements around it.

`verify_arch33.py --db` drives an INSERT of a PASS route with a null
`calibrated_probability` inside a rolled-back transaction and requires
Postgres to refuse it.

WHY THE VOCABULARY LISTS ARE DECLARED HERE AND ASSERTED, NOT IMPORTED
=====================================================================

The tuples below duplicate `app/services/assertions/vocabulary.py` on purpose.
A migration that imports application code is a migration that stops running
the day that import path moves, and the whole point of a migration is that it
can be replayed years later against a tree that has changed underneath it.

So they are copied, and `verify_arch33.py` asserts the copies are equal to the
originals. A ninth family added in one place and not the other fails a gate
rather than a production insert.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch33_step1_assertions"
down_revision = "arch32_step1_redaction"
branch_labels = None
depends_on = None


# --- copies of app/services/assertions/vocabulary.py, gated for equality ---

FAMILIES: tuple[str, ...] = (
    "duration_bound",
    "notice_period",
    "money_multiple_bound",
    "money_bound",
    "enumerated",
    "presence",
    "absence",
    "llm",
)

EVALUATION_MODES: tuple[str, ...] = ("DETERMINISTIC", "LLM")

VERDICTS: tuple[str, ...] = ("PASS", "FAIL", "UNDETERMINED")

ROUTES: tuple[str, ...] = ("PASS", "TRIAGE")

PHRASE_SOURCES: tuple[str, ...] = ("SEED", "REVIEWER")

#: The post-ARCH-33 node vocabulary. The first five are ARCH-13's.
NODE_TYPES: tuple[str, ...] = (
    "trigger",
    "condition",
    "action",
    "branch",
    "join",
    "assertion",
)

#: The post-ARCH-33 branch vocabulary. The first three are ARCH-13's.
BRANCH_LABELS: tuple[str, ...] = ("default", "true", "false", "pass", "triage")

#: The five ARCH-13 node types, for the downgrade.
LEGACY_NODE_TYPES: tuple[str, ...] = (
    "trigger",
    "condition",
    "action",
    "branch",
    "join",
)
LEGACY_BRANCH_LABELS: tuple[str, ...] = ("default", "true", "false")

THRESHOLD_MIN_EXCLUSIVE = "0.5"
THRESHOLD_MAX_EXCLUSIVE = "1"

MAX_PHRASE_LENGTH = 200


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # -----------------------------------------------------------------
    # 1. Widen the ARCH-13 graph vocabulary
    # -----------------------------------------------------------------
    #
    # DROP then ADD rather than ALTER: Postgres has no "modify a CHECK in
    # place", and the pair inside one transaction is atomic. There is no
    # window in which the table is unconstrained.
    op.drop_constraint(
        "ck_automation_nodes_type_known", "automation_nodes", type_="check"
    )
    op.create_check_constraint(
        "ck_automation_nodes_type_known",
        "automation_nodes",
        f"node_type IN ({_quoted(NODE_TYPES)})",
    )

    op.drop_constraint(
        "ck_automation_edges_branch_known", "automation_edges", type_="check"
    )
    op.create_check_constraint(
        "ck_automation_edges_branch_known",
        "automation_edges",
        f"branch IN ({_quoted(BRANCH_LABELS)})",
    )

    # -----------------------------------------------------------------
    # 2. assertion_definitions
    # -----------------------------------------------------------------
    op.create_table(
        "assertion_definitions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "node_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_nodes.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("sentence", sa.Text(), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("plan", postgresql.JSONB(), nullable=False),
        sa.Column("threshold", sa.Numeric(5, 4), nullable=False),
        sa.Column("evaluation_mode", sa.String(length=16), nullable=False),
        sa.Column(
            "llm_acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "version", sa.Integer(), nullable=False, server_default=sa.text("1")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"family IN ({_quoted(FAMILIES)})", name="ck_ad_family_known"
        ),
        sa.CheckConstraint(
            f"evaluation_mode IN ({_quoted(EVALUATION_MODES)})",
            name="ck_ad_mode_known",
        ),
        sa.CheckConstraint(
            "(family = 'llm') = (evaluation_mode = 'LLM')",
            name="ck_ad_mode_matches_family",
        ),
        sa.CheckConstraint(
            "evaluation_mode <> 'LLM' OR llm_acknowledged_by IS NOT NULL",
            name="ck_ad_llm_acknowledged",
        ),
        sa.CheckConstraint(
            f"threshold > {THRESHOLD_MIN_EXCLUSIVE} "
            f"AND threshold < {THRESHOLD_MAX_EXCLUSIVE}",
            name="ck_ad_threshold_bounded",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(plan) = 'object'", name="ck_ad_plan_object"
        ),
        sa.CheckConstraint("version >= 1", name="ck_ad_version_positive"),
        sa.CheckConstraint("btrim(sentence) <> ''", name="ck_ad_sentence_present"),
    )
    op.create_index(
        "uq_ad_node_version",
        "assertion_definitions",
        ["node_id", "version"],
        unique=True,
    )
    op.create_index(
        "ix_ad_workspace_node", "assertion_definitions", ["workspace_id", "node_id"]
    )
    op.create_index(
        "ix_ad_organization_family",
        "assertion_definitions",
        ["organization_id", "family"],
    )

    # -----------------------------------------------------------------
    # 3. assertion_evaluations
    # -----------------------------------------------------------------
    op.create_table(
        "assertion_evaluations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "definition_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("assertion_definitions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "node_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_node_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("verdict", sa.String(length=12), nullable=False),
        sa.Column("extracted_value", postgresql.JSONB(), nullable=True),
        sa.Column("raw_score", sa.Numeric(6, 5), nullable=False),
        sa.Column("calibrated_probability", sa.Numeric(6, 5), nullable=True),
        # ARCH-35's model version. No FK: the table does not exist yet, and
        # ARCH-35 adds the constraint in its own migration.
        sa.Column("calibration_model_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("routed_to", sa.String(length=8), nullable=False),
        sa.Column(
            "verification_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("document_verifications.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("usage_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("reviewer_verdict", sa.String(length=12), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"verdict IN ({_quoted(VERDICTS)})", name="ck_ae_verdict_known"
        ),
        sa.CheckConstraint(
            f"routed_to IN ({_quoted(ROUTES)})", name="ck_ae_route_known"
        ),
        # THE invariant. Nothing reaches the `pass` edge without a PASS
        # verdict and a calibrated probability.
        sa.CheckConstraint(
            "routed_to = 'TRIAGE' OR "
            "(verdict = 'PASS' AND calibrated_probability IS NOT NULL)",
            name="ck_ae_routing_consistent",
        ),
        sa.CheckConstraint(
            "routed_to = 'PASS' OR verification_id IS NOT NULL",
            name="ck_ae_triage_has_review",
        ),
        sa.CheckConstraint(
            "raw_score >= 0 AND raw_score <= 1",
            name="ck_ae_raw_score_unit_interval",
        ),
        sa.CheckConstraint(
            "calibrated_probability IS NULL OR "
            "(calibrated_probability >= 0 AND calibrated_probability <= 1)",
            name="ck_ae_probability_unit_interval",
        ),
        sa.CheckConstraint(
            "(reviewer_verdict IS NULL) = (reviewed_at IS NULL)",
            name="ck_ae_review_fields_together",
        ),
        sa.CheckConstraint(
            f"reviewer_verdict IS NULL OR reviewer_verdict IN ({_quoted(VERDICTS)})",
            name="ck_ae_reviewer_verdict_known",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence) = 'array'", name="ck_ae_evidence_is_array"
        ),
    )
    op.create_index(
        "ix_ae_definition_created",
        "assertion_evaluations",
        ["definition_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_ae_workspace_routed",
        "assertion_evaluations",
        ["workspace_id", "routed_to"],
    )
    op.create_index(
        "ix_ae_pending_review",
        "assertion_evaluations",
        ["verification_id"],
        postgresql_where=sa.text("reviewer_verdict IS NULL"),
    )

    # -----------------------------------------------------------------
    # 4. assertion_retrieval_phrases
    # -----------------------------------------------------------------
    #
    # Organization-scoped, not workspace-scoped, per §4.3: the synonym table
    # a reviewer teaches is "scoped to the tenant". A phrase learned reading
    # one supplier's paper in procurement helps legal read the same paper.
    op.create_table(
        "assertion_retrieval_phrases",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("phrase", sa.String(length=MAX_PHRASE_LENGTH), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "hits", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            f"family IN ({_quoted(FAMILIES)})", name="ck_arp_family_known"
        ),
        sa.CheckConstraint(
            f"source IN ({_quoted(PHRASE_SOURCES)})", name="ck_arp_source_known"
        ),
        sa.CheckConstraint("hits >= 0", name="ck_arp_hits_non_negative"),
        sa.CheckConstraint("btrim(phrase) <> ''", name="ck_arp_phrase_present"),
    )
    op.create_index(
        "uq_arp_org_family_phrase",
        "assertion_retrieval_phrases",
        ["organization_id", "family", sa.text("lower(phrase)")],
        unique=True,
    )
    op.create_index(
        "ix_arp_org_family_hits",
        "assertion_retrieval_phrases",
        ["organization_id", "family", sa.text("hits DESC")],
    )


def downgrade() -> None:
    """Reverse, and REFUSE rather than delete an administrator's rules.

    Narrowing the node and branch vocabularies is the asymmetric half of this
    migration: the widened CHECKs accepted rows the narrow ones do not.
    Postgres validates a new CHECK against existing rows, so if any assertion
    node or `pass`/`triage` edge exists, `create_check_constraint` below
    raises and the downgrade stops.

    That is deliberate. The alternative — deleting the offending rows to make
    the constraint fit — would silently destroy workflow steps a customer
    built, in a migration whose name says "downgrade" and whose output would
    say "OK".
    """
    op.drop_index("ix_arp_org_family_hits", table_name="assertion_retrieval_phrases")
    op.drop_index("uq_arp_org_family_phrase", table_name="assertion_retrieval_phrases")
    op.drop_table("assertion_retrieval_phrases")

    op.drop_index("ix_ae_pending_review", table_name="assertion_evaluations")
    op.drop_index("ix_ae_workspace_routed", table_name="assertion_evaluations")
    op.drop_index("ix_ae_definition_created", table_name="assertion_evaluations")
    op.drop_table("assertion_evaluations")

    op.drop_index("ix_ad_organization_family", table_name="assertion_definitions")
    op.drop_index("ix_ad_workspace_node", table_name="assertion_definitions")
    op.drop_index("uq_ad_node_version", table_name="assertion_definitions")
    op.drop_table("assertion_definitions")

    op.drop_constraint(
        "ck_automation_edges_branch_known", "automation_edges", type_="check"
    )
    op.create_check_constraint(
        "ck_automation_edges_branch_known",
        "automation_edges",
        f"branch IN ({_quoted(LEGACY_BRANCH_LABELS)})",
    )

    op.drop_constraint(
        "ck_automation_nodes_type_known", "automation_nodes", type_="check"
    )
    op.create_check_constraint(
        "ck_automation_nodes_type_known",
        "automation_nodes",
        f"node_type IN ({_quoted(LEGACY_NODE_TYPES)})",
    )