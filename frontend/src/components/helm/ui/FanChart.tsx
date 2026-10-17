import React, { useEffect, useId, useMemo, useRef, useState } from "react";

import { formatCalendarDate, formatNumber } from "@/utils/displayTime";
import { formatHelmMoney, formatHelmMoneyCompact } from "@/components/helm/money";
import type { CashPoint } from "@/types/helm";

export interface FlightPin {
  readonly on: string;
  readonly label: string;
}

export interface PastPoint {
  readonly on: string;
  readonly micros: number;
}

export interface FanChartProps {
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
  /** The days the company pays suppliers (summary.run_days), as ticks on the time axis. */
  readonly runDays?: readonly string[];
  /** currency -> money expected in (promises owed to us), as markers along the top. */
  readonly receipts?: Record<string, FlightPin[]>;
}

/** The drawing's width follows its box (360–760 units), so text stays legible on a phone. */
const MIN_W = 360;
const MAX_W = 760;
const H = 290;
const PAD = { left: 64, right: 14, top: 22, bottom: 30 };

const compact = formatHelmMoneyCompact;

const dayNumber = (iso: string): number => Math.floor(Date.parse(`${iso}T00:00:00Z`) / 86_400_000);

const linePath = (points: readonly CashPoint[], x: (iso: string) => number, y: (v: number) => number): string =>
  points.length > 1 ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p50).toFixed(1)}`).join("L")}` : "";

type SwatchKind = "line" | "band" | "floor" | "scenario" | "pin" | "past" | "receipt" | "runday";

