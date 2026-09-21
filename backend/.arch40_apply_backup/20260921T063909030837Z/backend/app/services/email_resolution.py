"""ARCH-40 — the one place that answers "what sender does this email use?".

Before ARCH-40 the answer lived in four places and agreed with itself by
accident:

  app/core/smtp.resolve_smtp_config          workspace SMTP, then organization
  organization_email_settings_service         the organization tier
  app/core/platform_email                     the platform relay
  tenant_branding.sender_domain               the tenant's own domain

Two defects followed from the split, and both are fixed here.

  D-1  Both production callers of `resolve_smtp_config` — the outbox
       dispatcher and `notification_service` — passed only `workspace_id`.
       `organization_id` defaulted to None, so the organization tier was
       unreachable from the notification path. `organization_email_settings`
       had a table, a migration, a service, a settings page and no effect on
       any email the product sent.

  D-2  `tenant_branding.sender_domain` and `sender_domain_status` were read by
       the branding console and by nothing in the mail path. A tenant could
       verify a sender domain and watch mail continue to leave under the relay
       username.

PRECEDENCE, STATED ONCE
=======================

Transport (which relay carries the message):

    workspace override  >  organization settings  >  platform relay

Identity (what the recipient sees in From and Reply-To):

    workspace override from_address
      > verified branding sender domain
      > organization settings sender
      > platform relay sender

A branding sender domain participates only at VERIFIED. PENDING and LAPSED do
not, which is ARCH-25 invariant 5: a lapsed domain degrades visibly rather
than silently continuing to claim the tenant's identity. `degraded_reason`
carries the sentence the console shows.

IDENTITY MAIL IS EXEMPT, DELIBERATELY
=====================================

`MessageKind.IDENTITY` — verification, password reset, the password-changed
notice — resolves to the platform relay and stops. It never touches tenant
state. `app/core/platform_email` states the reason and it has not changed: the
day a password reset depends on a tenant's SMTP row is the day a locked-out
user cannot recover their account. The enum makes that exemption a value a
caller passes rather than a branch a caller might forget.

WHY THE RESULT CARRIES ITS OWN TRAIL
====================================

`ResolvedEmailIdentity.trail` records every layer that was consulted, whether
it applied, and why not when it did not. The settings console renders it, so
"why did this email come from the wrong address" is answerable from the UI
instead of from a database session. It is also what gate E1 asserts against,
which means the explanation shown to an administrator and the explanation the
gate checks are the same object.
"""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.encryption import decrypt_password
from app.core.smtp import SMTPConfig
from app.models.email_settings import EmailEncryption
from app.models.workspace_email_override import WorkspaceEmailOverride

logger = logging.getLogger("app.services.email_resolution")


class MessageKind(str, enum.Enum):
    """Which resolution ladder a message climbs."""

    #: Verification, password reset, password-changed. Platform relay only.
    IDENTITY = "IDENTITY"
    #: Invitations, billing notices, anything the organization sends as itself.
    TRANSACTIONAL = "TRANSACTIONAL"
    #: Notifications and automation output, sent on a workspace's behalf.
    WORKSPACE = "WORKSPACE"


#: The layers, most specific first. Named so the trail and the gates use one
#: vocabulary rather than two spellings of the same idea.
LAYER_WORKSPACE_OVERRIDE: str = "WORKSPACE_OVERRIDE"
LAYER_BRANDING_SENDER: str = "BRANDING_SENDER"
LAYER_ORGANIZATION: str = "ORGANIZATION"
LAYER_PLATFORM: str = "PLATFORM"

LAYERS: tuple[str, ...] = (
    LAYER_WORKSPACE_OVERRIDE,
    LAYER_BRANDING_SENDER,
    LAYER_ORGANIZATION,
    LAYER_PLATFORM,
)


