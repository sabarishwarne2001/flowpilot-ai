/**
 * Phase 2 — TruthMesh's shared pieces: severity and risk badges (always a word with the colour),
 * the risk gauge, the kind palette used by the graph and lists, stat cards and small formatters.
 */
import React from "react";
import {
  AlertOctagon,
  AlertTriangle,
  Banknote,
  BookOpenCheck,
  ClipboardList,
  FileSignature,
  FileText,
  Info,
  PackageCheck,
  Receipt,
  ScrollText,
  ShieldAlert,
  Stethoscope,
  Truck,
} from "lucide-react";

import type { RiskBand, Severity } from "@/types/truthmesh";
import { formatMoneyMicros } from "@/utils/formatters";

export const SEVERITY_STYLE: Readonly<Record<Severity, { tone: string; dot: string; Icon: React.ElementType }>> = {
  CRITICAL: { tone: "border-red-500/40 bg-red-500/10 text-red-700 dark:text-red-300", dot: "bg-red-500", Icon: AlertOctagon },
  HIGH: { tone: "border-orange-500/40 bg-orange-500/10 text-orange-700 dark:text-orange-300", dot: "bg-orange-500", Icon: ShieldAlert },
  MEDIUM: { tone: "border-amber-500/40 bg-amber-500/10 text-amber-800 dark:text-amber-300", dot: "bg-amber-500", Icon: AlertTriangle },
  LOW: { tone: "border-sky-500/40 bg-sky-500/10 text-sky-700 dark:text-sky-300", dot: "bg-sky-500", Icon: Info },
};

