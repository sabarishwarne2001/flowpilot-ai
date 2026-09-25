"""ARCH-46 Step 1 — EXPAND: Obligations & Temporal Intelligence.

Revision ID: arch46_step1_obligations
Revises: arch45_step1_corroboration

ARCH46-S1:migration. Expand only. The whole of ARCH-46 in one revision:
nothing existing is dropped or narrowed.

CHAIN POSITION
==============

Inserted between arch45_step1_corroboration (the ARCH-45 release head) and
arch40_step3_contract_ai_settings (the flag-gated, lossy contract step), as hm1
and ARCH-41 to 45 were: the contract step now revises this revision, so there
is still ONE file head and `alembic upgrade head` still refuses the contract
without ARCH40_CONTRACT. run_arch46.ps1 upgrades to this revision by name and
handles a database whose contract already ran.

WHAT IT ADDS
============

  holiday_calendars     a workspace's stored business-day calendars: weekend
                        days (ISO weekdays) and holidays as a typed date[] with
                        their names (CHECK: the same length, strictly ascending,
                        at most 2000). Business days are computed from these
                        rows only -- no calendar API. One default per workspace
                        (partial UNIQUE index).
  obligations           renewal, notice, payment, delivery, expiry, reporting
                        (and a person's own): owner (a workspace MEMBER, by a
                        composite FK onto workspace_members, so losing access
                        clears the owner), state (OPEN / DUE_SOON / OVERDUE /
                        DONE / WAIVED, CHECK-tied to its timestamps), the due
                        date (a LOCAL date) and the rule it follows (FIXED,
                        OFFSET from another obligation or a stated date, an RRULE
                        SERIES, or NONE), evidence spans (a trigger refuses a
                        span on another document), and for extracted ones the
                        source key that makes re-extraction idempotent (partial
                        UNIQUE per document). Hangs off the document (CASCADE),
                        the ARCH-42 party (SET NULL on that column only), its
                        anchor obligation (SET NULL; a trigger refuses cycles)
                        and its calendar (SET NULL). Deleting a document
                        unlinks a person's own obligations first (trigger
                        trg_work_items_unlink_obligations): only what was read
                        from the document goes with it.
  obligation_events     every transition. A partial UNIQUE index makes DUE_SOON
                        and OVERDUE exactly-once per obligation and due date:
                        the sweep can run any number of times.
  calendar_feed_tokens  signed, revocable iCal feed tokens: only the SHA-256
                        of the token is stored (hex CHECK, UNIQUE); the member
                        FK cascades, so a person who loses access loses their
                        feeds with it.
  outbox vocabulary     trigger.obligation.due_soon and trigger.obligation.overdue
                        become legal INTERNAL events (the ARCH-37 CHECK is
                        rebuilt, same shape).
  review hub            an eighth kind, OBLIGATION (reason OBLIGATION_UNCONFIRMED):
                        the CHECK is recreated and the view is ARCH-45's text
                        plus one arm (loaded, not copied).
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from alembic import op

revision = "arch46_step1_obligations"
down_revision = "arch45_step1_corroboration"
branch_labels = None
depends_on = None

#: ARCH46-S1:vocabulary. Mirrors app/services/obligations/vocabulary.py (verify_arch46 T2).
KINDS = ("RENEWAL", "NOTICE", "PAYMENT", "DELIVERY", "EXPIRY", "REPORTING", "OTHER")
STATES = ("OPEN", "DUE_SOON", "OVERDUE", "DONE", "WAIVED")
ACTIVE_STATES = ("OPEN", "DUE_SOON", "OVERDUE")
ORIGINS = ("EXTRACTED", "MANUAL")
REVIEWS = ("AUTO", "PENDING", "CONFIRMED", "REJECTED")
RULE_KINDS = ("FIXED", "OFFSET", "SERIES", "NONE")
ROLLS = ("NONE", "FOLLOWING", "PRECEDING", "MODIFIED_FOLLOWING")
EVENT_KINDS = ("CREATED", "UPDATED", "DUE_SOON", "OVERDUE", "REOPENED", "DONE", "WAIVED", "ADVANCED", "RESCHEDULED",
               "CONFIRMED", "REJECTED", "SUPERSEDED")
ALERT_EVENT_KINDS = ("DUE_SOON", "OVERDUE")
CALENDAR_SOURCES = ("TEMPLATE", "MANUAL", "ICS")
FEED_SCOPES = ("ALL", "MINE")

#: ARCH46-S1:review-kinds. ARCH-45's seven + OBLIGATION.
REVIEW_KINDS = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION", "OBLIGATION")
REVIEW_KINDS_BEFORE = ("EXTRACTION", "ASSERTION", "ANOMALY", "MERGE", "SPLIT", "TABLE", "CORROBORATION")
#: ARCH46-S1:review-reasons. ARCH-45's eleven + OBLIGATION_UNCONFIRMED.
REVIEW_REASONS = (
    "DISAGREEMENT", "ESCALATION", "CALIBRATION_HOLD", "AUTONOMY_AUDIT",
    "PENDING_REVIEW", "CLAUSE_TRIAGE", "ANOMALY", "ENTITY_MERGE", "PACKET_SPLIT", "TABLE_ARITHMETIC",
    "MATERIAL_DISCREPANCY", "OBLIGATION_UNCONFIRMED",
)
#: ARCH46-S1:trigger-events. The two new INTERNAL trigger events.
NEW_TRIGGER_EVENTS = ("trigger.obligation.due_soon", "trigger.obligation.overdue")

TABLES_IN_DROP_ORDER = ("obligation_events", "calendar_feed_tokens", "obligations", "holiday_calendars")


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(f"{name}.py"))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _step45():
    return _load("arch45_step1_corroboration")


def internal_after_46() -> tuple[str, ...]:
    return tuple(_step45().internal_after_45()) + NEW_TRIGGER_EVENTS


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{x}'" for x in values)


#: ARCH46-S1:review-view-v7. ARCH-45's view, unchanged, plus an OBLIGATION arm.
#: An extracted obligation read with a doubt (review PENDING) is OPEN until a
#: person confirms or rejects it; one due within 30 days (or with no date yet)
#: is HIGH. A superseded one leaves the hub unless someone already reviewed it.
OBLIGATION_ARM = """
    UNION ALL

    SELECT
        'OBLIGATION'::varchar(16),
        o.id,
        o.organization_id,
        o.workspace_id,
        o.work_item_id,
        ('Confirm obligation: ' || o.title
            || CASE WHEN o.due_date IS NULL THEN ' (no date yet)'
                    ELSE ' - due ' || to_char(o.due_date, 'DD Mon YYYY') END)::text,
        CASE WHEN o.due_date IS NULL OR o.due_date <= CURRENT_DATE + 30 THEN 'HIGH' ELSE 'MEDIUM' END::varchar(8),
        CASE WHEN o.due_date IS NULL OR o.due_date <= CURRENT_DATE + 30 THEN 2 ELSE 3 END::integer,
        o.confidence::numeric(5, 4),
        o.created_at,
        CASE WHEN o.review = 'PENDING' AND o.superseded_at IS NULL THEN 'OPEN' ELSE 'RESOLVED' END::varchar(8),
        o.reviewed_at,
        o.reviewed_by_user_id,
        'OBLIGATION_UNCONFIRMED'::varchar(24)
    FROM obligations o
    WHERE o.origin = 'EXTRACTED'
      AND ((o.review = 'PENDING' AND o.superseded_at IS NULL) OR o.reviewed_at IS NOT NULL)
