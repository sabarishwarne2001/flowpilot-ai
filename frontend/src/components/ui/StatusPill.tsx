import React from "react";

export type StatusTone = "ok" | "warn" | "danger" | "info" | "neutral";

const TONE_CLASSES: Readonly<Record<StatusTone, string>> = {
  ok: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  warn: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  danger:
    "border-destructive/30 bg-destructive/10 text-destructive dark:text-red-400",
  info: "border-primary/30 bg-primary/10 text-primary",
  neutral: "border-border bg-muted/60 text-muted-foreground",
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
      className={`inline-flex items-center whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[11px] font-medium leading-tight ${TONE_CLASSES[resolved]} ${className}`}
    >
      {label ?? status}
    </span>
  );
};

export default StatusPill;
