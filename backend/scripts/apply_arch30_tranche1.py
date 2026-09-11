"""ARCH-30 Tranche 1 — anchored patch script for MODIFIED files.

    python scripts/apply_arch30_tranche1.py --check
    python scripts/apply_arch30_tranche1.py

Closes four blocking findings from the ARCH-30 ground-truth audit:

    T4-F1  no quota tier could be published (entitlement vocabulary)
    T4-F3  SCIM unreachable through the ingress; console shows no base URL
    T4-F4  first federated login loops back to /login
    T4-F5  vanity-domain branding cannot resolve; manifest had no consumer;
           a second, localhost-defaulting API base in the stream client

T4-F2 (Dodo dispatch) is Tranche 2 and is not touched here.

ARCH-19 / ARCH-29 precedent, and the three properties that come with it:

  IDEMPOTENT   — each file's edit set declares a sentinel that exists only in
                 the patched form. Present means SKIP, never double-apply.
  FAILS LOUDLY — every anchor declares how many times it must occur, and a
                 count mismatch aborts with the file and the first line of the
                 anchor. Two of the SAML anchors legitimately occur twice (the
                 ACS and OIDC handlers ended identically), so "exactly once"
                 would have been the wrong rule; "exactly N, declared" is not.
  ATOMIC       — every file is validated before ANY file is written. An abort
                 leaves the tree untouched.

Two refinements over the ARCH-29 script:

  * Byte-order marks are PRESERVED. `client.ts`, `routes.ts`, `branding.ts`
    and the Caddyfile carry one; the ARCH-29 script read with `utf-8-sig` and
    wrote plain `utf-8`, silently stripping it, which shows up as a one-byte
    diff on files nobody meant to change.
  * The sentinel is asserted present AFTER the edits are applied in memory. An
    edit set whose replacement text forgot its own sentinel would otherwise
    re-apply on every run and duplicate code.

Delivered WHOLE, not touched here:

    backend/app/core/entitlements.py                      (new)
    backend/scripts/verify_arch30_tranche1.py             (new)
    backend/scripts/mutate_arch30_tranche1.py             (new)
    frontend/src/pages/Auth/SsoComplete.tsx               (new)
    frontend/src/hooks/usePublicBrandingManifest.ts       (new)
    frontend/src/services/api/sso.ts                      (new)
    frontend/src/types/sso.ts                             (new)
    frontend/src/pages/Auth/Login.tsx                     (replacement)
    frontend/src/components/branding/Brand.tsx            (replacement)
    frontend/src/layouts/AuthLayout.tsx                   (replacement)
    frontend/vite.config.ts                               (replacement)

Copy those into place BEFORE running this script: the patched App.tsx imports
SsoComplete, and the patched quota_service imports app.core.entitlements.

Run `scripts/verify_arch30_tranche1.py` afterwards, then the mutation suite.
This script applying cleanly is not evidence that the result is correct.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FE = REPO / "frontend" / "src"

BOM = b"\xef\xbb\xbf"

# Each entry: (path, sentinel, [(old, new, expected_occurrences), ...])
EDITS: list[tuple[pathlib.Path, str, list[tuple[str, str, int]]]] = []


# =============================================================================
# T4-F1 — quota_service._validate learns the entitlement vocabulary
# =============================================================================
EDITS.append((
    ROOT / "app" / "services" / "quota_service.py",
    "entitlements.is_entitlement_key(spec.limit_key)",
    [
        (
            "from app.models.organization import Organization\n",
            "from app.core import entitlements\n"
            "from app.models.organization import Organization\n",
            1,
        ),
        (
            '''    for spec in entries:
        if not is_limit_key(spec.limit_key):
            raise QuotaTierValidationError(
                f"'{spec.limit_key}' is neither the wildcard '{TOTAL_COST_KEY}' nor a billable usage event type."
            )
''',
            '''    for spec in entries:
        # ARCH-30 Tranche 1 (T4-F1). Capability entitlements are validated by
        # their own rule, BEFORE the meter-vocabulary refusal below.
        #
        # Without this branch `llm.platform_key` reached the refusal, every
        # seeded tier was rejected, the seed's single transaction rolled all
        # four back, and `_platform_key_entitled` correctly refused inference
        # to every tenant without BYOK. The ordering is not an accident: an
        # entitlement must never fall through to `is_limit_key`, and
        # `entitlements._assert_disjoint_from_meters` guarantees no key can
        # satisfy both, so there is no string for which the order matters.
        if entitlements.is_entitlement_key(spec.limit_key):
            violation = entitlements.shape_violation(
                limit_key=spec.limit_key,
                period=spec.period,
                max_quantity=spec.max_quantity,
                max_cost_micros=spec.max_cost_micros,
                overage_policy=spec.overage_policy,
                overage_price_tier_key=spec.overage_price_tier_key,
                grace_quantity=spec.grace_quantity,
            )
            if violation is not None:
                raise QuotaTierValidationError(violation)
            entitlement_scope = (spec.limit_key, spec.period.value)
            if entitlement_scope in seen:
                raise QuotaTierValidationError(
                    f"Duplicate entry {entitlement_scope!r}."
                )
            seen.add(entitlement_scope)
            validated.append(spec)
            continue

        if not is_limit_key(spec.limit_key):
            raise QuotaTierValidationError(
                f"'{spec.limit_key}' is neither the wildcard '{TOTAL_COST_KEY}', "
                f"a billable usage event type, nor a registered entitlement."
            )
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F4 — saml.py: one SSO resolver, and federated logins land on the SPA
#          route that completes them
# =============================================================================
EDITS.append((
    ROOT / "app" / "api" / "v1" / "saml.py",
    "SSO_COMPLETE_PATH",
    [
        (
            "from urllib.parse import urlparse\n",
            "from urllib.parse import quote, urlparse\n",
            1,
        ),
        (
            '''from app.models.identity import (
    AuthMethod, EnterpriseIdpConfig, IdpProtocol, IdpSigningCertificate,
    SsoAssertion, SsoAuthRequest, VerifiedDomain,
)
''',
            '''from app.models.identity import (
    AuthMethod, DomainStatus, EnterpriseIdpConfig, IdpProtocol,
    IdpSigningCertificate, SsoAssertion, SsoAuthRequest, VerifiedDomain,
)
''',
            1,
        ),
        # ORDER MATTERS. The three edits below remove the handlers' own
        # `frontend = ...` lines and their bare redirects BEFORE the helper is
        # inserted, because `_sso_landing_redirect` contains an identical
        # `frontend = ...` line and would otherwise double the anchor count.
        (
            '''    frontend = str(getattr(get_settings(), "FRONTEND_URL", "")).rstrip("/")
''',
            "",
            1,
        ),
        (
            '''    frontend = str(getattr(settings, "FRONTEND_URL", "")).rstrip("/")
''',
            "",
            1,
        ),
        (
            '''    response = RedirectResponse(f"{frontend}{target}", status_code=302)
    set_refresh_cookie(response, token=issued.plaintext_token)
    return response''',
            '''    return _sso_landing_redirect(target=target,
                                 plaintext_token=issued.plaintext_token)''',
            2,
        ),
        (
            '''_GENERIC_FAILURE = {"detail": "Authentication failed."}
''',
            '''_GENERIC_FAILURE = {"detail": "Authentication failed."}


# ---------------------------------------------------------------------------
# ARCH-30 Tranche 1 (T4-F4) — SSO resolution and federated login completion
# ---------------------------------------------------------------------------

#: The SPA route that turns the refresh cookie into a signed-in session. Must
#: equal `ROUTES.SSO_COMPLETE` in frontend/src/constants/routes.ts;
#: verify_arch30_tranche1.py G7 compares the two literals.
SSO_COMPLETE_PATH = "/auth/sso/complete"

#: Domain states in which an email domain may route a login to an IdP. The
#: same set as `VerifiedDomain.provisioning_allowed`. PENDING proves nothing,
#: and LAPSED or REVOKED prove the proof expired.
_SSO_ROUTABLE_DOMAIN_STATUSES = (DomainStatus.VERIFIED, DomainStatus.GRACE)

_AMBIGUOUS_BINDING = {
    "detail": (
        "Single sign-on for this domain is misconfigured. "
        "Contact your administrator."
    )
}


class AmbiguousSsoBinding(Exception):
    """More than one active IdP configuration claims the same email domain."""


def _normalise_sso_domain(value: str) -> str:
    return value.strip().lower().rsplit("@", 1)[-1]


def _resolve_sso_binding(db, domain: str) -> EnterpriseIdpConfig | None:
    """THE lookup from an email domain to the IdP that authenticates it.

    WHY ONE FUNCTION
    ================

    `discover` and `start_sso` each carried their own copy of this query, and
    the copies had drifted: discover ended in `.first()`, start in
    `.one_or_none()`. On a domain with two bindings, discover told the login
    page SSO was available and start then raised `MultipleResultsFound` — a
    500 on the button the page had just shown. Worse, `.first()` without an
    ORDER BY routed the user to whichever organization's IdP the planner
    returned first.

    WHY AMBIGUITY IS REACHABLE
    ==========================

    `uq_verified_domains_org_domain` is `(organization_id, domain)`, not
    `(domain)`. Two organizations can each verify `acme.com` — after a domain
    changes hands, or when the owner publishes both TXT records — and each can
    mark it as an SSO binding. Nothing in the schema prevents it, so this
    function must not assume it away.

    The structural fix is a partial unique index on `verified_domains(domain)
    WHERE is_sso_binding`. That is a migration and belongs with the inherited
    `idp_entity_id` `.one_or_none()` defect in the ACS, which is the same class
    one table over. Until then, ambiguity is REFUSED and logged at ERROR on
    both endpoints. Refusing blocks a login; routing arbitrarily hands a user's
    credentials page to another tenant's IdP.

    WHY THE STATUS FILTER
    =====================

    Neither original query filtered `VerifiedDomain.status`. An organization
    holding a PENDING claim on a domain it never proved — or a LAPSED one it no
    longer controls — could, if the binding flag was set, have that domain's
    users sent to its IdP from the login page. `limit(2)` fetches just enough
    rows to tell one from many.
    """
    normalised = _normalise_sso_domain(domain)
    rows = (
        db.query(EnterpriseIdpConfig)
        .join(VerifiedDomain,
              EnterpriseIdpConfig.verified_domain_id == VerifiedDomain.id)
        .filter(
            VerifiedDomain.domain == normalised,
            VerifiedDomain.is_sso_binding.is_(True),
            VerifiedDomain.status.in_(_SSO_ROUTABLE_DOMAIN_STATUSES),
            EnterpriseIdpConfig.is_active.is_(True),
        )
        .limit(2)
        .all()
    )
    if len(rows) > 1:
        logger.error(
            "sso.ambiguous_domain_binding",
            extra={
                "domain": normalised,
                "idp_config_ids": sorted(str(row.id) for row in rows),
                "organization_ids": sorted(
                    {str(row.organization_id) for row in rows}
                ),
            },
        )
        raise AmbiguousSsoBinding(normalised)
    return rows[0] if rows else None


def _sso_landing_redirect(*, target: str | None,
                          plaintext_token: str) -> RedirectResponse:
    """Hand the browser to the SPA route that completes a federated login.

    ARCH-30 Step 0 made both federated handlers set the refresh cookie, and
    its comment said the SPA "exchanges it for an access token at
    /auth/refresh on landing". Nothing in the SPA did that for a browser that
    had never signed in:

      * `SessionBootstrap` restores only when the persisted `isAuthenticated`
        flag is already true, which on a first SSO login it is not;
      * `PrivateRoute` then redirects on that same flag before any request is
        made.

    So the cookie arrived, went unread, and the user was sent to /login — the
    loop Step 0 was written to end, moved one layer up. Landing on a route
    whose only job is the exchange makes the seam explicit instead of
    depending on a guard to guess that a session might exist.

    `next` is re-validated by the SPA with `isSafeRedirectPath`; it is
    validated here too so a bad value never reaches a URL at all. A path is not
    a credential, so the query string is an acceptable carrier — unlike the
    access token, which is why the token still travels only in the cookie.
    """
    frontend = str(getattr(get_settings(), "FRONTEND_URL", "")).rstrip("/")
    destination = target if target and is_safe_redirect_path(target) else "/"
    response = RedirectResponse(
        f"{frontend}{SSO_COMPLETE_PATH}?next={quote(destination, safe='')}",
        status_code=302,
    )
    set_refresh_cookie(response, token=plaintext_token)
    return response
''',
            1,
        ),
        (
            '''    normalised = domain.strip().lower().rsplit("@", 1)[-1]
    row = (
        db.query(VerifiedDomain, EnterpriseIdpConfig)
        .join(EnterpriseIdpConfig,
              EnterpriseIdpConfig.verified_domain_id == VerifiedDomain.id)
        .filter(VerifiedDomain.domain == normalised,
                VerifiedDomain.is_sso_binding.is_(True),
                EnterpriseIdpConfig.is_active.is_(True))
        .first()
    )
    if row is None:
        return {"sso_enabled": False}
    _, config = row
''',
            '''    normalised = _normalise_sso_domain(domain)
    try:
        config = _resolve_sso_binding(db, normalised)
    except AmbiguousSsoBinding:
        return JSONResponse(status_code=409, content=_AMBIGUOUS_BINDING)
    if config is None:
        return {"sso_enabled": False}
''',
            1,
        ),
        (
            '''    normalised = domain.strip().lower().rsplit("@", 1)[-1]
    row = (
        db.query(EnterpriseIdpConfig)
        .join(VerifiedDomain, EnterpriseIdpConfig.verified_domain_id == VerifiedDomain.id)
        .filter(VerifiedDomain.domain == normalised,
                VerifiedDomain.is_sso_binding.is_(True),
                EnterpriseIdpConfig.is_active.is_(True))
        .one_or_none()
    )
    if row is None:
''',
            '''    normalised = _normalise_sso_domain(domain)
    try:
        row = _resolve_sso_binding(db, normalised)
    except AmbiguousSsoBinding:
        return JSONResponse(status_code=409, content=_AMBIGUOUS_BINDING)
    if row is None:
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F3 — scim.py: on a tenant custom domain, the token must belong to the host
# =============================================================================
EDITS.append((
    ROOT / "app" / "api" / "v1" / "scim.py",
    "host_organization_id(request)",
    [
        (
            '''    client_ip = resolve_client_ip(request)
    return scim_service.authenticate(db, bearer=token, source_ip=client_ip)
''',
            '''    client_ip = resolve_client_ip(request)
    key = scim_service.authenticate(db, bearer=token, source_ip=client_ip)

    # ARCH-30 Tranche 1 (T4-F3). SCIM is now proxied on tenant custom domains
    # as well as the platform host, so a request can arrive on `ai.acme.com`
    # carrying a token issued to a different organization.
    #
    # The token alone already decides which directory is written — every
    # scim_service call is scoped by `key.organization_id` — so this is not
    # closing a read. It is closing a CONFUSION: an IdP configured with Acme's
    # hostname and Globex's token would provision into Globex while every log
    # line and every admin looking at the URL says Acme. ARCH-25's rule is that
    # a verified Host adds a constraint and never replaces authorization; this
    # applies that rule to the one router mounted outside /api.
    #
    # 404 with the same body as a bad token, so a mismatched host is
    # indistinguishable from a wrong credential. The platform host resolves no
    # tenant (`host_org is None`) and is unaffected.
    from app.middleware.host_tenant import host_organization_id

    host_org = host_organization_id(request)
    if host_org is not None and host_org != key.organization_id:
        logger.warning(
            "scim.host_token_mismatch",
            extra={
                "host_organization_id": str(host_org),
                "scim_key_organization_id": str(key.organization_id),
            },
        )
        raise ScimNotFound("Invalid credentials.")
    return key
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F3 — Caddyfile: SCIM is mounted at the application root, outside /api
# =============================================================================
EDITS.append((
    ROOT / "deploy" / "Caddyfile",
    "handle /scim/v2/*",
    [
        (
            '''    handle /docs* {
        reverse_proxy web:8000
    }
''',
            '''    # ARCH-30 Tranche 1 (T4-F3) — SCIM on the platform host.
    #
    # `app.main` mounts the SCIM router at /scim/v2, at the application ROOT,
    # not under /api. Every block above matches /api/* only, so an IdP's
    # POST /scim/v2/Users fell through to the catch-all file server below and
    # was answered with index.html. SCIM worked in development only because
    # start_dev.ps1 talks to uvicorn directly with no ingress in front.
    handle /scim/v2/* {
        reverse_proxy web:8000 {
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }

    handle /docs* {
        reverse_proxy web:8000
    }
''',
            1,
        ),
        (
            '''    handle /api/* {
        reverse_proxy web:8000 {
            header_up Host {host}
''',
            '''    # ARCH-30 Tranche 1 (T4-F3) — SCIM on tenant custom domains.
    #
    # `header_up Host {host}` is load-bearing: `scim_key` refuses a token whose
    # organization differs from the tenant this hostname resolves to, and
    # HostTenantMiddleware can only resolve the tenant if the Host survives.
    handle /scim/v2/* {
        reverse_proxy web:8000 {
            header_up Host {host}
            header_up X-Forwarded-Proto {scheme}
            header_up X-Real-IP {remote_host}
        }
    }

    handle /api/* {
        reverse_proxy web:8000 {
            header_up Host {host}
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F5 — client.ts: production builds call the API same-origin
# =============================================================================
EDITS.append((
    FE / "services" / "api" / "client.ts",
    "export const apiOrigin",
    [
        (
            '''const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";
''',
            '''/**
 * ARCH-30 Tranche 1 (T4-F5). The API base URL.
 *
 * This previously defaulted to `http://localhost:8000/api/v1` in EVERY build.
 * A production bundle built without VITE_API_URL shipped a localhost API; one
 * built with an absolute platform URL was worse, because it looked fine.
 *
 * ARCH-25 resolves a tenant from the Host header. Caddy serves the SPA and
 * proxies /api/* on each tenant custom domain with `header_up Host {host}`, so
 * a same-origin call from `ai.acme.com` arrives with Acme's host. An absolute
 * `https://app.flowpilot.ai/api/v1` sends that call to the PLATFORM host
 * instead: the branding manifest returns FlowPilot defaults, the refresh
 * cookie set on the tenant's origin is not sent, and nothing reports an error.
 *
 * Production therefore defaults to the relative `/api/v1`. `vite.config.ts`
 * refuses to build a production bundle with an absolute VITE_API_URL unless
 * VITE_ALLOW_CROSS_ORIGIN_API=true is set deliberately, so the silent case now
 * fails at build time. Development keeps the absolute default because Vite and
 * uvicorn run on different ports with no proxy between them.
 *
 * ONE OWNER. `services/streaming/resumableStream.ts` carried its own copy of
 * the old expression, so fixing only this file would have left every
 * production assistant stream pointed at `http://localhost:8000`. It now
 * imports this constant, and gate 30T1-G9 fails if any other module reads
 * `VITE_API_URL` again.
 */
export const API_BASE_URL: string =
  import.meta.env.VITE_API_URL ??
  (import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1");

const API_URL = API_BASE_URL;

/**
 * The origin serving the API, always absolute.
 *
 * `new URL` against `window.location.origin` resolves both shapes: the relative
 * production base becomes this page's origin, the absolute development base
 * stays what it is. Used where a full URL must leave the browser — the SCIM
 * base URL an administrator pastes into their IdP.
 */
export const apiOrigin = (): string =>
  new URL(API_URL, window.location.origin).origin;

/**
 * An href for a path under the API base, for top-level navigations.
 *
 * SSO start is a 302 to the identity provider and must own the window, so it
 * cannot go through axios. Composed from the base and a caller-built path —
 * never from a URL string in a response body.
 */
export const apiHref = (path: string): string => `${API_URL}${path}`;

/**
 * Resolve a server-supplied asset path against the API origin.
 *
 * The branding manifest returns `/api/v1/branding/logo`, an origin-relative
 * path. Rendered raw in development it would resolve against Vite's port and
 * 404. Anything that resolves OFF the API origin returns null: a manifest is
 * unauthenticated input to an `<img>` on the login page, and the only logo it
 * may point at is one this API serves.
 */
export const resolveApiAssetUrl = (
  path: string | null | undefined,
): string | null => {
  if (!path) {
    return null;
  }
  try {
    const origin = apiOrigin();
    const resolved = new URL(path, origin);
    return resolved.origin === origin ? resolved.toString() : null;
  } catch {
    return null;
  }
};
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F4 — routes.ts: the completion route
# =============================================================================
EDITS.append((
    FE / "constants" / "routes.ts",
    "SSO_COMPLETE:",
    [
        (
            '''  LOGIN: "/login",
''',
            '''  LOGIN: "/login",
  /**
   * ARCH-30 Tranche 1 (T4-F4). Where federated logins land. Must equal
   * SSO_COMPLETE_PATH in backend/app/api/v1/saml.py (gate 30T1-G7).
   */
  SSO_COMPLETE: "/auth/sso/complete",
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F4 — App.tsx: mount the completion route outside both session guards
# =============================================================================
EDITS.append((
    FE / "App.tsx",
    "path={ROUTES.SSO_COMPLETE}",
    [
        (
            '''import { Login } from "@/pages/Auth/Login";
''',
            '''import { Login } from "@/pages/Auth/Login";
import { SsoComplete } from "@/pages/Auth/SsoComplete";
''',
            1,
        ),
        (
            '''              {/* Authenticated, tenant-independent */}
''',
            '''              {/*
                ARCH-30 Tranche 1 (T4-F4). Federated login completion.

                Outside PublicRoute AND outside PrivateRoute, deliberately.
                PrivateRoute redirects a browser with no persisted session to
                /login before the refresh cookie is ever exchanged, which is
                the loop this route exists to break. PublicRoute redirects a
                browser WITH a persisted session straight to the dashboard,
                skipping the exchange and keeping the previous user's profile
                in the store. Under AuthLayout so a tenant host shows its own
                branding while the exchange runs.
              */}
              <Route element={<AuthLayout />}>
                <Route
                  path={ROUTES.SSO_COMPLETE}
                  element={<SsoComplete />}
                />
              </Route>

              {/* Authenticated, tenant-independent */}
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F4 — endpoints.ts: SSO endpoints
# =============================================================================
EDITS.append((
    FE / "services" / "api" / "endpoints.ts",
    "SSO_ENDPOINTS",
    [
        (
            '''/**
 * ARCH-21 — the public gateway.
''',
            '''/**
 * ARCH-16 / ARCH-30 Tranche 1 (T4-F4) — enterprise single sign-on.
 *
 * `start` is never called through axios. It answers with a 302 to the identity
 * provider, whose login page must own the window, so it is a top-level
 * navigation built with `apiHref`. That also means the server's `start_url`
 * field is not followed: the browser's destination is composed here from a
 * constant and encoded parameters, and nothing in a response body steers it.
 */
export const SSO_ENDPOINTS = {
  discover: "/sso/discover",
  start: "/sso/start",
} as const;

/**
 * ARCH-21 — the public gateway.
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F5 — branding.ts: the manifest gets a service function
# =============================================================================
EDITS.append((
    FE / "services" / "api" / "branding.ts",
    "getPublicBrandingManifest",
    [
        (
            '''import type {
  CertificateStatusResponse,
''',
            '''import type {
  BrandingManifest,
  CertificateStatusResponse,
''',
            1,
        ),
        (
            '''const JSON_HEADERS = { Accept: "application/json" } as const;
''',
            '''const JSON_HEADERS = { Accept: "application/json" } as const;

// ---------------------------------------------------------------------------
// Public, host-resolved — ARCH-30 Tranche 1 (T4-F5)
// ---------------------------------------------------------------------------

/**
 * The theme for whichever hostname this page was served on.
 *
 * ARCH-25 built this endpoint, mounted it, registered it as public and gave it
 * `Vary: Host`. `BRANDING_ENDPOINTS.manifest` and `brandingKeys.manifest` were
 * declared for it. Nothing called either, so the login page on a tenant's own
 * domain rendered FlowPilot's name — data with no reader, invisible to the
 * compiler because an unused export is legal.
 *
 * It takes no organization id and must never take one. The tenant is the
 * Host, resolved server-side against verified domains only; a parameter would
 * turn this into an unauthenticated "what does organization X look like" query.
 */
export const getPublicBrandingManifest = async (): Promise<BrandingManifest> => {
  const response = await apiClient.get<BrandingManifest>(
    BRANDING_ENDPOINTS.manifest,
    { headers: JSON_HEADERS },
  );
  return response.data;
};
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F3 — ScimTokenManager.tsx: show the base URL an IdP needs
# =============================================================================
EDITS.append((
    FE / "pages" / "identity" / "ScimTokenManager.tsx",
    "<ScimBaseUrl />",
    [
        (
            '''import { identityKeys } from "@/services/api/queryKeys";
''',
            '''import { apiOrigin } from "@/services/api/client";
import { identityKeys } from "@/services/api/queryKeys";
''',
            1,
        ),
        (
            '''            Tokens your identity provider uses to create and deactivate members
            automatically.
          </p>
''',
            '''            Tokens your identity provider uses to create and deactivate members
            automatically.
          </p>
          <ScimBaseUrl />
''',
            1,
        ),
        (
            '''export default ScimTokenManager;''',
            '''/**
 * ARCH-30 Tranche 1 (T4-F3). The SCIM base URL, with a copy control.
 *
 * Okta and Entra ID ask for two values: a base URL and a bearer token. This
 * console offered the token and never the URL, so an administrator had to
 * guess — and the natural guess, `<host>/api/v1/scim/v2`, is wrong, because
 * the router is mounted at the application root.
 *
 * Built from `apiOrigin()`, so it is this page's origin in production and the
 * uvicorn origin in development. On a tenant custom domain it is that domain,
 * which is valid: the ingress proxies SCIM there and `scim_key` accepts a token
 * whose organization matches the host.
 */
const ScimBaseUrl: React.FC = () => {
  const baseUrl = `${apiOrigin()}/scim/v2`;
  const [copied, setCopied] = useState(false);

  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(baseUrl);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      setCopied(false);
    }
  }, [baseUrl]);

  return (
    <div className="mt-2 flex max-w-xl items-center gap-2 rounded-md border border-border bg-muted/40 px-2 py-1.5">
      <span className="shrink-0 text-[11px] font-medium text-muted-foreground">
        SCIM base URL
      </span>
      <code className="min-w-0 flex-1 truncate font-mono text-xs">{baseUrl}</code>
      <button
        type="button"
        onClick={() => void copy()}
        aria-label="Copy SCIM base URL"
        className="inline-flex shrink-0 items-center rounded p-1 text-muted-foreground hover:bg-muted hover:text-foreground"
      >
        {copied ? <Check className="h-3.5 w-3.5" /> : <Copy className="h-3.5 w-3.5" />}
      </button>
    </div>
  );
};

export default ScimTokenManager;''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F5 — resumableStream.ts: the second copy of the API base
# =============================================================================
EDITS.append((
    FE / "services" / "streaming" / "resumableStream.ts",
    "API_BASE_URL",
    [
        (
            '''import { useAuthStore } from "@/store/useAuthStore";
''',
            '''import { API_BASE_URL } from "@/services/api/client";
import { useAuthStore } from "@/store/useAuthStore";
''',
            1,
        ),
        (
            '''const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";
''',
            '''// ARCH-30 Tranche 1 (T4-F5). This module computed its own API base with the
// same `?? "http://localhost:8000/api/v1"` default client.ts used to have, so
// every production build streamed assistant responses from localhost — mixed
// content on https, blocked outright — while every non-streaming request
// worked. One owner now; gate 30T1-G9 rejects a second reader of VITE_API_URL.
const API_URL = API_BASE_URL;
''',
            1,
        ),
    ],
))


# =============================================================================
# T4-F5 — dev scripts and the env template stop feeding `vite build` localhost
# =============================================================================
EDITS.append((
    REPO / "frontend" / ".env.example",
    "Leave VITE_API_URL UNSET",
    [
        (
            """# 1. API Gateway Target Context
