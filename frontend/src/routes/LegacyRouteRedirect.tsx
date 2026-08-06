/**
 * Legacy path forwarding for FlowPilot AI.
 *
 * Before ARCH-01 every route was flat — /work-items, /settings, /. Those paths
 * encode the assumption that a user has exactly one workspace, so the URL need
 * not say which.
 *
 * This component resolves the actor's tenant and forwards the flat path to its
 * tenant-scoped equivalent:
 *
 *   /                      ->  /acme/engineering
 *   /work-items            ->  /acme/engineering/work-items
 *   /work-items/abc-123    ->  /acme/engineering/work-items/abc-123
 *
 * Two reasons this exists rather than deleting the flat routes outright:
 *
 *   1. Sidebar, navigation.ts, and DashboardLayout still link to the flat
 *      paths. They are rewritten in Step 8; without this, every sidebar link
 *      would 404 for a full step.
 *
 *   2. It stays useful permanently. A link saved before ARCH-01 lands in the
 *      right place instead of on a 404, and the query string and hash survive
 *      the hop.
 *
 * Also serves as the root redirect: rebaseTenantPath("/") returns the
 * workspace root, so one component covers both cases.
 */

import React from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useTenant } from "@/hooks/useTenant";
import { loginPathWithRedirect, rebaseTenantPath } from "@/routes/tenantPaths";

/**
 * Forwards the current flat path to its tenant-scoped equivalent.
 *
 * Non-ready tenant states are delegated to the same destinations TenantGuard
 * uses, so a legacy path behaves identically to a tenant path for an expired
 * session, a membership-less actor, or a failed bootstrap.
 */
export const LegacyRouteRedirect: React.FC = () => {
  const location = useLocation();
  const { state } = useTenant();

  switch (state.status) {
    case "loading":
      return (
        <div className="flex h-screen w-full items-center justify-center bg-background">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      );

    case "unauthenticated":
      return (
        <Navigate
          to={loginPathWithRedirect(`${location.pathname}${location.search}`)}
          replace
        />
      );

    case "error":
      // No tenant is known, so no tenant path can be built. The picker offers a
      // reachable next step; guessing a destination is what ARCH-01 removed.
      return <Navigate to={ROUTES.WORKSPACES} replace />;

    case "onboarding_required":
      return <Navigate to={ROUTES.ONBOARDING} replace />;

    case "no_workspace":
      return <Navigate to={ROUTES.WORKSPACES} replace />;

    case "ready": {
      const target = rebaseTenantPath(
        location.pathname,
        state.organization.organization_slug,
        state.workspace.slug,
      );

      // Query string and hash are preserved so a saved link keeps its filters,
      // its selected tab, and its anchor.
      return (
        <Navigate to={`${target}${location.search}${location.hash}`} replace />
      );
    }

    default: {
      const exhaustive: never = state;
      return exhaustive;
    }
  }
};

export default LegacyRouteRedirect;