/** A legend key drawn with the chart's own classes, so the legend matches the lines exactly. */
const Swatch: React.FC<{ readonly kind: SwatchKind }> = ({ kind }) => (
  <svg width="22" height="10" viewBox="0 0 22 10" aria-hidden="true" className="mr-1 inline-block align-middle">
    {kind === "band" ? <rect x="1" y="1" width="20" height="8" rx="3" className="helm-chart-band" /> : null}
    {kind === "pin" ? <circle cx="11" cy="5" r="3" className="helm-chart-pin" /> : null}
    {kind === "receipt" ? <path d="M11 9 L7.5 3 L14.5 3 Z" className="helm-chart-receipt" /> : null}
    {kind === "runday" ? <line x1="11" x2="11" y1="2" y2="8" className="helm-chart-runday" /> : null}
    {kind === "line" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-p50" /> : null}
    {kind === "floor" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-floor" /> : null}
    {kind === "scenario" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-scenario" /> : null}
    {kind === "past" ? <line x1="1" x2="21" y1="5" y2="5" className="helm-chart-past" /> : null}
  </svg>
);

/**
 * The flight path, Night Bridge's fan chart (docs/helm/VISUAL.md "FanChart"): 90 days of cash per
 * currency, a gradient P10–P90 band and a glowing P50 line, the floor as a dashed line with its
 * label, today, the payment-run days as ticks, planned payments as pins and expected receipts as
 * markers; a replayed past left of today; a what-if's middle estimate as a dashed line. A crosshair
 * follows the pointer with a tooltip, and Left and Right arrow keys move it day by day (the day is
 * also announced). Hand-built SVG, with the whole forecast as a table for screen readers.
 */
export const FanChart: React.FC<FanChartProps> = ({
  paths,
  floors = {},
  pins = {},
  past = {},
  today,
  floorProbability = {},
  scenario = {},
  runDays = [],
  receipts = {},
}) => {
  const currencies = useMemo(() => Object.keys(paths).filter((c) => (paths[c]?.length ?? 0) > 1).sort(), [paths]);
  const [chosen, setChosen] = useState<string | null>(null);
  const currency = chosen && currencies.includes(chosen) ? chosen : currencies[0] ?? null;
  const titleId = useId();
  const gradientId = useId().replace(/:/g, "");
  const [cross, setCross] = useState<number | null>(null);
  const surface = useRef<HTMLDivElement>(null);
  const [W, setW] = useState(MAX_W);
  useEffect(() => {
    const node = surface.current;
    if (!node || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    const observer = new ResizeObserver(([entry]) => {
      const width = entry?.contentRect.width ?? MAX_W;
      setW(Math.round(Math.max(MIN_W, Math.min(MAX_W, width))));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

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
    v1 += span * 0.08;
    const x = (iso: string) => PAD.left + ((dayNumber(iso) - d0) / (d1 - d0)) * (W - PAD.left - PAD.right);
    const y = (v: number) => PAD.top + (1 - (v - v0) / (v1 - v0)) * (H - PAD.top - PAD.bottom);
    const inRange = (iso: string): boolean => dayNumber(iso) >= d0 && dayNumber(iso) <= d1;
    const band =
      points.length > 1
        ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p90).toFixed(1)}`).join("L")}` +
          `L${[...points].reverse().map((p) => `${x(p.on).toFixed(1)},${y(p.p10).toFixed(1)}`).join("L")}Z`
        : "";
    const upper = points.length > 1 ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p90).toFixed(1)}`).join("L")}` : "";
    const lower = points.length > 1 ? `M${points.map((p) => `${x(p.on).toFixed(1)},${y(p.p10).toFixed(1)}`).join("L")}` : "";
    const p50 = linePath(points, x, y);
    const scenarioLine = linePath(whatIf, x, y);
    const history_ = history.length > 1 ? `M${history.map((p) => `${x(p.on).toFixed(1)},${y(p.micros).toFixed(1)}`).join("L")}` : "";
    const ticks = [v0 + (v1 - v0) * 0.12, v0 + (v1 - v0) * 0.5, v0 + (v1 - v0) * 0.88];
    const first = points[0]?.on ?? history[0]?.on;
    const last = points[points.length - 1]?.on;
    return { x, y, inRange, band, upper, lower, p50, scenarioLine, whatIf, history: history_, ticks, floor, first, last, points };
  }, [W, currency, floors, past, paths, scenario]);

  if (!currency || !geometry) {
    return (
      <p className="helm-muted text-sm">
        No cash forecast yet. Enter a cash position in Settings and Helm will draw 90 days of it.
      </p>
    );
  }

  const { x, y, inRange, band, upper, lower, p50, scenarioLine, whatIf, history, ticks, floor, first, last, points } = geometry;
  const end = points[points.length - 1];
  const whatIfEnd = whatIf[whatIf.length - 1];
  const hasWhatIf = whatIf.length > 1;
  const probability = floorProbability[currency];
  const money = (micros: number | null | undefined): string => formatHelmMoney(micros, currency);
  const summary =
    `Cash in ${currency} over the next ${formatNumber(points.length)} days. ` +
    (end ? `By ${formatCalendarDate(end.on)}, the middle estimate is ${money(end.p50)}, ` +
      `with a cautious estimate of ${money(end.p10)}. ` : "") +
    (floor !== undefined ? `The floor is ${money(floor)}` +
      (probability !== undefined ? `; ${formatNumber(Math.round(probability * 100))} in 100 simulated futures stay above it.` : ".") : "") +
    (hasWhatIf && whatIfEnd
      ? ` With the what-if, the middle estimate by ${formatCalendarDate(whatIfEnd.on)} is ${money(whatIfEnd.p50)}.`
      : "");
  const weekly = points.filter((_, index) => index % 7 === 0 || index === points.length - 1);
  const whatIfOn = new Map(whatIf.map((point) => [point.on, point]));
  const todayX = today ? x(today) : null;
  const runTicks = runDays.filter((iso) => inRange(iso));
  const receiptMarks = (receipts[currency] ?? []).filter((pin) => inRange(pin.on));
  const pinMarks = (pins[currency] ?? []).filter((pin) => inRange(pin.on));
  const crossPoint = cross !== null ? points[Math.max(0, Math.min(points.length - 1, cross))] : undefined;
  const crossWhatIf = crossPoint ? whatIfOn.get(crossPoint.on) : undefined;
  const crossPins = crossPoint ? pinMarks.filter((pin) => pin.on === crossPoint.on) : [];
  const crossReceipts = crossPoint ? receiptMarks.filter((pin) => pin.on === crossPoint.on) : [];
  const crossX = crossPoint ? x(crossPoint.on) : 0;

  const pointAt = (clientX: number, box: DOMRect): number => {
    const svgX = ((clientX - box.left) / box.width) * W;
    let best = 0;
    let distance = Number.POSITIVE_INFINITY;
    points.forEach((point, index) => {
      const gap = Math.abs(x(point.on) - svgX);
      if (gap < distance) {
        distance = gap;
        best = index;
      }
    });
    return best;
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>): void => {
    const keys: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1, PageDown: 7, PageUp: -7 };
    if (event.key in keys) {
      event.preventDefault();
      const step = keys[event.key] as number;
      setCross((current) => (current === null ? 0 : Math.max(0, Math.min(points.length - 1, current + step))));
    } else if (event.key === "Home") {
      event.preventDefault();
      setCross(0);
    } else if (event.key === "End") {
      event.preventDefault();
      setCross(points.length - 1);
    } else if (event.key === "Escape") {
      setCross(null);
    }
  };

  const crossWords = crossPoint
    ? `${formatCalendarDate(crossPoint.on)}: middle ${money(crossPoint.p50)}, cautious ${money(crossPoint.p10)}, hopeful ${money(crossPoint.p90)}` +
      (crossWhatIf ? `; what-if middle ${money(crossWhatIf.p50)}` : "") +
      (crossPins.length > 0 ? `; ${crossPins.length} planned ${crossPins.length === 1 ? "payment" : "payments"}` : "") +
      (crossReceipts.length > 0 ? `; ${crossReceipts.length} expected ${crossReceipts.length === 1 ? "receipt" : "receipts"}` : "") +
      "."
    : "";

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
      <div
        ref={surface}
        className="helm-chart-surface"
        tabIndex={0}
        role="group"
        aria-label={`Flight path in ${currency}. Left and right arrow keys read the forecast day by day.`}
        onKeyDown={onKeyDown}
        onBlur={() => setCross(null)}
        onPointerMove={(event) => setCross(pointAt(event.clientX, event.currentTarget.getBoundingClientRect()))}
        onPointerLeave={() => setCross(null)}
        data-testid="helm-flight-chart"
      >
        <svg viewBox={`0 0 ${W} ${H}`} className="block h-auto w-full overflow-visible" role="img" aria-labelledby={titleId}>
          <title id={titleId}>{summary}</title>
          <defs>
            <linearGradient id={`${gradientId}-band`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" className="helm-chart-band-stop-top" />
              <stop offset="0.55" className="helm-chart-band-stop-mid" />
              <stop offset="1" className="helm-chart-band-stop-bottom" />
            </linearGradient>
          </defs>
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={PAD.left} x2={W - PAD.right} y1={y(tick)} y2={y(tick)} className="helm-chart-axis" strokeWidth="1" />
              <text x={PAD.left - 8} y={y(tick) + 3} textAnchor="end" className="helm-chart-text helm-figure">
                {compact(tick, currency)}
              </text>
            </g>
          ))}
          <g className="helm-draw-in">
            {band ? <path d={band} fill={`url(#${gradientId}-band)`} /> : null}
            {upper ? <path d={upper} className="helm-chart-band-edge" /> : null}
            {lower ? <path d={lower} className="helm-chart-band-edge" /> : null}
            {history ? <path d={history} className="helm-chart-past" /> : null}
            {p50 ? <path d={p50} className="helm-chart-p50-glow" /> : null}
            {p50 ? <path d={p50} className="helm-chart-p50" data-testid="helm-flight-p50" /> : null}
            {scenarioLine ? <path d={scenarioLine} className="helm-chart-scenario" data-testid="helm-flight-what-if" /> : null}
          </g>
          {floor !== undefined ? (
            <g data-testid="helm-flight-floor">
              <line x1={PAD.left} x2={W - PAD.right} y1={y(floor)} y2={y(floor)} className="helm-chart-floor" />
              <text x={W - PAD.right} y={y(floor) - 5} textAnchor="end" className="helm-chart-floor-label helm-figure">
                floor {compact(floor, currency)}
              </text>
            </g>
          ) : null}
          {runTicks.map((iso) => (
            <line key={`run-${iso}`} x1={x(iso)} x2={x(iso)} y1={H - PAD.bottom + 2} y2={H - PAD.bottom + 8} className="helm-chart-runday">
              <title>{`Payment run: ${formatCalendarDate(iso)}`}</title>
            </line>
          ))}
          {pinMarks.map((pin, index) => (
            <circle key={`${pin.on}-${index}`} cx={x(pin.on)} cy={H - PAD.bottom - 5} r="3.25" className="helm-chart-pin">
              <title>{pin.label}</title>
            </circle>
          ))}
          {receiptMarks.map((pin, index) => {
            const at = x(pin.on);
            return (
              <path key={`r-${pin.on}-${index}`} d={`M${at},${PAD.top - 6} l-3.5,-6 l7,0 Z`} className="helm-chart-receipt">
                <title>{pin.label}</title>
              </path>
            );
          })}
          {todayX !== null && Number.isFinite(todayX) ? (
            <g>
              <line x1={todayX} x2={todayX} y1={PAD.top} y2={H - PAD.bottom} className="helm-chart-today" />
              <circle cx={todayX} cy={PAD.top} r="2.5" className="helm-chart-today-dot" />
              <text x={todayX + 6} y={PAD.top + 14} className="helm-chart-text">
                today
              </text>
            </g>
          ) : null}
          {crossPoint ? (
            <g aria-hidden="true">
              <line x1={crossX} x2={crossX} y1={PAD.top} y2={H - PAD.bottom} className="helm-chart-cross" />
              <circle cx={crossX} cy={y(crossPoint.p50)} r="4.5" className="helm-chart-cross-dot" />
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
        {crossPoint ? (
          <div
            className="helm-tooltip"
            aria-hidden="true"
            style={{
              left: `${(crossX / W) * 100}%`,
              top: 8,
              transform: crossX > W * 0.62 ? "translateX(calc(-100% - 14px))" : "translateX(14px)",
            }}
            data-testid="helm-flight-tooltip"
          >
            <p className="font-semibold">{formatCalendarDate(crossPoint.on)}</p>
            <p className="mt-1 flex justify-between gap-3">
              <span className="helm-muted">Middle</span>
              <span className="helm-figure font-semibold">{money(crossPoint.p50)}</span>
            </p>
            <p className="flex justify-between gap-3">
              <span className="helm-muted">Cautious</span>
              <span className="helm-figure">{money(crossPoint.p10)}</span>
            </p>
            <p className="flex justify-between gap-3">
              <span className="helm-muted">Hopeful</span>
              <span className="helm-figure">{money(crossPoint.p90)}</span>
            </p>
            {crossWhatIf ? (
              <p className="flex justify-between gap-3">
                <span className="helm-muted">What-if</span>
                <span className="helm-figure font-semibold">{money(crossWhatIf.p50)}</span>
              </p>
            ) : null}
            {crossPins.length > 0 ? <p className="helm-accent mt-1">{crossPins.length} planned {crossPins.length === 1 ? "payment" : "payments"}</p> : null}
            {crossReceipts.length > 0 ? <p className="helm-state-sky">{crossReceipts.length} expected {crossReceipts.length === 1 ? "receipt" : "receipts"}</p> : null}
          </div>
        ) : null}
        <p className="sr-only" aria-live="polite">
          {crossWords}
        </p>
      </div>
      <figcaption className="helm-muted mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px]" aria-label="Legend">
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
        {runTicks.length > 0 ? (
          <span>
            <Swatch kind="runday" />
            Payment-run days
          </span>
        ) : null}
        {receiptMarks.length > 0 ? (
          <span>
            <Swatch kind="receipt" />
            Money expected in
          </span>
        ) : null}
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
                    <td className="helm-figure py-1 pr-3">{money(point.p10)}</td>
                    <td className="helm-figure py-1 pr-3">{money(point.p50)}</td>
                    <td className={`helm-figure py-1 ${hasWhatIf ? "pr-3" : ""}`}>{money(point.p90)}</td>
                    {hasWhatIf ? (
                      <>
                        <td className="helm-figure py-1 pr-3">{money(other?.p10)}</td>
                        <td className="helm-figure py-1 font-semibold">{money(other?.p50)}</td>
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
                    <td className="helm-figure py-1">{money(point.micros)}</td>
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

export default FanChart;