# Maps standard local dev, container, or production port gateways.
VITE_API_URL=http://localhost:8000/api/v1
""",
            """# 1. API Gateway Target Context
# Leave VITE_API_URL UNSET here (ARCH-30 Tranche 1). Development defaults to
# http://localhost:8000/api/v1 and production to same-origin /api/v1. Vite reads
# .env for `vite build` too, and vite.config.ts refuses a production build with
# an absolute URL. For a non-default dev port use .env.development.local, which
# start_dev.ps1 and start_dev.sh write for you.
# VITE_API_URL=http://localhost:8000/api/v1
""",
            1,
        ),
    ],
))

EDITS.append((
    REPO / "start_dev.ps1",
    ".env.development.local",
    [
        (
            """Set-EnvValue $frontendEnv 'VITE_API_URL' "http://localhost:$ApiPort/api/v1"
""",
            """# ARCH-30 Tranche 1 (T4-F5). VITE_API_URL belongs in .env.development.local,
# which Vite loads for the dev server only. Written to .env it was ALSO loaded by
# `vite build`, baking this machine's localhost API into production bundles;
# vite.config.ts now refuses that build. Any copy an earlier run left in .env is
# stripped, once, so an existing checkout builds again.
$frontendDevEnv = Join-Path $FrontendDir '.env.development.local'
if (Select-String -Path $frontendEnv -Pattern '^\\s*VITE_API_URL\\s*=' -Quiet) {
    $kept = @(Get-Content $frontendEnv | Where-Object { $_ -notmatch '^\\s*VITE_API_URL\\s*=' })
    Set-Content -Path $frontendEnv -Value $kept -Encoding UTF8
    Write-Ok 'Moved VITE_API_URL out of frontend/.env.'
}
if (Test-Path $frontendDevEnv) {
    Set-EnvValue $frontendDevEnv 'VITE_API_URL' "http://localhost:$ApiPort/api/v1"
} else {
    Set-Content -Path $frontendDevEnv -Value "VITE_API_URL=http://localhost:$ApiPort/api/v1" -Encoding UTF8
}
""",
            1,
        ),
    ],
))

EDITS.append((
    REPO / "start_dev.sh",
    "FRONTEND_DEV_ENV",
    [
        (
            """env_set "${FRONTEND_ENV}" VITE_API_URL "http://localhost:${API_PORT}/api/v1"
