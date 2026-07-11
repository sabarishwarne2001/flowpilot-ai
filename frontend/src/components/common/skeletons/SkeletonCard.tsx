import React from "react";

interface SkeletonCardProps {
  readonly className?: string;
}

export const SkeletonCard: React.FC<SkeletonCardProps> = ({
  className = "",
}) => {
  return (
    <div
      role="presentation"
      aria-hidden="true"
      aria-label="Loading"
      className={`pointer-events-none overflow-hidden rounded-xl border border-border/60 bg-card p-6 shadow-sm select-none dark:border-border/40 ${className}`}
    >
      {/* Header */}

      <div className="flex items-center space-x-4">
        <div className="h-10 w-10 flex-shrink-0 animate-pulse rounded-lg bg-muted/60 dark:bg-muted/15" />

        <div className="flex-1 space-y-2">
          <div className="h-3.5 w-1/3 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
          <div className="h-2.5 w-1/4 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
        </div>
      </div>

      {/* Body */}

      <div className="space-y-2 pt-6">
        <div className="h-3 w-full animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
        <div className="h-3 w-11/12 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
        <div className="h-3 w-3/4 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
      </div>

      {/* Footer */}

      <div className="flex items-center justify-between border-t border-border/40 pt-6">
        <div className="h-3 w-1/4 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
        <div className="h-3 w-1/6 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
      </div>
    </div>
  );
};

export default SkeletonCard;
