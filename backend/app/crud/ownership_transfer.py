"""
Database CRUD operations for OwnershipTransfer (ARCH-05 Step 6).

Handles direct relational queries and the atomic status transition, strictly
decoupled from re-authentication, permission, and invariant-enforcement
logic — that belongs to `app/services/ownership_transfer_service.py`, which
is the only caller of every function here.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.models.organization import OrganizationMember
from app.models.ownership_transfer import OwnershipTransfer, OwnershipTransferStatus


def create_transfer(
    db: Session,
    *,
    organization_id: uuid.UUID,
    initiated_by_id: uuid.UUID,
    target_membership_id: uuid.UUID,
    expires_at: datetime,
) -> OwnershipTransfer:
    """
    Instantiates and stages a new PENDING transfer proposal.

    Flushes rather than commits — the caller (initiate_transfer) still has
    re-authentication and the last-owner-adjacent invariant checks to run in
    the same transaction, and `uq_pending_ownership_transfer_per_org` is what
    actually enforces "at most one PENDING transfer per organization" under
    concurrency; a flush is what makes that constraint checkable without
    yet committing.

    Status is left to the model's own `default=OwnershipTransferStatus.PENDING`
    rather than set explicitly here — every transfer created through this
    function starts PENDING, and there is no other value a caller should be
    passing in.
    """
    transfer = OwnershipTransfer(
        organization_id=organization_id,
        initiated_by_id=initiated_by_id,
        target_membership_id=target_membership_id,
        expires_at=expires_at,
    )
    db.add(transfer)
    db.flush()
    return transfer


def get_pending_transfer_for_org(
    db: Session, *, organization_id: uuid.UUID
) -> OwnershipTransfer | None:
    """
    Fetches the organization's current PENDING transfer, if any.

    At most one row can ever match — `uq_pending_ownership_transfer_per_org`
    guarantees it — so this is safe to treat as "the" pending transfer
    rather than a list. Does NOT filter on `expires_at`: a PENDING row past
    its expiry is still PENDING in the database until a service function
    lazily resolves it (§B.8), and callers that care about that distinction
    make it themselves, exactly as `claim_invitation`'s callers do for
    invitations.
    """
    stmt = select(OwnershipTransfer).where(
        OwnershipTransfer.organization_id == organization_id,
        OwnershipTransfer.status == OwnershipTransferStatus.PENDING,
    )
    return db.execute(stmt).scalar_one_or_none()


def get_transfer_by_id(
    db: Session, *, organization_id: uuid.UUID, transfer_id: uuid.UUID
) -> OwnershipTransfer | None:
    """
    Fetches a transfer by its own identifier, scoped to an organization.

    The organization scope is not redundant, on the same reasoning
    `get_membership_or_raise` gives for the identical shape of check: without
    it, an actor authorized for one tenant could address a transfer
    belonging to another by supplying its identifier.
    """
    stmt = select(OwnershipTransfer).where(
        OwnershipTransfer.id == transfer_id,
        OwnershipTransfer.organization_id == organization_id,
    )
    return db.execute(stmt).scalar_one_or_none()


def list_pending_transfers_for_user(
    db: Session, *, user_id: uuid.UUID
) -> list[OwnershipTransfer]:
    """
    Every PENDING transfer this user is a party to — as target OR as
    initiator.

    Both sides, deliberately, not just the target. The target needs to see
    what is waiting on them; the initiator needs to see what they are
    waiting on, because §B.8 has no sweeper and
    `uq_pending_ownership_transfer_per_org` means their outstanding proposal
    is the reason a second one is refused. A list that showed only incoming
    proposals would leave an owner unable to find the very row blocking
    them.

    Does NOT filter expired rows out in SQL. A PENDING row past its
    `expires_at` is still PENDING in the database until something lazily
    resolves it (§B.8) — the caller applies the clock comparison, which
    keeps this function honest about what the table actually contains and
    keeps the expiry rule in one place rather than duplicated into every
    query's WHERE clause.

    Joins to the target membership rather than issuing a second query per
    row: this feeds a list view, and the N+1 would be per-proposal.
    """
    stmt = (
        select(OwnershipTransfer)
        .join(
            OrganizationMember,
            OwnershipTransfer.target_membership_id == OrganizationMember.id,
        )
        .where(
            OwnershipTransfer.status == OwnershipTransferStatus.PENDING,
            or_(
                OrganizationMember.user_id == user_id,
                OwnershipTransfer.initiated_by_id == user_id,
            ),
        )
        .order_by(OwnershipTransfer.created_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def update_transfer_status(
    db: Session,
    *,
    transfer_id: uuid.UUID,
    new_status: OwnershipTransferStatus,
    now: datetime,
) -> uuid.UUID | None:
    """
    Atomically transitions a PENDING transfer to a terminal state.

    A conditional UPDATE, never a read-then-write — the same shape as
    `organization_invitation.claim_invitation`, and for the same reason: the
    WHERE clause is the serialization point. Two concurrent requests
    resolving the same transfer race on this single UPDATE statement, not on
    an application-level check-then-act, so a PostgreSQL row lock (implicit
    in any UPDATE) decides the winner regardless of transaction isolation
    level. Returns the transfer's id on success and None when nothing
    matched — already resolved by this call or a concurrent one. The caller
    (ownership_transfer_service) is responsible for the corresponding
    organization-row lock via lock_organization_for_owner_change where the
    operation also touches the owner set (accept only); decline and cancel
    do not mutate organization_members at all, so no such lock applies to
    them, and this conditional UPDATE alone is sufficient serialization.

    Args:
        new_status: ACCEPTED, DECLINED, CANCELLED, or EXPIRED. PENDING is
            never a legal target — there is no transition back to it.
        now: The timestamp written to whichever terminal-state column
            applies. Passed explicitly rather than computed here, so the
            same instant is used for this write and for anything else the
            caller's transaction records about the same event.

    Raises:
        ValueError: new_status is PENDING, or not a recognized terminal
            value. Defensive: every call site in this codebase passes a
            literal terminal member, so this should be unreachable, but a
            silent no-op UPDATE (WHERE status = 'PENDING' AND status =
            'PENDING' effectively) is a worse failure mode than an
            exception naming exactly what was wrong.
    """
    if new_status is OwnershipTransferStatus.PENDING:
        raise ValueError("PENDING is not a valid transition target.")

    values: dict = {"status": new_status}
    if new_status in (
        OwnershipTransferStatus.ACCEPTED,
        OwnershipTransferStatus.DECLINED,
    ):
        # One shared column for both target-initiated outcomes — see the
        # model's own docstring for why responded_at does not split into
        # accepted_at/declined_at.
        values["responded_at"] = now
    elif new_status is OwnershipTransferStatus.CANCELLED:
        values["cancelled_at"] = now
    elif new_status is OwnershipTransferStatus.EXPIRED:
        pass  # No dedicated timestamp column; expiry is not a party's action.
    else:
        raise ValueError(f"Unrecognized transfer status: {new_status!r}")

    return db.execute(
        update(OwnershipTransfer)
        .where(
            OwnershipTransfer.id == transfer_id,
            OwnershipTransfer.status == OwnershipTransferStatus.PENDING,
        )
        .values(**values)
        .returning(OwnershipTransfer.id)
    ).scalar_one_or_none()
