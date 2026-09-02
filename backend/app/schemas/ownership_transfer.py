"""
Request and response schemas for the ARCH-05 ownership transfer lifecycle.

`current_password` appears in exactly one schema here
(`OwnershipTransferInitiateRequest`) and in no response schema anywhere —
it is a credential the caller sends once to prove §B.2 re-authentication and
is never persisted, echoed, or logged. `OwnershipTransferResponse` is built
from the `OwnershipTransfer` ORM row, which has no column that could carry
it back even by accident.

There is deliberately NO schema here carrying a token. §B.1: the target is
already an authenticated, verified member, so acceptance is authorized by
session and by `target_membership_id`, not by a secret in a URL. Compare
`organization_invitation.py`'s own note that `token_hash` appears in no
schema there — same principle, arrived at from the opposite direction:
invitations have a credential and hide it, transfers never have one at all.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.ownership_transfer import OwnershipTransferStatus


# ============================================================================
# Request Schemas
# ============================================================================

class OwnershipTransferInitiateRequest(BaseModel):
    """
    Input to propose an ownership transfer.

    Both fields are required. `current_password` is the §B.2
    re-authentication: an access token is a bearer credential that may have
    been taken, and a stolen session must not be enough on its own to hand a
    tenant — and its billing liability under Phase F — to an address the
    attacker controls. Mirrors `PasswordChangeRequest.current_password`'s
    existing shape in `app/schemas/auth.py`, including its bounds.

    Deliberately does NOT carry an `expires_at` or TTL field. The 7-day
    window (§B.8) is a policy set by `settings.OWNERSHIP_TRANSFER_TTL_DAYS`,
    not something a caller negotiates per request — a client-supplied expiry
    would let an initiator create a proposal that lapses in one second, or
    one that never meaningfully lapses at all.
    """
    target_membership_id: UUID = Field(
        ...,
        description="Membership that will become the new owner if they accept.",
    )
    current_password: str = Field(
        ...,
        min_length=1,
        max_length=128,
        description=(
            "Your current password, re-entered to confirm this transfer "
            "(ARCH-05 §B.2). Never stored or returned."
        ),
    )


# ============================================================================
# Response Schemas
# ============================================================================

class OwnershipTransferResponse(BaseModel):
    """
    Serialized transfer proposal.

    `responded_at` and `cancelled_at` are both present and both nullable,
    and which one is set is meaningful rather than redundant with `status`:
    `responded_at` records that the TARGET acted (accept or decline);
    `cancelled_at` records that the INITIATOR withdrew. A CANCELLED row has
    `responded_at` NULL forever, because the target never did anything —
    see the model's own docstring for why these are two columns and not one.

    Carries identifiers, not embedded user objects. A caller that needs the
    target's display name resolves the membership through the existing
    member-directory endpoint; embedding a `UserSummary` here would make
    every transfer read a multi-table join for data most callers already
    hold.
    """
    id: UUID
    organization_id: UUID
    initiated_by_id: UUID
    target_membership_id: UUID
    status: OwnershipTransferStatus
    expires_at: datetime
    responded_at: datetime | None = None
    cancelled_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PendingOwnershipTransferResponse(BaseModel):
    """
    The caller's own outstanding proposals — both those awaiting their
    response and those they are waiting on.

    Wrapped in an object with a `transfers` key rather than returned as a
    bare array, matching `MyPendingInvitationsResponse` and every other list
    response in this API. A bare top-level array cannot grow a sibling field
    (a count, a cursor) without breaking every existing client.
    """
    transfers: list[OwnershipTransferResponse] = Field(default_factory=list)
