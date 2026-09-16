import React from "react";

import { HINT } from "@/components/ui/primitives";
import type {
  CoveragePoint,
  FittedCurvePoint,
  ReliabilityBin,
} from "@/types/autonomy";
import { formatRate } from "@/types/autonomy";

/**
 * ARCH-35 §6.7 — the two charts the Autonomy console is built around.
 *
 * Hand-drawn SVG, like ARCH-34's price chart: no chart library, no new
 * dependency, and every coordinate is a number the server already computed at
 * fit time. Nothing here re-fits or re-estimates anything.
 */

const WIDTH = 320;
const HEIGHT = 220;
const PAD = 32;

const x = (value: number): number => PAD + value * (WIDTH - PAD * 1.5);
const y = (value: number, max = 1): number =>
  HEIGHT - PAD - (Math.min(value, max) / max) * (HEIGHT - PAD * 1.5);

const Axes: React.FC<{ readonly xLabel: string; readonly yLabel: string; readonly yMax?: number }> = ({
  xLabel,
  yLabel,
  yMax = 1,
}) => (
  <g className="text-muted-foreground">
    <line x1={PAD} y1={HEIGHT - PAD} x2={WIDTH - PAD / 2} y2={HEIGHT - PAD} stroke="currentColor" strokeOpacity={0.4} />
    <line x1={PAD} y1={PAD / 2} x2={PAD} y2={HEIGHT - PAD} stroke="currentColor" strokeOpacity={0.4} />
    {[0, 0.5, 1].map((tick) => (
      <g key={`x-${tick}`}>
        <text x={x(tick)} y={HEIGHT - PAD + 14} fontSize={9} textAnchor="middle" fill="currentColor">
          {Math.round(tick * 100)}%
        </text>
        <text x={PAD - 4} y={y(tick * yMax, yMax) + 3} fontSize={9} textAnchor="end" fill="currentColor">
          {formatRate(tick * yMax)}
        </text>
      </g>
    ))}
    <text x={(WIDTH + PAD) / 2} y={HEIGHT - 4} fontSize={10} textAnchor="middle" fill="currentColor">
      {xLabel}
    </text>
    <text
      x={10}
      y={HEIGHT / 2}
      fontSize={10}
      textAnchor="middle"
      fill="currentColor"
      transform={`rotate(-90 10 ${HEIGHT / 2})`}
    >
      {yLabel}
    </text>
  </g>
);

/**
 * Reliability: for each band of calibrated confidence, how often the platform
 * was actually right. On the diagonal is honest; the raw-score dots show how
 * far off the uncalibrated number was.
 */
export const ReliabilityDiagram: React.FC<{
  readonly bins: readonly ReliabilityBin[];
  readonly rawBins: readonly ReliabilityBin[];
  readonly curve: readonly FittedCurvePoint[];
}> = ({ bins, rawBins, curve }) => {
  const fitted = curve.map((p) => `${x(p.score)},${y(p.probability)}`).join(" ");
  const populated = bins.filter((b) => b.count > 0 && b.observed !== null && b.mean_predicted !== null);
  const rawPopulated = rawBins.filter((b) => b.count > 0 && b.observed !== null && b.mean_raw !== null);

  if (populated.length === 0 && curve.length === 0) {
    return <p className={HINT}>No held-out reviews to draw yet.</p>;
  }

  return (
    <figure className="space-y-1">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full max-w-md"
        role="img"
        aria-label="Reliability diagram: calibrated confidence against observed accuracy"
      >
        <Axes xLabel="Confidence (score / calibrated)" yLabel="Observed accuracy" />
        <line
          x1={x(0)}
          y1={y(0)}
          x2={x(1)}
          y2={y(1)}
          className="text-muted-foreground"
          stroke="currentColor"
          strokeDasharray="3 3"
          strokeOpacity={0.5}
        />
        {fitted ? (
          <polyline points={fitted} fill="none" className="text-primary" stroke="currentColor" strokeWidth={2} />
        ) : null}
        {rawPopulated.map((b) => (
          <circle
            key={`raw-${b.lower}`}
            cx={x(b.mean_raw ?? 0)}
            cy={y(b.observed ?? 0)}
            r={3}
            className="text-amber-500"
            fill="none"
            stroke="currentColor"
          >
            <title>{`Raw score ${formatRate(b.mean_raw)}: right ${formatRate(b.observed)} of ${b.count}`}</title>
          </circle>
        ))}
        {populated.map((b) => (
          <circle
            key={`cal-${b.lower}`}
            cx={x(b.mean_predicted ?? 0)}
            cy={y(b.observed ?? 0)}
            r={Math.min(7, 2.5 + Math.sqrt(b.count) / 3)}
            className="text-primary"
            fill="currentColor"
            fillOpacity={0.35}
            stroke="currentColor"
          >
            <title>{`Calibrated ${formatRate(b.mean_predicted)}: right ${formatRate(b.observed)} of ${b.count}`}</title>
          </circle>
        ))}
      </svg>
      <figcaption className={HINT}>
        Line: the fitted map from raw score to probability. Filled dots: calibrated
        confidence against how often reviewers agreed. Hollow dots: the raw score
        before calibration. Closer to the dashed diagonal is more honest.
      </figcaption>
    </figure>
  );
};

/**
 * Error against coverage: as more documents are let through automatically,
 * how the guaranteed bound and the confidence bound move. The marker is the
 * point the slider has selected.
 */
export const CoverageChart: React.FC<{
  readonly curve: readonly CoveragePoint[];
  readonly alpha: number;
  readonly selected: CoveragePoint | null;
}> = ({ curve, alpha, selected }) => {
  if (curve.length === 0) {
    return <p className={HINT}>No reviewed document could have passed automatically yet.</p>;
  }
  const yMax = Math.max(
    0.05,
    Math.min(1, Math.max(alpha * 1.5, ...curve.map((p) => p.conformal_bound))),
  );
  const bound = curve.map((p) => `${x(p.auto_share)},${y(p.conformal_bound, yMax)}`).join(" ");
  const upper = curve
    .map((p) => `${x(p.auto_share)},${y(p.clopper_pearson_upper, yMax)}`)
    .join(" ");

  return (
    <figure className="space-y-1">
      <svg
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        className="h-auto w-full max-w-md"
        role="img"
        aria-label="Error limit against share approved automatically"
      >
        <Axes xLabel="Share approved automatically" yLabel="Error" yMax={yMax} />
        <line
          x1={x(0)}
          x2={x(1)}
          y1={y(alpha, yMax)}
          y2={y(alpha, yMax)}
          className="text-destructive"
          stroke="currentColor"
          strokeDasharray="4 3"
          strokeOpacity={0.7}
        />
        <polyline points={upper} fill="none" className="text-amber-500" stroke="currentColor" strokeWidth={1.5} />
        <polyline points={bound} fill="none" className="text-primary" stroke="currentColor" strokeWidth={2} />
        {selected ? (
          <circle
            cx={x(selected.auto_share)}
            cy={y(selected.conformal_bound, yMax)}
            r={5}
            className="text-primary"
            fill="currentColor"
          />
        ) : null}
      </svg>
      <figcaption className={HINT}>
        Blue: the guaranteed limit on wrong automatic approvals per document. Amber:
        the error rate among automatic approvals, at 95% confidence. Dashed red: the
        limit you chose.
      </figcaption>
    </figure>
  );
};
