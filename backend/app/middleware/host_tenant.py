"""ARCH-25 §5 — host-based tenant resolution.

THIS IS THE PHASE'S SECURITY CENTRE OF GRAVITY
==============================================

Before ARCH-25 the `Host` header was routing metadata. After ARCH-25 it
selects a tenant, which makes it an authentication-adjacent control: a Host
that resolves to the wrong organization is a cross-tenant breach, and it is
one an attacker triggers by editing one line of an HTTP request.

Three invariants, and what each is actually defending against.

INVARIANT 2 — EXACT MATCH AGAINST VERIFIED DOMAINS ONLY
-------------------------------------------------------

`domain_service.resolve_verified_host` compares with `==` against a
lowercased, port-stripped, trailing-dot-stripped hostname, and filters
`status IN ('VERIFIED',)`.

Not `LIKE`, not `endswith`, not a regex. Suffix matching is the classic way
this goes wrong: `endswith("acme.com")` also matches `evil-acme.com`, which an
attacker can register for eleven dollars. Prefix matching fails the same way
in the other direction. There is no pattern here to get wrong because there is
no pattern.

Normalisation happens BEFORE the comparison and is the same normalisation the
database CHECK enforces on the stored column (`hostname = lower(hostname)`,
no port, no trailing dot). Two normalisations that disagree produce a domain
the console shows as VERIFIED and the middleware never matches.

INVARIANT 3 — NO DEFAULT-TENANT FALLBACK
----------------------------------------

There is no `else` branch in this file that assigns a tenant. An unmatched
hostname either falls through as the platform origin (when it IS the platform
origin) or is refused with 404.

The tempting shortcut — "if we cannot resolve it, treat it as the platform" —
is what turns a hostname typo into a silent cross-tenant read. The refusal is
what makes a misconfigured DNS record fail visibly instead of quietly serving
the wrong data.

verify_arch25.py G5 walks this module's AST looking for any assignment to the
resolved-tenant state that is not sourced from `resolve_verified_host`. A grep
would be defeated by an alias; the AST walk is not.

WHY AN UNKNOWN HOST GETS 404 AND NOT 403
----------------------------------------

403 confirms the hostname is known to FlowPilot and merely off-limits. An
attacker sweeping a customer's DNS namespace could then distinguish
`ai.acme.com` (a tenant) from `ai.example.com` (not one) by status code alone,
turning this middleware into a customer-list oracle. 404 says nothing.

WHY THE PLATFORM ORIGIN IS NOT "RESOLVED" AT ALL
------------------------------------------------

A request to `app.flowpilot.ai` sets `request.state.host_organization_id` to
None and continues. That is not a fallback — nothing is selected. The
downstream session and role machinery is unchanged and remains the only thing
that decides which organization a user may read. Host resolution ADDS a
constraint on vanity hosts; it never replaces authorization.

WHAT THIS MIDDLEWARE DELIBERATELY DOES NOT DO
---------------------------------------------

It does not log the user in, does not attach a session, and does not widen any
permission. A verified Host tells us which tenant's BRANDING to serve and
which tenant a public request is scoped to. Every authenticated route still
runs `get_organization_context`, and a user with no membership gets the same
404 they always did — arriving on the tenant's own vanity domain changes
nothing about that.
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import settings

logger = logging.getLogger("app.middleware.host_tenant")

#: Paths that must answer regardless of Host. A health check arrives with
#: whatever the load balancer sends — frequently an IP literal or a
#: Kubernetes service name — and refusing it would take the deployment down
#: while the vanity-domain feature was doing exactly what it was told.
EXEMPT_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/docs",
    "/redoc",
    "/openapi.json",
)


def normalise_host(raw: Optional[str]) -> str:
    """Reduce a Host header to the form stored in `custom_domains.hostname`.

    Strips the port, a trailing dot, surrounding whitespace, and IPv6
    brackets, then lowercases. Everything the database CHECK constraints
    already guarantee about the stored column, applied to the untrusted side
    of the comparison so that the two can actually meet.

    Returns "" for anything unusable, which the caller treats as unmatched
    rather than as a wildcard.
    """
    if not raw:
        return ""
    value = raw.strip()
    if not value:
        return ""

    # IPv6 literal: [::1]:8000
    if value.startswith("["):
        closing = value.find("]")
        if closing == -1:
            return ""
        value = value[1:closing]
    elif ":" in value:
        value = value.split(":", 1)[0]

    return value.strip().rstrip(".").lower()


def platform_hosts() -> frozenset[str]:
    """Hostnames that are the platform itself, not any tenant.

    Sourced from PLATFORM_RESERVED_HOSTS — the same list `domain_service`
    refuses claims against, so a hostname can never be both the platform
    origin and a tenant's vanity domain. One list, two consumers, no way for
    them to disagree.

    Localhost and 127.0.0.1 are included unconditionally so that development
    and the test client work on a deployment with an empty reserved list.
    They are safe to include: `ck_custom_domains_hostname_shape` refuses a
    single-label hostname and `ck_custom_domains_hostname_not_ip` refuses an
    address, so neither can ever appear in `custom_domains`.
    """
    configured = {
        str(entry).strip().lower().rstrip(".")
        for entry in (getattr(settings, "PLATFORM_RESERVED_HOSTS", None) or [])
        if str(entry).strip()
    }
    return frozenset(configured | {"localhost", "127.0.0.1", "::1", "testserver"})


def _is_platform_host(host: str) -> bool:
    if not host:
        # No Host header at all. HTTP/1.1 requires one; its absence is either
        # a probe or a malformed client. Treated as the platform origin so
        # that health checks and internal callers keep working, and NOT as a
        # tenant — nothing is resolved, so nothing is exposed.
        return True
    if host in platform_hosts():
        return True
    # A subdomain of a reserved host is the platform too. Label-boundary
    # comparison, so `notflowpilot.ai` is not treated as a subdomain of
    # `flowpilot.ai` the way a bare `endswith` would have it.
    return any(host.endswith(f".{entry}") for entry in platform_hosts())


class HostTenantMiddleware(BaseHTTPMiddleware):
    """Resolve `Host` to a tenant, or refuse.

    Sets exactly two pieces of request state:

        request.state.host_organization_id  UUID or None
        request.state.host_custom_domain_id UUID or None

    Both are None on the platform origin. Handlers that need a host-resolved
    tenant read the first and 404 when it is None; nothing anywhere reads it
    as a permission.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        # Default state is set on EVERY request, before any branch. A handler
        # reading `request.state.host_organization_id` must never hit an
        # AttributeError and fall into an `except` that treats the absence as
        # something other than "no tenant".
        request.state.host_organization_id = None
        request.state.host_custom_domain_id = None
        request.state.host_hostname = None

        path = request.url.path
        if path.startswith(EXEMPT_PREFIXES):
            return await call_next(request)

        host = normalise_host(request.headers.get("host"))

        if _is_platform_host(host):
            # Nothing resolved. This is not a fallback: no tenant has been
            # selected, and the request proceeds exactly as it did before this
            # phase existed.
            return await call_next(request)

        if not getattr(settings, "CUSTOM_DOMAINS_ENABLED", False):
            # A non-platform Host on a deployment with the feature off. There
            # is no table of verified domains to consult in any meaningful
            # sense, and guessing is not an option.
            logger.warning(
                "host_tenant.unknown_host_feature_disabled",
                extra={"host": host, "path": path},
            )
            return _refuse()

        resolved = self._resolve(host)
        if resolved is None:
            # Invariant 3. The only branch for an unmatched vanity host is a
            # refusal. There is deliberately no code path here that selects a
            # tenant when the lookup returned nothing.
            logger.warning(
                "host_tenant.unmatched_host",
                extra={"host": host, "path": path},
            )
            return _refuse()

        organization_id, custom_domain_id = resolved
        request.state.host_organization_id = organization_id
        request.state.host_custom_domain_id = custom_domain_id
        request.state.host_hostname = host

        return await call_next(request)

    @staticmethod
    def _resolve(host: str) -> Optional[tuple[uuid.UUID, uuid.UUID]]:
        """One query, one authority.

        The session is opened and closed here rather than taken from the
        request, because middleware runs outside FastAPI's dependency scope
        and there is no `get_db` to depend on. It is a short read on an index
        probe against `ix_custom_domains_verified_hostname`.

        Any exception resolves to None. A database blip must not become a
        request served as the wrong tenant, and "refuse on error" is the only
        safe direction for a control like this.
        """
        from app.db.session import SessionLocal
        from app.services.branding import domain_service

        try:
            with SessionLocal() as db:
                row = domain_service.resolve_verified_host(db, hostname=host)
                if row is None:
                    return None
                return row.organization_id, row.id
        except Exception:  # noqa: BLE001 - refuse rather than guess
            logger.exception(
                "host_tenant.resolution_failed", extra={"host": host}
            )
            return None


def _refuse() -> JSONResponse:
    """The one refusal shape.

    404, not 403, and no detail naming the hostname — see the module
    docstring on why confirming a hostname is known is itself the leak.
    """
    return JSONResponse(
        status_code=404,
        content={"detail": "Not Found"},
    )


def host_organization_id(request: Request) -> Optional[uuid.UUID]:
    """Read the resolved tenant, for handlers that need it.

    A function rather than direct attribute access so that every reader goes
    through one place, and so that `getattr` with a None default is written
    once instead of at each call site where one of them would eventually be
    written as `getattr(..., "host_organization_id", some_default)`.
    """
    return getattr(request.state, "host_organization_id", None)


__all__ = [
    "EXEMPT_PREFIXES",
    "HostTenantMiddleware",
    "host_organization_id",
    "normalise_host",
    "platform_hosts",
]