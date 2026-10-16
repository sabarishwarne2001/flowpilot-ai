"""Dependencies Module for FlowPilot AI."""

import logging
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Annotated, Any, Generator, Optional, Sequence, Union

from fastapi import Depends, HTTPException, Path, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from app import crud
from app.core import security
from app.core.audit_routes import resolve_route_audit
from app.core.config import settings
from app.core.exceptions import (
    OrganizationMfaRequiredError,
    OrganizationNetworkNotAllowedError,
    OrganizationSsoRequiredError,
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
from app.core.scopes import PEOPLE_ONLY_ROUTES, ApiKeyScope, ROUTE_SCOPE_MAP, effective_scopes
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
from app.models.user_session import SessionRevokedReason, UserSession
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


class ResolvableDependency:
    @property
    def __globals__(self) -> dict[str, Any]:
        return sys.modules[self.__class__.__module__].__dict__


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


def _credentials_exception() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )


@dataclass(frozen=True)
class _Authenticated:
    user: User
    principal: Principal
    key: Any = None
    membership: Optional[OrganizationMember] = None
    session_id: Optional[uuid.UUID] = None


async def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> User:
    # F-218. Every authenticated request runs this, and it queries the database (the
    # user, the session row, an API key and its plan). As an `async def` doing that
    # work inline, it held the event loop for every query: one slow lookup (a lock
    # wait, a cold connection) stalled every other request in the process. The
    # lookups run in the threadpool; the principal is set HERE, on the request's own
    # context, because a context variable set inside a pool thread is lost when the
    # thread returns. Dependencies resolve one after another, so the request's
    # session is never used from two threads at once.
    authenticated = await run_in_threadpool(_authenticate, db, token)
    set_current_principal(authenticated.principal)

    if request is not None:
        request.state.user_id = authenticated.user.id
        request.state.principal = authenticated.principal
        if authenticated.key is not None:
            request.state.api_key_id = authenticated.key.id
            request.state.api_key_obj = authenticated.key
            request.state.api_key_membership = authenticated.membership
            await run_in_threadpool(_assert_api_key_route_scope, request, db, authenticated.key,
                                    authenticated.membership, authenticated.principal)
        if authenticated.session_id is not None:
            request.state.session_id = authenticated.session_id

    return authenticated.user


def _route_scope(request: Request) -> Optional[ApiKeyScope]:
    """The scope an API key needs for the matched route, or None when no scope covers it.

    A route that declares `RequireScope(<scope>)` names its own; otherwise ROUTE_SCOPE_MAP
    does, keyed by the route's template (not the raw path), so `/work-items/{id}` can never
    match `/work-items` by accident.
    """
    route = request.scope.get("route")
    for dependency in getattr(route, "dependencies", None) or []:
        call = getattr(dependency, "dependency", None)
        if isinstance(call, RequireScope) and call.required_scope is not None:
            return call.required_scope
    method, template = _route_key(request)
    return ROUTE_SCOPE_MAP.get((method, template)) or ROUTE_SCOPE_MAP.get((method, template.rstrip("/")))


def _route_key(request: Request) -> tuple[str, str]:
    """The matched route as `ROUTE_SCOPE_MAP` keys it: the method and the template without /api/v1."""
    route = request.scope.get("route")
    template = getattr(route, "path_format", None) or getattr(route, "path", None) or request.url.path
    prefix = getattr(settings, "API_V1_STR", "")
    if prefix and template.startswith(prefix):
        template = template[len(prefix):]
    return request.method.upper(), template


def _assert_api_key_route_scope(request: Request, db: Session, key: Any, membership: Any,
                                principal: Optional[Principal] = None) -> None:
    """Campaign session 2. An API key may call only routes a scope covers, and only with that scope.

    Before, scopes were checked only on routes that declared `RequireScope`; every other
    tenant route accepted any key with the full rights of the member who issued it, so a
    read-only key could upload, delete documents and read the member list.

    Each refusal is audited with the key and the reason, through the denial aggregation (one row
    each, then summary rows), so a script calling with a refused key cannot fill the audit log.
    """
    method, template = _route_key(request)
    required = _route_scope(request)
    if required is None:
        # A route kept for people (approving a payment in Helm, say) says why in its own words.
        words = PEOPLE_ONLY_ROUTES.get((method, template))
        _audit_key_refusal(request, db, key, principal, {
            "reason": "people_only" if words else "api_key_unmapped_route", "route": f"{method} {template}"})
        raise OrganizationPermissionDeniedError(
            words or "API keys cannot call this route: no API key scope covers it."
        )
    if required not in effective_scopes(key, membership):
        _audit_key_refusal(request, db, key, principal, {
            "reason": "api_key_scope", "route": f"{method} {template}", "required_scope": required.value})
        raise OrganizationPermissionDeniedError(
            f"API key lacks the required scope: {required.value}"
        )


