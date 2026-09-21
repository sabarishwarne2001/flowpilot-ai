"""
SMTP Connection Configuration Utilities for FlowPilot AI.

ARCH40-S1:smtp-delegates. `resolve_smtp_config` is now a thin read-through onto
`app.services.email_resolution.resolve_email_identity`, which is the single
answer to "what sender, reply-to and template does this email use?".

Two defects are fixed by that delegation, both of them silent before ARCH-40:

  * Every production caller passed `workspace_id` and let `organization_id`
    default to None, so the organization tier of the old ladder was never
    reached. `organization_email_settings` had a table, a service and a
    settings page, and no effect on any email the product sent.
  * `tenant_branding.sender_domain` was consulted by the branding console and
    by nothing in the mail path, so a verified sender domain changed nothing.

This function is kept rather than deleted because `outbox_dispatcher` and
`notification_service` both want an `SMTPConfig` and nothing more. Callers who
need the reply-to, the template namespace or the explanation trail call
`resolve_email_identity` directly.
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.models.email_settings import EmailEncryption


class SMTPConfig(BaseModel):
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    sender_name: str
    encryption: EmailEncryption

    from_email: str | None = None

    @property
    def sender_address(self) -> str:
        return self.from_email or self.smtp_username


def resolve_smtp_config(
    db: Session,
    workspace_id: uuid.UUID | None,
    organization_id: uuid.UUID | None = None,
) -> SMTPConfig:
    """The relay this workspace's mail leaves through.

    `organization_id` stays optional for source compatibility, but the
    resolver no longer needs it when a workspace override exists: the
    override row carries `organization_id`, guaranteed coherent by the
    composite foreign key onto `workspaces (id, organization_id)`, so the
    organization tier is reachable even from a caller that only knows the
    workspace. That is what makes the old defect unrepeatable rather than
    merely fixed at two call sites.
    """
    from app.services.email_resolution import MessageKind, resolve_email_identity

    identity = resolve_email_identity(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        message_kind=MessageKind.WORKSPACE,
    )
    return identity.smtp
