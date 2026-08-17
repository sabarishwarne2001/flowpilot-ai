"""
Dependencies Module for FlowPilot AI.

Hosts database session factories, authentication guards, and the tenant context
resolution chain.
"""

import logging
import re
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Annotated, Any, Generator, Optional, Sequence, Union

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session
from starlette.requests import Request

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
from app.core.principal import Principal
from app.core.scopes import ApiKeyScope, ROUTE_SCOPE_MAP, effective_scopes
from app.core.workspace_permissions import is_at_least
from app.crud.membership_filters import ACTIVE_ONLY
from app.db.session import SessionLocal
from app.models.audit_log import AuditResourceType
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

_principal_var: ContextVar[Optional[Principal]] = ContextVar("principal", default=None)


def get_current_principal() -> Optional[Principal]:
    return _principal_var.get()


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

    if token and token.startswith(("fp_live_", "fp_test_")):
        res = api_key_service.authenticate_api_key_token(db, token=token)
        if res is None:
            raise credentials_exception
        key, membership = res
        user = membership.user

        principal = Principal(kind="API_KEY", user_id=user.id, api_key_id=key.id)
        _principal_var.set(principal)

        if request is not None:
            request.state.user_id = user.id
            request.state.api_key_id = key.id
            request.state.api_key_obj = key
            request.state.api_key_membership = membership
            request.state.principal = principal

        return user

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

    principal = Principal(kind="USER", user_id=user.id, api_key_id=None)
    _principal_var.set(principal)

    if request is not None:
        request.state.user_id = user.id
        request.state.principal = principal

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


class RequireScope:
    """Runtime scope enforcement for API keys with deny-by-default (ARCH-08.1 F1)."""

    def __init__(self, required_scope: Optional[ApiKeyScope] = None) -> None:
        self.required_scope = required_scope

    def __call__(
        self,
        request: Request,
        context: OrganizationContext = Depends(get_organization_context),
    ) -> OrganizationContext:
        principal = getattr(request.state, "principal", None) or get_current_principal()
        if principal and principal.kind == "API_KEY":
            target_scope = self.required_scope

            if target_scope is None:
                method = request.method.upper()
                raw_path = request.url.path
                clean_path = raw_path.replace(settings.API_V1_STR, "") if hasattr(settings, "API_V1_STR") else raw_path

                for (m, route_template), mapped_scope in ROUTE_SCOPE_MAP.items():
                    if m.upper() == method:
                        pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", route_template) + "$"
                        if re.match(pattern, clean_path) or clean_path.rstrip("/") == route_template.rstrip("/"):
                            target_scope = mapped_scope
                            break

            if target_scope is None:
                raise OrganizationPermissionDeniedError(
                    "API key access is prohibited on unmapped routes with no scope declaration."
                )

            key_obj = getattr(request.state, "api_key_obj", None)
            membership_obj = getattr(request.state, "api_key_membership", None)

            if not key_obj or not membership_obj:
                raise OrganizationPermissionDeniedError("API key authentication context missing.")

            eff_scopes = effective_scopes(key_obj, membership_obj)
            if target_scope not in eff_scopes:
                raise OrganizationPermissionDeniedError(
                    f"API key lacks the required scope: {target_scope.value}"
                )

        return context


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

OrgAdminCtx = Annotated[OrganizationContext, Depends(RequireOrgAdmin)]