"""ARCH-37 — Enterprise Flow Builder & Commercial Action Catalog.
Tranche 1 (engine, vocabulary, action registry, API) and Tranche 2 (the
step builder console). One atomic, idempotent apply.

Run from backend/:

    python apply_arch37.py --check     # report, write nothing
    python apply_arch37.py             # apply
    python apply_arch37.py             # again: every line reads "already applied"
    alembic upgrade head               # -> arch37_step1_flow_builder

Same engine as apply_arch39.py: NEW FILE (written if absent, identical is
fine, different is a failure) and ANCHORED PATCH (exact substrings, each
expected exactly once, with a sentinel inside the patched text). Every
anchor below was generated from the diff against `ARCH 39 DONE` (e2e1cb0)
and replayed against that tree before this file was written. Everything is
validated before anything is written; a failed write restores what this run
already wrote.

WHAT IT DELIVERS
================

Trigger catalog (app/services/automation/triggers.py)
  12 triggers over 13 INTERNAL events, served at
  GET /workspaces/{id}/automation/catalog. Every one has an emitter;
  verify_arch37 V3 fails on a trigger nothing sends.

INTERNAL twins and native triggers
  `trigger.` is a reserved internal namespace.
  outbox_service.emit_public_with_twin / emit_trigger write the event AND its
  automation.execute job in the caller's transaction (nothing relays INTERNAL
  events). Wired at: pipeline_state (document.completed/failed),
  case_service (procurement x3), radar/findings (anomaly.detected),
  document_intake_service (work_item.created), work_items reprocess,
  assertions/triage (assertion.held), redaction_service (redaction.completed).

Rule resolution
  EVENT_TO_RULE_TRIGGER is gone; rules resolve from automation_rule_triggers.
  "Clause check held" rules run while the document is under review.

Action registry (app/services/automation/actions/)
  webhook.send, redaction.start, review.escalate, warehouse.export,
  notify.role, autonomy.decide, email.send (+ work_item.mutate).
  Schema, R33 selector (app/services/tools/flow_selectors.py), perform,
  capability and minimum role, asserted at import. The executor dispatches
  through it; node runs record action_type, external_ref, duration_ms.

Flow rules
  condition groups, event.<key> fields, "otherwise" actions (a DAG branch),
  autonomy.decide holding a document stops the actions after it.
  Save-time validation returns FastAPI-shaped issues the console maps to cards.
  Test runs of flow rules are dry runs.

Latent defects fixed on the way
  * webhook_deliveries / webhook_endpoints CHECKs never admitted procurement.*
    or anomaly.detected, so those webhooks were refused by the database.
  * work_item.field_changed was emitted with no automation job.
  * work_item.mutate could set status / pipeline_stage.
  * the handler loaded a work item without a workspace predicate.
  * rule create/update/delete wrote no audit row.

Migrations: arch37_step0_flow_vocabulary (AUTOMATION_RULE audit resource),
arch37_step1_flow_builder (tables, columns, CHECKs, backfill; rules whose
legacy trigger never fired are PAUSED and audited).

Earlier gates: verify_arch31_step0, 31, 34, 35, 36 and 39 pin the Alembic
head; each is widened to accept arch37_step1_flow_builder.

Console (Tranche 2)
  pages/Automation/FlowBuilder.tsx and components/automation/flow/*: a vertical
  step builder (When -> Only if -> Then -> Otherwise) with a summary rail,
  catalog-driven pickers, per-action forms, server refusals mapped to cards,
  Ctrl/Cmd+S, Esc and an unsaved-changes guard. The rule list and the
  execution timeline read labels and node runs from the new endpoints.
  Retired: RuleForm.tsx, RuleEditor.tsx, schemas/automation.ts,
  constants/automationFields.ts (deleted only if unchanged since ARCH-39).
  The API still accepts the ARCH-13 rule shape.

ROLLBACK
========

    alembic downgrade arch39_step1_conversations
    git checkout -- backend
    git clean -n backend     # review, then -f
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"


# ---------------------------------------------------------------------------
# Operation types
# ---------------------------------------------------------------------------


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)


@dataclass
class FileReplace:
    root: Path
    relpath: str
    sentinel: str
    base_sha256: str
    content: str


@dataclass
class NewFile:
    root: Path
    relpath: str
    content: str


@dataclass
class DeleteFile:
    """ARCH-37: remove a file the milestone retires, only if it is still the
    file this script was written against (sha256 of its normalised text)."""

    root: Path
    relpath: str
    base_sha256: str


DELETE = b"\x00__ARCH37_DELETE__"


Operation = Union[FilePatch, FileReplace, NewFile, DeleteFile]


class PatchError(RuntimeError):
    pass


def _read(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _encode(text: str, newline: str, had_bom: bool) -> bytes:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    return b"\xef\xbb\xbf" + data if had_bom else data


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


#: ARCH38-S1:supersede-newfile. A later milestone may legitimately edit a file
#: an earlier apply created. ARCH-38 patches
#: `app/services/automation/triggers.py` to add the `batch.completed` entry,
#: which makes that file differ from the text ARCH-37 wrote.
#:
#: Without this, `verify_arch37.py`'s "a second apply changes nothing" gate
#: would fail on every tree that has ARCH-38 applied -- and the only ways to
#: silence it would be to delete the gate or to forbid later milestones from
#: touching the file, both worse than recording the supersession here.
#:
#: A file is superseded only when it carries a later milestone's sentinel.
#: Arbitrary local edits still fail, which is the property the gate exists for.
#: ARCH40-S1:supersede-newfile. ARCH-40 edits files this apply created
#: (verify scripts' head pins, the trigger catalog's gate counts).
SUPERSEDING_SENTINELS: tuple[str, ...] = ("ARCH38-S1:", "ARCH40-S1:")


def _is_superseded(text: str) -> bool:
    return any(sentinel in text for sentinel in SUPERSEDING_SENTINELS)


@dataclass
class Planned:
    path: Path
    relpath: str
    data: Optional[bytes]  # None: nothing to write
    created: bool
    message: str


def plan(op: Operation) -> Planned:
    path = op.root / op.relpath

    if isinstance(op, DeleteFile):
        if not path.exists():
            return Planned(path, op.relpath, None, False, "already removed")
        current, _, _ = _read(path)
        if _sha256(current) != op.base_sha256:
            raise PatchError(
                f"{op.relpath}: has local changes, so this script will not delete it. "
                "ARCH-37 retires this file; review your changes, then delete it "
                "yourself. Nothing has been written."
            )
        return Planned(path, op.relpath, DELETE, False, "retired (deleted)")

    if isinstance(op, NewFile):
        if path.exists():
            current, _, _ = _read(path)
            if current == op.content:
                return Planned(path, op.relpath, None, False, "already present")
            if _is_superseded(current):
                return Planned(
                    path, op.relpath, None, False, "superseded by a later milestone"
                )
            raise PatchError(
                f"{op.relpath}: exists with different content. ARCH-39 creates "
                "this file; a different file at this path is not something "
                "this script will overwrite. Nothing has been written."
            )
        return Planned(path, op.relpath, op.content.encode("utf-8"), True, "new file")

    if not path.exists():
        raise PatchError(f"{op.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if op.sentinel in text:
        return Planned(path, op.relpath, None, False, "already applied")

    if isinstance(op, FileReplace):
        if op.sentinel not in op.content:
            raise PatchError(f"{op.relpath}: sentinel is absent from the replacement")
        actual = _sha256(text)
        if actual != op.base_sha256:
            raise PatchError(
                f"{op.relpath}: sha256 {actual[:12]}… is not the ARCH-39 file "
                f"({op.base_sha256[:12]}…). It has local changes this script "
                "would discard. Nothing has been written."
            )
        return Planned(
            path, op.relpath, _encode(op.content, newline, had_bom), False, "replaced"
        )

    updated = text
    for edit in op.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{op.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if op.sentinel not in updated:
        raise PatchError(
            f"{op.relpath}: sentinel {op.sentinel!r} is absent from the patched "
            "text. A sentinel must be a substring of what its own patch writes, "
            "or the next run re-applies the edit."
        )
    return Planned(
        path, op.relpath, _encode(updated, newline, had_bom), False, f"{len(op.edits)} edit(s)"
    )



# ===========================================================================
# Constants
# ===========================================================================

HEAD_BEFORE = "arch39_step1_conversations"
HEAD_AFTER = "arch37_step1_flow_builder"

# ===========================================================================
# New files
# ===========================================================================

NEW_BACKEND_FILES: dict[str, str] = {}
NEW_BACKEND_FILES['alembic/versions/arch37_step0_flow_vocabulary.py'] = r'''"""ARCH-37 Step 0 — audit vocabulary for the flow builder (EXPAND)

Revision ID: arch37_step0_flow_vocabulary
Revises: arch39_step1_conversations
Create Date: 2026-09-17

WHY THIS IS ITS OWN MIGRATION
=============================

`arch37_step1_flow_builder` pauses rules whose trigger could never fire and
writes one audit row per paused rule. Those rows need the resource type
AUTOMATION_RULE, and PostgreSQL refuses to let an enum value be used in the
transaction that added it. ARCH-20, 22, 26 and 31 split vocabulary from DDL for
the same reason; this follows them.

AUTOMATION_RULE is also what the rule API now records on create, update and
delete. Before ARCH-37 a rule that could send email to any address was edited
with no audit trail at all.
"""

from __future__ import annotations

from alembic import op

revision = "arch37_step0_flow_vocabulary"
down_revision = "arch39_step1_conversations"
branch_labels = None
depends_on = None

NEW_RESOURCE_TYPES: tuple[str, ...] = ("AUTOMATION_RULE",)


def upgrade() -> None:
    with op.get_context().autocommit_block():
        for value in NEW_RESOURCE_TYPES:
            op.execute(
                f"ALTER TYPE audit_resource_type ADD VALUE IF NOT EXISTS '{value}'"
            )


def downgrade() -> None:
    """No-op, deliberately.

    PostgreSQL cannot drop an enum value, and rewriting `audit_resource_type`
    would rewrite every audit row, which ARCH-07's immutability trigger
    refuses. A value nothing emits is harmless.
    """
'''

NEW_BACKEND_FILES['alembic/versions/arch37_step1_flow_builder.py'] = r'''"""ARCH-37 Step 1 — flow builder: rule triggers, node-run facts, vocabulary (EXPAND)

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
'''

NEW_BACKEND_FILES['app/models/automation_trigger.py'] = r'''"""ARCH-37 — which internal events a rule listens to.

One row per (rule, event). The automation handler resolves rules by joining
this table on the event it was handed; before ARCH-37 it looked the event up in
a three-entry dictionary and three of the four triggers the console offered
could never match.

`workspace_id` is denormalised so the resolution query is a single index scan
on (workspace_id, event_type). `trg_automation_rule_triggers_workspace` keeps
it equal to the rule's workspace, so the copy cannot drift into a cross-tenant
match.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.automation_events import LEGACY_RULE_EVENT_TYPES
from app.db.base import Base

_LEGACY_SQL = ", ".join(f"'{value}'" for value in LEGACY_RULE_EVENT_TYPES)


class AutomationRuleTrigger(Base):
    __tablename__ = "automation_rule_triggers"

    __table_args__ = (
        CheckConstraint(
            f"event_type LIKE 'trigger.%' OR event_type IN ({_LEGACY_SQL})",
            name="ck_automation_rule_triggers_event_known",
        ),
        Index(
            "ix_automation_rule_triggers_workspace_event",
            "workspace_id",
            "event_type",
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("automation_rules.id", ondelete="CASCADE"),
        primary_key=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )

    def __repr__(self) -> str:
        return f"<AutomationRuleTrigger {self.rule_id} {self.event_type}>"


__all__ = ["AutomationRuleTrigger"]
'''

NEW_BACKEND_FILES['app/services/automation/triggers.py'] = r'''"""ARCH-37 — the trigger catalog. The only list of triggers in the product.

Served at `GET /workspaces/{id}/automation/catalog`. The console renders this
and nothing else; it holds no trigger list of its own.

WHAT AN ENTRY PROMISES
======================

Every entry names the INTERNAL events it listens to, and every one of those
events has an emitter in the code. `verify_arch37.py` greps for each emitter,
so a trigger that nothing sends fails the build instead of shipping as a rule
that silently never runs — the defect the pre-ARCH-37 dropdown had in three of
its four entries.

WHAT IS DELIBERATELY ABSENT
===========================

Organization-scoped identity and billing events: rules are workspace-scoped and
those events carry no workspace. `trigger.batch.completed`: reserved in the
vocabulary for ARCH-38, listed here only once ARCH-38 emits it.

This module is pure. It imports no session, no model and no service, so the
gates can load it on its own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Final, Mapping, Optional

from app.core.automation_events import (
    INTERNAL_EVENT_TYPES,
    LEGACY_RULE_EVENT_TYPES,
    TRIGGER_PREFIX,
)
from app.core.entitlements import (
    ANOMALY_RADAR_CAPABILITY,
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
)

FIELD_TYPES: Final[frozenset[str]] = frozenset({"string", "number", "boolean", "array", "date"})

#: Operators the condition builder offers per field type. The evaluator in
#: `automation_service._evaluate_condition` accepts every one of them.
OPERATORS_BY_TYPE: Final[Mapping[str, tuple[str, ...]]] = {
    "string": (
        "EQUALS", "NOT_EQUALS", "CONTAINS", "NOT_CONTAINS", "STARTS_WITH",
        "ENDS_WITH", "IN", "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "number": (
        "EQUALS", "NOT_EQUALS", "GREATER_THAN", "LESS_THAN",
        "GREATER_THAN_OR_EQUAL", "LESS_THAN_OR_EQUAL", "BETWEEN", "IN",
        "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "boolean": ("EQUALS", "NOT_EQUALS", "EXISTS"),
    "array": (
        "CONTAINS", "NOT_CONTAINS", "ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL",
        "IN", "NOT_IN", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
    "date": (
        "EQUALS", "NOT_EQUALS", "STARTS_WITH", "EXISTS", "IS_EMPTY", "IS_NOT_EMPTY",
    ),
}

VALUELESS_OPERATORS: Final[frozenset[str]] = frozenset({"EXISTS", "IS_EMPTY", "IS_NOT_EMPTY"})

#: Condition paths that read the trigger event's payload rather than the
#: document. Resolved by `app.services.automation.conditions`.
EVENT_FIELD_PREFIX: Final[str] = "event."


@dataclass(frozen=True)
class TriggerField:
    key: str
    label: str
    type: str
    example: str = ""
    description: str = ""

    @property
    def path(self) -> str:
        return f"{EVENT_FIELD_PREFIX}{self.key}"

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.path,
            "label": self.label,
            "type": self.type,
            "example": self.example,
            "description": self.description,
            "source": "event",
        }


@dataclass(frozen=True)
class TriggerSpec:
    key: str
    label: str
    category: str
    description: str
    event_types: tuple[str, ...]
    fields: tuple[TriggerField, ...] = ()
    capability: Optional[str] = None
    #: False when the event concerns something other than one document, so
    #: document field conditions have nothing to read.
    has_document: bool = True
    #: Action types this trigger may not run. See the entries for why.
    excluded_actions: tuple[str, ...] = ()
    #: True when the trigger announces a held review. The automation handler
    #: otherwise skips every event for a document with an open review, which
    #: would make this trigger unreachable by construction.
    runs_during_review: bool = False
    #: The pre-ARCH-37 `automation_rules.event` value this entry replaces.
    legacy_event: Optional[str] = None

    def as_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "category": self.category,
            "description": self.description,
            "event_types": list(self.event_types),
            "fields": [f.as_dict() for f in self.fields],
            "capability": self.capability,
            "has_document": self.has_document,
            "excluded_actions": list(self.excluded_actions),
            "runs_during_review": self.runs_during_review,
        }


_DOCUMENT_FIELDS: Final[tuple[TriggerField, ...]] = (
    TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
    TriggerField("status", "Pipeline status", "string", "COMPLETED"),
    TriggerField("page_count", "Page count", "number", "3"),
)

