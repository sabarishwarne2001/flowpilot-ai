import React, { useId, useMemo, useState } from "react";

import { formatCalendarDate, formatMoney, formatNumber } from "@/utils/displayTime";
import { formatMoneyMicros } from "@/utils/formatters";
import type { CashPoint } from "@/types/helm";

export interface FlightPin {
  readonly on: string;
  readonly label: string;
}

export interface PastPoint {
  readonly on: string;
  readonly micros: number;
}

interface FlightPathProps {
  /** currency -> the forecast's daily P10/P50/P90 */
  readonly paths: Record<string, CashPoint[]>;
  /** currency -> the floor a mandate keeps cash above */
  readonly floors?: Record<string, number>;
  /** currency -> plan pins (payments on their run days) */
  readonly pins?: Record<string, FlightPin[]>;
  /** currency -> a replayed past (a backtest), drawn left of today */
  readonly past?: Record<string, PastPoint[]>;
  readonly today: string | null | undefined;
  readonly floorProbability?: Record<string, number>;
  /** currency -> a what-if's daily P10/P50/P90, drawn as a second, dashed line (its middle estimate) */
  readonly scenario?: Record<string, CashPoint[]>;
}

const W = 720;
const H = 240;
const PAD = { left: 64, right: 12, top: 14, bottom: 26 };

const compact = (micros: number, currency: string): string =>
  formatMoney(micros / 1_000_000, currency, { notation: "compact", maximumFractionDigits: 1 });

const dayNumber = (iso: string): number => Math.floor(Date.parse(`${iso}T00:00:00Z`) / 86_400_000);

const linePath = (points: readonly CashPoint[], x: (iso: string) => number, y: (v: number) => number): string =>
  points.length > 1 ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p50).toFixed(1)}`).join("L")}` : "";

type SwatchKind = "line" | "band" | "floor" | "scenario" | "pin" | "past";

