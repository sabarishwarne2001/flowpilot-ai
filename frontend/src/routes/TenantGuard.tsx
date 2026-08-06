/**
 * Tenant resolution guard for FlowPilot AI.
 *
 * Replaces the OnboardingGuard defined inline in App.tsx, which called
 * getWorkspace() and treated any falsy result as "no workspace". An expired
 * token and a genuinely membership-less user produced the same signal, so
 * session expiry sent people to "Create My Workspace" instead of the login
 * page, and removed members founded phantom organizations.
 *
 * This guard handles all six tenant states explicitly, with a `never`
 * exhaustiveness check at the end — so a state added later fails to compile
 * here rather than silently falling through to whichever branch happens to be
 * last.
 *
 * ROUTING TABLE
 *
 *   loading              splash. No routing decision from incomplete data.
 *   unauthenticated      /login, with the destination preserved.
 *   error                explanatory screen with retry. NOT onboarding —
 *                        a network blip must never create an organization.
 *   onboarding_required  /onboarding. Reachable only from a 200 response.
 *   no_workspace         /workspaces picker. The actor HAS a tenant; telling
 *                        them to create one would be wrong.
 *   ready                reconcile the URL against the resolved tenant.
 */

import React, { useEffect } from "react";
import { Navigate, Outlet, useLocation, useParams } from "react-router-dom";
import { Loader2, RefreshCw } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useTenant } from "@/hooks/useTenant";
import { ROUTE_PARAMS, loginPathWithRedirect } from "@/routes/tenantPaths";
import { reconcileTenantWithUrl } from "@/routes/tenantReconciliation";
import { TenantContextProvider } from "@/routes/TenantContext";
import type { TenantRouteParams } from "@/routes/tenantPaths";

/**
 * Full-screen splash shown while the bootstrap context is in flight.
 *
 * Deliberately minimal and self-contained. This renders before any tenant is
 * known, so it cannot depend on workspace branding.
 */
const TenantSplash: React.FC = () => (
  <div className="flex h-screen w-full items-center justify-center bg-background">
    <div className="flex flex-col items-center gap-3">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      <p className="text-sm font-medium text-muted-foreground">
        Loading your workspace...
      </p>
    </div>
  </div>
);

interface TenantErrorProps {
  onRetry: () => void;
}

/**
 * Shown when the bootstrap fails for a reason other than authentication.
 *
 * A distinct screen rather than a redirect, because there is nowhere correct
 * to redirect to: the failure means tenancy is unknown, and every destination
 * would be a guess. Guessing here is exactly how a transient network failure
 * would have led a user to create a duplicate organization.
 */
const TenantError: React.FC<TenantErrorProps> = ({ onRetry }) => (
  <div className="flex h-screen w-full items-center justify-center bg-background px-6">
    <div className="w-full max-w-sm space-y-4 text-center">
      <h1 className="text-lg font-bold tracking-tight text-foreground">
        Unable to load your workspace
      </h1>
      <p className="text-sm text-muted-foreground">
        We could not reach the server. Your data is safe — this is a connection
        problem, not a change to your account.
      </p>
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex w-full items-center justify-center gap-2 rounded-lg bg-primary py-2 text-sm font-semibold text-primary-foreground transition hover:opacity-90"
      >
        <RefreshCw className="h-4 w-4" />
        Try again
      </button>
    </div>
  </div>
);

/**
 * Resolves tenant context and publishes it to descendants.
 *
 * Mount as the element of the workspace shell route, so every tenant-scoped
 * page renders beneath it.
 */
export const TenantGuard: React.FC = () => {
  const location = useLocation();
  const params = useParams() as TenantRouteParams;
  const { state, refresh, selectWorkspace } = useTenant();

  const urlOrgSlug = params[ROUTE_PARAMS.orgSlug];
  const urlWorkspaceSlug = params[ROUTE_PARAMS.workspaceSlug];

  const reconciliation =
    state.status === "ready"
      ? reconcileTenantWithUrl(state, urlOrgSlug, urlWorkspaceSlug)
      : null;

  // Sync the store to the tenant the URL selected.
  //
  // The URL is authoritative, so a deep link into another workspace updates
  // the remembered selection rather than being overridden by it. Guarded by
  // shouldSyncSelection so this writes only on a genuine change; without the
  // guard the store update would re-render and loop.
  useEffect(() => {
    if (
      reconciliation?.action === "render" &&
      reconciliation.shouldSyncSelection
    ) {
      selectWorkspace(
        reconciliation.organization.organization_id,
        reconciliation.workspace.id,
      );
    }
  }, [reconciliation, selectWorkspace]);

  switch (state.status) {
    case "loading":
      return <TenantSplash />;

    case "unauthenticated":
      // The destination is preserved so signing in returns the actor to where
      // they were heading. Losing it was half of the pre-ARCH-01 defect; the
      // other half was landing on onboarding instead of login.
      return (
        <Navigate
          to={loginPathWithRedirect(`${location.pathname}${location.search}`)}
          replace
        />
      );

    case "error":
      return <TenantError onRetry={refresh} />;

    case "onboarding_required":
      // Reachable only from a successful response reporting zero
      // organizations. An expired session cannot arrive here.
      return <Navigate to={ROUTES.ONBOARDING} replace />;

    case "no_workspace":
      // The actor belongs to an organization but can reach no workspace in it
      // — an organization MEMBER with no grant, or a BILLING controller. They
      // already have a tenant, so sending them to create one would be wrong.
      return <Navigate to={ROUTES.WORKSPACES} replace />;

    case "ready": {
      if (!reconciliation) {
        return <TenantSplash />;
      }

      if (reconciliation.action === "redirect") {
        return <Navigate to={reconciliation.to} replace />;
      }

      if (reconciliation.action === "unreachable") {
        // Not a silent substitution. The actor asked for a specific tenant and
        // cannot reach it; the picker tells them so. Quietly opening a
        // different workspace would let a removed member believe nothing had
        // changed.
        return (
          <Navigate
            to={ROUTES.WORKSPACES}
            replace
            state={{ unreachable: reconciliation.reason }}
          />
        );
      }

      return (
        <TenantContextProvider
          value={{
            user: state.user,
            organization: reconciliation.organization,
            workspace: reconciliation.workspace,
            organizationRole: reconciliation.organization.role,
            workspaceRole: reconciliation.workspace.effective_role,
            organizations: state.organizations,
          }}
        >
          <Outlet />
        </TenantContextProvider>
      );
    }

    default: {
      // Exhaustiveness. A new TenantState variant fails to compile here rather
      // than falling through to whichever branch happens to be last — which is
      // how the previous guard ended up treating every unknown condition as
      // "send them to onboarding".
      const exhaustive: never = state;
      return exhaustive;
    }
  }
};

export default TenantGuard;
