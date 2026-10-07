import React from "react";

export type StatusTone = "ok" | "warn" | "danger" | "info" | "neutral";

const TONE_CLASSES: Readonly<Record<StatusTone, string>> = {
  ok: "border-emerald-500/25 bg-emerald-500/[0.08] text-emerald-700 dark:text-emerald-300",
  warn: "border-amber-500/25 bg-amber-500/[0.08] text-amber-700 dark:text-amber-300",
  danger: "border-destructive/25 bg-destructive/[0.08] text-destructive dark:text-red-300",
  info: "border-primary/25 bg-primary/[0.08] text-primary dark:text-[hsl(213_94%_72%)]",
  neutral: "border-border-strong/60 bg-muted/60 text-muted-foreground",
};

const DOT_CLASSES: Readonly<Record<StatusTone, string>> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  danger: "bg-destructive",
  info: "bg-primary",
  neutral: "bg-muted-foreground/60",
};

const TONE_BY_STATUS: Readonly<Record<string, StatusTone>> = {
  ACTIVE: "ok",
  COMPLETE: "ok",
  COMPLETED: "ok",
  SUCCESS: "ok",
  SUCCEEDED: "ok",
  VERIFIED: "ok",
  HEALTHY: "ok",
  ACCEPTED: "ok",

  PENDING: "info",
  RUNNING: "info",
  QUEUED: "info",
  PROCESSING: "info",
  IN_PROGRESS: "info",

  EXPIRED: "warn",
  DISABLED: "warn",
  PAUSED: "warn",
  INVESTIGATE: "warn",
  DEGRADED: "warn",
  ARCHIVED: "warn",
  SUSPENDED: "warn",
  UNVERIFIED: "warn",

  FAILED: "danger",
  ERROR: "danger",
  REVOKED: "danger",
  CANCELLED: "danger",
  CANCELED: "danger",
  TIMED_OUT: "danger",
  BUDGET_EXHAUSTED: "danger",
  REJECTED: "danger",
};

export const toneForStatus = (status: string): StatusTone =>
  TONE_BY_STATUS[status.trim().toUpperCase()] ?? "neutral";

export interface StatusPillProps {
  readonly status: string;
  readonly tone?: StatusTone;
  readonly label?: string;
  readonly title?: string;
  readonly className?: string;
}

export const StatusPill: React.FC<StatusPillProps> = ({
  status,
  tone,
  label,
  title,
  className = "",
}) => {
  const resolved = tone ?? toneForStatus(status);
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-medium leading-tight ${TONE_CLASSES[resolved]} ${className}`}
    >
      <span aria-hidden="true" className={`h-1.5 w-1.5 shrink-0 rounded-full ${DOT_CLASSES[resolved]}`} />
      {label ?? status}
    </span>
  );
};

export default StatusPill;