def _audit_key_refusal(request: Request, db: Session, key: Any, principal: Optional[Principal],
                       details: dict[str, Any]) -> None:
    res_type, action = resolve_route_audit(request, default_resource=AuditResourceType.ORGANIZATION)
    record_threshold_denial(db=db, organization_id=key.organization_id, actor_id=getattr(key, "user_id", None),
                            resource_type=res_type, action=action, request=request, principal=principal,
                            details=details)


def _authenticate(db: Session, token: str) -> _Authenticated:
    credentials_exception = _credentials_exception()

    if token and token.startswith(("fp_live_", "fp_test_")):
        res = api_key_service.authenticate_api_key_token(db, token=token)
        if res is None:
            raise credentials_exception
        key, membership = res
        user = membership.user
        # HM-S1:api-key-capability-gate. A key authenticates only while the
        # organization's plan includes the developer API. 402, not 401: the
        # key is genuine, the plan does not include what it is for.
        from app.api.capability_gate import require_capability_for_organization
        from app.core.entitlements import DEVELOPER_API_CAPABILITY

        require_capability_for_organization(
            db,
            organization_id=key.organization_id,
            actor_id=user.id,
            capability_key=DEVELOPER_API_CAPABILITY,
            operation="api_key.authenticate",
            audit=False,
        )

        return _Authenticated(
            user=user,
            principal=Principal.for_api_key(api_key_id=key.id, issuer_user_id=user.id),
            key=key,
            membership=membership,
        )

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

    if _session_is_revoked(db, claims):
        logger.info(
            "AUTH_REJECTED | user=%s | session=%s | reason=session_revoked",
            user.id,
            claims.session_id,
        )
        raise credentials_exception

    return _Authenticated(
        user=user,
        principal=Principal.for_user(user.id),
        session_id=claims.session_id,
    )


def _token_predates_revocation(
    claims: security.AccessTokenClaims,
    user: User,
) -> bool:
    if user.sessions_revoked_at is None:
        return False

    issued = int(claims.issued_at.timestamp())
    cutoff = int(user.sessions_revoked_at.timestamp())
    # F-099. `iat` has one-second resolution. A token that carries a session is
    # also refused through its revoked session row, so a token minted in the
    # same second as the cutoff (a fresh sign-in right after a reset) is kept.
    # A token WITHOUT a session has no other way to be revoked, so for it the
    # cutoff second itself counts as "before": otherwise a token minted in the
    # same second as a revocation would outlive it until it expired.
    if claims.session_id is None:
        return issued <= cutoff
    return issued < cutoff


def _session_is_revoked(
    db: Session,
    claims: security.AccessTokenClaims,
) -> bool:
    """
    Rejects an access token whose own session row has been revoked.

    P2.4 requires that revoking one device produces a 401 on that device's
    NEXT request. _token_predates_revocation only reads
    users.sessions_revoked_at, which is a per-USER cutoff stamped by global
    logout and password change. Single-session revocation
    (DELETE /auth/sessions/{id}) sets user_sessions.revoked_at and nothing
    else, so before this check the revoked device kept authenticating on its
    existing access token for the remainder of ACCESS_TOKEN_EXPIRE_MINUTES
    while already having disappeared from the caller's Active Sessions list.
    The UI said signed-out; the token said otherwise.

    Bounded rather than unbounded -- rotation already refused the revoked
    family, so the device could not extend past one access-token lifetime --
    but "up to ten minutes" is not what "revoke this device" means to the
    person clicking it, and ten minutes is ample for the case that button
    exists for.

    One indexed primary-key lookup on a request that has already done a user
    lookup. Tokens minted before session_id was in the claim set carry
    session_id=None and are left to the users.sessions_revoked_at cutoff;
    they cannot be attributed to a session, so there is nothing to check.
    """
    if claims.session_id is None:
        return False

    row = db.execute(
        select(
            UserSession.revoked_at, UserSession.revoked_reason, UserSession.family_id
        ).where(UserSession.id == claims.session_id)
    ).first()

    # No row at all means the session was hard-deleted (erasure sweep, or a
    # session id that never existed). Treat that as revoked rather than as
    # absent: scalar_one_or_none() would collapse "no row" and
    # "row with revoked_at IS NULL" into the same None and fail open.
    if row is None:
        return True

    revoked_at, revoked_reason, family_id = row
    if revoked_at is None:
        return False

    # F-126. A session retired only by ROTATION was not revoked by anyone: another tab of
    # the same sign-in refreshed. Its access token keeps working until it expires, as long
    # as the sign-in itself is alive. Refusing it made every refresh in one tab cost the
    # other tab a 401. Sign-out, device revocation and reuse detection end the whole
    # sign-in (no live session left), so they still refuse it at once.
    if revoked_reason is SessionRevokedReason.ROTATED:
        from app.services import session_service

        return not session_service.family_is_live(db, family_id=family_id)
    return True


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


