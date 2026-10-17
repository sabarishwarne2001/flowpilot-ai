import React, { useId } from "react";

export type SparkTone = "holding" | "drifting" | "breached" | "unknown" | "accent" | "aqua";

/**
 * A small line with a soft area under it and its last point marked. Decoration for a figure that
 * is also in words: the caller passes `label` (the trend in a sentence) or leaves it hidden.
 */
export const Sparkline: React.FC<{
  readonly values: readonly number[];
  readonly tone?: SparkTone;
  readonly width?: number;
  readonly height?: number;
  readonly label?: string;
  readonly className?: string;
}> = ({ values, tone = "accent", width = 120, height = 32, label, className = "" }) => {
  const gradient = useId();
  if (values.length < 2) {
    return null;
  }
  const pad = 3;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const x = (i: number): number => pad + (i * (width - 2 * pad)) / (values.length - 1);
  const y = (v: number): number => height - pad - ((v - min) * (height - 2 * pad)) / span;
  const line = values.map((v, i) => `${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `M${x(0).toFixed(1)},${height} L${line.split(" ").join(" L")} L${x(values.length - 1).toFixed(1)},${height} Z`;
  const last = values[values.length - 1] as number;
  return (
    <svg
      viewBox={`0 0 ${width} ${height}`}
      width={width}
      height={height}
      className={`helm-spark-${tone} block overflow-visible ${className}`}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : "true"}
    >
      <defs>
        <linearGradient id={gradient} x1="0" x2="0" y1="0" y2="1">
          <stop offset="0" stopColor="currentColor" stopOpacity="0.28" />
          <stop offset="1" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradient})`} stroke="none" />
      <polyline points={line} className={`helm-spark-line helm-spark-${tone}`} />
      <circle cx={x(values.length - 1)} cy={y(last)} r={3} className="helm-spark-end" />
    </svg>
  );
};

export default Sparkline;
