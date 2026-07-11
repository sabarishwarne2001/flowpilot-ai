import React from "react";
import ReactDOM from "react-dom/client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import App from "./App";

import "@/styles/index.css";

import { ApiError } from "@/services/api/client";

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

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </React.StrictMode>
);
