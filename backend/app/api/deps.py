"""
Dependencies Module for FlowPilot AI.

Hosts database session factories, authentication guards, and the tenant context
resolution chain:

    get_current_user            identity from the access token
            |
    get_current_active_user     account status check
            |
    get_organization_context    resolves and authorizes organization membership
            |
    get_workspace_context       resolves the workspace, requires organization
            |                   membership, then applies the explicit grant or
            |                   the derived organization elevation
            |
    RequireOrgRole / RequireWorkspaceRole    assert the role

Two functions were removed in ARCH-01. Both resolved "the user's workspace"
with no workspace filter:

    get_current_workspace()
    RequireRole.__call__()

Each executed scalar_one_or_none() against a query that could legitimately
match many rows, so a second active membership raised MultipleResultsFound and
the account returned HTTP 500 on every request thereafter. They also emitted a
404 reading "Workspace not configured. Please complete onboarding.", which sent
removed members to organization creation.

The tenant identifier now arrives as a path parameter and is authorized
server-side. Master Plan section 4.6 forbade this, intending to prevent tenant
leakage; the mechanism was inverted. Safety comes from validating the named
tenant against the actor's membership, not from the client being unable to name
one. Withholding the identifier only removed the server's ability to know which
tenant the caller meant. GitHub, Slack, Linear, and Stripe all accept a tenant
identifier and authorize it. There is now exactly one function resolving
workspace authorization, against four independent call sites before.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Generator, Sequence, Union

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import crud
from app.core import security
from app.core.config import settings
from app.core.exceptions import (
    OrganizationAccessDeniedError,
    OrganizationPermissionDeniedError,
    WorkspaceAccessDeniedError,
    WorkspacePermissionDeniedError,
)
from app.core.workspace_permissions import is_at_least
from app.crud.membership_filters import ACTIVE_ONLY
from app.db.session import SessionLocal
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.services import organization_service
from app.services import workspace_member_service
from app.services import workspace_service

logger = logging.getLogger("app.api.deps")

# Instantiate standard OAuth2 authorization extractor targeting the unified login route
oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


# ===========================================================================
# Session
# ===========================================================================

def get_db() -> Generator[Session, None, None]:
    """
    Supplies an active transactional database session for a single HTTP request context.

    Guarantees session release back to the connection pool upon request termination.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ===========================================================================
# Authentication
# ===========================================================================