def _security_policy(db: Session, organization_id: uuid.UUID):
    """The organization's sign-in policy, read without creating one (most have none)."""
    from app.models.identity import TenantSecurityPolicy

    return (
        db.query(TenantSecurityPolicy)
        .filter(TenantSecurityPolicy.organization_id == organization_id)
        .one_or_none()
    )


def _assert_sso_compliance(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMember,
    session_id: Optional[uuid.UUID],
    policy: Any = None,
) -> None:
    """Refuse a person whose session does not satisfy the organization's "Require SSO".

    Campaign session 3. This check existed but no route called it, so "Require single sign-on"
    was shown as in force while password sessions kept full access. It now runs on every
    organization- and workspace-scoped request (`_assert_organization_access_policy`), reads the
    policy without creating one, and accepts only a session opened through one of THIS
    organization's identity providers: a SAML or OIDC session from another organization's
    provider proves nothing about this one.
    """
    if policy is None:
        policy = _security_policy(db, organization_id)
    if policy is None:
        return

    role_str = (
        membership.role.value
        if hasattr(membership.role, "value")
        else getattr(membership.role, "value", membership.role)
    )

    if not session_policy_service.sso_required_for(policy, org_role=role_str):
        return

    if not _session_from_organization_idp(db, session_id=session_id, organization_id=organization_id):
        raise OrganizationSsoRequiredError(
            "This organization requires SSO authentication. Sign in through your organization's "
            "identity provider to continue."
        )


def _session_from_organization_idp(
    db: Session, *, session_id: Optional[uuid.UUID], organization_id: uuid.UUID
) -> bool:
    """Whether the session was opened through one of this organization's identity providers."""
    from app.models.identity import EnterpriseIdpConfig

    if session_id is None:
        return False
    user_session = (
        db.query(UserSession)
        .filter(UserSession.id == session_id, UserSession.revoked_at.is_(None))
        .one_or_none()
    )
    if user_session is None:
        return False
    auth_method = (
        user_session.auth_method.value
        if hasattr(user_session.auth_method, "value")
        else str(user_session.auth_method)
    )
    if auth_method == "PASSWORD" or user_session.idp_config_id is None:
        return False
    idp_organization_id = db.execute(
        select(EnterpriseIdpConfig.organization_id).where(
            EnterpriseIdpConfig.id == user_session.idp_config_id
        )
    ).scalar_one_or_none()
    return idp_organization_id == organization_id


def _assert_two_factor(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMember,
    session_id: Optional[uuid.UUID],
    policy: Any,
) -> None:
    """Campaign session 3. "Require two-factor": a password session needs a second factor on the
    account; a session from the organization's own identity provider relies on the provider's."""
    if not getattr(policy, "require_mfa", False):
        return
    from app.services import mfa_service

    if mfa_service.is_enabled(db, membership.user_id):
        return
    if _session_from_organization_idp(db, session_id=session_id, organization_id=organization_id):
        return
    raise OrganizationMfaRequiredError(
        "This organization requires two-factor sign-in. Turn on two-factor sign-in for your "
        "account to continue."
    )


