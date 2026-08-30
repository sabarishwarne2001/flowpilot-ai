/**
 * ARCH-18 — the platform administration shell.
 *
 * Scope is the superuser flag ONLY. It sits under PrivateRoute (which proves
 * the session against the server) and deliberately not under OrganizationGuard
 * or TenantGuard, because a platform page has no tenant: mounting it inside an
 * organization shell would make cross-tenant totals appear to belong to
 * whichever organization the user last had selected, which is exactly the
 * misreading the COGS dashboard must not invite.
 *
 * This guard is a NAVIGATION affordance, not a security control. It hides a
 * link and avoids rendering a page that would 404 anyway. The authority is
 * `require_superadmin` on the backend, which every one of these endpoints
 * carries independently. A user who edits their persisted auth state to set
 * `is_superuser` gets an empty dashboard and a row of 404s — nothing more.
 * That separation is the point: if this component were load-bearing, a
 * localStorage edit would be a privilege escalation.
 */

import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { useMeContext } from "@/hooks/useMeContext";
import { ROUTES } from "@/constants/routes";
import { loginPathWithRedirect } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";

const Splash: React.FC = () => (
  <div className="flex h-screen w-full items-center justify-center bg-background">
    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
  </div>
);

export const useIsSuperAdmin = (): boolean =>
  useAuthStore((state) => Boolean(state.user?.is_superuser));

export const SuperAdminGuard: React.FC = () => {
  const location = useLocation();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const isSuperAdmin = useIsSuperAdmin();
  const { isLoading, isUnauthorized } = useMeContext();

  if (!isAuthenticated || isUnauthorized) {
    return (
      <Navigate
        to={loginPathWithRedirect(location.pathname)}
        replace
      />
    );
  }

  // Wait for the bootstrap before judging. Rendering the redirect first would
  // bounce a superadmin off their own page on every hard refresh, before the
  // user object has been fetched.
  if (isLoading) {
    return <Splash />;
  }

  if (!isSuperAdmin) {
    return <Navigate to={ROUTES.WORKSPACES} replace />;
  }

  return <Outlet />;
};

export default SuperAdminGuard;