async def get_current_user(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    """
    Validates, parses, and resolves incoming JWT access tokens.

    Inspects claims signatures, extracts UUID subjects, and returns the
    corresponding authenticated User database record.

    Raises HTTPException rather than a domain exception because a 401 must
    carry the WWW-Authenticate header, and because authentication failure is a
    protocol-level outcome rather than a business one.

    The `sid` claim is deliberately NOT looked up here. Verifying that the
    session still exists would put a query on every authenticated request,
    which is precisely the cost §B.6 avoided by comparing `iat` against
    sessions_revoked_at instead. sid is for attribution — knowing which device
    a request came from — not for authorization.

    Tokens issued before Step 7 carry no sid and are accepted. They expire
    within the access TTL on their own, and rejecting them would sign every
    user out a second time for no security gain.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    claims = security.decode_access_token_claims(token)
    if claims is None:
        raise credentials_exception

    user = crud.get_user_by_id(db, user_id=claims.subject)
    if user is None:
        raise credentials_exception

    if _token_predates_revocation(claims, user):
        # Distinguished in the log, not in the response. The client's only
        # useful reaction to either is to refresh or sign in again.
        logger.info(
            "AUTH_REJECTED | user=%s | jti=%s | reason=revoked_before_iat",
            user.id,
            claims.jti,
        )
        raise credentials_exception

    return user


def _token_predates_revocation(
    claims: security.AccessTokenClaims,
    user: User,
) -> bool:
    """
    Applies the global session cutoff to a stateless access token (§B.6).

    This is what makes password reset and sign-out-everywhere immediate. Access
    tokens are not recorded anywhere, so without this comparison they stay
    valid until their own expiry — up to the full access TTL after the user
    asked to be signed out.

    The check costs nothing: users.sessions_revoked_at is already on the row
    that was just loaded to resolve `sub`.

    THE COMPARISON IS AT WHOLE-SECOND GRANULARITY, DELIBERATELY
    -----------------------------------------------------------
    `iat` is an integer number of seconds — that is what JWT numeric dates are.
    sessions_revoked_at is a microsecond-precision timestamp. Comparing them
    directly loses to rounding in the wrong direction:

        revocation at 12:00:01.900  ->  sessions_revoked_at = ...01.900
        login       at 12:00:01.950  ->  iat = ...01  (truncated)
        01 < 01.900  ->  the brand-new token is rejected

    That is a real flow, not a hypothetical: "change your password, stay signed
    in" reissues a token immediately after revoking, and it would fail for any
    request that happened to land in the sub-second tail of a revocation.

    Truncating the cutoff to whole seconds too removes the asymmetry. The cost
    is that a token issued in the same second as a revocation survives it — for
    at most one second, during which the session rows are already revoked so no
    refresh is possible. A sub-second survival is immaterial; intermittently
    signing a user out of the session they just created is not.
    """
    if user.sessions_revoked_at is None:
        return False

    return int(claims.issued_at.timestamp()) < int(
        user.sessions_revoked_at.timestamp()
    )


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    """
    Secures API endpoints by enforcing that users must possess active accounts.
    """
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account",
        )
    return current_user


async def get_verified_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    """
    Requires a proved email address before any tenant-scoped access (§B.4).

    THE GATE IS ON TENANT ACCESS, NOT ON LOGIN
    ------------------------------------------
    An unverified address may belong to someone else — a typo that reaches a
    real stranger, or a deliberate registration on a colleague's address before
    they sign up. The moment such an account holds a workspace seat it holds
    another organization's data, and verifying afterwards does not undo what
    was read.

    So unverified users can sign in, see who they are at /auth/me and
    /me/context, list their sessions, verify, and resend. They cannot resolve
    an organization or a workspace, which is where this dependency sits.

    DELIBERATELY EXEMPT: /invitations/accept and /invitations/reject.
    Acceptance is itself a proof of address control (§B.4 Option 2) and is what
    grants verification on that path — gating it here would make the exemption
    unreachable and lock invited users out of the flow that verifies them.

    403 rather than 404, unlike the tenancy denials below. Those hide whether a
    tenant exists, because a 403 there is an enumeration oracle. This is not
    about a tenant at all: the caller is told precisely what is wrong because
    the caller is the only person who can fix it, and a 404 would send someone
    with a perfectly valid account hunting for a workspace that is right there.
    """
    if current_user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Verify your email address to continue. Check your inbox for "
                "the verification link, or request a new one."
            ),
        )
    return current_user


#: Reusable annotated dependencies. Keep route signatures readable and ensure
#: every handler resolves the session and actor the same way.
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]

#: An authenticated, active actor whose email address has been proved. Use in
#: place of CurrentUser on anything that touches tenant data directly.
VerifiedUser = Annotated[User, Depends(get_verified_user)]


# ===========================================================================
# Context objects
# ===========================================================================

@dataclass(frozen=True)
class OrganizationContext:
    """
    An authenticated actor's authorized standing in one organization.

    Supplied to commercial and administrative routes. Reaching a handler at all
    guarantees the organization exists and the actor holds an ACTIVE membership
    in it.
    """
    user: User
    organization: Organization
    membership: OrganizationMember

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def role(self) -> OrganizationRole:
        return self.membership.role


@dataclass(frozen=True)
class TenantContext:
    """
    An authenticated actor's fully resolved standing in one workspace.

    The single object every tenant-scoped service accepts. Carries both tiers
    because several decisions legitimately span them: granting workspace ADMIN,
    for instance, requires organization-level standing.

    workspace_membership may be None while effective_workspace_role is ADMIN.
    That is the derived-elevation case, not an inconsistency: organization
    OWNER and ADMIN hold ADMIN on every workspace without a stored grant, so
    that an organization role change takes effect on the next request instead
    of leaving stale rows behind.
    """
    user: User
    organization: Organization
    organization_membership: OrganizationMember
    workspace: Workspace
    workspace_membership: WorkspaceMember | None
    effective_workspace_role: WorkspaceRole

    @property
    def user_id(self) -> uuid.UUID:
        return self.user.id

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def workspace_id(self) -> uuid.UUID:
        return self.workspace.id

    @property
    def organization_role(self) -> OrganizationRole:
        return self.organization_membership.role

    @property
    def role(self) -> WorkspaceRole:
        """Alias for effective_workspace_role, for concise route code."""
        return self.effective_workspace_role


# ===========================================================================
# Context resolution
# ===========================================================================

async def get_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
) -> OrganizationContext:
    """
    Resolves and authorizes the actor's membership in the addressed organization.

    Mount on routes shaped /organizations/{organization_id}/... The path
    parameter name must match exactly.

    A non-member receives 404 rather than 403. A 403 would confirm the
    organization exists, which is an enumeration oracle; GitHub applies the
    same rule to private repositories.
    """
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    organization_service.assert_organization_operational(organization)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


async def get_billing_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
) -> OrganizationContext:
    """
    Resolves organization context WITHOUT requiring the tenant to be operational.

    A suspended organization is blocked everywhere else, but its owner must
    still reach billing to settle the balance that lifts the suspension.
    Locking them out of the recovery path would be a self-inflicted support
    incident. Mount only on billing and subscription routes; ARCH-05 consumes
    this.

    Membership is still required, so this weakens the tenant-status check
    alone, never the access check.
    """
    organization = organization_service.get_organization_or_raise(
        db, organization_id=organization_id
    )

    membership = crud.get_organization_member(
        db,
        organization_id=organization.id,
        user_id=current_user.id,
        statuses=ACTIVE_ONLY,
    )
    if membership is None:
        raise OrganizationAccessDeniedError("Organization not found.")

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


async def get_workspace_context(
    workspace_id: uuid.UUID = Path(..., description="Workspace identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
) -> TenantContext:
    """
    Resolves and authorizes the actor's standing in the addressed workspace.

    Mount on routes shaped /workspaces/{workspace_id}/... The path parameter
    name must match exactly.

    The workspace identifier is taken directly rather than nested under the
    organization, because a workspace identifier already determines its
    organization. Requiring both would create two sources of truth and an
    inconsistency check on every request. GitHub nests (/repos/{owner}/{repo})
    because its identifier is a name needing a namespace; a UUID is not.

    Resolution order, and why:
      1. Load the workspace with its organization eagerly (one round trip).
      2. Resolve access, which requires ACTIVE organization membership before
         any workspace grant is considered. Organization membership is a
         precondition for workspace access, not an alternative to it.
      3. No access -> 404, never 403.
      4. Assert both tenants are operational, so a suspended organization
         blocks its workspaces without needing a separate check per workspace.

    Costs three indexed lookups, with the third skipped for organization
    administrators. ARCH-11 introduces caching with invalidation on role change.
    """
    workspace = workspace_service.get_workspace_or_raise(
        db, workspace_id=workspace_id
    )

    access = workspace_member_service.resolve_workspace_access(
        db, workspace=workspace, user_id=current_user.id
    )

    if not access.has_access:
        raise WorkspaceAccessDeniedError("Workspace not found.")

    organization_service.assert_organization_operational(workspace.organization)
    workspace_service.assert_workspace_operational(workspace)

    # Narrowed by has_access. Asserted so the dataclass fields stay
    # non-optional for every downstream consumer.
    assert access.organization_membership is not None
    assert access.effective_role is not None

    return TenantContext(
        user=current_user,
        organization=workspace.organization,
        organization_membership=access.organization_membership,
        workspace=workspace,
        workspace_membership=access.workspace_membership,
        effective_workspace_role=access.effective_role,
    )


OrgContext = Annotated[OrganizationContext, Depends(get_organization_context)]
WorkspaceCtx = Annotated[TenantContext, Depends(get_workspace_context)]


# ===========================================================================
# Role guards
# ===========================================================================

class RequireOrgRole:
    """
    Parameterized organization-level RBAC dependency.

    Takes an EXPLICIT SET of permitted roles rather than a minimum, because
    organization roles are not a ladder. BILLING grants billing visibility
    while granting less content access than MEMBER, so it sits on no coherent
    rung and a minimum-based guard would include or exclude it wrongly. The
    argument type is the signal: a sequence means an explicit set.

    Usage:
        @router.get("/organizations/{organization_id}/members")
        async def list_members(
            context: OrganizationContext = Depends(
                deps.RequireOrgRole([OrganizationRole.OWNER,
                                     OrganizationRole.ADMIN])
            ),
        ): ...

    Composes get_organization_context, so membership, existence, and tenant
    status are already validated by the time __call__ runs. A rejection here is
    always 403: the actor is a member, so acknowledging the organization
    discloses nothing.
    """

    def __init__(self, allowed_roles: Sequence[OrganizationRole]) -> None:
        self.allowed_roles = frozenset(allowed_roles)

    def __call__(
        self,
        context: OrganizationContext = Depends(get_organization_context),
    ) -> OrganizationContext:
        if context.role not in self.allowed_roles:
            raise OrganizationPermissionDeniedError(
                "You do not have permission to perform this action in this "
                "organization."
            )
        return context


class RequireWorkspaceRole:
    """
    Parameterized workspace-level RBAC dependency.

    Takes a MINIMUM role rather than a set, because workspace roles do form a
    true ladder: ADMIN holds every capability of CONTRIBUTOR, which holds every
    capability of VIEWER. Enumerating a set at each route would invite the
    omissions that a ladder makes impossible.

    Usage:
        @router.post("/workspaces/{workspace_id}/work-items")
        async def create_item(
            context: TenantContext = Depends(
                deps.RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
            ),
        ): ...

    The role checked is the EFFECTIVE role, so an organization OWNER or ADMIN
    satisfies any workspace requirement through their derived grant without
    holding a stored row.

    A rejection here is always 403: reaching this guard means the actor already
    has some access to the workspace, so its existence is not a secret from
    them.
    """

    def __init__(self, minimum_role: WorkspaceRole) -> None:
        self.minimum_role = minimum_role

    def __call__(
        self,
        context: TenantContext = Depends(get_workspace_context),
    ) -> TenantContext:
        if not is_at_least(context.effective_workspace_role, self.minimum_role):
            raise WorkspacePermissionDeniedError(
                "You do not have permission to perform this action in this "
                "workspace."
            )
        return context


#: Convenience guards for the three common workspace requirements.
RequireWorkspaceViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireWorkspaceContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireWorkspaceAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)

#: Convenience guard for organization administration.
RequireOrgAdmin = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN]
)

#: Convenience guard for owner-only operations: billing changes, organization
#: deletion, ownership transfer, SSO, and security policy.
RequireOrgOwner = RequireOrgRole([OrganizationRole.OWNER])