TRIGGERS: Final[tuple[TriggerSpec, ...]] = (
    TriggerSpec(
        key="document.created",
        label="Document uploaded",
        category="Documents",
        description="A file was accepted and queued for extraction. No AI fields exist yet.",
        event_types=("trigger.work_item.created",),
        fields=(
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
            TriggerField("mime_type", "File type", "string", "application/pdf"),
            TriggerField("size_bytes", "Size (bytes)", "number", "482133"),
            TriggerField("page_count", "Page count", "number", "3"),
        ),
        legacy_event="WORK_ITEM_CREATED",
    ),
    TriggerSpec(
        key="document.ready",
        label="Document processed (AI fields ready)",
        category="Documents",
        description=(
            "Enrichment finished, or a human review released the document. "
            "Extracted fields are available to conditions."
        ),
        event_types=("work_item.enriched", "work_item.verification_completed"),
        fields=(
            TriggerField("classification", "Classification", "string", "Invoice"),
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
        ),
        legacy_event="WORK_ITEM_COMPLETED",
    ),
    TriggerSpec(
        key="document.completed",
        label="Document pipeline completed",
        category="Documents",
        description="The processing pipeline reached COMPLETED, including runs where enrichment was skipped.",
        event_types=("trigger.document.completed",),
        fields=_DOCUMENT_FIELDS,
    ),
    TriggerSpec(
        key="document.failed",
        label="Document failed",
        category="Documents",
        description="Extraction or enrichment failed, or the workspace ran out of quota.",
        event_types=("trigger.document.failed",),
        fields=_DOCUMENT_FIELDS + (
            TriggerField("failure_stage", "Failed at stage", "string", "EXTRACTING"),
            TriggerField("failure_reason", "Failure reason", "string", "OCR timeout"),
            TriggerField("quota_blocked", "Blocked by quota", "boolean", "false"),
        ),
        # Nothing to redact or mutate on a document that has no extraction.
        excluded_actions=("redaction.start", "work_item.mutate", "autonomy.decide"),
        legacy_event="WORK_ITEM_FAILED",
    ),
    TriggerSpec(
        key="document.reprocessed",
        label="Document sent for reprocessing",
        category="Documents",
        description="Someone asked for a document to be extracted again.",
        event_types=("trigger.work_item.reprocessed",),
        fields=(
            TriggerField("original_filename", "File name", "string", "invoice-2231.pdf"),
        ),
        legacy_event="WORK_ITEM_REPROCESSED",
    ),
    TriggerSpec(
        key="document.field_changed",
        label="Document field changed by a rule",
        category="Documents",
        description="Another rule, or the public API, set a field on the document.",
        event_types=("work_item.field_changed",),
        fields=(
            TriggerField("field", "Changed field", "string", "priority"),
        ),
        legacy_event="WORK_ITEM_UPDATED",
    ),
    TriggerSpec(
        key="procurement.completed",
        label="Three-way match finished",
        category="Procurement",
        description="An invoice was matched against its purchase order and receipt.",
        event_types=("trigger.procurement.completed",),
        fields=(
            TriggerField("status", "Match status", "string", "EXCEPTION"),
            TriggerField("exception_count", "Exceptions", "number", "2"),
            TriggerField("variance_micros", "Variance (micros)", "number", "12500000"),
            TriggerField("line_count", "Line count", "number", "14"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="procurement.approved",
        label="Three-way match approved",
        category="Procurement",
        description="A reviewer approved a matched invoice for payment.",
        event_types=("trigger.procurement.approved",),
        fields=(
            TriggerField("exception_count", "Exceptions", "number", "0"),
            TriggerField("variance_micros", "Variance (micros)", "number", "0"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="procurement.disputed",
        label="Three-way match disputed",
        category="Procurement",
        description="A reviewer disputed a matched invoice with the supplier.",
        event_types=("trigger.procurement.disputed",),
        fields=(
            TriggerField("variance_micros", "Variance (micros)", "number", "12500000"),
        ),
        capability=RECONCILIATION_CAPABILITY,
    ),
    TriggerSpec(
        key="anomaly.detected",
        label="Anomaly or duplicate detected",
        category="Forensic audit",
        description="The radar raised a finding on a document.",
        event_types=("trigger.anomaly.detected",),
        fields=(
            TriggerField("kind", "Finding kind", "string", "DUPLICATE"),
            TriggerField("layer", "Detection layer", "string", "L2"),
            TriggerField("severity", "Severity", "string", "HIGH"),
            TriggerField("score", "Score", "number", "0.94"),
        ),
        capability=ANOMALY_RADAR_CAPABILITY,
    ),
    TriggerSpec(
        key="assertion.held",
        label="Clause check held for review",
        category="Clause assertions",
        description="A clause assertion was not confident enough to pass and went to a reviewer.",
        event_types=("trigger.assertion.held",),
        fields=(
            TriggerField("family", "Assertion family", "string", "auto_renewal"),
            TriggerField("verdict", "Engine verdict", "string", "FAIL"),
            TriggerField("raw_score", "Raw score", "number", "0.61"),
        ),
        capability=SEMANTIC_ASSERTIONS_CAPABILITY,
        # The document is waiting for a human. Changing its fields or
        # deciding on it automatically would pre-empt that human.
        excluded_actions=("work_item.mutate", "autonomy.decide"),
        runs_during_review=True,
    ),
    TriggerSpec(
        key="redaction.completed",
        label="Redaction published",
        category="Redaction",
        description="A reviewed redaction was applied and the sanitized PDF stored.",
        event_types=("trigger.redaction.completed",),
        fields=(
            TriggerField("profile", "Redaction profile", "string", "india_kyc"),
            TriggerField("pages", "Pages", "number", "4"),
        ),
        capability=REDACTION_CAPABILITY,
        # Starting a redaction from "a redaction finished" is a loop that only
        # a human approval step interrupts, once per document, forever.
        excluded_actions=("redaction.start",),
    ),
)

TRIGGERS_BY_KEY: Final[Mapping[str, TriggerSpec]] = {spec.key: spec for spec in TRIGGERS}


def _events_by_trigger() -> dict[str, TriggerSpec]:
    mapping: dict[str, TriggerSpec] = {}
    for spec in TRIGGERS:
        for event_type in spec.event_types:
            if event_type in mapping:
                raise RuntimeError(
                    f"ARCH-37: event {event_type!r} is claimed by both "
                    f"{mapping[event_type].key!r} and {spec.key!r}."
                )
            mapping[event_type] = spec
    return mapping


TRIGGER_BY_EVENT: Final[Mapping[str, TriggerSpec]] = _events_by_trigger()

#: Every event any rule may be stored against.
CATALOG_EVENT_TYPES: Final[frozenset[str]] = frozenset(TRIGGER_BY_EVENT)

#: Events the automation handler runs even while the document is under review.
REVIEW_EXEMPT_EVENT_TYPES: Final[frozenset[str]] = frozenset(
    event for spec in TRIGGERS if spec.runs_during_review for event in spec.event_types
)

LEGACY_EVENT_TO_TRIGGER: Final[Mapping[str, str]] = {
    spec.legacy_event: spec.key for spec in TRIGGERS if spec.legacy_event
}


def _assert_catalog_is_internal() -> None:
    """Every catalog event is INTERNAL and is one a rule row may store."""
    not_internal = sorted(CATALOG_EVENT_TYPES - INTERNAL_EVENT_TYPES)
    if not_internal:
        raise RuntimeError(
            f"ARCH-37: trigger catalog names non-internal events {not_internal}. "
            "The automation handler refuses PUBLIC events (ARCH-13 F1)."
        )
    unstorable = sorted(
        e for e in CATALOG_EVENT_TYPES
        if not e.startswith(TRIGGER_PREFIX) and e not in LEGACY_RULE_EVENT_TYPES
    )
    if unstorable:
        raise RuntimeError(
            f"ARCH-37: catalog events {unstorable} would be refused by "
            "ck_automation_rule_triggers_event_known."
        )
    for spec in TRIGGERS:
        bad = [f.key for f in spec.fields if f.type not in FIELD_TYPES]
        if bad:
            raise RuntimeError(f"ARCH-37: trigger {spec.key!r} has untyped fields {bad}.")


_assert_catalog_is_internal()


def resolve_trigger_keys(keys: list[str] | tuple[str, ...]) -> tuple[list[TriggerSpec], list[str]]:
    """Specs for known keys, and the unknown keys, in the order given."""
    specs: list[TriggerSpec] = []
    unknown: list[str] = []
    for key in keys:
        spec = TRIGGERS_BY_KEY.get(key)
        if spec is None:
            unknown.append(key)
        elif spec not in specs:
            specs.append(spec)
    return specs, unknown


def event_types_for(keys: list[str] | tuple[str, ...]) -> list[str]:
    specs, _ = resolve_trigger_keys(keys)
    return sorted({event for spec in specs for event in spec.event_types})


def trigger_keys_for_events(event_types: list[str] | tuple[str, ...]) -> list[str]:
    """Which catalog triggers a stored set of events amounts to."""
    held = set(event_types)
    return [
        spec.key for spec in TRIGGERS if held.issuperset(spec.event_types)
    ]


def fields_for(keys: list[str] | tuple[str, ...]) -> list[TriggerField]:
    """Event fields common to EVERY selected trigger.

    A condition on a field only some of the triggers carry would read None on
    the others and quietly evaluate false.
    """
    specs, _ = resolve_trigger_keys(keys)
    if not specs:
        return []
    common = {f.key for f in specs[0].fields}
    for spec in specs[1:]:
        common &= {f.key for f in spec.fields}
    return [f for f in specs[0].fields if f.key in common]


def excluded_actions_for(keys: list[str] | tuple[str, ...]) -> set[str]:
    specs, _ = resolve_trigger_keys(keys)
    return {action for spec in specs for action in spec.excluded_actions}


def catalog_triggers() -> list[dict[str, object]]:
    return [spec.as_dict() for spec in TRIGGERS]


__all__ = [
    "CATALOG_EVENT_TYPES",
    "EVENT_FIELD_PREFIX",
    "FIELD_TYPES",
    "LEGACY_EVENT_TO_TRIGGER",
    "OPERATORS_BY_TYPE",
    "REVIEW_EXEMPT_EVENT_TYPES",
    "TRIGGERS",
    "TRIGGERS_BY_KEY",
    "TRIGGER_BY_EVENT",
    "TriggerField",
    "TriggerSpec",
    "VALUELESS_OPERATORS",
    "catalog_triggers",
    "event_types_for",
    "excluded_actions_for",
    "fields_for",
    "resolve_trigger_keys",
    "trigger_keys_for_events",
]
'''

NEW_BACKEND_FILES['app/services/automation/conditions.py'] = r'''"""ARCH-37 — condition groups and trigger-payload fields.

A flow-builder rule stores its conditions as groups:

    {"groups_operator": "AND",
     "groups": [{"logic_operator": "OR", "conditions": [...]}, ...]}

Each condition's `field` is either a document path (resolved exactly as
before ARCH-37, by `automation_service.resolve_field`) or `event.<key>`, which
reads the trigger event's payload. Before ARCH-37 a rule could only look at the
document, so "when a match finishes with more than two exceptions" had no way
to say "more than two exceptions".

An empty group list matches: a flow rule with no conditions runs on every
occurrence of its trigger, which is what "When an anomaly is detected, notify
Finance admins" means.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

from app.services.automation.triggers import EVENT_FIELD_PREFIX, VALUELESS_OPERATORS

MAX_GROUPS = 10
MAX_CONDITIONS_PER_GROUP = 20


def _attr(item: Any, name: str) -> Any:
    if isinstance(item, Mapping):
        return item.get(name)
    return getattr(item, name, None)


def _nested(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
        if current is None:
            return None
    return current


def resolve(
    field_path: str,
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> Any:
    if field_path.startswith(EVENT_FIELD_PREFIX):
        return _nested(event_payload or {}, field_path[len(EVENT_FIELD_PREFIX):])
    if work_item is None:
        return None
    from app.services.automation_service import resolve_field

    return resolve_field(work_item, field_path)


def evaluate_condition(
    condition: Any,
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    from app.services.automation_service import _evaluate_condition

    field_path = _attr(condition, "field")
    operator = _attr(condition, "operator")
    value = _attr(condition, "value")
    if not isinstance(field_path, str) or not field_path.strip():
        return False
    if not isinstance(operator, str) or not operator.strip():
        return False
    normalised = operator.upper().strip()
    if normalised not in VALUELESS_OPERATORS and (value is None or str(value).strip() == ""):
        return False
    actual = resolve(field_path.strip(), work_item=work_item, event_payload=event_payload)
    return bool(_evaluate_condition(actual, normalised, "" if value is None else str(value)))


def _combine(results: Sequence[bool], operator: str) -> bool:
    if not results:
        return True
    return any(results) if operator == "OR" else all(results)


def _operator(value: Any) -> str:
    text = str(value or "AND").upper().strip()
    return text if text in ("AND", "OR") else "AND"


def evaluate_groups(
    groups: Sequence[Any],
    *,
    groups_operator: Any,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    outcomes: list[bool] = []
    for group in groups or ():
        conditions = list(_attr(group, "conditions") or [])
        if not conditions:
            continue
        results = [
            evaluate_condition(c, work_item=work_item, event_payload=event_payload)
            for c in conditions
        ]
        outcomes.append(_combine(results, _operator(_attr(group, "logic_operator"))))
    return _combine(outcomes, _operator(groups_operator))


def evaluate_node_config(
    config: Mapping[str, Any],
    *,
    work_item: Any,
    event_payload: Optional[Mapping[str, Any]],
) -> bool:
    """A condition node's config, grouped (ARCH-37) or flat (ARCH-13)."""
    if "groups" in config:
        return evaluate_groups(
            config.get("groups") or [],
            groups_operator=config.get("groups_operator"),
            work_item=work_item,
            event_payload=event_payload,
        )
    flat = list(config.get("conditions") or [])
    if not flat:
        return False
    results = [
        evaluate_condition(c, work_item=work_item, event_payload=event_payload)
        for c in flat
    ]
    return _combine(results, _operator(config.get("logic_operator")))


def flatten(groups: Sequence[Any]) -> list[dict[str, Any]]:
    """Every condition in every group, for the legacy `conditions` column."""
    flat: list[dict[str, Any]] = []
    for group in groups or ():
        for condition in _attr(group, "conditions") or ():
            flat.append(
                {
                    "field": _attr(condition, "field"),
                    "operator": _attr(condition, "operator"),
                    "value": _attr(condition, "value") or "",
                }
            )
    return flat


__all__ = [
    "MAX_CONDITIONS_PER_GROUP",
    "MAX_GROUPS",
    "evaluate_condition",
    "evaluate_groups",
    "evaluate_node_config",
    "flatten",
    "resolve",
]
'''

NEW_BACKEND_FILES['app/services/automation/flow_service.py'] = r'''"""ARCH-37 — save-time validation, storage shape and dry runs for flow rules.

Every refusal is returned as a FastAPI-style issue list
(`[{"loc": ["body", "actions", 2, "config", "endpoint_id"], "msg": ...}]`),
which the console already parses (`services/api/errors.ts`) and maps to the
card that caused it.

WHAT IS CHECKED, AND WHY HERE
=============================

  triggers         known catalog keys the tenant's plan includes
  conditions       event fields every selected trigger carries; operators
                   valid for the field's type; values where required
  actions          registered; allowed for the triggers; config valid against
                   the action's schema; capability or add-on held; author's
                   organization role sufficient; referenced endpoint or
                   destination in this organization and active; template
                   variables from the allowlist
  otherwise        only with at least one condition

The run path re-checks capability, add-on and resource state, because each can
change after a rule is saved.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from pydantic import ValidationError

from app.services.automation import actions as action_registry
from app.services.automation import triggers as catalog
from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    SaveContext,
    invalid_variables,
)

MAX_ACTIONS = 10
DOCUMENT_ACTIONS = frozenset(
    {"redaction.start", "review.escalate", "autonomy.decide", "work_item.mutate"}
)
ALL_OPERATORS = frozenset(op for ops in catalog.OPERATORS_BY_TYPE.values() for op in ops)


class FlowValidationError(ValueError):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("; ".join(f"{'.'.join(map(str, i['loc']))}: {i['msg']}" for i in issues))
        self.issues = issues


@dataclass(frozen=True)
class Authoring:
    """Who is saving, and what their tenant holds."""

    db: Any
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    organization_role: str
    granted_capabilities: frozenset[str]
    addons: frozenset[str]


@dataclass
class NormalisedRule:
    event: str
    trigger_keys: list[str]
    event_types: list[str]
    conditions: list[dict[str, Any]]
    logic_operator: str
    actions: list[dict[str, Any]]
    flow_spec: Optional[dict[str, Any]]
    on_error: str
    issues: list[dict[str, Any]] = field(default_factory=list)


def _issue(loc: Sequence[Any], msg: str, kind: str = "value_error") -> dict[str, Any]:
    return {"loc": ["body", *loc], "msg": msg, "type": kind}


def _dump(model: Any) -> Any:
    return model.model_dump(mode="json") if hasattr(model, "model_dump") else model


def _role_ok(minimum: str, organization_role: str) -> bool:
    if minimum == ROLE_ORGANIZATION_ADMIN:
        return organization_role in ("OWNER", "ADMIN")
    return True


def _validate_conditions(
    groups: list[dict[str, Any]], trigger_keys: list[str], issues: list[dict[str, Any]]
) -> None:
    event_fields = {f.path: f for f in catalog.fields_for(trigger_keys)}
    specs, _ = catalog.resolve_trigger_keys(trigger_keys)
    documents = all(spec.has_document for spec in specs)
    for g_index, group in enumerate(groups):
        for c_index, condition in enumerate(group.get("conditions") or []):
            loc = ("condition_groups", g_index, "conditions", c_index)
            path = str(condition.get("field") or "")
            operator = str(condition.get("operator") or "").upper()
            value = str(condition.get("value") or "")
            if path.startswith(catalog.EVENT_FIELD_PREFIX):
                spec_field = event_fields.get(path)
                if spec_field is None:
                    issues.append(_issue((*loc, "field"), f"{path!r} is not carried by every selected trigger."))
                    continue
                allowed = catalog.OPERATORS_BY_TYPE[spec_field.type]
                if operator not in allowed:
                    issues.append(_issue((*loc, "operator"), f"{operator} does not apply to a {spec_field.type} field."))
            else:
                if not documents:
                    issues.append(_issue((*loc, "field"), "The selected trigger has no document to read."))
                if operator not in ALL_OPERATORS:
                    issues.append(_issue((*loc, "operator"), f"Unknown operator {operator!r}."))
            if operator not in catalog.VALUELESS_OPERATORS and not value.strip():
                issues.append(_issue((*loc, "value"), "A value is required for this operator."))


def _validate_actions(
    key: str,
    actions: list[dict[str, Any]],
    trigger_keys: list[str],
    authoring: Authoring,
    issues: list[dict[str, Any]],
    *,
    legacy: bool,
) -> list[dict[str, Any]]:
    excluded = catalog.excluded_actions_for(trigger_keys)
    specs, _ = catalog.resolve_trigger_keys(trigger_keys)
    documents = all(spec.has_document for spec in specs)
    event_keys = {f.path for f in catalog.fields_for(trigger_keys)}
    ctx = SaveContext(
        db=authoring.db,
        organization_id=authoring.organization_id,
        workspace_id=authoring.workspace_id,
        trigger_keys=tuple(trigger_keys),
    )
    stored: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        loc = (key, index)
        raw_type = str(action.get("action_type") or "")
        definition = action_registry.get(raw_type)
        if definition is None:
            issues.append(_issue((*loc, "action_type"), f"Unknown action {raw_type!r}."))
            continue
        action_type = definition.action_type
        if action_type in excluded:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} cannot run on the selected trigger."))
        if action_type in DOCUMENT_ACTIONS and not documents:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} needs a document."))
        if definition.capability and definition.capability not in authoring.granted_capabilities:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} is not included in your plan.", "plan"))
        if definition.addon and definition.addon not in authoring.addons:
            issues.append(_issue((*loc, "action_type"), f"{definition.label} needs an add-on your organization does not have.", "plan"))
        if not _role_ok(definition.minimum_role, authoring.organization_role):
            issues.append(_issue((*loc, "action_type"), f"Only organization owners and admins can add {definition.label}.", "permission"))

        config_values = dict(action.get("config") or {})
        try:
            config = definition.parse(config_values)
        except ValidationError as exc:
            for error in exc.errors():
                issues.append(_issue((*loc, "config", *error["loc"]), error["msg"]))
            continue

        for template_field in definition.template_fields:
            bad = invalid_variables(str(getattr(config, template_field, "") or ""), event_keys=event_keys)
            if bad:
                issues.append(_issue((*loc, "config", template_field), f"Unknown template variable(s): {', '.join(bad)}."))

        if definition.validate_resources is not None:
            for config_key, message in definition.validate_resources(ctx, config).items():
                issues.append(_issue((*loc, "config", config_key), message))

        if legacy and raw_type != action_type:
            # An ARCH-13 client: keep its stored shape so its message is unchanged.
            stored.append({"action_type": raw_type, "config": config_values})
        else:
            stored.append({"action_type": action_type, "config": _dump(config)})
    return stored


def normalise(payload: dict[str, Any], authoring: Authoring) -> NormalisedRule:
    """Validate a complete rule document and return its storage shape."""
    issues: list[dict[str, Any]] = []
    trigger_keys = payload.get("triggers")
    legacy = trigger_keys is None

    if legacy:
        legacy_event = str(payload.get("event") or "")
        mapped = catalog.LEGACY_EVENT_TO_TRIGGER.get(legacy_event)
        if mapped is None:
            issues.append(_issue(("event",), f"Unknown event {legacy_event!r}."))
            trigger_keys = []
        else:
            trigger_keys = [mapped]
    else:
        trigger_keys = list(dict.fromkeys(trigger_keys))
        specs, unknown = catalog.resolve_trigger_keys(trigger_keys)
        for key in unknown:
            issues.append(_issue(("triggers", trigger_keys.index(key)), f"Unknown trigger {key!r}."))
        for spec in specs:
            if spec.capability and spec.capability not in authoring.granted_capabilities:
                issues.append(_issue(("triggers", trigger_keys.index(spec.key)), f"{spec.label} is not included in your plan.", "plan"))

    on_error = str(payload.get("on_error") or "HALT")
    actions = [_dump(a) for a in payload.get("actions") or []]
    else_actions = [_dump(a) for a in payload.get("else_actions") or []]
    if not actions:
        issues.append(_issue(("actions",), "Add at least one action."))
    if len(actions) > MAX_ACTIONS or len(else_actions) > MAX_ACTIONS:
        issues.append(_issue(("actions",), f"At most {MAX_ACTIONS} actions per branch."))

    if legacy:
        conditions = [_dump(c) for c in payload.get("conditions") or []]
        groups = [{"logic_operator": payload.get("logic_operator") or "AND", "conditions": conditions}]
        groups_operator = "AND"
    else:
        groups = [_dump(g) for g in payload.get("condition_groups") or []]
        groups = [g for g in groups if g.get("conditions")]
        groups_operator = str(payload.get("groups_operator") or "AND")
    _validate_conditions(groups, trigger_keys, issues)

    has_conditions = any(g.get("conditions") for g in groups)
    if else_actions and not has_conditions:
        issues.append(_issue(("else_actions",), "'Otherwise' actions need at least one condition."))

    stored_actions = _validate_actions("actions", actions, trigger_keys, authoring, issues, legacy=legacy)
    stored_else = _validate_actions("else_actions", else_actions, trigger_keys, authoring, issues, legacy=False)

    if issues:
        raise FlowValidationError(issues)

    from app.services.automation.conditions import flatten

    event_types = catalog.event_types_for(trigger_keys)
    if legacy:
        return NormalisedRule(
            event=str(payload.get("event")),
            trigger_keys=trigger_keys,
            event_types=event_types,
            conditions=groups[0]["conditions"],
            logic_operator=str(groups[0]["logic_operator"]),
            actions=stored_actions,
            flow_spec=None,
            on_error=on_error,
        )
    single_group = len(groups) == 1
    return NormalisedRule(
        event=trigger_keys[0],
        trigger_keys=trigger_keys,
        event_types=event_types,
        conditions=flatten(groups),
        logic_operator=str(groups[0]["logic_operator"]) if single_group else groups_operator,
        actions=stored_actions,
        flow_spec={
            "version": 1,
            "condition_groups": groups,
            "groups_operator": groups_operator,
            "else_actions": stored_else,
        },
        on_error=on_error,
    )


def payload_from_rule(rule: Any, event_types: list[str]) -> dict[str, Any]:
    """The rule as a complete flow document, for merging a partial update."""
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is None:
        return {
            "event": rule.event,
            "conditions": list(rule.conditions or []),
            "logic_operator": rule.logic_operator,
            "actions": list(rule.actions or []),
            "on_error": rule.on_error,
        }
    return {
        "triggers": catalog.trigger_keys_for_events(event_types),
        "condition_groups": list(spec.get("condition_groups") or []),
        "groups_operator": spec.get("groups_operator") or "AND",
        "actions": list(rule.actions or []),
        "else_actions": list(spec.get("else_actions") or []),
        "on_error": rule.on_error,
    }


def rule_view(rule: Any, event_types: list[str]) -> dict[str, Any]:
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is not None:
        groups = list(spec.get("condition_groups") or [])
        groups_operator = spec.get("groups_operator") or "AND"
        else_actions = list(spec.get("else_actions") or [])
    else:
        groups = (
            [{"logic_operator": rule.logic_operator, "conditions": list(rule.conditions or [])}]
            if rule.conditions
            else []
        )
        groups_operator = "AND"
        else_actions = []
    return {
        "id": rule.id,
        "workspace_id": rule.workspace_id,
        "created_by_user_id": rule.created_by_user_id,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
        "name": rule.name,
        "priority": rule.priority,
        "event": rule.event,
        "is_active": rule.is_active,
        "conditions": list(rule.conditions or []),
        "logic_operator": rule.logic_operator,
        "actions": list(rule.actions or []),
        "triggers": catalog.trigger_keys_for_events(event_types),
        "trigger_events": list(event_types),
        "condition_groups": groups,
        "groups_operator": groups_operator,
        "else_actions": else_actions,
        "is_flow": spec is not None,
        "graph_version": int(rule.graph_version or 0),
        "on_error": rule.on_error,
    }


def dry_run(db: Any, *, rule: Any, work_item: Any) -> dict[str, Any]:
    """Evaluate the conditions on a document and report what WOULD run.

    Nothing is performed. A test run that started a redaction or posted to a
    customer's endpoint because an administrator clicked "Test" would be a
    production side effect with no trigger behind it.
    """
    from app.services.automation import conditions
    from app.services.automation.contracts import ActionNodeConfig

    started = time.perf_counter()
    spec = rule.flow_spec if isinstance(rule.flow_spec, dict) else None
    if spec is not None:
        groups = list(spec.get("condition_groups") or [])
        matched = conditions.evaluate_groups(
            groups,
            groups_operator=spec.get("groups_operator"),
            work_item=work_item,
            event_payload=None,
        )
        planned = list(rule.actions or []) if matched else list(spec.get("else_actions") or [])
        branch = "then" if matched else ("otherwise" if planned else "none")
    else:
        matched = conditions.evaluate_node_config(
            {"conditions": list(rule.conditions or []), "logic_operator": rule.logic_operator},
            work_item=work_item,
            event_payload=None,
        )
        planned = list(rule.actions or []) if matched else []
        branch = "then" if matched else "none"

    lines: list[str] = []
    ok = True
    for index, action in enumerate(planned):
        node = ActionNodeConfig.from_node_config(action)
        definition = action_registry.get(node.action_type)
        if definition is None:
            ok = False
            lines.append(f"#{index + 1} unknown action {node.action_type!r}")
            continue
        try:
            definition.parse(node.authored_parameters())
        except ValidationError as exc:
            ok = False
            lines.append(f"#{index + 1} {definition.label}: invalid config ({exc.error_count()} issue(s))")
            continue
        lines.append(f"#{index + 1} would run: {definition.label}")

    if not planned:
        message = "Conditions were not met; nothing would run."
    else:
        message = f"Conditions {'met' if matched else 'not met'} ({branch} branch). " + "; ".join(lines)
    return {
        "success": ok,
        "matched": bool(matched),
        "notification_sent": False,
        "message": message[:2000],
        "execution_time_ms": round((time.perf_counter() - started) * 1000.0, 2),
    }


__all__ = [
    "Authoring",
    "FlowValidationError",
    "NormalisedRule",
    "dry_run",
    "normalise",
    "payload_from_rule",
    "rule_view",
]
'''

NEW_BACKEND_FILES['app/services/automation/catalog_service.py'] = r'''"""ARCH-37 — `GET /workspaces/{id}/automation/catalog`.

Everything the step builder may offer, for this tenant, in one response:
triggers and actions with availability, operators per field type, template
variables, the workspace's observed document fields, and the resources an
action can point at (endpoints, destinations, profiles, roles, datasets).

Observed fields come from the most recent documents' extracted entities, so a
healthcare workspace sees `patient_name` and an AP workspace sees
`total_amount` without anyone maintaining a list.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.entitlements import WAREHOUSE_SYNC_ADDON
from app.services.automation import actions as action_registry
from app.services.automation import triggers as catalog
from app.services.automation.actions.base import STATIC_VARIABLES
from app.services.automation.flow_service import Authoring

OBSERVED_SAMPLE = 200
MAX_OBSERVED_FIELDS = 120
SKIPPED_ENTITY_KEYS = frozenset({"verification", "_meta"})


def authoring_for(db: Session, *, context: Any) -> Authoring:
    from app.api import capability_gate
    from app.services.billing import entitlement_service

    granted = frozenset(
        capability_gate.granted_capabilities(db, organization_id=context.organization_id)
    )
    addons: set[str] = set()
    try:
        if entitlement_service.addon_access(
            db, organization_id=context.organization_id, addon_key=WAREHOUSE_SYNC_ADDON
        ).can_maintain:
            addons.add(WAREHOUSE_SYNC_ADDON)
    except Exception:  # noqa: BLE001 - an unreadable ledger is "no add-on"
        pass
    role = getattr(getattr(context, "organization_membership", None), "role", None)
    return Authoring(
        db=db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        organization_role=str(getattr(role, "value", role) or ""),
        granted_capabilities=granted,
        addons=frozenset(addons),
    )


def _type_of(value: Any) -> str | None:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, str):
        stripped = value.strip()
        if len(stripped) == 10 and stripped[4:5] == "-" and stripped[7:8] == "-":
            return "date"
        try:
            float(stripped.replace(",", ""))
            return "number" if stripped else "string"
        except ValueError:
            return "string"
    return None


def observed_fields(db: Session, *, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.models.work_item import WorkItem

    rows = db.execute(
        select(WorkItem.extracted_entities)
        .where(WorkItem.workspace_id == workspace_id, WorkItem.extracted_entities.is_not(None))
        .order_by(WorkItem.created_at.desc())
        .limit(OBSERVED_SAMPLE)
    ).scalars().all()

    seen: dict[str, dict[str, Any]] = {}

    def note(path: str, value: Any) -> None:
        kind = _type_of(value)
        if kind is None:
            return
        entry = seen.setdefault(path, {"key": path, "type": kind, "count": 0, "example": ""})
        entry["count"] += 1
        if entry["type"] != kind and {entry["type"], kind} == {"number", "string"}:
            entry["type"] = "string"
        if not entry["example"] and value not in (None, "", []):
            entry["example"] = str(value if not isinstance(value, list) else (value[0] if value else ""))[:60]

    for entities in rows:
        if not isinstance(entities, dict):
            continue
        for key, value in entities.items():
            if key in SKIPPED_ENTITY_KEYS or not isinstance(key, str):
                continue
            if isinstance(value, dict):
                for sub, sub_value in value.items():
                    if isinstance(sub, str):
                        note(f"{key}.{sub}", sub_value)
            else:
                note(key, value)

    fields = sorted(seen.values(), key=lambda e: (-e["count"], e["key"]))[:MAX_OBSERVED_FIELDS]
    return [
        {
            "key": entry["key"],
            "label": entry["key"].replace("classification_details.", "").replace("_", " ").replace(".", " › ").capitalize(),
            "type": entry["type"],
            "example": entry["example"],
            "description": f"Seen on {entry['count']} recent document(s).",
            "source": "document",
        }
        for entry in fields
    ]


def _endpoints(db: Session, *, organization_id: uuid.UUID, workspace_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

    rows = db.execute(
        select(WebhookEndpoint)
        .where(
            WebhookEndpoint.organization_id == organization_id,
            WebhookEndpoint.status == WebhookEndpointStatus.ACTIVE,
            (WebhookEndpoint.workspace_id.is_(None)) | (WebhookEndpoint.workspace_id == workspace_id),
        )
        .order_by(WebhookEndpoint.created_at.asc())
    ).scalars().all()
    return [
        {
            "id": str(row.id),
            # The host only. Paths often carry tokens.
            "label": (row.description or urlparse(row.url).hostname or "endpoint")[:120],
            "host": urlparse(row.url).hostname or "",
        }
        for row in rows
    ]


def _destinations(db: Session, *, organization_id: uuid.UUID) -> list[dict[str, Any]]:
    from app.services.analytics import sync_service

    return [
        {"id": str(row.id), "label": row.label, "kind": row.kind}
        for row in sync_service.list_destinations(db, organization_id=organization_id)
        if row.is_active
    ]


def build(db: Session, *, context: Any) -> dict[str, Any]:
    from app.models.warehouse_sync import EXPORT_DATASET_VALUES
    from app.services.automation.actions.notify_role import ROLE_VALUES
    from app.services.automation.actions.work_item_mutate import MUTABLE_COLUMNS
    from app.services.redaction.vocabulary import PROFILE_KEYS

    authoring = authoring_for(db, context=context)
    triggers = []
    for entry in catalog.catalog_triggers():
        capability = entry["capability"]
        entry["available"] = capability is None or capability in authoring.granted_capabilities
        triggers.append(entry)

    actions = []
    for action_type, definition in action_registry.ACTIONS.items():
        reason = None
        if definition.capability and definition.capability not in authoring.granted_capabilities:
            reason = "Not included in your plan."
        elif definition.addon and definition.addon not in authoring.addons:
            reason = "Needs the warehouse sync add-on."
        elif definition.minimum_role == "ORGANIZATION_ADMIN" and authoring.organization_role not in ("OWNER", "ADMIN"):
            reason = "Only organization owners and admins can add this."
        actions.append(
            {
                "type": action_type,
                "label": definition.label,
                "description": definition.description,
                "category": definition.category,
                "capability": definition.capability,
                "addon": definition.addon,
                "minimum_role": definition.minimum_role,
                "available": reason is None,
                "unavailable_reason": reason,
                "commercial": action_type in action_registry.COMMERCIAL_ACTION_TYPES,
                "needs_document": action_type in ("redaction.start", "review.escalate", "autonomy.decide", "work_item.mutate"),
                "template_fields": list(definition.template_fields),
                # Legacy stored names (e.g. the ARCH-13 console's email type),
                # so the builder can open an old rule without a vocabulary.
                "aliases": list(definition.aliases),
                "config_schema": definition.schema(),
            }
        )

    resources: dict[str, Any] = {
        "webhook_endpoints": _endpoints(
            db, organization_id=authoring.organization_id, workspace_id=authoring.workspace_id
        ),
        "warehouse_destinations": (
            _destinations(db, organization_id=authoring.organization_id)
            if WAREHOUSE_SYNC_ADDON in authoring.addons
            else []
        ),
        "redaction_profiles": list(PROFILE_KEYS),
        "organization_roles": list(ROLE_VALUES),
        "export_datasets": list(EXPORT_DATASET_VALUES),
        "mutable_fields": list(MUTABLE_COLUMNS),
    }
    return {
        "triggers": triggers,
        "actions": actions,
        "operators": {k: list(v) for k, v in catalog.OPERATORS_BY_TYPE.items()},
        "valueless_operators": sorted(catalog.VALUELESS_OPERATORS),
        "template_variables": list(STATIC_VARIABLES),
        "document_fields": observed_fields(db, workspace_id=authoring.workspace_id),
        "resources": resources,
        "limits": {"triggers": 4, "groups": 10, "conditions_per_group": 20, "actions_per_branch": 10},
    }


__all__ = ["authoring_for", "build", "observed_fields"]
'''

NEW_BACKEND_FILES['app/services/automation/rule_triggers.py'] = r'''"""ARCH-37 — reading and writing `automation_rule_triggers`.

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
'''

NEW_BACKEND_FILES['app/services/automation/actions/__init__.py'] = r'''"""ARCH-37 — the action registry.

`perform_action(state, spec)` is a dictionary dispatch. Before ARCH-37 the
executor's `_default_perform_action` was an if/elif over two action types, and
`resolve_selector("webhook")` returned a selector for an action nothing could
perform, so a webhook rule failed at run time with "Unsupported action type".

`_assert_registry_complete` runs at import. Every registered action must have
a config schema, a registered R33 selector, a perform function, and a declared
capability and minimum role; every alias must name one action. A module that
forgets a part stops the process from starting, not the first customer run.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.services.automation.actions import (
    autonomy_decide,
    email_send,
    notify_role,
    redaction_start,
    review_escalate,
    warehouse_export,
    webhook_send,
    work_item_mutate,
)
from app.services.automation.actions.base import (
    MINIMUM_ROLES,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
)

_MODULES = (
    webhook_send,
    redaction_start,
    review_escalate,
    warehouse_export,
    notify_role,
    autonomy_decide,
    email_send,
    work_item_mutate,
)

ACTIONS: Mapping[str, ActionDefinition] = {
    module.DEFINITION.action_type: module.DEFINITION for module in _MODULES
}

#: The seven actions ARCH-37 sells, in catalog order. `work_item.mutate`
#: predates it and is registered alongside.
COMMERCIAL_ACTION_TYPES: tuple[str, ...] = (
    "webhook.send",
    "redaction.start",
    "review.escalate",
    "warehouse.export",
    "notify.role",
    "autonomy.decide",
    "email.send",
)

ALIASES: Mapping[str, str] = {
    alias: definition.action_type
    for definition in ACTIONS.values()
    for alias in definition.aliases
}

#: Handled by the executor itself, never by this registry.
LLM_ACTION_TYPES: frozenset[str] = frozenset({"llm.extract", "llm.classify"})


def canonical(action_type: str) -> str:
    normalised = (action_type or "").strip().lower()
    return ALIASES.get(normalised, normalised)


def get(action_type: str) -> ActionDefinition | None:
    return ACTIONS.get(canonical(action_type))


def selector_for(action_type: str) -> str | None:
    definition = get(action_type)
    return definition.selector if definition is not None else None


def perform_action(state: Any, spec: Any) -> ActionOutcome:
    definition = get(spec.action_type)
    if definition is None:
        raise ActionFailure(f"Unsupported action type '{spec.action_type}'.", recoverable=False)
    outcome = definition.perform(state, spec)
    if not isinstance(outcome, ActionOutcome):
        raise TypeError(f"{definition.action_type}.perform returned {type(outcome).__name__}")
    return outcome


def _assert_registry_complete() -> None:
    from pydantic import BaseModel

    from app.services import tools as _tools  # noqa: F401
    from app.services.fenced_context import TOOL_SELECTORS
    from app.services.tools import flow_selectors  # noqa: F401  (registers)

    problems: list[str] = []
    for action_type, definition in ACTIONS.items():
        if not (isinstance(definition.config_model, type) and issubclass(definition.config_model, BaseModel)):
            problems.append(f"{action_type}: no config schema")
        if not issubclass(definition.config_model, ActionConfig):
            problems.append(f"{action_type}: config schema does not forbid unknown keys")
        if not definition.selector or definition.selector not in TOOL_SELECTORS:
            problems.append(f"{action_type}: selector {definition.selector!r} is not registered")
        if not callable(definition.perform):
            problems.append(f"{action_type}: no perform")
        if definition.minimum_role not in MINIMUM_ROLES:
            problems.append(f"{action_type}: unknown minimum role {definition.minimum_role!r}")
        if not hasattr(definition, "capability"):
            problems.append(f"{action_type}: no capability declaration")
    missing = [a for a in COMMERCIAL_ACTION_TYPES if a not in ACTIONS]
    if missing:
        problems.append(f"commercial actions not registered: {missing}")
    clashes = [alias for alias in ALIASES if alias in ACTIONS]
    if clashes:
        problems.append(f"aliases shadow action types: {clashes}")
    if len(ALIASES) != sum(len(d.aliases) for d in ACTIONS.values()):
        problems.append("an alias is claimed by two actions")
    if problems:
        raise RuntimeError("ARCH-37 action registry is incomplete: " + "; ".join(problems))


_assert_registry_complete()


__all__ = [
    "ACTIONS",
    "ALIASES",
    "COMMERCIAL_ACTION_TYPES",
    "LLM_ACTION_TYPES",
    "ActionDefinition",
    "ActionFailure",
    "ActionOutcome",
    "canonical",
    "get",
    "perform_action",
    "selector_for",
]
'''

NEW_BACKEND_FILES['app/services/automation/actions/base.py'] = r'''"""ARCH-37 — the contract every flow-builder action implements.

One module per action type, each exporting a `DEFINITION` with four parts the
registry refuses to import without:

    config_model   a pydantic model; the save path and the run path both
                   validate the author's config against it
    selector       the name of an R33 selector in
                   app/services/tools/flow_selectors.py
    perform        perform(state, spec) -> ActionOutcome
    capability / minimum_role
                   what the tenant must hold to run it and who may author it

`perform` receives the executor's `_WalkState` and the selector's
`ActionSpec`. It never reads the node config directly: the spec is the only
path from the author to the effect, and the selector is what checked it.
"""

from __future__ import annotations

import html
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional

from pydantic import BaseModel, ConfigDict, ValidationError

#: Who may put an action into a rule.
ROLE_WORKSPACE_ADMIN = "WORKSPACE_ADMIN"
ROLE_ORGANIZATION_ADMIN = "ORGANIZATION_ADMIN"
MINIMUM_ROLES = frozenset({ROLE_WORKSPACE_ADMIN, ROLE_ORGANIZATION_ADMIN})


class ActionFailure(RuntimeError):
    """An action could not run. Mirrors automation_service.ActionFailure."""

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class ActionConfig(BaseModel):
    """Base for every action config. Unknown keys are refused."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


@dataclass
class ActionOutcome:
    summary: str
    external_ref: Optional[str] = None
    #: False stops every node after this one: `autonomy.decide` holding a
    #: document is the branch on outcome the builder offers.
    continue_downstream: bool = True
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # executors that expect a string still get one
        return self.summary


@dataclass(frozen=True)
class SaveContext:
    """What save-time validation may consult."""

    db: Any
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    trigger_keys: tuple[str, ...]


@dataclass(frozen=True)
class ActionDefinition:
    action_type: str
    label: str
    description: str
    category: str
    config_model: type[ActionConfig]
    selector: str
    perform: Callable[[Any, Any], ActionOutcome]
    capability: Optional[str]
    minimum_role: str
    addon: Optional[str] = None
    aliases: tuple[str, ...] = ()
    #: Config keys that hold templates. Their variables are checked on save.
    template_fields: tuple[str, ...] = ()
    #: Extra save-time checks that need the database (ownership of an
    #: endpoint, a destination). Returns {config_key: message}.
    validate_resources: Optional[Callable[[SaveContext, ActionConfig], dict[str, str]]] = None
    #: True when `perform` has an external effect a test run must not cause.
    side_effect: bool = True

    def parse(self, values: Mapping[str, Any]) -> ActionConfig:
        return self.config_model.model_validate(dict(values))

    def parse_spec(self, spec: Any) -> ActionConfig:
        try:
            return self.parse(spec.parameter_dict())
        except ValidationError as exc:
            raise ActionFailure(
                f"{self.action_type} config is invalid: "
                + "; ".join(
                    f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()
                ),
                recoverable=False,
            ) from exc

    def schema(self) -> dict[str, Any]:
        return self.config_model.model_json_schema()


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_.]+)\s*\}\}")
_SAFE_FIELD = re.compile(r"^field\.[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$")
_SAFE_EVENT = re.compile(r"^event\.[a-z0-9_]{1,64}$")
MAX_VARIABLE_CHARS = 256

STATIC_VARIABLES: tuple[str, ...] = (
    "document.filename",
    "document.status",
    "document.classification",
    "rule.name",
    "trigger.label",
)


def template_variables(template: str) -> list[str]:
    return _VARIABLE.findall(template or "")


def invalid_variables(template: str, *, event_keys: set[str]) -> list[str]:
    """Variables a template may not use: not static, not the selected
    triggers' event fields, and not a well-formed document field path."""
    bad: list[str] = []
    for name in template_variables(template):
        if name in STATIC_VARIABLES:
            continue
        if _SAFE_EVENT.match(name) and name in event_keys:
            continue
        if _SAFE_FIELD.match(name):
            continue
        bad.append(name)
    return bad


def _scalar(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    text = str(value)
    return text[:MAX_VARIABLE_CHARS]


class TemplateValues(dict):
    """Resolved variables. `field.<path>` is read from the document on demand."""

    def __init__(self, values: Mapping[str, str], entities: Mapping[str, Any]) -> None:
        super().__init__(values)
        self._entities = entities

    def __missing__(self, key: str) -> str:
        if not _SAFE_FIELD.match(key):
            return ""
        current: Any = self._entities
        for part in key[len("field."):].split("."):
            if not isinstance(current, Mapping):
                return ""
            current = current.get(part)
        return html.escape(_scalar(current), quote=True)


def variables_for(state: Any) -> TemplateValues:
    """Runtime values. Scalars only, truncated, and HTML-escaped."""
    from app.services.automation.triggers import TRIGGER_BY_EVENT

    work_item = getattr(state, "work_item", None)
    event = getattr(state, "trigger_event", None)
    payload = dict(getattr(event, "payload", None) or {})
    entities = dict(getattr(work_item, "extracted_entities", None) or {}) if work_item else {}
    spec = TRIGGER_BY_EVENT.get(getattr(event, "event_type", "") or "")
    classification = entities.get("classification_details")
    status = getattr(work_item, "status", None)

    values: dict[str, str] = {
        "document.filename": _scalar(getattr(work_item, "original_filename", None)),
        "document.status": _scalar(getattr(status, "value", status)),
        "document.classification": _scalar(
            classification.get("document_classification")
            if isinstance(classification, Mapping)
            else None
        ),
        "rule.name": _scalar(getattr(getattr(state, "rule", None), "name", None)),
        "trigger.label": spec.label if spec else "",
    }
    for key, value in payload.items():
        values[f"event.{key}"] = _scalar(value)
    escaped = {k: html.escape(v, quote=True) for k, v in values.items()}
    return TemplateValues(escaped, entities)


def render(template: str, values: Mapping[str, str]) -> str:
    return _VARIABLE.sub(lambda m: values[m.group(1)] if isinstance(values, TemplateValues) else values.get(m.group(1), ""), template or "")


def require_work_item(state: Any, action_type: str) -> Any:
    work_item = getattr(state, "work_item", None)
    if work_item is None:
        raise ActionFailure(f"{action_type} needs a document, and this trigger has none.", recoverable=False)
    return work_item


def has_capability(state: Any, capability: Optional[str]) -> bool:
    if capability is None:
        return True
    from app.api import capability_gate

    return capability_gate.has_capability(
        state.db,
        organization_id=state.execution.organization_id,
        capability_key=capability,
    )


__all__ = [
    "ActionConfig",
    "ActionDefinition",
    "ActionFailure",
    "ActionOutcome",
    "MINIMUM_ROLES",
    "ROLE_ORGANIZATION_ADMIN",
    "ROLE_WORKSPACE_ADMIN",
    "STATIC_VARIABLES",
    "SaveContext",
    "TemplateValues",
    "has_capability",
    "invalid_variables",
    "render",
    "require_work_item",
    "template_variables",
    "variables_for",
]
'''

NEW_BACKEND_FILES['app/services/automation/actions/webhook_send.py'] = r'''"""ARCH-37 action `webhook.send` — post to one registered webhook endpoint.

GUARDRAIL: there is no URL in this action's config. A free-form URL in a
tenant-authored rule is a server-side request forgery primitive, so the rule
names a `webhook_endpoints` row, and that row must be ACTIVE, in the rule's
organization, and either organization-wide or scoped to the rule's workspace.
The check runs at save time and again at run time, because an endpoint can be
disabled or re-scoped between the two.

Delivery reuses ARCH-09 end to end: the action writes one `webhook_deliveries`
row, and the delivery loop signs it (HMAC, rotating secrets), sends it through
the SSRF-safe client, and retries with backoff.
"""

from __future__ import annotations

import re
import uuid
from typing import Any, Optional

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    SaveContext,
)

ACTION_TYPE = "webhook.send"
EVENT_TYPE = "workflow.triggered"
FIELD_PATTERN = r"^[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$"
MAX_FIELD_CHARS = 512


class WebhookSendConfig(ActionConfig):
    endpoint_id: uuid.UUID = Field(description="A registered, active webhook endpoint.")
    include_fields: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="Extracted document fields to include, by path. Scalars only.",
    )

    @field_validator("include_fields")
    @classmethod
    def _paths(cls, value: list[str]) -> list[str]:
        bad = [f for f in value if not re.match(FIELD_PATTERN, f)]
        if bad:
            raise ValueError(f"include_fields has invalid paths: {bad}")
        if len(set(value)) != len(value):
            raise ValueError("include_fields must not repeat.")
        return value


def load_endpoint(
    db: Any,
    *,
    endpoint_id: uuid.UUID,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> tuple[Optional[Any], Optional[str]]:
    """The endpoint if this workspace may target it, else a refusal reason."""
    from sqlalchemy import select

    from app.models.webhook_endpoint import WebhookEndpoint, WebhookEndpointStatus

    endpoint = db.execute(
        select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id)
    ).scalar_one_or_none()
    if endpoint is None or endpoint.organization_id != organization_id:
        # Same message for "absent" and "another organization's": the
        # difference is not the author's to learn.
        return None, "Webhook endpoint not found in this organization."
    if endpoint.workspace_id is not None and endpoint.workspace_id != workspace_id:
        return None, "This webhook endpoint is scoped to a different workspace."
    if endpoint.status is not WebhookEndpointStatus.ACTIVE:
        return None, "This webhook endpoint is disabled."
    return endpoint, None


def _validate(ctx: SaveContext, config: ActionConfig) -> dict[str, str]:
    assert isinstance(config, WebhookSendConfig)
    _, reason = load_endpoint(
        ctx.db,
        endpoint_id=config.endpoint_id,
        organization_id=ctx.organization_id,
        workspace_id=ctx.workspace_id,
    )
    return {"endpoint_id": reason} if reason else {}


def _scalar(value: Any) -> Any:
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    if isinstance(value, str):
        return value[:MAX_FIELD_CHARS]
    return None


def build_payload(state: Any, config: WebhookSendConfig) -> dict[str, Any]:
    from app.services.automation.triggers import TRIGGER_BY_EVENT

    event = state.trigger_event
    event_type = getattr(event, "event_type", None)
    spec = TRIGGER_BY_EVENT.get(event_type or "")
    event_payload = dict(getattr(event, "payload", None) or {})
    work_item = state.work_item

    fields: dict[str, Any] = {}
    entities = dict(getattr(work_item, "extracted_entities", None) or {}) if work_item else {}
    for path in config.include_fields:
        current: Any = entities
        for part in path.split("."):
            current = current.get(part) if isinstance(current, dict) else None
        fields[path] = _scalar(current)

    return {
        "rule": {"id": str(state.rule.id), "name": state.rule.name},
        "execution_id": str(state.execution.id),
        "workspace_id": str(state.execution.workspace_id),
        "trigger": {
            "key": spec.key if spec else None,
            "label": spec.label if spec else None,
            "event_id": str(event.id) if event is not None else None,
        },
        "work_item": (
            {
                "id": str(work_item.id),
                "original_filename": work_item.original_filename,
                "status": getattr(work_item.status, "value", work_item.status),
            }
            if work_item is not None
            else None
        ),
        # Only the fields the catalog declares for this trigger. A dispute
        # reason or a finding headline is free text and stays inside.
        "event": {
            f.key: _scalar(event_payload.get(f.key))
            for f in (spec.fields if spec else ())
            if f.key not in ("failure_reason",)
        },
        "fields": fields,
    }


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.models.webhook_delivery import WebhookDelivery, WebhookDeliveryStatus

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WebhookSendConfig)
    endpoint, reason = load_endpoint(
        state.db,
        endpoint_id=config.endpoint_id,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
    )
    if endpoint is None:
        raise ActionFailure(reason or "Webhook endpoint unavailable.", recoverable=False)

    delivery = WebhookDelivery(
        webhook_endpoint_id=endpoint.id,
        outbox_event_id=None,
        organization_id=state.execution.organization_id,
        event_type=EVENT_TYPE,
        payload=build_payload(state, config),
        status=WebhookDeliveryStatus.PENDING,
    )
    state.db.add(delivery)
    state.db.flush([delivery])
    return ActionOutcome(
        summary=f"webhook queued for endpoint {endpoint.id}",
        external_ref=str(delivery.id),
        details={"endpoint_id": str(endpoint.id)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send to webhook",
    description="Post a signed event to one of your organization's registered webhook endpoints.",
    category="Integrations",
    config_model=WebhookSendConfig,
    selector="automation.flow.webhook_send",
    perform=perform,
    capability=None,
    minimum_role=ROLE_ORGANIZATION_ADMIN,
    aliases=("webhook",),
    validate_resources=_validate,
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/redaction_start.py'] = r'''"""ARCH-37 action `redaction.start` — open an ARCH-32 redaction job.

GUARDRAILS
  * capability.redaction, checked when the rule runs, not only when saved.
  * PDF only; `redaction_service.start_job` refuses anything else.
  * The job is left in REVIEW for a human. This action never approves or
    applies a redaction: burning pixels out of a contract is not something a
    rule decides on its own.
  * One open job per document. A second trigger while a job is still in
    detection or review returns that job instead of starting another.
  * Refused when the rule was triggered by `redaction.completed`, which would
    otherwise start a new job every time the previous one was published.
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from app.core.entitlements import REDACTION_CAPABILITY
from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    has_capability,
    require_work_item,
)

ACTION_TYPE = "redaction.start"
OPEN_STATUSES = ("DETECTING", "REVIEW", "APPLYING")


class RedactionStartConfig(ActionConfig):
    profile_key: str = Field(description="Redaction profile.")

    @field_validator("profile_key")
    @classmethod
    def _known(cls, value: str) -> str:
        from app.services.redaction.vocabulary import PROFILE_KEYS

        if value not in PROFILE_KEYS:
            raise ValueError(f"Unknown redaction profile {value!r}.")
        return value


def perform(state: Any, spec: Any) -> ActionOutcome:
    from sqlalchemy import select

    from app.models.redaction import RedactionJob
    from app.services.redaction import redaction_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, RedactionStartConfig)
    if getattr(state.trigger_event, "event_type", None) == "trigger.redaction.completed":
        raise ActionFailure(
            "A redaction cannot be started by a redaction finishing.", recoverable=False
        )
    if not has_capability(state, REDACTION_CAPABILITY):
        raise ActionFailure("This plan does not include redaction.", recoverable=False)
    work_item = require_work_item(state, ACTION_TYPE)
    author = state.rule.created_by_user_id
    if author is None:
        raise ActionFailure(
            "A redaction job needs an owner, and this rule has no author.", recoverable=False
        )

    existing = state.db.execute(
        select(RedactionJob.id).where(
            RedactionJob.work_item_id == work_item.id,
            RedactionJob.workspace_id == state.execution.workspace_id,
            RedactionJob.status.in_(OPEN_STATUSES),
        ).limit(1)
    ).scalar_one_or_none()
    if existing is not None:
        return ActionOutcome(
            summary="redaction already open for this document",
            external_ref=str(existing),
            details={"reused": True},
        )

    try:
        job = redaction_service.start_job(
            state.db,
            organization_id=state.execution.organization_id,
            workspace_id=state.execution.workspace_id,
            work_item_id=work_item.id,
            user_id=author,
            profile_key=config.profile_key,
        )
    except redaction_service.RedactionError as exc:
        raise ActionFailure(str(exc), recoverable=False) from exc
    return ActionOutcome(
        summary=f"redaction started ({config.profile_key}); awaiting review",
        external_ref=str(job.id),
        details={"profile": config.profile_key},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Redact PII",
    description="Detect personal data in the PDF and open a redaction for a reviewer to approve.",
    category="Document intelligence",
    config_model=RedactionStartConfig,
    selector="automation.flow.redaction_start",
    perform=perform,
    capability=REDACTION_CAPABILITY,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/review_escalate.py'] = r'''"""ARCH-37 action `review.escalate` — put the document in the review queue.

Writes a DISAGREED `document_verifications` row, which is what the review
queue lists and what the automation handler treats as blocking: later
triggers on this document wait for the reviewer.

Every extracted field is attached, agreed, so the reviewer confirms the
document rather than one field. The row is marked
`escalation.review_all_fields`, which `document_verification_service.resolve`
honours. It deliberately does NOT use ARCH-35's `calibration.review_all_fields`:
the calibration harvester learns from rows carrying that key, and a rule's
escalation is not a calibration audit.

One open verification per document is a unique index, so a repeated escalation
returns the open row.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import Field

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionOutcome,
    require_work_item,
)

ACTION_TYPE = "review.escalate"
MAX_FIELDS = 50


class ReviewEscalateConfig(ActionConfig):
    reason: str = Field(
        default="Sent to review by an automation rule.",
        min_length=1,
        max_length=240,
    )


def escalate(state: Any, *, reason: str, source: str) -> ActionOutcome:
    from sqlalchemy import select

    from app.models.verification import (
        BLOCKING_STATUSES,
        DocumentVerification,
        DocumentVerificationField,
        VerificationStatus,
    )
    from app.services.document_verification_service import EXCLUDED_FIELDS

    work_item = require_work_item(state, ACTION_TYPE)
    open_row = state.db.execute(
        select(DocumentVerification.id).where(
            DocumentVerification.work_item_id == work_item.id,
            DocumentVerification.status.in_(BLOCKING_STATUSES),
        ).limit(1)
    ).scalar_one_or_none()
    if open_row is not None:
        return ActionOutcome(
            summary="document already awaiting review",
            external_ref=str(open_row),
            details={"reused": True},
        )

    verification = DocumentVerification(
        work_item_id=work_item.id,
        workspace_id=state.execution.workspace_id,
        organization_id=state.execution.organization_id,
        status=VerificationStatus.DISAGREED,
        agent_count=2,
        agreement_score=Decimal("0"),
        confidence=None,
        cost_micros=0,
        auto_approved=False,
        details={
            "escalation": {
                "source": source,
                "reason": reason,
                "rule_id": str(state.rule.id),
                "execution_id": str(state.execution.id),
                "trigger_event_id": (
                    str(state.trigger_event.id) if state.trigger_event is not None else None
                ),
                # The fact contract carries keys and a digest, never values.
                "evidence": state.facts.as_details(),
                "review_all_fields": True,
            }
        },
    )
    state.db.add(verification)
    state.db.flush([verification])

    entities = dict(work_item.extracted_entities or {})
    written = 0
    for key in sorted(entities):
        if key in EXCLUDED_FIELDS or written >= MAX_FIELDS:
            continue
        value = entities[key]
        state.db.add(
            DocumentVerificationField(
                verification_id=verification.id,
                field_path=str(key)[:200],
                agreed=True,
                confidence=Decimal("1"),
                consensus_value=value,
                agent_values=[value],
                disagreement_kind=None,
            )
        )
        written += 1
    state.db.flush()
    return ActionOutcome(
        summary=f"sent to review ({written} fields)",
        external_ref=str(verification.id),
        details={"fields": written},
    )


def perform(state: Any, spec: Any) -> ActionOutcome:
    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, ReviewEscalateConfig)
    return escalate(state, reason=config.reason, source=ACTION_TYPE)


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send to review",
    description="Hold the document for a reviewer. Later rules on it wait for the decision.",
    category="Human review",
    config_model=ReviewEscalateConfig,
    selector="automation.flow.review_escalate",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/warehouse_export.py'] = r'''"""ARCH-37 action `warehouse.export` — run an ARCH-26 export now.

Enqueues `analytics.export_sync`, the job the "Sync now" button enqueues, so
the push runs on the worker and never blocks the automation.

GUARDRAILS
  * The destination is an OWNER-registered `warehouse_destinations` row in
    the rule's organization (the destination API is OWNER-only), and it must
    be active when the rule runs.
  * The warehouse-sync add-on must be maintainable (active or in grace), the
    same policy the manual sync applies.
  * Authoring needs an organization OWNER or ADMIN: the datasets are
    organization-wide.
  * Debounced per destination. The idempotency key is the destination and a
    time bucket, so a burst of documents produces one export per window.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from pydantic import Field, field_validator

from app.core.entitlements import WAREHOUSE_SYNC_ADDON
from app.services.automation.actions.base import (
    ROLE_ORGANIZATION_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    SaveContext,
)

ACTION_TYPE = "warehouse.export"
JOB_TYPE = "analytics.export_sync"


class WarehouseExportConfig(ActionConfig):
    destination_id: uuid.UUID
    datasets: list[str] = Field(min_length=1, max_length=8)
    lookback_days: int = Field(default=1, ge=1, le=90)
    debounce_minutes: int = Field(default=60, ge=5, le=1440)

    @field_validator("datasets")
    @classmethod
    def _known(cls, value: list[str]) -> list[str]:
        from app.models.warehouse_sync import EXPORT_DATASET_VALUES

        unknown = sorted(set(value) - set(EXPORT_DATASET_VALUES))
        if unknown:
            raise ValueError(f"Unknown dataset(s): {unknown}.")
        if len(set(value)) != len(value):
            raise ValueError("datasets must not repeat.")
        return value


def _destination(db: Any, *, organization_id: uuid.UUID, destination_id: uuid.UUID) -> tuple[Any, str | None]:
    from app.services.analytics import sync_service

    try:
        destination = sync_service.get_destination(
            db, organization_id=organization_id, destination_id=destination_id
        )
    except sync_service.DestinationNotFoundError:
        return None, "Warehouse destination not found in this organization."
    if not destination.is_active:
        return None, "This warehouse destination is disabled."
    return destination, None


def _validate(ctx: SaveContext, config: ActionConfig) -> dict[str, str]:
    assert isinstance(config, WarehouseExportConfig)
    _, reason = _destination(
        ctx.db, organization_id=ctx.organization_id, destination_id=config.destination_id
    )
    return {"destination_id": reason} if reason else {}


def debounce_key(destination_id: uuid.UUID, minutes: int, *, now: float | None = None) -> str:
    bucket = int((now if now is not None else time.time()) // (minutes * 60))
    return f"automation:warehouse:{destination_id}:{minutes}:{bucket}"


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services import job_service
    from app.services.billing import entitlement_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WarehouseExportConfig)
    organization_id = state.execution.organization_id

    access = entitlement_service.addon_access(
        state.db, organization_id=organization_id, addon_key=WAREHOUSE_SYNC_ADDON
    )
    if not access.can_maintain:
        raise ActionFailure("The warehouse sync add-on is not active.", recoverable=False)
    destination, reason = _destination(
        state.db, organization_id=organization_id, destination_id=config.destination_id
    )
    if destination is None:
        raise ActionFailure(reason or "Destination unavailable.", recoverable=False)

    job = job_service.enqueue(
        state.db,
        job_type=JOB_TYPE,
        organization_id=organization_id,
        payload={
            "organization_id": str(organization_id),
            "destination_id": str(destination.id),
            "datasets": list(config.datasets),
            "lookback_days": int(config.lookback_days),
            "trigger": "MANUAL",
        },
        max_attempts=1,
        idempotency_key=debounce_key(destination.id, config.debounce_minutes),
    )
    return ActionOutcome(
        summary=f"export queued to {destination.label}",
        external_ref=str(job.id),
        details={"destination_id": str(destination.id), "datasets": list(config.datasets)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Export to warehouse",
    description="Push the selected datasets to a registered warehouse destination, at most once per window.",
    category="Integrations",
    config_model=WarehouseExportConfig,
    selector="automation.flow.warehouse_export",
    perform=perform,
    capability=None,
    addon=WAREHOUSE_SYNC_ADDON,
    minimum_role=ROLE_ORGANIZATION_ADMIN,
    validate_resources=_validate,
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/notify_role.py'] = r'''"""ARCH-37 action `notify.role` — tell everyone holding an organization role.

Recipients are resolved when the rule runs, from active organization
memberships, so a rule written a year ago reaches today's Finance team.

Who is reachable: OWNER and ADMIN hold implicit ADMIN on every workspace
(`organization_permissions.IMPLICIT_WORKSPACE_ADMIN_ROLES`). BILLING and
MEMBER do not, so they are notified only if they are active members of this
workspace — otherwise the notification would link to a page they cannot open.

Delivery is `notification.outbox_dispatcher.dispatch`: in-app immediately,
email for WARNING and ERROR, content passed through the stream's output
filter before it is stored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    render,
    variables_for,
)

ACTION_TYPE = "notify.role"
ROLE_VALUES = ("OWNER", "ADMIN", "BILLING", "MEMBER")
MAX_RECIPIENTS = 200


class NotifyRoleConfig(ActionConfig):
    roles: list[str] = Field(min_length=1, max_length=4)
    title: str = Field(default="{{rule.name}}: {{document.filename}}", min_length=1, max_length=150)
    message: str = Field(
        default="{{trigger.label}} — {{document.filename}}", min_length=1, max_length=500
    )
    priority: Literal["INFO", "SUCCESS", "WARNING", "ERROR"] = "INFO"

    @field_validator("roles")
    @classmethod
    def _roles(cls, value: list[str]) -> list[str]:
        normalised = [v.upper() for v in value]
        unknown = sorted(set(normalised) - set(ROLE_VALUES))
        if unknown:
            raise ValueError(f"Unknown organization role(s): {unknown}.")
        if len(set(normalised)) != len(normalised):
            raise ValueError("roles must not repeat.")
        return normalised


def recipients(db: Any, *, organization_id: Any, workspace_id: Any, roles: list[str]) -> list[Any]:
    from sqlalchemy import select

    from app.core.organization_permissions import IMPLICIT_WORKSPACE_ADMIN_ROLES
    from app.models.organization import MembershipStatus, OrganizationMember, OrganizationRole
    from app.models.user import User
    from app.models.workspace import WorkspaceMember

    wanted = [OrganizationRole(role) for role in roles]
    rows = db.execute(
        select(User, OrganizationMember.role)
        .join(OrganizationMember, OrganizationMember.user_id == User.id)
        .where(
            OrganizationMember.organization_id == organization_id,
            OrganizationMember.status == MembershipStatus.ACTIVE,
            OrganizationMember.role.in_(wanted),
        )
        .order_by(User.id)
        .limit(MAX_RECIPIENTS)
    ).all()
    implicit = set(IMPLICIT_WORKSPACE_ADMIN_ROLES)
    needs_membership = [user.id for user, role in rows if role not in implicit]
    members: set[Any] = set()
    if needs_membership:
        members = set(
            db.execute(
                select(WorkspaceMember.user_id).where(
                    WorkspaceMember.workspace_id == workspace_id,
                    WorkspaceMember.status == MembershipStatus.ACTIVE,
                    WorkspaceMember.user_id.in_(needs_membership),
                )
            ).scalars()
        )
    return [user for user, role in rows if role in implicit or user.id in members]


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.models.notification import (
        Notification,
        NotificationChannel,
        NotificationPriority,
        NotificationStatus,
        NotificationType,
    )
    from app.services.notification import outbox_dispatcher

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, NotifyRoleConfig)
    users = recipients(
        state.db,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
        roles=config.roles,
    )
    if not users:
        raise ActionFailure(
            f"Nobody in this workspace holds {', '.join(config.roles)}.", recoverable=True
        )

    values = variables_for(state)
    title = render(config.title, values)[:150] or state.rule.name[:150]
    message = render(config.message, values)[:500] or title
    work_item_id = state.work_item.id if state.work_item is not None else None

    for user in users:
        notification = Notification(
            workspace_id=state.execution.workspace_id,
            user_id=user.id,
            work_item_id=work_item_id,
            title=title,
            message=message,
            notification_type=NotificationType.AUTOMATION,
            priority=NotificationPriority(config.priority),
            delivery_channel=NotificationChannel.IN_APP,
            delivery_status=NotificationStatus.PENDING,
            retry_count=0,
            is_read=False,
        )
        state.db.add(notification)
        state.db.flush([notification])
        outbox_dispatcher.dispatch(
            state.db,
            notification=notification,
            user=user,
            organization_id=state.execution.organization_id,
            workspace_id=state.execution.workspace_id,
            idempotency_prefix=f"automation:{state.execution.id}",
        )
    return ActionOutcome(
        summary=f"notified {len(users)} {'person' if len(users) == 1 else 'people'}",
        external_ref=f"recipients:{len(users)}",
        details={"roles": list(config.roles), "recipients": len(users)},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Notify a role",
    description="Send an in-app notification (and email for warnings) to everyone holding a role.",
    category="Notifications",
    config_model=NotifyRoleConfig,
    selector="automation.flow.notify_role",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    template_fields=("title", "message"),
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/autonomy_decide.py'] = r'''"""ARCH-37 action `autonomy.decide` — ask ARCH-35 whether to proceed.

Calls `calibration.apply.decide` for `verification.document`, which honours
the tenant's calibrated model, its suspension and its audit sampling. The
action never grants autonomy a tenant has not bought: without
capability.calibrated_autonomy `decide` returns None and the action fails.

THE BRANCH ON OUTCOME
=====================

Allowed: the actions after this one run.
Held (below threshold, suspended, audit-sampled, or no score): every action
after this one is skipped, and with `on_hold = ESCALATE` the document goes to
the review queue first. That is what "only push to the ERP when the platform
is confident" means in a rule.
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import Field

from app.core.entitlements import CALIBRATED_AUTONOMY_CAPABILITY
from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    has_capability,
    require_work_item,
)

ACTION_TYPE = "autonomy.decide"
DECISION_TYPE = "verification.document"


class AutonomyDecideConfig(ActionConfig):
    score_source: Literal[
        "VERIFICATION_CONFIDENCE", "CLASSIFICATION_CONFIDENCE", "EVENT_SCORE"
    ] = "VERIFICATION_CONFIDENCE"
    on_hold: Literal["ESCALATE", "STOP"] = "ESCALATE"
    hold_reason: str = Field(
        default="Held by calibrated autonomy.", min_length=1, max_length=240
    )


def _score(state: Any, source: str) -> Optional[Any]:
    from sqlalchemy import select

    work_item = state.work_item
    if source == "EVENT_SCORE":
        return (getattr(state.trigger_event, "payload", None) or {}).get("score")
    if source == "CLASSIFICATION_CONFIDENCE":
        details = (work_item.extracted_entities or {}).get("classification_details")
        return details.get("confidence") if isinstance(details, dict) else None

    from app.models.verification import DocumentVerification

    return state.db.execute(
        select(DocumentVerification.confidence)
        .where(DocumentVerification.work_item_id == work_item.id)
        .order_by(DocumentVerification.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services.calibration import apply as calibrated_autonomy
    from app.services.automation.actions.review_escalate import escalate

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, AutonomyDecideConfig)
    if not has_capability(state, CALIBRATED_AUTONOMY_CAPABILITY):
        raise ActionFailure("This plan does not include calibrated autonomy.", recoverable=False)
    work_item = require_work_item(state, ACTION_TYPE)

    score = _score(state, config.score_source)
    decision = None
    if score is not None:
        decision = calibrated_autonomy.decide(
            state.db,
            organization_id=state.execution.organization_id,
            decision_type=DECISION_TYPE,
            raw_score=score,
            sample_key=f"{state.execution.id}:{work_item.id}",
            check_capability=False,
        )
    allowed = bool(decision is not None and decision.auto_allowed)
    details: dict[str, Any] = {
        "score_source": config.score_source,
        "has_score": score is not None,
        "decision": decision.as_details() if decision is not None else None,
    }
    if allowed:
        return ActionOutcome(
            summary="calibrated autonomy allowed the document",
            external_ref=(decision.model_id if decision and decision.model_id else None),
            details=details,
        )

    reason = config.hold_reason
    if decision is not None and decision.reason:
        reason = f"{reason} ({decision.reason})"[:240]
    elif score is None:
        reason = f"{reason} (no score available)"[:240]
    ref = decision.model_id if decision and decision.model_id else None
    if config.on_hold == "ESCALATE":
        escalated = escalate(state, reason=reason, source=ACTION_TYPE)
        ref = escalated.external_ref
        details["escalated"] = True
    return ActionOutcome(
        summary="held by calibrated autonomy; later actions skipped",
        external_ref=ref,
        continue_downstream=False,
        details=details,
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Decide with calibrated autonomy",
    description=(
        "Continue only when the calibrated model allows the document; "
        "otherwise stop, and optionally send it to review."
    ),
    category="Human review",
    config_model=AutonomyDecideConfig,
    selector="automation.flow.autonomy_decide",
    perform=perform,
    capability=CALIBRATED_AUTONOMY_CAPABILITY,
    minimum_role=ROLE_WORKSPACE_ADMIN,
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/email_send.py'] = r'''"""ARCH-37 action `email.send` — the pre-ARCH-37 email action, templated.

The delivery path is unchanged: the workspace's automation email settings and
`notification_dispatcher.send`. What is new is the subject and body, which are
templates over an allowlist of variables (document, rule, trigger, the
trigger's declared event fields, and scalar document fields). Every value is
HTML-escaped and truncated before it is substituted; an unknown variable is
refused when the rule is saved.

`email` and `send_email` (and the console's old `SEND_EMAIL`) are aliases, so
every rule written before ARCH-37 keeps running through this module.
"""

from __future__ import annotations

from typing import Any

from pydantic import EmailStr, Field

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    render,
    variables_for,
)

ACTION_TYPE = "email.send"
DEFAULT_SUBJECT = "Automation Rule Triggered: {{rule.name}}"
DEFAULT_BODY = (
    "Document: {{document.filename}}\n"
    "Rule: {{rule.name}}\n"
    "Trigger: {{trigger.label}}\n"
)


class EmailSendConfig(ActionConfig):
    recipient: EmailStr
    subject: str = Field(default=DEFAULT_SUBJECT, min_length=1, max_length=150)
    body: str = Field(default=DEFAULT_BODY, min_length=1, max_length=4000)


def perform(state: Any, spec: Any) -> ActionOutcome:
    import asyncio

    from app.services.automation_service import _LazyEmailSettings, _render_action_message
    from app.services.notification.dispatcher import notification_dispatcher

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, EmailSendConfig)
    settings = _LazyEmailSettings(db=state.db, workspace_id=state.execution.workspace_id).require()

    legacy = spec.action_type in ("email", "send_email") and not dict(spec.parameters).get("body")
    if legacy and state.work_item is not None:
        # Byte-for-byte the message a pre-ARCH-37 rule sent.
        title, body = _render_action_message(rule=state.rule, work_item=state.work_item)
    else:
        values = variables_for(state)
        title = render(config.subject, values)[:150] or state.rule.name
        body = render(config.body, values)

    ok = asyncio.run(
        notification_dispatcher.send(
            action_type="email",
            settings=settings,
            recipient=str(config.recipient),
            title=title,
            body=body,
        )
    )
    if not ok:
        raise ActionFailure("The mail provider reported a delivery failure.")
    return ActionOutcome(summary=f"email -> {config.recipient}", details={"channel": "email"})


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Send email",
    description="Send an email through the workspace's automation email settings.",
    category="Notifications",
    config_model=EmailSendConfig,
    selector="automation.flow.email_send",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    aliases=("email", "send_email"),
    template_fields=("subject", "body"),
)
'''

NEW_BACKEND_FILES['app/services/automation/actions/work_item_mutate.py'] = r'''"""ARCH-37 action `work_item.mutate` — set a field on the document.

The ARCH-13 behaviour, with two corrections:

  * The field is from an allowlist. Before ARCH-37 any attribute except four
    could be set, including `status` and `pipeline_stage`, which let a rule
    move a document through the pipeline state machine without a transition.
    Allowed now: `summary`, `original_filename`, and one extracted entity at
    `extracted_entities.<key>`.
  * The `work_item.field_changed` event it emits now carries its
    `automation.execute` job. It was emitted with no job, so a rule chained on
    another rule's mutation never ran.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import Field, field_validator

from app.services.automation.actions.base import (
    ROLE_WORKSPACE_ADMIN,
    ActionConfig,
    ActionDefinition,
    ActionFailure,
    ActionOutcome,
    require_work_item,
)

ACTION_TYPE = "work_item.mutate"
MUTABLE_COLUMNS = ("summary", "original_filename")
ENTITY_PREFIX = "extracted_entities."
_ENTITY_KEY = re.compile(r"^extracted_entities\.[a-z0-9_]{1,64}$")


class WorkItemMutateConfig(ActionConfig):
    target_field: str = Field(min_length=1, max_length=100)
    target_value: str = Field(default="", max_length=1000)

    @field_validator("target_field")
    @classmethod
    def _allowed(cls, value: str) -> str:
        if value in MUTABLE_COLUMNS or _ENTITY_KEY.match(value):
            return value
        raise ValueError(
            f"{value!r} cannot be set by a rule. Allowed: "
            f"{', '.join(MUTABLE_COLUMNS)}, or extracted_entities.<key>."
        )


def perform(state: Any, spec: Any) -> ActionOutcome:
    from app.services import outbox_service

    config = DEFINITION.parse_spec(spec)
    assert isinstance(config, WorkItemMutateConfig)
    work_item = require_work_item(state, ACTION_TYPE)

    if config.target_field.startswith(ENTITY_PREFIX):
        key = config.target_field[len(ENTITY_PREFIX):]
        entities = dict(work_item.extracted_entities or {})
        entities[key] = config.target_value
        work_item.extracted_entities = entities
    elif config.target_field == "original_filename":
        if not config.target_value.strip():
            raise ActionFailure("A file name cannot be empty.", recoverable=False)
        work_item.original_filename = config.target_value[:255]
    else:
        work_item.summary = config.target_value
    state.db.flush([work_item])

    event = outbox_service.emit_trigger(
        state.db,
        organization_id=state.execution.organization_id,
        workspace_id=state.execution.workspace_id,
        event_type="work_item.field_changed",
        resource_id=work_item.id,
        payload={
            "work_item_id": str(work_item.id),
            "field": config.target_field,
            "rule_id": str(state.rule.id),
            "execution_id": str(state.execution.id),
        },
        caused_by=state.trigger_event,
    )
    if event is not None:
        state.emitted_event_ids.append(str(event.id))
    return ActionOutcome(
        summary=f"set_field {config.target_field}",
        external_ref=str(event.id) if event is not None else None,
        details={"field": config.target_field},
    )


DEFINITION = ActionDefinition(
    action_type=ACTION_TYPE,
    label="Set a document field",
    description="Write a value to the document's summary, file name or an extracted field.",
    category="Documents",
    config_model=WorkItemMutateConfig,
    selector="automation.flow.work_item_mutate",
    perform=perform,
    capability=None,
    minimum_role=ROLE_WORKSPACE_ADMIN,
    aliases=("set_field",),
    side_effect=True,
)
'''

NEW_BACKEND_FILES['app/services/tools/flow_selectors.py'] = r'''"""ARCH-37 — one R33 selector per flow-builder action type.

Each selector copies the author's configuration into an `ActionSpec` and then
proves, with `assert_no_document_derived_values`, that nothing in the spec came
from a document. The facts are passed in only so a selector can record which
keys existed (`rationale`); their values never reach the spec.

The action registry (`app/services/automation/actions`) names these selectors
by string and refuses to import if one is missing, so an action cannot be
registered without passing through this boundary.
"""

from __future__ import annotations

import logging

from app.services.automation.contracts import (
    ActionNodeConfig,
    ActionSpec,
    FactSet,
    TenantScope,
    register_tool_selector,
)

logger = logging.getLogger("app.services.tools.flow_selectors")

SELECTOR_PREFIX = "automation.flow."


def _select(
    name: str,
    *,
    node_config: ActionNodeConfig,
    facts: FactSet,
    tenant: TenantScope,
) -> ActionSpec:
    spec = ActionSpec(
        action_type=node_config.action_type,
        recipient=node_config.recipient,
        target_field=node_config.target_field,
        target_value=node_config.target_value,
        rationale=tuple(facts.keys()),
        parameters=tuple(node_config.options),
        list_parameters=tuple(node_config.list_options),
    )
    spec.assert_no_document_derived_values(config=node_config, facts=facts)
    logger.debug(
        "tools.flow_action_selected",
        extra={
            "selector": name,
            "action_type": node_config.action_type,
            "workspace_id": str(tenant.workspace_id),
            "rule_id": str(tenant.rule_id),
            "execution_id": str(tenant.execution_id),
        },
    )
    return spec


@register_tool_selector("automation.flow.webhook_send")
def select_webhook_send(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.webhook_send", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.redaction_start")
def select_redaction_start(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.redaction_start", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.review_escalate")
def select_review_escalate(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.review_escalate", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.warehouse_export")
def select_warehouse_export(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.warehouse_export", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.notify_role")
def select_notify_role(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.notify_role", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.autonomy_decide")
def select_autonomy_decide(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    return _select("automation.flow.autonomy_decide", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.email_send")
def select_email_send(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    if not node_config.recipient:
        raise ValueError("An email action needs an author-supplied recipient.")
    return _select("automation.flow.email_send", node_config=node_config, facts=facts, tenant=tenant)


@register_tool_selector("automation.flow.work_item_mutate")
def select_work_item_mutate(
    *, node_config: ActionNodeConfig, facts: FactSet, tenant: TenantScope
) -> ActionSpec:
    if node_config.target_field is None:
        raise ValueError(
            "A mutation action needs an author-supplied target_field. Deriving "
            "the field name from extraction would let a document choose which "
            "column it writes to."
        )
    return _select("automation.flow.work_item_mutate", node_config=node_config, facts=facts, tenant=tenant)


__all__ = [
    "SELECTOR_PREFIX",
    "select_autonomy_decide",
    "select_email_send",
    "select_notify_role",
    "select_redaction_start",
    "select_review_escalate",
    "select_warehouse_export",
    "select_webhook_send",
    "select_work_item_mutate",
]
'''


NEW_FRONTEND_FILES: dict[str, str] = {}
NEW_FRONTEND_FILES['src/types/automationFlow.ts'] = r'''/**
 * ARCH-37 — the flow builder's contract with the server.
 *
 * Everything the builder may offer (triggers, actions, operators, fields,
 * endpoints, destinations, profiles, roles) arrives in one catalog response
 * from GET /workspaces/{id}/automation/catalog. The console holds no trigger or
 * action list of its own; `verify_arch37.py` fails if one appears.
 */

export type FlowFieldType = "string" | "number" | "boolean" | "array" | "date";

export interface FlowField {
  readonly key: string;
  readonly label: string;
  readonly type: FlowFieldType;
  readonly example: string;
  readonly description: string;
  readonly source: "event" | "document";
}

export interface FlowTrigger {
  readonly key: string;
  readonly label: string;
  readonly category: string;
  readonly description: string;
  readonly event_types: readonly string[];
  readonly fields: readonly FlowField[];
  readonly capability: string | null;
  readonly has_document: boolean;
  readonly excluded_actions: readonly string[];
  readonly runs_during_review: boolean;
  readonly available: boolean;
}

export interface JsonSchemaProperty {
  readonly type?: string;
  readonly title?: string;
  readonly description?: string;
  readonly default?: unknown;
  readonly enum?: readonly string[];
  readonly minimum?: number;
  readonly maximum?: number;
  readonly maxLength?: number;
  readonly items?: JsonSchemaProperty;
  readonly anyOf?: readonly JsonSchemaProperty[];
  readonly format?: string;
}

export interface FlowActionSchema {
  readonly properties?: Readonly<Record<string, JsonSchemaProperty>>;
  readonly required?: readonly string[];
}

export type FlowMinimumRole = "WORKSPACE_ADMIN" | "ORGANIZATION_ADMIN";

export interface FlowAction {
  readonly type: string;
  readonly label: string;
  readonly description: string;
  readonly category: string;
  readonly capability: string | null;
  readonly addon: string | null;
  readonly minimum_role: FlowMinimumRole;
  readonly available: boolean;
  readonly unavailable_reason: string | null;
  readonly commercial: boolean;
  readonly needs_document: boolean;
  readonly template_fields: readonly string[];
  readonly aliases: readonly string[];
  readonly config_schema: FlowActionSchema;
}

export interface FlowEndpointOption {
  readonly id: string;
  readonly label: string;
  readonly host: string;
}

export interface FlowDestinationOption {
  readonly id: string;
  readonly label: string;
  readonly kind: string;
}

export interface FlowCatalog {
  readonly triggers: readonly FlowTrigger[];
  readonly actions: readonly FlowAction[];
  readonly operators: Readonly<Record<FlowFieldType, readonly string[]>>;
  readonly valueless_operators: readonly string[];
  readonly template_variables: readonly string[];
  readonly document_fields: readonly FlowField[];
  readonly resources: {
    readonly webhook_endpoints: readonly FlowEndpointOption[];
    readonly warehouse_destinations: readonly FlowDestinationOption[];
    readonly redaction_profiles: readonly string[];
    readonly organization_roles: readonly string[];
    readonly export_datasets: readonly string[];
    readonly mutable_fields: readonly string[];
  };
  readonly limits: {
    readonly triggers: number;
    readonly groups: number;
    readonly conditions_per_group: number;
    readonly actions_per_branch: number;
  };
}

export interface FlowCondition {
  readonly field: string;
  readonly operator: string;
  readonly value: string;
}

export interface FlowConditionGroup {
  readonly logic_operator: "AND" | "OR";
  readonly conditions: readonly FlowCondition[];
}

export interface FlowActionEntry {
  readonly action_type: string;
  readonly config: Readonly<Record<string, unknown>>;
}

/** One row of GET .../automation/executions/{id}/nodes. */
export interface AutomationNodeRun {
  readonly id: string;
  readonly node_key: string | null;
  readonly node_type: string | null;
  readonly sequence: number;
  readonly status: string;
  readonly started_at: string | null;
  readonly completed_at: string | null;
  readonly attempt: number;
  readonly error: string | null;
  readonly action_type: string | null;
  readonly external_ref: string | null;
  readonly duration_ms: number | null;
  readonly outcome: string | null;
}
'''

NEW_FRONTEND_FILES['src/pages/Automation/FlowBuilder.tsx'] = r'''/**
 * ARCH-37 — the Enterprise Flow Builder.
 *
 * Replaces RuleForm (one trigger dropdown, one hardcoded "Send SMTP Email"
 * action, a field list written for resumes and invoices). A vertical step
 * builder: When -> Only if -> Then (-> Otherwise), with a summary rail.
 *
 * Everything offered comes from the catalog endpoint. Saving sends the whole
 * rule; the server validates it and returns refusals located by `loc`, which
 * `issuesFromError` places on the card that caused them. Local checks mirror
 * the server's so most mistakes show before a round trip.
 *
 * Keyboard: Ctrl/Cmd+S and Ctrl/Cmd+Enter save, Esc closes. Closing with
 * unsaved changes asks first, and so does leaving the page.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Loader2, Settings2, X } from "lucide-react";
import { toast } from "sonner";

import { ActionsCard } from "@/components/automation/flow/ActionsCard";
import { ConditionsCard } from "@/components/automation/flow/ConditionsCard";
import {
  draftFromRule,
  emptyDraft,
  issuesFromError,
  issuesFor,
  localIssues,
  payloadFromDraft,
  sameDraft,
  summarize,
  type CardIssue,
  type FlowDraft,
} from "@/components/automation/flow/flowModel";
import { IssueText, StepCard, inputClass } from "@/components/automation/flow/StepCard";
import { SummaryRail } from "@/components/automation/flow/SummaryRail";
import { TriggerCard } from "@/components/automation/flow/TriggerCard";
import { useActiveWorkspaceId } from "@/hooks/useActiveWorkspace";
import { automationApi } from "@/services/api/automation";
import { automationKeys } from "@/services/api/queryKeys";
import type { AutomationRule } from "@/types/automation";

interface FlowBuilderProps {
  readonly isOpen: boolean;
  readonly onClose: () => void;
  readonly onSaveSuccess: () => void;
  readonly ruleToEdit?: AutomationRule | null;
  readonly ruleToDuplicate?: AutomationRule | null;
  readonly existingRules?: readonly AutomationRule[];
}

export const FlowBuilder: React.FC<FlowBuilderProps> = ({
  isOpen,
  onClose,
  onSaveSuccess,
  ruleToEdit = null,
  ruleToDuplicate = null,
  existingRules = [],
}) => {
  const workspaceId = useActiveWorkspaceId();
  const queryClient = useQueryClient();
  const isEdit = ruleToEdit !== null;
  const source = ruleToEdit ?? ruleToDuplicate;

  const catalogQuery = useQuery({
    queryKey: automationKeys.catalog(workspaceId ?? ""),
    queryFn: () => automationApi.getAutomationCatalog(workspaceId ?? ""),
    enabled: isOpen && Boolean(workspaceId),
    staleTime: 60_000,
  });
  const catalog = catalogQuery.data;

  const [draft, setDraft] = useState<FlowDraft>(emptyDraft);
  const [initial, setInitial] = useState<FlowDraft>(emptyDraft);
  const [serverIssues, setServerIssues] = useState<readonly CardIssue[]>([]);
  const [attempted, setAttempted] = useState(false);
  const openedFor = useRef<string | null>(null);

  // Load the draft once per opening, after the catalog arrives (legacy action
  // names are resolved through the catalog's aliases).
  useEffect(() => {
    if (!isOpen) {
      openedFor.current = null;
      return;
    }
    if (!catalog) {return;}
    const key = `${source?.id ?? "new"}:${isEdit ? "edit" : "dup"}`;
    if (openedFor.current === key) {return;}
    openedFor.current = key;
    const next = source ? draftFromRule(source, catalog, isEdit ? "edit" : "duplicate") : emptyDraft();
    setDraft(next);
    setInitial(next);
    setServerIssues([]);
    setAttempted(false);
  }, [isOpen, catalog, source, isEdit]);

  const update = useCallback((patch: Partial<FlowDraft>): void => {
    setDraft((current) => ({ ...current, ...patch }));
    setServerIssues([]);
  }, []);

  const dirty = catalog ? !sameDraft(draft, initial, catalog) : false;

  const duplicateName = useMemo(() => {
    const name = draft.name.trim().toLowerCase();
    return Boolean(name) && existingRules.some((r) => r.name.trim().toLowerCase() === name && r.id !== ruleToEdit?.id);
  }, [draft.name, existingRules, ruleToEdit]);

  const issues = useMemo<CardIssue[]>(() => {
    const local = catalog ? localIssues(draft, catalog) : [];
    if (duplicateName) {
      local.push({ card: "general", index: null, subIndex: null, field: "name", message: "Another rule already has this name." });
    }
    return [...serverIssues, ...local.filter((l) => !serverIssues.some((s) => s.card === l.card && s.message === l.message))];
  }, [catalog, draft, duplicateName, serverIssues]);
  const shown = attempted ? issues : serverIssues;

  const save = useMutation({
    mutationFn: async () => {
      if (!workspaceId || !catalog) {throw new Error("The workspace is not ready yet.");}
      const payload = payloadFromDraft(draft, catalog);
      return ruleToEdit
        ? automationApi.updateAutomationRule(workspaceId, ruleToEdit.id, payload)
        : automationApi.createAutomationRule(workspaceId, payload);
    },
    onSuccess: async (rule) => {
      toast.success(isEdit ? `Saved “${rule.name}”.` : `Created “${rule.name}”.`);
      if (workspaceId) {await queryClient.invalidateQueries({ queryKey: automationKeys.rules(workspaceId) });}
      setInitial(draft);
      onSaveSuccess();
      onClose();
    },
    onError: (error: unknown) => {
      const located = issuesFromError(error);
      if (located && located.length) {
        setServerIssues(located);
        toast.error("The rule was not saved. See the highlighted steps.");
      } else {
        toast.error(error instanceof Error ? error.message : "The rule could not be saved.");
      }
    },
  });

  const submit = useCallback((): void => {
    setAttempted(true);
    if (save.isPending) {return;}
    if (issues.length > 0) {
      toast.error("Fix the highlighted steps before saving.");
      const first = issues[0];
      if (first) {document.getElementById(`flow-step-${first.card}`)?.scrollIntoView({ behavior: "smooth", block: "start" });}
      return;
    }
    save.mutate();
  }, [issues, save]);

  const requestClose = useCallback((): void => {
    if (save.isPending) {return;}
    if (dirty && !window.confirm("Discard your unsaved changes to this rule?")) {return;}
    onClose();
  }, [dirty, onClose, save.isPending]);

  useEffect(() => {
    if (!isOpen) {return undefined;}
    const onKey = (event: KeyboardEvent): void => {
      const mod = event.ctrlKey || event.metaKey;
      if (mod && (event.key === "s" || event.key === "S" || event.key === "Enter")) {
        event.preventDefault();
        submit();
      } else if (event.key === "Escape") {
        event.preventDefault();
        requestClose();
      }
    };
    const onUnload = (event: BeforeUnloadEvent): void => {
      if (dirty) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("beforeunload", onUnload);
    };
  }, [isOpen, submit, requestClose, dirty]);

  if (!isOpen) {return null;}

  const busy = save.isPending;
  const hasConditions = draft.groups.some((g) => g.conditions.length > 0);
  const general = issuesFor(shown, "general");

  return (
    <div
      className="fixed inset-0 z-50 flex items-stretch justify-center bg-black/40 p-0 backdrop-blur-sm sm:p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="flow-builder-title"
    >
      <div className="flex w-full max-w-6xl flex-col overflow-hidden border border-border bg-background shadow-2xl sm:rounded-xl">
        <header className="flex h-14 shrink-0 items-center justify-between border-b border-border/60 bg-card px-5">
          <h2 id="flow-builder-title" className="text-sm font-extrabold uppercase tracking-wider">
            {isEdit ? "Edit automation" : ruleToDuplicate ? "Duplicate automation" : "New automation"}
          </h2>
          <button
            type="button"
            onClick={requestClose}
            disabled={busy}
            className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
            aria-label="Close the flow builder"
          >
            <X className="h-5 w-5" />
          </button>
        </header>

        {!catalog ? (
          <div className="flex flex-1 items-center justify-center p-10 text-sm text-muted-foreground" role="status">
            {catalogQuery.isError ? (
              <span className="text-destructive">
                The automation catalog could not be loaded.{" "}
                <button type="button" className="underline" onClick={() => void catalogQuery.refetch()}>Retry</button>
              </span>
            ) : (
              <span className="inline-flex items-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> Loading triggers and actions…</span>
            )}
          </div>
        ) : (
          <div className="grid flex-1 grid-cols-1 gap-5 overflow-y-auto p-5 lg:grid-cols-[minmax(0,1fr)_300px]">
            <div className="min-w-0 space-y-5">
              <StepCard
                id="flow-step-general"
                step={0}
                title="Details"
                subtitle="Name the rule and decide how it behaves when an action fails."
                icon={<Settings2 className="h-4 w-4" />}
                issues={general.filter((i) => i.field === null)}
              >
                <div className="grid grid-cols-1 gap-3 md:grid-cols-12">
                  <div className="md:col-span-6">
                    <label htmlFor="flow-name" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Name</label>
                    <input
                      id="flow-name"
                      autoFocus
                      disabled={busy}
                      maxLength={100}
                      value={draft.name}
                      placeholder="e.g. Escalate disputed invoices over $10k"
                      onChange={(e) => update({ name: e.target.value })}
                      className={inputClass(general.some((i) => i.field === "name"))}
                    />
                    <IssueText issues={general.filter((i) => i.field === "name")} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="flow-priority" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">Priority</label>
                    <input
                      id="flow-priority"
                      type="number"
                      min={1}
                      max={9999}
                      disabled={busy}
                      value={Number.isFinite(draft.priority) ? draft.priority : ""}
                      onChange={(e) => update({ priority: Number(e.target.value) })}
                      className={inputClass(general.some((i) => i.field === "priority"))}
                    />
                    <IssueText issues={general.filter((i) => i.field === "priority")} />
                  </div>
                  <div className="md:col-span-2">
                    <label htmlFor="flow-on-error" className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">On failure</label>
                    <select
                      id="flow-on-error"
                      disabled={busy}
                      value={draft.on_error}
                      onChange={(e) => update({ on_error: e.target.value === "CONTINUE" ? "CONTINUE" : "HALT" })}
                      className={inputClass(false)}
                    >
                      <option value="HALT">Stop the rule</option>
                      <option value="CONTINUE">Keep going</option>
                    </select>
                  </div>
                  <div className="flex items-end md:col-span-2">
                    <label className="inline-flex cursor-pointer items-center gap-2 pb-2 text-sm font-semibold">
                      <input
                        type="checkbox"
                        className="h-4 w-4 accent-primary"
                        disabled={busy}
                        checked={draft.is_active}
                        onChange={(e) => update({ is_active: e.target.checked })}
                      />
                      Active
                    </label>
                  </div>
                </div>
              </StepCard>

              <TriggerCard
                catalog={catalog}
                selected={draft.triggers}
                onChange={(triggers) => update({ triggers })}
                issues={issuesFor(shown, "trigger")}
                disabled={busy}
              />
              <ConditionsCard
                catalog={catalog}
                triggers={draft.triggers}
                groups={draft.groups}
                groupsOperator={draft.groups_operator}
                onChange={(groups, groups_operator) => update({ groups, groups_operator })}
                issues={issuesFor(shown, "conditions")}
                disabled={busy}
              />
              <ActionsCard
                catalog={catalog}
                card="actions"
                step={3}
                triggers={draft.triggers}
                actions={draft.actions}
                onChange={(actions) => update({ actions })}
                issues={issuesFor(shown, "actions")}
                disabled={busy}
                hasConditions={hasConditions}
              />
              <ActionsCard
                catalog={catalog}
                card="else"
                step={4}
                triggers={draft.triggers}
                actions={draft.else_actions}
                onChange={(else_actions) => update({ else_actions })}
                issues={issuesFor(shown, "else")}
                disabled={busy}
                hasConditions={hasConditions}
              />
            </div>

            <SummaryRail
              sentence={summarize(draft, catalog)}
              issues={shown}
              dirty={dirty}
              saving={busy}
              isEdit={isEdit}
              onSave={submit}
            />
          </div>
        )}
      </div>
    </div>
  );
};

export default FlowBuilder;
'''

NEW_FRONTEND_FILES['src/components/automation/flow/flowModel.ts'] = r'''/**
 * ARCH-37 — the step builder's draft model. Pure functions only.
 *
 * A draft is what the three cards edit. `draftFromRule` opens any stored rule
 * (ARCH-13 or ARCH-37), `payloadFromDraft` produces the request, and
 * `issuesByCard` turns the server's FastAPI-shaped refusals into messages on
 * the card that caused them. Action and trigger vocabulary comes from the
 * catalog; nothing here names one.
 */

import { ApiError } from "@/services/api/errors";
import type {
  AutomationErrorPolicy,
  AutomationLogicOperator,
  AutomationRule,
  AutomationRuleCreateRequest,
} from "@/types/automation";
import type {
  FlowAction,
  FlowActionEntry,
  FlowCatalog,
  FlowField,
  FlowFieldType,
  FlowTrigger,
  JsonSchemaProperty,
} from "@/types/automationFlow";

export interface ConditionDraft {
  readonly uid: string;
  readonly field: string;
  readonly operator: string;
  readonly value: string;
}

export interface GroupDraft {
  readonly uid: string;
  readonly logic_operator: AutomationLogicOperator;
  readonly conditions: readonly ConditionDraft[];
}

export interface ActionDraft {
  readonly uid: string;
  readonly action_type: string;
  readonly config: Readonly<Record<string, unknown>>;
}

export interface FlowDraft {
  readonly name: string;
  readonly priority: number;
  readonly is_active: boolean;
  readonly on_error: AutomationErrorPolicy;
  readonly triggers: readonly string[];
  readonly groups: readonly GroupDraft[];
  readonly groups_operator: AutomationLogicOperator;
  readonly actions: readonly ActionDraft[];
  readonly else_actions: readonly ActionDraft[];
}

export type CardKey = "general" | "trigger" | "conditions" | "actions" | "else";

export interface CardIssue {
  readonly card: CardKey;
  /** Group index for conditions, action index for actions / else. */
  readonly index: number | null;
  /** Condition index inside a group. */
  readonly subIndex: number | null;
  /** The config key or field the issue names, if any. */
  readonly field: string | null;
  readonly message: string;
}

let counter = 0;
export const uid = (prefix: string): string => {
  counter += 1;
  return `${prefix}-${Date.now().toString(36)}-${counter}`;
};

export const OPERATOR_LABELS: Readonly<Record<string, string>> = {
  EQUALS: "equals",
  NOT_EQUALS: "does not equal",
  CONTAINS: "contains",
  NOT_CONTAINS: "does not contain",
  STARTS_WITH: "starts with",
  ENDS_WITH: "ends with",
  GREATER_THAN: "is greater than",
  LESS_THAN: "is less than",
  GREATER_THAN_OR_EQUAL: "is at least",
  LESS_THAN_OR_EQUAL: "is at most",
  BETWEEN: "is between",
  IN: "is one of",
  NOT_IN: "is not one of",
  EXISTS: "exists",
  IS_EMPTY: "is empty",
  IS_NOT_EMPTY: "is not empty",
  ARRAY_CONTAINS_ANY: "contains any of",
  ARRAY_CONTAINS_ALL: "contains all of",
};

export const operatorLabel = (operator: string): string =>
  OPERATOR_LABELS[operator] ?? operator.toLowerCase().replace(/_/g, " ");

export const humanize = (key: string): string => {
  const last = key.replace(/^event\./, "").replace(/^classification_details\./, "");
  const text = last.replace(/[._]+/g, " ").trim();
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : key;
};

export const emptyDraft = (): FlowDraft => ({
  name: "",
  priority: 100,
  is_active: true,
  on_error: "HALT",
  triggers: [],
  groups: [],
  groups_operator: "AND",
  actions: [],
  else_actions: [],
});

export const findTrigger = (catalog: FlowCatalog | undefined, key: string): FlowTrigger | undefined =>
  catalog?.triggers.find((t) => t.key === key);

/** The catalog action for a stored type, including legacy aliases. */
export const findAction = (catalog: FlowCatalog | undefined, actionType: string): FlowAction | undefined => {
  if (!catalog) {return undefined;}
  const lowered = actionType.trim().toLowerCase();
  return (
    catalog.actions.find((a) => a.type === lowered) ??
    catalog.actions.find((a) => a.aliases.includes(lowered))
  );
};

export const triggerLabel = (catalog: FlowCatalog | undefined, key: string): string =>
  findTrigger(catalog, key)?.label ?? humanize(key);

export const actionLabel = (catalog: FlowCatalog | undefined, actionType: string): string =>
  findAction(catalog, actionType)?.label ?? humanize(actionType);

/** Event fields every selected trigger carries (the server enforces the same). */
export const commonEventFields = (catalog: FlowCatalog | undefined, triggers: readonly string[]): FlowField[] => {
  const specs = triggers
    .map((key) => findTrigger(catalog, key))
    .filter((t): t is FlowTrigger => t !== undefined);
  const first = specs[0];
  if (!first) {return [];}
  return first.fields.filter((field) => specs.every((spec) => spec.fields.some((f) => f.key === field.key)));
};

export const availableFields = (catalog: FlowCatalog | undefined, triggers: readonly string[]): FlowField[] => {
  const specs = triggers.map((key) => findTrigger(catalog, key)).filter((t): t is FlowTrigger => t !== undefined);
  const documents = specs.length > 0 && specs.every((spec) => spec.has_document);
  return [...commonEventFields(catalog, triggers), ...(documents ? catalog?.document_fields ?? [] : [])];
};

export const fieldFor = (catalog: FlowCatalog | undefined, triggers: readonly string[], key: string): FlowField | undefined =>
  availableFields(catalog, triggers).find((f) => f.key === key);

export const fieldLabel = (catalog: FlowCatalog | undefined, key: string): string => {
  const pool = [
    ...(catalog?.triggers.flatMap((t) => t.fields) ?? []),
    ...(catalog?.document_fields ?? []),
  ];
  return pool.find((f) => f.key === key)?.label ?? humanize(key);
};

export const operatorsFor = (catalog: FlowCatalog | undefined, type: FlowFieldType | undefined): readonly string[] => {
  if (!catalog) {return [];}
  if (type) {return catalog.operators[type] ?? [];}
  return Array.from(new Set(Object.values(catalog.operators).flat()));
};

export const isValueless = (catalog: FlowCatalog | undefined, operator: string): boolean =>
  catalog?.valueless_operators.includes(operator) ?? false;

/** Actions the selected triggers may run. */
export const excludedActions = (catalog: FlowCatalog | undefined, triggers: readonly string[]): Set<string> =>
  new Set(triggers.flatMap((key) => findTrigger(catalog, key)?.excluded_actions ?? []));

export const schemaProperties = (action: FlowAction | undefined): [string, JsonSchemaProperty][] =>
  Object.entries(action?.config_schema.properties ?? {});

export const defaultConfig = (action: FlowAction): Record<string, unknown> => {
  const config: Record<string, unknown> = {};
  for (const [key, prop] of schemaProperties(action)) {
    if (prop.default !== undefined) {
      config[key] = prop.default;
    } else if (prop.type === "array") {
      config[key] = [];
    }
  }
  return config;
};

const cleanConfig = (action: FlowAction | undefined, config: Readonly<Record<string, unknown>>): Record<string, unknown> => {
  const allowed = new Set(schemaProperties(action).map(([key]) => key));
  const required = new Set(action?.config_schema.required ?? []);
  const cleaned: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(config)) {
    if (allowed.size > 0 && !allowed.has(key)) {continue;}
    if (typeof value === "string" && value.trim() === "" && !required.has(key)) {continue;}
    cleaned[key] = typeof value === "string" ? value.trim() : value;
  }
  return cleaned;
};

const toActionDraft = (catalog: FlowCatalog | undefined, entry: FlowActionEntry): ActionDraft => {
  const action = findAction(catalog, entry.action_type);
  const base = action ? defaultConfig(action) : {};
  return {
    uid: uid("action"),
    action_type: action?.type ?? entry.action_type,
    config: { ...base, ...(entry.config ?? {}) },
  };
};

export const draftFromRule = (
  rule: AutomationRule,
  catalog: FlowCatalog | undefined,
  mode: "edit" | "duplicate",
): FlowDraft => {
  const groups: GroupDraft[] = (rule.condition_groups ?? []).map((group) => ({
    uid: uid("group"),
    logic_operator: group.logic_operator,
    conditions: group.conditions.map((condition) => ({
      uid: uid("condition"),
      field: condition.field,
      operator: condition.operator,
      value: condition.value ?? "",
    })),
  }));
  return {
    name: mode === "duplicate" ? `${rule.name} (copy)`.slice(0, 100) : rule.name,
    priority: rule.priority,
    is_active: mode === "duplicate" ? false : rule.is_active,
    on_error: rule.on_error ?? "HALT",
    triggers: [...(rule.triggers ?? [])],
    groups,
    groups_operator: rule.groups_operator ?? "AND",
    actions: rule.actions.map((entry) => toActionDraft(catalog, entry)),
    else_actions: (rule.else_actions ?? []).map((entry) => toActionDraft(catalog, entry)),
  };
};

export const payloadFromDraft = (draft: FlowDraft, catalog: FlowCatalog | undefined): AutomationRuleCreateRequest => {
  const actionEntry = (entry: ActionDraft): FlowActionEntry => ({
    action_type: entry.action_type,
    config: cleanConfig(findAction(catalog, entry.action_type), entry.config),
  });
  return {
    name: draft.name.trim(),
    priority: draft.priority,
    is_active: draft.is_active,
    on_error: draft.on_error,
    triggers: [...draft.triggers],
    condition_groups: draft.groups
      .filter((group) => group.conditions.length > 0)
      .map((group) => ({
        logic_operator: group.logic_operator,
        conditions: group.conditions.map(({ field, operator, value }) => ({ field, operator, value })),
      })),
    groups_operator: draft.groups_operator,
    actions: draft.actions.map(actionEntry),
    else_actions: draft.else_actions.map(actionEntry),
  };
};

/** Client-side checks that mirror the server's, so most mistakes show before a round trip. */
export const localIssues = (draft: FlowDraft, catalog: FlowCatalog | undefined): CardIssue[] => {
  const issues: CardIssue[] = [];
  const push = (card: CardKey, message: string, index: number | null = null, subIndex: number | null = null, field: string | null = null): void => {
    issues.push({ card, index, subIndex, field, message });
  };
  if (!draft.name.trim()) {push("general", "Give the rule a name.", null, null, "name");}
  if (!Number.isInteger(draft.priority) || draft.priority < 1) {push("general", "Priority must be a whole number of at least 1.", null, null, "priority");}
  if (draft.triggers.length === 0) {push("trigger", "Choose at least one trigger.");}
  draft.triggers.forEach((key, index) => {
    const trigger = findTrigger(catalog, key);
    if (!trigger) {push("trigger", "This trigger no longer exists.", index);}
    else if (!trigger.available) {push("trigger", `${trigger.label} is not included in your plan.`, index);}
  });
  draft.groups.forEach((group, g) => {
    group.conditions.forEach((condition, c) => {
      if (!condition.field) {push("conditions", "Choose a field.", g, c, "field");}
      else if (!fieldFor(catalog, draft.triggers, condition.field) && condition.field.startsWith("event.")) {
        push("conditions", "Not every selected trigger carries this field.", g, c, "field");
      }
      if (!condition.operator) {push("conditions", "Choose a comparison.", g, c, "operator");}
      if (condition.operator && !isValueless(catalog, condition.operator) && !condition.value.trim()) {
        push("conditions", "Enter a value.", g, c, "value");
      }
    });
  });
  const hasConditions = draft.groups.some((group) => group.conditions.length > 0);
  if (draft.actions.length === 0) {push("actions", "Add at least one action.");}
  if (draft.else_actions.length > 0 && !hasConditions) {push("else", "'Otherwise' actions need at least one condition.");}
  const excluded = excludedActions(catalog, draft.triggers);
  const checkActions = (card: CardKey, list: readonly ActionDraft[]): void => {
    list.forEach((entry, index) => {
      const action = findAction(catalog, entry.action_type);
      if (!action) {
        push(card, "This action no longer exists.", index);
        return;
      }
      if (!action.available) {push(card, action.unavailable_reason ?? `${action.label} is unavailable.`, index);}
      if (excluded.has(action.type)) {push(card, `${action.label} cannot run on the selected trigger.`, index);}
      for (const key of action.config_schema.required ?? []) {
        const value = entry.config[key];
        const empty = value === undefined || value === null || (typeof value === "string" && !value.trim()) || (Array.isArray(value) && value.length === 0);
        if (empty) {push(card, `${humanize(key)} is required.`, index, null, key);}
      }
    });
  };
  checkActions("actions", draft.actions);
  checkActions("else", draft.else_actions);
  return issues;
};

interface ServerIssue {
  readonly loc?: readonly (string | number)[];
  readonly msg?: string;
}

/** The server's refusals, placed on the card that caused them. */
export const issuesFromError = (error: unknown): CardIssue[] | null => {
  if (!(error instanceof ApiError)) {return null;}
  const raw = error.details["issues"];
  if (!Array.isArray(raw)) {return null;}
  return (raw as ServerIssue[]).map((issue) => {
    const loc = (issue.loc ?? []).filter((part) => part !== "body");
    const head = String(loc[0] ?? "");
    const num = (value: unknown): number | null => (typeof value === "number" ? value : null);
    const message = issue.msg ?? "Invalid value.";
    if (head === "triggers" || head === "event") {
      return { card: "trigger", index: num(loc[1]), subIndex: null, field: null, message };
    }
    if (head === "condition_groups" || head === "conditions" || head === "groups_operator" || head === "logic_operator") {
      return {
        card: "conditions",
        index: num(loc[1]),
        subIndex: num(loc[3]),
        field: typeof loc[4] === "string" ? loc[4] : null,
        message,
      };
    }
    if (head === "actions" || head === "else_actions") {
      const configKey = loc[2] === "config" && typeof loc[3] === "string" ? loc[3] : null;
      return {
        card: head === "actions" ? "actions" : "else",
        index: num(loc[1]),
        subIndex: null,
        field: configKey,
        message,
      };
    }
    return {
      card: "general",
      index: null,
      subIndex: null,
      field: typeof loc[0] === "string" ? loc[0] : null,
      message,
    };
  });
};

export const issuesFor = (
  issues: readonly CardIssue[],
  card: CardKey,
  index: number | null = null,
): CardIssue[] =>
  issues.filter((issue) => issue.card === card && (index === null || issue.index === index));

const joinWords = (parts: readonly string[], word: string): string => {
  if (parts.length <= 1) {return parts[0] ?? "";}
  return `${parts.slice(0, -1).join(", ")} ${word} ${parts[parts.length - 1] ?? ""}`;
};

/** A plain-English sentence for the summary rail and the rule list. */
export const summarize = (draft: FlowDraft, catalog: FlowCatalog | undefined): string => {
  const when = draft.triggers.length
    ? joinWords(draft.triggers.map((key) => triggerLabel(catalog, key).toLowerCase()), "or")
    : "…";
  const groups = draft.groups
    .filter((group) => group.conditions.length > 0)
    .map((group) => {
      const parts = group.conditions.map((condition) => {
        const valueless = isValueless(catalog, condition.operator);
        return `${fieldLabel(catalog, condition.field).toLowerCase()} ${operatorLabel(condition.operator)}${valueless ? "" : ` “${condition.value}”`}`;
      });
      return joinWords(parts, group.logic_operator === "OR" ? "or" : "and");
    });
  const condition = groups.length
    ? `, if ${groups.map((g) => (groups.length > 1 ? `(${g})` : g)).join(draft.groups_operator === "OR" ? " or " : " and ")}`
    : "";
  const then = draft.actions.length
    ? joinWords(draft.actions.map((a) => actionLabel(catalog, a.action_type).toLowerCase()), "then")
    : "…";
  const otherwise = draft.else_actions.length
    ? `; otherwise ${joinWords(draft.else_actions.map((a) => actionLabel(catalog, a.action_type).toLowerCase()), "then")}`
    : "";
  return `When ${when}${condition}: ${then}${otherwise}.`;
};

export const sameDraft = (a: FlowDraft, b: FlowDraft, catalog: FlowCatalog | undefined): boolean =>
  JSON.stringify(payloadFromDraft(a, catalog)) === JSON.stringify(payloadFromDraft(b, catalog));
'''

NEW_FRONTEND_FILES['src/components/automation/flow/StepCard.tsx'] = r'''/**
 * ARCH-37 — the shell every builder step shares: a numbered card with its
 * own issue list, so a server refusal lands where the user is looking.
 */

import React from "react";
import { AlertTriangle } from "lucide-react";

import type { CardIssue } from "./flowModel";

interface StepCardProps {
  readonly step: number;
  readonly title: string;
  readonly subtitle: string;
  readonly icon: React.ReactNode;
  readonly issues: readonly CardIssue[];
  readonly children: React.ReactNode;
  readonly aside?: React.ReactNode;
  readonly id: string;
}

export const StepCard: React.FC<StepCardProps> = ({ step, title, subtitle, icon, issues, children, aside, id }) => {
  const cardLevel = issues.filter((issue) => issue.index === null);
  return (
    <section
      id={id}
      aria-labelledby={`${id}-title`}
      className={`rounded-xl border bg-card shadow-sm ${issues.length ? "border-destructive/60" : "border-border"}`}
    >
      <header className="flex items-start justify-between gap-3 border-b border-border/50 px-5 py-4">
        <div className="flex items-start gap-3 min-w-0">
          <span className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-black text-primary">
            {step}
          </span>
          <div className="min-w-0">
            <h3 id={`${id}-title`} className="flex items-center gap-2 text-sm font-extrabold uppercase tracking-wider">
              <span aria-hidden="true" className="text-primary">{icon}</span>
              {title}
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">{subtitle}</p>
          </div>
        </div>
        {aside}
      </header>
      {cardLevel.length > 0 && (
        <ul className="mx-5 mt-4 space-y-1 rounded-lg border border-destructive/40 bg-destructive/5 px-3 py-2" role="alert">
          {cardLevel.map((issue, index) => (
            <li key={index} className="flex items-start gap-2 text-xs font-semibold text-destructive">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {issue.message}
            </li>
          ))}
        </ul>
      )}
      <div className="px-5 py-4">{children}</div>
    </section>
  );
};

export const IssueText: React.FC<{ readonly issues: readonly CardIssue[] }> = ({ issues }) =>
  issues.length ? (
    <p className="mt-1 text-xs font-semibold text-destructive" role="alert">
      {issues.map((issue) => issue.message).join(" ")}
    </p>
  ) : null;

export const inputClass = (invalid: boolean): string =>
  `w-full rounded-lg border bg-background px-3 py-2 text-sm focus:outline-none focus:ring-2 ${
    invalid
      ? "border-destructive focus:border-destructive focus:ring-destructive/20"
      : "border-border focus:border-primary focus:ring-primary/20"
  }`;
'''

NEW_FRONTEND_FILES['src/components/automation/flow/TriggerCard.tsx'] = r'''/**
 * ARCH-37 — step 1: when. Every entry is a catalog trigger; an entry the plan
 * does not include is shown, locked, with the reason.
 */

import React, { useMemo } from "react";
import { Lock, Zap } from "lucide-react";

import type { FlowCatalog, FlowTrigger } from "@/types/automationFlow";
import type { CardIssue } from "./flowModel";
import { IssueText, StepCard } from "./StepCard";

interface TriggerCardProps {
  readonly catalog: FlowCatalog;
  readonly selected: readonly string[];
  readonly onChange: (next: readonly string[]) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
}

export const TriggerCard: React.FC<TriggerCardProps> = ({ catalog, selected, onChange, issues, disabled }) => {
  const byCategory = useMemo(() => {
    const groups = new Map<string, FlowTrigger[]>();
    for (const trigger of catalog.triggers) {
      const list = groups.get(trigger.category) ?? [];
      list.push(trigger);
      groups.set(trigger.category, list);
    }
    return Array.from(groups.entries());
  }, [catalog.triggers]);

  const limit = catalog.limits.triggers;
  const toggle = (key: string): void => {
    if (selected.includes(key)) {
      onChange(selected.filter((k) => k !== key));
    } else if (selected.length < limit) {
      onChange([...selected, key]);
    }
  };

  return (
    <StepCard
      id="flow-step-trigger"
      step={1}
      title="When"
      subtitle={`Choose what starts this rule. Pick up to ${limit}; the rule runs when any of them happens.`}
      icon={<Zap className="h-4 w-4" />}
      issues={issues}
    >
      <div className="space-y-4">
        {byCategory.map(([category, triggers]) => (
          <fieldset key={category} className="space-y-2">
            <legend className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">{category}</legend>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
              {triggers.map((trigger) => {
                const checked = selected.includes(trigger.key);
                const index = selected.indexOf(trigger.key);
                const locked = !trigger.available;
                const full = !checked && selected.length >= limit;
                const own = issues.filter((issue) => issue.index !== null && issue.index === index);
                return (
                  <label
                    key={trigger.key}
                    className={`flex cursor-pointer items-start gap-3 rounded-lg border p-3 transition-colors ${
                      checked ? "border-primary bg-primary/5" : "border-border hover:bg-muted/40"
                    } ${locked || full || disabled ? "cursor-not-allowed opacity-60" : ""}`}
                  >
                    <input
                      type="checkbox"
                      className="mt-0.5 h-4 w-4 accent-primary"
                      checked={checked}
                      disabled={disabled || (!checked && (locked || full))}
                      onChange={() => toggle(trigger.key)}
                      aria-describedby={`trigger-desc-${trigger.key}`}
                    />
                    <span className="min-w-0">
                      <span className="flex flex-wrap items-center gap-1.5 text-sm font-semibold">
                        {trigger.label}
                        {locked && (
                          <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">
                            <Lock className="h-3 w-3" aria-hidden="true" /> Not in your plan
                          </span>
                        )}
                        {trigger.runs_during_review && (
                          <span className="rounded bg-amber-500/15 px-1.5 py-0.5 text-[10px] font-bold text-amber-700 dark:text-amber-400">
                            Runs during review
                          </span>
                        )}
                      </span>
                      <span id={`trigger-desc-${trigger.key}`} className="mt-0.5 block text-xs text-muted-foreground">
                        {trigger.description}
                      </span>
                      <IssueText issues={own} />
                    </span>
                  </label>
                );
              })}
            </div>
          </fieldset>
        ))}
      </div>
    </StepCard>
  );
};
'''

NEW_FRONTEND_FILES['src/components/automation/flow/ConditionsCard.tsx'] = r'''/**
 * ARCH-37 — step 2: only if. Groups of conditions; AND/OR inside a group and
 * between groups. Fields come from the selected triggers (event fields every
 * one of them carries) and from this workspace's recent extractions.
 * Operators are filtered by the field's type.
 */

import React from "react";
import { Filter, Plus, Trash2 } from "lucide-react";

import type { AutomationLogicOperator } from "@/types/automation";
import type { FlowCatalog } from "@/types/automationFlow";
import {
  availableFields,
  commonEventFields,
  fieldFor,
  isValueless,
  operatorLabel,
  operatorsFor,
  uid,
  type CardIssue,
  type ConditionDraft,
  type GroupDraft,
} from "./flowModel";
import { IssueText, StepCard, inputClass } from "./StepCard";

interface ConditionsCardProps {
  readonly catalog: FlowCatalog;
  readonly triggers: readonly string[];
  readonly groups: readonly GroupDraft[];
  readonly groupsOperator: AutomationLogicOperator;
  readonly onChange: (groups: readonly GroupDraft[], groupsOperator: AutomationLogicOperator) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
}

const OperatorToggle: React.FC<{
  readonly value: AutomationLogicOperator;
  readonly onChange: (value: AutomationLogicOperator) => void;
  readonly label: string;
  readonly disabled: boolean;
}> = ({ value, onChange, label, disabled }) => (
  <div role="radiogroup" aria-label={label} className="inline-flex rounded-md border border-border p-0.5">
    {(["AND", "OR"] as const).map((option) => (
      <button
        key={option}
        type="button"
        role="radio"
        aria-checked={value === option}
        disabled={disabled}
        onClick={() => onChange(option)}
        className={`rounded px-2 py-0.5 text-[10px] font-black ${
          value === option ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-muted"
        }`}
      >
        {option === "AND" ? "ALL" : "ANY"}
      </button>
    ))}
  </div>
);

export const ConditionsCard: React.FC<ConditionsCardProps> = ({
  catalog, triggers, groups, groupsOperator, onChange, issues, disabled,
}) => {
  const eventFields = commonEventFields(catalog, triggers);
  const documentFields = availableFields(catalog, triggers).filter((f) => f.source === "document");
  const limits = catalog.limits;

  const setGroup = (index: number, next: GroupDraft): void =>
    onChange(groups.map((group, i) => (i === index ? next : group)), groupsOperator);

  const setCondition = (g: number, c: number, patch: Partial<ConditionDraft>): void => {
    const group = groups[g];
    if (!group) {return;}
    const conditions = group.conditions.map((condition, i) => {
      if (i !== c) {return condition;}
      const field = patch.field ?? condition.field;
      let operator = patch.operator ?? condition.operator;
      let value = patch.value ?? condition.value;
      if (patch.field !== undefined) {
        const allowed = operatorsFor(catalog, fieldFor(catalog, triggers, field)?.type);
        if (!allowed.includes(operator)) {operator = allowed[0] ?? "EQUALS";}
      }
      if (isValueless(catalog, operator)) {value = "";}
      return { uid: condition.uid, field, operator, value };
    });
    setGroup(g, { ...group, conditions });
  };

  const freshCondition = (): ConditionDraft => {
    const first = eventFields[0] ?? documentFields[0];
    const operators = operatorsFor(catalog, first?.type);
    return { uid: uid("condition"), field: first?.key ?? "", operator: operators[0] ?? "EQUALS", value: "" };
  };

  const addCondition = (g: number): void => {
    const group = groups[g];
    if (!group || group.conditions.length >= limits.conditions_per_group) {return;}
    setGroup(g, { ...group, conditions: [...group.conditions, freshCondition()] });
  };

  const addGroup = (): void => {
    if (groups.length >= limits.groups) {return;}
    // A new group opens with one condition, ready to edit.
    const next: GroupDraft = { uid: uid("group"), logic_operator: "AND", conditions: [freshCondition()] };
    onChange([...groups, next], groupsOperator);
  };

  return (
    <StepCard
      id="flow-step-conditions"
      step={2}
      title="Only if"
      subtitle={
        groups.length === 0
          ? "No conditions: the rule runs every time the trigger fires."
          : "Every group is checked; combine groups with ALL or ANY."
      }
      icon={<Filter className="h-4 w-4" />}
      issues={issues}
      aside={
        groups.length > 1 ? (
          <OperatorToggle
            value={groupsOperator}
            onChange={(value) => onChange(groups, value)}
            label="Combine groups"
            disabled={disabled}
          />
        ) : undefined
      }
    >
      <div className="space-y-3">
        {groups.map((group, g) => (
          <div key={group.uid} className="rounded-lg border border-border/70 bg-muted/20 p-3">
            <div className="mb-2 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-xs font-bold text-muted-foreground">
                <span>Group {g + 1}: match</span>
                <OperatorToggle
                  value={group.logic_operator}
                  onChange={(value) => setGroup(g, { ...group, logic_operator: value })}
                  label={`Group ${g + 1} combines with`}
                  disabled={disabled}
                />
              </div>
              <button
                type="button"
                disabled={disabled}
                onClick={() => onChange(groups.filter((_, i) => i !== g), groupsOperator)}
                className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                aria-label={`Remove group ${g + 1}`}
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>

            <div className="space-y-2">
              {group.conditions.map((condition, c) => {
                const field = fieldFor(catalog, triggers, condition.field);
                const operators = operatorsFor(catalog, field?.type);
                const own = issues.filter((issue) => issue.index === g && issue.subIndex === c);
                const byField = (name: string) => own.filter((issue) => issue.field === name);
                const custom = condition.field !== "" && field === undefined && !condition.field.startsWith("event.");
                return (
                  <div key={condition.uid} className="grid grid-cols-1 gap-2 md:grid-cols-12">
                    <div className="md:col-span-5">
                      <select
                        aria-label="Field"
                        disabled={disabled}
                        value={custom ? "__custom__" : condition.field}
                        onChange={(e) =>
                          setCondition(g, c, { field: e.target.value === "__custom__" ? "custom_field" : e.target.value })
                        }
                        className={inputClass(byField("field").length > 0)}
                      >
                        <option value="" disabled>Choose a field…</option>
                        {eventFields.length > 0 && (
                          <optgroup label="From the trigger">
                            {eventFields.map((f) => (
                              <option key={f.key} value={f.key}>{f.label}</option>
                            ))}
                          </optgroup>
                        )}
                        {documentFields.length > 0 && (
                          <optgroup label="From the document">
                            {documentFields.map((f) => (
                              <option key={f.key} value={f.key}>{f.label}</option>
                            ))}
                          </optgroup>
                        )}
                        {triggers.length > 0 && <option value="__custom__">Another document field…</option>}
                      </select>
                      {custom && (
                        <input
                          aria-label="Document field path"
                          disabled={disabled}
                          value={condition.field}
                          onChange={(e) => setCondition(g, c, { field: e.target.value.trim() })}
                          placeholder="e.g. invoice_number"
                          className={`${inputClass(false)} mt-1 font-mono text-xs`}
                        />
                      )}
                      <IssueText issues={byField("field")} />
                    </div>
                    <div className="md:col-span-3">
                      <select
                        aria-label="Comparison"
                        disabled={disabled}
                        value={condition.operator}
                        onChange={(e) => setCondition(g, c, { operator: e.target.value })}
                        className={inputClass(byField("operator").length > 0)}
                      >
                        {operators.map((op) => (
                          <option key={op} value={op}>{operatorLabel(op)}</option>
                        ))}
                      </select>
                      <IssueText issues={byField("operator")} />
                    </div>
                    <div className="md:col-span-3">
                      {!isValueless(catalog, condition.operator) && (
                        <input
                          aria-label="Value"
                          disabled={disabled}
                          type={field?.type === "number" && !["IN", "NOT_IN", "BETWEEN"].includes(condition.operator) ? "number" : "text"}
                          value={condition.value}
                          placeholder={
                            ["IN", "NOT_IN", "BETWEEN", "ARRAY_CONTAINS_ANY", "ARRAY_CONTAINS_ALL"].includes(condition.operator)
                              ? "comma, separated"
                              : field?.example || "value"
                          }
                          onChange={(e) => setCondition(g, c, { value: e.target.value })}
                          className={inputClass(byField("value").length > 0)}
                        />
                      )}
                      <IssueText issues={[...byField("value"), ...own.filter((i) => i.field === null)]} />
                    </div>
                    <div className="flex items-start justify-end md:col-span-1">
                      <button
                        type="button"
                        disabled={disabled}
                        onClick={() => setGroup(g, { ...group, conditions: group.conditions.filter((_, i) => i !== c) })}
                        className="rounded p-2 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
                        aria-label="Remove condition"
                      >
                        <Trash2 className="h-3.5 w-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
              <button
                type="button"
                disabled={disabled || triggers.length === 0 || group.conditions.length >= limits.conditions_per_group}
                onClick={() => addCondition(g)}
                className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-bold text-primary hover:bg-primary/10 disabled:opacity-50"
              >
                <Plus className="h-3.5 w-3.5" /> Condition
              </button>
            </div>
          </div>
        ))}

        <button
          type="button"
          disabled={disabled || triggers.length === 0 || groups.length >= limits.groups}
          onClick={addGroup}
          className="inline-flex items-center gap-1 rounded-md border border-dashed border-border px-3 py-1.5 text-xs font-bold text-muted-foreground hover:border-primary hover:text-primary disabled:opacity-50"
        >
          <Plus className="h-3.5 w-3.5" /> {groups.length === 0 ? "Add a condition group" : "Add another group"}
        </button>
        {triggers.length === 0 && (
          <p className="text-xs text-muted-foreground">Choose a trigger first; the fields you can test depend on it.</p>
        )}
      </div>
    </StepCard>
  );
};
'''

NEW_FRONTEND_FILES['src/components/automation/flow/ActionConfigForm.tsx'] = r'''/**
 * ARCH-37 — the configuration form for one action.
 *
 * ACTION_FORMS lists, per registered action type, the config keys the form
 * shows, in order. `verify_arch37.py` compares it with the server's action
 * registry and each action's schema: an action the server can run but the
 * console cannot configure, or a config key the form never shows, fails the
 * build. Controls are chosen from the key (resource pickers) or the JSON
 * schema the catalog serves (enums, numbers, lists, text).
 */

import React from "react";

import type { FlowAction, FlowCatalog, JsonSchemaProperty } from "@/types/automationFlow";
import {
  commonEventFields,
  humanize,
  schemaProperties,
  type CardIssue,
} from "./flowModel";
import { IssueText, inputClass } from "./StepCard";

export const ACTION_FORMS: Readonly<Record<string, readonly string[]>> = {
  "webhook.send": ["endpoint_id", "include_fields"],
  "redaction.start": ["profile_key"],
  "review.escalate": ["reason"],
  "warehouse.export": ["destination_id", "datasets", "lookback_days", "debounce_minutes"],
  "notify.role": ["roles", "priority", "title", "message"],
  "autonomy.decide": ["score_source", "on_hold", "hold_reason"],
  "email.send": ["recipient", "subject", "body"],
  "work_item.mutate": ["target_field", "target_value"],
};

const LABELS: Readonly<Record<string, string>> = {
  endpoint_id: "Webhook endpoint",
  include_fields: "Document fields to include",
  profile_key: "Redaction profile",
  destination_id: "Warehouse destination",
  lookback_days: "Look back (days)",
  debounce_minutes: "At most once every (minutes)",
  score_source: "Score to decide on",
  on_hold: "When the document is held",
  hold_reason: "Reason shown to the reviewer",
  target_field: "Field to set",
  target_value: "New value",
};

const HELP: Readonly<Record<string, string>> = {
  endpoint_id: "Only active endpoints registered for this organization are listed.",
  include_fields: "Only scalar values are sent. Leave empty to send references only.",
  destination_id: "Registered by an organization owner. Exports are debounced per destination.",
  score_source: "Held when below the calibrated threshold, suspended, or sampled for audit.",
  on_hold: "Either way, the actions after this one are skipped.",
};

const enumOf = (prop: JsonSchemaProperty | undefined): readonly string[] | undefined =>
  prop?.enum ?? prop?.anyOf?.find((p) => p.enum)?.enum;

const optionLabel = (value: string): string => humanize(value.toLowerCase());

interface ActionConfigFormProps {
  readonly catalog: FlowCatalog;
  readonly action: FlowAction;
  readonly triggers: readonly string[];
  readonly config: Readonly<Record<string, unknown>>;
  readonly onChange: (config: Readonly<Record<string, unknown>>) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
  readonly idPrefix: string;
}

export const ActionConfigForm: React.FC<ActionConfigFormProps> = ({
  catalog, action, triggers, config, onChange, issues, disabled, idPrefix,
}) => {
  const properties = new Map(schemaProperties(action));
  const keys = ACTION_FORMS[action.type] ?? Array.from(properties.keys());
  const required = new Set(action.config_schema.required ?? []);
  const resources = catalog.resources;
  const documentKeys = catalog.document_fields.map((f) => f.key).filter((k) => /^[a-z0-9_]{1,64}(\.[a-z0-9_]{1,64})?$/.test(k));
  const variables = [
    ...catalog.template_variables,
    ...commonEventFields(catalog, triggers).map((f) => f.key),
    ...catalog.document_fields.filter((f) => !f.key.includes(".")).map((f) => `field.${f.key}`),
  ];

  const set = (key: string, value: unknown): void => onChange({ ...config, [key]: value });
  const text = (key: string): string => {
    const value = config[key];
    return typeof value === "string" || typeof value === "number" ? String(value) : "";
  };
  const list = (key: string): string[] => {
    const value = config[key];
    return Array.isArray(value) ? value.map(String) : [];
  };
  const toggleIn = (key: string, item: string): void => {
    const current = list(key);
    set(key, current.includes(item) ? current.filter((v) => v !== item) : [...current, item]);
  };

  const control = (key: string): React.ReactNode => {
    const prop = properties.get(key);
    const id = `${idPrefix}-${key}`;
    const invalid = issues.some((issue) => issue.field === key);
    const options = enumOf(prop);

    const select = (choices: readonly { value: string; label: string }[], empty: string): React.ReactNode => (
      <select id={id} disabled={disabled} value={text(key)} onChange={(e) => set(key, e.target.value)} className={inputClass(invalid)}>
        <option value="">{choices.length ? "Choose…" : empty}</option>
        {choices.map((choice) => (
          <option key={choice.value} value={choice.value}>{choice.label}</option>
        ))}
      </select>
    );

    const checks = (choices: readonly string[], empty: string): React.ReactNode =>
      choices.length === 0 ? (
        <p className="text-xs text-muted-foreground">{empty}</p>
      ) : (
        <div id={id} role="group" className="flex flex-wrap gap-1.5">
          {choices.map((choice) => {
            const on = list(key).includes(choice);
            return (
              <button
                key={choice}
                type="button"
                disabled={disabled}
                aria-pressed={on}
                onClick={() => toggleIn(key, choice)}
                className={`rounded-full border px-2.5 py-0.5 text-xs font-semibold ${
                  on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"
                }`}
              >
                {choice.includes("_") || choice === choice.toUpperCase() ? optionLabel(choice) : choice}
              </button>
            );
          })}
        </div>
      );

    switch (key) {
      case "endpoint_id":
        return select(
          resources.webhook_endpoints.map((e) => ({ value: e.id, label: `${e.label}${e.host && e.host !== e.label ? ` (${e.host})` : ""}` })),
          "No active webhook endpoints — register one under Settings › Webhooks",
        );
      case "destination_id":
        return select(
          resources.warehouse_destinations.map((d) => ({ value: d.id, label: `${d.label} · ${d.kind}` })),
          "No active destinations, or the warehouse add-on is not active",
        );
      case "profile_key":
        return select(resources.redaction_profiles.map((p) => ({ value: p, label: optionLabel(p) })), "No profiles");
      case "roles":
        return checks(resources.organization_roles, "No roles available.");
      case "datasets":
        return checks(resources.export_datasets, "No datasets available.");
      case "include_fields":
        return checks(documentKeys, "No extracted fields seen in this workspace yet.");
      case "target_field":
        return select(
          [
            ...resources.mutable_fields.map((f) => ({ value: f, label: humanize(f) })),
            ...documentKeys
              .filter((k) => !k.includes("."))
              .map((k) => ({ value: `extracted_entities.${k}`, label: `Extracted: ${humanize(k)}` })),
          ],
          "No fields",
        );
      default:
        break;
    }

    if (options) {
      return select(options.map((o) => ({ value: o, label: optionLabel(o) })), "—");
    }
    if (prop?.type === "integer" || prop?.type === "number") {
      return (
        <input
          id={id}
          type="number"
          disabled={disabled}
          min={prop.minimum}
          max={prop.maximum}
          value={text(key)}
          onChange={(e) => set(key, e.target.value === "" ? "" : Number(e.target.value))}
          className={inputClass(invalid)}
        />
      );
    }
    const isTemplate = action.template_fields.includes(key);
    const long = (prop?.maxLength ?? 0) > 500;
    return (
      <div className="space-y-1">
        {long ? (
          <textarea
            id={id}
            rows={5}
            disabled={disabled}
            maxLength={prop?.maxLength}
            value={text(key)}
            onChange={(e) => set(key, e.target.value)}
            className={`${inputClass(invalid)} font-mono text-xs`}
          />
        ) : (
          <input
            id={id}
            type={prop?.format === "email" ? "email" : "text"}
            disabled={disabled}
            maxLength={prop?.maxLength}
            value={text(key)}
            onChange={(e) => set(key, e.target.value)}
            className={inputClass(invalid)}
          />
        )}
        {isTemplate && (
          <select
            aria-label={`Insert a variable into ${humanize(key)}`}
            disabled={disabled}
            value=""
            onChange={(e) => {
              if (e.target.value) {set(key, `${text(key)}{{${e.target.value}}}`);}
            }}
            className="rounded-md border border-border bg-background px-2 py-1 text-[11px] text-muted-foreground"
          >
            <option value="">Insert variable…</option>
            {variables.map((v) => (
              <option key={v} value={v}>{`{{${v}}}`}</option>
            ))}
          </select>
        )}
      </div>
    );
  };

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
      {keys.map((key) => {
        const prop = properties.get(key);
        const wide = ["include_fields", "roles", "datasets", "body", "message", "title", "subject", "reason", "hold_reason"].includes(key);
        return (
          <div key={key} className={wide ? "md:col-span-2" : ""}>
            <label htmlFor={`${idPrefix}-${key}`} className="mb-1 block text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
              {LABELS[key] ?? prop?.title ?? humanize(key)}
              {required.has(key) && <span className="text-destructive"> *</span>}
            </label>
            {control(key)}
            {(HELP[key] ?? prop?.description) && (
              <p className="mt-1 text-[11px] text-muted-foreground">{HELP[key] ?? prop?.description}</p>
            )}
            <IssueText issues={issues.filter((issue) => issue.field === key)} />
          </div>
        );
      })}
    </div>
  );
};
'''

NEW_FRONTEND_FILES['src/components/automation/flow/ActionsCard.tsx'] = r'''/**
 * ARCH-37 — step 3 ("Then") and the optional "Otherwise" branch.
 *
 * An ordered list. Each action runs after the one above it succeeds; a failed
 * action, or calibrated autonomy holding the document, stops the ones below.
 * Actions the plan, the add-on or the author's role does not allow are shown
 * disabled with the reason; actions the selected trigger cannot run are
 * hidden from the picker and flagged if already present.
 */

import React, { useMemo, useState } from "react";
import { ArrowDown, ArrowUp, GitBranch, Lock, Play, Plus, Trash2 } from "lucide-react";

import type { FlowAction, FlowCatalog } from "@/types/automationFlow";
import { ActionConfigForm } from "./ActionConfigForm";
import {
  defaultConfig,
  excludedActions,
  findAction,
  uid,
  type ActionDraft,
  type CardIssue,
  type CardKey,
} from "./flowModel";
import { IssueText, StepCard } from "./StepCard";

interface ActionsCardProps {
  readonly catalog: FlowCatalog;
  readonly card: Extract<CardKey, "actions" | "else">;
  readonly step: number;
  readonly triggers: readonly string[];
  readonly actions: readonly ActionDraft[];
  readonly onChange: (next: readonly ActionDraft[]) => void;
  readonly issues: readonly CardIssue[];
  readonly disabled: boolean;
  readonly hasConditions: boolean;
}

export const ActionsCard: React.FC<ActionsCardProps> = ({
  catalog, card, step, triggers, actions, onChange, issues, disabled, hasConditions,
}) => {
  const [picking, setPicking] = useState<string>("");
  const excluded = excludedActions(catalog, triggers);
  const limit = catalog.limits.actions_per_branch;
  const isElse = card === "else";

  const grouped = useMemo(() => {
    const groups = new Map<string, FlowAction[]>();
    for (const action of catalog.actions) {
      if (excluded.has(action.type)) {continue;}
      const list = groups.get(action.category) ?? [];
      list.push(action);
      groups.set(action.category, list);
    }
    return Array.from(groups.entries());
  }, [catalog.actions, excluded]);

  const add = (type: string): void => {
    const action = findAction(catalog, type);
    if (!action || !action.available || actions.length >= limit) {return;}
    onChange([...actions, { uid: uid("action"), action_type: action.type, config: defaultConfig(action) }]);
    setPicking("");
  };

  const move = (index: number, delta: number): void => {
    const target = index + delta;
    if (target < 0 || target >= actions.length) {return;}
    const next = [...actions];
    const [item] = next.splice(index, 1);
    if (item) {next.splice(target, 0, item);}
    onChange(next);
  };

  const replace = (index: number, entry: ActionDraft): void =>
    onChange(actions.map((a, i) => (i === index ? entry : a)));

  if (isElse && !hasConditions && actions.length === 0) {
    return null;
  }

  return (
    <StepCard
      id={`flow-step-${card}`}
      step={step}
      title={isElse ? "Otherwise" : "Then"}
      subtitle={
        isElse
          ? "Runs instead when the conditions are not met."
          : "Runs in order. A failure, or a document held by calibrated autonomy, stops the actions after it."
      }
      icon={isElse ? <GitBranch className="h-4 w-4" /> : <Play className="h-4 w-4" />}
      issues={issues}
    >
      <ol className="space-y-3">
        {actions.map((entry, index) => {
          const action = findAction(catalog, entry.action_type);
          const own = issues.filter((issue) => issue.index === index);
          const loose = own.filter((issue) => issue.field === null);
          return (
            <li
              key={entry.uid}
              className={`rounded-lg border p-3 ${own.length ? "border-destructive/60" : "border-border"} bg-background`}
            >
              <div className="mb-3 flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex flex-wrap items-center gap-2 text-sm font-bold">
                    <span className="rounded bg-muted px-1.5 py-0.5 font-mono text-[10px] text-muted-foreground">#{index + 1}</span>
                    {action?.label ?? entry.action_type}
                    {action && <span className="text-[10px] font-semibold uppercase text-muted-foreground">{action.category}</span>}
                    {action && !action.available && (
                      <span className="inline-flex items-center gap-1 rounded bg-muted px-1.5 py-0.5 text-[10px] font-bold text-muted-foreground">
                        <Lock className="h-3 w-3" aria-hidden="true" /> {action.unavailable_reason}
                      </span>
                    )}
                  </p>
                  {action && <p className="mt-0.5 text-xs text-muted-foreground">{action.description}</p>}
                  <IssueText issues={loose} />
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button type="button" disabled={disabled || index === 0} onClick={() => move(index, -1)}
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-40" aria-label={`Move action ${index + 1} up`}>
                    <ArrowUp className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" disabled={disabled || index === actions.length - 1} onClick={() => move(index, 1)}
                    className="rounded p-1.5 text-muted-foreground hover:bg-muted disabled:opacity-40" aria-label={`Move action ${index + 1} down`}>
                    <ArrowDown className="h-3.5 w-3.5" />
                  </button>
                  <button type="button" disabled={disabled} onClick={() => onChange(actions.filter((_, i) => i !== index))}
                    className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive" aria-label={`Remove action ${index + 1}`}>
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>
              </div>
              {action ? (
                <ActionConfigForm
                  catalog={catalog}
                  action={action}
                  triggers={triggers}
                  config={entry.config}
                  onChange={(config) => replace(index, { ...entry, config })}
                  issues={own}
                  disabled={disabled}
                  idPrefix={`${card}-${entry.uid}`}
                />
              ) : (
                <p className="text-xs text-destructive">This action is no longer offered. Remove it to save the rule.</p>
              )}
            </li>
          );
        })}
      </ol>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <label htmlFor={`${card}-picker`} className="sr-only">Add an action</label>
        <select
          id={`${card}-picker`}
          value={picking}
          disabled={disabled || actions.length >= limit}
          onChange={(e) => {
            setPicking(e.target.value);
            add(e.target.value);
          }}
          className="rounded-lg border border-dashed border-border bg-background px-3 py-1.5 text-xs font-bold text-muted-foreground"
        >
          <option value="">{actions.length >= limit ? `Limit of ${limit} actions reached` : "+ Add an action…"}</option>
          {grouped.map(([category, list]) => (
            <optgroup key={category} label={category}>
              {list.map((action) => (
                <option key={action.type} value={action.type} disabled={!action.available}>
                  {action.label}{action.available ? "" : ` — ${action.unavailable_reason ?? "unavailable"}`}
                </option>
              ))}
            </optgroup>
          ))}
        </select>
        {actions.length === 0 && !isElse && (
          <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
            <Plus className="h-3 w-3" aria-hidden="true" /> A rule needs at least one action.
          </span>
        )}
      </div>
    </StepCard>
  );
};
'''

NEW_FRONTEND_FILES['src/components/automation/flow/SummaryRail.tsx'] = r'''/**
 * ARCH-37 — the builder's right rail: the rule in one sentence, what still
 * blocks saving (each item jumps to its card), and the keyboard shortcuts.
 */

import React from "react";
import { AlertTriangle, CheckCircle2, Keyboard, Loader2, Save } from "lucide-react";

import type { CardIssue, CardKey } from "./flowModel";

const CARD_TITLES: Readonly<Record<CardKey, string>> = {
  general: "Details",
  trigger: "When",
  conditions: "Only if",
  actions: "Then",
  else: "Otherwise",
};

const CARD_ANCHORS: Readonly<Record<CardKey, string>> = {
  general: "flow-step-general",
  trigger: "flow-step-trigger",
  conditions: "flow-step-conditions",
  actions: "flow-step-actions",
  else: "flow-step-else",
};

interface SummaryRailProps {
  readonly sentence: string;
  readonly issues: readonly CardIssue[];
  readonly dirty: boolean;
  readonly saving: boolean;
  readonly isEdit: boolean;
  readonly onSave: () => void;
}

export const SummaryRail: React.FC<SummaryRailProps> = ({ sentence, issues, dirty, saving, isEdit, onSave }) => (
  <aside className="space-y-4 lg:sticky lg:top-0" aria-label="Rule summary">
    <div className="rounded-xl border border-border bg-card p-4">
      <h3 className="text-[10px] font-black uppercase tracking-wider text-muted-foreground">This rule</h3>
      <p className="mt-2 text-sm leading-relaxed" aria-live="polite">{sentence}</p>
    </div>

    <div className="rounded-xl border border-border bg-card p-4">
      {issues.length === 0 ? (
        <p className="flex items-center gap-2 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-4 w-4" aria-hidden="true" /> Ready to save
        </p>
      ) : (
        <>
          <p className="flex items-center gap-2 text-xs font-bold text-destructive">
            <AlertTriangle className="h-4 w-4" aria-hidden="true" />
            {issues.length} {issues.length === 1 ? "thing" : "things"} to fix
          </p>
          <ul className="mt-2 space-y-1">
            {issues.slice(0, 8).map((issue, index) => (
              <li key={index}>
                <a
                  href={`#${CARD_ANCHORS[issue.card]}`}
                  onClick={(event) => {
                    event.preventDefault();
                    document.getElementById(CARD_ANCHORS[issue.card])?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                  className="block rounded px-1.5 py-1 text-xs hover:bg-muted"
                >
                  <span className="font-bold">
                    {CARD_TITLES[issue.card]}
                    {issue.index !== null ? ` #${issue.index + 1}` : ""}:
                  </span>{" "}
                  {issue.message}
                </a>
              </li>
            ))}
          </ul>
        </>
      )}
      <button
        type="button"
        onClick={onSave}
        disabled={saving}
        className="mt-3 inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-60"
      >
        {saving ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : <Save className="h-4 w-4" aria-hidden="true" />}
        {isEdit ? "Save changes" : "Create rule"}
      </button>
      {dirty && !saving && <p className="mt-2 text-center text-[11px] text-muted-foreground">Unsaved changes</p>}
    </div>

    <div className="rounded-xl border border-border bg-card p-4 text-[11px] text-muted-foreground">
      <p className="mb-1 flex items-center gap-1.5 font-bold uppercase tracking-wider">
        <Keyboard className="h-3.5 w-3.5" aria-hidden="true" /> Shortcuts
      </p>
      <p><kbd className="rounded border border-border px-1">Ctrl/⌘ S</kbd> save</p>
      <p><kbd className="rounded border border-border px-1">Ctrl/⌘ Enter</kbd> save</p>
      <p><kbd className="rounded border border-border px-1">Esc</kbd> close (asks if unsaved)</p>
    </div>
  </aside>
);
'''


# Retired files and the sha256 of the ARCH-39 text each must still have.
DELETED_FRONTEND_FILES: dict[str, str] = {
    'src/pages/Automation/RuleEditor.tsx': '6281dbff2ab507c04c632693b678b243deb4c7935e9fb029c26ca9538d8d1d05',
    'src/pages/Automation/RuleForm.tsx': 'd4d6e424c01acf46252f600e94467d17da44910fc9f2b4168409c1df3d80f1f8',
    'src/schemas/automation.ts': '74af2c827445d60714afbf0b16929d477fa8e08855173514e648b85247711daa',
    'src/constants/automationFields.ts': '7ab33162ef26bd766d806721bdf019d79b7db3e78940d1e4e9661227f310668a',
}

# ===========================================================================
# Anchored patches (generated from the diff against ARCH 39 DONE)
# ===========================================================================

PATCHES: list[tuple[str, str, list[tuple[str, str]]]] = []
PATCHES.append(('app/api/v1/automation.py', 'ARCH37-S1:flow-create', [
    (r'''
    id: uuid.UUID
    node_key: Optional[str] = None
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt: int = 0
    error: Optional[str] = None


class AutomationExecutionResponse(BaseModel):
''',
     r'''
    id: uuid.UUID
    node_key: Optional[str] = None
    node_type: Optional[str] = None
    sequence: int = 0
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    attempt: int = 0
    error: Optional[str] = None
    # ARCH37-S1:node-run-response
    action_type: Optional[str] = None
    external_ref: Optional[str] = None
    duration_ms: Optional[int] = None
    outcome: Optional[str] = None


# ==========================================================================
# ARCH-37 flow builder
# ==========================================================================


def _flow_issues(exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail=list(getattr(exc, "issues", [])) or [
            {"loc": ["body"], "msg": str(exc), "type": "value_error"}
        ],
    )


def _audit_rule(
    db: Session,
    *,
    context: Any,
    rule: AutomationRule,
    action: Any,
    details: dict[str, Any],
) -> None:
    from app.models.audit_log import AuditOutcome, AuditResourceType
    from app.services import audit_service

    audit_service.record(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        resource_type=AuditResourceType.AUTOMATION_RULE,
        resource_id=rule.id,
        action=action,
        outcome=AuditOutcome.ALLOWED,
        details={"rule_name": rule.name, **details},
    )


def _view(db: Session, rule: AutomationRule) -> dict[str, Any]:
    from app.services.automation import flow_service, rule_triggers

    events = rule_triggers.event_types_of(db, rule_ids=[rule.id]).get(rule.id, [])
    return flow_service.rule_view(rule, events)


@router.get(
    "/catalog",
    summary="Flow builder catalog",
    response_description=(
        "Triggers, actions, operators, template variables, observed document "
        "fields and the resources actions may reference. The console renders "
        "this and holds no trigger or action list of its own."
    ),
)
async def get_catalog(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> dict[str, Any]:
    from app.services.automation import catalog_service

    return catalog_service.build(db, context=context)


@router.get(
    "/executions/{execution_id}/nodes",
    response_model=list[AutomationNodeRunResponse],
    summary="Node runs of one execution",
)
async def list_execution_nodes(
    execution_id: uuid.UUID,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> list[AutomationNodeRunResponse]:
    from app.models.automation_execution import AutomationNodeRun

    owned = db.execute(
        select(AutomationExecution.id).where(
            AutomationExecution.id == execution_id,
            AutomationExecution.workspace_id == context.workspace_id,
        )
    ).scalar_one_or_none()
    if owned is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Execution not found.")
    rows = db.execute(
        select(AutomationNodeRun)
        .where(AutomationNodeRun.execution_id == execution_id)
        .order_by(AutomationNodeRun.sequence.asc())
    ).scalars().all()
    return [
        AutomationNodeRunResponse(
            id=row.id,
            node_key=row.node_key,
            node_type=row.node_type,
            sequence=row.sequence,
            status=getattr(row.status, "value", str(row.status)),
            started_at=row.started_at,
            completed_at=row.completed_at,
            error=row.error,
            action_type=row.action_type,
            external_ref=row.external_ref,
            duration_ms=row.duration_ms,
            outcome=(row.details or {}).get("outcome") if isinstance(row.details, dict) else None,
        )
        for row in rows
    ]


class AutomationExecutionResponse(BaseModel):
'''),
    (r'''    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    rule = crud.create_automation_rule(
        db,
        workspace_id=context.workspace_id,
        obj_in=rule_in,
        created_by_user_id=context.user_id,
    )
    logger.info(f"User {context.user_id} created Automation Rule '{rule.name}' [ID: {rule.id}] in workspace {context.workspace_id}")
    return rule


@router.get(
''',
     r'''    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin)
) -> Any:
    # ARCH37-S1:flow-create. Validated as a whole rule, stored with its
    # trigger rows, audited, committed once.
    from app.models.audit_log import AuditAction
    from app.services.automation import catalog_service, flow_service, rule_triggers

    authoring = catalog_service.authoring_for(db, context=context)
    try:
        normalised = flow_service.normalise(rule_in.model_dump(mode="json"), authoring)
    except flow_service.FlowValidationError as exc:
        raise _flow_issues(exc) from exc

    rule = AutomationRule(
        name=rule_in.name,
        priority=rule_in.priority,
        event=normalised.event,
        conditions=normalised.conditions,
        logic_operator=normalised.logic_operator,
        actions=normalised.actions,
        is_active=rule_in.is_active,
        on_error=normalised.on_error,
        flow_spec=normalised.flow_spec,
        workspace_id=context.workspace_id,
        created_by_user_id=context.user_id,
    )
    db.add(rule)
    db.flush([rule])
    rule_triggers.set_event_types(db, rule=rule, event_types=normalised.event_types)
    _audit_rule(
        db, context=context, rule=rule, action=AuditAction.CREATED,
        details={"triggers": normalised.trigger_keys, "actions": [a["action_type"] for a in normalised.actions]},
    )
    db.commit()
    db.refresh(rule)
    logger.info(f"User {context.user_id} created Automation Rule '{rule.name}' [ID: {rule.id}] in workspace {context.workspace_id}")
    return _view(db, rule)


@router.get(
'''),
    (r'''    skip: int = Query(0, ge=0, description="The number of rules to skip for pagination."),
    limit: int = Query(100, ge=1, le=100, description="The maximum number of rules to return.")
) -> Any:
    rules = crud.list_automation_rules(db, workspace_id=context.workspace_id, skip=skip, limit=limit)
    return rules


# ---------------------------------------------------------------------------
''',
     r'''    skip: int = Query(0, ge=0, description="The number of rules to skip for pagination."),
    limit: int = Query(100, ge=1, le=100, description="The maximum number of rules to return.")
) -> Any:
    from app.services.automation import flow_service, rule_triggers

    rules = crud.list_automation_rules(db, workspace_id=context.workspace_id, skip=skip, limit=limit)
    events = rule_triggers.event_types_of(db, rule_ids=[r.id for r in rules])
    return [flow_service.rule_view(r, events.get(r.id, [])) for r in rules]


# ---------------------------------------------------------------------------
'''),
    (r'''            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )
    return rule


@router.patch(
''',
     r'''            status_code=status.HTTP_404_NOT_FOUND,
            detail="Automation rule not found or you do not have permission to access it."
        )
    return _view(db, rule)


@router.patch(
'''),
    (r'''            detail="Automation rule not found or you do not have permission to access it."
        )
    
    updated_rule = crud.update_automation_rule(db, db_obj=rule, obj_in=rule_in)
    logger.info(f"User {context.user_id} updated Automation Rule [ID: {rule_id}] inside workspace {context.workspace_id}")
    return updated_rule


@router.delete(
''',
     r'''            detail="Automation rule not found or you do not have permission to access it."
        )
    
    # ARCH37-S1:flow-update. A partial update is merged into the stored rule
    # and the WHOLE result is validated, so a toggle cannot re-enable a rule
    # whose actions the tenant's plan no longer includes... except that a
    # pure enable/disable/rename is always allowed: turning a rule OFF must
    # never be refused.
    from app.models.audit_log import AuditAction
    from app.services.automation import catalog_service, flow_service, rule_triggers

    changes = rule_in.model_dump(exclude_unset=True, mode="json")
    structural = set(changes) - {"is_active", "name", "priority"}
    enabling = changes.get("is_active") is True and not rule.is_active

    if structural or enabling:
        events = rule_triggers.event_types_of(db, rule_ids=[rule.id]).get(rule.id, [])
        merged = flow_service.payload_from_rule(rule, events)
        flow_keys = {"triggers", "condition_groups", "groups_operator", "else_actions"}
        if flow_keys & set(changes) and "triggers" not in merged:
            # Upgrading an ARCH-13 rule to the flow shape.
            merged = {
                "triggers": changes.get("triggers") or [],
                "condition_groups": [
                    {"logic_operator": rule.logic_operator, "conditions": list(rule.conditions or [])}
                ],
                "actions": list(rule.actions or []),
                "on_error": rule.on_error,
            }
        for key in ("event", "conditions", "logic_operator", "actions", "on_error", *flow_keys):
            if key in changes and changes[key] is not None:
                merged[key] = changes[key]
        try:
            normalised = flow_service.normalise(
                merged, catalog_service.authoring_for(db, context=context)
            )
        except flow_service.FlowValidationError as exc:
            raise _flow_issues(exc) from exc
        if structural:
            rule.event = normalised.event
            rule.conditions = normalised.conditions
            rule.logic_operator = normalised.logic_operator
            rule.actions = normalised.actions
            rule.on_error = normalised.on_error
            rule.flow_spec = normalised.flow_spec
            rule_triggers.set_event_types(db, rule=rule, event_types=normalised.event_types)

    for key in ("name", "priority", "is_active"):
        if key in changes and changes[key] is not None:
            setattr(rule, key, changes[key])

    action = AuditAction.UPDATED
    if set(changes) == {"is_active"}:
        action = AuditAction.ENABLED if changes["is_active"] else AuditAction.DISABLED
    _audit_rule(db, context=context, rule=rule, action=action, details={"fields": sorted(changes)})
    db.commit()
    db.refresh(rule)
    logger.info(f"User {context.user_id} updated Automation Rule [ID: {rule_id}] inside workspace {context.workspace_id}")
    return _view(db, rule)


@router.delete(
'''),
    (r'''            detail="Automation rule not found or you do not have permission to access it."
        )
    
    crud.delete_automation_rule(db, db_obj=rule)
    logger.info(f"User {context.user_id} deleted Automation Rule [ID: {rule_id}] in workspace {context.workspace_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
''',
     r'''            detail="Automation rule not found or you do not have permission to access it."
        )
    
    from app.models.audit_log import AuditAction

    _audit_rule(db, context=context, rule=rule, action=AuditAction.DELETED, details={})
    crud.delete_automation_rule(db, db_obj=rule)
    logger.info(f"User {context.user_id} deleted Automation Rule [ID: {rule_id}] in workspace {context.workspace_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
'''),
]))

PATCHES.append(('app/api/v1/work_items.py', 'ARCH37-S1:work-item-reprocessed', [
    (r'''        idempotency_key=f"{document_intake_service.OCR_JOB_TYPE}:{work_item.id}:{uuid.uuid4().hex[:8]}",
        max_attempts=settings.OCR_JOB_MAX_ATTEMPTS,
    )
    db.commit()

    return {
''',
     r'''        idempotency_key=f"{document_intake_service.OCR_JOB_TYPE}:{work_item.id}:{uuid.uuid4().hex[:8]}",
        max_attempts=settings.OCR_JOB_MAX_ATTEMPTS,
    )
    # ARCH37-S1:work-item-reprocessed
    from app.services import outbox_service

    outbox_service.emit_trigger(
        db,
        organization_id=context.organization_id,
        workspace_id=context.workspace_id,
        event_type="trigger.work_item.reprocessed",
        resource_id=work_item.id,
        payload={
            "work_item_id": str(work_item.id),
            "original_filename": work_item.original_filename,
            "requested_by_user_id": str(context.user_id),
        },
        idempotency_key=f"trigger.work_item.reprocessed:{work_item.id}:{job.id}",
    )
    db.commit()

    return {
'''),
]))

PATCHES.append(('app/core/automation_events.py', 'ARCH37-S1:trigger-vocabulary', [
    (r'''from typing import Final, FrozenSet

from app.core.webhook_events import WEBHOOK_EVENT_TYPES

#: Visibility discriminator values. Mirrors the DB CHECK constraint.
VISIBILITY_PUBLIC: Final[str] = "PUBLIC"
''',
     r'''from typing import Final, FrozenSet

from app.core.webhook_events import WEBHOOK_EVENT_TYPES

#: ARCH37-S1:trigger-prefix. The namespace every flow-builder trigger lives in.
TRIGGER_PREFIX: Final[str] = "trigger."

#: Internal twins of public events. The public name is the part after the
#: prefix; `_assert_twins_have_public_sources` proves each one exists.
TRIGGER_TWIN_EVENT_TYPES: Final[tuple[str, ...]] = (
    "trigger.document.completed",
    "trigger.document.failed",
    "trigger.procurement.completed",
    "trigger.procurement.approved",
    "trigger.procurement.disputed",
    "trigger.anomaly.detected",
)

#: Internal trigger events with no public counterpart.
#:
#: `trigger.batch.completed` is reserved here (and in the database CHECK) so
#: ARCH-38 needs no vocabulary migration. It is deliberately NOT in the trigger
#: catalog until ARCH-38 ships an emitter: a trigger nothing emits is the
#: defect ARCH-37 exists to remove, and `verify_arch37.py` fails on one.
TRIGGER_NATIVE_EVENT_TYPES: Final[tuple[str, ...]] = (
    "trigger.work_item.created",
    "trigger.work_item.reprocessed",
    "trigger.assertion.held",
    "trigger.redaction.completed",
    "trigger.batch.completed",
)

#: Internal events that existed before ARCH-37 and that rules may listen to.
#: `automation_rule_triggers` accepts exactly these plus the `trigger.` names.
LEGACY_RULE_EVENT_TYPES: Final[tuple[str, ...]] = (
    "work_item.enriched",
    "work_item.verification_completed",
    "work_item.field_changed",
)

#: Visibility discriminator values. Mirrors the DB CHECK constraint.
VISIBILITY_PUBLIC: Final[str] = "PUBLIC"
'''),
    (r'''        "identity.jit_cap_reached",
        # Emitted when an enterprise IdP configuration is created or activated.
        "identity.idp_config_changed",
    }
)

''',
     r'''        "identity.jit_cap_reached",
        # Emitted when an enterprise IdP configuration is created or activated.
        "identity.idp_config_changed",

        # --- ARCH37-S1:trigger-vocabulary ---
        # The flow builder's trigger events. Every name lives under the
        # reserved `trigger.` namespace, so none of them can ever be made
        # PUBLIC (see INTERNAL_ONLY_PREFIXES below).
        #
        # Twins: written by `outbox_service.emit_public_with_twin` in the
        # SAME transaction as the public event whose name follows the prefix.
        # The public row goes to customer endpoints; the twin goes to the
        # automation engine. ARCH-13 F1 keeps those two audiences apart, and a
        # twin is how a public state change reaches a rule without breaking it.
        *TRIGGER_TWIN_EVENT_TYPES,
        # State changes with no public counterpart, emitted by
        # `outbox_service.emit_trigger` where the state changes.
        *TRIGGER_NATIVE_EVENT_TYPES,
    }
)

'''),
    (r'''    "automation.",
    "billing.seat_",
    "identity.",
)


''',
     r'''    "automation.",
    "billing.seat_",
    "identity.",
    # ARCH37-S1:trigger-prefix
    TRIGGER_PREFIX,
)


'''),
    (r'''_assert_vocabularies_disjoint()


def is_internal(event_type: str) -> bool:
    return event_type in INTERNAL_EVENT_TYPES

''',
     r'''_assert_vocabularies_disjoint()


def twin_of(public_event_type: str) -> str:
    """The internal twin name for a public event type."""
    return f"{TRIGGER_PREFIX}{public_event_type}"


def _assert_twins_have_public_sources() -> None:
    """Every twin names a real public event, and every trigger is reserved."""
    orphans = [
        twin
        for twin in TRIGGER_TWIN_EVENT_TYPES
        if twin[len(TRIGGER_PREFIX):] not in WEBHOOK_EVENT_TYPES
    ]
    if orphans:
        raise RuntimeError(
            "ARCH-37 twin(s) with no public source event: "
            f"{', '.join(orphans)}. A twin is written beside its public event; "
            "one with no public event is never written."
        )
    misnamed = [
        name
        for name in (*TRIGGER_TWIN_EVENT_TYPES, *TRIGGER_NATIVE_EVENT_TYPES)
        if not name.startswith(TRIGGER_PREFIX)
    ]
    if misnamed:
        raise RuntimeError(
            f"ARCH-37 trigger event(s) outside the {TRIGGER_PREFIX!r} namespace: "
            f"{', '.join(misnamed)}."
        )


_assert_twins_have_public_sources()


def is_internal(event_type: str) -> bool:
    return event_type in INTERNAL_EVENT_TYPES

'''),
    (r'''__all__ = [
    "INTERNAL_EVENT_TYPES",
    "INTERNAL_ONLY_PREFIXES",
    "VISIBILITIES",
    "VISIBILITY_INTERNAL",
    "VISIBILITY_PUBLIC",
''',
     r'''__all__ = [
    "INTERNAL_EVENT_TYPES",
    "INTERNAL_ONLY_PREFIXES",
    "LEGACY_RULE_EVENT_TYPES",
    "TRIGGER_NATIVE_EVENT_TYPES",
    "TRIGGER_PREFIX",
    "TRIGGER_TWIN_EVENT_TYPES",
    "twin_of",
    "VISIBILITIES",
    "VISIBILITY_INTERNAL",
    "VISIBILITY_PUBLIC",
'''),
]))

PATCHES.append(('app/core/webhook_events.py', 'ARCH37-S1:workflow-triggered', [
    (r'''        # webhook would send contract language to whatever URL a tenant
        # configured; the API serves it under the capability gate.
        "anomaly.detected",
    }
)

''',
     r'''        # webhook would send contract language to whatever URL a tenant
        # configured; the API serves it under the capability gate.
        "anomaly.detected",
        # ARCH37-S1:workflow-triggered. The one event a customer endpoint
        # receives because a tenant-authored rule chose that endpoint
        # (`webhook.send`). Never fanned out from the outbox: the action writes
        # the delivery row for the single endpoint the rule names, and the
        # ARCH-09 delivery loop signs and retries it like any other.
        "workflow.triggered",
    }
)

'''),
]))

PATCHES.append(('app/models/__init__.py', 'ARCH37-S1:models-registered', [
    (r'''    TERMINAL_JOB_STATUSES,
)
from app.models.automation import AutomationRule, AutomationLog
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
''',
     r'''    TERMINAL_JOB_STATUSES,
)
from app.models.automation import AutomationRule, AutomationLog
# ARCH37-S1:models-registered
from app.models.automation_trigger import AutomationRuleTrigger
from app.models.automation_execution import (
    AutomationExecution,
    AutomationExecutionStatus,
'''),
    (r'''    "CLAIMABLE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "AutomationRule",
    "AutomationLog",
    "Notification",
    "NotificationDelivery",
''',
     r'''    "CLAIMABLE_JOB_STATUSES",
    "TERMINAL_JOB_STATUSES",
    "AutomationRule",
    "AutomationRuleTrigger",
    "AutomationLog",
    "Notification",
    "NotificationDelivery",
'''),
]))

PATCHES.append(('app/models/audit_log.py', 'ARCH37-S1:audit-automation-rule', [
    (r'''    REV_SHARE_LEDGER = "REV_SHARE_LEDGER"
    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"
    PROCUREMENT_CASE = "PROCUREMENT_CASE"
    PROCUREMENT_TOLERANCE_POLICY = "PROCUREMENT_TOLERANCE_POLICY"


''',
     r'''    REV_SHARE_LEDGER = "REV_SHARE_LEDGER"
    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"
    PROCUREMENT_CASE = "PROCUREMENT_CASE"
    # ARCH37-S1:audit-automation-rule. Added to the PostgreSQL type by
    # arch37_step0_flow_vocabulary.
    AUTOMATION_RULE = "AUTOMATION_RULE"
    PROCUREMENT_TOLERANCE_POLICY = "PROCUREMENT_TOLERANCE_POLICY"


'''),
]))

PATCHES.append(('app/models/automation.py', 'ARCH37-S1:flow-spec', [
    (r'''
if TYPE_CHECKING:
    from app.models.automation_graph import AutomationNode
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace
''',
     r'''
if TYPE_CHECKING:
    from app.models.automation_graph import AutomationNode
    from app.models.automation_trigger import AutomationRuleTrigger
    from app.models.user import User
    from app.models.work_item import WorkItem
    from app.models.workspace import Workspace
'''),
    (r'''        CheckConstraint(
            "budget_cost_micros IS NULL OR budget_cost_micros >= 0",
            name="ck_automation_rules_budget_non_negative",
        ),
    )

''',
     r'''        CheckConstraint(
            "budget_cost_micros IS NULL OR budget_cost_micros >= 0",
            name="ck_automation_rules_budget_non_negative",
        ),
        # ARCH37-S1:flow-spec
        CheckConstraint(
            "flow_spec IS NULL OR jsonb_typeof(flow_spec) = 'object'",
            name="ck_automation_rules_flow_spec_is_object",
        ),
    )

