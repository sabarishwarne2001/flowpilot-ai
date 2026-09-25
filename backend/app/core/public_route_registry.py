"""S6 — ONE registry for routes that bypass authentication and rate limiting."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PublicRoute:
    path: str
    methods: tuple[str, ...]
    phase: str
    credential: str
    rate_limit_policy: str
    prefix_match: bool = False

    def matches(self, path: str, method: str) -> bool:
        if method.upper() not in self.methods and "*" not in self.methods:
            return False
        if self.prefix_match:
            return path.startswith(self.path)
        if self.path == path:
            return True
        if "{" in self.path:
            pattern = "^" + re.sub(r"\{[^}]+\}", r"[^/]+", self.path) + "$"
            return bool(re.match(pattern, path))
        return False


PUBLIC_ROUTES: tuple[PublicRoute, ...] = (
    # Health
    PublicRoute(
        path="/api/v1/health",
        methods=("GET",),
        phase="CORE",
        credential="none",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # Invitations
    PublicRoute(
        path="/api/v1/invitations/preview",
        methods=("POST",),
        phase="ARCH-04",
        credential="invitation token (request body)",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # ARCH-25 on-demand TLS authorization (Caddy `ask`)
    PublicRoute(
        path="/api/v1/internal/tls/authorize",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — internal network only, not ingress-published",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # Auth endpoints
    PublicRoute(
        path="/api/v1/auth/login",
        methods=("POST",),
        phase="ARCH-03",
        credential="email and password",
        rate_limit_policy="POLICY_LOGIN_IP",
    ),
    PublicRoute(
        path="/api/v1/auth/register",
        methods=("POST",),
        phase="ARCH-03",
        credential="none",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/forgot-password",
        methods=("POST",),
        phase="ARCH-03",
        credential="none",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/reset-password",
        methods=("POST",),
        phase="ARCH-03",
        credential="password-reset token",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/verify-email",
        methods=("POST",),
        phase="ARCH-03",
        credential="email-verification token",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/refresh",
        methods=("POST",),
        phase="ARCH-03",
        credential="httpOnly refresh cookie",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/logout",
        methods=("POST",),
        phase="ARCH-03",
        credential="authenticated session or refresh cookie",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/auth/email-change/confirm",
        methods=("POST",),
        phase="ARCH-06",
        credential="email-change token",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # Billing webhooks (Stripe & Multi-Gateway / Dodo)
    PublicRoute(
        path="/api/v1/billing/webhooks/stripe",
        methods=("POST",),
        phase="ARCH-15",
        credential="Stripe-Signature HMAC over the raw body",
        rate_limit_policy="POLICY_WEBHOOK_INBOUND",
    ),
    PublicRoute(
        path="/api/v1/billing/stripe/webhook",
        methods=("POST",),
        phase="ARCH-15",
        credential="Stripe-Signature HMAC over the raw body",
        rate_limit_policy="POLICY_WEBHOOK_INBOUND",
    ),
    PublicRoute(
        path="/api/v1/billing/webhooks/{gateway}",
        methods=("POST",),
        phase="ARCH-29",
        credential="Gateway webhook signature (Standard Webhooks HMAC-SHA256 or Stripe HMAC)",
        rate_limit_policy="POLICY_WEBHOOK_INBOUND",
    ),
    # SAML / SSO
    PublicRoute(
        path="/api/v1/saml/metadata",
        methods=("GET",),
        phase="ARCH-16",
        credential="none — SP metadata is public by specification",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/saml/acs",
        methods=("POST",),
        phase="ARCH-16",
        credential="XML signature over the assertion",
        rate_limit_policy="POLICY_SSO_ACS",
    ),
    PublicRoute(
        path="/api/v1/saml/slo",
        methods=("POST", "GET"),
        phase="ARCH-16",
        credential="LogoutRequest; effect limited to revoking sessions",
        rate_limit_policy="POLICY_SSO_ACS",
    ),
    PublicRoute(
        path="/api/v1/sso/discover",
        methods=("GET",),
        phase="ARCH-16",
        credential="none — keyed on DOMAIN",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/sso/start",
        methods=("GET",),
        phase="ARCH-16",
        credential="none",
        rate_limit_policy="POLICY_SSO_ACS",
    ),
    PublicRoute(
        path="/api/v1/oidc/callback",
        methods=("GET",),
        phase="ARCH-16",
        credential="authorization code + PKCE verifier + server-side nonce",
        rate_limit_policy="POLICY_SSO_ACS",
    ),
    # SCIM
    PublicRoute(
        path="/scim/v2",
        methods=("*",),
        phase="ARCH-16",
        credential="organization-owned SCIM bearer token",
        rate_limit_policy="POLICY_SCIM",
        prefix_match=True,
    ),
    # ARCH-25 branding surface
    PublicRoute(
        path="/api/v1/branding/manifest",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — tenant resolved from a verified Host",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/branding/logo",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — returns image bytes or 404",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # ARCH43-S1:public-document-requests. The recipient of a missing-document
    # request has no account: the credential is the single-use token in the
    # path (stored only as SHA-256; consumed by one conditional UPDATE).
    PublicRoute(
        path="/api/v1/public/document-requests/{token}",
        methods=("GET", "POST"),
        phase="ARCH-43",
        credential="single-use expiring document-request token (path)",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # ARCH46-S1:public-calendar-feeds. A calendar app polling a member's
    # obligations feed has no session: the credential is the signed token in
    # the path (HMAC-verified, stored only as SHA-256, revocable; every refusal
    # is the same 404).
    PublicRoute(
        path="/api/v1/public/calendar-feeds/{token}.ics",
        methods=("GET",),
        phase="ARCH-46",
        credential="signed, revocable calendar-feed token (path)",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/branding/favicon",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — returns image bytes or 404",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
)


def is_public(path: str, method: str) -> bool:
    return any(r.matches(path, method) for r in PUBLIC_ROUTES)


def policy_for(path: str, method: str) -> str | None:
    for route in PUBLIC_ROUTES:
        if route.matches(path, method):
            return route.rate_limit_policy
    return None


def registered_paths() -> set[str]:
    return {r.path for r in PUBLIC_ROUTES}


# ARCH46-S1:redact-path. A credential carried IN a public path (a calendar-feed
# token, an ARCH-43 document-request token) must never reach a log line. Every
# writer of a request path -- RequestTraceMiddleware, the exception handlers,
# and (through logging_config.RedactSecretPaths on the console handler) uvicorn's
# access log -- passes it through redact_path(); the ingress (deploy/Caddyfile)
# filters request>uri the same way. The whole segment after the prefix goes,
# including a suffix such as ".ics", so a malformed token is hidden too.
REDACTED_SEGMENT = "[redacted]"
SECRET_PATH_PREFIXES: tuple[str, ...] = tuple(sorted(
    {r.path.split("{token}", 1)[0] for r in PUBLIC_ROUTES if "{token}" in r.path}))
_SECRET_SEGMENT = re.compile(
    "(" + "|".join(re.escape(p) for p in SECRET_PATH_PREFIXES) + r")[^/?#\s\"']+") if SECRET_PATH_PREFIXES else None


def redact_path(value):  # type: ignore[no-untyped-def]
    """Any text that may hold a token-bearing public path, with the token replaced. Non-strings pass through."""
    if _SECRET_SEGMENT is None or not isinstance(value, str) or "/public/" not in value:
        return value
    return _SECRET_SEGMENT.sub(lambda m: m.group(1) + REDACTED_SEGMENT, value)
