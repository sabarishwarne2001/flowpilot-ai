"""ARCH-37 Step 1 — flow builder: rule triggers, node-run facts, vocabulary (EXPAND)

Revision ID: arch37_step1_flow_builder
Revises: arch37_step0_flow_vocabulary
Create Date: 2026-09-17

WHAT THIS CHANGES
=================

automation_rule_triggers (new)
    One row per (rule, internal event). Replaces the three-entry
    EVENT_TO_RULE_TRIGGER dictionary, so a rule can listen to several events
    and every event a rule can name is one the engine can actually receive.
    trg_automation_rule_triggers_workspace refuses a row whose workspace is
    not the rule's workspace.

automation_rules.flow_spec (new, nullable)
    The step builder's own document: condition groups, the groups operator and
    the "otherwise" actions. NULL for every rule written before ARCH-37, which
    keeps running exactly as it did.

automation_node_runs.action_type / external_ref / duration_ms (new, nullable)
    What an action node did, the id of what it created (delivery, job,
    verification) and how long it took.

automation_nodes: ck_automation_nodes_action_has_type
    An action node must name its action. Added NOT VALID and validated only
    when no existing row violates it, so a legacy row cannot fail the deploy;
    new rows are checked either way.

ck_outbox_events_visibility_vocabulary (rebuilt)
    Pinned the sixteen internal names. The `trigger.` twins and native trigger
    events are added, or the first twin insert fails.

ck_webhook_deliveries_event_type_vocabulary and
ck_webhook_endpoints_event_types_vocabulary (rebuilt)
    Both were last written by ARCH-10. ARCH-31 and ARCH-34 added
    procurement.* and anomaly.detected to the Python vocabulary without
    widening either, so every delivery of those events was refused by the
    database. Rebuilt from the full public vocabulary, plus workflow.triggered
    for the `webhook.send` action.

BACKFILL
========

    WORK_ITEM_COMPLETED  -> work_item.enriched, work_item.verification_completed
    WORK_ITEM_UPDATED    -> work_item.field_changed
    WORK_ITEM_CREATED    -> trigger.work_item.created      rule PAUSED
    WORK_ITEM_FAILED     -> trigger.document.failed        rule PAUSED
    WORK_ITEM_REPROCESSED-> trigger.work_item.reprocessed  rule PAUSED
    anything else        -> no trigger rows                rule PAUSED

Before ARCH-37 the last four could never run. A rule that never ran must not
start sending email on deploy day, so each active one is paused and an
AUTOMATION_RULE / DISABLED audit row says why. Rules that were already
inactive are left inactive and not audited.

CONTRACT (a later release)
==========================

automation_rules.event becomes nullable and unused, then is dropped.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch37_step1_flow_builder"
down_revision = "arch37_step0_flow_vocabulary"
branch_labels = None
depends_on = None

#: The internal vocabulary before ARCH-37 (arch16_step8).
INTERNAL_BEFORE: tuple[str, ...] = (
    "work_item.enriched",
    "work_item.field_changed",
    "work_item.verification_completed",
    "work_item.verification_disagreed",
    "automation.execution_completed",
    "automation.budget_exhausted",
    "billing.seat_added",
    "billing.seat_removed",
    "billing.seat_sync_needed",
    "identity.user_provisioned",
    "identity.user_deprovisioned",
    "identity.user_reactivated",
    "identity.domain_verified",
    "identity.domain_lapsed",
    "identity.jit_cap_reached",
    "identity.idp_config_changed",
)

TRIGGER_EVENTS: tuple[str, ...] = (
    "trigger.document.completed",
    "trigger.document.failed",
    "trigger.procurement.completed",
    "trigger.procurement.approved",
    "trigger.procurement.disputed",
    "trigger.anomaly.detected",
    "trigger.work_item.created",
    "trigger.work_item.reprocessed",
    "trigger.assertion.held",
    "trigger.redaction.completed",
    "trigger.batch.completed",
)

INTERNAL_AFTER: tuple[str, ...] = INTERNAL_BEFORE + TRIGGER_EVENTS

LEGACY_RULE_EVENTS: tuple[str, ...] = (
    "work_item.enriched",
    "work_item.verification_completed",
    "work_item.field_changed",
)

#: The public vocabulary as of ARCH-39, pinned. A migration must not import
#: the live Python set: a later phase would silently change what this one did.
PUBLIC_BEFORE: tuple[str, ...] = (
    "organization.updated",
    "member.invited",
    "member.joined",
    "member.role_changed",
    "member.deactivated",
    "member.reactivated",
    "invitation.created",
    "invitation.accepted",
    "invitation.rejected",
    "invitation.revoked",
    "invitation.expired",
    "workspace.created",
    "workspace.updated",
    "workspace.archived",
    "workspace.restored",
    "work_item.created",
    "work_item.updated",
    "work_item.deleted",
    "document.queued",
    "document.processing",
    "document.completed",
    "document.failed",
    "procurement.completed",
    "procurement.approved",
    "procurement.disputed",
    "anomaly.detected",
)
PUBLIC_AFTER: tuple[str, ...] = PUBLIC_BEFORE + ("workflow.triggered",)

BACKFILL: dict[str, tuple[str, ...]] = {
    "WORK_ITEM_COMPLETED": ("work_item.enriched", "work_item.verification_completed"),
    "WORK_ITEM_UPDATED": ("work_item.field_changed",),
    "WORK_ITEM_CREATED": ("trigger.work_item.created",),
    "WORK_ITEM_FAILED": ("trigger.document.failed",),
    "WORK_ITEM_REPROCESSED": ("trigger.work_item.reprocessed",),
}
#: Events whose rules actually ran before ARCH-37 and therefore keep running.
LIVE_LEGACY_EVENTS: tuple[str, ...] = ("WORK_ITEM_COMPLETED", "WORK_ITEM_UPDATED")


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


#: The pause-and-audit statement, a module constant so `verify_arch37.py --db`
#: runs exactly this text against fixtures.
PAUSE_SQL: str = f"""
        WITH paused AS (
            UPDATE automation_rules AS r
               SET is_active = false,
                   updated_at = now()
             WHERE r.is_active
               AND r.event NOT IN ({_sql_list(LIVE_LEGACY_EVENTS)})
            RETURNING r.id, r.workspace_id, r.event, r.name
        )
        INSERT INTO audit_logs (
            id, organization_id, workspace_id, resource_type, resource_id,
            action, outcome, details, created_at, updated_at
        )
        SELECT gen_random_uuid(), w.organization_id, p.workspace_id,
               'AUTOMATION_RULE'::audit_resource_type, p.id,
               'DISABLED'::audit_action, 'ALLOWED'::audit_outcome,
               jsonb_build_object(
                   'reason', 'arch37_dead_trigger_paused',
                   'legacy_event', p.event,
                   'rule_name', p.name,
                   'explanation',
                   'Before ARCH-37 this trigger never fired, so the rule never ran. '
                   'It was paused rather than activated on deploy; review it in the '
                   'flow builder and re-enable it deliberately.'
               ),
               now(), now()
          FROM paused AS p
          JOIN workspaces AS w ON w.id = p.workspace_id
        """

#: The backfill statement, one execution per (legacy event, internal event).
BACKFILL_SQL: str = """
    INSERT INTO automation_rule_triggers (rule_id, event_type, workspace_id)
    SELECT r.id, :event_type, r.workspace_id
      FROM automation_rules AS r
     WHERE r.event = :legacy_event
    ON CONFLICT DO NOTHING
