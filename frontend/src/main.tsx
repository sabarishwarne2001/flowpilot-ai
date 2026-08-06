import React from "react";
import ReactDOM from "react-dom/client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { WorkspaceProvider } from "@/context/WorkspaceContext";

import App from "./App";

import "@/styles/index.css";

import { ApiError } from "@/services/api/client";

import { assertPermissionParity } from "@/permissions";

import { assertTenantResolutionIntegrity } from "@/hooks/tenantResolution";

import { assertTenantPathIntegrity } from "@/routes/tenantPaths";

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

// Verifies the client permission mirror still agrees with the backend
// contract. DEV-gated: Vite statically evaluates import.meta.env.DEV and
// tree-shakes both the call and the entire selfCheck module out of production
// bundles.
//
// The alternative to this check is silent drift — a helper that disagrees with
// the server produces buttons that 403, and nothing fails loudly enough for
// anyone to notice.
if (import.meta.env.DEV) {
  assertPermissionParity();
  assertTenantResolutionIntegrity();
  assertTenantPathIntegrity();
}

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <WorkspaceProvider>
        <App />
      </WorkspaceProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
