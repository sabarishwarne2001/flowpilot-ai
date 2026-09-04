"""ARCH-27 frontend wiring — anchored, idempotent patches to five files.

    python scripts/patch_arch27_frontend.py
    python scripts/patch_arch27_frontend.py --check

FILES TOUCHED
=============
    frontend/src/services/api/endpoints.ts       PARTNER_/MARKETPLACE_ENDPOINTS
    frontend/src/services/api/queryKeys.ts       partnerKeys, marketplaceKeys
    frontend/src/routes/tenantPaths.ts           route patterns and builders
    frontend/src/components/layout/navigation.ts sidebar entry
    frontend/src/App.tsx                         two lazy routes

WHY THE PARTNER PORTAL IS NOT UNDER THE ORGANIZATION SHELL
==========================================================

`/partners` is a sibling of the organization shell, like ARCH-18's `/admin`,
and for the same reason. A partner principal reads across a BOOK of
organizations; nesting the portal inside `OrganizationGuard` would render a
tenant switcher beside figures that do not respond to it, and would make a
cross-tenant total appear to belong to whichever organization happened to be
selected.

The marketplace IS under the organization shell, because it is genuinely
organization-scoped: what a tenant may browse and install depends on which
partner holds them.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2] / "frontend"

CHECK_ONLY = False
_applied: list[str] = []
_skipped: list[str] = []
_failed: list[str] = []


class AnchorMissing(RuntimeError):
    """An anchor was not found. The file has moved on; stop rather than guess."""


def _read(relative: str) -> tuple[pathlib.Path, str]:
    path = ROOT / relative
    if not path.exists():
        raise AnchorMissing(f"{relative} does not exist.")
    return path, path.read_text(encoding="utf-8-sig")


def _write(path: pathlib.Path, text: str) -> None:
    if CHECK_ONLY:
        return
    path.write_text(text, encoding="utf-8")


def patch(relative: str, *, marker: str, anchor: str, replacement: str) -> None:
    try:
        path, source = _read(relative)
    except AnchorMissing as exc:
        _failed.append(f"{relative}: {exc}")
        return

    if marker in source:
        _skipped.append(f"{relative} (already applied)")
        return

    if anchor not in source:
        _failed.append(
            f"{relative}: anchor not found. Expected to find:\n"
            f"    {anchor.splitlines()[0][:100]}"
        )
        return

    if source.count(anchor) != 1:
        _failed.append(
            f"{relative}: anchor appears {source.count(anchor)} times; "
            "refusing to guess which one."
        )
        return

    _write(path, source.replace(anchor, replacement))
    _applied.append(relative)


def append(relative: str, *, marker: str, text: str) -> None:
    """Append to the end of a file, once.

    Used where there is no stable interior anchor — endpoint and query-key
    modules grow by accretion at the bottom, and every phase since ARCH-21 has
    added its block there.
    """
    try:
        path, source = _read(relative)
    except AnchorMissing as exc:
        _failed.append(f"{relative}: {exc}")
        return

    if marker in source:
        _skipped.append(f"{relative} (already applied)")
        return

    _write(path, source.rstrip("\n") + "\n" + text)
    _applied.append(relative)


# ---------------------------------------------------------------------------
# 1. endpoints.ts
# ---------------------------------------------------------------------------

append(
    "src/services/api/endpoints.ts",
    marker="PARTNER_ENDPOINTS",
    text='''
/**
 * ARCH-27 — the partner tier.
 *
 * Note the absence of an organization id in these paths. A partner is a tier
 * ABOVE organization and reads across a book of them; a path scoped to one
 * organization would belong under ORGANIZATION_ENDPOINTS behind an org role,
 * which is precisely the confusion these separate constants prevent.
 */
export const PARTNER_ENDPOINTS = {
  list: "/partners",
  detail: (partnerId: string): string => `/partners/${seg(partnerId)}`,
  members: (partnerId: string): string => `/partners/${seg(partnerId)}/members`,
  member: (partnerId: string, userId: string): string =>
    `/partners/${seg(partnerId)}/members/${seg(userId)}`,
  book: (partnerId: string): string => `/partners/${seg(partnerId)}/book`,
  bookEntry: (partnerId: string, organizationId: string): string =>
    `/partners/${seg(partnerId)}/book/${seg(organizationId)}`,
  signingKeys: (partnerId: string): string =>
    `/partners/${seg(partnerId)}/signing-keys`,
  revokeSigningKey: (partnerId: string, keyId: string): string =>
    `/partners/${seg(partnerId)}/signing-keys/${seg(keyId)}/revoke`,
  agreements: (partnerId: string): string =>
    `/partners/${seg(partnerId)}/agreements`,
  payouts: (partnerId: string): string => `/partners/${seg(partnerId)}/payouts`,
  payout: (partnerId: string, periodId: string): string =>
    `/partners/${seg(partnerId)}/payouts/${seg(periodId)}`,
  sealPayout: (partnerId: string, periodId: string): string =>
    `/partners/${seg(partnerId)}/payouts/${seg(periodId)}/seal`,
  economics: (partnerId: string): string =>
    `/partners/${seg(partnerId)}/economics`,
  catalog: (partnerId: string): string => `/partners/${seg(partnerId)}/catalog`,
  manifests: (partnerId: string, itemId: string): string =>
    `/partners/${seg(partnerId)}/catalog/${seg(itemId)}/manifests`,
} as const;

/**
 * ARCH-27 — the tenant-facing marketplace. Organization-scoped, because what a
 * tenant may browse depends on which partner currently holds them.
 */
export const MARKETPLACE_ENDPOINTS = {
  catalog: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/marketplace/catalog`,
  manifest: (organizationId: string, manifestId: string): string =>
    `/organizations/${org(organizationId)}/marketplace/manifests/${seg(manifestId)}`,
  installations: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/marketplace/installations`,
  installation: (organizationId: string, installationId: string): string =>
    `/organizations/${org(organizationId)}/marketplace/installations/${seg(installationId)}`,
} as const;
''',
)


# ---------------------------------------------------------------------------
# 2. queryKeys.ts
# ---------------------------------------------------------------------------

append(
    "src/services/api/queryKeys.ts",
    marker="export const partnerKeys",
    text='''
/**
 * ARCH-27 — partner portal query keys.
 *
 * Rooted at "partner" and NOT under organizationScope: invalidating an
 * organization must not blow away a partner's book-wide ledger, and switching
 * tenant must not refetch it. They are different subjects.
 */
export const partnerKeys = {
  root: () => ["partner"] as const,
  mine: () => [...partnerKeys.root(), "mine"] as const,
  all: (partnerId: string) => [...partnerKeys.root(), partnerId] as const,
  detail: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "detail"] as const,
  members: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "members"] as const,
  book: (partnerId: string) => [...partnerKeys.all(partnerId), "book"] as const,
  signingKeys: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "signing-keys"] as const,
  agreements: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "agreements"] as const,
  payouts: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "payouts"] as const,
  statement: (partnerId: string, periodId: string) =>
    [...partnerKeys.payouts(partnerId), periodId] as const,
  economics: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "economics"] as const,
  catalog: (partnerId: string) =>
    [...partnerKeys.all(partnerId), "catalog"] as const,
};

/** ARCH-27 — the tenant marketplace. Organization-scoped, unlike partnerKeys. */
export const marketplaceKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "marketplace"] as const,
  catalog: (organizationId: string) =>
    [...marketplaceKeys.all(organizationId), "catalog"] as const,
  manifest: (organizationId: string, manifestId: string) =>
    [...marketplaceKeys.all(organizationId), "manifest", manifestId] as const,
  installations: (organizationId: string) =>
    [...marketplaceKeys.all(organizationId), "installations"] as const,
};

export const invalidatePartner = async (
  queryClient: QueryClient,
  partnerId: string,
): Promise<void> => {
  await queryClient.invalidateQueries({ queryKey: partnerKeys.all(partnerId) });
};
''',
)


# ---------------------------------------------------------------------------
# 3. tenantPaths.ts
# ---------------------------------------------------------------------------

patch(
    "src/routes/tenantPaths.ts",
    marker="organizationMarketplace:",
    anchor='  organizationAnalytics: "analytics",',
    replacement='''  organizationAnalytics: "analytics",
  // ARCH-27. Same reasoning as compliance, developer, byok, branding and
  // analytics: "organizations" is already in RESERVED_ROUTE_SEGMENTS, so
  // /organizations/:orgSlug/marketplace cannot be misread by parseTenantPath
  // as a workspace route. No new reserved segment is needed.
  organizationMarketplace: "marketplace",''',
)

patch(
    "src/routes/tenantPaths.ts",
    marker="partnerPortalShell",
    anchor='''  platformShell: "/admin",
  platformMargins: "margins",''',
    replacement='''  platformShell: "/admin",
  platformMargins: "margins",

  // ARCH-27 partner portal. A sibling of the organization shell, never a child
  // of it — the same decision ARCH-18 made for /admin. A partner principal
  // reads across a BOOK of organizations, so nesting this inside
  // OrganizationGuard would render a tenant switcher beside figures that do
  // not respond to it.
  partnerPortalShell: "/partners",''',
)

patch(
    "src/routes/tenantPaths.ts",
    marker="organizationMarketplacePath",
    anchor='''export const organizationBrandingPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/branding`;''',
    replacement='''export const organizationBrandingPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/branding`;

export const organizationMarketplacePath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/marketplace`;

export const partnerPortalPath = (): string => "/partners";''',
)


# ---------------------------------------------------------------------------
# 4. navigation.ts
# ---------------------------------------------------------------------------

patch(
    "src/components/layout/navigation.ts",
    marker="organizationMarketplacePath,",
    anchor="  organizationBrandingPath,\n",
    replacement="  organizationBrandingPath,\n  organizationMarketplacePath,\n",
)

patch(
    "src/components/layout/navigation.ts",
    marker="Partner marketplace",
    anchor='''    items.push({
      name: "Analytics & BI egress",
      path: organizationAnalyticsPath(orgSlug),
      icon: BarChart3,
    });''',
    replacement='''    items.push({
      name: "Analytics & BI egress",
      path: organizationAnalyticsPath(orgSlug),
      icon: BarChart3,
    });
    // ARCH-27. ADMIN sees the catalog because reading which third-party
    // workflows are installed, and what they do, is support work. Installing
    // is OWNER-gated by RequireOrgOwner on the endpoint: admitting executable
    // code authored by a third party into the tenant's own automation engine
    // is an ownership decision. Hiding the link is not what protects it —
    // marketplace_installations.verified_signature_id being NOT NULL is.
    items.push({
      name: "Partner marketplace",
      path: organizationMarketplacePath(orgSlug),
      icon: Store,
    });''',
)

patch(
    "src/components/layout/navigation.ts",
    marker="  Store,\n",
    anchor="  ShieldCheck,\n",
    replacement="  ShieldCheck,\n  Store,\n",
)


# ---------------------------------------------------------------------------
# 5. App.tsx
# ---------------------------------------------------------------------------

patch(
    "src/App.tsx",
    marker="MarketplaceCatalog",
    anchor='''const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),
);''',
    replacement='''const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),
);
// ARCH-27. Lazy like every other organization surface. The catalog pulls in a
// manifest inspector that renders a DAG and a JSON viewer, which no other page
// needs and most sessions never open.
const MarketplaceCatalog = lazy(
  () => import("@/pages/marketplace/MarketplaceCatalog"),
);
// ARCH-27. The partner portal is lazy for a stronger reason than the others:
// the overwhelming majority of users are not partner members at all, and this
// bundle would otherwise ship to every one of them.
const PartnerPortal = lazy(() => import("@/pages/partner/PartnerPortal"));''',
)

patch(
    "src/App.tsx",
    marker="ROUTE_PATTERNS.organizationMarketplace",
    anchor='''                    <Route
                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}
                    />''',
    replacement='''                    <Route
                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationMarketplace}
                      element={<MarketplaceCatalog />}
                    />''',
)

patch(
    "src/App.tsx",
    marker="ROUTE_PATTERNS.partnerPortalShell",
    anchor='''                {/* Legacy redirects */}''',
    replacement='''                {/* ARCH-27 partner portal.

                    A sibling of the organization shell, never a child of it —
                    the same decision ARCH-18 made for /admin. A partner reads
                    across a BOOK of organizations, so nesting this inside
                    OrganizationGuard would render a tenant switcher beside
                    figures that do not respond to it, and would make a
                    book-wide total appear to belong to whichever organization
                    happened to be selected.

                    It is behind PrivateRoute, not SuperAdminGuard: partner
                    membership is authorized server-side by
                    tenancy_service.require_membership, which returns 404 for a
                    non-member so the route is not a partner enumeration
                    oracle. */}
                <Route
                  path={ROUTE_PATTERNS.partnerPortalShell}
                  element={<PartnerPortal />}
                />

                {/* Legacy redirects */}''',
)


# ---------------------------------------------------------------------------


def main() -> int:
    global CHECK_ONLY

    parser = argparse.ArgumentParser(description="ARCH-27 frontend wiring")
    parser.add_argument(
        "--check",
        action="store_true",
        help="report what would change without writing",
    )
    args = parser.parse_args()
    CHECK_ONLY = args.check

    for entry in _applied:
        print(f"  {'WOULD PATCH' if CHECK_ONLY else 'PATCHED'}  {entry}")
    for entry in _skipped:
        print(f"  SKIP     {entry}")
    for entry in _failed:
        print(f"  FAILED   {entry}", file=sys.stderr)

    print(
        f"\n{len(_applied)} applied, {len(_skipped)} already present, "
        f"{len(_failed)} failed."
    )
    if _failed:
        print(
            "\nAn anchor was missing. That means the target file has changed "
            "since ARCH-27 was written. Do NOT hand-apply blindly — re-read "
            "the file and update the anchor in this script, so the next run "
            "is still idempotent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())