'''),
    (r'''        BigInteger, nullable=True
    )

    workspace: Mapped[Workspace] = relationship("Workspace")

    created_by: Mapped[Union[User, None]] = relationship("User")
''',
     r'''        BigInteger, nullable=True
    )

    # ---- ARCH-37 ------------------------------------------------------
    #: The step builder's document: condition groups, the operator between
    #: them, and the "otherwise" actions. NULL for rules written before
    #: ARCH-37, which `graph_service.flatten_legacy_rule` runs unchanged.
    flow_spec: Mapped[Union[dict[str, Any], None]] = mapped_column(
        JSONB, nullable=True
    )

    trigger_rows: Mapped[list["AutomationRuleTrigger"]] = relationship(
        "AutomationRuleTrigger",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
        order_by="AutomationRuleTrigger.event_type",
    )

    @property
    def trigger_event_types(self) -> list[str]:
        return [row.event_type for row in self.trigger_rows]

    workspace: Mapped[Workspace] = relationship("Workspace")

    created_by: Mapped[Union[User, None]] = relationship("User")
'''),
]))

PATCHES.append(('app/models/automation_execution.py', 'ARCH37-S1:node-run-facts', [
    (r'''            "output_digest IS NULL OR output_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_output_digest_prefixed",
        ),
        UniqueConstraint(
            "execution_id",
            "sequence",
''',
     r'''            "output_digest IS NULL OR output_digest LIKE 'sha256:%'",
            name="ck_automation_node_runs_output_digest_prefixed",
        ),
        # ARCH37-S1:node-run-facts
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_automation_node_runs_duration_non_negative",
        ),
        UniqueConstraint(
            "execution_id",
            "sequence",
'''),
    (r'''        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    execution: Mapped[AutomationExecution] = relationship(
        "AutomationExecution", back_populates="node_runs"
    )
''',
     r'''        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )

    #: ARCH-37. What an action node did, the id of what it created (a webhook
    #: delivery, a redaction job, a verification) and how long it took.
    action_type: Mapped[Optional[str]] = mapped_column(String(48), nullable=True)
    external_ref: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    execution: Mapped[AutomationExecution] = relationship(
        "AutomationExecution", back_populates="node_runs"
    )
