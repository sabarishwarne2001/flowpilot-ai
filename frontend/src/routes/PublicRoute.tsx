/**
 * Public route guard for FlowPilot AI.
 *
 * Keeps authenticated users off the login and registration screens.
 *
 * The change from the previous implementation is that the intended destination
 * is now honoured. Before, an authenticated user arriving at
 * /login?redirect=/acme/engineering/work-items was sent to the dashboard and
 * the destination was discarded — most visibly when an already-signed-in user
 * clicked an invitation link that routed through login.
 */

import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ROUTES } from "@/constants/routes";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";

interface PublicRouteProps {
  readonly children?: React.ReactNode;
}

export function PublicRoute({ children }: PublicRouteProps) {
  const location = useLocation();
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  if (isAuthenticated) {
    const requested = new URLSearchParams(location.search).get("redirect");

    // Validated structurally, so a tenant deep link is accepted while an
    // off-origin value is not. See isSafeRedirectPath.
    const destination = isSafeRedirectPath(requested)
      ? (requested as string)
      : ROUTES.DASHBOARD;

    return <Navigate to={destination} replace />;
  }

  if (children) {
    return <>{children}</>;
  }

  return <Outlet />;
}

export default PublicRoute;