""",
            """# ARCH-30 Tranche 1 (T4-F5) — see start_dev.ps1. Dev-server-only file, and any
# copy an earlier run left in .env is stripped so `vite build` stops refusing.
FRONTEND_DEV_ENV="${FRONTEND_DIR}/.env.development.local"
if grep -q '^[[:space:]]*VITE_API_URL[[:space:]]*=' "${FRONTEND_ENV}"; then
    { grep -v '^[[:space:]]*VITE_API_URL[[:space:]]*=' "${FRONTEND_ENV}" || true; } > "${FRONTEND_ENV}.tmp"
    mv "${FRONTEND_ENV}.tmp" "${FRONTEND_ENV}"
fi
[[ -f "${FRONTEND_DEV_ENV}" ]] || printf 'VITE_API_URL=http://localhost:%s/api/v1\\n' "${API_PORT}" > "${FRONTEND_DEV_ENV}"
env_set "${FRONTEND_DEV_ENV}" VITE_API_URL "http://localhost:${API_PORT}/api/v1"
""",
            1,
        ),
    ],
))


# =============================================================================
# Engine
# =============================================================================

def _first_line(fragment: str) -> str:
    for line in fragment.splitlines():
        if line.strip():
            return line.strip()
    return fragment[:60]


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-30 Tranche 1 patch script")
    parser.add_argument(
        "--check", action="store_true",
        help="validate every anchor and report, without writing",
    )
    args = parser.parse_args()

    planned: list[tuple[pathlib.Path, bool, str]] = []
    skipped: list[pathlib.Path] = []
    errors: list[str] = []

    for path, sentinel, replacements in EDITS:
        rel = path.relative_to(REPO)
        if not path.exists():
            errors.append(f"{rel}: file not found")
            continue

        raw = path.read_bytes()
        has_bom = raw.startswith(BOM)
        text = raw.decode("utf-8-sig")

        if "\r\n" in text:
            errors.append(
                f"{rel}: CRLF line endings. Anchors are LF; normalise the file "
                f"(git add --renormalize) rather than letting a partial match "
                f"through."
            )
            continue

        if sentinel in text:
            skipped.append(path)
            continue

        updated = text
        failed = False
        for old, new, expected in replacements:
            found = updated.count(old)
            if found != expected:
                errors.append(
                    f"{rel}: anchor expected {expected}x, found {found}x — "
                    f"{_first_line(old)!r}"
                )
                failed = True
                break
            updated = updated.replace(old, new)

        if failed:
            continue

        if sentinel not in updated:
            errors.append(
                f"{rel}: sentinel {sentinel!r} absent after applying edits; the "
                f"edit set is malformed and would re-apply on every run"
            )
            continue

        planned.append((path, has_bom, updated))

    print("=" * 78)
    print("ARCH-30 TRANCHE 1 — PATCH")
    print("=" * 78)

    for path in skipped:
        print(f"[SKIP] {path.relative_to(REPO)}  (already applied)")

    if errors:
        for message in errors:
            print(f"[FAIL] {message}")
        print("-" * 78)
        print("ABORTED — no file was written.")
        return 1

    for path, _, _ in planned:
        verb = "WOULD" if args.check else " OK "
        print(f"[{verb}] {path.relative_to(REPO)}")

    if args.check:
        print("-" * 78)
        print(f"check: {len(planned)} to apply, {len(skipped)} already applied")
        return 0

    for path, has_bom, updated in planned:
        payload = updated.encode("utf-8")
        path.write_bytes((BOM + payload) if has_bom else payload)

    print("-" * 78)
    print(f"{len(planned)} applied, {len(skipped)} already applied")
    return 0


if __name__ == "__main__":
    sys.exit(main())