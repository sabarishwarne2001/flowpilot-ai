"""ARCH-49 Step 1 — EXPAND: Process Intelligence & the Governed Exception Agent.

Revision ID: arch49_step1_process_intelligence
Revises: arch48_step1_collaborative_review

ARCH49-S1:migration. Expand only. The whole of ARCH-49 in one revision: nothing
existing is dropped or narrowed; the review hub's view is not touched.

CHAIN POSITION
==============
Inserted between arch48_step1_collaborative_review (the ARCH-48 release head)
and arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as
hm1 and ARCH-41 to 48 were: the contract step now revises this revision, so
there is still ONE file head. run_arch49.ps1 upgrades to this revision by name
and handles a database whose contract already ran.

WHERE THE EVENT LOG LIVES, AND WHY
==================================
A MATERIALIZED table built incrementally, not views over the sources: the
outbox prunes published events (ix_outbox_events_prunable), jobs are swept,
`case_rule_results` is deleted and rewritten on every evaluation, and a review
lock's row is deleted when it is released -- a view would forget exactly the
history process mining is for. `audit_logs` cannot be pruned but is read, never
copied wholesale: only the rows that are process steps become events.

  process_events         one row per event: the source it came from and a key
                         unique per source (so a re-read writes nothing twice),
                         a dotted activity name (CHECKed: no free text can be an
                         activity), when, who (PERSON / SYSTEM / AGENT) and
                         attributes that are ids, enums and numbers only.
  process_event_objects  OBJECT-CENTRIC: the objects each event touched (a
                         document, a case, a posting, a review item, a radar
                         finding, a flow execution), with the event's time and
                         activity copied in so a trace is one index scan.
  process_ingest_cursors per workspace and source, how far ingestion has read.

SLA PREDICTION
==============
  process_sla_policies   per workspace and object type: the target, the
                         probability at which an instance is AT_RISK, alerts on.
  process_model_runs     every fit, ACCEPTED or REFUSED. An ACCEPTED run must
                         beat the base rate's Brier score on held-out instances:
                         the CHECK refuses anything else (NULL-safe).
  process_predictions    the latest prediction per open instance.

THE AGENT
=========
  agent_policies         per workspace: planning on/off, auto-apply (off by
                         default), which auto-capable kinds, the undo hold.
  agent_proposals        one row per proposal. At most ONE live proposal per
                         subject (partial UNIQUE). Only the two auto-capable
                         kinds can ever be AUTO_SCHEDULED or AUTO_APPLIED
                         (CHECK). `action` is the typed tool call; evidence,
                         rationale and autonomy are ids, numbers and template
                         keys -- no document text is stored.
  agent_tool_calls       the typed tool calls behind each proposal, in order.

The outbox vocabulary gains ONE internal trigger event,
trigger.process.sla_at_risk (ck_outbox_events_visibility_vocabulary rebuilt
from arch47's list plus it; ARCH-48 added none).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch49_step1_process_intelligence"
down_revision = "arch48_step1_collaborative_review"
branch_labels = None
depends_on = None

#: ARCH49-S1:vocabulary. Mirrors app/services/process_intel/vocabulary.py and
#: app/services/process_intel/agent/vocabulary.py (verify_arch49 T2).
SOURCES = ("OUTBOX", "AUDIT", "JOB", "REVIEW", "ASSIGNMENT", "LOCK", "THREAD", "EXECUTION", "NODE_RUN", "CASE",
           "CASE_DOCUMENT", "CASE_RULE", "POSTING", "POSTING_ATTEMPT", "FINDING", "DOCUMENT", "AGENT")
OBJECT_TYPES = ("DOCUMENT", "CASE", "POSTING", "REVIEW_ITEM", "FINDING", "EXECUTION")
ACTOR_KINDS = ("PERSON", "SYSTEM", "AGENT")
ACTIVITY_PATTERN = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)*$"
MAX_ACTIVITY_CHARS = 64
SLA_OBJECT_TYPES = ("REVIEW_ITEM", "CASE", "POSTING")
MIN_SLA_HOURS = 1
MAX_SLA_HOURS = 24 * 90
PREDICTION_STATES = ("AT_RISK", "OK", "BREACHED")
RUN_STATUSES = ("ACCEPTED", "REFUSED")
PROPOSAL_STATUSES = ("PROPOSED", "AUTO_SCHEDULED", "APPLIED", "AUTO_APPLIED", "REJECTED", "UNDONE", "SUPERSEDED",
                     "FAILED")
SUBJECT_TYPES = ("REVIEW_ITEM", "CASE")
PROPOSAL_KINDS = ("extraction.approve_consensus", "assertion.accept_engine", "anomaly.confirm", "anomaly.dismiss",
                  "merge.merge", "merge.separate", "split.approve", "table.accept", "corroboration.confirm",
                  "posting.retry", "review.route", "case.reevaluate", "case.request_document")
AUTO_CAPABLE_KINDS = ("extraction.approve_consensus", "assertion.accept_engine")
REJECT_REASONS = ("WRONG_DECISION", "WRONG_EVIDENCE", "NEEDS_CONTEXT", "NOT_NOW", "OTHER")
MIN_HOLD_MINUTES = 5
MAX_HOLD_MINUTES = 1440
TOOLS = ("read.review_item", "read.verification", "read.assertion", "read.finding", "read.precedent",
         "read.merge_candidate", "read.split", "read.table", "read.corroboration", "read.obligation", "read.posting",
         "read.case", "read.calibration", "read.lock", "read.threads", "agent.resolve_review_item",
         "agent.assign_review_item", "agent.reevaluate_case", "agent.request_case_document")
TOOL_OUTCOMES = ("OK", "EMPTY", "REFUSED")

#: ARCH49-S1:trigger-events. The one new INTERNAL trigger event.
NEW_TRIGGER_EVENTS = ("trigger.process.sla_at_risk",)

TABLES_IN_DROP_ORDER = ("agent_tool_calls", "agent_proposals", "agent_policies", "process_predictions",
                        "process_model_runs", "process_sla_policies", "process_ingest_cursors",
                        "process_event_objects", "process_events")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step47():
    return _load("arch47_step1_erp_posting")


def internal_after_49() -> tuple[str, ...]:
    """ARCH-47's internal events (ARCH-48 added none) plus ARCH-49's one."""
    return tuple(_step47().internal_after_47()) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _load("arch40_step0_review_vocabulary")._visibility_check(values)


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE process_events (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            source varchar(16) NOT NULL,
            source_key varchar(200) NOT NULL,
            activity varchar({MAX_ACTIVITY_CHARS}) NOT NULL,
            occurred_at timestamptz NOT NULL,
            actor_kind varchar(8) NOT NULL,
            actor_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            attributes jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            ingested_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_process_events_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT uq_process_events_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_process_events_source UNIQUE (source, source_key),
            CONSTRAINT ck_process_events_source CHECK (source IN ({_in(SOURCES)})),
            CONSTRAINT ck_process_events_activity CHECK (
                activity IS NOT NULL AND length(activity) BETWEEN 1 AND {MAX_ACTIVITY_CHARS}
                AND activity ~ '{ACTIVITY_PATTERN}'),
            CONSTRAINT ck_process_events_source_key CHECK (source_key IS NOT NULL AND length(source_key) >= 1),
            CONSTRAINT ck_process_events_actor CHECK (
                actor_kind IN ({_in(ACTOR_KINDS)}) AND (actor_user_id IS NULL OR actor_kind IN ('PERSON', 'AGENT'))),
            CONSTRAINT ck_process_events_attributes CHECK (jsonb_typeof(attributes) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_process_events_workspace_time ON process_events (workspace_id, occurred_at)")
    op.execute("CREATE INDEX ix_process_events_workspace_source ON process_events (workspace_id, source, occurred_at)")

    op.execute(
        f"""
        CREATE TABLE process_event_objects (
            event_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            object_type varchar(16) NOT NULL,
            object_id uuid NOT NULL,
            qualifier varchar(32) NOT NULL DEFAULT '',
            occurred_at timestamptz NOT NULL,
            activity varchar({MAX_ACTIVITY_CHARS}) NOT NULL,
            CONSTRAINT pk_process_event_objects PRIMARY KEY (event_id, object_type, object_id),
            CONSTRAINT fk_process_event_objects_event FOREIGN KEY (event_id, workspace_id)
                REFERENCES process_events (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_process_event_objects_type CHECK (object_type IN ({_in(OBJECT_TYPES)})),
            CONSTRAINT ck_process_event_objects_qualifier CHECK (
                qualifier IS NOT NULL AND length(qualifier) <= 32 AND qualifier ~ '^[a-z_]*$'),
            CONSTRAINT ck_process_event_objects_activity CHECK (
                activity IS NOT NULL AND length(activity) BETWEEN 1 AND {MAX_ACTIVITY_CHARS}
                AND activity ~ '{ACTIVITY_PATTERN}')
        )
        """
    )
    op.execute("CREATE INDEX ix_process_event_objects_object ON process_event_objects "
               "(workspace_id, object_type, object_id, occurred_at)")
    op.execute("CREATE INDEX ix_process_event_objects_type_time ON process_event_objects "
               "(workspace_id, object_type, occurred_at)")

    op.execute(
        f"""
        CREATE TABLE process_ingest_cursors (
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            source varchar(16) NOT NULL,
            watermark timestamptz NOT NULL,
            watermark_key varchar(200) NOT NULL DEFAULT '',
            caught_up boolean NOT NULL DEFAULT true,
            last_run_at timestamptz NOT NULL,
            rows_read integer NOT NULL DEFAULT 0,
            events_written integer NOT NULL DEFAULT 0,
            CONSTRAINT pk_process_ingest_cursors PRIMARY KEY (workspace_id, source),
            CONSTRAINT ck_process_ingest_cursors_source CHECK (source IN ({_in(SOURCES)})),
            CONSTRAINT ck_process_ingest_cursors_counts CHECK (rows_read >= 0 AND events_written >= 0)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE process_sla_policies (
            workspace_id uuid NOT NULL REFERENCES workspaces (id) ON DELETE CASCADE,
            object_type varchar(16) NOT NULL,
            target_hours integer NOT NULL,
            at_risk_probability numeric(4,3) NOT NULL DEFAULT 0.5,
            alerts_enabled boolean NOT NULL DEFAULT true,
            updated_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT pk_process_sla_policies PRIMARY KEY (workspace_id, object_type),
            CONSTRAINT ck_process_sla_policies_type CHECK (object_type IN ({_in(SLA_OBJECT_TYPES)})),
            CONSTRAINT ck_process_sla_policies_target CHECK (target_hours BETWEEN {MIN_SLA_HOURS} AND {MAX_SLA_HOURS}),
            CONSTRAINT ck_process_sla_policies_risk CHECK (at_risk_probability > 0 AND at_risk_probability < 1)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE process_model_runs (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            object_type varchar(16) NOT NULL,
            status varchar(10) NOT NULL,
            reason text NULL,
            target_hours integer NOT NULL,
            instances_train integer NOT NULL DEFAULT 0,
            instances_holdout integer NOT NULL DEFAULT 0,
            snapshots_train integer NOT NULL DEFAULT 0,
            snapshots_holdout integer NOT NULL DEFAULT 0,
            base_rate numeric(6,5) NULL,
            brier numeric(8,6) NULL,
            brier_baseline numeric(8,6) NULL,
            skill numeric(9,5) NULL,
            reliability jsonb NOT NULL DEFAULT '[]'::jsonb,
            importances jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            engine_version varchar(16) NOT NULL,
            trained_at timestamptz NOT NULL,
            CONSTRAINT fk_process_model_runs_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_process_model_runs_type CHECK (object_type IN ({_in(SLA_OBJECT_TYPES)})),
            CONSTRAINT ck_process_model_runs_status CHECK (status IN ({_in(RUN_STATUSES)})),
            CONSTRAINT ck_process_model_runs_counts CHECK (
                instances_train >= 0 AND instances_holdout >= 0 AND snapshots_train >= 0 AND snapshots_holdout >= 0),
            CONSTRAINT ck_process_model_runs_brier_range CHECK (
                (brier IS NULL OR (brier >= 0 AND brier <= 1))
                AND (brier_baseline IS NULL OR (brier_baseline >= 0 AND brier_baseline <= 1))
                AND (base_rate IS NULL OR (base_rate >= 0 AND base_rate <= 1))),
            CONSTRAINT ck_process_model_runs_accepted_beats_baseline CHECK (
                status <> 'ACCEPTED' OR (brier IS NOT NULL AND brier_baseline IS NOT NULL AND skill IS NOT NULL
                                         AND brier < brier_baseline AND skill > 0)),
            CONSTRAINT ck_process_model_runs_refusal_says_why CHECK (
                status <> 'REFUSED' OR (reason IS NOT NULL AND length(reason) >= 1)),
            CONSTRAINT ck_process_model_runs_json CHECK (
                jsonb_typeof(reliability) = 'array' AND jsonb_typeof(importances) = 'object')
        )
        """
    )
    op.execute("CREATE INDEX ix_process_model_runs_latest ON process_model_runs (workspace_id, object_type, trained_at DESC)")

    op.execute(
        f"""
        CREATE TABLE process_predictions (
            workspace_id uuid NOT NULL,
            organization_id uuid NOT NULL,
            object_type varchar(16) NOT NULL,
            object_id uuid NOT NULL,
            run_id uuid NOT NULL REFERENCES process_model_runs (id) ON DELETE CASCADE,
            kind varchar(16) NULL,
            started_at timestamptz NOT NULL,
            due_at timestamptz NOT NULL,
            probability numeric(6,5) NOT NULL,
            state varchar(10) NOT NULL,
            predicted_at timestamptz NOT NULL,
            alerted_at timestamptz NULL,
            CONSTRAINT pk_process_predictions PRIMARY KEY (workspace_id, object_type, object_id),
            CONSTRAINT fk_process_predictions_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_process_predictions_type CHECK (object_type IN ({_in(SLA_OBJECT_TYPES)})),
            CONSTRAINT ck_process_predictions_state CHECK (state IN ({_in(PREDICTION_STATES)})),
            CONSTRAINT ck_process_predictions_probability CHECK (probability >= 0 AND probability <= 1),
            CONSTRAINT ck_process_predictions_due CHECK (due_at > started_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_process_predictions_state ON process_predictions (workspace_id, state, probability DESC)")

    op.execute(
        f"""
        CREATE TABLE agent_policies (
            workspace_id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            planning_enabled boolean NOT NULL DEFAULT true,
            auto_apply_enabled boolean NOT NULL DEFAULT false,
            auto_apply_kinds varchar(40)[] NOT NULL DEFAULT '{{}}',
            hold_minutes integer NOT NULL DEFAULT 30,
            enabled_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            enabled_at timestamptz NULL,
            updated_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_agent_policies_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_agent_policies_hold CHECK (hold_minutes BETWEEN {MIN_HOLD_MINUTES} AND {MAX_HOLD_MINUTES}),
            CONSTRAINT ck_agent_policies_kinds CHECK (
                auto_apply_kinds <@ ARRAY[{_in(AUTO_CAPABLE_KINDS)}]::varchar[]),
            CONSTRAINT ck_agent_policies_enabled CHECK (NOT auto_apply_enabled OR enabled_at IS NOT NULL)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE agent_proposals (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            subject_type varchar(12) NOT NULL,
            subject_kind varchar(16) NOT NULL,
            subject_id uuid NOT NULL,
            work_item_id uuid NULL,
            proposal_kind varchar(40) NOT NULL,
            action jsonb NOT NULL,
            subject_version bigint NOT NULL DEFAULT 0,
            confidence numeric(6,5) NOT NULL,
            calibrated_probability numeric(6,5) NULL,
            calibration_model_id uuid NULL,
            evidence jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            rationale jsonb NOT NULL DEFAULT '[]'::jsonb,
            autonomy jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            injection_flags jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            status varchar(16) NOT NULL DEFAULT 'PROPOSED',
            apply_after timestamptz NULL,
            waiting_until timestamptz NULL,
            decided_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            decided_at timestamptz NULL,
            reject_reason varchar(16) NULL,
            applied_at timestamptz NULL,
            applied_as_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            resolution varchar(64) NULL,
            failure varchar(300) NULL,
            engine_version varchar(16) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_agent_proposals_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT uq_agent_proposals_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT ck_agent_proposals_subject_type CHECK (subject_type IN ({_in(SUBJECT_TYPES)})),
            CONSTRAINT ck_agent_proposals_kind CHECK (proposal_kind IN ({_in(PROPOSAL_KINDS)})),
            CONSTRAINT ck_agent_proposals_status CHECK (status IN ({_in(PROPOSAL_STATUSES)})),
            CONSTRAINT ck_agent_proposals_reject_reason CHECK (
                reject_reason IS NULL OR reject_reason IN ({_in(REJECT_REASONS)})),
            CONSTRAINT ck_agent_proposals_json CHECK (
                jsonb_typeof(action) = 'object' AND jsonb_typeof(evidence) = 'object'
                AND jsonb_typeof(rationale) = 'array' AND jsonb_typeof(autonomy) = 'object'
                AND jsonb_typeof(injection_flags) = 'object'),
            CONSTRAINT ck_agent_proposals_confidence CHECK (
                confidence >= 0 AND confidence <= 1
                AND (calibrated_probability IS NULL OR (calibrated_probability >= 0 AND calibrated_probability <= 1))),
            CONSTRAINT ck_agent_proposals_version CHECK (subject_version >= 0),
            CONSTRAINT ck_agent_proposals_only_calibrated_kinds_apply_themselves CHECK (
                status NOT IN ('AUTO_SCHEDULED', 'AUTO_APPLIED') OR proposal_kind IN ({_in(AUTO_CAPABLE_KINDS)})),
            CONSTRAINT ck_agent_proposals_scheduled CHECK (status <> 'AUTO_SCHEDULED' OR apply_after IS NOT NULL),
            CONSTRAINT ck_agent_proposals_decided CHECK (
                status NOT IN ('APPLIED', 'REJECTED', 'UNDONE') OR decided_at IS NOT NULL),
            CONSTRAINT ck_agent_proposals_rejected CHECK (status <> 'REJECTED' OR reject_reason IS NOT NULL),
            CONSTRAINT ck_agent_proposals_applied CHECK (
                status NOT IN ('APPLIED', 'AUTO_APPLIED') OR applied_at IS NOT NULL),
            CONSTRAINT ck_agent_proposals_auto_nobody_decided CHECK (
                status <> 'AUTO_APPLIED' OR decided_by_user_id IS NULL)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_agent_proposals_live ON agent_proposals (subject_type, subject_id) "
               "WHERE status IN ('PROPOSED', 'AUTO_SCHEDULED')")
    op.execute("CREATE INDEX ix_agent_proposals_workspace ON agent_proposals (workspace_id, status, created_at DESC)")
    op.execute("CREATE INDEX ix_agent_proposals_due ON agent_proposals (apply_after) WHERE status = 'AUTO_SCHEDULED'")
    op.execute("CREATE INDEX ix_agent_proposals_subject ON agent_proposals (subject_id)")

    op.execute(
        f"""
        CREATE TABLE agent_tool_calls (
            id uuid PRIMARY KEY,
            proposal_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            seq integer NOT NULL,
            tool varchar(40) NOT NULL,
            arguments jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            outcome varchar(8) NOT NULL,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_agent_tool_calls_proposal FOREIGN KEY (proposal_id, workspace_id)
                REFERENCES agent_proposals (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT uq_agent_tool_calls_seq UNIQUE (proposal_id, seq),
            CONSTRAINT ck_agent_tool_calls_tool CHECK (tool IN ({_in(TOOLS)})),
            CONSTRAINT ck_agent_tool_calls_outcome CHECK (outcome IN ({_in(TOOL_OUTCOMES)})),
            CONSTRAINT ck_agent_tool_calls_json CHECK (
                jsonb_typeof(arguments) = 'object' AND jsonb_typeof(detail) = 'object'),
            CONSTRAINT ck_agent_tool_calls_seq CHECK (seq >= 1)
        )
        """
    )

    _visibility_check(internal_after_49())


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type IN ('trigger.process.sla_at_risk')")
    _visibility_check(tuple(_step47().internal_after_47()))
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
