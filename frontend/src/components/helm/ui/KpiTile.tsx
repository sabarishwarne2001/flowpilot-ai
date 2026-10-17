import React from "react";

import { useCountUp } from "@/components/helm/ui/motion";

/**
 * A KPI tile (docs/helm/VISUAL.md "KpiTile"): a label, a big figure that counts up to the API's
 * exact value, an optional suffix ("/ 12"), a line of detail, an optional delta badge and a visual
 * (a ring or a sparkline). Screen readers get the exact figure at once (the counting text is hidden
 * from them); the visible text ends on exactly the same string.
 */
export const KpiTile: React.FC<{
  readonly label: string;
  readonly value: number;
  readonly format: (value: number) => string;
  readonly suffix?: React.ReactNode;
  readonly detail?: React.ReactNode;
  readonly delta?: React.ReactNode;
  readonly visual?: React.ReactNode;
  readonly index?: number;
  readonly testId?: string;
  readonly tone?: "default" | "primary";
}> = ({ label, value, format, suffix, detail, delta, visual, index = 0, testId, tone = "default" }) => {
  const shown = useCountUp(value);
  // Integers count in whole steps; a money figure rounds each frame, the last frame is `value`.
  const text = format(shown === value ? value : Number.isInteger(value) ? Math.round(shown) : shown);
  const exact = format(value);
  return (
    <li
      className={`helm-panel helm-enter flex min-w-0 flex-col gap-2.5 p-4 ${tone === "primary" ? "helm-panel-primary" : ""}`}
      style={{ ["--i" as string]: index }}
      data-testid={testId}
    >
      <div className="flex min-h-[2.5rem] items-start justify-between gap-2">
        <p className="helm-label leading-snug">{label}</p>
        {visual ? <div className="-mr-1 -mt-1 shrink-0">{visual}</div> : null}
      </div>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <p className="min-w-0 max-w-full">
          <span
            className="helm-numeral block whitespace-nowrap text-[1.85rem] sm:text-[2.1rem]"
            aria-hidden="true"
            data-testid={testId ? `${testId}-value` : undefined}
            data-value={exact}
          >
            {text}
            {suffix ? <span className="helm-faint ml-1 text-[1.05rem] font-medium tracking-normal">{suffix}</span> : null}
          </span>
          <span className="sr-only">
            {exact}
            {typeof suffix === "string" ? ` ${suffix}` : ""}
          </span>
        </p>
        {delta ?? null}
      </div>
      {detail ? <div className="helm-muted text-xs leading-relaxed">{detail}</div> : null}
    </li>
  );
};

export default KpiTile;
