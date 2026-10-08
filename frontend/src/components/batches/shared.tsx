/**
 * Phase 1 — small pieces the batch operations pages share: lane chips (always an icon and a
 * word, never colour alone), stat tiles, the segmented progress bar, a modal shell and a SHA-256
 * read-out with copy.
 */
import React, { useRef } from "react";
import { createPortal } from "react-dom";
import { AlertOctagon, CheckCircle2, Clock, Copy, UserCheck, X } from "lucide-react";
import { toast } from "sonner";

import { useDialogFocus } from "@/hooks/useDialogFocus";
import { LANE_LABELS, type Lane } from "@/types/batches";

export const pct = (value: number | null | undefined, digits = 0): string =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

export const formatSeconds = (seconds: number | null | undefined): string => {
  if (seconds === null || seconds === undefined) {
    return "—";
  }
  if (seconds < 60) {
    return `${Math.round(seconds)} s`;
  }
  if (seconds < 3600) {
    return `${(seconds / 60).toFixed(seconds < 600 ? 1 : 0)} min`;
  }
  return `${(seconds / 3600).toFixed(1)} h`;
};

const LANE_STYLE: Readonly<Record<Lane | "PENDING", { tone: string; Icon: React.ElementType }>> = {
  STRAIGHT_THROUGH: {
    tone: "border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
    Icon: CheckCircle2,
  },
  REVIEW: { tone: "border-amber-500/35 bg-amber-500/10 text-amber-800 dark:text-amber-300", Icon: UserCheck },
  EXCEPTION: { tone: "border-destructive/35 bg-destructive/10 text-destructive", Icon: AlertOctagon },
  PENDING: { tone: "border-border bg-muted/50 text-muted-foreground", Icon: Clock },
};

export const LaneChip: React.FC<{ readonly lane: Lane | null; readonly count?: number; readonly compact?: boolean }> = ({
  lane,
  count,
  compact = false,
}) => {
  const key = lane ?? "PENDING";
  const { tone, Icon } = LANE_STYLE[key];
  const label = lane ? LANE_LABELS[lane] : "Processing";
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold ${tone}`}
      data-lane={key}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {compact ? null : label}
      {count !== undefined ? <span className="tabular-nums">{compact ? count : `· ${count}`}</span> : null}
      {compact ? <span className="sr-only">{label}</span> : null}
    </span>
  );
};

export const StatTile: React.FC<{
  readonly label: string;
  readonly value: React.ReactNode;
  readonly hint?: React.ReactNode;
  readonly icon?: React.ElementType;
  readonly testId?: string;
}> = ({ label, value, hint, icon: Icon, testId }) => (
  <div className="fp-card relative overflow-hidden p-4" data-testid={testId}>
    <div className="flex items-start justify-between gap-3">
      <p className="fp-eyebrow">{label}</p>
      {Icon ? (
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted/60 text-muted-foreground">
          <Icon className="h-3.5 w-3.5" aria-hidden />
        </span>
      ) : null}
    </div>
    <p className="fp-num mt-2 text-2xl font-semibold leading-none tracking-tight text-foreground">{value}</p>
    {hint ? <p className="mt-2 text-xs leading-snug text-muted-foreground">{hint}</p> : null}
  </div>
);

/** Finished / failed / running / waiting as one bar. Each segment is labelled for screen readers. */
export const SegmentedProgress: React.FC<{
  readonly completed: number;
  readonly failed: number;
  readonly processing: number;
  readonly queued: number;
  readonly label: string;
}> = ({ completed, failed, processing, queued, label }) => {
  const total = completed + failed + processing + queued;
  const parts = [
    { key: "completed", value: completed, className: "bg-emerald-500", name: "completed" },
    { key: "failed", value: failed, className: "bg-destructive", name: "failed" },
    { key: "processing", value: processing, className: "bg-amber-400 animate-pulse", name: "processing" },
    { key: "queued", value: queued, className: "bg-primary/40", name: "queued" },
  ].filter((p) => p.value > 0);
  const finished = completed + failed;
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={total}
      aria-valuenow={finished}
      aria-valuetext={`${finished} of ${total} finished${failed ? `, ${failed} failed` : ""}`}
      className="flex h-1.5 w-full gap-[2px] overflow-hidden rounded-full bg-muted"
    >
      {parts.map((part) => (
        <span
          key={part.key}
          className={`h-full first:rounded-l-full last:rounded-r-full ${part.className}`}
          style={{ width: `${(part.value / Math.max(total, 1)) * 100}%` }}
          title={`${part.value} ${part.name}`}
        />
      ))}
    </div>
  );
};

export const Modal: React.FC<{
  readonly title: string;
  readonly description?: string;
  readonly onClose: () => void;
  readonly busy?: boolean;
  readonly wide?: boolean;
  readonly children: React.ReactNode;
  readonly footer?: React.ReactNode;
}> = ({ title, description, onClose, busy = false, wide = false, children, footer }) => {
  const ref = useRef<HTMLDivElement | null>(null);
  useDialogFocus(ref, onClose, { busy });
  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex animate-fade-in items-end justify-center bg-black/60 p-0 backdrop-blur-sm sm:items-center sm:p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`flex max-h-[92vh] w-full ${wide ? "sm:max-w-3xl" : "sm:max-w-lg"} animate-scale-in flex-col rounded-t-2xl border border-border-strong/60 bg-popover text-popover-foreground shadow-elevation-3 sm:rounded-2xl`}
      >
        <header className="flex items-start justify-between gap-3 border-b border-border px-5 py-4">
          <div className="min-w-0">
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
            {description ? <p className="mt-1 text-xs leading-relaxed text-muted-foreground">{description}</p> : null}
          </div>
          <button
            type="button"
            onClick={onClose}
            disabled={busy}
            aria-label="Close"
            className="rounded-md p-1 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-40"
          >
            <X className="h-4 w-4" />
          </button>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        {footer ? <footer className="flex flex-wrap justify-end gap-2 border-t border-border px-5 py-3">{footer}</footer> : null}
      </div>
    </div>,
    document.body,
  );
};

export const Sha: React.FC<{ readonly value: string | null; readonly label?: string; readonly full?: boolean }> = ({
  value,
  label = "SHA-256",
  full = false,
}) => {
  if (!value) {
    return <span className="text-muted-foreground">—</span>;
  }
  return (
    <span className="inline-flex max-w-full items-center gap-1.5">
      <code className={`font-mono text-[11px] text-foreground ${full ? "break-all" : ""}`} title={value}>
        {full ? value : `${value.slice(0, 12)}…${value.slice(-6)}`}
      </code>
      <button
        type="button"
        aria-label={`Copy ${label}`}
        onClick={() => {
          void navigator.clipboard
            ?.writeText(value)
            .then(() => toast.success(`${label} copied.`))
            .catch(() => toast.error("Copy failed; select the value instead."));
        }}
        className="shrink-0 rounded p-0.5 text-muted-foreground hover:bg-accent hover:text-foreground"
      >
        <Copy className="h-3 w-3" aria-hidden />
      </button>
    </span>
  );
};

export const formatBytesShort = (bytes: number): string => {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`;
};
