/**
 * Authentication guard for FlowPilot AI.
 *
 * The previous implementation trusted a single rehydrated flag:
 *
 *   const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
 *   if (!isAuthenticated) return <Navigate to={ROUTES.LOGIN} />;
 *
 * That flag comes from localStorage via zustand persist and says only "this
 * browser once held a session". After token expiry a hard refresh renders the
 * entire authenticated shell, and every request beneath it then 401s — the
 * session-bootstrap finding in the architecture audit.
 *
 * This guard validates the session against the server before rendering
 * anything. The persisted flag is retained only as a fast negative: no token
 * at all means no request is worth making.
 *
 * Scope is authentication ONLY. Tenancy is TenantGuard's concern, so routes
 * that require a session but no tenant — onboarding, the workspace picker —
 * mount under this guard alone.
 */

import React from "react";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import { Loader2 } from "lucide-react";

import { useMeContext } from "@/hooks/useMeContext";
import { loginPathWithRedirect } from "@/routes/tenantPaths";
import { useAuthStore } from "@/store/useAuthStore";

interface PrivateRouteProps {
  readonly children?: React.ReactNode;
}

const SessionSplash: React.FC = () => (
  <div className="flex h-screen w-full items-center justify-center bg-background">
    <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
  </div>
);

export function PrivateRoute({ children }: PrivateRouteProps) {
  const location = useLocation();

  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const { context, isLoading, isUnauthorized } = useMeContext();

  const destination = `${location.pathname}${location.search}`;

  // Fast negative: no persisted session means no request is worth making.
  if (!isAuthenticated) {
    return <Navigate to={loginPathWithRedirect(destination)} replace />;
  }

  // The server rejected the token. The interceptor has already cleared local
  // state; this routes to login with the destination preserved.
  if (isUnauthorized) {
    return <Navigate to={loginPathWithRedirect(destination)} replace />;
  }

  // Validation in flight. Render a splash rather than the authenticated shell
  // — rendering it optimistically is precisely what produced the "app loads,
  // then every request fails" behaviour.
  if (isLoading || !context) {
    return <SessionSplash />;
  }

  if (children) {
    return <>{children}</>;
  }

  return <Outlet />;
}

export default PrivateRoute;