export const SeverityBadge: React.FC<{ readonly severity: Severity; readonly compact?: boolean }> = ({
  severity,
  compact = false,
}) => {
  const { tone, Icon } = SEVERITY_STYLE[severity];
  return (
    <span
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded-md border px-1.5 py-0.5 text-[10.5px] font-bold uppercase tracking-[0.06em] ${tone}`}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {compact ? <span className="sr-only">{severity.toLowerCase()}</span> : severity.toLowerCase()}
    </span>
  );
};

export const RISK_COLOR: Readonly<Record<RiskBand, { text: string; stroke: string; label: string }>> = {
  CRITICAL: { text: "text-red-600 dark:text-red-400", stroke: "#dc2626", label: "Critical" },
  HIGH: { text: "text-orange-600 dark:text-orange-400", stroke: "#ea580c", label: "High" },
  ELEVATED: { text: "text-amber-600 dark:text-amber-400", stroke: "#d97706", label: "Elevated" },
  LOW: { text: "text-emerald-600 dark:text-emerald-400", stroke: "#059669", label: "Low" },
};

export const bandOf = (score: number): RiskBand =>
  score >= 60 ? "CRITICAL" : score >= 35 ? "HIGH" : score >= 15 ? "ELEVATED" : "LOW";

export const RiskBadge: React.FC<{ readonly score: number }> = ({ score }) => {
  const band = bandOf(score);
  return (
    <span className={`inline-flex items-center gap-1.5 text-xs font-semibold tabular-nums ${RISK_COLOR[band].text}`}>
      <span className="h-2 w-2 rounded-full" style={{ backgroundColor: RISK_COLOR[band].stroke }} aria-hidden />
      {Math.round(score)}
      <span className="sr-only"> risk, {RISK_COLOR[band].label}</span>
    </span>
  );
};

/** A ring gauge, 0..100. */
export const RiskGauge: React.FC<{ readonly score: number; readonly size?: number }> = ({ score, size = 76 }) => {
  const band = bandOf(score);
  const radius = size / 2 - 7;
  const circumference = 2 * Math.PI * radius;
  const shown = Math.max(0, Math.min(100, score));
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label={`Risk index ${Math.round(shown)} of 100, ${RISK_COLOR[band].label}`}>
      <circle cx={size / 2} cy={size / 2} r={radius} fill="none" className="stroke-muted" strokeWidth={7} />
      <circle
        cx={size / 2}
        cy={size / 2}
        r={radius}
        fill="none"
        stroke={RISK_COLOR[band].stroke}
        strokeWidth={7}
        strokeLinecap="round"
        strokeDasharray={`${(shown / 100) * circumference} ${circumference}`}
        transform={`rotate(-90 ${size / 2} ${size / 2})`}
      />
      <text x="50%" y="50%" dominantBaseline="central" textAnchor="middle" className="fill-foreground text-[17px] font-semibold">
        {Math.round(shown)}
      </text>
    </svg>
  );
};

/** One colour and icon per document kind, shared by the graph, lists and the ripple view. */
export const KIND_STYLE: Readonly<Record<string, { color: string; Icon: React.ElementType }>> = {
  MASTER_AGREEMENT: { color: "#7c3aed", Icon: FileSignature },
  CONTRACT: { color: "#8b5cf6", Icon: FileSignature },
  AMENDMENT: { color: "#a78bfa", Icon: FileSignature },
  STATEMENT_OF_WORK: { color: "#6366f1", Icon: ClipboardList },
  POLICY: { color: "#0d9488", Icon: BookOpenCheck },
  PURCHASE_ORDER: { color: "#2563eb", Icon: ScrollText },
  CUSTOMS_MANIFEST: { color: "#0891b2", Icon: ScrollText },
  INSURANCE_CLAIM: { color: "#0f766e", Icon: FileText },
  GOODS_RECEIPT: { color: "#0ea5e9", Icon: PackageCheck },
  WAYBILL: { color: "#06b6d4", Icon: Truck },
  CLINICAL_NOTE: { color: "#14b8a6", Icon: Stethoscope },
  INVOICE: { color: "#f59e0b", Icon: Receipt },
  CREDIT_NOTE: { color: "#eab308", Icon: Banknote },
  STATEMENT: { color: "#d97706", Icon: Banknote },
  OTHER: { color: "#94a3b8", Icon: FileText },
};

export const kindStyle = (kind: string) => KIND_STYLE[kind] ?? KIND_STYLE.OTHER!;

export const KindChip: React.FC<{ readonly kind: string; readonly label: string }> = ({ kind, label }) => {
  const { color, Icon } = kindStyle(kind);
  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-md border border-border/70 bg-background px-1.5 py-0.5 text-[11px] font-medium text-foreground/80">
      <Icon className="h-3 w-3" style={{ color }} aria-hidden />
      {label}
    </span>
  );
};

export const money = (micros: number | null | undefined, currency: string | null | undefined): string =>
  formatMoneyMicros(micros, currency);

export const StatCard: React.FC<{
  readonly label: string;
  readonly value: React.ReactNode;
  readonly hint?: React.ReactNode;
  readonly icon?: React.ReactNode;
  readonly accent?: string | undefined;
  readonly testId?: string;
}> = ({ label, value, hint, icon, accent, testId }) => (
  <div className="fp-card flex min-w-0 flex-col justify-between gap-2 p-4" data-testid={testId}>
    <div className="flex items-center justify-between gap-2 text-muted-foreground">
      <span className="truncate text-[11px] font-semibold uppercase tracking-[0.07em]">{label}</span>
      {icon}
    </div>
    <div className={`truncate text-[26px] font-semibold leading-none tracking-tight tabular-nums ${accent ?? "text-foreground"}`}>
      {value}
    </div>
    {hint ? <div className="line-clamp-2 text-xs leading-snug text-muted-foreground">{hint}</div> : null}
  </div>
);

export const StrengthBar: React.FC<{ readonly value: number }> = ({ value }) => (
  <span className="inline-flex items-center gap-1.5" title={`Link strength ${Math.round(value * 100)}%`}>
    <span className="relative h-1.5 w-14 overflow-hidden rounded-full bg-muted">
      <span className="absolute inset-y-0 left-0 rounded-full bg-primary" style={{ width: `${Math.round(value * 100)}%` }} />
    </span>
    <span className="text-[11px] tabular-nums text-muted-foreground">{Math.round(value * 100)}%</span>
  </span>
);

export const SIGNAL_LABELS: Readonly<Record<string, string>> = {
  IDENTIFIER: "Names its number",
  TEXT_REFERENCE: "Mentioned in its text",
  SAME_NUMBER: "Same number",
  SHARED_REFERENCE: "Shares a reference",
  AGREEMENT_PARTIES: "Parties of the agreement",
  PARTY: "Shared party",
  SEMANTIC: "Similar meaning",
};

export const relativeTime = (iso: string | null | undefined): string => {
  if (!iso) {
    return "never";
  }
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) {
    return "just now";
  }
  if (seconds < 3600) {
    return `${Math.round(seconds / 60)} min ago`;
  }
  if (seconds < 86_400) {
    return `${Math.round(seconds / 3600)} h ago`;
  }
  return `${Math.round(seconds / 86_400)} d ago`;
};
