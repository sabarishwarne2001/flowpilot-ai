/**
 * The Documents Overview's Lumen pieces (docs/helm/VISUAL.md "Documents: Lumen"): the hero with the
 * workspace's total and its last 14 days, the success ring, the processing pipeline and the
 * document-type donut. Every figure is the overview API's own; each chart says the same in words or
 * as a list for screen readers.
 */
import React from "react";
import { AlertTriangle, CheckCircle2, Clock, FileText, Loader2, ScanText } from "lucide-react";

import { useCountUp } from "@/components/helm/ui/motion";
import { formatCalendarDate, formatCalendarMonthShort, formatNumber } from "@/utils/displayTime";
import type { DashboardDailyCount, DashboardDocTypeDistribution } from "@/types/dashboard";

/** A whole number that counts up to the exact value on first view (and is exact for screen readers). */
export const CountUp: React.FC<{ readonly value: number; readonly className?: string; readonly testId?: string }> = ({
  value,
  className = "",
  testId,
}) => {
  const shown = useCountUp(value);
  return (
    <span className={className} data-testid={testId} data-value={value}>
      <span aria-hidden="true">{formatNumber(shown === value ? value : Math.round(shown))}</span>
      <span className="sr-only">{formatNumber(value)}</span>
    </span>
  );
};

/** The last 14 days, one rounded column a day, today lit. A list of the same counts for screen readers. */
export const DailyColumns: React.FC<{ readonly days: readonly DashboardDailyCount[] }> = ({ days }) => {
  const max = Math.max(1, ...days.map((d) => d.count));
  const total = days.reduce((sum, d) => sum + d.count, 0);
  const label = (iso: string): string =>
    `${formatCalendarMonthShort(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)))} ${Number(iso.slice(8, 10))}`;
  return (
    <figure className="m-0 min-w-0" data-testid="overview-daily">
      <figcaption className="mb-3 flex items-baseline justify-between gap-2">
        <span className="fp-eyebrow">Read in the last 14 days</span>
        <span className="fp-num text-xs text-muted-foreground">{formatNumber(total)} documents</span>
      </figcaption>
      <div className="fp-columns" aria-hidden="true">
        {days.map((day, index) => (
          <div key={day.on} className="flex min-w-0 flex-col items-center gap-1.5" title={`${label(day.on)}: ${formatNumber(day.count)}`}>
            <div className="fp-column-track">
              {day.count > 0 ? (
                <div
                  className={`fp-column ${index === days.length - 1 ? "fp-column-today" : ""}`}
                  style={{ height: `${Math.max(8, (day.count / max) * 100)}%`, ["--i" as string]: index }}
                />
              ) : null}
            </div>
            <span className="fp-num h-3.5 text-[10px] leading-3.5 text-muted-foreground">{(days.length - 1 - index) % 2 === 0 ? Number(day.on.slice(8, 10)) : ""}</span>
          </div>
        ))}
      </div>
      <ul className="sr-only" aria-label="Documents read each day, the last 14 days">
        {days.map((day) => (
          <li key={day.on}>
            {formatCalendarDate(day.on)}: {formatNumber(day.count)}
          </li>
        ))}
      </ul>
    </figure>
  );
};

/** The share of finished documents that succeeded, as a ring with the figure at its heart. */
export const SuccessRing: React.FC<{ readonly rate: number | null; readonly completed: number | null; readonly failed: number }> = ({
  rate,
  completed,
  failed,
}) => {
  const size = 148;
  const stroke = 12;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const share = rate === null ? 0 : Math.max(0, Math.min(100, rate)) / 100;
  return (
    <div className="flex flex-col items-center gap-3" data-testid="overview-success">
      <div className="relative" style={{ width: size, height: size }}>
        <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
          <defs>
            <linearGradient id="fp-ring-gradient" x1="0" x2="1" y1="0" y2="1">
              <stop offset="0" className="fp-ring-stop-a" />
              <stop offset="1" className="fp-ring-stop-b" />
            </linearGradient>
          </defs>
          <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} className="fp-ring-track" />
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            stroke="url(#fp-ring-gradient)"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - share)}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
            className="fp-ring-value"
            style={{ ["--fp-ring-c" as string]: circumference }}
          />
        </svg>
        <div className="absolute inset-0 flex flex-col items-center justify-center text-center">
          <span className="fp-num text-[30px] font-semibold leading-none tracking-[-0.03em]">{rate === null ? "—" : `${rate}%`}</span>
          <span className="mt-1 text-[11px] text-muted-foreground">succeeded</span>
        </div>
      </div>
      <p className="text-center text-xs text-muted-foreground">
        {rate === null ? (
          "Nothing has finished yet"
        ) : (
          <>
            {completed !== null ? (
              <>
                <span className="fp-num font-semibold text-foreground">{formatNumber(completed)}</span> read ·{" "}
              </>
            ) : null}
            <span className="fp-num font-semibold text-foreground">{formatNumber(failed)}</span> failed, of finished documents
          </>
        )}
      </p>
    </div>
  );
};

