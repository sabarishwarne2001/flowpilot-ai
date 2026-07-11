import React from "react";
import { SkeletonCard } from "@/components/common/skeletons/SkeletonCard";
import { SkeletonSidebar } from "@/components/common/skeletons/SkeletonSidebar";
import { SkeletonTable } from "@/components/common/skeletons/SkeletonTable";

interface SkeletonDashboardProps {
  readonly className?: string;
}

export const SkeletonDashboard: React.FC<SkeletonDashboardProps> = ({
  className = "",
}) => {
  return (
    <div
      role="presentation"
      aria-hidden="true"
      aria-label="Loading dashboard"
      className={`pointer-events-none flex min-h-dvh overflow-hidden overflow-x-hidden bg-background text-foreground select-none ${className}`}
    >
      {/* ===========================
          Desktop Sidebar
      =========================== */}

      <div className="hidden lg:block">
        <SkeletonSidebar />
      </div>

      {/* ===========================
          Main Workspace
      =========================== */}

      <div className="flex min-h-dvh min-w-0 flex-1 flex-col overflow-hidden">
        <header className="flex h-16 items-center justify-between border-b border-border/40 bg-card px-6">
          <div className="flex items-center space-x-4">
            <div className="h-9 w-9 animate-pulse rounded-lg bg-muted/40 dark:bg-muted/10 lg:hidden" />

            <div className="h-4 w-36 animate-pulse rounded bg-muted/60 dark:bg-muted/15" />
          </div>

          <div className="flex items-center space-x-3">
            <div className="h-9 w-9 animate-pulse rounded-lg bg-muted/40 dark:bg-muted/10" />

            <div className="h-9 w-9 animate-pulse rounded-lg bg-muted/40 dark:bg-muted/10" />
          </div>
        </header>
        <main className="flex-1 space-y-6 overflow-y-auto bg-muted/10 p-6 dark:bg-background">
          {/* ===========================
              Metrics
          =========================== */}

          <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
            <SkeletonCard />
            <SkeletonCard />
            <SkeletonCard />
          </div>

          {/* ===========================
              Main Table
          =========================== */}

          <SkeletonTable rows={6} />
        </main>
      </div>
    </div>
  );
};

export default SkeletonDashboard;
