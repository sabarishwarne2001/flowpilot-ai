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
      className={`pointer-events-none overflow-hidden fp-card p-6 select-none ${className}`}
    >
      {/* Header */}

      <div className="flex items-center space-x-4">
        <div className="h-10 w-10 flex-shrink-0 fp-skeleton rounded-lg" />

        <div className="flex-1 space-y-2">
          <div className="h-3.5 w-1/3 fp-skeleton rounded" />
          <div className="h-2.5 w-1/4 fp-skeleton rounded" />
        </div>
      </div>

      {/* Body */}

      <div className="space-y-2 pt-6">
        <div className="h-3 w-full fp-skeleton rounded" />
        <div className="h-3 w-11/12 fp-skeleton rounded" />
        <div className="h-3 w-3/4 fp-skeleton rounded" />
      </div>

      {/* Footer */}

      <div className="flex items-center justify-between border-t border-border/40 pt-6">
        <div className="h-3 w-1/4 fp-skeleton rounded" />
        <div className="h-3 w-1/6 fp-skeleton rounded" />
      </div>
    </div>
  );
};

export default SkeletonCard;
