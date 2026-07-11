import { Navigate, Outlet, useLocation } from "react-router-dom";

import { ROUTES } from "@/constants/routes";
import { useAuthStore } from "@/store/useAuthStore";

interface PrivateRouteProps {
  readonly children?: React.ReactNode;
}

export function PrivateRoute({ children }: PrivateRouteProps) {
  const location = useLocation();

  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);

  if (!isAuthenticated) {
    return (
      <Navigate
        to={ROUTES.LOGIN}
        replace
        state={{
          from: location,
        }}
      />
    );
  }

  if (children) {
    return <>{children}</>;
  }

  return <Outlet />;
}

export default PrivateRoute;