'''),
]))

PATCHES.append(('app/models/automation_graph.py', 'ARCH37-S1:action-has-type', [
    (r'''        CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_automation_nodes_config_is_object",
        ),
        CheckConstraint(
            "topological_order >= 0",
''',
     r'''        CheckConstraint(
            "jsonb_typeof(config) = 'object'",
            name="ck_automation_nodes_config_is_object",
        ),
        # ARCH37-S1:action-has-type
        CheckConstraint(
            "node_type <> 'action' OR config ? 'action_type'",
            name="ck_automation_nodes_action_has_type",
        ),
        CheckConstraint(
            "topological_order >= 0",
'''),
]))

PATCHES.append(('app/schemas/automation.py', 'ARCH37-S1:rule-response', [
    (r'''
import uuid
from datetime import datetime
from typing import Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

''',
     r'''
import uuid
from datetime import datetime
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

'''),
    (r'''        return value.strip() if isinstance(value, str) else value


class AutomationRuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    priority: int = Field(default=100, ge=1)
''',
     r'''        return value.strip() if isinstance(value, str) else value


class AutomationConditionGroup(BaseModel):
    """ARCH37-S1:condition-group-schema."""

    logic_operator: Literal["AND", "OR"] = Field(default="AND")
    conditions: list[AutomationCondition] = Field(default_factory=list, max_length=20)

    @field_validator("logic_operator", mode="before")
    @classmethod
    def normalize_group_operator(cls, value: Any) -> Any:
        return value.upper().strip() if isinstance(value, str) else value


class AutomationRuleBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    priority: int = Field(default=100, ge=1)
'''),
    (r'''

class AutomationRuleCreate(AutomationRuleBase):
    pass


class AutomationRuleUpdate(BaseModel):
''',
     r'''

class AutomationRuleCreate(AutomationRuleBase):
    """A rule. Either the ARCH-13 shape (`event` + flat `conditions`) or the
    ARCH-37 flow shape (`triggers` + `condition_groups`)."""

    event: Optional[str] = Field(default=None, max_length=50)  # type: ignore[assignment]
    triggers: Optional[list[str]] = Field(default=None, max_length=4)
    condition_groups: Optional[list[AutomationConditionGroup]] = Field(default=None, max_length=10)
    groups_operator: Literal["AND", "OR"] = Field(default="AND")
    else_actions: list[AutomationAction] = Field(default_factory=list, max_length=10)
    on_error: Literal["HALT", "CONTINUE"] = Field(default="HALT")

    @model_validator(mode="after")
    def validate_conditions_presence(self) -> "AutomationRuleCreate":  # type: ignore[override]
        if self.triggers is not None:
            if not self.triggers:
                raise ValueError("Choose at least one trigger.")
            return self
        if not self.event:
            raise ValueError("A rule needs `triggers` (or the legacy `event`).")
        if not self.conditions:
            raise ValueError("At least one trigger condition must be specified.")
        return self


class AutomationRuleUpdate(BaseModel):
'''),
    (r'''    conditions: list[AutomationCondition] | None = None
    logic_operator: Literal["AND", "OR"] | None = None
    actions: list[AutomationAction] | None = Field(None, min_length=1)


class AutomationRuleResponse(AutomationRuleBase):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: Union[uuid.UUID, None] = None
    created_at: datetime
    updated_at: datetime

    graph_version: int = Field(
        default=0,
''',
     r'''    conditions: list[AutomationCondition] | None = None
    logic_operator: Literal["AND", "OR"] | None = None
    actions: list[AutomationAction] | None = Field(None, min_length=1)
    # ARCH-37
    triggers: list[str] | None = Field(None, min_length=1, max_length=4)
    condition_groups: list[AutomationConditionGroup] | None = Field(None, max_length=10)
    groups_operator: Literal["AND", "OR"] | None = None
    else_actions: list[AutomationAction] | None = Field(None, max_length=10)
    on_error: Literal["HALT", "CONTINUE"] | None = None


class AutomationRuleResponse(BaseModel):
    """ARCH37-S1:rule-response. Not derived from AutomationRuleBase: a flow
    rule may have no flat conditions, and a response must never fail the
    create-time validator."""

    id: uuid.UUID
    workspace_id: uuid.UUID
    created_by_user_id: Union[uuid.UUID, None] = None
    created_at: datetime
    updated_at: datetime
    name: str
    priority: int
    event: str
    is_active: bool
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    logic_operator: Literal["AND", "OR"] = "AND"
    actions: list[dict[str, Any]] = Field(default_factory=list)
    triggers: list[str] = Field(default_factory=list)
    trigger_events: list[str] = Field(default_factory=list)
    condition_groups: list[AutomationConditionGroup] = Field(default_factory=list)
    groups_operator: Literal["AND", "OR"] = "AND"
    else_actions: list[dict[str, Any]] = Field(default_factory=list)
    is_flow: bool = False

    graph_version: int = Field(
        default=0,
'''),
]))

PATCHES.append(('app/services/assertions/triage.py', 'ARCH37-S1:assertion-held', [
    (r'''    db.add(evaluation)
    db.flush()

    if meter:
        meter_evaluation(
            db,
''',
     r'''    db.add(evaluation)
    db.flush()

    # ARCH37-S1:assertion-held. A held clause is a trigger. Emitted after the
    # evaluation row exists, so the payload can name it.
    if decision.routed_to == vocab.ROUTE_TRIAGE:
        from app.services import outbox_service

        outbox_service.emit_trigger(
            db,
            organization_id=definition.organization_id,
            workspace_id=definition.workspace_id,
            event_type="trigger.assertion.held",
            resource_id=work_item_id,
            payload={
                "work_item_id": str(work_item_id),
                "evaluation_id": str(evaluation.id),
                "definition_id": str(definition.id),
                "family": definition.family,
                "verdict": evaluation_result.verdict,
                "raw_score": str(evaluation_result.raw_score),
                "verification_id": str(verification.id) if verification is not None else None,
            },
            idempotency_key=f"trigger.assertion.held:{evaluation.id}",
        )

    if meter:
        meter_evaluation(
            db,
'''),
]))

PATCHES.append(('app/services/automation/contracts.py', 'ARCH37-S1:list-options', [
    (r'''    target_field: Optional[str] = None
    target_value: Optional[str] = None
    options: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_node_config(cls, config: Any) -> ActionNodeConfig:
''',
     r'''    target_field: Optional[str] = None
    target_value: Optional[str] = None
    options: tuple[tuple[str, str], ...] = ()
    #: ARCH37-S1:list-options. Lists of scalars the author wrote (roles,
    #: datasets, field names). Before ARCH-37 they were dropped, because no
    #: action took one.
    list_options: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @classmethod
    def from_node_config(cls, config: Any) -> ActionNodeConfig:
'''),
    (r'''            return str(value).strip() if isinstance(value, (str, int, float)) else None

        reserved = {"recipient", "target_field", "target_value", "action_type"}
        options = tuple(
            (str(k), str(v))
            for k, v in sorted(inner.items())
            if k not in reserved and isinstance(v, (str, int, float, bool))
        )
        return cls(
            action_type=str(raw.get("action_type") or inner.get("action_type") or "").strip().lower(),
''',
     r'''            return str(value).strip() if isinstance(value, (str, int, float)) else None

        reserved = {"recipient", "target_field", "target_value", "action_type"}

        def scalar(value: Any) -> str:
            if isinstance(value, bool):
                return "true" if value else "false"
            return str(value)

        options = tuple(
            (str(k), scalar(v))
            for k, v in sorted(inner.items())
            if k not in reserved and isinstance(v, (str, int, float, bool))
        )
        list_options = tuple(
            (str(k), tuple(scalar(item) for item in v))
            for k, v in sorted(inner.items())
            if k not in reserved
            and isinstance(v, (list, tuple))
            and all(isinstance(item, (str, int, float, bool)) for item in v)
        )
        return cls(
            action_type=str(raw.get("action_type") or inner.get("action_type") or "").strip().lower(),
'''),
    (r'''            target_field=text("target_field"),
            target_value=text("target_value"),
            options=options,
        )

    @property
    def authored_values(self) -> frozenset[str]:
        values = [self.recipient, self.target_field, self.target_value]
        values.extend(value for _, value in self.options)
        return frozenset(
            str(v).strip().lower() for v in values if v and str(v).strip()
        )
''',
     r'''            target_field=text("target_field"),
            target_value=text("target_value"),
            options=options,
            list_options=list_options,
        )

    def authored_parameters(self) -> dict[str, Any]:
        """Everything the author wrote, keyed as the action's schema expects."""
        values: dict[str, Any] = {k: v for k, v in self.options}
        values.update({k: list(v) for k, v in self.list_options})
        for name in ("recipient", "target_field", "target_value"):
            value = getattr(self, name)
            if value is not None:
                values[name] = value
        return values

    @property
    def authored_values(self) -> frozenset[str]:
        values = [self.recipient, self.target_field, self.target_value]
        values.extend(value for _, value in self.options)
        values.extend(item for _, items in self.list_options for item in items)
        return frozenset(
            str(v).strip().lower() for v in values if v and str(v).strip()
        )
'''),
    (r'''    target_field: Optional[str] = None
    target_value: Optional[str] = None
    rationale: tuple[str, ...] = field(default_factory=tuple)

    def assert_no_document_derived_values(
        self,
''',
     r'''    target_field: Optional[str] = None
    target_value: Optional[str] = None
    rationale: tuple[str, ...] = field(default_factory=tuple)
    #: ARCH-37. The action's own configuration, copied from the author's
    #: config by a selector and checked below like every other value.
    parameters: tuple[tuple[str, str], ...] = ()
    list_parameters: tuple[tuple[str, tuple[str, ...]], ...] = ()

    def parameter_dict(self) -> dict[str, Any]:
        values: dict[str, Any] = {k: v for k, v in self.parameters}
        values.update({k: list(v) for k, v in self.list_parameters})
        for name in ("recipient", "target_field", "target_value"):
            value = getattr(self, name)
            if value is not None:
                values.setdefault(name, value)
        return values

    def assert_no_document_derived_values(
        self,
'''),
    (r'''        authored = config.authored_values
        derived = facts.document_derived_strings

        for name, value in (
            ("recipient", self.recipient),
            ("target_field", self.target_field),
            ("target_value", self.target_value),
        ):
            if value is None:
                continue
            normalised = str(value).strip().lower()
''',
     r'''        authored = config.authored_values
        derived = facts.document_derived_strings

        checked: list[tuple[str, Optional[str]]] = [
            ("recipient", self.recipient),
            ("target_field", self.target_field),
            ("target_value", self.target_value),
        ]
        checked.extend((f"parameters.{k}", v) for k, v in self.parameters)
        checked.extend(
            (f"list_parameters.{k}", item)
            for k, items in self.list_parameters
            for item in items
        )
        for name, value in checked:
            if value is None:
                continue
            normalised = str(value).strip().lower()
'''),
]))

PATCHES.append(('app/services/automation/executor.py', 'ARCH37-S1:registry-dispatch', [
    (r'''import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
''',
     r'''import hashlib
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
'''),
    (r'''

def _evaluate_condition_node(state: _WalkState, config: dict[str, Any]) -> bool:
    from app.services.automation_service import _evaluate_rule_conditions

    if state.work_item is None:
        return False

    class _Adapter:
        id = state.rule.id
        conditions = config.get("conditions") or []
        logic_operator = config.get("logic_operator", "AND")

    return bool(_evaluate_rule_conditions(_Adapter(), state.work_item))


def _tenant_scope_for(state: _WalkState) -> TenantScope:
''',
     r'''

def _evaluate_condition_node(state: _WalkState, config: dict[str, Any]) -> bool:
    # ARCH37-S1:condition-groups. Grouped conditions, and `event.<key>` paths
    # that read the trigger's payload. A flat ARCH-13 config evaluates exactly
    # as before, except that an event field no longer needs a document.
    from app.services.automation import conditions

    payload = getattr(state.trigger_event, "payload", None)
    if state.work_item is None and "groups" not in config:
        flat = config.get("conditions") or []
        if not any(
            str((c or {}).get("field", "")).startswith("event.") for c in flat
            if isinstance(c, dict)
        ):
            return False
    return bool(
        conditions.evaluate_node_config(
            config,
            work_item=state.work_item,
            event_payload=payload if isinstance(payload, dict) else None,
        )
    )


def _tenant_scope_for(state: _WalkState) -> TenantScope:
'''),
    (r'''    node_config = ActionNodeConfig.from_node_config(config)
    from app.services.tools import action_selectors

    selector_name = action_selectors.resolve_selector(node_config.action_type)
    if selector_name is None:
        raise ValueError(
''',
     r'''    node_config = ActionNodeConfig.from_node_config(config)
    from app.services.tools import action_selectors

    # ARCH37-S1:registry-selector. Every registered action names its own R33
    # selector; resolve_selector delegates to the registry.
    selector_name = action_selectors.resolve_selector(node_config.action_type)
    if selector_name is None:
        raise ValueError(
'''),
    (r'''            error = str(exc)
            break
        except Exception as exc:
            _record_node(
                state,
                node_key=node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
            )
            logger.warning("automation.node_failed", extra={"execution_id": str(execution.id), "error": str(exc)})
            if on_error == "HALT":
                terminal = AutomationExecutionStatus.FAILED
''',
     r'''            error = str(exc)
            break
        except Exception as exc:
            if isinstance(exc, _AlreadyRecorded):
                exc = exc.original  # type: ignore[assignment]
            else:
                _record_node(
                    state,
                    node_key=node_key,
                    node_type=node.node_type,
                    status=AutomationNodeRunStatus.FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                )
            logger.warning("automation.node_failed", extra={"execution_id": str(execution.id), "error": str(exc)})
            if on_error == "HALT":
                terminal = AutomationExecutionStatus.FAILED
'''),
    (r'''        if action_type in ("llm.extract", "llm.classify"):
            return _run_llm_node(state, node=node, config=config, call_model=call_model)

        outcome = _run_action_node(state, node_key=node.node_key, config=config, perform=perform)
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            details={"outcome": outcome, "action_type": action_type},
        )
        return True

    raise ValueError(f"Unknown node type {node.node_type!r}")

''',
     r'''        if action_type in ("llm.extract", "llm.classify"):
            return _run_llm_node(state, node=node, config=config, call_model=call_model)

        from app.services.automation import actions as action_registry

        canonical = action_registry.canonical(action_type)
        started = time.perf_counter()
        try:
            outcome = _run_action_node(state, node_key=node.node_key, config=config, perform=perform)
        except Exception as exc:
            # Recorded here, with what was attempted and for how long, then
            # re-raised for run_execution's on_error policy. The outer
            # handler skips a second row for a node already recorded.
            _record_node(
                state,
                node_key=node.node_key,
                node_type=node.node_type,
                status=AutomationNodeRunStatus.FAILED,
                error=f"{type(exc).__name__}: {exc}",
                details={"action_type": canonical},
                action_type=canonical,
                duration_ms=_elapsed_ms(started),
            )
            raise _AlreadyRecorded(exc) from exc
        summary = str(outcome)
        external_ref = getattr(outcome, "external_ref", None)
        proceed = bool(getattr(outcome, "continue_downstream", True))
        _record_node(
            state,
            node_key=node.node_key,
            node_type=node.node_type,
            status=AutomationNodeRunStatus.COMPLETED,
            details={
                "outcome": summary,
                "action_type": canonical,
                **({"continue_downstream": False} if not proceed else {}),
                **(dict(getattr(outcome, "details", None) or {})),
            },
            action_type=canonical,
            external_ref=external_ref,
            duration_ms=_elapsed_ms(started),
        )
        return proceed

    raise ValueError(f"Unknown node type {node.node_type!r}")

'''),
    (r'''                _propagate_skip(state, candidate, taken=None)


def _record_node(
    state: _WalkState,
    *,
''',
     r'''                _propagate_skip(state, candidate, taken=None)


class _AlreadyRecorded(RuntimeError):
    """A node failure whose node-run row is already written."""

    def __init__(self, original: BaseException) -> None:
        super().__init__(str(original))
        self.original = original


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _record_node(
    state: _WalkState,
    *,
'''),
    (r'''    input_digest: Optional[str] = None,
    output_digest: Optional[str] = None,
    cost_micros: int = 0,
) -> AutomationNodeRun:
    run = AutomationNodeRun(
        execution_id=state.execution.id,
''',
     r'''    input_digest: Optional[str] = None,
    output_digest: Optional[str] = None,
    cost_micros: int = 0,
    action_type: Optional[str] = None,
    external_ref: Optional[str] = None,
    duration_ms: Optional[int] = None,
) -> AutomationNodeRun:
    run = AutomationNodeRun(
        execution_id=state.execution.id,
'''),
    (r'''        cost_micros=cost_micros,
        error=error,
        details=details or {},
    )
    state.sequence += 1
    state.db.add(run)
''',
     r'''        cost_micros=cost_micros,
        error=error,
        details=details or {},
        action_type=(action_type or None) and action_type[:48],
        external_ref=(external_ref or None) and str(external_ref)[:128],
        duration_ms=duration_ms,
    )
    state.sequence += 1
    state.db.add(run)
'''),
    (r'''
def _default_perform_action(
    state: _WalkState,
) -> Callable[[ActionSpec, ActionNodeConfig], str]:
    def _perform(spec: ActionSpec, config: ActionNodeConfig) -> str:
        import asyncio
        from app.services.automation_service import (
            ActionFailure,
            EMAIL_ACTION_TYPES,
            _LazyEmailSettings,
            _render_action_message,
        )
        from app.services.notification.dispatcher import notification_dispatcher

        if spec.action_type in EMAIL_ACTION_TYPES:
            resolved = _LazyEmailSettings(
                db=state.db, workspace_id=state.execution.workspace_id
            ).require()
            if not spec.recipient:
                raise ActionFailure("Email action has no recipient configured.")
            title, body = _render_action_message(rule=state.rule, work_item=state.work_item)
            ok = asyncio.run(
                notification_dispatcher.send(
                    action_type=spec.action_type,
                    settings=resolved,
                    recipient=spec.recipient,
                    title=title,
                    body=body,
                )
            )
            if not ok:
                raise ActionFailure(f"Provider '{spec.action_type}' reported a delivery failure.")
            return f"{spec.action_type} -> {spec.recipient}"

        if spec.action_type in ("set_field", "work_item.mutate"):
            return _perform_mutation(state, spec)

        raise ActionFailure(f"Unsupported action type '{spec.action_type}'.")

    return _perform


def _perform_mutation(state: _WalkState, spec: ActionSpec) -> str:
    from app.services import outbox_service
    from app.services.automation_service import ActionFailure

''',
     r'''
def _default_perform_action(
    state: _WalkState,
) -> Callable[[ActionSpec, ActionNodeConfig], Any]:
    # ARCH37-S1:registry-dispatch. A dictionary dispatch over the action
    # registry. Every registered type has a schema, a selector and a perform
    # function, asserted when the registry is imported.
    from app.services.automation import actions as action_registry

    def _perform(spec: ActionSpec, config: ActionNodeConfig) -> Any:
        return action_registry.perform_action(state, spec)

    return _perform


def _perform_mutation(state: _WalkState, spec: ActionSpec) -> str:
    """Retained for callers that imported it. The registry's
    `work_item.mutate` module is the live path."""
    from app.services import outbox_service
    from app.services.automation_service import ActionFailure

'''),
]))

PATCHES.append(('app/services/automation/graph_service.py', 'ARCH37-S1:flow-flatten', [
    (r'''    )


def flatten_legacy_rule(rule: AutomationRule) -> CompiledGraph:
    nodes: list[NodeSpec] = [NodeSpec(node_key="trigger", node_type="trigger")]
    edges: list[EdgeSpec] = []

''',
     r'''    )


def flatten_flow_rule(rule: AutomationRule, spec: dict[str, Any]) -> CompiledGraph:
    """ARCH37-S1:flow-flatten. The step builder's shape as a graph.

        trigger -> [conditions] -> then actions            (no "otherwise")
        trigger -> branch -true-> then actions
                          -false-> otherwise actions

    Each action list is a chain, so a failed or held action stops the ones
    after it and never the other branch.
    """
    groups = list(spec.get("condition_groups") or [])
    groups_operator = spec.get("groups_operator") or "AND"
    then_actions = list(getattr(rule, "actions", []) or [])
    else_actions = list(spec.get("else_actions") or [])
    has_conditions = any((g or {}).get("conditions") for g in groups if isinstance(g, dict))

    nodes: list[NodeSpec] = [NodeSpec(node_key="trigger", node_type="trigger")]
    edges: list[EdgeSpec] = []
    condition_config = {"groups": groups, "groups_operator": groups_operator}

    def chain(prefix: str, actions: list[Any], start: str, branch: str) -> None:
        previous, label = start, branch
        for index, action in enumerate(actions):
            key = f"{prefix}_{index}"
            nodes.append(
                NodeSpec(
                    node_key=key,
                    node_type="action",
                    config=dict(action) if isinstance(action, dict) else {"action": action},
                )
            )
            edges.append(EdgeSpec(previous, key, label))
            previous, label = key, "default"

    if else_actions and has_conditions:
        nodes.append(NodeSpec(node_key="decision", node_type="branch", config=condition_config))
        edges.append(EdgeSpec("trigger", "decision"))
        chain("action", then_actions, "decision", "true")
        chain("else", else_actions, "decision", "false")
    elif has_conditions:
        nodes.append(NodeSpec(node_key="conditions", node_type="condition", config=condition_config))
        edges.append(EdgeSpec("trigger", "conditions"))
        chain("action", then_actions, "conditions", "default")
    else:
        chain("action", then_actions, "trigger", "default")

    return CompiledGraph(
        nodes=tuple(nodes),
        edges=tuple(edges),
        order=tuple(n.node_key for n in nodes),
        trigger_key="trigger",
    )


def flatten_legacy_rule(rule: AutomationRule) -> CompiledGraph:
    flow_spec = getattr(rule, "flow_spec", None)
    if isinstance(flow_spec, dict):
        return flatten_flow_rule(rule, flow_spec)

    nodes: list[NodeSpec] = [NodeSpec(node_key="trigger", node_type="trigger")]
    edges: list[EdgeSpec] = []

'''),
    (r'''    "NodeSpec",
    "compile_graph",
    "convert_rule_to_graph",
    "flatten_legacy_rule",
    "load_graph",
    "save_graph",
''',
     r'''    "NodeSpec",
    "compile_graph",
    "convert_rule_to_graph",
    "flatten_flow_rule",
    "flatten_legacy_rule",
    "load_graph",
    "save_graph",
'''),
]))

PATCHES.append(('app/services/automation_service.py', 'ARCH37-S1:dry-run', [
    (r'''        rule: AutomationRule,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        start_time = time.perf_counter()
        success, matched, notification_sent = True, False, False
        message = "Rule conditions were not satisfied."
''',
     r'''        rule: AutomationRule,
        work_item: WorkItem,
    ) -> dict[str, Any]:
        # ARCH37-S1:dry-run. A flow rule, or any rule with an action other
        # than email, is tested without side effects: conditions are
        # evaluated and each action's config is validated, nothing runs.
        # The ARCH-13 email-only path below still sends its labelled test.
        if getattr(rule, "flow_spec", None) is not None or any(
            str(_get_condition_attribute(a, "action_type") or "").lower().strip()
            not in EMAIL_ACTION_TYPES
            for a in (getattr(rule, "actions", []) or [])
        ):
            from app.services.automation import flow_service

            return flow_service.dry_run(db, rule=rule, work_item=work_item)

        start_time = time.perf_counter()
        success, matched, notification_sent = True, False, False
        message = "Rule conditions were not satisfied."
'''),
]))

PATCHES.append(('app/services/document_intake_service.py', 'ARCH37-S1:work-item-created', [
    (r'''            },
        )

    except Exception:
        try:
            driver.delete(stored.key)
''',
     r'''            },
        )

        # ARCH37-S1:work-item-created. No public event has ever been emitted
        # for a new document, so this trigger is native.
        from app.services import outbox_service

        outbox_service.emit_trigger(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            event_type="trigger.work_item.created",
            resource_id=work_item.id,
            payload={
                "work_item_id": str(work_item.id),
                "original_filename": work_item.original_filename,
                "mime_type": validated.mime_type,
                "size_bytes": int(stored.size),
                "page_count": validated.page_count,
            },
            idempotency_key=f"trigger.work_item.created:{work_item.id}",
        )

    except Exception:
        try:
            driver.delete(stored.key)
'''),
]))

PATCHES.append(('app/services/document_verification_service.py', 'ARCH37-S1:escalation-review-all', [
    (r'''        ((verification.details or {}).get("calibration") or {}).get(
            "review_all_fields"
        )
    )
    disagreed = {
        f.field_path: f
''',
     r'''        ((verification.details or {}).get("calibration") or {}).get(
            "review_all_fields"
        )
    ) or bool(
        # ARCH37-S1:escalation-review-all. A rule's `review.escalate` asks
        # the reviewer to confirm every field, under its own key so ARCH-35's
        # label harvester never mistakes it for a calibration audit.
        ((verification.details or {}).get("escalation") or {}).get(
            "review_all_fields"
        )
    )
    disagreed = {
        f.field_path: f
'''),
]))

PATCHES.append(('app/services/outbox_service.py', 'ARCH37-S1:emit-twin', [
    (r'''    )


def emit_many(db: Session, events: Sequence[dict[str, Any]], *, require_active_transaction: bool = True) -> list[OutboxEvent]:
    prepared: list[OutboxEvent] = []
    _assert_in_transaction(db, required=require_active_transaction)
''',
     r'''    )


