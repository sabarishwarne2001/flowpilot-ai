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
    #
    # POST, not GET. The invitation token is a bearer secret, so it travels
    # in the request body like every other token-bearing public route below
    # (reset-password, verify-email, email-change/confirm). The GET form this
    # replaced took the token as `?token=...`, which undid ARCH-04 B.10 --
    # the fragment in the accept link keeps the token out of server logs
    # right up until the page makes its first API call.
    PublicRoute(
        path="/api/v1/invitations/preview",
        methods=("POST",),
        phase="ARCH-04",
        credential="invitation token (request body)",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    # ARCH-25 on-demand TLS authorization (Caddy `ask`)
    #
    # Unauthenticated because Caddy has no credential to present: it resolves
    # this BEFORE terminating TLS, so there is no session, token or
    # certificate in existence yet.
    #
    # DEPLOYMENT CONSTRAINT, and it is load-bearing: Caddy reaches this as
    # `http://web:8000/...` on the compose network. It must NOT be published
    # through the ingress. Adding a public route for `/api/v1/internal/*` in
    # a Caddyfile site block would expose an unauthenticated endpoint that
    # enumerates which hostnames are verified tenant domains.
    #
    # The handler mutates nothing and performs one indexed read. A refusal is
    # the safe direction -- Caddy declines to issue and retries on its own
    # interval.
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
    # ARCH-25 — the host-resolved branding surface.
    PublicRoute(
        path="/api/v1/branding/manifest",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — tenant resolved from a verified Host, response carries no identifiers",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/branding/logo",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — tenant resolved from a verified Host, returns image bytes or 404",
        rate_limit_policy="POLICY_PUBLIC_READ",
    ),
    PublicRoute(
        path="/api/v1/branding/favicon",
        methods=("GET",),
        phase="ARCH-25",
        credential="none — tenant resolved from a verified Host, returns image bytes or 404",
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
