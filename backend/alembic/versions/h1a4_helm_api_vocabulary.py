"""Helm through API keys and webhooks (Enterprise, `capability.helm_api`): the vocabularies.

Three CHECK constraints pin a vocabulary into the database, and each refuses Helm's names:

    ck_api_keys_scopes_allowed                    an API key's scopes: + helm:read, helm:write
    ck_webhook_endpoints_event_types_vocabulary   what an endpoint may subscribe to: + the three events
    ck_webhook_deliveries_event_type_vocabulary   what a delivery row may carry: + the three events

    helm.plan.ready        a new plan version
    helm.action.executed   an action ran
    helm.mandate.breached  an instrument newly breached

`ck_outbox_events_visibility_vocabulary` is NOT touched: it pins the INTERNAL names only, and a
PUBLIC event may carry any name that is not internal.

Each list is written out in full, not imported, for the reason ARCH-37's migration gives: a
migration that imports the live set silently changes what it did the next time the set changes.
Expand only. The downgrade first takes Helm's names out of the rows that carry them, then narrows
the CHECKs: Helm's deliveries go (their attempts cascade); an endpoint that listened to nothing
but Helm goes too, since an endpoint must listen to at least one event
(`ck_webhook_endpoints_event_types_non_empty`), and every other endpoint keeps its other events;
a key left with no scope is deactivated as ARCH-21's downgrade does (`SCOPE_DOWNGRADE`, holding
`organizations:read` so `ck_api_keys_scopes_not_empty` holds), and every other key keeps the rest.

Revision ID: h1a4_helm_api_vocabulary
Revises: h1a3_helm_reconcile
"""

from __future__ import annotations

from alembic import op

revision = "h1a4_helm_api_vocabulary"
down_revision = "h1a3_helm_reconcile"
branch_labels = None
depends_on = None

#: arch21_step1_public_api_tiers.SCOPES_17, unchanged.
SCOPES_BEFORE: tuple[str, ...] = (
    "organizations:read",
    "workspaces:read",
    "workspaces:write",
    "members:read",
    "work_items:read",
    "work_items:write",
    "audit_logs:read",
    "files:read",
    "files:write",
    "webhooks:read",
    "webhooks:write",
    "webhooks:admin",
    "billing:read",
    "public_documents:read",
    "public_query:write",
    "public_workflows:read",
    "public_workflows:write",
)
HELM_SCOPES: tuple[str, ...] = ("helm:read", "helm:write")

#: p4a2_webhook_test_event.PUBLIC (ARCH-37's PUBLIC_AFTER), unchanged.
EVENTS_BEFORE: tuple[str, ...] = (
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
HELM_EVENTS: tuple[str, ...] = ("helm.plan.ready", "helm.action.executed", "helm.mandate.breached")
#: Deliveries also carry the "Send test event" ping (p4a2); nobody subscribes to it.
TEST_EVENT = "webhook.test"


def _sql_list(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _scopes(values: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE api_keys DROP CONSTRAINT IF EXISTS ck_api_keys_scopes_allowed")
    op.execute("ALTER TABLE api_keys ADD CONSTRAINT ck_api_keys_scopes_allowed "
               f"CHECK (scopes <@ ARRAY[{_sql_list(values)}]::text[])")


def _events(values: tuple[str, ...]) -> None:
    op.execute("ALTER TABLE webhook_deliveries DROP CONSTRAINT IF EXISTS ck_webhook_deliveries_event_type_vocabulary")
    op.execute("ALTER TABLE webhook_deliveries ADD CONSTRAINT ck_webhook_deliveries_event_type_vocabulary "
               f"CHECK (event_type IN ({_sql_list(values + (TEST_EVENT,))}))")
    op.execute("ALTER TABLE webhook_endpoints DROP CONSTRAINT IF EXISTS ck_webhook_endpoints_event_types_vocabulary")
    op.execute("ALTER TABLE webhook_endpoints ADD CONSTRAINT ck_webhook_endpoints_event_types_vocabulary "
               f"CHECK (event_types <@ ARRAY[{_sql_list(values)}]::varchar[])")


def upgrade() -> None:
    _scopes(SCOPES_BEFORE + HELM_SCOPES)
    _events(EVENTS_BEFORE + HELM_EVENTS)


def downgrade() -> None:
    events, scopes = _sql_list(HELM_EVENTS), _sql_list(HELM_SCOPES)
    op.execute(f"DELETE FROM webhook_deliveries WHERE event_type IN ({events})")
    op.execute(f"DELETE FROM webhook_endpoints WHERE event_types <@ ARRAY[{events}]::varchar[]")
    op.execute("UPDATE webhook_endpoints SET event_types = ARRAY(SELECT e FROM unnest(event_types) AS e "
               f"WHERE e NOT IN ({events}))::varchar[] WHERE event_types && ARRAY[{events}]::varchar[]")
    op.execute("UPDATE api_keys SET deactivated_at = COALESCE(deactivated_at, now()), "
               "deactivated_reason = COALESCE(deactivated_reason, 'SCOPE_DOWNGRADE'), "
               f"scopes = ARRAY['organizations:read']::text[] WHERE scopes <@ ARRAY[{scopes}]::text[]")
    op.execute("UPDATE api_keys SET scopes = ARRAY(SELECT s FROM unnest(scopes) AS s "
               f"WHERE s NOT IN ({scopes}))::text[] WHERE scopes && ARRAY[{scopes}]::text[]")
    _events(EVENTS_BEFORE)
    _scopes(SCOPES_BEFORE)