# ---------------------------------------------------------------------------
# ARCH37-S1:emit-twin. Flow-builder triggers.
# ---------------------------------------------------------------------------

AUTOMATION_JOB_TYPE: str = "automation.execute"


def _ensure_automation_handler() -> None:
    """`job_service.enqueue` refuses a job type with no registered handler.

    The API and the worker register every handler at startup. A script or a
    gate that drives a service directly may not have, and a trigger must not
    fail the state change that caused it for that reason.
    """
    from app.services import job_service

    if AUTOMATION_JOB_TYPE not in job_service.JOB_HANDLERS:
        from app.workers.handlers import register_all

        register_all()


def emit_trigger(
    db: Session,
    *,
    organization_id: uuid.UUID,
    workspace_id: Optional[uuid.UUID],
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    resource_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    caused_by: Optional[OutboxEvent] = None,
) -> Optional[OutboxEvent]:
    """Write an INTERNAL trigger event and its `automation.execute` job.

    Both rows go into the caller's transaction. Nothing relays INTERNAL events
    to the engine (OUTBOX_INTERNAL_QUEUE has no claimer), so an event written
    without its job is an event no rule ever sees.

    Returns None when there is no workspace: rules are workspace-scoped, so an
    organization-level change has no rule to run.
    """
    if workspace_id is None:
        return None
    if event_type not in INTERNAL_EVENT_TYPES:
        raise UnknownEventTypeError(f"'{event_type}' is not an internal event type.")

    from app.services import job_service

    event = emit_internal(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=event_type,
        payload=payload,
        resource_id=resource_id,
        caused_by=caused_by,
        idempotency_key=idempotency_key,
    )
    _ensure_automation_handler()
    job_service.enqueue(
        db,
        job_type=AUTOMATION_JOB_TYPE,
        organization_id=organization_id,
        payload={"outbox_event_id": str(event.id)},
        idempotency_key=f"automation:execute:{event.id}",
    )
    return event


