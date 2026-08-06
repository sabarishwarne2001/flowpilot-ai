"""
Request validation and serialization schemas for the Organization tenant root.

Every response carries both the identifier and the slug. The API contract is
expressed in identifiers; the frontend addresses tenants by slug at
/{organization}/{workspace}/... Returning both means the client never needs a
lookup to build a URL, nor to parse a URL to make a request.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.core.slugs import MAX_SLUG_LENGTH, MIN_SLUG_LENGTH
from app.models.organization import (
    MembershipStatus,
    OrganizationRole,
    OrganizationStatus,
)


# ============================================================================
# Shared projections
# ============================================================================

class UserSummary(BaseModel):
    """
    Minimal user projection embedded in membership responses.

    Embedded rather than referenced by identifier because every consumer of a
    member list needs the email immediately; returning bare identifiers would
    force a request waterfall.

    Mirrors the User model exactly: it has no display-name column, so none is
    declared here.
    """
    id: UUID
    email: EmailStr
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


# ============================================================================
# Request Schemas
# ============================================================================

class OrganizationCreate(BaseModel):
    """
    Provisions a new tenant: an organization plus its first workspace.

    This replaces onboarding. The legacy endpoint both created and updated
    workspaces, which is why an existing owner revisiting the onboarding screen
    silently overwrote their live settings.
    """
    organization_name: str = Field(
        ...,
        min_length=1,
        max_length=150,
        description="Display name of the organization.",
    )
    workspace_name: str | None = Field(
        default=None,
        max_length=100,
        description="Name of the first workspace. Defaults to 'General'.",
    )
    organization_slug: str | None = Field(
        default=None,
        min_length=MIN_SLUG_LENGTH,
        max_length=MAX_SLUG_LENGTH,
        description=(
            "Optional URL identifier. Derived from the organization name with "
            "collision resolution when omitted."
        ),
    )
    legal_name: str | None = Field(
        default=None,
        max_length=255,
        description="Registered legal entity name, used on invoices.",
    )
    timezone: str = Field(default="UTC", max_length=100)
    language: str = Field(default="en", max_length=20)
    currency: str = Field(default="USD", max_length=10)
    date_format: str = Field(default="YYYY-MM-DD", max_length=30)


class OrganizationUpdate(BaseModel):
    """
    Partial update. None means "leave unchanged", not "set to null".

    Changing the slug changes the tenant's public URL and the previous address
    stops resolving immediately. ARCH-04 adds slug history with redirects.
    """
    name: str | None = Field(default=None, min_length=1, max_length=150)
    legal_name: str | None = Field(default=None, max_length=255)
    slug: str | None = Field(
        default=None, min_length=MIN_SLUG_LENGTH, max_length=MAX_SLUG_LENGTH
    )


class OrganizationMemberRoleUpdate(BaseModel):
    """
    Changes a member's organization role.

    This capability did not exist before ARCH-01: the permission helpers were
    written but never wired to an endpoint, so roles were permanent from the
    moment of invitation.
    """
    role: OrganizationRole = Field(..., description="The new organization role.")


class OwnershipTransferRequest(BaseModel):
    """
    Transfers ownership to another active member.

    The outgoing owner is demoted to ADMIN rather than removed: losing the
    organization and losing ownership of it are different intentions.
    """
    target_membership_id: UUID = Field(
        ..., description="Membership that will become the new owner."
    )


# ============================================================================
# Response Schemas
# ============================================================================

class OrganizationResponse(BaseModel):
    """Serialized organization."""
    id: UUID
    slug: str
    name: str
    legal_name: str | None = None
    status: OrganizationStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrganizationMemberResponse(BaseModel):
    """
    A seat in an organization.

    The seat is the billable unit: a user in five workspaces of one
    organization holds one of these and consumes one seat.
    """
    id: UUID
    organization_id: UUID
    user: UserSummary
    role: OrganizationRole
    status: MembershipStatus
    deactivated_at: datetime | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class OrganizationMemberListResponse(BaseModel):
    """Member directory with a seat count for plan enforcement."""
    items: list[OrganizationMemberResponse]
    total: int
    seats_consumed: int = Field(
        ...,
        description=(
            "Seats currently occupied, including pending invitations. A "
            "pending invitation reserves a seat so a tenant cannot over-invite "
            "past its plan limit."
        ),
    )


class SlugAvailabilityResponse(BaseModel):
    """
    Advisory availability check for the organization creation form.

    Advisory only: two concurrent requests can both observe a free slug. The
    unique index is the authority and creation may still return 409.
    """
    slug: str
    available: bool
    reason: str | None = None