"""Helm: supplier statement reconciliation (owner addition B, flagship session).

helm_actions.kind gains draft_reconciliation: the email Helm drafts listing the differences between
a supplier's statement of account and the Promise ledger (sent only in Act mode, to a known contact).
Expand only; the downgrade removes the kind, and any action of it, first.

Revision ID: h1a3_helm_reconcile
Revises: h1a2_helm_bank
"""

from __future__ import annotations

from alembic import op

revision = "h1a3_helm_reconcile"
down_revision = "h1a2_helm_bank"
branch_labels = None
depends_on = None

BEFORE = ("hold_payment", "release_hold", "schedule_payment", "approve_payment", "mark_paid", "draft_dispute",
          "send_reminder", "request_document", "create_task", "decide_renewal", "notify", "webhook")
AFTER = BEFORE + ("draft_reconciliation",)


def _check(kinds: tuple[str, ...]) -> str:
    listed = ", ".join(f"'{kind}'" for kind in kinds)
    return f"ALTER TABLE helm_actions ADD CONSTRAINT ck_helm_actions_kind CHECK (kind IN ({listed}))"


def upgrade() -> None:
    op.execute("ALTER TABLE helm_actions DROP CONSTRAINT ck_helm_actions_kind")
    op.execute(_check(AFTER))


def downgrade() -> None:
    # Their certificates go with them (helm_certificates.action_id is ON DELETE CASCADE).
    op.execute("DELETE FROM helm_actions WHERE kind = 'draft_reconciliation'")
    op.execute("ALTER TABLE helm_actions DROP CONSTRAINT ck_helm_actions_kind")
    op.execute(_check(BEFORE))