def _assert_network_allowed(address: Optional[str], policy: Any) -> None:
    """Campaign session 3. The organization's IP allowlist, for people and API keys alike.

    `address` is the strictly resolved client address (`trusted_client_ip`): behind a proxy
    whose forwarding chain cannot be trusted it is unknown, and an unknown address is refused.
    """
    if policy is None or not policy.ip_allowlist:
        return
    if session_policy_service.ip_allowed(policy, address):
        return
    raise OrganizationNetworkNotAllowedError(
        "This organization allows access only from its approved networks, and your address "
        f"({address or 'unknown'}) is not one of them. Connect from an approved network, or ask "
        "an owner to add yours."
    )


def assert_organization_sign_in_policy(
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMember,
    session_id: Optional[uuid.UUID],
    client_address: Optional[str],
    api_key: bool = False,
) -> None:
    """The organization's sign-in policy for one caller (campaign session 3).

    The IP allowlist applies to people and API keys; single sign-on and two-factor to people
    only. Shared by
    every organization- and workspace-scoped dependency and by the live review WebSocket's gate,
    which authenticates outside the dependency system.
    """
    policy = _security_policy(db, organization_id)
    if policy is None:
        return
    _assert_network_allowed(client_address, policy)
    if api_key:
        return
    _assert_sso_compliance(
        db,
        organization_id=organization_id,
        membership=membership,
        session_id=session_id,
        policy=policy,
    )
    _assert_two_factor(
        db,
        organization_id=organization_id,
        membership=membership,
        session_id=session_id,
        policy=policy,
    )


def _is_api_key_request(request: Optional[Request]) -> bool:
    principal = (getattr(request.state, "principal", None) if request is not None else None) or (
        get_current_principal()
    )
    return bool(
        principal and (principal.kind == "API_KEY" or principal.kind is PrincipalKind.API_KEY)
    )


def _assert_organization_access_policy(
    request: Optional[Request],
    db: Session,
    *,
    organization_id: uuid.UUID,
    membership: OrganizationMember,
    not_found: Optional[Exception] = None,
) -> None:
    """The organization's sign-in policy, checked on every request into it (campaign session 3).

    Called by every organization- and workspace-scoped context dependency, so a route added later
    is covered without anyone remembering a guard.

    An API key goes no further than the organization that issued it. Its scopes come from its
    issuer's role THERE; a person who belongs to two organizations could otherwise use a key from
    one on the other, past that organization's sign-in rules, plan and administrators. It is
    refused as a stranger is (`not_found`, 404), so it learns nothing about the organization.
    """
    from app.core.client_ip import trusted_client_ip

    key = getattr(request.state, "api_key_obj", None) if request is not None else None
    if key is not None and getattr(key, "organization_id", None) != organization_id:
        raise not_found or OrganizationAccessDeniedError("Organization not found.")

    assert_organization_sign_in_policy(
        db,
        organization_id=organization_id,
        membership=membership,
        session_id=getattr(request.state, "session_id", None) if request is not None else None,
        client_address=trusted_client_ip(request) if request is not None else None,
        api_key=_is_api_key_request(request),
    )


# F-218. The context dependencies below query the database and hold no context
# variable, so they are plain `def`: FastAPI runs them in the threadpool instead
# of on the event loop.
def get_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
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
    _assert_organization_access_policy(
        request, db, organization_id=organization.id, membership=membership
    )

    # ARCH-30 Tranche 3 (D-11). Read-only billing states refuse writes here,
    # once, for every organization-scoped route.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    assert_billing_writes_allowed(request, db, organization_id=organization.id)

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


def get_billing_organization_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
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
    _assert_organization_access_policy(
        request, db, organization_id=organization.id, membership=membership
    )

    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


