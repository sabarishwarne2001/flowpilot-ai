"""S6 — ONE registry for routes that bypass authentication and rate limiting."""

from __future__ import annotations

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
        return path.startswith(self.path) if self.prefix_match else path == self.path


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
        methods=("GET",),
        phase="ARCH-04",
        credential="invitation token",
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
    # Billing webhooks (Stripe)
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
        credential="XML signature over the assertion, verified against a live idp_signing_certificates row",
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
        credential="none — keyed on DOMAIN, never on email, so it cannot enumerate accounts",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/sso/start",
        methods=("GET",),
        phase="ARCH-16",
        credential="none — issues an AuthnRequest for a public IdP redirect",
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
        credential="organization-owned SCIM bearer token (scim_api_keys)",
        rate_limit_policy="POLICY_SCIM",
        prefix_match=True,
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