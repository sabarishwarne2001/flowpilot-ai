"""ARCH-39 — Conversational AI Suite: session columns, scope items, templates.

Revision ID: arch39_step1_conversations
Revises: arch35_step1_calibration

EXPAND ONLY
===========

Every column added to `conversations` is nullable or carries a server
default, so ARCH-36 code keeps working against this schema during a rolling
deploy. Nothing is dropped.

WHAT THE DATABASE REFUSES
=========================

  ck_conversations_scope_mode_known
      scope_mode is one of WORKSPACE, SELECTED, DOCUMENT.
  ck_conversations_scope_matches_document
      a conversation is DOCUMENT-scoped exactly when it has a work item, so a
      document chat can never be widened to the workspace by an update.
  trg_conversation_scope_same_workspace
      a scope row's conversation and work item both belong to the row's
      workspace. Without it, one bad service call is a cross-tenant search.
  uq_prompt_templates_org_name_live
      one live template per name per organization, case-insensitively.

`trg_conversation_messages_last_at` keeps `conversations.last_message_at`
current from both chat paths without either having to remember to.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "arch39_step1_conversations"
down_revision = "arch35_step1_calibration"
branch_labels = None
depends_on = None

SCOPE_MODES: tuple[str, ...] = ("WORKSPACE", "SELECTED", "DOCUMENT")


def upgrade() -> None:
    # ---- conversations -------------------------------------------------
    op.add_column("conversations", sa.Column("pinned_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("conversations", sa.Column("model_override", sa.String(96), nullable=True))
    op.add_column("conversations", sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("scope_mode", sa.String(16), nullable=False, server_default="WORKSPACE"),
    )

    op.execute(
        "UPDATE conversations SET scope_mode = 'DOCUMENT' WHERE work_item_id IS NOT NULL"
    )
    op.execute(
        """
        UPDATE conversations AS c
           SET last_message_at = m.last_at
          FROM (
                SELECT conversation_id, max(created_at) AS last_at
                  FROM conversation_messages
                 GROUP BY conversation_id
               ) AS m
         WHERE m.conversation_id = c.id
        """
    )

    modes = ", ".join(f"'{mode}'" for mode in SCOPE_MODES)
    op.create_check_constraint(
        "ck_conversations_scope_mode_known",
        "conversations",
        f"scope_mode IN ({modes})",
    )
    op.create_check_constraint(
        "ck_conversations_scope_matches_document",
        "conversations",
        "(scope_mode = 'DOCUMENT') = (work_item_id IS NOT NULL)",
    )
    op.create_index(
        "ix_conversations_session_list",
        "conversations",
        ["workspace_id", "user_id", "archived_at", "pinned_at", "last_message_at"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fp_conversation_messages_last_at()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            UPDATE conversations
               SET last_message_at = GREATEST(
                       COALESCE(last_message_at, NEW.created_at),
                       NEW.created_at
                   )
             WHERE id = NEW.conversation_id;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversation_messages_last_at
        AFTER INSERT ON conversation_messages
        FOR EACH ROW EXECUTE FUNCTION fp_conversation_messages_last_at();
        """
    )

    # ---- conversation_scope_items --------------------------------------
    op.create_table(
        "conversation_scope_items",
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "work_item_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("work_items.id", ondelete="CASCADE"),
            primary_key=True,
        ),
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
    op.create_index(
        "ix_conversation_scope_items_work_item",
        "conversation_scope_items",
        ["work_item_id"],
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION fp_conversation_scope_same_workspace()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            conversation_workspace uuid;
            conversation_document uuid;
            item_workspace uuid;
        BEGIN
            SELECT workspace_id, work_item_id
              INTO conversation_workspace, conversation_document
              FROM conversations
             WHERE id = NEW.conversation_id;

            SELECT workspace_id INTO item_workspace
              FROM work_items
             WHERE id = NEW.work_item_id;

            IF conversation_workspace IS DISTINCT FROM NEW.workspace_id
               OR item_workspace IS DISTINCT FROM NEW.workspace_id THEN
                RAISE EXCEPTION
                    'conversation_scope_items_cross_workspace: conversation %, work item % and row workspace % disagree',
                    NEW.conversation_id, NEW.work_item_id, NEW.workspace_id
                    USING ERRCODE = 'check_violation';
            END IF;

            IF conversation_document IS NOT NULL THEN
                RAISE EXCEPTION
                    'conversation_scope_items_document_chat: conversation % is a document conversation',
                    NEW.conversation_id
                    USING ERRCODE = 'check_violation';
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_conversation_scope_same_workspace
        BEFORE INSERT OR UPDATE ON conversation_scope_items
        FOR EACH ROW EXECUTE FUNCTION fp_conversation_scope_same_workspace();
        """
    )

    # ---- prompt_templates ----------------------------------------------
    op.create_table(
        "prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "created_by_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 80",
            name="ck_prompt_templates_name_length",
        ),
        sa.CheckConstraint(
            "char_length(body) BETWEEN 1 AND 8000",
            name="ck_prompt_templates_body_length",
        ),
    )
    op.create_index(
        "ix_prompt_templates_organization_id", "prompt_templates", ["organization_id"]
    )
    op.execute(
        """
        CREATE UNIQUE INDEX uq_prompt_templates_org_name_live
            ON prompt_templates (organization_id, lower(name))
         WHERE archived_at IS NULL
        """
    )


def downgrade() -> None:
    """Remove everything ARCH-39 added. Session metadata is lost; messages are not."""
    op.execute("DROP INDEX IF EXISTS uq_prompt_templates_org_name_live")
    op.drop_index("ix_prompt_templates_organization_id", table_name="prompt_templates")
    op.drop_table("prompt_templates")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_conversation_scope_same_workspace ON conversation_scope_items"
    )
    op.execute("DROP FUNCTION IF EXISTS fp_conversation_scope_same_workspace()")
    op.drop_index("ix_conversation_scope_items_work_item", table_name="conversation_scope_items")
    op.drop_table("conversation_scope_items")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_conversation_messages_last_at ON conversation_messages"
    )
    op.execute("DROP FUNCTION IF EXISTS fp_conversation_messages_last_at()")
    op.drop_index("ix_conversations_session_list", table_name="conversations")
    op.drop_constraint("ck_conversations_scope_matches_document", "conversations", type_="check")
    op.drop_constraint("ck_conversations_scope_mode_known", "conversations", type_="check")
    for column in ("scope_mode", "last_message_at", "model_override", "archived_at", "pinned_at"):
        op.drop_column("conversations", column)
