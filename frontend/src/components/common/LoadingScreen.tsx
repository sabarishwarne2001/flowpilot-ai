import React from "react";
import { Loader2 } from "lucide-react";

/**
 * Full-viewport, stateless application loading screen for FlowPilot AI.
 *
 * Provides automated transitions, aligns with system design languages,
 * and complies with accessibility guidelines during startup or lazy-loads transitions.
 */
export const LoadingScreen: React.FC = () => {
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

        {/* Unified Typography Branding Logo */}
        <div className="flex items-center space-x-2">
          <div className="h-6 w-6 rounded bg-primary flex items-center justify-center shadow-sm">
            <span className="font-mono text-xs font-black text-primary-foreground">FP</span>
          </div>
          <span className="text-lg font-black tracking-tight font-sans">
            FlowPilot<span className="text-primary font-black">AI</span>
          </span>
        </div>

        {/* Loading Indicators Text Pane */}
        <div className="space-y-1.5">
          <h1 className="text-sm font-extrabold text-foreground">
            Loading FlowPilot AI...
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
