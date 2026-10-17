import React from "react";

import { HelmMark } from "@/components/helm/ui/HelmMark";

/** Helm's loading state: glass skeletons and the mark turning, never a bare spinner. */
export const HelmSkeleton: React.FC<{ readonly label: string }> = ({ label }) => (
  <div className="mx-auto max-w-7xl space-y-4" role="status" aria-live="polite">
    <p className="helm-muted flex items-center gap-2 text-sm">
      <HelmMark size={18} spinning className="helm-accent" />
      {label}
    </p>
    <div className="grid gap-3 sm:grid-cols-3 xl:grid-cols-6" aria-hidden="true">
      {Array.from({ length: 6 }, (_, index) => (
        <div key={index} className="helm-skeleton h-28" />
      ))}
    </div>
    <div className="grid gap-4 lg:grid-cols-12" aria-hidden="true">
      <div className="helm-skeleton h-72 lg:col-span-8" />
      <div className="helm-skeleton h-72 lg:col-span-4" />
    </div>
  </div>
);

export default HelmSkeleton;