def get_workspace_context(
    workspace_id: uuid.UUID = Path(..., description="Workspace identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
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
    _assert_organization_access_policy(
        request,
        db,
        organization_id=workspace.organization_id,
        membership=access.organization_membership,
        not_found=WorkspaceAccessDeniedError("Workspace not found."),
    )

    # ARCH-30 Tranche 3 (D-11). Same gate for every workspace-scoped route.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    assert_billing_writes_allowed(request, db, organization_id=workspace.organization_id)

    return TenantContext(
        user=current_user,
        organization=workspace.organization,
        organization_membership=access.organization_membership,
        workspace=workspace,
        workspace_membership=access.workspace_membership,
        effective_workspace_role=access.effective_role,
    )


def get_archived_workspace_context(
    workspace_id: uuid.UUID = Path(..., description="Workspace identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
) -> TenantContext:
    """The workspace context WITHOUT the "is it active" check, for restoring it and nothing else.

    F-131. `POST /workspaces/{id}/restore` used get_workspace_context, which refuses an archived
    workspace, so a restore could never succeed. Access is resolved exactly as for any workspace
    route, the organization must still be operational, and the restore itself checks the
    organization role (owner or admin).
    """
    workspace = workspace_service.get_workspace_or_raise(db, workspace_id=workspace_id)
    access = workspace_member_service.resolve_workspace_access(
        db, workspace=workspace, user_id=current_user.id
    )
    if not access.has_access:
        raise WorkspaceAccessDeniedError("Workspace not found.")

    organization_service.assert_organization_operational(workspace.organization)

    assert access.organization_membership is not None
    assert access.effective_role is not None
    _assert_organization_access_policy(
        request,
        db,
        organization_id=workspace.organization_id,
        membership=access.organization_membership,
        not_found=WorkspaceAccessDeniedError("Workspace not found."),
    )
    return TenantContext(
        user=current_user,
        organization=workspace.organization,
        organization_membership=access.organization_membership,
        workspace=workspace,
        workspace_membership=access.workspace_membership,
        effective_workspace_role=access.effective_role,
    )


def get_archived_organization_owner_context(
    organization_id: uuid.UUID = Path(..., description="Organization identifier"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_verified_user),
    request: Request = None,  # type: ignore[assignment]  # injected by FastAPI
) -> OrganizationContext:
    """The organization context WITHOUT the "is it active" check, for its owner to restore it.

    F-132. Archiving was described as reversible, but every organization route refuses an
    archived organization, so nothing could reverse it. Only an active OWNER membership passes.
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
    if membership.role is not OrganizationRole.OWNER:
        raise OrganizationPermissionDeniedError(
            "Only an organization owner can restore the organization."
        )
    _assert_organization_access_policy(
        request, db, organization_id=organization.id, membership=membership
    )
    return OrganizationContext(
        user=current_user,
        organization=organization,
        membership=membership,
    )


OrgContext = Annotated[OrganizationContext, Depends(get_organization_context)]
WorkspaceCtx = Annotated[TenantContext, Depends(get_workspace_context)]


class RequireScope(ResolvableDependency):
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


class RequireOrgRole(ResolvableDependency):
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
                "You do not have permission to perform this action in this organization."
            )
        return context


class RequireWorkspaceRole(ResolvableDependency):
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
                "You do not have permission to perform this action in this workspace."
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
#: F-192. The roles that read the organization's money: plan, invoices, usage and the
#: spend limits in force. BILLING exists for exactly this (a finance contact who never
#: sees documents); changing any of it stays with OWNER (plan, seats) or ADMIN (limits).
RequireOrgUsageReader = RequireOrgRole(
    [OrganizationRole.OWNER, OrganizationRole.ADMIN, OrganizationRole.BILLING]
)
#: Every organization role, BILLING included: for reads of the caller's own
#: data (their notifications, the plan's entitlements), where no role is less
#: entitled than another.
RequireAnyOrgRole = RequireOrgRole(
    [
        OrganizationRole.OWNER,
        OrganizationRole.ADMIN,
        OrganizationRole.BILLING,
        OrganizationRole.MEMBER,
    ]
)


async def require_superadmin(
    request: Request,
    current_user: User = Depends(get_verified_user),
) -> User:
    if not bool(getattr(current_user, "is_superuser", False)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Not Found",
        )
    return current_user


RequireSuperAdmin = require_superadmin
SuperAdminUser = Annotated[User, Depends(require_superadmin)]


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


def _authenticate_public_api_key(db: Session, token: str) -> tuple[Any, OrganizationMember, Organization]:
    """The public API key, its membership and organization, refusing a key that may not call it."""
    result = api_key_service.authenticate_api_key_token(db, token=token)
    if result is None:
        raise _gateway_unauthorized("Could not validate credentials")

    key, membership = result
    if not key.is_public_api_enabled:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This API key is not enabled for the public API. An organization admin can enable it in the developer portal.",
        )

    organization = crud.get_organization_by_id(db, organization_id=key.organization_id)
    if organization is None:
        raise _gateway_unauthorized("Could not validate credentials")

    # HM-S1:public-api-capability-gate. Same rule as the console path in
    # get_current_user: the public API is part of the developer API.
    from app.api.capability_gate import require_capability_for_organization
    from app.core.entitlements import DEVELOPER_API_CAPABILITY

    require_capability_for_organization(
        db,
        organization_id=key.organization_id,
        actor_id=membership.user_id,
        capability_key=DEVELOPER_API_CAPABILITY,
        operation="public_api.authenticate",
        audit=False,
    )

    return key, membership, organization


async def require_api_key(
    request: Request,
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> PublicApiPrincipal:
    from app.core.api_tiers import ef_search_for, parse_tier
    from app.core.rate_limit.backend import RateLimitDecision
    from app.core.rate_limit.limiter import consume_rate_limit
    from app.core.rate_limit.policy import FailureMode, RateLimitPolicy, RateLimitScope

    if not token or not token.startswith(("fp_live_", "fp_test_")):
        raise _gateway_unauthorized("A FlowPilot API key is required for the public API.")

    # F-218. The key, organization and plan lookups, the Redis rate limit and the
    # billing gate are blocking I/O: they run in the threadpool, so one slow
    # lookup no longer stalls the event loop. The principal is set here, on the
    # request's own context (a context variable set in a pool thread is lost).
    key, membership, organization = await run_in_threadpool(_authenticate_public_api_key, db, token)

    principal = Principal.for_api_key(api_key_id=key.id, issuer_user_id=membership.user_id)
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
    decision: RateLimitDecision = await run_in_threadpool(consume_rate_limit, request, policy)

    if not decision.allowed:
        # ARCH50-S1:rate-limit-429-headers (GA-2, verify_arch21 G16). This 429 short-circuits
        # PublicApiRateLimitMiddleware, so it carries the rate limit headers itself: a client
        # that backs off on X-RateLimit-* / RateLimit-* sees them on the refusal too. The names
        # are app.middleware.public_rate_limit.RATE_LIMIT_HEADERS (CORS exposes them).
        reset = str(int(decision.reset_seconds))
        limit = str(int(policy.limit))
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded for tier {tier.value} ({policy.limit} requests/minute).",
            headers={
                "Retry-After": reset,
                "X-RateLimit-Limit": limit,
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": reset,
                "RateLimit-Limit": limit,
                "RateLimit-Remaining": "0",
                "RateLimit-Reset": reset,
                "X-RateLimit-Tier": tier.value,
            },
        )

    # PHASE 4. The snapshot PublicApiRateLimitMiddleware turns into the
    # X-RateLimit-* / RateLimit-* headers and the gateway echoes in each
    # body's `rate_limit`. Nothing ever recorded it: successful responses
    # carried no rate limit headers at all and every body reported tier FREE,
    # limit 0, remaining 0, whatever the key's real tier.
    request.state.public_rate_limit = {
        "tier": tier.value,
        "limit": int(policy.limit),
        "remaining": max(0, int(decision.remaining)),
        "reset_seconds": int(decision.reset_seconds),
    }

    # ARCH30-T4F:api-key-billing-gate — A6.
    #
    # The D-11 audit found the read-only gate wired into
    # `get_organization_context`
    # and `get_workspace_context` — and nowhere on the API-key path. A
    # LAPSED organization could still POST /v1/query (which spends LLM
    # and embedding quota) and POST /v1/workflows/{id}/trigger (which
    # commits to the outbox) with a key minted while it was paying.
    #
    # Gated HERE, in the shared dependency, rather than route by route.
    # `assert_billing_writes_allowed` already returns immediately for
    # non-mutating methods, so reads are untouched, and every public API
    # route that exists now or is added later is covered without anybody
    # having to remember. `verify_arch30_tranche4_final --gates a6` walks
    # the live route table and fails if a mutating API-key route ever
    # resolves without passing through this dependency.
    from app.api.billing_write_gate import assert_billing_writes_allowed

    await run_in_threadpool(
        assert_billing_writes_allowed, request, db, organization_id=organization.id
    )

    # Campaign session 3. The organization's IP allowlist binds its API keys too.
    from app.core.client_ip import trusted_client_ip

    await run_in_threadpool(
        lambda: _assert_network_allowed(trusted_client_ip(request), _security_policy(db, organization.id))
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