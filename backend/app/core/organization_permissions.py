"""
Organization-level authorization logic for FlowPilot AI.

Governs the commercial and identity surface: billing, seats, member
administration, workspace provisioning, audit access, SSO, and security
policy. Workspace content access is governed separately by
app/core/workspace_permissions.py.

Two concepts are kept deliberately separate here, because conflating them is
what produced the permission defects in the pre-ARCH-01 design:

  1. PRECEDENCE  — administrative ordering, used only to decide whether an
                   actor may act upon another member's role. It carries no
                   capability implication.
  2. CAPABILITY  — explicit predicates, one per action. Never derived from
                   precedence.

BILLING is the reason this separation matters. It grants billing visibility
while granting less content access than MEMBER, so it occupies no coherent
position on a single ordinal ladder. It sits at the same precedence as MEMBER
(neither can administer anyone) and receives its billing capability
explicitly.

Scope boundary: these functions consider roles only. They never inspect
MembershipStatus or tenant status; a DEACTIVATED membership is filtered out by
the query layer and the request context before any role reaches this module.
Invariant enforcement ("an organization must retain an active owner") requires
counting other rows and therefore belongs to the service layer.
"""

from __future__ import annotations

from app.models.organization import OrganizationRole

# ===========================================================================
# Precedence
# ===========================================================================

#: Administrative precedence. Used ONLY by the can_modify_* and can_assign_*
#: functions to determine whether an actor outranks a target.
#:
#: BILLING and MEMBER are peers at weight 1. Neither can administer the other,
#: and neither can administer anyone else, so no ordering between them exists
#: or is needed. Do not read a capability implication into these numbers.
ORGANIZATION_ROLE_PRECEDENCE: dict[OrganizationRole, int] = {
    OrganizationRole.OWNER: 3,
    OrganizationRole.ADMIN: 2,
    OrganizationRole.BILLING: 1,
    OrganizationRole.MEMBER: 1,
}

#: Roles permitted to administer an organization's members and workspaces.
ADMINISTRATIVE_ROLES: frozenset[OrganizationRole] = frozenset(
    {OrganizationRole.OWNER, OrganizationRole.ADMIN}
)

#: Roles that receive an implicit ADMIN grant on every workspace in the
#: organization. Consumed by workspace_permissions.resolve_effective_workspace_role.
IMPLICIT_WORKSPACE_ADMIN_ROLES: frozenset[OrganizationRole] = ADMINISTRATIVE_ROLES

#: Roles a non-OWNER administrator is permitted to assign. Promotion to ADMIN
#: or OWNER is reserved to OWNER, so that an administrator cannot manufacture a
#: peer and escape the precedence check.
ADMIN_ASSIGNABLE_ROLES: frozenset[OrganizationRole] = frozenset(
    {OrganizationRole.MEMBER, OrganizationRole.BILLING}
)


def precedence(role: OrganizationRole) -> int:
    """Returns the administrative precedence weight of an organization role."""
    return ORGANIZATION_ROLE_PRECEDENCE[role]


def outranks(actor_role: OrganizationRole, target_role: OrganizationRole) -> bool:
    """
    Returns True if the actor's precedence strictly exceeds the target's.

    Strict comparison is deliberate: peers may not act on one another. Without
    it, one administrator could demote another and an organization could be
    captured by whichever admin acted first.
    """
    return precedence(actor_role) > precedence(target_role)


# ===========================================================================
# Billing capabilities
# ===========================================================================

def can_view_billing(role: OrganizationRole) -> bool:
    """
    Whether the role may view invoices, plan, and usage.

    BILLING exists precisely for this: in mid-market and enterprise deals the
    person holding the payment method is typically a finance controller who
    must never see customer documents.
    """
    return role in {
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.BILLING,
    }


def can_manage_billing(role: OrganizationRole) -> bool:
    """
    Whether the role may change the plan, payment method, or cancel.

    Restricted to OWNER. Changing the plan alters the contract, and contract
    authority is not delegable to an operational administrator.
    """
    return role is OrganizationRole.OWNER


def can_manage_seats(role: OrganizationRole) -> bool:
    """
    Whether the role may purchase or release seats.

    Available to ADMIN because seat changes are an operational consequence of
    hiring, not a contractual decision. BILLING is excluded: it may observe
    spend, not cause it.
    """
    return role in ADMINISTRATIVE_ROLES


# ===========================================================================
# Organization capabilities
# ===========================================================================

def can_manage_organization_settings(role: OrganizationRole) -> bool:
    """Whether the role may rename the organization or change its branding."""
    return role in ADMINISTRATIVE_ROLES


def can_delete_organization(role: OrganizationRole) -> bool:
    """Whether the role may archive or delete the entire tenant. OWNER only."""
    return role is OrganizationRole.OWNER


