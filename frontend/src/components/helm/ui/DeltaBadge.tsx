import React from "react";
import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";

/**
 * A small delta pill, "▲ 2 since last week". Only for a delta that is in the data: a caller that
 * has none leaves the badge out rather than inventing one. `good` says whether the change is good
 * news (more mandates holding is; more money at risk is not), which picks the colour; the arrow
 * and the words carry the meaning.
 */
export const DeltaBadge: React.FC<{
  readonly direction: "up" | "down" | "flat";
  readonly good: boolean | null;
  readonly children: React.ReactNode;
  readonly label?: string;
}> = ({ direction, good, children, label }) => {
  const Icon = direction === "up" ? ArrowUpRight : direction === "down" ? ArrowDownRight : Minus;
  const tone = good === null || direction === "flat" ? "unknown" : good ? "holding" : "breached";
  return (
    <span className={`helm-chip helm-tone-${tone} helm-state-${tone} gap-1 px-2 text-[11px] font-semibold`} aria-label={label}>
      <Icon className="h-3 w-3" aria-hidden="true" />
      <span className="helm-figure">{children}</span>
    </span>
  );
};

export default DeltaBadge;
