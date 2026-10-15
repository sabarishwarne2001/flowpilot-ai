import React from "react";

import { formatCalendarDate } from "@/utils/displayTime";
import type { TrendPoint } from "@/types/helm";

/**
 * An instrument's small trend: its key figure, one point a day, as a thin line in ink (the state
 * badge beside it already says holding, drifting or breached in words, so the line carries no
 * status colour). The last point is marked; each point answers on hover; the whole trend is also
 * said in words for screen readers. Nothing is drawn for fewer than two days.
 */
const W = 112;
const H = 28;
const PAD = 4;

export const InstrumentTrend: React.FC<{ readonly points: readonly TrendPoint[]; readonly title: string }> = ({ points, title }) => {
  if (points.length < 2) {
    return null;
  }
  const values = points.map((p) => p.value);
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number): number => PAD + (i * (W - 2 * PAD)) / (points.length - 1);
  const y = (v: number): number => H - PAD - ((v - min) * (H - 2 * PAD)) / span;
  const first = points[0] as TrendPoint;
  const last = points[points.length - 1] as TrendPoint;
  const direction = last.value > first.value ? "rose" : last.value < first.value ? "fell" : "held steady";
  const words = `${title}: over ${points.length} days, from ${formatCalendarDate(first.on)} to ${formatCalendarDate(last.on)}, its figure ${direction}.`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={words} className="helm-trend mt-2 block overflow-visible">
      <polyline
        points={points.map((p, i) => `${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(" ")}
        fill="none"
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
        className="helm-trend-line"
      />
      <circle cx={x(points.length - 1)} cy={y(last.value)} r={4} className="helm-trend-end" />
      {points.map((p, i) => (
        <rect key={p.on} x={x(i) - (W - 2 * PAD) / (2 * (points.length - 1))} y={0} width={(W - 2 * PAD) / (points.length - 1)} height={H} fill="transparent">
          <title>{`${formatCalendarDate(p.on)}: ${p.value.toLocaleString()}`}</title>
        </rect>
      ))}
    </svg>
  );
};

export default InstrumentTrend;