def emit_public_with_twin(
    db: Session,
    *,
    organization_id: uuid.UUID,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    workspace_id: Optional[uuid.UUID] = None,
    resource_id: Optional[uuid.UUID] = None,
    idempotency_key: Optional[str] = None,
    caused_by: Optional[OutboxEvent] = None,
    twin_resource_id: Optional[uuid.UUID] = None,
    twin_payload: Optional[dict[str, Any]] = None,
) -> tuple[OutboxEvent, Optional[OutboxEvent]]:
    """Emit a PUBLIC event and its INTERNAL `trigger.` twin, in one transaction.

    The public row is exactly what `emit` would have written. The twin carries
    the same payload plus `twin_payload`, and its `resource_id` is the work
    item the automation handler should load (`twin_resource_id`): procurement
    and radar events name a case or a finding as their resource, and the
    handler reads `resource_id` as a work item id.

    `emit` flushes inside a savepoint and never commits, so a caller that rolls
    back loses both rows together. `verify_arch37.py --db` proves it.
    """
    from app.core.automation_events import TRIGGER_TWIN_EVENT_TYPES, twin_of

    public = emit(
        db,
        organization_id=organization_id,
        event_type=event_type,
        payload=payload,
        workspace_id=workspace_id,
        resource_id=resource_id,
        idempotency_key=idempotency_key,
        caused_by=caused_by,
    )
    twin_name = twin_of(event_type)
    if twin_name not in TRIGGER_TWIN_EVENT_TYPES:
        raise UnknownEventTypeError(f"'{event_type}' has no registered internal twin.")

    merged = dict(payload or {})
    merged.update(twin_payload or {})
    merged["public_event_id"] = str(public.id)
    twin = emit_trigger(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=twin_name,
        payload=merged,
        resource_id=twin_resource_id,
        idempotency_key=(f"trigger:{idempotency_key}" if idempotency_key else None),
        caused_by=caused_by,
    )
    return public, twin


