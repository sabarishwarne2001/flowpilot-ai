import React from "react";
import { Loader2 } from "lucide-react";

import { Brand } from "@/components/branding/Brand";
import { useOptionalTenant } from "@/routes/TenantContext";

/**
 * Full-viewport, stateless application loading screen for FlowPilot AI.
 *
 * Provides automated transitions, aligns with system design languages,
 * and complies with accessibility guidelines during startup or lazy-loads transitions.
 *
 * ARCH-01 replaced the workspace context with optional tenant context. This is
 * the Suspense fallback in App.tsx, mounted ABOVE every guard, so it renders
 * during the initial load of every session — before any tenant is resolved and
 * outside the provider entirely. useResolvedTenant would throw there by
 * design, which is why this one reads the optional variant.
 */
export const LoadingScreen: React.FC = () => {
  const tenant = useOptionalTenant();

  return (
    <div
      className="min-h-dvh w-full flex items-center justify-center p-6 bg-background text-foreground transition-colors duration-200 select-none"
      role="status"
      aria-live="polite"
      aria-label="Loading application"
    >
      <div className="flex flex-col items-center text-center space-y-4 max-w-sm">
        {/* Animated Loading Spinner Container */}
        <div className="p-3 bg-primary/10 text-primary rounded-xl mb-1 flex items-center justify-center shadow-sm">
          <Loader2
            className="h-8 w-8 animate-spin"
            aria-hidden="true"
          />
        </div>

        <Brand variant="loading" />

        {/* Loading Indicators Text Pane */}
        <div className="space-y-1.5">
          <h1 className="text-sm font-extrabold text-foreground">
            Loading {tenant?.workspace.workspace_name ?? "Workspace"}...
          </h1>
          <p className="text-xs text-muted-foreground font-semibold leading-relaxed">
            Preparing your workspace.
          </p>
        </div>
      </div>
    </div>
  );
};

export default LoadingScreen;