/** A legend key drawn with the chart's own classes, so the legend matches the lines exactly. */
const Swatch: React.FC<{ readonly kind: SwatchKind }> = ({ kind }) => (
  <svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true" className="mr-1 inline-block align-middle">
    {kind === "band" ? <rect x="1" y="1" width="20" height="8" rx="1.5" className="helm-chart-band" /> : null}
    {kind === "pin" ? <circle cx="11" cy="5" r="3" className="helm-chart-pin" /> : null}
    {kind === "line" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-p50" /> : null}
    {kind === "floor" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-floor" /> : null}
    {kind === "scenario" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-scenario" /> : null}
    {kind === "past" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-past" /> : null}
  </svg>
);

/**
 * The flight path (brief §10.1): 90 days of cash per currency, the P10–P90 band, the P50 line,
 * the floor line, plan pins and a today marker; a replayed past left of today when a backtest has
 * run; and a what-if's middle estimate as a second, dashed line. Hand-built SVG like the rest of
 * the app, with a data table for screen readers.
 */
export const FlightPath: React.FC<FlightPathProps> = ({
  paths,
  floors = {},
  pins = {},
  past = {},
  today,
  floorProbability = {},
  scenario = {},
}) => {
  const currencies = useMemo(() => Object.keys(paths).filter((c) => (paths[c]?.length ?? 0) > 1).sort(), [paths]);
  const [chosen, setChosen] = useState<string | null>(null);
  const currency = chosen && currencies.includes(chosen) ? chosen : currencies[0] ?? null;
  const titleId = useId();

  const geometry = useMemo(() => {
    if (!currency) {
      return null;
    }
    const points = paths[currency] ?? [];
    const history = past[currency] ?? [];
    const whatIf = scenario[currency] ?? [];
    const floor = floors[currency];
    const days = [
      ...history.map((p) => dayNumber(p.on)),
      ...points.map((p) => dayNumber(p.on)),
      ...whatIf.map((p) => dayNumber(p.on)),
    ];
    const values = [
      ...points.flatMap((p) => [p.p10, p.p90]),
      ...whatIf.map((p) => p.p50),
      ...history.map((p) => p.micros),
      ...(floor !== undefined ? [floor] : []),
    ];
    if (days.length === 0 || values.length === 0) {
      return null;
    }
    const d0 = Math.min(...days);
    const d1 = Math.max(...days, d0 + 1);
    let v0 = Math.min(...values, 0);
    let v1 = Math.max(...values);
    const span = v1 - v0 || Math.abs(v1) || 1;
    v0 -= span * 0.06;
    v1 += span * 0.06;
    const x = (iso: string) => PAD.left + ((dayNumber(iso) - d0) / (d1 - d0)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - v0) / (v1 - v0)) * (H - PAD.top - PAD.bottom);
    const band =
      points.length > 1
        ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p90).toFixed(1)}`).join("L")}` +
          `L${[...points].reverse().map((p) => `${x(p.on).toFixed(1)},${y(p.p10).toFixed(1)}`).join("L")}Z`
        : "";
    const p50 = linePath(points, x, y);
    const scenarioLine = linePath(whatIf, x, y);
    const history_ = history.length > 1 ? `M${history.map((p) => `${x(p.on).toFixed(1)},${y(p.micros).toFixed(1)}`).join("L")}` : "";
    const ticks = [v0 + (v1 - v0) * 0.1, v0 + (v1 - v0) * 0.5, v0 + (v1 - v0) * 0.9];
    const first = points[0]?.on ?? history[0]?.on;
    const last = points[points.length - 1]?.on;
    return { x, y, band, p50, scenarioLine, whatIf, history: history_, ticks, floor, first, last, points };
  }, [currency, floors, past, paths, scenario]);

  if (!currency || !geometry) {
    return (
      <p className="helm-muted text-sm">
        No cash forecast yet. Enter a cash position in Settings and Helm will draw 90 days of it.
      </p>
    );
  }

  const { x, y, band, p50, scenarioLine, whatIf, history, ticks, floor, first, last, points } = geometry;
  const end = points[points.length - 1];
  const whatIfEnd = whatIf[whatIf.length - 1];
  const hasWhatIf = whatIf.length > 1;
  const probability = floorProbability[currency];
  const summary =
    `Cash in ${currency} over the next ${formatNumber(points.length)} days. ` +
    (end ? `By ${formatCalendarDate(end.on)}, the middle estimate is ${formatMoneyMicros(end.p50, currency)}, ` +
      `with a cautious estimate of ${formatMoneyMicros(end.p10, currency)}. ` : "") +
    (floor !== undefined ? `The floor is ${formatMoneyMicros(floor, currency)}` +
      (probability !== undefined ? `; ${formatNumber(Math.round(probability * 100))} in 100 simulated futures stay above it.` : ".") : "") +
    (hasWhatIf && whatIfEnd
      ? ` With the what-if, the middle estimate by ${formatCalendarDate(whatIfEnd.on)} is ${formatMoneyMicros(whatIfEnd.p50, currency)}.`
      : "");
  const weekly = points.filter((_, index) => index % 7 === 0 || index === points.length - 1);
  const whatIfOn = new Map(whatIf.map((point) => [point.on, point]));
  const todayX = today ? x(today) : null;

  return (
    <figure className="m-0">
      {currencies.length > 1 ? (
        <div role="radiogroup" aria-label="Currency" className="mb-2 flex flex-wrap gap-1.5">
          {currencies.map((code) => (
            <button
              key={code}
              type="button"
              role="radio"
              aria-checked={code === currency}
              className={`helm-chip ${code === currency ? "helm-chip-accent" : ""}`}
              onClick={() => setChosen(code)}
            >
              {code}
            </button>
          ))}
        </div>
      ) : null}
      <svg viewBox={`0 0 ${W} ${H}`} className="h-auto w-full" role="img" aria-labelledby={titleId}>
        <title id={titleId}>{summary}</title>
        {ticks.map((tick) => (
          <g key={tick}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} className="helm-chart-axis" strokeWidth="1" />
            <text x={PAD.left - 6} y={y(tick) + 3} textAnchor="end" className="helm-chart-text helm-figure">
              {compact(tick, currency)}
            </text>
          </g>
        ))}
        {band ? <path d={band} className="helm-chart-band" /> : null}
        {history ? <path d={history} className="helm-chart-past" /> : null}
        {p50 ? <path d={p50} className="helm-chart-p50" /> : null}
        {scenarioLine ? <path d={scenarioLine} className="helm-chart-scenario" data-testid="helm-flight-what-if" /> : null}
        {floor !== undefined ? (
          <g>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(floor)} y2={y(floor)} className="helm-chart-floor" />
            <text x={W - PAD.right} y={y(floor) - 4} textAnchor="end" className="helm-chart-text">
              floor
            </text>
          </g>
        ) : null}
        {(pins[currency] ?? []).map((pin, index) => (
          <circle key={`${pin.on}-${index}`} cx={x(pin.on)} cy={H - PAD.bottom - 4} r="3" className="helm-chart-pin">
            <title>{pin.label}</title>
          </circle>
        ))}
        {todayX !== null && Number.isFinite(todayX) ? (
          <g>
            <line x1={todayX} x2={todayX} y1={PAD.top} y2={H - PAD.bottom} className="helm-chart-today" />
            <text x={todayX + 4} y={PAD.top + 9} className="helm-chart-text">
              today
            </text>
          </g>
        ) : null}
        {first ? (
          <text x={PAD.left} y={H - 8} className="helm-chart-text">
            {formatCalendarDate(first)}
          </text>
        ) : null}
        {last ? (
          <text x={W - PAD.right} y={H - 8} textAnchor="end" className="helm-chart-text">
            {formatCalendarDate(last)}
          </text>
        ) : null}
      </svg>
      <figcaption className="helm-muted mt-1 flex flex-wrap gap-x-4 gap-y-1 text-[11px]" aria-label="Legend">
        <span>
          <Swatch kind="line" />
          The middle of {formatNumber(1000)} simulated futures
        </span>
        <span>
          <Swatch kind="band" />8 in 10 of them
        </span>
        {hasWhatIf ? (
          <span className="font-semibold">
            <Swatch kind="scenario" />
            The what-if (its middle)
          </span>
        ) : null}
        {floor !== undefined ? (
          <span>
            <Swatch kind="floor" />
            The floor
          </span>
        ) : null}
        <span>
          <Swatch kind="pin" />
          Planned payments
        </span>
        {(past[currency] ?? []).length > 1 ? (
          <span>
            <Swatch kind="past" />
            Left of today: the cash Helm saw in the replayed past
          </span>
        ) : null}
      </figcaption>
      <details className="mt-2 text-xs">
        <summary className="helm-accent cursor-pointer font-semibold">Show the forecast as a table</summary>
        <div className="relative mt-2 overflow-x-auto">
          <table className={`w-full text-left ${hasWhatIf ? "min-w-[620px]" : "min-w-[420px]"}`}>
            <caption className="sr-only">{summary}</caption>
            <thead>
              <tr className="helm-label">
                <th scope="col" className="py-1 pr-3">Date</th>
                <th scope="col" className="py-1 pr-3">Cautious (P10)</th>
                <th scope="col" className="py-1 pr-3">Middle (P50)</th>
                <th scope="col" className={hasWhatIf ? "py-1 pr-3" : "py-1"}>Hopeful (P90)</th>
                {hasWhatIf ? (
                  <>
                    <th scope="col" className="py-1 pr-3">What-if cautious</th>
                    <th scope="col" className="py-1">What-if middle</th>
                  </>
                ) : null}
              </tr>
            </thead>
            <tbody>
              {weekly.map((point) => {
                const other = whatIfOn.get(point.on);
                return (
                  <tr key={point.on} className="helm-hairline border-t">
                    <th scope="row" className="py-1 pr-3 font-normal">{formatCalendarDate(point.on)}</th>
                    <td className="helm-figure py-1 pr-3">{formatMoneyMicros(point.p10, currency)}</td>
                    <td className="helm-figure py-1 pr-3">{formatMoneyMicros(point.p50, currency)}</td>
                    <td className={`helm-figure py-1 ${hasWhatIf ? "pr-3" : ""}`}>{formatMoneyMicros(point.p90, currency)}</td>
                    {hasWhatIf ? (
                      <>
                        <td className="helm-figure py-1 pr-3">{formatMoneyMicros(other?.p10, currency)}</td>
                        <td className="helm-figure py-1 font-semibold">{formatMoneyMicros(other?.p50, currency)}</td>
                      </>
                    ) : null}
                  </tr>
                );
              })}
            </tbody>
          </table>
          {(past[currency] ?? []).length > 0 ? (
            <table className="mt-3 w-full min-w-[260px] text-left">
              <caption className="helm-label py-1 text-left">The replayed past</caption>
              <thead>
                <tr className="helm-label">
                  <th scope="col" className="py-1 pr-3">Date</th>
                  <th scope="col" className="py-1">Cash Helm saw</th>
                </tr>
              </thead>
              <tbody>
                {(past[currency] ?? []).map((point) => (
                  <tr key={point.on} className="helm-hairline border-t">
                    <th scope="row" className="py-1 pr-3 font-normal">{formatCalendarDate(point.on)}</th>
                    <td className="helm-figure py-1">{formatMoneyMicros(point.micros, currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
        </div>
      </details>
    </figure>
  );
};

export default FlightPath;