def emit_many(db: Session, events: Sequence[dict[str, Any]], *, require_active_transaction: bool = True) -> list[OutboxEvent]:
    prepared: list[OutboxEvent] = []
    _assert_in_transaction(db, required=require_active_transaction)
'''),
]))

PATCHES.append(('app/services/pipeline_state.py', 'ARCH37-S1:document-twins', [
    (r'''    PipelineStage.FAILED: "document.failed",
    PipelineStage.QUOTA_BLOCKED: "document.failed",
}


class PipelineStateError(Exception):
''',
     r'''    PipelineStage.FAILED: "document.failed",
    PipelineStage.QUOTA_BLOCKED: "document.failed",
}


#: ARCH-37. Public pipeline events the flow builder can trigger on.
TWINNED_EVENTS: frozenset[str] = frozenset({"document.completed", "document.failed"})


class PipelineStateError(Exception):
'''),
    (r'''            if event_payload:
                payload.update(event_payload)

            outbox_service.emit(
                db,
                organization_id=organization_id,
                workspace_id=work_item.workspace_id,
                event_type=event_type,
                resource_id=work_item.id,
                payload=payload,
                idempotency_key=idempotency_key,
            )
        else:
            logger.debug(
                "pipeline.outbox_already_emitted",
''',
     r'''            if event_payload:
                payload.update(event_payload)

            # ARCH37-S1:document-twins. document.completed / document.failed
            # carry an internal twin for the flow builder, in this transaction.
            if event_type in TWINNED_EVENTS:
                outbox_service.emit_public_with_twin(
                    db,
                    organization_id=organization_id,
                    workspace_id=work_item.workspace_id,
                    event_type=event_type,
                    resource_id=work_item.id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                    twin_resource_id=work_item.id,
                )
            else:
                outbox_service.emit(
                    db,
                    organization_id=organization_id,
                    workspace_id=work_item.workspace_id,
                    event_type=event_type,
                    resource_id=work_item.id,
                    payload=payload,
                    idempotency_key=idempotency_key,
                )
        else:
            logger.debug(
                "pipeline.outbox_already_emitted",
'''),
]))

PATCHES.append(('app/services/procurement_matching/case_service.py', 'ARCH37-S1:procurement-twins', [
    (r'''        digest=computed_digest,
    )

    outbox_service.emit(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_COMPLETED,
        resource_id=case.id,
        idempotency_key=f"{EVENT_COMPLETED}:{computed_digest}",
        payload={
            "case_id": str(case.id),
''',
     r'''        digest=computed_digest,
    )

    # ARCH37-S1:procurement-twins. The twin names the invoice as its
    # resource: the automation handler loads resource_id as a work item.
    outbox_service.emit_public_with_twin(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_COMPLETED,
        resource_id=case.id,
        twin_resource_id=invoice_work_item_id,
        idempotency_key=f"{EVENT_COMPLETED}:{computed_digest}",
        payload={
            "case_id": str(case.id),
'''),
    (r'''            "red_outcomes": sorted({line.outcome for line in red_lines}),
        },
    )
    outbox_service.emit(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_APPROVED,
        resource_id=case.id,
        idempotency_key=f"{EVENT_APPROVED}:{case.id}",
        payload={
            "case_id": str(case.id),
''',
     r'''            "red_outcomes": sorted({line.outcome for line in red_lines}),
        },
    )
    outbox_service.emit_public_with_twin(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_APPROVED,
        resource_id=case.id,
        twin_resource_id=case.invoice_work_item_id,
        idempotency_key=f"{EVENT_APPROVED}:{case.id}",
        payload={
            "case_id": str(case.id),
'''),
    (r'''            "policy_version": case.policy_version,
        },
    )
    outbox_service.emit(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_DISPUTED,
        resource_id=case.id,
        idempotency_key=f"{EVENT_DISPUTED}:{case.id}",
        payload={
            "case_id": str(case.id),
''',
     r'''            "policy_version": case.policy_version,
        },
    )
    outbox_service.emit_public_with_twin(
        db,
        organization_id=case.organization_id,
        workspace_id=workspace_id,
        event_type=EVENT_DISPUTED,
        resource_id=case.id,
        twin_resource_id=case.invoice_work_item_id,
        idempotency_key=f"{EVENT_DISPUTED}:{case.id}",
        payload={
            "case_id": str(case.id),
'''),
]))

PATCHES.append(('app/services/radar/findings.py', 'ARCH37-S1:anomaly-twin', [
    (r'''    language to whatever URL a tenant configured. The event names the finding;
    the API serves the evidence under the capability gate.
    """
    outbox_service.emit(
        db,
        organization_id=finding.organization_id,
        workspace_id=finding.workspace_id,
        event_type=vocab.OUTBOX_EVENT_ANOMALY_DETECTED,
        resource_id=finding.id,
        payload={
            "finding_id": str(finding.id),
            "kind": finding.kind,
''',
     r'''    language to whatever URL a tenant configured. The event names the finding;
    the API serves the evidence under the capability gate.
    """
    # ARCH37-S1:anomaly-twin. The twin's resource is the subject document.
    outbox_service.emit_public_with_twin(
        db,
        organization_id=finding.organization_id,
        workspace_id=finding.workspace_id,
        event_type=vocab.OUTBOX_EVENT_ANOMALY_DETECTED,
        resource_id=finding.id,
        twin_resource_id=finding.subject_work_item_id,
        payload={
            "finding_id": str(finding.id),
            "kind": finding.kind,
'''),
]))

PATCHES.append(('app/services/redaction/redaction_service.py', 'ARCH37-S1:redaction-completed', [
    (r'''        job.status = JOB_STATUS_COMPLETED
        db.flush([job])

        audit_service.record(
            db,
            organization_id=job.organization_id,
''',
     r'''        job.status = JOB_STATUS_COMPLETED
        db.flush([job])

        # ARCH37-S1:redaction-completed
        from app.services import outbox_service

        outbox_service.emit_trigger(
            db,
            organization_id=job.organization_id,
            workspace_id=job.workspace_id,
            event_type="trigger.redaction.completed",
            resource_id=job.work_item_id,
            payload={
                "work_item_id": str(job.work_item_id),
                "redaction_job_id": str(job.id),
                "profile": job.profile_key,
                "pages": document.page_count,
            },
            idempotency_key=f"trigger.redaction.completed:{job.id}",
        )

        audit_service.record(
            db,
            organization_id=job.organization_id,
'''),
]))

PATCHES.append(('app/services/tools/action_selectors.py', 'ARCH37-S1:selector-registry', [
    (r'''

def resolve_selector(action_type: str) -> Optional[str]:
    """Which registered selector handles an action type."""
    normalised = (action_type or "").strip().lower()
    if normalised in ("email", "send_email", "webhook"):
        return "automation.action_selector"
    if normalised in ("set_field", "work_item.mutate"):
        return "automation.mutation_selector"
    return None


__all__ = [
''',
     r'''

def resolve_selector(action_type: str) -> Optional[str]:
    """Which registered selector handles an action type.

    ARCH37-S1:selector-registry. Delegates to the action registry, which names
    a selector per action and refuses to import without one. The two ARCH-13
    selectors above stay registered for their direct callers.
    """
    from app.services.automation import actions as action_registry

    return action_registry.selector_for(action_type)


__all__ = [
'''),
]))

PATCHES.append(('app/workers/handlers/automation.py', 'ARCH37-S1:trigger-table', [
    (r'''    TIMED_OUT = "TIMED_OUT"


EVENT_TO_RULE_TRIGGER: dict[str, str] = {
    "work_item.enriched": "WORK_ITEM_COMPLETED",
    "work_item.field_changed": "WORK_ITEM_UPDATED",
    "work_item.verification_completed": "WORK_ITEM_COMPLETED",
}


def _model_caller(db: Session, execution: Any, rule: Any, ai_settings: Any):
''',
     r'''    TIMED_OUT = "TIMED_OUT"


# ARCH37-S1:trigger-table. The three-entry EVENT_TO_RULE_TRIGGER dictionary is
# gone. Rules are resolved from automation_rule_triggers, so a rule may listen
# to several events and every event a rule can store is one the engine
# receives (ck_automation_rule_triggers_event_known).


def _model_caller(db: Session, execution: Any, rule: Any, ai_settings: Any):
'''),
    (r'''def _resolve_rules(
    db: Session, *, workspace_id: uuid.UUID, event_type: str
) -> list[AutomationRule]:
    from app import crud

    trigger = EVENT_TO_RULE_TRIGGER.get(event_type)
    if trigger is None:
        return []
    rules = crud.list_active_rules_for_event(
        db, workspace_id=workspace_id, event=trigger
    )
    return sorted(
        rules,
''',
     r'''def _resolve_rules(
    db: Session, *, workspace_id: uuid.UUID, event_type: str
) -> list[AutomationRule]:
    from app.services.automation import rule_triggers

    rules = rule_triggers.active_rules_for_event(
        db, workspace_id=workspace_id, event_type=event_type
    )
    return sorted(
        rules,
'''),
    (r'''            if isinstance(event.payload, dict) and event.payload.get("work_item_id")
            else None
        )
        work_item = (
            db.execute(select(WorkItem).where(WorkItem.id == work_item_id)).scalar_one_or_none()
            if work_item_id
            else None
        )

        if work_item is not None and _verification_blocks(db, work_item_id=work_item.id):
            logger.info(
                "automation.blocked_pending_verification",
                extra={"work_item_id": str(work_item.id)},
''',
     r'''            if isinstance(event.payload, dict) and event.payload.get("work_item_id")
            else None
        )
        # Scoped to the event's workspace: a resource id that is not a work
        # item in this workspace loads nothing rather than a stranger's row.
        work_item = (
            db.execute(
                select(WorkItem).where(
                    WorkItem.id == work_item_id,
                    WorkItem.workspace_id == workspace_id,
                )
            ).scalar_one_or_none()
            if work_item_id
            else None
        )

        from app.services.automation.triggers import REVIEW_EXEMPT_EVENT_TYPES

        # ARCH37-S1:review-exempt. "Clause check held for review" announces a
        # review; the review it announces must not suppress it.
        if (
            work_item is not None
            and event.event_type not in REVIEW_EXEMPT_EVENT_TYPES
            and _verification_blocks(db, work_item_id=work_item.id)
        ):
            logger.info(
                "automation.blocked_pending_verification",
                extra={"work_item_id": str(work_item.id)},
'''),
    (r'''    db.commit()


__all__ = ["EVENT_TO_RULE_TRIGGER", "JOB_TYPE", "Outcome", "handle_automation_execute"]''',
     r'''    db.commit()


__all__ = ["JOB_TYPE", "Outcome", "handle_automation_execute"]'''),
]))

PATCHES.append(('apply_arch39.py', 'ARCH37-S1:arch39-embed-synced', [
    ('\nNEW_BACKEND_FILES: dict[str, str] = {}\nNEW_FRONTEND_FILES: dict[str, str] = {}\nNEW_BACKEND_FILES[\'alembic/versions/arch39_step1_conversations.py\'] = r\'\'\'"""ARCH-39 — Conversational AI Suite: session columns, scope items, templates.\n\nRevision ID: arch39_step1_conversations\n',
     '\nNEW_BACKEND_FILES: dict[str, str] = {}\nNEW_FRONTEND_FILES: dict[str, str] = {}\n# ARCH37-S1:arch39-embed-synced. ChatSessionBar.tsx below carries the\n# model-dropdown styling committed after this script was first run, so\n# `--check` on an applied tree reports "already present" again.\nNEW_BACKEND_FILES[\'alembic/versions/arch39_step1_conversations.py\'] = r\'\'\'"""ARCH-39 — Conversational AI Suite: session columns, scope items, templates.\n\nRevision ID: arch39_step1_conversations\n'),
    (r'''          value={selectedModel}
          disabled={models.isLoading || chooseModel.isPending}
          onChange={(event) => chooseModel.mutate(event.target.value)}
          className="max-w-[12rem] bg-transparent text-xs font-semibold outline-none"
        >
          <option value="">Default{defaultModel ? ` (${defaultModel})` : ""}</option>
          {(models.data ?? [])
            .filter((option) => !option.is_workspace_default)
            .map((option) => (
              <option key={option.model} value={option.model}>
                {option.model}
              </option>
            ))}
''',
     r'''          value={selectedModel}
          disabled={models.isLoading || chooseModel.isPending}
          onChange={(event) => chooseModel.mutate(event.target.value)}
          className="max-w-[14rem] bg-slate-900 text-slate-100 text-xs font-semibold outline-none cursor-pointer rounded px-1.5 py-0.5 border border-slate-700"
        >
          <option value="" className="bg-slate-900 text-slate-100 py-1">Default{defaultModel ? ` (${defaultModel})` : ""}</option>
          {(models.data ?? [])
            .filter((option) => !option.is_workspace_default)
            .map((option) => (
              <option key={option.model} value={option.model} className="bg-slate-900 text-slate-100 py-1">
                {option.model}
              </option>
            ))}
'''),
]))

PATCHES.append(('verify_arch31.py', 'ARCH37-S1:head-widened-31', [
    (r'''            ]
        # ARCH35-S1:head-widened-31
        # ARCH39-S1:head-widened-31
        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"]), heads

    rec.check("DB: exactly one Alembic head, at Step 1", single_alembic_head)

''',
     r'''            ]
        # ARCH35-S1:head-widened-31
        # ARCH39-S1:head-widened-31
        assert heads in (["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"], ["arch37_step1_flow_builder"]), heads  # ARCH37-S1:head-widened-31

    rec.check("DB: exactly one Alembic head, at Step 1", single_alembic_head)

'''),
]))

PATCHES.append(('verify_arch31_step0.py', 'ARCH37-S1:head-widened-31s0', [
    (r'''        heads = sorted(r for r in revs if r not in downs)
        # ARCH35-S1:head-widened-31s0
        # ARCH39-S1:head-widened-31s0
        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"]), heads

    rec.check("Step 0 leaves exactly one Alembic head", single_alembic_head)

''',
     r'''        heads = sorted(r for r in revs if r not in downs)
        # ARCH35-S1:head-widened-31s0
        # ARCH39-S1:head-widened-31s0
        assert heads in (["arch31_step0_document_roles"], ["arch31_step1_procurement_matching"], ["arch32_step1_redaction"], ["arch33_step1_assertions"], ["arch34_step1_radar"], ["arch35_step1_calibration"], ["arch39_step1_conversations"], ["arch37_step1_flow_builder"]), heads  # ARCH37-S1:head-widened-31s0

    rec.check("Step 0 leaves exactly one Alembic head", single_alembic_head)

'''),
]))

PATCHES.append(('verify_arch34.py', 'ARCH37-S1:head-widened-34', [
    (r'''        check(
            "alembic head is arch34_step1_radar or later",
            # ARCH39-S1:head-widened-34
            head in ("arch34_step1_radar", "arch35_step1_calibration", "arch39_step1_conversations"),
            str(head),
        )

''',
     r'''        check(
            "alembic head is arch34_step1_radar or later",
            # ARCH39-S1:head-widened-34
            head in ("arch34_step1_radar", "arch35_step1_calibration", "arch39_step1_conversations", "arch37_step1_flow_builder"),  # ARCH37-S1:head-widened-34
            str(head),
        )

'''),
]))

PATCHES.append(('verify_arch35.py', 'ARCH37-S1:head-widened-35', [
    (r'''        head = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
        # ARCH39-S1:head-widened-35. ARCH-39 moves the head forward; ARCH-35's
        # tables must be present at or after its own head.
        assert head in ("arch35_step1_calibration", "arch39_step1_conversations"), (
            f"alembic head is {head}; run `alembic upgrade head`"
        )
        for table in ("calibration_labels", "calibration_models"):
''',
     r'''        head = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
        # ARCH39-S1:head-widened-35. ARCH-39 moves the head forward; ARCH-35's
        # tables must be present at or after its own head.
        assert head in ("arch35_step1_calibration", "arch39_step1_conversations", "arch37_step1_flow_builder"), (  # ARCH37-S1:head-widened-35
            f"alembic head is {head}; run `alembic upgrade head`"
        )
        for table in ("calibration_labels", "calibration_models"):
'''),
]))

PATCHES.append(('verify_arch36.py', 'ARCH37-S1:head-widened-36', [
    (r'''        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH39-S1:head-widened-36. ARCH-36 added no migration; ARCH-39 does.
            assert value in (EXPECTED_HEAD, "arch39_step1_conversations"), (
                f"alembic head is {value}; expected {EXPECTED_HEAD} or later"
            )

''',
     r'''        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH39-S1:head-widened-36. ARCH-36 added no migration; ARCH-39 does.
            assert value in (EXPECTED_HEAD, "arch39_step1_conversations", "arch37_step1_flow_builder"), (  # ARCH37-S1:head-widened-36
                f"alembic head is {value}; expected {EXPECTED_HEAD} or later"
            )

'''),
]))

PATCHES.append(('verify_arch39.py', 'ARCH37-S1:head-widened-39', [
    (r'''    try:
        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            assert value == HEAD, f"alembic head is {value}; run `alembic upgrade head`"

        if not rec.check(f"DB: head is {HEAD}", head):
            return
''',
     r'''    try:
        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH37-S1:head-widened-39. ARCH-37 moves the head forward.
            assert value in (HEAD, "arch37_step1_flow_builder"), f"alembic head is {value}; run `alembic upgrade head`"

        if not rec.check(f"DB: head is {HEAD}", head):
            return
'''),
]))


FRONTEND_PATCHES: list[tuple[str, str, list[tuple[str, str]]]] = []
FRONTEND_PATCHES.append(('src/pages/Automation/Automation.tsx', 'ARCH37-S2:flow-builder-wired', [
    (r'''  ChevronUp,
} from "lucide-react";
import { automationApi } from "@/services/api/automation";
import { RuleForm } from "@/pages/Automation/RuleForm";
import { RuleTestDialog } from "@/pages/Automation/RuleTestDialog";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import { getFriendlyFieldName } from "@/constants/automationFields";
import type { AutomationRule, AutomationLog, AutomationErrorPolicy } from "@/types/automation";
import { formatCostMicros, formatDurationMs } from "@/types/automation";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
''',
     r'''  ChevronUp,
} from "lucide-react";
import { automationApi } from "@/services/api/automation";
// ARCH37-S2:flow-builder-wired. The step builder replaces RuleForm; labels come from the catalog.
import { FlowBuilder } from "@/pages/Automation/FlowBuilder";
import { actionLabel, fieldLabel, triggerLabel } from "@/components/automation/flow/flowModel";
import { RuleTestDialog } from "@/pages/Automation/RuleTestDialog";
import { formatDateTime } from "@/utils/formatters";
import { ApiError } from "@/services/api/client";
import type { AutomationRule, AutomationLog, AutomationErrorPolicy } from "@/types/automation";
import { formatCostMicros, formatDurationMs } from "@/types/automation";
import { ConfirmDialog } from "@/components/common/ConfirmDialog";
'''),
    (r'''  // Search & Filters Panel states (Rule list)
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ENABLED" | "DISABLED">("ALL");
  const [eventFilter, setEventFilter] = useState<string>("ALL");
  const [sortBy, setSortBy] = useState<string>("PRIORITY_ASC");

''',
     r'''  // Search & Filters Panel states (Rule list)
  const [searchQuery, setSearchQuery] = useState<string>("");
  const [statusFilter, setStatusFilter] = useState<"ALL" | "ENABLED" | "DISABLED">("ALL");
  const { data: catalog } = useQuery({
    queryKey: automationKeys.catalog(workspaceId ?? ""),
    queryFn: () => automationApi.getAutomationCatalog(workspaceId ?? ""),
    enabled: Boolean(workspaceId),
    staleTime: 60_000,
  });
  const [eventFilter, setEventFilter] = useState<string>("ALL");
  const [sortBy, setSortBy] = useState<string>("PRIORITY_ASC");

'''),
    (r'''          ? rule.is_active
          : !rule.is_active;
      const matchesEvent =
        eventFilter === "ALL" ? true : rule.event === eventFilter;
      return matchesSearch && matchesStatus && matchesEvent;
    });
  }, [rules, searchQuery, statusFilter, eventFilter]);
''',
     r'''          ? rule.is_active
          : !rule.is_active;
      const matchesEvent =
        eventFilter === "ALL"
          ? true
          : (rule.triggers ?? []).includes(eventFilter) || rule.event === eventFilter;
      return matchesSearch && matchesStatus && matchesEvent;
    });
  }, [rules, searchQuery, statusFilter, eventFilter]);
