"""Dependencies Module for FlowPilot AI."""

import logging
import re
import uuid
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
from app.core.principal import (
    Principal,
    PrincipalKind,
    get_current_principal,
    set_current_principal,
)
from app.core.scopes import ApiKeyScope, ROUTE_SCOPE_MAP, effective_scopes
from app.core.workspace_permissions import is_at_least
from app.crud.membership_filters import ACTIVE_ONLY
from app.db.session import ReadSessionLocal, SessionLocal
from app.models.audit_log import AuditResourceType
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.user import User
from app.models.user_session import UserSession
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole
from app.services import api_key_service
from app.services import organization_service
from app.services import workspace_member_service
from app.services import workspace_service
from app.services.denial_aggregation_service import record_threshold_denial
from app.services.identity import session_policy_service

logger = logging.getLogger("app.api.deps")

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/auth/login"
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_read_db() -> Generator[Session, None, None]:
    db = ReadSessionLocal()
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

        principal = Principal.for_api_key(api_key_id=key.id, issuer_user_id=user.id)
        set_current_principal(principal)

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

    principal = Principal.for_user(user.id)
    set_current_principal(principal)

    if request is not None:
        request.state.user_id = user.id
        request.state.principal = principal
        if claims.session_id is not None:
            request.state.session_id = claims.session_id

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
ReadDbSession = Annotated[Session, Depends(get_read_db)]
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


def _assert_sso_compliance(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMember,
    session_id: Optional[uuid.UUID],
) -> None:
    policy = session_policy_service.get_or_create_policy(
        db, organization_id=organization_id
    )

    role_str = (
        membership.role.value
        if hasattr(membership.role, "value")
        else str(membership.role)
    )

    if not session_policy_service.sso_required_for(policy, org_role=role_str):
        return

    if session_id is None:
        raise OrganizationPermissionDeniedError(
            "This organization requires SSO authentication."
        )

    user_session = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .one_or_none()
    )

    if user_session is None:
        raise OrganizationPermissionDeniedError(
            "This organization requires SSO authentication."
        )

    auth_method = (
        user_session.auth_method.value
        if hasattr(user_session.auth_method, "value")
        else str(user_session.auth_method)
    )

    if auth_method == "PASSWORD":
        raise OrganizationPermissionDeniedError(
            "This organization requires SSO authentication."
        )


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


async def get_sso_compliant_organization_context(
    request: Request,
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    token: str = Depends(oauth2_scheme),
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

    principal = getattr(request.state, "principal", None) or get_current_principal()
    if not (principal and (principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY)):
        claims = security.decode_access_token_claims(token)
        session_id = claims.session_id if claims else None
        _assert_sso_compliance(
            db,
            organization_id=organization.id,
            membership=membership,
            session_id=session_id,
        )

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
SSOCompliantOrgContext = Annotated[OrganizationContext, Depends(get_sso_compliant_organization_context)]
WorkspaceCtx = Annotated[TenantContext, Depends(get_workspace_context)]


class RequireScope:
    def __init__(self, required_scope: Optional[ApiKeyScope] = None) -> None:
        self.required_scope = required_scope

    def __call__(
        self,
        request: Request,
        context: OrganizationContext = Depends(get_organization_context),
    ) -> OrganizationContext:
        principal = getattr(request.state, "principal", None) or get_current_principal()
        if principal and (principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY):
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


async def require_superadmin(
    request: Request,
    current_user: User = Depends(get_verified_user),
) -> User:
    if not bool(getattr(current_user, "is_superuser", False)):
        logger.warning(
            "PLATFORM_ACCESS_DENIED | user=%s | path=%s | reason=not_superuser",
            current_user.id,
            request.url.path if request is not None else "?",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    principal = getattr(request.state, "principal", None) or get_current_principal()
    if principal is not None and (
        principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY
    ):
        logger.warning(
            "PLATFORM_ACCESS_DENIED | user=%s | path=%s | reason=api_key_principal",
            current_user.id,
            request.url.path if request is not None else "?",
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )

    return current_user


RequireSuperAdmin = require_superadmin
SuperAdminUser = Annotated[User, Depends(require_superadmin)]


# ---------------------------------------------------------------------------
# ARCH-21 §3.1 — Public API Gateway Principal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicApiPrincipal:
    api_key: Any
    membership: OrganizationMember
    organization: Organization
    tier: Any
    ef_search: int

    @property
    def api_key_id(self) -> uuid.UUID:
        return self.api_key.id

    @property
    def organization_id(self) -> uuid.UUID:
        return self.organization.id

    @property
    def user_id(self) -> Optional[uuid.UUID]:
        return self.membership.user_id


def _gateway_unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


async def require_api_key(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> PublicApiPrincipal:
    from app.core.api_tiers import ef_search_for, parse_tier
    from app.core.rate_limit.backend import RateLimitDecision
    from app.core.rate_limit.limiter import consume_rate_limit
    from app.core.rate_limit.policy import (
        FailureMode,
        RateLimitPolicy,
        RateLimitScope,
    )

    if not token or not token.startswith(("fp_live_", "fp_test_")):
        raise _gateway_unauthorized(
            "A FlowPilot API key is required for the public API."
        )

    result = api_key_service.authenticate_api_key_token(db, token=token)
    if result is None:
        raise _gateway_unauthorized("Could not validate credentials")

    key, membership = result

    if not key.is_public_api_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "This API key is not enabled for the public API. Enable it "
                "in the developer portal."
            ),
        )

    organization = crud.get_organization_by_id(
        db, organization_id=key.organization_id
    )
    if organization is None:
        raise _gateway_unauthorized("Could not validate credentials")

    principal = Principal.for_api_key(
        api_key_id=key.id, issuer_user_id=membership.user_id
    )
    set_current_principal(principal)

    request.state.user_id = membership.user_id
    request.state.api_key_id = key.id
    request.state.api_key_obj = key
    request.state.api_key_membership = membership
    request.state.principal = principal

    tier = parse_tier(key.tier_key)

    policy = RateLimitPolicy(
        name=f"public_api_{tier.value.lower()}",
        scope=RateLimitScope.API_KEY,
        limit=int(key.rate_limit_per_minute),
        window_seconds=60,
        failure_mode=FailureMode.FAIL_CLOSED,
    )

    decision: RateLimitDecision = consume_rate_limit(request, policy)

    request.state.public_rate_limit = {
        "limit": policy.limit,
        "remaining": max(int(decision.remaining), 0),
        "reset_seconds": int(decision.reset_seconds),
        "tier": tier.value,
        "api_key_id": str(key.id),
        "organization_id": str(key.organization_id),
        "allowed": bool(decision.allowed),
    }

    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Rate limit exceeded for tier {tier.value} "
                f"({policy.limit} requests/minute)."
            ),
            headers={
                "Retry-After": str(int(decision.reset_seconds)),
                "X-RateLimit-Limit": str(policy.limit),
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": str(int(decision.reset_seconds)),
            },
        )

    return PublicApiPrincipal(
        api_key=key,
        membership=membership,
        organization=organization,
        tier=tier,
        ef_search=ef_search_for(tier),
    )


PublicApiCtx = Annotated[PublicApiPrincipal, Depends(require_api_key)]
OrgAdminCtx = Annotated[OrganizationContext, Depends(RequireOrgAdmin)]