"""


def _visibility_check(values: tuple[str, ...]) -> None:
    op.execute(
        "ALTER TABLE outbox_events DROP CONSTRAINT IF EXISTS "
        "ck_outbox_events_visibility_vocabulary"
    )
    array = ", ".join(f"'{value}'::character varying" for value in values)
    op.execute(
        "ALTER TABLE outbox_events ADD CONSTRAINT "
        "ck_outbox_events_visibility_vocabulary CHECK ("
        f"((visibility::text = 'INTERNAL'::text AND event_type::text = ANY (ARRAY[{array}]::text[])) OR "
        f"(visibility::text = 'PUBLIC'::text AND event_type::text <> ALL (ARRAY[{array}]::text[]))))"
    )


def _webhook_checks(values: tuple[str, ...]) -> None:
    op.execute(
        "ALTER TABLE webhook_deliveries DROP CONSTRAINT IF EXISTS "
        "ck_webhook_deliveries_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
        "ck_webhook_deliveries_event_type_vocabulary "
        f"CHECK (event_type IN ({_sql_list(values)}))"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints DROP CONSTRAINT IF EXISTS "
        "ck_webhook_endpoints_event_types_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_endpoints ADD CONSTRAINT "
        "ck_webhook_endpoints_event_types_vocabulary "
        f"CHECK (event_types <@ ARRAY[{_sql_list(values)}]::varchar[])"
    )


def upgrade() -> None:
    # ---- vocabularies ----------------------------------------------------
    _visibility_check(INTERNAL_AFTER)
    _webhook_checks(PUBLIC_AFTER)

    # ---- automation_rules.flow_spec -------------------------------------
    op.add_column(
        "automation_rules",
        sa.Column("flow_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    # Raw SQL for every CHECK in this migration: op.create_check_constraint
    # runs the name through the metadata naming convention and doubles the
    # prefix (see arch16_step8). The gates look these up by exact name.
    op.execute(
        "ALTER TABLE automation_rules ADD CONSTRAINT ck_automation_rules_flow_spec_is_object "
        "CHECK (flow_spec IS NULL OR jsonb_typeof(flow_spec) = 'object')"
    )

    # ---- automation_rule_triggers ----------------------------------------
    op.create_table(
        "automation_rule_triggers",
        sa.Column(
            "rule_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_rules.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("event_type", sa.String(64), primary_key=True),
        sa.Column(
            "workspace_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )
    op.execute(
        "ALTER TABLE automation_rule_triggers ADD CONSTRAINT ck_automation_rule_triggers_event_known "
        f"CHECK (event_type LIKE 'trigger.%' OR event_type IN ({_sql_list(LEGACY_RULE_EVENTS)}))"
    )
    op.create_index(
        "ix_automation_rule_triggers_workspace_event",
        "automation_rule_triggers",
        ["workspace_id", "event_type"],
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION fp_automation_rule_triggers_workspace()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            rule_workspace uuid;
        BEGIN
            SELECT workspace_id INTO rule_workspace
              FROM automation_rules
             WHERE id = NEW.rule_id;
            IF rule_workspace IS DISTINCT FROM NEW.workspace_id THEN
                RAISE EXCEPTION
                    'automation_rule_triggers_cross_workspace: rule % is in workspace %, row says %',
                    NEW.rule_id, rule_workspace, NEW.workspace_id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_automation_rule_triggers_workspace
        BEFORE INSERT OR UPDATE ON automation_rule_triggers
        FOR EACH ROW EXECUTE FUNCTION fp_automation_rule_triggers_workspace();
        """
    )

    # ---- backfill --------------------------------------------------------
    for legacy_event, event_types in BACKFILL.items():
        for event_type in event_types:
            op.execute(
                sa.text(BACKFILL_SQL).bindparams(
                    event_type=event_type, legacy_event=legacy_event
                )
            )

    # Pause every ACTIVE rule whose legacy trigger never fired, and say why.
    op.execute(PAUSE_SQL)

    # ---- automation_node_runs --------------------------------------------
    op.add_column("automation_node_runs", sa.Column("action_type", sa.String(48), nullable=True))
    op.add_column("automation_node_runs", sa.Column("external_ref", sa.String(128), nullable=True))
    op.add_column("automation_node_runs", sa.Column("duration_ms", sa.Integer(), nullable=True))
    op.execute(
        "ALTER TABLE automation_node_runs ADD CONSTRAINT ck_automation_node_runs_duration_non_negative "
        "CHECK (duration_ms IS NULL OR duration_ms >= 0)"
    )

    # ---- automation_nodes ------------------------------------------------
    op.execute(
        "ALTER TABLE automation_nodes ADD CONSTRAINT ck_automation_nodes_action_has_type "
        "CHECK (node_type <> 'action' OR config ? 'action_type') NOT VALID"
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM automation_nodes
                 WHERE node_type = 'action' AND NOT (config ? 'action_type')
            ) THEN
                ALTER TABLE automation_nodes
                    VALIDATE CONSTRAINT ck_automation_nodes_action_has_type;
            ELSE
                RAISE NOTICE 'ck_automation_nodes_action_has_type left NOT VALID: '
                             'legacy action nodes without action_type exist';
            END IF;
        END;
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE automation_nodes DROP CONSTRAINT IF EXISTS ck_automation_nodes_action_has_type"
    )
    op.execute(
        "ALTER TABLE automation_node_runs DROP CONSTRAINT IF EXISTS "
        "ck_automation_node_runs_duration_non_negative"
    )
    op.drop_column("automation_node_runs", "duration_ms")
    op.drop_column("automation_node_runs", "external_ref")
    op.drop_column("automation_node_runs", "action_type")

    # Paused rules stay paused: re-enabling a rule that never ran is exactly
    # what the upgrade refused to do.
    op.execute("DROP TRIGGER IF EXISTS trg_automation_rule_triggers_workspace ON automation_rule_triggers")
    op.execute("DROP FUNCTION IF EXISTS fp_automation_rule_triggers_workspace()")
    op.drop_index("ix_automation_rule_triggers_workspace_event", table_name="automation_rule_triggers")
    op.drop_table("automation_rule_triggers")

    op.execute(
        "ALTER TABLE automation_rules DROP CONSTRAINT IF EXISTS ck_automation_rules_flow_spec_is_object"
    )
    op.drop_column("automation_rules", "flow_spec")

    # Trigger rows were never delivered anywhere; they cannot survive the
    # narrower CHECK.
    op.execute("DELETE FROM outbox_events WHERE event_type LIKE 'trigger.%'")
    _visibility_check(INTERNAL_BEFORE)

    op.execute("DELETE FROM webhook_deliveries WHERE event_type = 'workflow.triggered'")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM webhook_endpoints
                 WHERE event_types = ARRAY['workflow.triggered']::varchar[]
            ) THEN
                RAISE EXCEPTION 'A webhook endpoint subscribes only to workflow.triggered; '
                                'change its subscription before downgrading.';
            END IF;
        END;
        $$;
        """
    )
    op.execute(
        "UPDATE webhook_endpoints SET event_types = array_remove(event_types, 'workflow.triggered') "
        "WHERE 'workflow.triggered' = ANY(event_types)"
    )
    # The procurement / anomaly widening is kept on downgrade: restoring the
    # ARCH-10 list would reintroduce the refusal this migration fixed.
    _webhook_checks(PUBLIC_BEFORE)
