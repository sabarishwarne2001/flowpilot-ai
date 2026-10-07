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
      className={`relative mx-auto flex max-w-sm flex-col items-center justify-center overflow-hidden rounded-xl border border-destructive/25 bg-card/60 px-8 py-10 text-center select-none animate-fade-in ${className}`}
      role="alert"
      aria-live="assertive"
      aria-labelledby="error-state-title"
      aria-describedby="error-state-desc"
    >
      {/* Visual Error Icon Container */}
      <div className="mb-4 flex h-12 w-12 items-center justify-center rounded-xl border border-destructive/25 bg-destructive/10 text-destructive">
        <AlertCircle className="h-5 w-5 flex-shrink-0" />
      </div>

      {/* Main Descriptions Labels */}
      <h3
        id="error-state-title"
        className="mb-1 text-sm font-semibold tracking-tight text-foreground"
      >
        {title}
      </h3>

      <p
        id="error-state-desc"
        className="mb-5 max-w-[280px] text-[13px] leading-relaxed text-muted-foreground"
      >
        {description}
      </p>

      {/* Active Retry Execution Button */}
      <button
        type="button"
        onClick={onRetry}
        className="fp-btn fp-btn-secondary text-[13px]"
      >
        <RefreshCw className="h-3.5 w-3.5 flex-shrink-0" />
        <span>Retry Connection</span>
      </button>
    </div>
  );
};

export default ErrorState;
