import { Navigate, Outlet } from "react-router-dom";

import { ROUTES } from "@/constants/routes";
import { useAuthStore } from "@/store/useAuthStore";

interface PublicRouteProps {
  readonly children?: React.ReactNode;
}

export function PublicRoute({ children }: PublicRouteProps) {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  if (isAuthenticated) {
    return <Navigate to={ROUTES.DASHBOARD} replace />;
  }

  if (children) {
    return <>{children}</>;
  }

  return <Outlet />;
}

export default PublicRoute;