'''),
    (r'''              </SelectTrigger>

              <SelectContent>
                <SelectItem value="ALL">All Events</SelectItem>
                <SelectItem value="WORK_ITEM_CREATED">Created</SelectItem>
                <SelectItem value="WORK_ITEM_COMPLETED">Completed</SelectItem>
                <SelectItem value="WORK_ITEM_FAILED">Failed</SelectItem>
                <SelectItem value="WORK_ITEM_REPROCESSED">
                  Reprocessed
                </SelectItem>
              </SelectContent>
            </Select>
          </div>
''',
     r'''              </SelectTrigger>

              <SelectContent>
                <SelectItem value="ALL">All triggers</SelectItem>
                {(catalog?.triggers ?? []).map((trigger) => (
                  <SelectItem key={trigger.key} value={trigger.key}>
                    {trigger.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
'''),
    (r'''                          Priority #{rule.priority}
                        </span>
                        <span className="text-[10px] bg-secondary text-secondary-foreground border border-border/40 px-2 py-0.5 rounded-md font-semibold select-none whitespace-nowrap">
                          Event: {rule.event.replace("WORK_ITEM_", "")}
                        </span>
                        {rule.is_active ? (
                          <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-2.5 py-0.5 rounded-full font-bold select-none whitespace-nowrap">
                            Active
''',
     r'''                          Priority #{rule.priority}
                        </span>
                        <span className="text-[10px] bg-secondary text-secondary-foreground border border-border/40 px-2 py-0.5 rounded-md font-semibold select-none whitespace-nowrap">
                          When:{" "}
                          {(rule.triggers ?? []).length > 0
                            ? (rule.triggers ?? []).map((key) => triggerLabel(catalog, key)).join(" or ")
                            : "No trigger (paused)"}
                        </span>
                        {(rule.else_actions ?? []).length > 0 && (
                          <span className="text-[10px] bg-amber-500/10 text-amber-700 dark:text-amber-400 px-2 py-0.5 rounded-md font-semibold select-none whitespace-nowrap">
                            Has otherwise
                          </span>
                        )}
                        {rule.is_active ? (
                          <span className="text-[10px] bg-emerald-500/10 text-emerald-500 px-2.5 py-0.5 rounded-full font-bold select-none whitespace-nowrap">
                            Active
'''),
    (r'''                            )}
                            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                              <span className="px-2 py-1 rounded-md bg-background border border-border/60 font-mono font-bold text-foreground truncate max-w-[150px]">
                                {getFriendlyFieldName(cond.field)}
                              </span>
                              <span className="text-[10px] uppercase font-bold text-muted-foreground px-1">
                                {OPERATOR_DISPLAY_MAP[cond.operator] ??
''',
     r'''                            )}
                            <div className="flex flex-wrap items-center gap-1.5 text-xs">
                              <span className="px-2 py-1 rounded-md bg-background border border-border/60 font-mono font-bold text-foreground truncate max-w-[150px]">
                                {fieldLabel(catalog, cond.field)}
                              </span>
                              <span className="text-[10px] uppercase font-bold text-muted-foreground px-1">
                                {OPERATOR_DISPLAY_MAP[cond.operator] ??
'''),
    (r'''                              className="px-2 py-1 rounded-md bg-background border border-border/60 text-emerald-600 dark:text-emerald-400 truncate max-w-[150px]"
                              title={act.config?.recipient as string}
                            >
                              {act.action_type.replace("_", " ")}

                              {"recipient" in act.config &&
                                typeof act.config.recipient === "string" && (
''',
     r'''                              className="px-2 py-1 rounded-md bg-background border border-border/60 text-emerald-600 dark:text-emerald-400 truncate max-w-[150px]"
                              title={act.config?.recipient as string}
                            >
                              {actionLabel(catalog, act.action_type)}

                              {"recipient" in act.config &&
                                typeof act.config.recipient === "string" && (
'''),
    (r'''        </div>
      </section>

      <RuleForm
        isOpen={isFormOpen}
        onClose={handleFormClose}
        onSaveSuccess={handleSaveSuccessCallback}
''',
     r'''        </div>
      </section>

      <FlowBuilder
        isOpen={isFormOpen}
        onClose={handleFormClose}
        onSaveSuccess={handleSaveSuccessCallback}
'''),
]))

FRONTEND_PATCHES.append(('src/pages/Automation/ExecutionTimeline.tsx', 'ARCH37-S2:node-runs', [
    (r'''  AutomationExecution,
  AutomationExecutionStatus,
} from "@/services/api/executions";
import { workspaceScope } from "@/services/api/queryKeys";
import { formatMicros } from "@/types/billing";
import { pollUnlessRefused } from "@/services/api/polling";

''',
     r'''  AutomationExecution,
  AutomationExecutionStatus,
} from "@/services/api/executions";
import { automationKeys, workspaceScope } from "@/services/api/queryKeys";
import { listExecutionNodes } from "@/services/api/executions";
import { formatMicros } from "@/types/billing";
import { pollUnlessRefused } from "@/services/api/polling";

'''),
    (r'''  execution,
  isLast,
}) => {
  const presentation = EXECUTION_STATUS_PRESENTATION[execution.status] ?? {
    label: execution.status,
    tone: "muted" as const,
''',
     r'''  execution,
  isLast,
}) => {
  const [showNodes, setShowNodes] = useState(false);
  const presentation = EXECUTION_STATUS_PRESENTATION[execution.status] ?? {
    label: execution.status,
    tone: "muted" as const,
'''),
    (r'''              prior {priorExecutionId.slice(0, 8)}
            </span>
          )}
        </div>
      </div>
    </li>
  );
};

export default ExecutionTimeline;
''',
     r'''              prior {priorExecutionId.slice(0, 8)}
            </span>
          )}
          {execution.node_count > 0 && (
            <button
              type="button"
              onClick={() => setShowNodes((open) => !open)}
              aria-expanded={showNodes}
              className="font-semibold text-primary hover:underline"
            >
              {showNodes ? "Hide steps" : "Show steps"}
            </button>
          )}
        </div>

        {showNodes && <NodeRuns executionId={execution.id} />}
      </div>
    </li>
  );
};

/**
 * ARCH37-S2:node-runs. What each node did — the action type, what it created (delivery,
 * job, verification) and how long it took.
 */
const NodeRuns: React.FC<{ readonly executionId: string }> = ({ executionId }) => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { data, isLoading, isError } = useQuery({
    queryKey: automationKeys.executionNodes(workspaceId, executionId),
    queryFn: () => listExecutionNodes(workspaceId, executionId),
    enabled: Boolean(workspaceId),
    staleTime: 30_000,
  });
  if (isLoading) {
    return <p className="mt-2 text-[11px] text-muted-foreground">Loading steps…</p>;
  }
  if (isError || !data) {
    return <p className="mt-2 text-[11px] text-destructive">Steps could not be loaded.</p>;
  }
  return (
    <ol className="mt-2 space-y-1 rounded bg-background/60 p-2 text-[11px]">
      {data.map((node) => (
        <li key={node.id} className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="font-mono text-muted-foreground">#{node.sequence}</span>
          <span className="font-semibold">{node.action_type ?? node.node_type ?? node.node_key}</span>
          <span className={node.status === "FAILED" ? "text-destructive" : "text-muted-foreground"}>
            {node.status.toLowerCase()}
          </span>
          {node.duration_ms !== null && <span className="text-muted-foreground">{node.duration_ms}ms</span>}
          {node.external_ref && (
            <span className="font-mono text-muted-foreground break-all" title="Reference to what this step created">
              ref {node.external_ref.slice(0, 12)}
            </span>
          )}
          {node.outcome && <span className="text-muted-foreground break-words">— {node.outcome}</span>}
          {node.error && <span className="w-full break-all font-mono text-destructive">{node.error}</span>}
        </li>
      ))}
    </ol>
  );
};

export default ExecutionTimeline;
'''),
]))

FRONTEND_PATCHES.append(('src/services/api/automation.ts', 'ARCH37-S2:catalog-api', [
    (r'''  AutomationRuleUpdateRequest,
  AutomationRuleTestResponse,
} from "@/types/automation";

export const createAutomationRule = async (
  workspaceId: string,
''',
     r'''  AutomationRuleUpdateRequest,
  AutomationRuleTestResponse,
} from "@/types/automation";
import type { FlowCatalog } from "@/types/automationFlow";

export const createAutomationRule = async (
  workspaceId: string,
'''),
    (r'''  return response.data;
};

export const automationApi = {
  createAutomationRule,
  getAutomationRules,
''',
     r'''  return response.data;
};

/** ARCH37-S2:catalog-api. Every trigger, action and resource the builder may offer. */
export const getAutomationCatalog = async (
  workspaceId: string,
): Promise<FlowCatalog> => {
  const response = await apiClient.get<FlowCatalog>(
    AUTOMATION_ENDPOINTS.catalog(workspaceId),
    { headers: { Accept: "application/json" } },
  );
  return response.data;
};

export const automationApi = {
  createAutomationRule,
  getAutomationRules,
'''),
    (r'''  deleteAutomationRule,
  getAutomationLogs,
  testAutomationRule,
};

export default automationApi;
''',
     r'''  deleteAutomationRule,
  getAutomationLogs,
  testAutomationRule,
  getAutomationCatalog,
};

export default automationApi;
'''),
]))

FRONTEND_PATCHES.append(('src/services/api/endpoints.ts', 'ARCH37-S2:catalog-endpoint', [
    (r'''  rule: (workspaceId: string, ruleId: string): string =>
    `${scoped(workspaceId)}/automation/rules/${seg(ruleId)}`,
  logs: (workspaceId: string): string => `${scoped(workspaceId)}/automation/logs`,
} as const;

export const NOTIFICATION_ENDPOINTS = {
''',
     r'''  rule: (workspaceId: string, ruleId: string): string =>
    `${scoped(workspaceId)}/automation/rules/${seg(ruleId)}`,
  logs: (workspaceId: string): string => `${scoped(workspaceId)}/automation/logs`,
  // ARCH37-S2:catalog-endpoint. The flow builder's catalog.
  catalog: (workspaceId: string): string => `${scoped(workspaceId)}/automation/catalog`,
} as const;

export const NOTIFICATION_ENDPOINTS = {
'''),
]))

FRONTEND_PATCHES.append(('src/services/api/executions.ts', 'ARCH37-S2:node-runs-endpoint', [
    (r''' */

import apiClient from "@/services/api/client";

export type AutomationExecutionStatus =
  | "QUEUED"
''',
     r''' */

import apiClient from "@/services/api/client";
import type { AutomationNodeRun } from "@/types/automationFlow";

export type AutomationExecutionStatus =
  | "QUEUED"
'''),
    (r'''    `/workspaces/${ws(workspaceId)}/automation/executions`,
  detail: (workspaceId: string, executionId: string) =>
    `/workspaces/${ws(workspaceId)}/automation/executions/${encodeURIComponent(executionId)}`,
} as const;

export const listExecutions = async (
  workspaceId: string,
''',
     r'''    `/workspaces/${ws(workspaceId)}/automation/executions`,
  detail: (workspaceId: string, executionId: string) =>
    `/workspaces/${ws(workspaceId)}/automation/executions/${encodeURIComponent(executionId)}`,
  // ARCH37-S2:node-runs-endpoint. What each node did, with action type, reference and duration.
  nodes: (workspaceId: string, executionId: string) =>
    `/workspaces/${ws(workspaceId)}/automation/executions/${encodeURIComponent(executionId)}/nodes`,
} as const;

export const listExecutionNodes = async (
  workspaceId: string,
  executionId: string,
): Promise<readonly AutomationNodeRun[]> => {
  const response = await apiClient.get<readonly AutomationNodeRun[]>(
    EXECUTION_ENDPOINTS.nodes(workspaceId, executionId),
  );
  return response.data;
};

export const listExecutions = async (
  workspaceId: string,
'''),
]))

FRONTEND_PATCHES.append(('src/services/api/queryKeys.ts', 'ARCH37-S2:catalog-keys', [
    (r'''  rule: (workspaceId: string, ruleId: string) =>
    [...automationKeys.all(workspaceId), "rule", ruleId] as const,
  logs: (workspaceId: string) => [...automationKeys.all(workspaceId), "logs"] as const,
};

export const notificationKeys = {
''',
     r'''  rule: (workspaceId: string, ruleId: string) =>
    [...automationKeys.all(workspaceId), "rule", ruleId] as const,
  logs: (workspaceId: string) => [...automationKeys.all(workspaceId), "logs"] as const,
  // ARCH37-S2:catalog-keys
  catalog: (workspaceId: string) => [...automationKeys.all(workspaceId), "catalog"] as const,
  executionNodes: (workspaceId: string, executionId: string) =>
    [...automationKeys.all(workspaceId), "execution-nodes", executionId] as const,
};

export const notificationKeys = {
'''),
]))

FRONTEND_PATCHES.append(('src/types/automation.ts', 'ARCH37-S2:catalog-vocabulary', [
    (r'''/**
 * Automation Engine Data Transfer Objects (DTOs) for FlowPilot AI.
 */

export type AutomationEvent =
  | "WORK_ITEM_CREATED"
  | "WORK_ITEM_COMPLETED"
  | "WORK_ITEM_FAILED"
  | "WORK_ITEM_REPROCESSED";

export type AutomationOperator =
  | "EQUALS"
''',
     r'''/**
 * Automation Engine Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * ARCH37-S2:catalog-vocabulary. Triggers and action types are catalog keys served by the API
 * (see types/automationFlow.ts). No trigger or action vocabulary lives here.
 */

import type {
  FlowActionEntry,
  FlowConditionGroup,
} from "@/types/automationFlow";

/** The stored `event` column: a catalog key, or an ARCH-13 legacy value. */
export type AutomationEvent = string;

export type AutomationOperator =
  | "EQUALS"
'''),
    (r'''  | "ARRAY_CONTAINS_ANY"
  | "ARRAY_CONTAINS_ALL";

export type AutomationActionType = "SEND_EMAIL";
export type AutomationExecutionStatus = "SUCCESS" | "FAILED";
export type AutomationLogicOperator = "AND" | "OR";

''',
     r'''  | "ARRAY_CONTAINS_ANY"
  | "ARRAY_CONTAINS_ALL";

export type AutomationActionType = string;
export type AutomationExecutionStatus = "SUCCESS" | "FAILED";
export type AutomationLogicOperator = "AND" | "OR";

'''),
    (r'''  readonly updated_at: string;
  readonly graph_version?: number;
  readonly on_error?: AutomationErrorPolicy;
}

export interface AutomationRuleCreateRequest {
  readonly name: string;
  readonly priority: number;
  readonly event: AutomationEvent;
  readonly conditions: readonly AutomationCondition[];
  readonly logic_operator: AutomationLogicOperator;
  readonly actions: readonly AutomationAction[];
  readonly is_active?: boolean;
}

export interface AutomationRuleUpdateRequest {
  readonly name?: string;
  readonly priority?: number;
  readonly event?: AutomationEvent;
  readonly conditions?: readonly AutomationCondition[];
  readonly logic_operator?: AutomationLogicOperator;
  readonly actions?: readonly AutomationAction[];
  readonly is_active?: boolean;
}

export interface AutomationLog {
''',
     r'''  readonly updated_at: string;
  readonly graph_version?: number;
  readonly on_error?: AutomationErrorPolicy;
  // ARCH-37
  readonly triggers?: readonly string[];
  readonly trigger_events?: readonly string[];
  readonly condition_groups?: readonly FlowConditionGroup[];
  readonly groups_operator?: AutomationLogicOperator;
  readonly else_actions?: readonly FlowActionEntry[];
  readonly is_flow?: boolean;
}

/** ARCH-37 flow shape. The server still accepts the ARCH-13 shape. */
export interface AutomationRuleCreateRequest {
  readonly name: string;
  readonly priority: number;
  readonly triggers: readonly string[];
  readonly condition_groups: readonly FlowConditionGroup[];
  readonly groups_operator: AutomationLogicOperator;
  readonly actions: readonly FlowActionEntry[];
  readonly else_actions: readonly FlowActionEntry[];
  readonly on_error: AutomationErrorPolicy;
  readonly is_active?: boolean;
}

export interface AutomationRuleUpdateRequest {
  readonly name?: string;
  readonly priority?: number;
  readonly is_active?: boolean;
  readonly triggers?: readonly string[];
  readonly condition_groups?: readonly FlowConditionGroup[];
  readonly groups_operator?: AutomationLogicOperator;
  readonly actions?: readonly FlowActionEntry[];
  readonly else_actions?: readonly FlowActionEntry[];
  readonly on_error?: AutomationErrorPolicy;
}

export interface AutomationLog {
'''),
]))


def operations() -> list[Operation]:
    ops: list[Operation] = [NewFile(BACKEND, rel, text) for rel, text in NEW_BACKEND_FILES.items()]
    ops.extend(NewFile(FRONTEND, rel, text) for rel, text in NEW_FRONTEND_FILES.items())
    ops.extend(DeleteFile(FRONTEND, rel, sha) for rel, sha in DELETED_FRONTEND_FILES.items())
    for root, patches in ((BACKEND, PATCHES), (FRONTEND, FRONTEND_PATCHES)):
        for relpath, sentinel, edits in patches:
            ops.append(
                FilePatch(
                    root,
                    relpath,
                    sentinel,
                    [Edit(anchor, replacement, 1, f"{relpath} hunk {i + 1}") for i, (anchor, replacement) in enumerate(edits)],
                )
            )
    return ops


def run(*, check_only: bool) -> int:
    if not (BACKEND / "app").is_dir():
        print(f"  FAIL  app/ not found under {BACKEND}. Run from backend/.")
        return 1
    if not (FRONTEND / "src").is_dir():
        print(f"  FAIL  frontend/src not found at {FRONTEND}.")
        return 1

    try:
        planned = [plan(op) for op in operations()]
    except PatchError as exc:
        print(f"  FAIL  {exc}")
        return 1

    pending = [p for p in planned if p.data is not None]

    if check_only:
        for p in planned:
            verb = "WOULD" if p.data is not None else "OK   "
            print(f"  {verb} {p.relpath}: {p.message}")
        print(f"\n{len(pending)} file(s) would change. Nothing was written.")
        return 0

    written: list[tuple[Planned, Optional[bytes]]] = []
    try:
        for p in pending:
            original = None if p.created else p.path.read_bytes()
            if p.data == DELETE:
                p.path.unlink()
                written.append((p, original))
                continue
            p.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.path.with_name(p.path.name + ".arch37.tmp")
            tmp.write_bytes(p.data or b"")
            tmp.replace(p.path)
            written.append((p, original))
    except OSError as exc:
        for done, original in reversed(written):
            try:
                if original is None:
                    done.path.unlink(missing_ok=True)
                else:
                    done.path.parent.mkdir(parents=True, exist_ok=True)
                    done.path.write_bytes(original)
            except OSError:
                print(f"  !!    could not restore {done.relpath}; restore it from git")
        print(f"  FAIL  write failed ({exc}); {len(written)} file(s) restored")
        return 1

    for p in planned:
        verb = "WROTE" if p.data is not None else "OK   "
        print(f"  {verb} {p.relpath}: {p.message}")
    print(f"\n{len(pending)} file(s) changed.")
    if pending:
        print(f"Next: alembic upgrade head (-> {HEAD_AFTER}), then python verify_arch37.py --db --mutate")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-37 apply")
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    print("ARCH-37 — Enterprise Flow Builder & Commercial Action Catalog (Tranches 1 + 2)")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()
    return run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())
