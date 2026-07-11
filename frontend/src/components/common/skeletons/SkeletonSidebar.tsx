import React from "react";

interface SkeletonSidebarProps {
  readonly itemsCount?: number;
  readonly className?: string;
}

export const SkeletonSidebar: React.FC<SkeletonSidebarProps> = ({
  itemsCount = 5,
  className = "",
}) => {
  const count = Math.max(1, Math.min(10, itemsCount));

  const dummyItems = Array.from({
    length: count,
  });

  return (
    <aside
      role="presentation"
      aria-hidden="true"
      aria-label="Loading navigation"
      className={`pointer-events-none flex min-h-dvh w-64 flex-col justify-between overflow-hidden overflow-y-hidden border-r border-border/60 bg-card p-4 select-none dark:border-border/40 ${className}`}
    >
      {/* ===========================
          Brand
      =========================== */}

      <div>
        <div className="mb-4 flex h-12 items-center space-x-3 border-b border-border/20 px-2 pb-4">
          <div className="h-8 w-8 flex-shrink-0 animate-pulse rounded-lg bg-muted/60 dark:bg-muted/15" />

          <div className="h-4 w-24 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
        </div>

        {/* ===========================
            Navigation
        =========================== */}

        <nav
          aria-label="Loading navigation placeholder"
          className="space-y-4 px-2"
        >
          {dummyItems.map((_, index) => (
            <div
              key={`skeleton-sidebar-${index}`}
              className="flex items-center space-x-3.5 py-1"
            >
              <div className="h-5 w-5 flex-shrink-0 animate-pulse rounded-md bg-muted/40 dark:bg-muted/10" />

              <div className="h-3 w-2/3 animate-pulse rounded bg-muted/40 dark:bg-muted/10" />
            </div>
          ))}
        </nav>
      </div>

      {/* ===========================
          User Profile
      =========================== */}

      <div className="flex items-center space-x-3 rounded-xl border-t border-border/20 bg-muted/10 p-3 dark:bg-muted/5">
        <div className="h-9 w-9 flex-shrink-0 animate-pulse rounded-full bg-muted/40 dark:bg-muted/10" />

        <div className="min-w-0 flex-1 space-y-2">
          <div className="h-3 w-16 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />

          <div className="h-2.5 w-28 animate-pulse truncate rounded bg-muted/40 dark:bg-muted/10" />
        </div>
      </div>
    </aside>
  );
};

export default SkeletonSidebar;
