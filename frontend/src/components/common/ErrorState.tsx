import React from "react";
import { AlertCircle, RefreshCw } from "lucide-react";

interface ErrorStateProps {
  /**
   * Optional custom error header. Defaults to "Workspace sync failed".
   */
  readonly title?: string;
  /**
   * Detailed descriptive message detailing the connectivity fault or schema validation issue.
   */
  readonly description: string;
  /**
   * Safe closure callback triggered to re-route requests or refetch TanStack Query caches.
   */
  readonly onRetry: () => void;
  readonly className?: string;
}

/**
 * Universal, stateless Error Fallback Presenter Card for FlowPilot AI.
 *
 * Provides consistent failure typography, error vectors, and standardized retry triggers
 * to maintain high user engagement during transient API bottlenecks.
 */
export const ErrorState: React.FC<ErrorStateProps> = ({
  title = "Workspace sync failed",
  description,
  onRetry,
  className = "",
}) => {
  return (
    <div
      className={`flex flex-col items-center justify-center text-center p-8 bg-card border border-border/60 dark:border-border/40 rounded-xl max-w-sm mx-auto shadow-sm select-none animate-fade-in ${className}`}
      role="alert"
      aria-live="assertive"
      aria-labelledby="error-state-title"
      aria-describedby="error-state-desc"
    >
      {/* Visual Error Icon Container */}
      <div className="p-4 bg-destructive/10 text-destructive rounded-full mb-4">
        <AlertCircle className="h-7 w-7 flex-shrink-0" />
      </div>

      {/* Main Descriptions Labels */}
      <h3
        id="error-state-title"
        className="text-sm font-extrabold tracking-tight text-foreground/90 font-sans mb-1.5"
      >
        {title}
      </h3>

      <p
        id="error-state-desc"
        className="text-xs text-muted-foreground font-semibold leading-relaxed mb-5 max-w-[280px]"
      >
        {description}
      </p>

      {/* Active Retry Execution Button */}
      <button
        type="button"
        onClick={onRetry}
        className="inline-flex items-center px-4 py-2 bg-primary text-primary-foreground font-bold text-xs rounded-lg hover:bg-primary/95 transition-all shadow-sm active:scale-[0.98] outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <RefreshCw className="h-3.5 w-3.5 mr-2 flex-shrink-0" />
        <span>Retry Connection</span>
      </button>
    </div>
  );
};

export default ErrorState;
