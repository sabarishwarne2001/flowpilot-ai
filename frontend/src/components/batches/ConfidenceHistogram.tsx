/**
 * Phase 1 — how many documents (or fields) fell in each confidence band.
 *
 * One series, one hue: the title says what is counted, so there is no legend. Columns are thin
 * with a rounded cap on a single baseline, each non-empty column carries its count on the cap,
 * and hovering or focusing a column shows the band and its share. A table view carries the same
 * numbers for anyone who cannot read the bars.
 */
import React, { useState } from "react";

import type { HistogramBucket } from "@/types/batches";

interface Props {
  readonly buckets: readonly HistogramBucket[];
  readonly unit: string;
  readonly title: string;
  readonly testId?: string;
}

export const ConfidenceHistogram: React.FC<Props> = ({ buckets, unit, title, testId }) => {
  const [active, setActive] = useState<number | null>(null);
  const total = buckets.reduce((sum, b) => sum + b.count, 0);
  const max = Math.max(...buckets.map((b) => b.count), 1);
  if (total === 0) {
    return (
      <div className="flex h-44 items-center justify-center rounded-lg border border-dashed border-border text-center text-xs text-muted-foreground">
        No {unit} carry a confidence score yet. Scores come from verification (Settings → Documents).
      </div>
    );
  }
  return (
    <figure className="space-y-2" data-testid={testId}>
      <figcaption className="sr-only">{title}</figcaption>
      <div className="relative">
        <div className="flex h-44 items-end gap-1 border-b border-border px-1" role="list" aria-label={title}>
          {buckets.map((bucket, index) => {
            const height = (bucket.count / max) * 100;
            const share = total ? Math.round((bucket.count / total) * 100) : 0;
            return (
              <div
                key={bucket.label}
                role="listitem"
                tabIndex={0}
                aria-label={`${bucket.label}: ${bucket.count} ${unit} (${share}%)`}
                onMouseEnter={() => setActive(index)}
                onMouseLeave={() => setActive(null)}
                onFocus={() => setActive(index)}
                onBlur={() => setActive(null)}
                className="group relative flex h-full flex-1 flex-col items-center justify-end outline-none"
              >
                {bucket.count > 0 ? (
                  <span className="fp-num mb-1 text-[11px] font-medium text-muted-foreground">{bucket.count}</span>
                ) : null}
                <span
                  className={`w-full max-w-[24px] rounded-t-[4px] transition-colors ${
                    active === index ? "bg-primary" : "bg-primary/70"
                  } group-focus-visible:ring-2 group-focus-visible:ring-primary/50`}
                  style={{ height: bucket.count ? `${Math.max(height, 3)}%` : "0%" }}
                />
                {active === index ? (
                  <span
                    role="tooltip"
                    className="pointer-events-none absolute bottom-full z-10 mb-2 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-[11px] text-popover-foreground shadow-elevation-2"
                  >
                    <span className="font-semibold">{bucket.label}</span> · {bucket.count} {unit} · {share}%
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
        <div className="flex gap-1 px-1 pt-1.5">
          {buckets.map((bucket) => (
            <span key={bucket.label} className="flex-1 text-center text-[10px] leading-tight text-muted-foreground">
              {bucket.label}
            </span>
          ))}
        </div>
      </div>
      <details className="text-xs">
        <summary className="cursor-pointer select-none text-muted-foreground hover:text-foreground">Show as a table</summary>
        <table className="mt-2 w-full text-left">
          <thead>
            <tr className="text-muted-foreground">
              <th className="py-1 font-medium">Confidence</th>
              <th className="py-1 text-right font-medium">{unit[0]?.toUpperCase() + unit.slice(1)}</th>
              <th className="py-1 text-right font-medium">Share</th>
            </tr>
          </thead>
          <tbody>
            {buckets.map((bucket) => (
              <tr key={bucket.label} className="border-t border-border/60">
                <td className="py-1">{bucket.label}</td>
                <td className="fp-num py-1 text-right">{bucket.count}</td>
                <td className="fp-num py-1 text-right">{total ? Math.round((bucket.count / total) * 100) : 0}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
};

export default ConfidenceHistogram;
