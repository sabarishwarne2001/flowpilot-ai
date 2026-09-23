"""ARCH-40 — workspace email, resolved.

    GET  /workspaces/{wid}/email-settings            the override   [CONTRIBUTOR]
    PUT  /workspaces/{wid}/email-settings            write it       [ADMIN]
    GET  /workspaces/{wid}/email-settings/resolution what wins, why [CONTRIBUTOR]
    POST /workspaces/{wid}/email-settings/test       send a probe   [ADMIN]

ARCH40-S1:email-settings-override. These endpoints read and write
`workspace_email_overrides`, not `email_settings`.

WHY THE TABLE UNDERNEATH CHANGED
================================

There were three email surfaces and no single answer to "what sender does this
use?". `arch40_step2_settings_backfill` copies every `email_settings` row into
the override — archived first, through the `settings_migration_archive`
pattern, so it is reversible — and the override owns workspace email from
there on. The old table keeps its rows and loses its readers; gate A5 proves
the runtime has none.

Two tables kept in sync was the alternative, and it was rejected for the
reason the prompt gives: the first time one of them lost a write, the answer
to "why did this email come from the wrong address" would be "because two
tables disagreed", which is not an answer anybody can act on.

WHY /resolution EXISTS
======================

Because otherwise that question needs a database session. The endpoint returns
the resolved sender, reply-to and template namespace together with the trail —
every layer that was consulted, whether it applied, and why not when it did
not. The console renders the trail directly, so an administrator sees
"organization settings, superseded by this workspace's override" rather than
an address with no provenance.

The password is never returned, anywhere. Not masked, not null — absent from
the response model entirely, the same decision
`OrganizationEmailSettingsResponse` records.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.core.encryption import encrypt_password
from app.models.workspace_email_override import WorkspaceEmailOverride
from app.schemas.email_settings import (
    EmailResolutionResponse,
    EmailResolutionLayerResponse,
    TestEmailRequest,
    TestEmailResponse,
    WorkspaceEmailOverrideResponse,
    WorkspaceEmailOverrideUpdate,
)
from app.services.email_resolution import MessageKind, resolve_email_identity
from app.api import capability_gate as _cap_gate  # HM-S1:capability-gated
from app.core import entitlements as _ent

logger = logging.getLogger("app.api.v1.email_settings")

router = APIRouter(tags=["Email Settings"])


def _load(db: Session, *, workspace_id) -> WorkspaceEmailOverride | None:
    return db.execute(
        select(WorkspaceEmailOverride).where(
            WorkspaceEmailOverride.workspace_id == workspace_id
        )
    ).scalar_one_or_none()


@router.get(
    "",
    response_model=WorkspaceEmailOverrideResponse,
    summary="Get the workspace email override",
)
async def get_email_settings(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> WorkspaceEmailOverrideResponse:
    row = _load(db, workspace_id=context.workspace_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="This workspace has no email override.",
        )
    return WorkspaceEmailOverrideResponse.model_validate(row)


@router.put(
    "",
    response_model=WorkspaceEmailOverrideResponse,
    summary="Create or update the workspace email override",
)
async def upsert_email_settings(
    settings_in: WorkspaceEmailOverrideUpdate,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> WorkspaceEmailOverrideResponse:
    _cap_gate.require_capability(db, context=context, capability_key=_ent.CUSTOM_EMAIL_CAPABILITY, operation="email.workspace_override.update")
    row = _load(db, workspace_id=context.workspace_id)
    if row is None:
        row = WorkspaceEmailOverride(
            workspace_id=context.workspace_id,
            organization_id=context.organization_id,
        )
        db.add(row)

    row.is_enabled = settings_in.is_enabled
    row.smtp_host = settings_in.smtp_host
    row.smtp_port = settings_in.smtp_port
    row.smtp_username = settings_in.smtp_username
    row.encryption = settings_in.encryption.value
    row.sender_name = settings_in.sender_name
    row.from_address = settings_in.from_address
    row.reply_to_address = settings_in.reply_to_address
    row.updated_by_user_id = context.user_id

    # A write that omits the password keeps the stored one. Requiring it on
    # every save would mean an administrator changing only the reply-to had to
    # fetch the relay password out of a vault to do it.
    if settings_in.smtp_password:
        row.smtp_password_encrypted = encrypt_password(settings_in.smtp_password)

    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        # ck_workspace_email_overrides_enabled_is_complete is the likely one:
        # enabling an override with no credentials.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "An enabled override needs a host, port, username, password "
                "and sender name. Save it disabled until you have them all."
            ),
        ) from exc

    db.refresh(row)
    logger.info(
        "email_override.updated",
        extra={
            "workspace_id": str(context.workspace_id),
            "user_id": str(context.user_id),
            "enabled": row.is_enabled,
        },
    )
    return WorkspaceEmailOverrideResponse.model_validate(row)


@router.get(
    "/resolution",
    response_model=EmailResolutionResponse,
    summary="What sender this workspace's mail uses, and what it inherited from",
)
async def get_email_resolution(
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceContributor),
) -> EmailResolutionResponse:
    try:
        identity = resolve_email_identity(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            message_kind=MessageKind.WORKSPACE,
        )
    except Exception as exc:
        # An unconfigured platform relay is the only thing resolution raises,
        # and it is an operator problem, not a tenant one. Say so plainly
        # rather than returning a 500 with a stack trace behind it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "No platform email relay is configured, so no sender can be "
                "resolved. Set PLATFORM_SMTP_HOST, PLATFORM_SMTP_USERNAME and "
                "PLATFORM_SMTP_PASSWORD."
            ),
        ) from exc

    return EmailResolutionResponse(
        from_address=identity.from_address,
        sender_name=identity.sender_name,
        reply_to=identity.reply_to,
        template_namespace=identity.template_namespace,
        transport_layer=identity.transport_layer,
        identity_layer=identity.identity_layer,
        smtp_host=identity.smtp.smtp_host,
        degraded_reason=identity.degraded_reason,
        trail=[
            EmailResolutionLayerResponse(**decision.as_dict())
            for decision in identity.trail
        ],
    )


@router.post(
    "/test",
    response_model=TestEmailResponse,
    summary="Send a test message through the resolved sender",
)
async def test_email_settings(
    request: TestEmailRequest,
    db: Session = Depends(deps.get_db),
    context: deps.TenantContext = Depends(deps.RequireWorkspaceAdmin),
) -> TestEmailResponse:
    """Sends through whatever the resolver picks, not through the override.

    Testing the override directly would prove the credentials work and prove
    nothing about the mail the workspace actually sends — which is the thing
    the administrator is trying to find out.
    """
    from app.services.email_service import email_service

    try:
        identity = resolve_email_identity(
            db,
            organization_id=context.organization_id,
            workspace_id=context.workspace_id,
            message_kind=MessageKind.WORKSPACE,
        )
    except Exception as exc:
        return TestEmailResponse(
            success=False,
            message=f"No sender could be resolved: {exc}",
            transport_layer=None,
            from_address=None,
        )

    success, message = email_service.send_email(
        settings=identity.smtp,
        recipient=request.recipient,
        subject="FlowPilot AI SMTP Test",
        body=(
            "Your email configuration is working.\n\n"
            f"This message was sent through the {identity.transport_layer} "
            f"relay as {identity.from_address}."
        ),
    )

    return TestEmailResponse(
        success=success,
        message=message,
        transport_layer=identity.transport_layer,
        from_address=identity.from_address,
    )


__all__ = ["router"]