def can_transfer_ownership(role: OrganizationRole) -> bool:
    """Whether the role may transfer ownership to another member. OWNER only."""
    return role is OrganizationRole.OWNER


def can_configure_sso(role: OrganizationRole) -> bool:
    """
    Whether the role may configure SSO, domain capture, or SCIM.

    OWNER only. Identity configuration can redirect authentication for every
    member of the tenant, which makes it an ownership-level capability
    regardless of how operational it appears. Consumed from ARCH-09.
    """
    return role is OrganizationRole.OWNER


def can_manage_security_policy(role: OrganizationRole) -> bool:
    """
    Whether the role may set 2FA enforcement, session TTL, or IP allowlists.

    OWNER only, for the same reason as SSO. Consumed from ARCH-09.
    """
    return role is OrganizationRole.OWNER


def can_view_audit_log(role: OrganizationRole) -> bool:
    """Whether the role may read the organization audit trail. From ARCH-07."""
    return role in ADMINISTRATIVE_ROLES


def can_manage_api_keys(role: OrganizationRole) -> bool:
    """Whether the role may create or revoke API keys. From ARCH-08."""
    return role in ADMINISTRATIVE_ROLES


def can_manage_webhooks(role: OrganizationRole) -> bool:
    """Whether the role may configure webhook endpoints. From ARCH-08."""
    return role in ADMINISTRATIVE_ROLES


# ===========================================================================
# Workspace provisioning
# ===========================================================================

def can_create_workspace(role: OrganizationRole) -> bool:
    """
    Whether the role may create a new workspace inside this organization.

    Distinct from creating a new organization, which is an account-level
    capability available to any verified user and is not governed by any
    organization role. A Viewer in Acme may found their own organization; that
    says nothing about their standing in Acme.
    """
    return role in ADMINISTRATIVE_ROLES


def can_delete_workspace(role: OrganizationRole) -> bool:
    """
    Whether the role may archive or delete a workspace.

    Held at organization level rather than workspace level: a workspace does
    not own itself, so its destruction is a decision for the tenant that does.
    """
    return role in ADMINISTRATIVE_ROLES


# ===========================================================================
# Member administration
# ===========================================================================

def can_manage_members(role: OrganizationRole) -> bool:
    """Whether the role may invite, deactivate, or re-role organization members."""
    return role in ADMINISTRATIVE_ROLES


def can_invite_members(role: OrganizationRole) -> bool:
    """Whether the role may issue invitations to the organization."""
    return role in ADMINISTRATIVE_ROLES


def can_assign_organization_role(
    actor_role: OrganizationRole,
    target_role: OrganizationRole,
) -> bool:
    """
    Whether the actor may assign the given role, at invitation or promotion.

    This is the check whose absence allowed a Manager to invite at OWNER level
    in the pre-ARCH-01 design. It must be applied at BOTH invitation creation
    and role change: an invitation is a deferred role assignment, and enforcing
    only on promotion leaves the escalation path open.

    Rules:
      - OWNER may assign any role, including OWNER (ownership transfer).
      - ADMIN may assign only MEMBER and BILLING. Promotion to ADMIN or OWNER
        is reserved to OWNER, so an administrator cannot manufacture a peer and
        thereby escape the strict precedence check in can_modify_member.
      - No other role may assign anything.
    """
    if not can_manage_members(actor_role):
        return False
    if actor_role is OrganizationRole.OWNER:
        return True
    return target_role in ADMIN_ASSIGNABLE_ROLES


def can_modify_member(
    actor_role: OrganizationRole,
    target_role: OrganizationRole,
) -> bool:
    """
    Whether the actor may act on an existing member holding the target role.

    Covers deactivation, suspension, and role change. Requires administrative
    standing plus strictly greater precedence, so:

      - OWNER may act on ADMIN, BILLING, and MEMBER, but not another OWNER.
      - ADMIN may act on BILLING and MEMBER, but not an OWNER or another ADMIN.
      - BILLING and MEMBER may act on no one.

    An OWNER acting on another OWNER is disallowed here and handled explicitly
    by the ownership transfer flow, which can enforce the accompanying
    invariants. A co-owner is not administrable by their peer.

    Self-directed actions (leaving, self-demotion) are not covered by this
    function. They are governed by the service layer, which must also enforce
    the last-active-owner invariant.
    """
    if not can_manage_members(actor_role):
        return False
    return outranks(actor_role, target_role)


def can_modify_member_role(
    actor_role: OrganizationRole,
    target_current_role: OrganizationRole,
    target_new_role: OrganizationRole,
) -> bool:
    """
    Whether the actor may change a member from one role to another.

    Both halves are required: the actor must outrank the member as they stand
    today, and must be permitted to assign the role they are moving to.
    Checking only one half leaves an escalation path open in the other
    direction.
    """
    return can_modify_member(
        actor_role, target_current_role
    ) and can_assign_organization_role(actor_role, target_new_role)
