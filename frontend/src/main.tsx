import React from "react";
import ReactDOM from "react-dom/client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";

import "@/styles/index.css";

import { ApiError } from "@/services/api/client";

import { assertPermissionParity } from "@/permissions";

import { assertTenantResolutionIntegrity } from "@/hooks/tenantResolution";

import { assertTenantPathIntegrity } from "@/routes/tenantPaths";

import { assertTenantReconciliationIntegrity } from "@/routes/tenantReconciliation";

/**
 * Application entry point for FlowPilot AI.
 *
 * ARCH-01 removed WorkspaceProvider from this tree. It sat above <App /> and
 * therefore fetched on every page load regardless of route — including the
 * login screen, where no tenant exists. It called GET /api/v1/workspace for an
 * authenticated visitor and GET /api/v1/workspace/public for an anonymous one;
 * both endpoints were deleted in the backend transformation, so both 404 now.
 *
 * The public variant deserved deletion on its own merits: it returned the
 * oldest workspace row in the database to any visitor, disclosing one tenant's
 * name, logo, and locale to everyone who reached the login page.
 *
 * Tenant data now enters the tree through TenantGuard, which mounts inside
 * PrivateRoute on workspace-scoped routes only. Tenancy is fetched when a
 * tenant is in scope, and never before.
 */

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 1000 * 60 * 5,

      gcTime: 1000 * 60 * 30,

      refetchOnWindowFocus: true,

      refetchOnReconnect: "always",

      refetchOnMount: "always",

      retry: (failureCount, error) => {
        if (error instanceof ApiError) {
          if (
            error.status === 401 ||
            error.status === 403 ||
            error.status === 404
          ) {
            return false;
          }
        }

        return failureCount < 2;
      },
    },
    mutations: {
      retry: false,
    },
  },
});

// Verifies the client mirrors of backend logic still agree with their
// counterparts. DEV-gated: Vite statically evaluates import.meta.env.DEV and
// tree-shakes these calls out of production bundles.
//
// The alternative is silent drift — a helper that disagrees with the server
// produces buttons that 403, or routes a user somewhere unexpected, and
// nothing fails loudly enough for anyone to notice.
if (import.meta.env.DEV) {
  assertPermissionParity();
  assertTenantResolutionIntegrity();
  assertTenantPathIntegrity();
  assertTenantReconciliationIntegrity();
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
