"""
Dependencies Module for FlowPilot AI.

Hosts database session factories, authentication guards, and the tenant context
resolution chain.
"""

import logging
import time
import uuid
from dataclasses import dataclass
from typing import Annotated, Generator, Sequence, Union

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app import crud
from app.core import security
from app.core.audit_routes import resolve_route_audit
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
from app.models.audit_log import AuditOutcome, AuditResourceType
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.services import api_key_service
from app.services import organization_service
from app.services import workspace_member_service
from app.services import workspace_service
from app.services.denial_aggregation_service import record_threshold_denial

logger = logging.getLogger("app.api.deps")

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)

_DENIAL_SUPPRESSION_CACHE: dict[tuple[uuid.UUID, uuid.UUID, str, str], float] = {}
_DENIAL_SUPPRESSION_WINDOW = 60.0


def _should_record_denial(key: tuple[uuid.UUID, uuid.UUID, str, str]) -> bool:
    now = time.time()
    last = _DENIAL_SUPPRESSION_CACHE.get(key)
    if last is not None and (now - last) < _DENIAL_SUPPRESSION_WINDOW:
        return False
    _DENIAL_SUPPRESSION_CACHE[key] = now
    return True


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # 1. API Key Token Authentication (ARCH-08 Step 9, Step 11)
    if token and token.startswith(("fp_live_", "fp_test_")):
        res = api_key_service.authenticate_api_key_token(db, token=token)
        if res is None:
            raise credentials_exception
        key, membership = res
        user = membership.user
        # ARCH-08 Step 11: Wire request.state for rate limiter identity resolution
        if request is not None:
            request.state.user_id = user.id
            request.state.api_key_id = key.id
        return user

    # 2. Bearer JWT Session Authentication
    claims = security.decode_access_token_claims(token)
    if claims is None:
        raise credentials_exception

    user = crud.get_user_by_id(db, user_id=claims.subject)
    if user is None:
        raise credentials_exception

    if _token_predates_revocation(claims, user):
        logger.info(
            "AUTH_REJECTED | user=%s | jti=%s | reason=revoked_before_iat",
            user.id,
            claims.jti,
        )
        raise credentials_exception

    # ARCH-08 Step 11: Wire request.state.user_id for per-user rate limiters
    if request is not None:
        request.state.user_id = user.id

    return user


def _token_predates_revocation(
    claims: security.AccessTokenClaims,
    user: User,
) -> bool:
    if user.sessions_revoked_at is None:
        return False

    return int(claims.issued_at.timestamp()) < int(
        user.sessions_revoked_at.timestamp()
    )


async def get_current_active_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Inactive user account",
        )
    return current_user


async def get_verified_user(
    current_user: User = Depends(get_current_active_user),
) -> User:
    if current_user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Verify your email address to continue. Check your inbox for "
                "the verification link, or request a new one."
            ),
        )
    return current_user


DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[User, Depends(get_current_active_user)]
VerifiedUser = Annotated[User, Depends(get_verified_user)]


@dataclass(frozen=True)
class OrganizationContext:
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
    user: User
    organization: Organization
    organization_membership: OrganizationMember
    workspace: Workspace
    workspace_membership: Union[WorkspaceMember, None]
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
        return self.effective_workspace_role


async def get_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
) -> OrganizationContext:
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


class RequireOrgRole:
    def __init__(self, allowed_roles: Sequence[OrganizationRole]) -> None:
        self.allowed_roles = frozenset(allowed_roles)

    def __call__(
        self,
        request: Request,
        db: Session = Depends(get_db),
        context: OrganizationContext = Depends(get_organization_context),
    ) -> OrganizationContext:
        if context.role not in self.allowed_roles:
            res_type, action = resolve_route_audit(
                request, default_resource=AuditResourceType.ORGANIZATION
            )
            record_threshold_denial(
                db=db,
                organization_id=context.organization_id,
                actor_id=context.user_id,
                resource_type=res_type,
                action=action,
                request=request,
            )
            raise OrganizationPermissionDeniedError(
                "You do not have permission to perform this action in this "
                "organization."
            )
        return context


class RequireWorkspaceRole:
    def __init__(self, minimum_role: WorkspaceRole) -> None:
        self.minimum_role = minimum_role

    def __call__(
        self,
        request: Request,
        db: Session = Depends(get_db),
        context: TenantContext = Depends(get_workspace_context),
    ) -> TenantContext:
        if not is_at_least(context.effective_workspace_role, self.minimum_role):
            res_type, action = resolve_route_audit(
                request, default_resource=AuditResourceType.WORKSPACE
            )
            record_threshold_denial(
                db=db,
                organization_id=context.organization_id,
                workspace_id=context.workspace_id,
                actor_id=context.user_id,
                resource_type=res_type,
                action=action,
                request=request,
            )
            raise WorkspacePermissionDeniedError(
                "You do not have permission to perform this action in this "
                "workspace."
            )
        return context


RequireWorkspaceViewer = RequireWorkspaceRole(WorkspaceRole.VIEWER)
RequireWorkspaceContributor = RequireWorkspaceRole(WorkspaceRole.CONTRIBUTOR)
RequireWorkspaceAdmin = RequireWorkspaceRole(WorkspaceRole.ADMIN)
RequireWorkspaceMember = RequireWorkspaceRole(WorkspaceRole.VIEWER)

RequireOrgMember = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.MEMBER]
)

RequireOrgAdmin = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN]
)

RequireOrgOwner = RequireOrgRole([OrganizationRole.OWNER])