@dataclass(frozen=True)
class LayerDecision:
    """One rung of the ladder and what happened on it."""

    layer: str
    applied: bool
    #: Present when `applied` is False and the layer existed but was skipped.
    reason: Optional[str] = None
    #: A short human label for the console: the host, the domain, the address.
    detail: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "layer": self.layer,
            "applied": self.applied,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class ResolvedEmailIdentity:
    """Everything a sender needs, plus why it is what it is."""

    message_kind: MessageKind
    smtp: SMTPConfig
    from_address: str
    sender_name: str
    reply_to: Optional[str]
    template_namespace: str

    #: The layer that decided the transport, and the one that decided identity.
    transport_layer: str
    identity_layer: str

    trail: list[LayerDecision] = field(default_factory=list)

    #: Set when a branding sender domain exists but is not VERIFIED. ARCH-25
    #: invariant 5 — the degradation is visible, never silent.
    degraded_reason: Optional[str] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "message_kind": self.message_kind.value,
            "from_address": self.from_address,
            "sender_name": self.sender_name,
            "reply_to": self.reply_to,
            "template_namespace": self.template_namespace,
            "transport_layer": self.transport_layer,
            "identity_layer": self.identity_layer,
            "smtp_host": self.smtp.smtp_host,
            "degraded_reason": self.degraded_reason,
            "trail": [decision.as_dict() for decision in self.trail],
        }


def _override_row(
    db: Session, *, workspace_id: Optional[uuid.UUID]
) -> Optional[WorkspaceEmailOverride]:
    if workspace_id is None:
        return None
    return db.execute(
        select(WorkspaceEmailOverride).where(
            WorkspaceEmailOverride.workspace_id == workspace_id
        )
    ).scalar_one_or_none()


def _override_smtp(row: WorkspaceEmailOverride) -> SMTPConfig:
    return SMTPConfig(
        smtp_host=str(row.smtp_host),
        smtp_port=int(row.smtp_port or 0),
        smtp_username=str(row.smtp_username),
        smtp_password=decrypt_password(str(row.smtp_password_encrypted)),
        sender_name=str(row.sender_name),
        encryption=EmailEncryption(row.encryption),
        from_email=row.from_address,
    )


def _branding(db: Session, *, organization_id: Optional[uuid.UUID]) -> Any:
    if organization_id is None:
        return None
    try:
        from app.models.tenant_branding import TenantBranding

        return db.execute(
            select(TenantBranding).where(
                TenantBranding.organization_id == organization_id
            )
        ).scalar_one_or_none()
    except Exception:  # pragma: no cover - branding is optional at runtime
        logger.debug("branding lookup failed; continuing without it", exc_info=True)
        return None


