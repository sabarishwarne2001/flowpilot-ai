"""
ARCH-05 ownership transfer endpoints.

Five routes: propose, accept, decline, cancel, and the caller's own pending
list. Routes carry their full path; register with no prefix, matching
`organization_invitations.py`.

WHY ONLY INITIATE USES RequireOrgOwner
    Accept and decline are performed by the TARGET, who is very often an
    ordinary MEMBER — that is the entire point of §B.1's two-phase design.
    Guarding those routes with `RequireOrgOwner` would make the flow
    unusable by exactly the people it exists for, so they take `OrgContext`
    (any active member of this tenant) and let the SERVICE perform the real
    authorization: `accept_transfer` and `decline_transfer` compare
    `actor.id` to the transfer's own `target_membership.user_id`, and
    `cancel_transfer` compares it to `initiated_by_id`.

    Cancel is the subtle one. It is an owner-ish action, but it is
    ALSO deliberately not `RequireOrgOwner`-guarded: §B.8 lets a proposal
    outlive its initiator's ownership, and the person who proposed it should
    still be able to withdraw it even after ownership moved elsewhere.
    `TransferInitiatorMismatchError` is the real check, and it is narrower
    than an owner check, not looser.

MAIL DISPATCH: every mutating route builds its result, dispatches the
matching `app.services.ownership_mail` function via BackgroundTasks, and
returns. FastAPI attaches the populated BackgroundTasks instance to the
Response it builds from a normal return, so this ordering is safe.

    ARCH-04's §D7.1 exception — a branch that must notify on FAILURE, and
    therefore cannot use `add_task` followed by `raise`, because the
    exception handler builds an unrelated Response that silently drops the
    task — has NO equivalent here. Every notice this phase sends
    accompanies a SUCCESSFUL transition, so every dispatch below sits on a
    normal return path. `TransferExpiredError` is the one outcome that both
    fails the request and changes state, and §B.7 specifies no mail for a
    lapsed proposal — deliberately, since a message announcing that nothing
    happened is worse than silence.

CARRIERS CROSS THE COMMIT BOUNDARY, NOT ORM OBJECTS. Each service call
returns a frozen dataclass of primitives, and it is those primitives that
are handed to `add_task`. The background callback runs AFTER the request's
session has closed; a detached ORM object passed there would raise on first
attribute access. The response model is built by re-reading the row inside
the request, which is a different concern and is why both exist.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.api import deps
from app.core.exceptions import TransferNotFoundError
from app.core.links import build_ownership_transfer_link
from app.crud import ownership_transfer as transfer_crud
from app.schemas.ownership_transfer import (
    OwnershipTransferInitiateRequest,
    OwnershipTransferResponse,
    PendingOwnershipTransferResponse,
)
from app.services import ownership_mail
from app.services import ownership_transfer_service

logger = logging.getLogger("app.api.v1.ownership_transfers")

router = APIRouter(tags=["Ownership Transfer"])


def _reread(db, *, organization_id: uuid.UUID, transfer_id: uuid.UUID):
    """
    Re-reads the transfer row for the response body after the service has
    committed.

    The service layer returns a carrier of primitives (built for the mail
    dispatch, which outlives the session) rather than the ORM row, so the
    row is fetched again here to serialize `status`, `responded_at`,
    `cancelled_at`, and `created_at` — fields the carrier has no reason to
    hold. One indexed lookup on a rare, human-initiated action.

    Raises TransferNotFoundError rather than returning None: reaching this
    point means the service just succeeded, so a missing row is a genuine
    invariant violation and should surface as one rather than as a
    `ResponseValidationError` from a null response body.
    """
    row = transfer_crud.get_transfer_by_id(
        db, organization_id=organization_id, transfer_id=transfer_id
    )
    if row is None:
        raise TransferNotFoundError("No such ownership transfer.")
    return row


# ============================================================================
# Organization-scoped
# ============================================================================

@router.post(
    "/organizations/{organization_id}/ownership-transfers",
    response_model=OwnershipTransferResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Propose Ownership Transfer",
)
async def initiate_ownership_transfer(
    payload: OwnershipTransferInitiateRequest,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context=Depends(deps.RequireOrgOwner),
) -> Any:
    """
    Proposes transferring ownership of this organization to another active,
    email-verified member (§B.1). Nothing about who owns the organization
    changes until the target accepts.

    Requires the caller's current password (§B.2), which the service
    verifies before taking any lock or writing anything.
    """
    initiated = ownership_transfer_service.initiate_transfer(
        db,
        organization=context.organization,
        actor=context.user,
        initiator_membership=context.membership,
        target_membership_id=payload.target_membership_id,
        current_password=payload.current_password,
    )

    background_tasks.add_task(
        ownership_mail.send_transfer_requested,
        target_email=initiated.target_email,
        organization_name=initiated.organization_name,
        initiator_email=initiated.initiator_email,
        initiator_display=initiated.initiator_display,
        review_link=initiated.review_link,
        expires_at=initiated.expires_at,
        transfer_id=initiated.transfer_id,
    )

    return _reread(
        db,
        organization_id=context.organization_id,
        transfer_id=initiated.transfer_id,
    )


@router.post(
    "/organizations/{organization_id}/ownership-transfers/{transfer_id}/accept",
    response_model=OwnershipTransferResponse,
    summary="Accept Ownership Transfer",
)
async def accept_ownership_transfer(
    transfer_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context: deps.OrgContext,
) -> Any:
    """
    Accepts a proposal addressed to the caller. Promotes them to OWNER and
    demotes the initiator to ADMIN, in one transaction.

    `OrgContext`, not `RequireOrgOwner` — the target is usually a MEMBER.
    The service compares the caller's id to the transfer's own target
    membership, which is the actual authorization.

    Notifies BOTH parties (A.2.2). The outgoing owner's copy is the one that
    matters: it is the only signal an unintended transfer would produce, on
    the same reasoning ARCH-03 gives for sending password-changed on every
    change.
    """
    accepted = ownership_transfer_service.accept_transfer(
        db,
        organization=context.organization,
        transfer_id=transfer_id,
        actor=context.user,
    )

    background_tasks.add_task(
        ownership_mail.send_ownership_transferred,
        organization_name=accepted.organization_name,
        previous_owner_email=accepted.previous_owner_email,
        previous_owner_display=accepted.previous_owner_display,
        new_owner_email=accepted.new_owner_email,
        new_owner_display=accepted.new_owner_display,
        transferred_at=accepted.transferred_at,
        transfer_id=accepted.transfer_id,
    )

    return _reread(
        db,
        organization_id=context.organization_id,
        transfer_id=accepted.transfer_id,
    )


@router.post(
    "/organizations/{organization_id}/ownership-transfers/{transfer_id}/decline",
    response_model=OwnershipTransferResponse,
    summary="Decline Ownership Transfer",
)
async def decline_ownership_transfer(
    transfer_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context: deps.OrgContext,
) -> Any:
    """
    Declines a proposal addressed to the caller. No roles change.

    Notifies the initiator (§B.7) — otherwise a declined proposal looks
    identical to an ignored one, and with §B.8's lazy expiry there is no
    sweeper to eventually tell them either.
    """
    declined = ownership_transfer_service.decline_transfer(
        db,
        organization=context.organization,
        transfer_id=transfer_id,
        actor=context.user,
    )

    background_tasks.add_task(
        ownership_mail.send_transfer_declined,
        initiator_email=declined.initiator_email,
        organization_name=declined.organization_name,
        target_email=declined.target_email,
        target_display=declined.target_display,
        declined_at=declined.declined_at,
        transfer_id=declined.transfer_id,
    )

    return _reread(
        db,
        organization_id=context.organization_id,
        transfer_id=declined.transfer_id,
    )


@router.post(
    "/organizations/{organization_id}/ownership-transfers/{transfer_id}/cancel",
    response_model=OwnershipTransferResponse,
    summary="Cancel Ownership Transfer",
)
async def cancel_ownership_transfer(
    transfer_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    db: deps.DbSession,
    context: deps.OrgContext,
) -> Any:
    """
    Withdraws a proposal the caller made. Initiator-only — see this module's
    docstring for why this is `OrgContext` rather than `RequireOrgOwner`.

    Notifies the target (§B.7): they may be midway through deciding, and a
    review page that silently stops working is worse than being told.
    """
    cancelled = ownership_transfer_service.cancel_transfer(
        db,
        organization=context.organization,
        transfer_id=transfer_id,
        actor=context.user,
    )

    background_tasks.add_task(
        ownership_mail.send_transfer_cancelled,
        target_email=cancelled.target_email,
        organization_name=cancelled.organization_name,
        initiator_email=cancelled.initiator_email,
        initiator_display=cancelled.initiator_display,
        cancelled_at=cancelled.cancelled_at,
        transfer_id=cancelled.transfer_id,
    )

    return _reread(
        db,
        organization_id=context.organization_id,
        transfer_id=cancelled.transfer_id,
    )


# ============================================================================
# Account-scoped
# ============================================================================

@router.get(
    "/me/ownership-transfers",
    response_model=PendingOwnershipTransferResponse,
    summary="List My Pending Ownership Transfers",
)
async def list_my_ownership_transfers(
    db: deps.DbSession,
    current_user: deps.CurrentUser,
) -> Any:
    """
    Every pending proposal the caller is a party to — awaiting their
    response, or awaiting someone else's.

    Both directions on purpose. §B.8 has no sweeper, and
    `uq_pending_ownership_transfer_per_org` means an owner's own outstanding
    proposal is the reason a second one gets refused; a list showing only
    incoming proposals would leave them unable to find the row blocking
    them.

    Rows whose `expires_at` has passed are filtered out HERE rather than in
    SQL. They are still `PENDING` in the database until something lazily
    resolves them (§B.8), and this is a read — it must not write. Excluding
    them from the view without claiming EXPIRED keeps the read side honest
    while leaving the state transition to whichever mutating call touches
    the row next.

    Not tenant-scoped: `/me/*` routes answer for the account across every
    organization it belongs to, matching `/me/invitations`.
    """
    now = datetime.now(UTC)
    transfers = transfer_crud.list_pending_transfers_for_user(
        db, user_id=current_user.id
    )
    return PendingOwnershipTransferResponse(
        transfers=[
            OwnershipTransferResponse.model_validate(t)
            for t in transfers
            if t.expires_at > now
        ]
    )