/** Where every document is right now: uploaded, waiting, being read, read, or failed. */
export const PipelineStrip: React.FC<{
  readonly total: number;
  readonly queued: number;
  readonly processing: number;
  readonly completed: number | null;
  readonly failed: number;
}> = ({ total, queued, processing, completed, failed }) => {
  const stages: { key: string; label: string; value: number | null; icon: React.ElementType; tone: string; live?: boolean }[] = [
    { key: "uploaded", label: "Uploaded", value: total, icon: FileText, tone: "fp-stage-ink" },
    { key: "queued", label: "Waiting", value: queued, icon: Clock, tone: "fp-stage-amber", live: queued > 0 },
    { key: "reading", label: "Being read", value: processing, icon: processing > 0 ? Loader2 : ScanText, tone: "fp-stage-blue", live: processing > 0 },
    { key: "read", label: "Read", value: completed, icon: CheckCircle2, tone: "fp-stage-green" },
    { key: "failed", label: "Failed", value: failed, icon: AlertTriangle, tone: failed > 0 ? "fp-stage-red" : "fp-stage-ink" },
  ];
  return (
    <ol className="fp-pipeline" aria-label="Where the documents are">
      {stages
        .filter((stage) => stage.value !== null)
        .map((stage) => {
          const Icon = stage.icon;
          return (
            <li key={stage.key} className={`fp-stage ${stage.tone}`} data-live={stage.live ? "true" : undefined} data-testid={`overview-stage-${stage.key}`}>
              <span className="fp-stage-node" aria-hidden="true">
                <Icon className={`h-4 w-4 ${stage.key === "reading" && stage.live ? "animate-spin motion-reduce:animate-none" : ""}`} />
              </span>
              <span className="fp-num text-[22px] font-semibold leading-none tracking-[-0.02em] text-foreground">
                {formatNumber(stage.value ?? 0)}
              </span>
              <span className="text-xs text-muted-foreground">{stage.label}</span>
            </li>
          );
        })}
    </ol>
  );
};

/** Ten Lumen hues for the document-type donut, in order; the list beside it names each with its count. */
const KIND_TONES = ["fp-kind-1", "fp-kind-2", "fp-kind-3", "fp-kind-4", "fp-kind-5", "fp-kind-6", "fp-kind-7", "fp-kind-8"] as const;

export const kindTone = (index: number): string => KIND_TONES[index % KIND_TONES.length] as string;

/** The classifier's mix as a donut: one arc per kind (the biggest first), the total at its heart. */
export const KindsDonut: React.FC<{ readonly items: readonly DashboardDocTypeDistribution[]; readonly shown: number }> = ({ items, shown }) => {
  const size = 132;
  const stroke = 16;
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  const total = items.reduce((sum, item) => sum + item.count, 0);
  const gap = items.length > 1 ? 3 : 0;
  let offset = 0;
  const arcs = items.slice(0, shown).map((item, index) => {
    const length = (item.count / Math.max(1, total)) * circumference;
    const arc = { key: item.document_type, start: offset, length: Math.max(0, length - gap), tone: kindTone(index) };
    offset += length;
    return arc;
  });
  const rest = Math.max(0, circumference - offset - gap);
  return (
    <div className="relative shrink-0" style={{ width: size, height: size }} aria-hidden="true">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" strokeWidth={stroke} className="fp-ring-track" />
        {arcs.map((arc) => (
          <circle
            key={arc.key}
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            className={`fp-kind-arc ${arc.tone}`}
            strokeDasharray={`${arc.length} ${circumference - arc.length}`}
            strokeDashoffset={-arc.start}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        ))}
        {rest > 1 ? (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={radius}
            fill="none"
            strokeWidth={stroke}
            className="fp-kind-arc fp-kind-rest"
            strokeDasharray={`${rest} ${circumference - rest}`}
            strokeDashoffset={-offset}
            transform={`rotate(-90 ${size / 2} ${size / 2})`}
          />
        ) : null}
      </svg>
      <div className="absolute inset-0 flex flex-col items-center justify-center">
        <span className="fp-num text-xl font-semibold leading-none">{formatNumber(total)}</span>
        <span className="mt-1 text-[10px] text-muted-foreground">classified</span>
      </div>
    </div>
  );
};
