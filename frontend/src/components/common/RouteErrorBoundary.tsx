import React from "react";
import { useLocation } from "react-router-dom";

import { ErrorBoundary } from "@/components/common/ErrorBoundary";

/**
 * The content area's error boundary. A page that throws while rendering shows
 * an in-page error card instead of replacing the whole application: the
 * sidebar, header and every other page keep working, and moving to another
 * route clears the error. Before this, the only boundary sat above the router,
 * so one page's error blanked the shell and "Retry" re-rendered the same crash.
 */
export const RouteErrorBoundary: React.FC<{ readonly children: React.ReactNode }> = ({ children }) => {
  const location = useLocation();
  return (
    <ErrorBoundary variant="page" resetKey={location.pathname}>
      {children}
    </ErrorBoundary>
  );
};

export default RouteErrorBoundary;
