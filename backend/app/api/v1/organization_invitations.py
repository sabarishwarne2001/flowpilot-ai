"""
Organization invitation API router for FlowPilot AI.

Exposes the ARCH-04 invitation lifecycle: issuance, resend, revocation, preview,
acceptance, and rejection.

Thin by contract. Every handler validates its schema, delegates to
app.services.organization_invitation_service, and returns. Authorization is
expressed declaratively through app.api.deps.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Query, status

from app.api import deps
from app.core.exceptions import SeatLimitExceededError
from app.core.links import (
    build_organization_invitations_link,
    build_organization_members_link,
)
from app.crud import user as user_crud
from app.models.organization_invitation import InvitationStatus
from app.schemas.common import MessageResponse
from app.schemas.organization_invitation import (
    AcceptedGrantSummary,
    InvitationCreateRequest,
    InvitationPreviewResponse,
    InvitationResponse,
    MyPendingInvitation,
    MyPendingInvitationsResponse,
    OrganizationInvitationAcceptResponse,
    OrganizationInvitationListResponse,
    OrganizationInvitationTokenRequest,
    WorkspacePreviewEntry,
)
from app.services import invitation_mail, organization_invitation_service

logger = logging.getLogger("app.api.v1.organization_invitations")

router = APIRouter(tags=["Invitations"])


# ============================================================================
# Issuance & Management
# ============================================================================

@router.post(
    "/organizations/{organization_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue Organization Invitation",
)
def create_invitation(
    payload: InvitationCreateRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    issued = organization_invitation_service.create_invitation(
        db,
        organization=context.organization,
        inviter=context.user,
        actor_role=context.role,
        email=payload.email,
        organization_role=payload.organization_role,
        grants=[(g.workspace_id, g.role) for g in payload.grants],
    )

    background_tasks.add_task(
        invitation_mail.send_invitation,
        organization_id=context.organization_id,
        invited_email=issued.invitation.email,
        organization_name=issued.organization_name,
        inviter_email=issued.inviter_email,
        inviter_display=issued.inviter_display,
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )

    return InvitationResponse.model_validate(issued.invitation)


@router.post(
    "/organizations/{organization_id}/invitations/{invitation_id}/resend",
    response_model=InvitationResponse,
    summary="Resend Organization Invitation",
)
def resend_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    issued = organization_invitation_service.resend_invitation(
        db,
        organization=context.organization,
        invitation_id=invitation_id,
        actor_role=context.role,
    )

    background_tasks.add_task(
        invitation_mail.send_invitation,
        organization_id=context.organization_id,
        invited_email=issued.invitation.email,
        organization_name=issued.organization_name,
        inviter_email=issued.inviter_email,
        inviter_display=issued.inviter_display,
        organization_role_display=issued.invitation.organization_role.value,
        grants=issued.grant_lines,
        accept_link=issued.accept_link,
        expires_at=issued.invitation.expires_at,
        invitation_id=issued.invitation.id,
    )

    return InvitationResponse.model_validate(issued.invitation)


@router.post(
    "/organizations/{organization_id}/invitations/{invitation_id}/revoke",
    response_model=MessageResponse,
    summary="Revoke Organization Invitation",
)
def revoke_invitation(
    invitation_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgAdmin),
) -> Any:
    result = organization_invitation_service.revoke_invitation(
        db,
        organization=context.organization,
        invitation_id=invitation_id,
        actor=context.user,
        actor_role=context.role,
    )

    background_tasks.add_task(
        invitation_mail.send_invitation_revoked,
        organization_id=context.organization_id,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        inviter_email=result.inviter_email,
        inviter_display=result.inviter_display,
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation revoked.")


@router.get(
    "/organizations/{organization_id}/invitations",
    response_model=OrganizationInvitationListResponse,
    summary="List Organization Invitations",
)
def list_invitations(
    db: deps.ReadDbSession,
    context=Depends(deps.RequireOrgAdmin),
    statuses: Optional[list[InvitationStatus]] = Query(
        default=None,
        alias="status",
        description=(
            "Only invitations in these states (repeatable). The members and "
            "workspace screens ask for PENDING; without it the full history is listed."
        ),
    ),
) -> Any:
    # F-216. Without a filter every invitation came back, and the workspace page
    # rendered accepted and revoked ones as pending, with Resend and Revoke.
    invitations = organization_invitation_service.list_invitations(
        db, organization_id=context.organization_id, statuses=statuses or None
    )
    return OrganizationInvitationListResponse(
        items=[InvitationResponse.model_validate(i) for m in invitations for i in [m]],
        total=len(invitations),
    )


# ============================================================================
# Public / Recipient Lifecycle
# ============================================================================

@router.post(
    "/invitations/preview",
    response_model=InvitationPreviewResponse,
    summary="Preview Invitation",
)
def preview_invitation(
    payload: OrganizationInvitationTokenRequest,
    db: deps.DbSession,
) -> Any:
    """
    Resolves an invitation token to its display summary, unauthenticated.

    POST, not GET, and the token rides in the body.

    This was `GET /invitations/preview?token=...` and that is the one shape
    an invitation token must never take. ARCH-04 B.10 moved the token out of
    the accept LINK's query string into a fragment precisely so it could not
    reach an access log, a proxy log, or a Referer header -- and then this
    handler put it straight back into a query string on the very next
    request the recipient makes. The fragment never leaves the browser; this
    call did, with `?token=` attached, into every log between the client and
    the app.

    Every other token-bearing public route in PUBLIC_ROUTES already carries
    its credential in a POST body: auth/reset-password, auth/verify-email,
    auth/email-change/confirm. This route was the sole exception, and
    accept/reject -- which take the SAME token, from the SAME page, moments
    later -- are both POST with an OrganizationInvitationTokenRequest body.

    So the frontend was never wrong. `previewInvitation` posting `{ token }`
    matches its two siblings and matches the rest of the registry; the 405
    was the backend disagreeing with its own convention. Making the frontend
    issue a GET would have cleared the error and reintroduced the leak.

    A GET is not semantically owed here either. This reads nothing the
    caller owns, is not cacheable (the token is a bearer secret and a shared
    cache keyed on the URL is a cross-user leak), and is rate-limited under
    POLICY_PUBLIC_READ regardless of verb.
    """
    return organization_invitation_service.preview_invitation(
        db, token=payload.token
    )


@router.post(
    "/invitations/accept",
    response_model=OrganizationInvitationAcceptResponse,
    summary="Accept Invitation",
)
def accept_invitation(
    payload: OrganizationInvitationTokenRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    try:
        accepted = organization_invitation_service.accept_invitation(
            db, token=payload.token, actor=current_user
        )
        queue_accepted_mail(background_tasks, accepted)
        return OrganizationInvitationAcceptResponse(
            invitation_id=accepted.invitation_id,
            organization_id=accepted.organization_id,
            organization_slug=accepted.organization_slug,
            organization_role=accepted.organization_role,
            provisioned_grants=[
                AcceptedGrantSummary(
                    workspace_name=g.workspace_name,
                    role=g.role_display,
                )
                for g in accepted.provisioned_grants
            ],
            skipped_grant_count=accepted.skipped_grant_count,
            workspace_slug=accepted.first_workspace_slug,
        )
    except SeatLimitExceededError:
        queue_seat_blocked_mail(db, token=payload.token)
        raise


def queue_accepted_mail(background_tasks: BackgroundTasks, accepted: Any) -> None:
    """Tell the inviter their invitation was accepted (after the response)."""
    background_tasks.add_task(
        invitation_mail.send_invitation_accepted,
        organization_id=accepted.organization_id,
        inviter_email=accepted.inviter_email,
        invited_email=accepted.invited_email,
        invited_display=accepted.invited_display,
        organization_name=accepted.organization_name,
        organization_role_display=accepted.organization_role.value,
        provisioned_grants=accepted.provisioned_grants,
        skipped_grant_count=accepted.skipped_grant_count,
        members_url=build_organization_members_link(accepted.organization_slug),
        invitation_id=accepted.invitation_id,
    )


#: F-234. How long one "no seats" notice covers repeated attempts on one invitation.
SEAT_BLOCKED_NOTICE_WINDOW_SECONDS = 24 * 60 * 60


def _first_seat_blocked_notice(invitation_id: Any) -> bool:
    """True once per invitation per window. Without Redis, every attempt notifies (as before)."""
    from app.core.redis_client import get_redis_client

    client = get_redis_client()
    if client is None:
        return True
    try:
        return bool(client.set(
            f"invitation:seat_blocked_notice:{invitation_id}", "1",
            nx=True, ex=SEAT_BLOCKED_NOTICE_WINDOW_SECONDS,
        ))
    except Exception:  # noqa: BLE001 - a notice is not worth failing the request over
        logger.warning("SEAT_BLOCKED_NOTICE_DEDUPE_UNAVAILABLE | invitation=%s", invitation_id)
        return True


def queue_seat_blocked_mail(db: Any, *, token: str) -> None:
    """Tell the inviter an acceptance was blocked for want of a seat.

    F-234. Once a day per invitation: the sign-up is public, so its holder could
    otherwise send the inviter a notice on every refused attempt.
    """
    blocked = organization_invitation_service.describe_seat_blocked(db, token=token)
    if not _first_seat_blocked_notice(blocked["invitation_id"]):
        return
    invitation_mail.send_invitation_seat_blocked(
        inviter_email=blocked["inviter_email"],
        invited_email=blocked["invited_email"],
        organization_name=blocked["organization_name"],
        seat_limit=blocked["seat_limit"],
        members_url=build_organization_members_link(blocked["organization_slug"]),
        invitation_id=blocked["invitation_id"],
    )


@router.post(
    "/invitations/reject",
    response_model=MessageResponse,
    summary="Reject Invitation",
)
def reject_invitation(
    payload: OrganizationInvitationTokenRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    result = organization_invitation_service.reject_invitation(
        db, token=payload.token, actor=current_user
    )
    invitations_url = build_organization_invitations_link(result.organization_slug)
    background_tasks.add_task(
        invitation_mail.send_invitation_rejected,
        inviter_email=result.inviter_email,
        invited_email=result.invited_email,
        organization_name=result.organization_name,
        invitations_url=invitations_url,
        invitation_id=result.invitation_id,
    )
    return MessageResponse(message="Invitation declined.")


@router.get(
    "/me/invitations",
    response_model=MyPendingInvitationsResponse,
    summary="List My Pending Invitations",
)
def list_my_invitations(
    db: deps.ReadDbSession,
    current_user: deps.CurrentUser,
) -> Any:
    invitations = organization_invitation_service.list_invitations_for_user(
        db, user_id=current_user.id
    )
    items = []
    for inv in invitations:
        inviter = user_crud.get_user_by_id(db, user_id=inv.inviter_id)
        items.append(
            MyPendingInvitation(
                organization_name=inv.organization.name,
                organization_role=inv.organization_role,
                inviter_email=inviter.email if inviter else "",
                workspaces=[
                    WorkspacePreviewEntry(name=g.workspace.workspace_name, role=g.role)
                    for g in inv.grants
                    if g.workspace is not None
                ],
                expires_at=inv.expires_at,
            )
        )
    return MyPendingInvitationsResponse(items=items)