def resolve_email_identity(
    db: Session,
    *,
    organization_id: Optional[uuid.UUID],
    workspace_id: Optional[uuid.UUID] = None,
    message_kind: MessageKind = MessageKind.WORKSPACE,
) -> ResolvedEmailIdentity:
    """Answer sender, reply-to and template for one (org, workspace, kind).

    Every email the product sends goes through this function. It raises only
    what `platform_smtp_config` raises — an unconfigured platform relay — and
    otherwise always returns a usable answer, because a resolution that can
    fail is a resolution a caller will wrap in a bare except.
    """
    from app.core.platform_email import platform_smtp_config

    trail: list[LayerDecision] = []
    platform = platform_smtp_config()

    if message_kind is MessageKind.IDENTITY:
        trail.append(
            LayerDecision(
                LAYER_WORKSPACE_OVERRIDE,
                False,
                reason="Identity mail never uses tenant configuration.",
            )
        )
        trail.append(
            LayerDecision(
                LAYER_BRANDING_SENDER,
                False,
                reason="Identity mail never uses tenant configuration.",
            )
        )
        trail.append(
            LayerDecision(
                LAYER_ORGANIZATION,
                False,
                reason="Identity mail never uses tenant configuration.",
            )
        )
        trail.append(
            LayerDecision(LAYER_PLATFORM, True, detail=platform.smtp_host)
        )
        return ResolvedEmailIdentity(
            message_kind=message_kind,
            smtp=platform,
            from_address=platform.sender_address,
            sender_name=platform.sender_name,
            reply_to=None,
            template_namespace="identity",
            transport_layer=LAYER_PLATFORM,
            identity_layer=LAYER_PLATFORM,
            trail=trail,
        )

    # ---- transport ----------------------------------------------------
    smtp = platform
    transport_layer = LAYER_PLATFORM
    from_address = platform.sender_address
    sender_name = platform.sender_name
    identity_layer = LAYER_PLATFORM
    reply_to: Optional[str] = None

    override = _override_row(db, workspace_id=workspace_id)
    organization_for_lookup = organization_id
    if override is not None and organization_for_lookup is None:
        # The composite FK guarantees this matches the workspace's owner.
        organization_for_lookup = override.organization_id

    organization_config: Optional[SMTPConfig] = None
    if organization_for_lookup is not None:
        from app.services.organization_email_settings_service import (
            resolve_organization_smtp_config,
        )

        organization_config = resolve_organization_smtp_config(
            db, organization_id=organization_for_lookup
        )

    if organization_config is not None:
        smtp = organization_config
        transport_layer = LAYER_ORGANIZATION
        from_address = organization_config.sender_address
        sender_name = organization_config.sender_name
        identity_layer = LAYER_ORGANIZATION
        trail.append(
            LayerDecision(
                LAYER_ORGANIZATION, True, detail=organization_config.smtp_host
            )
        )
    else:
        trail.append(
            LayerDecision(
                LAYER_ORGANIZATION,
                False,
                reason=(
                    "No organization transactional email is configured and "
                    "enabled."
                ),
            )
        )

    # ---- branding sender domain, VERIFIED only ------------------------
    degraded_reason: Optional[str] = None
    branding = _branding(db, organization_id=organization_for_lookup)
    if branding is None:
        trail.append(
            LayerDecision(
                LAYER_BRANDING_SENDER, False, reason="No branding row for this tenant."
            )
        )
    elif getattr(branding, "can_send_as_tenant", False):
        domain = str(branding.sender_domain)
        local_part = from_address.split("@", 1)[0] or "no-reply"
        from_address = f"{local_part}@{domain}"
        identity_layer = LAYER_BRANDING_SENDER
        trail.append(LayerDecision(LAYER_BRANDING_SENDER, True, detail=domain))
    else:
        degraded_reason = getattr(branding, "sender_degradation_reason", None)
        trail.append(
            LayerDecision(
                LAYER_BRANDING_SENDER,
                False,
                reason=(
                    degraded_reason
                    or "The sender domain is not verified, so mail is not sent as it."
                ),
                detail=getattr(branding, "sender_domain", None),
            )
        )

    # ---- workspace override, the most specific rung -------------------
    if message_kind is MessageKind.WORKSPACE and override is not None and override.can_send:
        smtp = _override_smtp(override)
        transport_layer = LAYER_WORKSPACE_OVERRIDE
        sender_name = str(override.sender_name)
        if override.from_address:
            from_address = override.from_address
            identity_layer = LAYER_WORKSPACE_OVERRIDE
        else:
            from_address = smtp.sender_address
            identity_layer = LAYER_WORKSPACE_OVERRIDE
        reply_to = override.reply_to_address
        trail.append(
            LayerDecision(
                LAYER_WORKSPACE_OVERRIDE, True, detail=str(override.smtp_host)
            )
        )
    elif override is None:
        trail.append(
            LayerDecision(
                LAYER_WORKSPACE_OVERRIDE,
                False,
                reason="This workspace has no email override.",
            )
        )
    elif message_kind is not MessageKind.WORKSPACE:
        trail.append(
            LayerDecision(
                LAYER_WORKSPACE_OVERRIDE,
                False,
                reason=(
                    "Transactional mail is sent by the organization, so a "
                    "workspace override does not apply."
                ),
            )
        )
    else:
        trail.append(
            LayerDecision(
                LAYER_WORKSPACE_OVERRIDE,
                False,
                reason=(
                    "The override exists but is disabled or incomplete."
                    if not override.is_enabled
                    else "The override is enabled but missing credentials."
                ),
            )
        )

    if transport_layer == LAYER_PLATFORM:
        trail.append(LayerDecision(LAYER_PLATFORM, True, detail=platform.smtp_host))
    else:
        trail.append(
            LayerDecision(
                LAYER_PLATFORM,
                False,
                reason=f"Superseded by {transport_layer}.",
                detail=platform.smtp_host,
            )
        )

    smtp = smtp.model_copy(update={"from_email": from_address, "sender_name": sender_name})

    return ResolvedEmailIdentity(
        message_kind=message_kind,
        smtp=smtp,
        from_address=from_address,
        sender_name=sender_name,
        reply_to=reply_to,
        template_namespace=(
            "workspace" if message_kind is MessageKind.WORKSPACE else "organization"
        ),
        transport_layer=transport_layer,
        identity_layer=identity_layer,
        trail=trail,
        degraded_reason=degraded_reason,
    )


__all__ = [
    "LAYERS",
    "LAYER_BRANDING_SENDER",
    "LAYER_ORGANIZATION",
    "LAYER_PLATFORM",
    "LAYER_WORKSPACE_OVERRIDE",
    "LayerDecision",
    "MessageKind",
    "ResolvedEmailIdentity",
    "resolve_email_identity",
]
