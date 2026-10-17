import React from "react";

export interface PillBar {
  readonly key: string;
  /** The short label under the bar ("12 Oct"). */
  readonly label: string;
  readonly value: number;
  /** The value in words, for the tooltip and the table ("₹4,20,000.00 in 3 payments"). */
  readonly words: string;
}

/**
 * Pill-shaped bars (docs/helm/VISUAL.md "PillBars"): one rounded bar per period, grown from the
 * bottom on first view. The bars are decoration; the same numbers are in a table for screen readers.
 */
export const PillBars: React.FC<{
  readonly bars: readonly PillBar[];
  readonly caption: string;
  readonly height?: number;
  readonly valueHeader?: string;
  readonly periodHeader?: string;
  readonly testId?: string;
}> = ({ bars, caption, height = 112, valueHeader = "Amount", periodHeader = "Week", testId }) => {
  const max = Math.max(0, ...bars.map((b) => b.value));
  return (
    <figure className="m-0" data-testid={testId}>
      <div className="helm-pillbars" style={{ height }} aria-hidden="true">
        {bars.map((bar, index) => (
          <div key={bar.key} className="flex h-full min-w-0 flex-col items-stretch gap-1.5" title={`${bar.label}: ${bar.words}`}>
            <div className="helm-pillbar-track mx-auto w-full max-w-[2.5rem] flex-1">
              {bar.value > 0 ? (
                <div
                  className={`helm-pillbar ${bar.value === max ? "" : "helm-pillbar-quiet"}`}
                  style={{ height: `${Math.max(6, (bar.value / (max || 1)) * 100)}%`, ["--i" as string]: index }}
                />
              ) : null}
            </div>
            <span className="helm-faint truncate text-center text-[10px] font-medium">{bar.label}</span>
          </div>
        ))}
      </div>
      <table className="sr-only">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">{periodHeader}</th>
            <th scope="col">{valueHeader}</th>
          </tr>
        </thead>
        <tbody>
          {bars.map((bar) => (
            <tr key={bar.key}>
              <th scope="row">{bar.label}</th>
              <td>{bar.words}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
};

export default PillBars;
