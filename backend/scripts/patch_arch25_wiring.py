#!/usr/bin/env python
"""ARCH-25 — wire the phase into the ten shared files it extends.

    python scripts/patch_arch25_wiring.py
    python scripts/patch_arch25_wiring.py --check

WHY A PATCH SCRIPT AND NOT TEN REWRITTEN FILES
==============================================

Every file touched here is one that every phase touches: `config.py` (756
lines), `App.tsx`, `tenantPaths.ts`, `endpoints.ts`, `queryKeys.ts`,
`navigation.ts`, `router.py`, `profiles.py`, `handlers/__init__.py` and
`public_route_registry.py`. ARCH-25's contribution to each is an insertion of
between four and eighty lines.

Reproducing 756 lines to change 80 puts a transcription risk on the 676 that
were not supposed to move, and a reviewer reading the diff cannot tell an
intentional change from a typo. This is the ARCH-19 precedent, applied to the
same class of problem: anchored, idempotent, loud on a miss.

EVERY EDIT IS ANCHORED AND IDEMPOTENT
=====================================

Each edit locates an exact existing string and inserts relative to it. An
absent anchor, or an anchor that appears more than once, EXITS NON-ZERO and
writes nothing at all — including the edits that would have succeeded. Partial
wiring is worse than none: a registered job handler with no worker profile
stops the whole fleet booting, and a mounted router with no public-route entry
stops the app booting.

If the inserted text is already present the edit is skipped, so re-running
after a failure is safe.

WHAT THIS SCRIPT DOES NOT DO
============================

It does not create any new file. Every new module in ARCH-25 ships whole.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
FRONTEND = REPO / "frontend"

CONFIG = BACKEND / "app" / "core" / "config.py"
PUBLIC_ROUTES = BACKEND / "app" / "core" / "public_route_registry.py"
PROFILES = BACKEND / "app" / "workers" / "profiles.py"
HANDLERS = BACKEND / "app" / "workers" / "handlers" / "__init__.py"
ROUTER = BACKEND / "app" / "api" / "v1" / "router.py"
MAIN = BACKEND / "app" / "main.py"

ENDPOINTS_TS = FRONTEND / "src" / "services" / "api" / "endpoints.ts"
QUERY_KEYS_TS = FRONTEND / "src" / "services" / "api" / "queryKeys.ts"
TENANT_PATHS_TS = FRONTEND / "src" / "routes" / "tenantPaths.ts"
NAVIGATION_TS = FRONTEND / "src" / "components" / "layout" / "navigation.ts"
APP_TSX = FRONTEND / "src" / "App.tsx"


# ===========================================================================
# 1. app/core/config.py
# ===========================================================================

CONFIG_ANCHOR = "    S3_REGIONAL_BUCKETS: dict[str, str] = {}\n"

CONFIG_INSERT = '''
    # ======================================================================
    # ARCH-25 — white-label, custom domains and tenant branding.
    #
    # WHY THESE ARE DECLARED FIELDS AND NOT getattr() LOOKUPS
    #
    # `model_config` sets extra="ignore". An environment variable with no
    # matching field here is DISCARDED, not surfaced. So the
    # `getattr(settings, "NAME", default)` idiom does not read configuration
    # at all — it returns the literal default, always, on every deployment.
    #
    # ARCH-16 has been affected by exactly this since it shipped:
    # dns_service reads DNS_RESOLVERS, DNS_TIMEOUT_S and
    # DOMAIN_VERIFICATION_TXT_PREFIX through getattr, none of which was ever
    # declared, so setting them in .env has never had any effect and domain
    # verification has always resolved against 1.1.1.1 and 8.8.8.8.
    #
    # The four ARCH-16 fields below are declared here at their existing
    # getattr defaults. Behaviour is unchanged on every current deployment;
    # what changes is that the env vars now work.
    # ======================================================================

    # ---- DNS resolution (shared with ARCH-16 identity domain checks) ----
    DNS_RESOLVERS: str = "1.1.1.1,8.8.8.8"
    DNS_TIMEOUT_S: float = 5.0
    DOMAIN_VERIFICATION_TXT_PREFIX: str = "flowpilot-site-verification"
    DOMAIN_VERIFICATION_TOKEN_TTL_DAYS: int = 30

    # ---- Custom domains ------------------------------------------------
    #
    # OFF by default. A deployment that has not configured an ACME agent and
    # a wildcard-capable ingress cannot serve a vanity host, and letting a
    # tenant claim one on such a deployment produces a VERIFIED domain that
    # 404s forever.
    CUSTOM_DOMAINS_ENABLED: bool = False

    # Hostnames a tenant may never claim, JSON list, matched case-insensitively
    # against the hostname AND against every parent suffix of it. This is what
    # stops a tenant claiming the platform's own origin and having every
    # session cookie in the estate delivered to a page they control.
    #
    #   PLATFORM_RESERVED_HOSTS='["flowpilot.ai","app.flowpilot.ai"]'
    #
    # Empty is a DEPLOYMENT ERROR, not a permissive default: domain_service
    # refuses every claim while this is empty rather than allowing all of
    # them. An unset allowlist that means "allow everything" is the failure
    # mode this phase exists to avoid.
    PLATFORM_RESERVED_HOSTS: list[str] = []

    # How long a published TXT challenge stays valid. A challenge that never
    # expired would let a token published years ago verify a hostname the
    # tenant has since let lapse.
    CUSTOM_DOMAIN_CHALLENGE_TTL_HOURS: int = 168
    CUSTOM_DOMAIN_MAX_PER_ORG: int = 10
    CUSTOM_DOMAIN_VERIFY_INTERVAL_MINUTES: int = 30
    # After this many consecutive misses a PENDING domain moves to FAILED.
    # Resolver failures do NOT count toward it — that is our outage, not the
    # tenant's, and charging it to them produces a console that blames the
    # customer for our DNS.
    CUSTOM_DOMAIN_MAX_VERIFY_FAILURES: int = 20

    # ---- TLS / ACME ----------------------------------------------------
    #
    # The agent is a local Caddy admin API or an equivalent sidecar. Empty
    # means no issuance is attempted and request_certificate refuses rather
    # than silently marking a domain as covered.
    ACME_AGENT_URL: str = ""
    ACME_AGENT_TOKEN: SecretStr | None = None
    ACME_DIRECTORY_URL: str = "https://acme-v02.api.letsencrypt.org/directory"
    ACME_CONTACT_EMAIL: str = ""
    ACME_REQUEST_TIMEOUT_S: float = 20.0

    # Renew this many days before expiry. Let's Encrypt issues for 90 days;
    # 30 leaves two full retry windows before anything is user-visible.
    TLS_RENEWAL_WINDOW_DAYS: int = 30
    # The dead-man threshold. A certificate this close to expiry that has not
    # renewed is an alert, not a retry — expiry on a customer's vanity domain
    # is a total outage for that tenant with no error in our logs.
    TLS_DEAD_MAN_DAYS: int = 7
    TLS_RENEWAL_SWEEP_INTERVAL_MINUTES: int = 60

    # ---- Branding ------------------------------------------------------
    BRANDING_MANIFEST_CACHE_SECONDS: int = 60
    BRANDING_MAX_LOGO_BYTES: int = 2 * 1024 * 1024
    BRANDING_MAX_FAVICON_BYTES: int = 512 * 1024
    BRANDING_MAX_IMAGE_DIMENSION: int = 2048
'''

CONFIG_MARKER = "PLATFORM_RESERVED_HOSTS: list[str] = []"


# ===========================================================================
# 2. app/core/public_route_registry.py
# ===========================================================================

PUBLIC_ROUTES_ANCHOR = '''        rate_limit_policy="POLICY_SCIM",
        prefix_match=True,
    ),
)
'''

PUBLIC_ROUTES_INSERT = '''        rate_limit_policy="POLICY_SCIM",
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
'''

PUBLIC_ROUTES_MARKER = '"/api/v1/branding/manifest"'


# ===========================================================================
# 3. app/workers/profiles.py
# ===========================================================================

PROFILES_ANCHOR = '            "identity.sweep_auth_requests",\n'

PROFILES_INSERT = '''            "identity.sweep_auth_requests",
            # ARCH-25 white-label. A DNS TXT lookup and an HTTP call to the
            # local ACME agent — no heavy imports, so they belong on the thin
            # image with the rest of the housekeeping types.
            #
            # This entry is not optional bookkeeping.
            # assert_imports_match_profile() runs uncovered_job_types() at
            # EVERY worker's startup and raises ProfileError on a handler no
            # profile claims. Registering these two in handlers/__init__.py
            # without adding them here stops the entire fleet booting — the
            # same defect ARCH-16 shipped and had to remediate, recorded in
            # the comment directly above.
            "domain.verify_dns",
            "tls.renew_sweep",
'''

PROFILES_MARKER = '"domain.verify_dns",'


# ===========================================================================
# 4. app/workers/handlers/__init__.py  (three edits)
# ===========================================================================

HANDLERS_TYPES_ANCHOR = '''ARCH16_JOB_TYPES: frozenset[str] = frozenset(
    {
        "identity.recheck_domains",
        "identity.purge_assertion_payloads",
        "identity.sweep_replay_guard",
        "identity.sweep_auth_requests",
    }
)
'''

HANDLERS_TYPES_INSERT = HANDLERS_TYPES_ANCHOR + '''ARCH25_JOB_TYPES: frozenset[str] = frozenset(
    {"domain.verify_dns", "tls.renew_sweep"}
)
'''

HANDLERS_TYPES_MARKER = "ARCH25_JOB_TYPES"

HANDLERS_FUNCS_ANCHOR = '''def _identity_sweep_auth_requests(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.identity_jobs import handle_sweep_auth_requests
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_sweep_auth_requests(db, payload)
'''

HANDLERS_FUNCS_INSERT = HANDLERS_FUNCS_ANCHOR + '''

def _domain_verify_dns(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.branding import handle_domain_verify_dns
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_domain_verify_dns(db, payload)


def _tls_renew_sweep(payload: dict[str, Any]) -> dict[str, Any]:
    from app.workers.handlers.branding import handle_tls_renew_sweep
    from app.db.session import SessionLocal
    with SessionLocal() as db:
        return handle_tls_renew_sweep(db, payload)
'''

HANDLERS_FUNCS_MARKER = "def _domain_verify_dns("

HANDLERS_MAP_ANCHOR = '    "identity.sweep_auth_requests": _identity_sweep_auth_requests,\n}'

HANDLERS_MAP_INSERT = '''    "identity.sweep_auth_requests": _identity_sweep_auth_requests,
    # ARCH-25. Both are also listed on the LIGHT profile in
    # app/workers/profiles.py; a handler here with no profile there is a job
    # that enqueues cleanly and never runs.
    "domain.verify_dns": _domain_verify_dns,
    "tls.renew_sweep": _tls_renew_sweep,
}'''

HANDLERS_MAP_MARKER = '"domain.verify_dns": _domain_verify_dns,'

HANDLERS_ALL_ANCHOR = '''    "ARCH16_JOB_TYPES",
    "register_all",
]'''

HANDLERS_ALL_INSERT = '''    "ARCH16_JOB_TYPES",
    "ARCH25_JOB_TYPES",
    "register_all",
]'''

HANDLERS_ALL_MARKER = '    "ARCH25_JOB_TYPES",\n'


# ===========================================================================
# 5. app/api/v1/router.py  (two edits)
# ===========================================================================

ROUTER_IMPORT_ANCHOR = '''    compliance,
    dashboard,
'''

ROUTER_IMPORT_INSERT = '''    compliance,
    custom_domains,
    dashboard,
'''

ROUTER_IMPORT_MARKER = "    custom_domains,\n"

ROUTER_IMPORT2_ANCHOR = '''    slos,
    upload,
'''

ROUTER_IMPORT2_INSERT = '''    slos,
    tenant_branding,
    upload,
'''

ROUTER_IMPORT2_MARKER = "    tenant_branding,\n"

ROUTER_MOUNT_ANCHOR = (
    "api_router.include_router(byok.router)  "
    "# ARCH-22 Enterprise BYOK & Model Routing\n"
)

ROUTER_MOUNT_INSERT = (
    "api_router.include_router(byok.router)  "
    "# ARCH-22 Enterprise BYOK & Model Routing\n"
    "\n"
    "# ARCH-25 White-label. Two routers rather than one because the role\n"
    "# boundary differs: every domain write is OWNER-gated (a vanity hostname\n"
    "# is an authentication-adjacent control), while branding writes are\n"
    "# ADMIN-gated. Mounting them together would invite one shared dependency.\n"
    "#\n"
    "# tenant_branding.public_router carries the ONE unauthenticated route in\n"
    "# this phase and is mounted separately so that adding an endpoint to it\n"
    "# is a visible act rather than an accident of file position.\n"
    "api_router.include_router(custom_domains.router)\n"
    "api_router.include_router(tenant_branding.router)\n"
    "api_router.include_router(tenant_branding.public_router)\n"
)

ROUTER_MOUNT_MARKER = "api_router.include_router(custom_domains.router)"


# ===========================================================================
# 6. frontend/src/services/api/endpoints.ts
# ===========================================================================

ENDPOINTS_ANCHOR = '''  savings: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/byok/savings`,
} as const;
'''

ENDPOINTS_INSERT = ENDPOINTS_ANCHOR + '''
/**
 * ARCH-25 — white-label, custom domains and tenant branding.
 *
 * `manifest` is the odd one out and deliberately so: it takes no organization
 * id because the tenant is resolved server-side from the Host header. Passing
 * an id would make it an endpoint that answers "what does organization X look
 * like" to an unauthenticated caller, which is exactly what it must not be.
 */
export const BRANDING_ENDPOINTS = {
  domains: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains`,
  domain: (organizationId: string, domainId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains/${seg(domainId)}`,
  verifyDomain: (organizationId: string, domainId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains/${seg(domainId)}/verify`,
  reissueChallenge: (organizationId: string, domainId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains/${seg(domainId)}/challenge`,
  primaryDomain: (organizationId: string, domainId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains/${seg(domainId)}/primary`,
  certificate: (organizationId: string, domainId: string): string =>
    `/organizations/${org(organizationId)}/custom-domains/${seg(domainId)}/certificate`,
  branding: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/branding`,
  logo: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/branding/logo`,
  favicon: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/branding/favicon`,
  senderDomain: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/branding/sender-domain`,
  verifySenderDomain: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/branding/sender-domain/verify`,
  manifest: "/branding/manifest",
} as const;
'''

ENDPOINTS_MARKER = "export const BRANDING_ENDPOINTS"


# ===========================================================================
# 7. frontend/src/services/api/queryKeys.ts
# ===========================================================================

QUERY_KEYS_ANCHOR = '''  savings: (organizationId: string, windowDays: number) =>
    [...byokKeys.all(organizationId), "savings", windowDays] as const,
};
'''

QUERY_KEYS_INSERT = QUERY_KEYS_ANCHOR + '''
export const brandingKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "branding"] as const,
  domains: (organizationId: string) =>
    [...brandingKeys.all(organizationId), "domains"] as const,
  domain: (organizationId: string, domainId: string) =>
    [...brandingKeys.domains(organizationId), domainId] as const,
  branding: (organizationId: string) =>
    [...brandingKeys.all(organizationId), "tokens"] as const,
  sender: (organizationId: string) =>
    [...brandingKeys.all(organizationId), "sender"] as const,
  // Not organization-scoped, because the manifest is not addressed by
  // organization: it is resolved from the Host header. Scoping it would
  // suggest a per-tenant cache key the request does not actually have.
  manifest: ["branding", "manifest"] as const,
};
'''

QUERY_KEYS_MARKER = "export const brandingKeys"


# ===========================================================================
# 8. frontend/src/routes/tenantPaths.ts  (three edits)
# ===========================================================================

PATHS_PATTERN_ANCHOR = '''  organizationBYOK: "byok",
'''

PATHS_PATTERN_INSERT = '''  organizationBYOK: "byok",
  // ARCH-25. Same reasoning as compliance, developer and byok:
  // "organizations" is already in RESERVED_ROUTE_SEGMENTS, so
  // /organizations/:orgSlug/branding cannot be misread by parseTenantPath as
  // a workspace route. No new reserved segment is needed.
  organizationBranding: "branding",
'''

PATHS_PATTERN_MARKER = 'organizationBranding: "branding"'

PATHS_BUILDER_ANCHOR = '''export const organizationBYOKPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/byok`;
'''

PATHS_BUILDER_INSERT = PATHS_BUILDER_ANCHOR + '''
export const organizationBrandingPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/branding`;
'''

PATHS_BUILDER_MARKER = "export const organizationBrandingPath"

PATHS_SELFCHECK_ANCHOR = '''  expect(
    "the BYOK console is an organization route, not a tenant route",
    organizationBYOKPath("acme") === "/organizations/acme/byok" &&
      parseTenantPath("/organizations/acme/byok") === null,
  );
'''

PATHS_SELFCHECK_INSERT = PATHS_SELFCHECK_ANCHOR + '''  expect(
    "the branding console is an organization route, not a tenant route",
    organizationBrandingPath("acme") === "/organizations/acme/branding" &&
      parseTenantPath("/organizations/acme/branding") === null,
  );
'''

PATHS_SELFCHECK_MARKER = "the branding console is an organization route"


# ===========================================================================
# 9. frontend/src/components/layout/navigation.ts  (three edits)
# ===========================================================================

NAV_ICON_ANCHOR = '''  Gauge,
  TerminalSquare,
'''

NAV_ICON_INSERT = '''  Gauge,
  Palette,
  TerminalSquare,
'''

NAV_ICON_MARKER = "  Palette,\n"

NAV_IMPORT_ANCHOR = '''  organizationBillingPath,
  organizationBYOKPath,
'''

NAV_IMPORT_INSERT = '''  organizationBillingPath,
  organizationBrandingPath,
  organizationBYOKPath,
'''

NAV_IMPORT_MARKER = "  organizationBrandingPath,\n"

NAV_ITEM_ANCHOR = '''    items.push({
      name: "Enterprise BYOK & models",
      path: organizationBYOKPath(orgSlug),
      icon: KeySquare,
    });
'''

NAV_ITEM_INSERT = NAV_ITEM_ANCHOR + '''    // ARCH-25. ADMIN sees the console because visual branding is an
    // administrator's job. Every DOMAIN operation behind it is OWNER-gated by
    // RequireOrgOwner on the route: a vanity hostname resolves to a tenant,
    // which makes claiming one authentication-adjacent rather than cosmetic.
    // Hiding the link is not what protects the domain endpoints.
    items.push({
      name: "Branding & custom domains",
      path: organizationBrandingPath(orgSlug),
      icon: Palette,
    });
'''

NAV_ITEM_MARKER = '"Branding & custom domains"'


# ===========================================================================
# 10. frontend/src/App.tsx  (two edits)
# ===========================================================================

APP_LAZY_ANCHOR = '''const OrganizationBYOK = lazy(
  () => import("@/pages/organization/OrganizationBYOK"),
);
'''

APP_LAZY_INSERT = APP_LAZY_ANCHOR + '''// ARCH-25. Lazy like every other organization surface. The branding console
// pulls in a colour-picker preview and an image uploader that no other page
// needs, so keeping it out of the main chunk matters more here than most.
const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),
);
'''

APP_LAZY_MARKER = "const OrganizationBranding = lazy("

APP_ROUTE_ANCHOR = '''                    <Route
                      path={ROUTE_PATTERNS.organizationBYOK}
                      element={<OrganizationBYOK />}
                    />
'''

APP_ROUTE_INSERT = APP_ROUTE_ANCHOR + '''                    <Route
                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}
                    />
'''

APP_ROUTE_MARKER = "ROUTE_PATTERNS.organizationBranding"


# ===========================================================================
# 11. app/main.py  (two edits)
# ===========================================================================

MAIN_IMPORT_ANCHOR = (
    "from app.middleware.global_rate_limit import GlobalRateLimitMiddleware\n"
)

MAIN_IMPORT_INSERT = (
    "from app.middleware.global_rate_limit import GlobalRateLimitMiddleware\n"
    "from app.middleware.host_tenant import HostTenantMiddleware\n"
)

MAIN_IMPORT_MARKER = "from app.middleware.host_tenant import HostTenantMiddleware"

MAIN_MIDDLEWARE_ANCHOR = "app.add_middleware(GlobalRateLimitMiddleware)\n"

MAIN_MIDDLEWARE_INSERT = (
    "# ARCH-25 host resolution. Registered FIRST, which in Starlette makes it\n"
    "# the INNERMOST of the four: the effective order is RequestTrace ->\n"
    "# PublicApiRateLimit -> GlobalRateLimit -> HostTenant -> app.\n"
    "#\n"
    "# That ordering is deliberate in both directions. Host resolution runs\n"
    "# INSIDE the rate limiters so that sweeping the vanity namespace to\n"
    "# discover which hostnames belong to FlowPilot customers is rate-limited\n"
    "# like any other probe. It runs INSIDE RequestTrace so that every refusal\n"
    "# carries a request id and lands in the same log stream as everything\n"
    "# else, because an unmatched Host is the first thing anyone will look for\n"
    "# when a tenant reports their vanity domain returning 404.\n"
    "app.add_middleware(HostTenantMiddleware)\n"
    "app.add_middleware(GlobalRateLimitMiddleware)\n"
)

MAIN_MIDDLEWARE_MARKER = "app.add_middleware(HostTenantMiddleware)"


# ---------------------------------------------------------------------------
# Edit table
# ---------------------------------------------------------------------------

Edit = tuple[pathlib.Path, str, str, str, str]

EDITS: tuple[Edit, ...] = (
    (
        CONFIG,
        "config.py declares the ARCH-25 settings (and fixes ARCH-16's undeclared DNS fields)",
        CONFIG_ANCHOR,
        CONFIG_ANCHOR + CONFIG_INSERT,
        CONFIG_MARKER,
    ),
    (
        PUBLIC_ROUTES,
        "public_route_registry registers GET /api/v1/branding/manifest",
        PUBLIC_ROUTES_ANCHOR,
        PUBLIC_ROUTES_INSERT,
        PUBLIC_ROUTES_MARKER,
    ),
    (
        PROFILES,
        "LIGHT profile claims domain.verify_dns and tls.renew_sweep",
        PROFILES_ANCHOR,
        PROFILES_INSERT,
        PROFILES_MARKER,
    ),
    (
        HANDLERS,
        "handlers/__init__ declares ARCH25_JOB_TYPES",
        HANDLERS_TYPES_ANCHOR,
        HANDLERS_TYPES_INSERT,
        HANDLERS_TYPES_MARKER,
    ),
    (
        HANDLERS,
        "handlers/__init__ defines the two ARCH-25 delegates",
        HANDLERS_FUNCS_ANCHOR,
        HANDLERS_FUNCS_INSERT,
        HANDLERS_FUNCS_MARKER,
    ),
    (
        HANDLERS,
        "handlers/__init__ routes the two ARCH-25 job types",
        HANDLERS_MAP_ANCHOR,
        HANDLERS_MAP_INSERT,
        HANDLERS_MAP_MARKER,
    ),
    (
        HANDLERS,
        "handlers/__init__ exports ARCH25_JOB_TYPES",
        HANDLERS_ALL_ANCHOR,
        HANDLERS_ALL_INSERT,
        HANDLERS_ALL_MARKER,
    ),
    (
        ROUTER,
        "router.py imports custom_domains",
        ROUTER_IMPORT_ANCHOR,
        ROUTER_IMPORT_INSERT,
        ROUTER_IMPORT_MARKER,
    ),
    (
        ROUTER,
        "router.py imports tenant_branding",
        ROUTER_IMPORT2_ANCHOR,
        ROUTER_IMPORT2_INSERT,
        ROUTER_IMPORT2_MARKER,
    ),
    (
        ROUTER,
        "router.py mounts the ARCH-25 routers",
        ROUTER_MOUNT_ANCHOR,
        ROUTER_MOUNT_INSERT,
        ROUTER_MOUNT_MARKER,
    ),
    (
        MAIN,
        "main.py imports HostTenantMiddleware",
        MAIN_IMPORT_ANCHOR,
        MAIN_IMPORT_INSERT,
        MAIN_IMPORT_MARKER,
    ),
    (
        MAIN,
        "main.py installs HostTenantMiddleware innermost",
        MAIN_MIDDLEWARE_ANCHOR,
        MAIN_MIDDLEWARE_INSERT,
        MAIN_MIDDLEWARE_MARKER,
    ),
    (
        ENDPOINTS_TS,
        "endpoints.ts declares BRANDING_ENDPOINTS",
        ENDPOINTS_ANCHOR,
        ENDPOINTS_INSERT,
        ENDPOINTS_MARKER,
    ),
    (
        QUERY_KEYS_TS,
        "queryKeys.ts declares brandingKeys",
        QUERY_KEYS_ANCHOR,
        QUERY_KEYS_INSERT,
        QUERY_KEYS_MARKER,
    ),
    (
        TENANT_PATHS_TS,
        "tenantPaths.ts adds the organizationBranding pattern",
        PATHS_PATTERN_ANCHOR,
        PATHS_PATTERN_INSERT,
        PATHS_PATTERN_MARKER,
    ),
    (
        TENANT_PATHS_TS,
        "tenantPaths.ts adds organizationBrandingPath",
        PATHS_BUILDER_ANCHOR,
        PATHS_BUILDER_INSERT,
        PATHS_BUILDER_MARKER,
    ),
    (
        TENANT_PATHS_TS,
        "tenantPaths.ts self-check covers the branding route",
        PATHS_SELFCHECK_ANCHOR,
        PATHS_SELFCHECK_INSERT,
        PATHS_SELFCHECK_MARKER,
    ),
    (
        NAVIGATION_TS,
        "navigation.ts imports the Palette icon",
        NAV_ICON_ANCHOR,
        NAV_ICON_INSERT,
        NAV_ICON_MARKER,
    ),
    (
        NAVIGATION_TS,
        "navigation.ts imports organizationBrandingPath",
        NAV_IMPORT_ANCHOR,
        NAV_IMPORT_INSERT,
        NAV_IMPORT_MARKER,
    ),
    (
        NAVIGATION_TS,
        "navigation.ts adds the Branding & custom domains link",
        NAV_ITEM_ANCHOR,
        NAV_ITEM_INSERT,
        NAV_ITEM_MARKER,
    ),
    (
        APP_TSX,
        "App.tsx lazy-imports OrganizationBranding",
        APP_LAZY_ANCHOR,
        APP_LAZY_INSERT,
        APP_LAZY_MARKER,
    ),
    (
        APP_TSX,
        "App.tsx registers the branding route",
        APP_ROUTE_ANCHOR,
        APP_ROUTE_INSERT,
        APP_ROUTE_MARKER,
    ),
)


def _read(path: pathlib.Path) -> str:
    """utf-8-sig: app/schemas/usage.py still carries a BOM upstream."""
    return path.read_text(encoding="utf-8-sig")


def apply(check_only: bool) -> int:
    applied = 0
    skipped = 0
    contents: dict[pathlib.Path, str] = {}

    for path, label, anchor, replacement, marker in EDITS:
        if not path.exists():
            print(f"[FAIL] {label}: {path} does not exist")
            return 1

        source = contents.get(path)
        if source is None:
            source = _read(path)

        if marker in source:
            print(f"[skip] {label} — already present")
            skipped += 1
            contents[path] = source
            continue

        occurrences = source.count(anchor)
        if occurrences != 1:
            print(
                f"[FAIL] {label}: anchor found {occurrences} times in "
                f"{path.name}, expected exactly 1. NOTHING was written — "
                "not even the edits that would have succeeded."
            )
            print(f"       anchor begins: {anchor.strip().splitlines()[0][:74]!r}")
            return 1

        contents[path] = source.replace(anchor, replacement, 1)
        print(f"[ ok ] {label}")
        applied += 1

    if check_only:
        print(
            f"\n--check: {applied} edit(s) would be applied, "
            f"{skipped} already present."
        )
        return 1 if applied else 0

    for path, text in contents.items():
        path.write_text(text, encoding="utf-8", newline="\n")

    print(f"\n{applied} edit(s) applied, {skipped} already present.")
    if applied:
        print(
            "\nRun scripts/verify_arch25.py next. G4 asserts the job types are "
            "on a profile, G13 asserts the manifest route is registered."
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-25 wiring patch")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Report what would change and exit non-zero if anything would.",
    )
    args = parser.parse_args()

    print("=" * 72)
    print("ARCH-25 — wiring into shared files")
    print("=" * 72)
    return apply(args.check)


if __name__ == "__main__":
    raise SystemExit(main())