"""PHASE 4 — let a webhook endpoint receive a test event ("Send test event").

Revision ID: p4a2_webhook_test_event
Revises: p4a1_outbox_vocabulary_restore
Create Date: 2026-10-05

WHAT CHANGES
============

`POST /organizations/{id}/webhooks/endpoints/{endpoint_id}/test` queues one
signed `webhook.test` delivery for one endpoint, so an administrator can prove
their receiver (URL, TLS, signature check) before real events depend on it.

`ck_webhook_deliveries_event_type_vocabulary` pins the event names a delivery
row may carry, and refused the ping. It is rebuilt here with `webhook.test`
added. `ck_webhook_endpoints_event_types_vocabulary` is NOT touched: nobody
can subscribe to the test event, so the outbox relay never fans it out; the
only way to receive one is to ask for it.

The list is written out in full, not imported, for the reason ARCH-37's
migration gives: a migration that imports the live set silently changes what it
did the next time the set changes.
"""

from __future__ import annotations

from alembic import op

revision = "p4a2_webhook_test_event"
down_revision = "p4a1_outbox_vocabulary_restore"
branch_labels = None
depends_on = None

#: ARCH-37's PUBLIC_AFTER, unchanged.
PUBLIC: tuple[str, ...] = (
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
    "workflow.triggered",
)

TEST_EVENT = "webhook.test"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _rebuild(values: tuple[str, ...]) -> None:
    op.execute(
        "ALTER TABLE webhook_deliveries DROP CONSTRAINT IF EXISTS "
        "ck_webhook_deliveries_event_type_vocabulary"
    )
    op.execute(
        "ALTER TABLE webhook_deliveries ADD CONSTRAINT "
        "ck_webhook_deliveries_event_type_vocabulary "
        f"CHECK (event_type IN ({_sql_list(values)}))"
    )


def upgrade() -> None:
    _rebuild(PUBLIC + (TEST_EVENT,))


def downgrade() -> None:
    # Attempt rows cascade with their delivery; test pings carry no business data.
    op.execute(f"DELETE FROM webhook_deliveries WHERE event_type = '{TEST_EVENT}'")
    _rebuild(PUBLIC)
