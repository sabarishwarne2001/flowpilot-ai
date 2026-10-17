import React from "react";

/**
 * A status pill with a live dot that breathes (docs/helm/VISUAL.md "StatusPill"): "● Propose mode ·
 * Guard armed". The dot is decoration; the words say the state.
 */
export const StatusPill: React.FC<{
  readonly tone?: "holding" | "drifting" | "breached" | "sky" | "unknown";
  readonly live?: boolean;
  readonly children: React.ReactNode;
  readonly className?: string;
}> = ({ tone = "holding", live = true, children, className = "" }) => (
  <span
    className={`helm-chip helm-tone-${tone} gap-2 px-3 py-1 text-[13px] ${className}`}
    data-testid="helm-status-pill"
  >
    <span className={`helm-state-${tone} inline-flex`} aria-hidden="true">
      <span className={live ? "helm-live-dot" : "helm-live-dot helm-live-still"} />
    </span>
    <span className="font-semibold">{children}</span>
  </span>
);

export default StatusPill;
