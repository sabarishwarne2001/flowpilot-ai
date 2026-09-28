"""ARCH48-S1:gate — who may use the live review channel, checked at the handshake and again while it is open.

THE SAME SESSION, NOT A NEW SCHEME
==================================

The console holds its access token in memory and sends it as
`Authorization: Bearer` on every REST call (the refresh token is an HttpOnly
cookie scoped to /auth/refresh; the access token is never a cookie). A
browser cannot set Authorization on a WebSocket, so the console offers the
same token in the one header a page may set: the subprotocol list,
`["flowpilot.review.v1", "bearer.<access token>"]`. Non-browser clients may
send `Authorization: Bearer` instead. The token is validated by exactly the
checks `deps.get_current_user` / `get_verified_user` / `get_workspace_context`
apply (signature, expiry, type; the user exists, is active and verified; the
token was not issued before a global sign-out; its own session row is not
revoked; workspace access through the organization; organization and
workspace operational) plus the review hub's own role (CONTRIBUTOR) and the
capability. A token in the query string is REFUSED, never read: it would be
written into every access log between the browser and uvicorn.

API keys (`fp_live_` / `fp_test_`) cannot open a live connection: presence
and soft locks are a person's, and a key has no person at a screen.

The Host header is held to the same rule HostTenantMiddleware applies to HTTP
(Starlette's BaseHTTPMiddleware passes WebSocket scopes through untouched, so
the middleware never sees them): the platform host, or a verified custom
domain when custom domains are enabled -- anything else is refused.

REFUSAL IS BEFORE ACCEPT
========================

`authenticate` raises `LiveRefused`; the endpoint closes with 1008 (policy)
WITHOUT accepting, so a refused client never receives a single frame -- the
WebSocket equivalent of a 401/402/403. The reason is logged, never sent.
Denials are not written to the audit log: a browser retrying a refused
handshake would fill it (the REST routes, which carry the same gate, audit
their 402s).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace
from typing import Mapping, Optional

from sqlalchemy.orm import Session

from app.services.collab import vocabulary as v

logger = logging.getLogger("app.services.collab.gate")


class LiveRefused(Exception):
    """The handshake (or a re-check of an open connection) is refused."""

    def __init__(self, reason: str, *, close_code: int = v.CLOSE_POLICY) -> None:
        super().__init__(reason)
        self.reason = reason
        self.close_code = close_code


@dataclass
class LivePrincipal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    name: str
    role: str
    session_id: Optional[uuid.UUID]
    token_expires_at: datetime
    token_issued_at: datetime
    allowed_kinds: tuple[str, ...] = field(default_factory=tuple)

    def person(self) -> dict:
        return {"user_id": str(self.user_id), "email": self.email, "name": self.name}


def capability_key() -> str:
    from app.core.entitlements import COLLABORATIVE_REVIEW_CAPABILITY

    return COLLABORATIVE_REVIEW_CAPABILITY


def has_collab(db: Session, organization_id: uuid.UUID) -> bool:
    from app.api import capability_gate

    return capability_gate.has_capability(db, organization_id=organization_id, capability_key=capability_key())


def require(db: Session, *, context: object, operation: str) -> None:
    """The REST gate: 402 CAPABILITY_REQUIRED (audited) unless the plan includes collaborative review."""
    from app.api import capability_gate

    capability_gate.require_capability(db, context=context, capability_key=capability_key(), operation=operation)


def allowed_kinds(db: Session, organization_id: uuid.UUID) -> tuple[str, ...]:
    """The review kinds this organization's plan shows (the hub's own rule)."""
    from app.api.v1 import review as review_api

    return review_api._allowed_kinds(db, SimpleNamespace(organization_id=organization_id))  # noqa: SLF001


def extract_token(headers: Mapping[str, str], query: Mapping[str, str]) -> tuple[str, bool]:
    """(the bearer token, whether our subprotocol was offered). Raises LiveRefused."""
    for name in ("token", "access_token", "bearer", "auth"):
        if name in query:
            raise LiveRefused("a token in the query string is refused (it would be logged)")
    offered = [p.strip() for p in (headers.get("sec-websocket-protocol") or "").split(",") if p.strip()]
    ours = v.SUBPROTOCOL in offered
    token = next((p[len(v.TOKEN_PREFIX):] for p in offered if p.startswith(v.TOKEN_PREFIX)), "")
    if not token:
        auth = headers.get("authorization") or ""
        if auth[:7].lower() == "bearer ":
            token = auth[7:].strip()
    if not token:
        raise LiveRefused("no access token")
    if token.startswith(("fp_live_", "fp_test_")):
        raise LiveRefused("API keys cannot open a live review connection")
    return token, ours


def check_host(host_header: Optional[str]) -> None:
    from app.core.config import settings
    from app.middleware import host_tenant

    host = host_tenant.normalise_host(host_header)
    if host_tenant._is_platform_host(host):  # noqa: SLF001 - the middleware's own rule
        return
    if not getattr(settings, "CUSTOM_DOMAINS_ENABLED", False):
        raise LiveRefused("unknown host")
    if host_tenant.HostTenantMiddleware._resolve(host) is None:  # noqa: SLF001
        raise LiveRefused("unknown host")


def check_origin(origin: Optional[str], host_header: Optional[str]) -> None:
    """ARCH48-S1:ws-origin. A browser always sends Origin on a WebSocket; it must be this host (the SPA and
    the API share an origin behind Caddy, custom domains included) or a configured CORS origin (development:
    Vite on another port). No Origin at all is a non-browser client, which needs the token anyway. The token
    is never ambient (not a cookie), so this is defence in depth against cross-site WebSocket hijacking."""
    from urllib.parse import urlsplit

    from app.core.config import settings
    from app.middleware import host_tenant

    if not origin:
        return
    if origin.strip().lower() == "null":
        raise LiveRefused("opaque origin")
    allowed = {o.rstrip("/").lower() for o in (getattr(settings, "cors_origins", None) or [])}
    if origin.rstrip("/").lower() in allowed:
        return
    try:
        origin_host = host_tenant.normalise_host(urlsplit(origin).netloc)
    except ValueError as exc:
        raise LiveRefused("malformed origin") from exc
    if origin_host and origin_host == host_tenant.normalise_host(host_header):
        return
    raise LiveRefused("cross-origin WebSocket refused")


def authenticate(db: Session, *, token: str, workspace_id: uuid.UUID) -> LivePrincipal:
    """Everything the REST path checks, plus CONTRIBUTOR and the capability. Raises LiveRefused."""
    from app import crud
    from app.api import deps
    from app.core import security
    from app.core.workspace_permissions import is_at_least
    from app.models.workspace import WorkspaceRole
    from app.services import organization_service, workspace_member_service, workspace_service

    claims = security.decode_access_token_claims(token)
    if claims is None:
        raise LiveRefused("invalid or expired token")
    user = crud.get_user_by_id(db, user_id=claims.subject)
    if user is None:
        raise LiveRefused("no such user")
    if deps._token_predates_revocation(claims, user):  # noqa: SLF001 - the REST path's own check
        raise LiveRefused("token issued before a sign-out")
    if deps._session_is_revoked(db, claims):  # noqa: SLF001
        raise LiveRefused("session revoked")
    if not user.is_active:
        raise LiveRefused("inactive user")
    if user.email_verified_at is None:
        raise LiveRefused("email not verified")
    try:
        workspace = workspace_service.get_workspace_or_raise(db, workspace_id=workspace_id)
    except Exception as exc:  # noqa: BLE001 - not found and not operational read the same
        raise LiveRefused("workspace not found") from exc
    access = workspace_member_service.resolve_workspace_access(db, workspace=workspace, user_id=user.id)
    if not access.has_access or access.effective_role is None:
        raise LiveRefused("no access to this workspace")
    try:
        organization_service.assert_organization_operational(workspace.organization)
        workspace_service.assert_workspace_operational(workspace)
    except Exception as exc:  # noqa: BLE001
        raise LiveRefused("organization or workspace not operational") from exc
    if not is_at_least(access.effective_role, WorkspaceRole.CONTRIBUTOR):
        raise LiveRefused("the review hub needs the contributor role")
    # ARCH48-S1:ws-capability-gate. The WebSocket has no 402: the handshake is refused (1008) instead.
    if not has_collab(db, workspace.organization_id):
        raise LiveRefused("the plan does not include collaborative review")
    role = access.effective_role
    return LivePrincipal(
        user_id=user.id, organization_id=workspace.organization_id, workspace_id=workspace.id,
        email=str(user.email or ""), name=str((user.display_name or user.email or "")).strip(),
        role=str(getattr(role, "value", role)), session_id=claims.session_id, token_expires_at=claims.expires_at,
        token_issued_at=claims.issued_at,
        allowed_kinds=allowed_kinds(db, workspace.organization_id),
    )


def recheck(db: Session, principal: LivePrincipal) -> None:
    """An open connection, re-validated: the session, the person, the access and the plan. Raises LiveRefused."""
    from app import crud
    from app.api import deps
    from app.core.workspace_permissions import is_at_least
    from app.models.workspace import WorkspaceRole
    from app.services import workspace_member_service, workspace_service

    user = crud.get_user_by_id(db, user_id=principal.user_id)
    if user is None or not user.is_active:
        raise LiveRefused("the account is no longer active", close_code=v.CLOSE_SESSION_ENDED)
    claims = SimpleNamespace(issued_at=principal.token_issued_at, session_id=principal.session_id)
    if deps._token_predates_revocation(claims, user):  # noqa: SLF001 - a sign-out everywhere since the handshake
        raise LiveRefused("signed out everywhere", close_code=v.CLOSE_SESSION_ENDED)
    if deps._session_is_revoked(db, claims):  # noqa: SLF001 - this device was signed out
        raise LiveRefused("session revoked", close_code=v.CLOSE_SESSION_ENDED)
    from app.services import organization_service

    try:
        workspace = workspace_service.get_workspace_or_raise(db, workspace_id=principal.workspace_id)
        organization_service.assert_organization_operational(workspace.organization)
        workspace_service.assert_workspace_operational(workspace)
    except Exception as exc:  # noqa: BLE001 - gone, suspended or archived all end the connection
        raise LiveRefused("workspace gone or not operational", close_code=v.CLOSE_ACCESS_LOST) from exc
    access = workspace_member_service.resolve_workspace_access(db, workspace=workspace, user_id=principal.user_id)
    if not access.has_access or access.effective_role is None or not is_at_least(access.effective_role,
                                                                                  WorkspaceRole.CONTRIBUTOR):
        raise LiveRefused("workspace access ended", close_code=v.CLOSE_ACCESS_LOST)
    if not has_collab(db, principal.organization_id):
        raise LiveRefused("the plan no longer includes collaborative review", close_code=v.CLOSE_ACCESS_LOST)
    principal.allowed_kinds = allowed_kinds(db, principal.organization_id)


__all__ = ["LivePrincipal", "LiveRefused", "allowed_kinds", "authenticate", "capability_key", "check_host",
           "check_origin",
           "extract_token", "has_collab", "recheck", "require"]