"""


def review_queue_view_v7() -> str:
    return _step45().review_queue_view_v6().rstrip() + "\n" + OBLIGATION_ARM


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION obligations_dates_ascending(dates date[]) RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
            SELECT coalesce(bool_and(a < b), true)
            FROM (SELECT d AS a, lead(d) OVER (ORDER BY ord) AS b FROM unnest(dates) WITH ORDINALITY AS u(d, ord)) s
            WHERE b IS NOT NULL
        $$
        """
    )
    op.execute(
        f"""
        CREATE TABLE holiday_calendars (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            name varchar(120) NOT NULL,
            source varchar(10) NOT NULL,
            template_code varchar(20) NULL,
            region varchar(8) NULL,
            weekend_days smallint[] NOT NULL DEFAULT '{{6,7}}',
            holidays date[] NOT NULL DEFAULT '{{}}',
            holiday_names text[] NOT NULL DEFAULT '{{}}',
            is_default boolean NOT NULL DEFAULT false,
            revision integer NOT NULL DEFAULT 1,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT uq_holiday_calendars_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT uq_holiday_calendars_name UNIQUE (workspace_id, name),
            CONSTRAINT fk_holiday_calendars_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT ck_holiday_calendars_source CHECK (source IN ({_in(CALENDAR_SOURCES)})),
            CONSTRAINT ck_holiday_calendars_template CHECK ((source = 'TEMPLATE') = (template_code IS NOT NULL)),
            CONSTRAINT ck_holiday_calendars_names CHECK (cardinality(holidays) = cardinality(holiday_names)),
            CONSTRAINT ck_holiday_calendars_size CHECK (cardinality(holidays) <= 2000),
            CONSTRAINT ck_holiday_calendars_sorted CHECK (obligations_dates_ascending(holidays)),
            CONSTRAINT ck_holiday_calendars_weekend CHECK (cardinality(weekend_days) <= 6
                AND weekend_days <@ ARRAY[1, 2, 3, 4, 5, 6, 7]::smallint[]),
            CONSTRAINT ck_holiday_calendars_name_length CHECK (length(btrim(name)) BETWEEN 1 AND 120),
            CONSTRAINT ck_holiday_calendars_revision CHECK (revision >= 1)
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_holiday_calendars_default ON holiday_calendars (workspace_id) WHERE is_default")

    op.execute(
        f"""
        CREATE TABLE obligations (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            work_item_id uuid NULL,
            entity_id uuid NULL,
            anchor_obligation_id uuid NULL,
            calendar_id uuid NULL,
            owner_user_id uuid NULL,
            kind varchar(12) NOT NULL,
            title varchar(300) NOT NULL,
            description text NULL,
            origin varchar(10) NOT NULL,
            review varchar(10) NOT NULL,
            state varchar(10) NOT NULL DEFAULT 'OPEN',
            due_date date NULL,
            due_rule jsonb NOT NULL,
            recurrence varchar(300) NULL,
            series_start date NULL,
            occurrence integer NOT NULL DEFAULT 1,
            completed_occurrences integer NOT NULL DEFAULT 0,
            business_day_rule varchar(20) NOT NULL DEFAULT 'NONE',
            lead_days smallint NOT NULL,
            amount numeric(18, 2) NULL,
            currency char(3) NULL,
            counterparty_name varchar(300) NULL,
            confidence numeric(5, 4) NOT NULL DEFAULT 1,
            reasons jsonb NOT NULL DEFAULT '[]'::jsonb,
            evidence jsonb NOT NULL DEFAULT '[]'::jsonb,
            quote text NULL,
            clause_number varchar(40) NULL,
            derivation jsonb NOT NULL DEFAULT '[]'::jsonb,
            source_key char(64) NULL,
            engine_version varchar(16) NULL,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            revision integer NOT NULL DEFAULT 1,
            created_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            state_changed_at timestamptz NOT NULL DEFAULT now(),
            done_at timestamptz NULL,
            done_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            waived_at timestamptz NULL,
            waived_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            waiver_reason text NULL,
            last_completed_at timestamptz NULL,
            reviewed_at timestamptz NULL,
            reviewed_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            superseded_at timestamptz NULL,
            CONSTRAINT uq_obligations_id_workspace UNIQUE (id, workspace_id),
            CONSTRAINT fk_obligations_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_obligations_work_item FOREIGN KEY (work_item_id, workspace_id)
                REFERENCES work_items (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT fk_obligations_entity FOREIGN KEY (entity_id, workspace_id)
                REFERENCES entities (id, workspace_id) ON DELETE SET NULL (entity_id),
            CONSTRAINT fk_obligations_anchor FOREIGN KEY (anchor_obligation_id, workspace_id)
                REFERENCES obligations (id, workspace_id) ON DELETE SET NULL (anchor_obligation_id),
            CONSTRAINT fk_obligations_calendar FOREIGN KEY (calendar_id, workspace_id)
                REFERENCES holiday_calendars (id, workspace_id) ON DELETE SET NULL (calendar_id),
            CONSTRAINT fk_obligations_owner FOREIGN KEY (owner_user_id, workspace_id)
                REFERENCES workspace_members (user_id, workspace_id) ON DELETE SET NULL (owner_user_id),
            CONSTRAINT ck_obligations_kind CHECK (kind IN ({_in(KINDS)})),
            CONSTRAINT ck_obligations_state CHECK (state IN ({_in(STATES)})),
            CONSTRAINT ck_obligations_origin CHECK (origin IN ({_in(ORIGINS)})),
            CONSTRAINT ck_obligations_review CHECK (review IN ({_in(REVIEWS)})),
            CONSTRAINT ck_obligations_roll CHECK (business_day_rule IN ({_in(ROLLS)})),
            CONSTRAINT ck_obligations_rule CHECK (jsonb_typeof(due_rule) = 'object'
                AND due_rule ->> 'kind' IN ({_in(RULE_KINDS)})),
            CONSTRAINT ck_obligations_series CHECK (((due_rule ->> 'kind') = 'SERIES') = (recurrence IS NOT NULL)
                AND (recurrence IS NULL) = (series_start IS NULL)),
            CONSTRAINT ck_obligations_recurrence CHECK (recurrence IS NULL
                OR recurrence ~ '^FREQ=(DAILY|WEEKLY|MONTHLY|YEARLY)(;[A-Z]+=[A-Z0-9,+-]+)*$'),
            CONSTRAINT ck_obligations_undetermined CHECK ((due_rule ->> 'kind') <> 'NONE' OR due_date IS NULL),
            CONSTRAINT ck_obligations_alert_dated CHECK (state NOT IN ('DUE_SOON', 'OVERDUE') OR due_date IS NOT NULL),
            CONSTRAINT ck_obligations_done CHECK ((state = 'DONE') = (done_at IS NOT NULL)),
            CONSTRAINT ck_obligations_waived CHECK ((state = 'WAIVED') = (waived_at IS NOT NULL)
                AND (state <> 'WAIVED' OR waiver_reason IS NOT NULL)),
            CONSTRAINT ck_obligations_extracted CHECK (origin <> 'EXTRACTED'
                OR (work_item_id IS NOT NULL AND source_key IS NOT NULL AND engine_version IS NOT NULL)),
            CONSTRAINT ck_obligations_manual CHECK (origin <> 'MANUAL' OR (review = 'CONFIRMED' AND source_key IS NULL)),
            CONSTRAINT ck_obligations_source_key CHECK (source_key IS NULL OR source_key ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_obligations_reviewed CHECK (reviewed_by_user_id IS NULL OR reviewed_at IS NOT NULL),
            CONSTRAINT ck_obligations_confidence CHECK (confidence >= 0 AND confidence <= 1),
            CONSTRAINT ck_obligations_lead CHECK (lead_days BETWEEN 0 AND 365),
            CONSTRAINT ck_obligations_occurrence CHECK (occurrence >= 1 AND completed_occurrences >= 0 AND revision >= 1),
            CONSTRAINT ck_obligations_currency CHECK (currency IS NULL OR currency ~ '^[A-Z]{{3}}$'),
            CONSTRAINT ck_obligations_amount CHECK (amount IS NULL OR amount >= 0),
            CONSTRAINT ck_obligations_title CHECK (length(btrim(title)) BETWEEN 1 AND 300),
            CONSTRAINT ck_obligations_waiver_length CHECK (waiver_reason IS NULL OR length(waiver_reason) <= 2000),
            CONSTRAINT ck_obligations_anchor_self CHECK (anchor_obligation_id IS NULL OR anchor_obligation_id <> id),
            CONSTRAINT ck_obligations_anchor_rule CHECK (anchor_obligation_id IS NULL OR (due_rule ->> 'kind') = 'OFFSET'),
            CONSTRAINT ck_obligations_json CHECK (jsonb_typeof(reasons) = 'array' AND jsonb_typeof(evidence) = 'array'
                AND jsonb_typeof(derivation) = 'array' AND jsonb_typeof(detail) = 'object')
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_obligations_source ON obligations (work_item_id, source_key) "
               "WHERE source_key IS NOT NULL")
    op.execute("CREATE INDEX ix_obligations_due ON obligations (workspace_id, due_date) "
               f"WHERE superseded_at IS NULL AND state IN ({_in(ACTIVE_STATES)})")
    op.execute("CREATE INDEX ix_obligations_workspace ON obligations (workspace_id, created_at DESC)")
    op.execute("CREATE INDEX ix_obligations_work_item ON obligations (work_item_id)")
    op.execute("CREATE INDEX ix_obligations_entity ON obligations (entity_id) WHERE entity_id IS NOT NULL")
    op.execute("CREATE INDEX ix_obligations_owner ON obligations (owner_user_id) WHERE owner_user_id IS NOT NULL")
    op.execute("CREATE INDEX ix_obligations_anchor ON obligations (anchor_obligation_id) "
               "WHERE anchor_obligation_id IS NOT NULL")
    op.execute(
        """
        CREATE FUNCTION obligation_evidence_valid() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE span jsonb;
        BEGIN
            FOR span IN SELECT * FROM jsonb_array_elements(NEW.evidence) LOOP
                IF jsonb_typeof(span) <> 'object' OR NOT (span ? 'page') OR jsonb_typeof(span -> 'page') <> 'number'
                   OR (span ->> 'page')::numeric < 1 THEN
                    RAISE EXCEPTION 'an evidence span needs a page >= 1: %', span
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_obligations_evidence_shape';
                END IF;
                IF NEW.work_item_id IS NULL OR (span ? 'work_item_id'
                   AND span ->> 'work_item_id' <> NEW.work_item_id::text) THEN
                    RAISE EXCEPTION 'evidence points at document % which is not this obligation''s document',
                        coalesce(span ->> 'work_item_id', '(none)')
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_obligations_evidence_document';
                END IF;
            END LOOP;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_obligations_evidence BEFORE INSERT OR UPDATE OF evidence, work_item_id ON obligations "
        "FOR EACH ROW EXECUTE FUNCTION obligation_evidence_valid()"
    )
    op.execute(
        """
        CREATE FUNCTION obligation_anchor_acyclic() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE cursor_id uuid := NEW.anchor_obligation_id; depth integer := 0;
        BEGIN
            WHILE cursor_id IS NOT NULL LOOP
                IF cursor_id = NEW.id THEN
                    RAISE EXCEPTION 'an obligation cannot count from itself (anchor cycle through %)', NEW.id
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_obligations_anchor_acyclic';
                END IF;
                depth := depth + 1;
                IF depth > 4 THEN
                    RAISE EXCEPTION 'an anchor chain is at most 4 deep'
                        USING ERRCODE = '23514', CONSTRAINT = 'ck_obligations_anchor_depth';
                END IF;
                SELECT anchor_obligation_id INTO cursor_id FROM obligations WHERE id = cursor_id;
            END LOOP;
            RETURN NEW;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_obligations_anchor BEFORE INSERT OR UPDATE OF anchor_obligation_id ON obligations "
        "FOR EACH ROW EXECUTE FUNCTION obligation_anchor_acyclic()"
    )
    # ARCH46-S1:unlink-manual. Deleting a document takes the obligations READ from
    # it (FK CASCADE: they quote it); a person's own obligation that merely links
    # the document is unlinked first, never deleted with it.
    op.execute(
        """
        CREATE FUNCTION obligations_unlink_manual() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            UPDATE obligations SET work_item_id = NULL, evidence = '[]'::jsonb
            WHERE work_item_id = OLD.id AND origin = 'MANUAL';
            RETURN OLD;
        END $$
        """
    )
    op.execute(
        "CREATE TRIGGER trg_work_items_unlink_obligations BEFORE DELETE ON work_items "
        "FOR EACH ROW EXECUTE FUNCTION obligations_unlink_manual()"
    )

    op.execute(
        f"""
        CREATE TABLE obligation_events (
            id uuid PRIMARY KEY,
            obligation_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            kind varchar(12) NOT NULL,
            from_state varchar(10) NULL,
            to_state varchar(10) NULL,
            due_date date NULL,
            occurrence integer NULL,
            emitted boolean NOT NULL DEFAULT false,
            outbox_event_id uuid NULL,
            actor_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            detail jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            created_at timestamptz NOT NULL DEFAULT now(),
            CONSTRAINT fk_obligation_events_obligation FOREIGN KEY (obligation_id, workspace_id)
                REFERENCES obligations (id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_obligation_events_kind CHECK (kind IN ({_in(EVENT_KINDS)})),
            CONSTRAINT ck_obligation_events_states CHECK ((from_state IS NULL OR from_state IN ({_in(STATES)}))
                AND (to_state IS NULL OR to_state IN ({_in(STATES)}))),
            CONSTRAINT ck_obligation_events_alert CHECK (kind NOT IN ({_in(ALERT_EVENT_KINDS)})
                OR (due_date IS NOT NULL AND to_state = kind)),
            CONSTRAINT ck_obligation_events_emitted CHECK (NOT emitted OR outbox_event_id IS NOT NULL),
            CONSTRAINT ck_obligation_events_occurrence CHECK (occurrence IS NULL OR occurrence >= 1),
            CONSTRAINT ck_obligation_events_json CHECK (jsonb_typeof(detail) = 'object')
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_obligation_events_alert ON obligation_events (obligation_id, kind, due_date) "
               f"WHERE kind IN ({_in(ALERT_EVENT_KINDS)})")
    op.execute("CREATE INDEX ix_obligation_events_obligation ON obligation_events (obligation_id, created_at)")

    op.execute(
        f"""
        CREATE TABLE calendar_feed_tokens (
            id uuid PRIMARY KEY,
            organization_id uuid NOT NULL,
            workspace_id uuid NOT NULL,
            user_id uuid NOT NULL,
            label varchar(100) NOT NULL,
            scope varchar(8) NOT NULL,
            include_closed boolean NOT NULL DEFAULT true,
            token_hash char(64) NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),
            expires_at timestamptz NULL,
            last_used_at timestamptz NULL,
            use_count bigint NOT NULL DEFAULT 0,
            revoked_at timestamptz NULL,
            revoked_by_user_id uuid NULL REFERENCES users (id) ON DELETE SET NULL,
            CONSTRAINT uq_calendar_feed_tokens_hash UNIQUE (token_hash),
            CONSTRAINT fk_calendar_feed_tokens_workspace FOREIGN KEY (workspace_id, organization_id)
                REFERENCES workspaces (id, organization_id) ON DELETE CASCADE,
            CONSTRAINT fk_calendar_feed_tokens_member FOREIGN KEY (user_id, workspace_id)
                REFERENCES workspace_members (user_id, workspace_id) ON DELETE CASCADE,
            CONSTRAINT ck_calendar_feed_tokens_hash CHECK (token_hash ~ '^[0-9a-f]{{64}}$'),
            CONSTRAINT ck_calendar_feed_tokens_scope CHECK (scope IN ({_in(FEED_SCOPES)})),
            CONSTRAINT ck_calendar_feed_tokens_label CHECK (length(btrim(label)) BETWEEN 1 AND 100),
            CONSTRAINT ck_calendar_feed_tokens_uses CHECK (use_count >= 0),
            CONSTRAINT ck_calendar_feed_tokens_revoked CHECK (revoked_by_user_id IS NULL OR revoked_at IS NOT NULL),
            CONSTRAINT ck_calendar_feed_tokens_expiry CHECK (expires_at IS NULL OR expires_at > created_at)
        )
        """
    )
    op.execute("CREATE INDEX ix_calendar_feed_tokens_member ON calendar_feed_tokens (workspace_id, user_id)")

    _visibility_check(internal_after_46())

    # -- the review hub learns OBLIGATION -------------------------------------------
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS)}))"
    )
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(review_queue_view_v7())


def _visibility_check(values: tuple[str, ...]) -> None:
    """ARCH-37's constraint, same shape byte for byte (arch40_step0._visibility_check)."""
    _load("arch40_step0_review_vocabulary")._visibility_check(values)


def downgrade() -> None:
    op.execute("DELETE FROM outbox_events WHERE event_type IN ('trigger.obligation.due_soon', 'trigger.obligation.overdue')")
    _visibility_check(tuple(_step45().internal_after_45()))
    op.execute("DROP VIEW IF EXISTS review_queue_items")
    op.execute(_step45().review_queue_view_v6())
    op.execute("DELETE FROM review_assignments WHERE kind = 'OBLIGATION'")
    op.execute("ALTER TABLE review_assignments DROP CONSTRAINT ck_review_assignments_kind_known")
    op.execute(
        "ALTER TABLE review_assignments ADD CONSTRAINT ck_review_assignments_kind_known "
        f"CHECK (kind IN ({_in(REVIEW_KINDS_BEFORE)}))"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_work_items_unlink_obligations ON work_items")
    op.execute("DROP TRIGGER IF EXISTS trg_obligations_anchor ON obligations")
    op.execute("DROP TRIGGER IF EXISTS trg_obligations_evidence ON obligations")
    for table in TABLES_IN_DROP_ORDER:
        op.execute(f"DROP TABLE IF EXISTS {table}")
    op.execute("DROP FUNCTION IF EXISTS obligation_anchor_acyclic()")
    op.execute("DROP FUNCTION IF EXISTS obligations_unlink_manual()")
    op.execute("DROP FUNCTION IF EXISTS obligation_evidence_valid()")
    op.execute("DROP FUNCTION IF EXISTS obligations_dates_ascending(date[])")
