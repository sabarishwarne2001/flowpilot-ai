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
    # ARCH-25 — the host-resolved branding surface.
    #
    # Unauthenticated by necessity: a visitor landing on ai.acme.com sees the
    # login page BEFORE they have a session, and theming that page is most of
    # what the tenant bought. `assert_public_route_registry` refuses to start
    # the app with an unauthenticated route missing from this tuple, so these
    # entries are load-bearing rather than documentation.
    #
    # None of the three takes a parameter. The tenant is resolved solely by
    # HostTenantMiddleware's exact match against a VERIFIED custom domain, and
    # an unmatched Host never reaches the handler at all.
    #
    # WHY THERE ARE THREE AND NOT ONE
    #
    # The Phase 2 audit proposed a single manifest route. That was wrong, and
    # the reason is worth recording. A manifest is useless without the logo it
    # references, and the logo has to be fetchable by the same unauthenticated
    # visitor. The two alternatives were both worse:
    #
    #   * a presigned storage URL in the manifest would expose the object key,
    #     which is `{organization_id}/logos/...` — leaking the tenant id the
    #     manifest exists to withhold;
    #   * a base64 data URI would put two megabytes into a JSON body served on
    #     every cold load.
    #
    # So the asset bytes get their own routes. They return image/png or 404
    # and nothing else: no filename, no id, no headers naming a tenant.
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
