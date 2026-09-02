"""
Persistence operations for ARCH-04 organization invitations.

Layering, matching every other CRUD module in this project: queries and
flushes only. No authorization, no business rules, no commits. Transaction
boundaries belong to the service layer.

Two functions here are deliberately not plain reads-then-writes:

  claim_invitation      — a conditional UPDATE. A SELECT followed by an UPDATE
                          has a window in which two concurrent requests both
                          observe a PENDING invitation and both proceed; a
                          double-click on an accept link is enough to hit it.
                          The WHERE clause closes it, exactly as
                          auth_token_service.consume_token does (ARCH-03).

  expire_stale_invitations — one set-based UPDATE with RETURNING, not a loop.
                          §B.7: a sweeper that iterates will one day iterate
                          four hundred rows at 03:17.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Sequence

from sqlalchemy import Select, delete, func, select, update
from sqlalchemy.orm import Session, selectinload

from app.models.organization import OrganizationRole
from app.models.organization_invitation import (
    InvitationStatus,
    InvitationWorkspaceGrant,
    OrganizationInvitation,
)
from app.models.workspace import WorkspaceRole


# ===========================================================================
# Creation
# ===========================================================================

def create_invitation(
    db: Session,
    *,
    organization_id: uuid.UUID,
    inviter_id: uuid.UUID,
    email: str,
    organization_role: OrganizationRole,
    token_hash: str,
    expires_at: datetime,
    invited_user_id: uuid.UUID | None = None,
) -> OrganizationInvitation:
    """
    Inserts an invitation and flushes it.

    `email` must already be normalized to lowercase by the caller. The partial
    unique index applies lower(email), so a mixed-case value here would still
    be constrained correctly but would display inconsistently against every
    row the service writes.
    """
    invitation = OrganizationInvitation(
        organization_id=organization_id,
        inviter_id=inviter_id,
        invited_user_id=invited_user_id,
        email=email,
        organization_role=organization_role,
        status=InvitationStatus.PENDING,
        token_hash=token_hash,
        expires_at=expires_at,
        last_sent_at=datetime.now(tz=expires_at.tzinfo),
        send_count=1,
    )
    db.add(invitation)
    db.flush()
    return invitation


def add_workspace_grants(
    db: Session,
    *,
    invitation_id: uuid.UUID,
    grants: Sequence[tuple[uuid.UUID, WorkspaceRole]],
) -> list[InvitationWorkspaceGrant]:
    """
    Attaches workspace grants to an invitation.

    An empty sequence is valid and returns an empty list — the §B.1 BILLING
    case, where an invitation carries an organization seat and no workspace
    access at all.
    """
    rows = [
        InvitationWorkspaceGrant(
            invitation_id=invitation_id,
            workspace_id=workspace_id,
            role=role,
        )
        for workspace_id, role in grants
    ]
    if rows:
        db.add_all(rows)
        db.flush()
    return rows


# ===========================================================================
# Retrieval
# ===========================================================================

def _with_grants() -> Select:
    """Base select that eager-loads grants, avoiding N+1 on any list view."""
    return select(OrganizationInvitation).options(
        selectinload(OrganizationInvitation.grants)
    )


def get_invitation_by_id(
    db: Session,
    *,
    invitation_id: uuid.UUID,
) -> OrganizationInvitation | None:
    """Fetches by primary key, regardless of status or organization."""
    return db.execute(
        _with_grants().where(OrganizationInvitation.id == invitation_id)
    ).scalar_one_or_none()


def get_invitation_by_token_hash(
    db: Session,
    *,
    token_hash: str,
) -> OrganizationInvitation | None:
    """
    Fetches by token hash, regardless of status.

    Safe as a scalar: token_hash carries a unique index. Returns terminal and
    expired invitations too, so the service can distinguish "already accepted"
    from "no such invitation" rather than collapsing both to one message.
    """
    return db.execute(
        _with_grants().where(OrganizationInvitation.token_hash == token_hash)
    ).scalar_one_or_none()


def get_pending_invitation_for_email(
    db: Session,
    *,
    organization_id: uuid.UUID,
    email: str,
) -> OrganizationInvitation | None:
    """
    Fetches the live invitation for an address in an organization, if any.

    Safe as a scalar: uq_pending_organization_invitation guarantees at most
    one PENDING row per (organization, lower(email)) pair — §B.9.
    """
    return db.execute(
        _with_grants().where(
            OrganizationInvitation.organization_id == organization_id,
            func.lower(OrganizationInvitation.email) == email.lower(),
            OrganizationInvitation.status == InvitationStatus.PENDING,
        )
    ).scalar_one_or_none()


def list_invitations_for_organization(
    db: Session,
    *,
    organization_id: uuid.UUID,
    statuses: Sequence[InvitationStatus] | None = None,
) -> list[OrganizationInvitation]:
    """
    Lists an organization's invitations, newest first.

    Served by ix_organization_invitations_organization_status when a status
    filter is supplied.
    """
    stmt = _with_grants().where(
        OrganizationInvitation.organization_id == organization_id
    )
    if statuses is not None:
        stmt = stmt.where(OrganizationInvitation.status.in_(statuses))

    stmt = stmt.order_by(
        OrganizationInvitation.created_at.desc(),
        OrganizationInvitation.id.desc(),
    )
    return list(db.execute(stmt).scalars().all())


def list_pending_invitations_for_email(
    db: Session,
    *,
    email: str,
) -> list[OrganizationInvitation]:
    """
    Every live invitation addressed to one mailbox, across all organizations.

    Backs /me/invitations. Served by ix_organization_invitations_email.
    """
    stmt = (
        _with_grants()
        .where(
            func.lower(OrganizationInvitation.email) == email.lower(),
            OrganizationInvitation.status == InvitationStatus.PENDING,
        )
        .order_by(OrganizationInvitation.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def count_pending_invitations(
    db: Session,
    *,
    organization_id: uuid.UUID,
) -> int:
    """
    Counts outstanding invitations for an organization.

    Half of the seat calculation. See ARCH-04 Step 6 §0: an ARCH-04 invitation
    creates no OrganizationMember row — it cannot, since organization_members
    .user_id is NOT NULL and the invitee may have no account — so
    count_consumed_seats alone under-reports by exactly this number, and a
    seat ceiling enforced on it alone can be walked past by issuing
    invitations instead of adding members.
    """
    return db.execute(
        select(func.count())
        .select_from(OrganizationInvitation)
        .where(
            OrganizationInvitation.organization_id == organization_id,
            OrganizationInvitation.status == InvitationStatus.PENDING,
        )
    ).scalar_one()


# ===========================================================================
# State transitions
# ===========================================================================

def claim_invitation(
    db: Session,
    *,
    token_hash: str,
    new_status: InvitationStatus,
    now: datetime,
    actor_id: uuid.UUID | None = None,
) -> uuid.UUID | None:
    """
    Atomically transitions a PENDING, unexpired invitation to a terminal state.

    A conditional UPDATE, never a read-then-write. Returns the invitation's id
    on success and None when nothing matched — already spent, revoked, or
    expired. The caller classifies the failure with a second query, on the
    failure path only.

    Participates in the caller's transaction. If the caller then fails — a
    grant cannot be provisioned, the commit is rejected — the claim rolls back
    with it and the link still works.

    Args:
        new_status: ACCEPTED or REJECTED. Revocation goes through
            revoke_invitation, which is actor-initiated and records who.
        actor_id: Written to invited_user_id on acceptance, binding the
            invitation to the account it produced (§B.5). NEVER used to
            authorize — that is the session-email comparison in the service.
    """
    values: dict = {"status": new_status}
    if new_status is InvitationStatus.ACCEPTED:
        values["accepted_at"] = now
        if actor_id is not None:
            values["invited_user_id"] = actor_id
    elif new_status is InvitationStatus.REJECTED:
        values["rejected_at"] = now

    return db.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.token_hash == token_hash,
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at > now,
        )
        .values(**values)
        .returning(OrganizationInvitation.id)
    ).scalar_one_or_none()


def revoke_invitation(
    db: Session,
    *,
    invitation_id: uuid.UUID,
    revoked_by_id: uuid.UUID,
    now: datetime,
) -> uuid.UUID | None:
    """
    Withdraws a PENDING invitation. Conditional, same reasoning as claim.

    Unlike claim_invitation this does not require the invitation to be
    unexpired: revoking something that lapsed an hour ago is harmless and
    refusing it would be a confusing error on an administrative screen.
    """
    return db.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.id == invitation_id,
            OrganizationInvitation.status == InvitationStatus.PENDING,
        )
        .values(
            status=InvitationStatus.REVOKED,
            revoked_at=now,
            revoked_by_id=revoked_by_id,
        )
        .returning(OrganizationInvitation.id)
    ).scalar_one_or_none()


def rotate_token(
    db: Session,
    *,
    invitation: OrganizationInvitation,
    token_hash: str,
    expires_at: datetime,
    now: datetime,
) -> OrganizationInvitation:
    """
    Replaces an invitation's token and extends its expiry, for resend.

    Increments send_count rather than assigning, so a concurrent resend cannot
    silently lose a count.
    """
    invitation.token_hash = token_hash
    invitation.expires_at = expires_at
    invitation.last_sent_at = now
    invitation.send_count = OrganizationInvitation.send_count + 1
    db.add(invitation)
    db.flush()
    db.refresh(invitation)
    return invitation


# ===========================================================================
# Sweep (Step 8)
# ===========================================================================

def expire_stale_invitations(db: Session, *, now: datetime) -> list[dict]:
    """
    Transitions every lapsed PENDING invitation to EXPIRED.

    One set-based UPDATE with RETURNING, not a loop — §B.7. Served by
    ix_organization_invitations_status_expires_at.

    Returns one dict per affected row carrying what the digest needs:
    inviter_id, email, organization_id, expires_at. The service groups by
    inviter so the sweeper sends one message per person, never one per row.
    """
    rows = db.execute(
        update(OrganizationInvitation)
        .where(
            OrganizationInvitation.status == InvitationStatus.PENDING,
            OrganizationInvitation.expires_at <= now,
        )
        .values(status=InvitationStatus.EXPIRED)
        .returning(
            OrganizationInvitation.id,
            OrganizationInvitation.inviter_id,
            OrganizationInvitation.organization_id,
            OrganizationInvitation.email,
            OrganizationInvitation.expires_at,
        )
    ).mappings().all()
    return [dict(row) for row in rows]


def delete_invitations_before(db: Session, *, cutoff: datetime) -> int:
    """
    Purges terminal invitations older than the cutoff.

    PENDING rows are never purged regardless of age — a PENDING row past its
    expiry is a sweeper bug, and deleting it would erase the evidence.
    Grants cascade.
    """
    result = db.execute(
        delete(OrganizationInvitation).where(
            OrganizationInvitation.status != InvitationStatus.PENDING,
            OrganizationInvitation.created_at < cutoff,
        )
    )
    return result.rowcount or 0
