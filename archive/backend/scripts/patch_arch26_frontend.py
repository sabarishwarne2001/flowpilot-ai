"""ARCH-26 frontend wiring — anchored, idempotent patches to five files.

    python scripts/patch_arch26_frontend.py
    python scripts/patch_arch26_frontend.py --check

Same discipline as patch_arch26_wiring.py: every patch checks for its own
marker first, so running twice is a no-op, and a missing anchor stops the run
loudly rather than guessing.

FILES TOUCHED
=============
    frontend/src/services/api/endpoints.ts        ANALYTICS_ENDPOINTS
    frontend/src/services/api/queryKeys.ts        analyticsKeys
    frontend/src/routes/tenantPaths.ts            route pattern + path helper
    frontend/src/components/layout/navigation.ts  nav link (ADMIN and above)
    frontend/src/App.tsx                          lazy route

WHY NO NEW RESERVED_ROUTE_SEGMENTS ENTRY IS NEEDED
==================================================

"organizations" is already in RESERVED_ROUTE_SEGMENTS, so
/organizations/:orgSlug/analytics cannot be misread by parseTenantPath as
orgSlug="organizations", workspaceSlug=":orgSlug". Same reasoning ARCH-20,
ARCH-21, ARCH-22 and ARCH-25 recorded for compliance, developer, byok and
branding.
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


def patch(relative: str, *, marker: str, anchor: str, replacement: str) -> None:
    path = ROOT / relative
    if not path.exists():
        _failed.append(f"{relative}: file does not exist")
        return
    source = path.read_text(encoding="utf-8")

    if marker in source:
        _skipped.append(f"{relative} (already applied)")
        return
    if anchor not in source:
        _failed.append(
            f"{relative}: anchor not found — {anchor.splitlines()[0][:90]!r}"
        )
        return
    if source.count(anchor) != 1:
        _failed.append(
            f"{relative}: anchor appears {source.count(anchor)} times; "
            "refusing to guess which one."
        )
        return

    if not CHECK_ONLY:
        path.write_text(source.replace(anchor, replacement), encoding="utf-8")
    _applied.append(relative)


# ---------------------------------------------------------------------------
# 1. endpoints.ts
# ---------------------------------------------------------------------------

patch(
    "src/services/api/endpoints.ts",
    marker="ANALYTICS_ENDPOINTS",
    anchor='''/**
 * ARCH-25 — white-label, custom domains and tenant branding.''',
    replacement='''/**
 * ARCH-26 — enterprise analytics, BI egress and warehouse sync.
 *
 * `org()` throws on an empty organizationId rather than producing
 * `/organizations//analytics/...`, which the server answers with a 404 that
 * reads like a missing feature.
 */
export const ANALYTICS_ENDPOINTS = {
  destinations: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/destinations`,
  destination: (organizationId: string, destinationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/destinations/${seg(destinationId)}`,
  testDestination: (organizationId: string, destinationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/destinations/${seg(destinationId)}/test`,
  schedules: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/schedules`,
  schedule: (organizationId: string, scheduleId: string): string =>
    `/organizations/${org(organizationId)}/analytics/schedules/${seg(scheduleId)}`,
  sync: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/sync`,
  runs: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/runs`,
  consumption: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/consumption`,
  datasets: (organizationId: string): string =>
    `/organizations/${org(organizationId)}/analytics/datasets`,
} as const;

/**
 * ARCH-25 — white-label, custom domains and tenant branding.''',
)


# ---------------------------------------------------------------------------
# 2. queryKeys.ts
# ---------------------------------------------------------------------------

patch(
    "src/services/api/queryKeys.ts",
    marker="analyticsKeys",
    anchor="export const sessionKeys = {",
    replacement='''export const analyticsKeys = {
  all: (organizationId: string) =>
    [...organizationScope(organizationId), "analytics"] as const,
  destinations: (organizationId: string) =>
    [...analyticsKeys.all(organizationId), "destinations"] as const,
  destination: (organizationId: string, destinationId: string) =>
    [...analyticsKeys.destinations(organizationId), destinationId] as const,
  schedules: (organizationId: string) =>
    [...analyticsKeys.all(organizationId), "schedules"] as const,
  runs: (organizationId: string) =>
    [...analyticsKeys.all(organizationId), "runs"] as const,
  consumption: (organizationId: string, windowDays: number) =>
    [...analyticsKeys.all(organizationId), "consumption", windowDays] as const,
  datasets: (organizationId: string) =>
    [...analyticsKeys.all(organizationId), "datasets"] as const,
};

export const sessionKeys = {''',
)


# ---------------------------------------------------------------------------
# 3. tenantPaths.ts
# ---------------------------------------------------------------------------

patch(
    "src/routes/tenantPaths.ts",
    marker="organizationAnalytics:",
    anchor='''  organizationBranding: "branding",''',
    replacement='''  organizationBranding: "branding",
  // ARCH-26. Same reasoning as compliance, developer, byok and branding:
  // "organizations" is already in RESERVED_ROUTE_SEGMENTS, so
  // /organizations/:orgSlug/analytics cannot be misread by parseTenantPath as
  // a workspace route. No new reserved segment is needed.
  organizationAnalytics: "analytics",''',
)

patch(
    "src/routes/tenantPaths.ts",
    marker="organizationAnalyticsPath",
    anchor="export const organizationBrandingPath = (orgSlug: string): string =>",
    replacement='''export const organizationAnalyticsPath = (orgSlug: string): string =>
  `${organizationPath(orgSlug)}/analytics`;

export const organizationBrandingPath = (orgSlug: string): string =>''',
)


# ---------------------------------------------------------------------------
# 4. navigation.ts
# ---------------------------------------------------------------------------

# The icon import runs BEFORE the nav item that uses it, and its marker is the
# two-line import fragment rather than the bare token.
#
# The first version of this script had it the other way round with
# marker="BarChart3,". The nav-item patch inserted `icon: BarChart3,` first,
# the import patch then found its own marker in that line, concluded it had
# already run, and skipped — leaving a component referencing an identifier
# nobody imported. A marker has to be a string only the patch itself can
# produce.
patch(
    "src/components/layout/navigation.ts",
    marker="  BarChart3,\n  Bell,",
    anchor="import {\n  Bell,",
    replacement="import {\n  BarChart3,\n  Bell,",
)

patch(
    "src/components/layout/navigation.ts",
    marker="organizationAnalyticsPath",
    anchor="  organizationBrandingPath,",
    replacement="  organizationAnalyticsPath,\n  organizationBrandingPath,",
)

patch(
    "src/components/layout/navigation.ts",
    marker='name: "Analytics & BI egress"',
    anchor='''    items.push({
      name: "Branding & custom domains",
      path: organizationBrandingPath(orgSlug),
      icon: Palette,
    });''',
    replacement='''    items.push({
      name: "Branding & custom domains",
      path: organizationBrandingPath(orgSlug),
      icon: Palette,
    });
    // ARCH-26. ADMIN sees the console because reading which warehouses the
    // tenant syncs to, and why last night's run failed, is support work.
    // Every write behind it is OWNER-gated by RequireOrgOwner on the
    // endpoint: registering a destination hands a credential for third-party
    // infrastructure to this platform and starts a recurring egress of tenant
    // data to it. Hiding the link is not what protects those endpoints.
    items.push({
      name: "Analytics & BI egress",
      path: organizationAnalyticsPath(orgSlug),
      icon: BarChart3,
    });''',
)


# ---------------------------------------------------------------------------
# 5. App.tsx
# ---------------------------------------------------------------------------

patch(
    "src/App.tsx",
    marker="OrganizationAnalytics",
    anchor='''const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),''',
    replacement='''// ARCH-26. Lazy like every other organization surface, so the analytics
// console's forms and charts stay out of the initial bundle.
const OrganizationAnalytics = lazy(
  () => import("@/pages/organization/OrganizationAnalytics"),
);
const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),''',
)

patch(
    "src/App.tsx",
    marker="ROUTE_PATTERNS.organizationAnalytics",
    anchor='''                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}''',
    replacement='''                      path={ROUTE_PATTERNS.organizationAnalytics}
                      element={<OrganizationAnalytics />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}''',
)


def main() -> int:
    global CHECK_ONLY
    parser = argparse.ArgumentParser(description="ARCH-26 frontend wiring")
    parser.add_argument("--check", action="store_true")
    parser.parse_args(namespace=argparse.Namespace())
    CHECK_ONLY = "--check" in sys.argv

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
            "\nAn anchor was missing. The target file has changed since "
            "ARCH-26 was written. Re-read it and update the anchor here so "
            "the next run is still idempotent.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())