import React, { useEffect, useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, ChevronRight, FileWarning, Grid3x3, Loader2, Scale, TrendingUp } from "lucide-react";

import {
  BUTTON_GHOST,
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  HINT,
  PAGE_TITLE,
  SELECT,
  SURFACE,
  SURFACE_INSET,
  TEXTAREA,
} from "@/components/ui/primitives";
import CapabilityLockCard from "@/components/radar/CapabilityLockCard";
import PaymentRiskPanel from "@/components/radar/PaymentRiskPanel";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { ApiError } from "@/services/api/errors";
import {
  confirmAnomaly,
  dismissAnomaly,
  getAnomaly,
  getPriceSeries,
  listAnomalies,
  microsToUnits,
  percent,
} from "@/services/api/radar";
import { formatTimestamp } from "@/utils/displayTime";
import { vendorLabel } from "@/utils/formatters";
import type {
  AnomalyEvidence,
  AnomalyFindingSummary,
  AnomalyKind,
  AnomalySeverity,
  AnomalyStatus,
  PriceSeries,
} from "@/types/radar";
import { KIND_LABELS, LAYER_LABELS } from "@/types/radar";

/**
 * ARCH-34 §5.6 — the Forensic Audit Radar.
 *
 * EVERY SENTENCE ON THIS SCREEN COMES FROM THE SERVER
 * ===================================================
 *
 * `finding.headline` is rendered verbatim. It is a format string over the
 * metrics the detectors computed, assembled in `findings.headline_for`, and
 * nothing here paraphrases, summarises or re-describes it. §5.6 requires
 * headlines to be "written from the metrics by templates, never generated",
 * and a console that rewrote them in nicer English would put that guarantee
 * one refactor away from being false.
 *
 * The one thing this file adds is the LAYER LABEL under the headline, because
 * "Identical file" and "Reads similar" are the difference between a fact and
 * an estimate, and a reader who cannot see which one they have will treat
 * both the same.
 *
 * L3 CARRIES ITS OWN CAVEAT
 * =========================
 *
 * §5.9: embedding similarity is evidence, not proof. The backend enforces
 * that as a severity cap; here it is a visible line of text, because a reader
 * deciding whether to hold a payment should be told why this particular
 * finding is capped at MEDIUM rather than having to infer it from a colour.
 *
 * "NOT AN ANOMALY" REQUIRES A REASON AND NEVER DELETES ANYTHING
 * =============================================================
 *
 * The dismiss control will not submit without text. `ck_af_dismissal_has_reason`
 * would refuse it anyway; refusing here means the person finds out while they
 * still have their thought, rather than after a round trip.
 */

const ANOMALY_RADAR_CAPABILITY = "capability.anomaly_radar";

type TabKey = "ALL" | AnomalyKind;

const TABS: readonly { readonly key: TabKey; readonly label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "DUPLICATE_DOCUMENT", label: KIND_LABELS.DUPLICATE_DOCUMENT },
  { key: "PRICE_SURGE", label: KIND_LABELS.PRICE_SURGE },
  { key: "CONTRACT_DRIFT", label: KIND_LABELS.CONTRACT_DRIFT },
];

const SEVERITY_STYLES: Readonly<Record<AnomalySeverity, string>> = {
  HIGH: "bg-destructive/10 text-destructive border-destructive/30",
  MEDIUM: "bg-amber-500/10 text-amber-600 border-amber-500/30",
  LOW: "bg-muted text-muted-foreground border-border",
};

const KindIcon: React.FC<{ readonly kind: AnomalyKind }> = ({ kind }) => {
  if (kind === "PRICE_SURGE") {return <TrendingUp className="h-4 w-4" aria-hidden />;}
  if (kind === "CONTRACT_DRIFT") {return <Scale className="h-4 w-4" aria-hidden />;}
  return <FileWarning className="h-4 w-4" aria-hidden />;
};

const SeverityBadge: React.FC<{ readonly severity: AnomalySeverity }> = ({
  severity,
}) => (
  <span
    className={`rounded border px-1.5 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${SEVERITY_STYLES[severity]}`}
  >
    {severity}
  </span>
);

/** A side-by-side row. Used for identifiers, chunk pairs and clause pairs. */
const SideBySide: React.FC<{
  readonly label: string;
  readonly leftTitle: string;
  readonly rightTitle: string;
  readonly left: React.ReactNode;
  readonly right: React.ReactNode;
  readonly note?: string | undefined;
}> = ({ label, leftTitle, rightTitle, left, right, note }) => (
  <div className={`${SURFACE_INSET} space-y-2 p-3`}>
    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
      {label}
    </p>
    <div className="grid gap-3 sm:grid-cols-2">
      <div className="space-y-1">
        <p className={HINT}>{leftTitle}</p>
        <div className="text-sm text-foreground">{left}</div>
      </div>
      <div className="space-y-1">
        <p className={HINT}>{rightTitle}</p>
        <div className="text-sm text-foreground">{right}</div>
      </div>
    </div>
    {note ? <p className={HINT}>{note}</p> : null}
  </div>
);

const asText = (value: unknown): string =>
  value === null || value === undefined ? "—" : String(value);

const side = (item: AnomalyEvidence, key: "subject" | "counterpart") =>
  (item[key] ?? {}) as Record<string, unknown>;

/**
 * The price chart: an inline SVG with the median band and the flagged point.
 *
 * Hand-drawn rather than pulled from a chart library because the shape is
 * fixed and small, and because the BAND is the point of the picture: a reader
 * needs to see that the new price is outside where this item normally sits,
 * not a pretty line. A generic library would draw the line and leave the band
 * as an afterthought.
 */
const PriceChart: React.FC<{ readonly series: PriceSeries }> = ({ series }) => {
  const width = 640;
  const height = 180;
  const pad = 28;

  const points = series.points;
  if (points.length < 2) {
    return <p className={HINT}>Not enough price history to draw a chart.</p>;
  }

  const values = points.map((point) => microsToUnits(point.unit_price_micros));
  const median =
    series.median_micros === null || series.median_micros === undefined
      ? null
      : microsToUnits(Number(series.median_micros));
  const mad =
    series.mad_micros === null || series.mad_micros === undefined
      ? 0
      : microsToUnits(Number(series.mad_micros));

  const min = Math.min(...values, median ?? Infinity);
  const max = Math.max(...values, median ?? -Infinity);
  const span = max - min || 1;

  const x = (index: number): number =>
    pad + (index * (width - pad * 2)) / (points.length - 1);
  const y = (value: number): number =>
    height - pad - ((value - min) / span) * (height - pad * 2);

  const path = values.map((value, index) => `${index === 0 ? "M" : "L"} ${x(index)} ${y(value)}`).join(" ");
  const flaggedIndex = points.findIndex(
    (point) => point.work_item_id === series.flagged_work_item_id,
  );

  return (
    <div className="space-y-2">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        role="img"
        aria-label={`Unit price history for ${series.sku}`}
      >
        {median !== null && mad > 0 ? (
          <rect
            x={pad}
            y={y(median + mad)}
            width={width - pad * 2}
            height={Math.max(2, y(median - mad) - y(median + mad))}
            className="fill-muted"
            opacity={0.55}
          />
        ) : null}
        {median !== null ? (
          <line
            x1={pad}
            x2={width - pad}
            y1={y(median)}
            y2={y(median)}
            className="stroke-muted-foreground"
            strokeDasharray="4 4"
            strokeWidth={1}
          />
        ) : null}
        <path d={path} fill="none" className="stroke-primary" strokeWidth={2} />
        {values.map((value, index) => (
          <circle
            key={points[index]?.work_item_id ?? index}
            cx={x(index)}
            cy={y(value)}
            r={index === flaggedIndex ? 5 : 3}
            className={
              index === flaggedIndex ? "fill-destructive" : "fill-primary"
            }
          />
        ))}
      </svg>
      <p className={HINT}>
        {median === null
          ? "No median available."
          : `Median ${median.toFixed(2)} ${series.currency}${
              mad > 0
                ? `, typical spread ±${mad.toFixed(2)}.`
                : ". This item's price had not moved before now, so there is no historical spread to compare against and the finding rests on the size of the change alone."
            }`}
      </p>
      {series.z !== null && series.z !== undefined ? (
        <p className={HINT}>Robust z-score of the latest price: {Number(series.z).toFixed(2)}.</p>
      ) : null}
    </div>
  );
};

const EvidenceBlock: React.FC<{ readonly item: AnomalyEvidence }> = ({ item }) => {
  const label = item.label ?? "Evidence";

  if (item.kind === "identifiers" || item.kind === "chunk_pair") {
    const left = side(item, "subject");
    const right = side(item, "counterpart");
    // F-174: a vendor is evidenced by its normalised key ("name:acme industrial supplies"); show it readable.
    const shown = (value: unknown): string =>
      label === "Vendor" ? vendorLabel(null, asText(value)) : asText(value);
    return (
      <SideBySide
        label={label}
        leftTitle="This document"
        rightTitle="Matched document"
        left={shown(left["text"] ?? left["value"] ?? left["work_item_id"])}
        right={shown(right["text"] ?? right["value"] ?? right["work_item_id"])}
        note={item.note}
      />
    );
  }

  if (item.kind === "clause_pair") {
    const left = side(item, "subject");
    const right = side(item, "counterpart");
    return (
      <SideBySide
        label={label}
        leftTitle={`This version — ${asText(left["value"] ?? left["literal"])} ${asText(left["unit"] ?? "")}`.trim()}
        rightTitle={`Earlier version — ${asText(right["value"] ?? right["literal"])} ${asText(right["unit"] ?? "")}`.trim()}
        left={<blockquote className="italic">{asText(left["quote"])}</blockquote>}
        right={<blockquote className="italic">{asText(right["quote"])}</blockquote>}
        note={item.note}
      />
    );
  }

  if (item.kind === "shingles") {
    const samples = (item["samples"] as string[] | undefined) ?? [];
    return (
      <div className={`${SURFACE_INSET} space-y-2 p-3`}>
        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
          {label}
        </p>
        <p className="text-sm text-foreground">
          {percent(item["jaccard_estimate"] as string)}% of line-item phrases appear on both documents.
        </p>
        <ul className="space-y-1">
          {samples.map((sample) => (
            <li key={sample} className="font-mono text-xs text-muted-foreground">
              {sample}
            </li>
          ))}
        </ul>
        {item.note ? <p className={HINT}>{item.note}</p> : null}
      </div>
    );
  }

  // price_series is rendered by the chart; anything unrecognised degrades to a
  // labelled block rather than disappearing.
  if (item.kind === "price_series") {return null;}

  return (
    <div className={`${SURFACE_INSET} space-y-2 p-3`}>
      <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
      <pre className="overflow-x-auto text-xs text-muted-foreground">
        {JSON.stringify(item, null, 2)}
      </pre>
    </div>
  );
};

/**
 * A zero-prop route component, like every other page under `pages/`.
 *
 * The workspace and the capability are resolved from the same two hooks
 * `procurement/CaseQueue` uses, rather than passed in. A page that took them
 * as props would need a wrapper at the route, and the wrapper would be the
 * one place the capability check could be forgotten.
 */
export const ForensicAuditRadar: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const capability = useCapabilityAccess(organizationId, ANOMALY_RADAR_CAPABILITY);
  const hasCapability = capability.granted;

  const queryClient = useQueryClient();
  const [tab, setTab] = useState<TabKey>("ALL");
  const [severity, setSeverity] = useState<AnomalySeverity | "">("");
  const [status, setStatus] = useState<AnomalyStatus | "">("OPEN");
  const [openId, setOpenId] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [showMatrix, setShowMatrix] = useState(true);
  // A reason typed for one finding is not carried to the next one opened.
  useEffect(() => setReason(""), [openId]);

  const filters = useMemo(
    () => ({
      ...(tab === "ALL" ? {} : { kind: tab }),
      ...(severity === "" ? {} : { severity }),
      ...(status === "" ? {} : { status }),
    }),
    [tab, severity, status],
  );

  const feed = useQuery({
    queryKey: ["radar", "feed", workspaceId, filters],
    queryFn: () => listAnomalies(workspaceId, filters),
    enabled: Boolean(workspaceId) && hasCapability,
  });

  const detail = useQuery({
    queryKey: ["radar", "finding", workspaceId, openId],
    queryFn: () => getAnomaly(workspaceId, openId as string),
    enabled: Boolean(workspaceId) && hasCapability && openId !== null,
  });

  const vendorKey = detail.data?.metrics?.["vendor_key"] as string | undefined;
  const sku = detail.data?.metrics?.["sku"] as string | undefined;

  const series = useQuery({
    queryKey: ["radar", "series", workspaceId, vendorKey, sku],
    queryFn: () => getPriceSeries(workspaceId, vendorKey as string, sku as string),
    enabled:
      Boolean(workspaceId) &&
      hasCapability &&
      detail.data?.kind === "PRICE_SURGE" &&
      typeof vendorKey === "string" &&
      typeof sku === "string",
  });

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: ["radar", "feed", workspaceId] });
    void queryClient.invalidateQueries({ queryKey: ["radar", "finding", workspaceId] });
  };

  const confirm = useMutation({
    mutationFn: (id: string) => confirmAnomaly(workspaceId, id),
    onSuccess: invalidate,
  });

  const dismiss = useMutation({
    mutationFn: (input: { id: string; reason: string }) =>
      dismissAnomaly(workspaceId, input.id, input.reason),
    onSuccess: () => {
      setReason("");
      invalidate();
    },
  });

  if (capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading&hellip;
      </div>
    );
  }

  if (!hasCapability) {
    return (
      <div className="p-6">
        <CapabilityLockCard canChangePlan={workspace?.role === "ADMIN"} />
      </div>
    );
  }

  const errorOf = (error: unknown): string =>
    error instanceof ApiError ? error.message : "Something went wrong.";

  const counts = feed.data?.counts;
  const items = feed.data?.items ?? [];

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-1">
        <h1 className={PAGE_TITLE}>Audit radar</h1>
        <p className={HINT}>
          Duplicate documents, price changes and contract drift, raised as documents arrive and checked
          nightly; payment risk first, because money leaves on it.
        </p>
      </header>

      {counts ? (
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-5" role="group" aria-label="Findings at a glance">
          {(
            [
              { key: "HIGH", label: "Open high", value: counts.high, tone: "text-red-600 dark:text-red-400", bar: "bg-red-500" },
              { key: "MEDIUM", label: "Open medium", value: counts.medium, tone: "text-amber-600 dark:text-amber-400", bar: "bg-amber-500" },
              { key: "LOW", label: "Open low", value: counts.low, tone: "text-sky-600 dark:text-sky-400", bar: "bg-sky-500" },
              { key: "CONFIRMED", label: "Confirmed", value: counts.confirmed, tone: "text-foreground", bar: "bg-foreground/40" },
              { key: "DISMISSED", label: "Dismissed", value: counts.dismissed, tone: "text-muted-foreground", bar: "bg-muted-foreground/40" },
            ] as const
          ).map((tile) => {
            const isSeverity = tile.key === "HIGH" || tile.key === "MEDIUM" || tile.key === "LOW";
            const active = isSeverity ? severity === tile.key && status === "OPEN" : status === tile.key && severity === "";
            return (
              <button
                key={tile.key}
                type="button"
                aria-pressed={active}
                onClick={() => {
                  if (active) {
                    setSeverity("");
                    setStatus("OPEN");
                  } else if (isSeverity) {
                    setSeverity(tile.key);
                    setStatus("OPEN");
                  } else {
                    setSeverity("");
                    setStatus(tile.key);
                  }
                }}
                className={`${SURFACE} relative overflow-hidden px-3 py-2 text-left transition-colors hover:bg-muted/40 ${active ? "ring-2 ring-primary/60" : ""}`}
              >
                <span aria-hidden className={`absolute inset-x-0 top-0 h-0.5 ${tile.bar}`} />
                <span className="block text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground">{tile.label}</span>
                <span className={`block text-2xl font-semibold tabular-nums ${tile.tone}`}>{tile.value}</span>
              </button>
            );
          })}
        </div>
      ) : null}

      <PaymentRiskPanel workspaceId={workspaceId} role={workspace?.role ?? "VIEWER"} />

      <div className={`${SURFACE} flex flex-wrap items-center gap-2 p-3`}>
        <div className="flex flex-wrap gap-1">
          {TABS.map((entry) => (
            <button
              key={entry.key}
              type="button"
              onClick={() => setTab(entry.key)}
              className={tab === entry.key ? BUTTON_SECONDARY : BUTTON_GHOST}
            >
              {entry.label}
            </button>
          ))}
        </div>
        <div className="ml-auto flex gap-2">
          <select
            className={`${SELECT} w-auto min-w-[9.5rem]`}
            value={severity}
            aria-label="Severity"
            onChange={(event) =>
              setSeverity(event.target.value as AnomalySeverity | "")
            }
          >
            <option value="">Any severity</option>
            <option value="HIGH">High</option>
            <option value="MEDIUM">Medium</option>
            <option value="LOW">Low</option>
          </select>
          <select
            className={`${SELECT} w-auto min-w-[9rem]`}
            value={status}
            aria-label="Status"
            onChange={(event) => setStatus(event.target.value as AnomalyStatus | "")}
          >
            <option value="OPEN">Open</option>
            <option value="CONFIRMED">Confirmed</option>
            <option value="DISMISSED">Dismissed</option>
            <option value="">Any status</option>
          </select>
        </div>
      </div>

      {feed.isPending ? (
        <p className={HINT}>Loading findings&hellip;</p>
      ) : feed.isError ? (
        <p className="text-sm text-destructive">{errorOf(feed.error)}</p>
      ) : items.length === 0 ? (
        <div className={`${SURFACE} p-6`}>
          <p className="text-sm text-muted-foreground">
            Nothing flagged. Duplicate detection runs as documents arrive; price
            changes and contract drift are checked nightly.
          </p>
        </div>
      ) : (
        <>
        {(tab === "ALL" || tab === "DUPLICATE_DOCUMENT") ? (
          <DuplicateMatrix
            findings={items}
            open={showMatrix}
            onToggle={() => setShowMatrix((value) => !value)}
            selected={openId}
            onSelect={(id) => {
              setOpenId(id);
              window.requestAnimationFrame(() =>
                document.getElementById(`finding-${id}`)?.scrollIntoView({ behavior: "smooth", block: "start" }),
              );
            }}
          />
        ) : null}
        <div className={`${SURFACE} overflow-hidden`}>
        <div
          aria-hidden
          className="hidden grid-cols-[5.5rem_minmax(0,1fr)_9rem_6.5rem_9.5rem_1.25rem] gap-3 border-b border-border bg-muted/40 px-4 py-2 text-[10.5px] font-semibold uppercase tracking-[0.07em] text-muted-foreground md:grid"
        >
          <span>Severity</span>
          <span>Finding</span>
          <span>Kind</span>
          <span>Confidence</span>
          <span>Raised</span>
          <span />
        </div>
        <ul className="divide-y divide-border" aria-label="Findings">
          {items.map((finding: AnomalyFindingSummary) => (
            <li key={finding.id} id={`finding-${finding.id}`} className={`relative ${openId === finding.id ? "bg-primary/[0.03]" : ""}`}>
              <span aria-hidden className={`absolute inset-y-0 left-0 w-[3px] ${SEVERITY_STRIPE[finding.severity]}`} />
              <button
                type="button"
                className="grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-x-3 gap-y-1 px-4 py-2.5 text-left hover:bg-muted/40 md:grid-cols-[5.5rem_minmax(0,1fr)_9rem_6.5rem_9.5rem_1.25rem]"
                onClick={() =>
                  setOpenId((current) => (current === finding.id ? null : finding.id))
                }
                aria-expanded={openId === finding.id}
              >
                <span>
                  <SeverityBadge severity={finding.severity} />
                </span>
                <span className="min-w-0">
                  <span className="block text-sm font-medium leading-snug text-foreground [overflow-wrap:anywhere]">
                    {finding.headline}
                  </span>
                  <span className={`${HINT} block`}>
                    {LAYER_LABELS[finding.layer]}
                    {finding.status === "OPEN" ? "" : ` · ${finding.status.toLowerCase()}`}
                  </span>
                </span>
                <span className="col-span-2 flex items-center gap-1.5 text-xs text-muted-foreground md:col-span-1">
                  <KindIcon kind={finding.kind} />
                  {KIND_LABELS[finding.kind]}
                </span>
                <span className="hidden items-center gap-1.5 md:flex" title={`${percent(finding.score)}% confident`}>
                  <span className="relative h-1.5 w-10 overflow-hidden rounded-full bg-muted">
                    <span className="absolute inset-y-0 left-0 rounded-full bg-foreground/60" style={{ width: `${percent(finding.score)}%` }} />
                  </span>
                  <span className="text-xs tabular-nums text-muted-foreground">{percent(finding.score)}%</span>
                </span>
                <span className="hidden text-xs tabular-nums text-muted-foreground md:block">
                  {formatTimestamp(finding.created_at)}
                </span>
                <ChevronRight
                  aria-hidden
                  className={`hidden h-4 w-4 text-muted-foreground transition-transform md:block ${openId === finding.id ? "rotate-90" : ""}`}
                />
              </button>

              {openId === finding.id ? (
                <div className="space-y-3 border-t border-border/60 px-4 pb-4 pt-4 md:pl-[7.75rem]">
                  {detail.isPending ? (
                    <p className={HINT}>Loading evidence&hellip;</p>
                  ) : detail.isError ? (
                    <p className="text-sm text-destructive">{errorOf(detail.error)}</p>
                  ) : detail.data ? (
                    <>
                      {detail.data.layer === "L3" ? (
                        <p className="flex items-start gap-2 text-xs text-amber-600">
                          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                          This match rests on how the two documents read, not on
                          an identifier they share. It is capped at medium
                          severity for that reason.
                        </p>
                      ) : null}

                      {detail.data.kind === "PRICE_SURGE" && series.data ? (
                        <PriceChart series={series.data} />
                      ) : null}

                      {detail.data.evidence.map((item, index) => (
                        <EvidenceBlock key={`${item.kind}-${index}`} item={item} />
                      ))}

                      {detail.data.status === "OPEN" ? (
                        <div className="space-y-2">
                          <label className={HINT} htmlFor={`reason-${finding.id}`}>
                            If this is not an anomaly, say why. The reason is kept
                            and stops the same match being raised again.
                          </label>
                          <textarea
                            id={`reason-${finding.id}`}
                            className={TEXTAREA}
                            value={reason}
                            onChange={(event) => setReason(event.target.value)}
                            placeholder="e.g. the supplier resets invoice numbers every April"
                          />
                          <div className="flex flex-wrap gap-2">
                            <button
                              type="button"
                              className={BUTTON_PRIMARY}
                              disabled={confirm.isPending}
                              onClick={() => confirm.mutate(finding.id)}
                            >
                              This is a real finding
                            </button>
                            <button
                              type="button"
                              className={BUTTON_SECONDARY}
                              disabled={dismiss.isPending || reason.trim().length === 0}
                              onClick={() =>
                                dismiss.mutate({ id: finding.id, reason: reason.trim() })
                              }
                            >
                              Not an anomaly
                            </button>
                          </div>
                          {dismiss.isError ? (
                            <p className="text-sm text-destructive">
                              {errorOf(dismiss.error)}
                            </p>
                          ) : null}
                          {confirm.isError ? (
                            <p className="text-sm text-destructive">
                              {errorOf(confirm.error)}
                            </p>
                          ) : null}
                        </div>
                      ) : (
                        <p className={HINT}>
                          {detail.data.status === "CONFIRMED" ? "Confirmed" : "Dismissed"}{" "}
                          {formatTimestamp(detail.data.resolved_at)}
                          {detail.data.resolution_note
                            ? ` — ${detail.data.resolution_note}`
                            : ""}
                        </p>
                      )}
                    </>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
        </div>
        </>
      )}
    </div>
  );
};

const SEVERITY_STRIPE: Readonly<Record<AnomalySeverity, string>> = {
  HIGH: "bg-red-500",
  MEDIUM: "bg-amber-500",
  LOW: "bg-sky-500",
};

const SEVERITY_CELL: Readonly<Record<AnomalySeverity, string>> = {
  HIGH: "bg-red-500 hover:bg-red-600",
  MEDIUM: "bg-amber-500 hover:bg-amber-600",
  LOW: "bg-sky-500 hover:bg-sky-600",
};

const MATRIX_LIMIT = 14;

/**
 * Phase 2 — which documents the duplicate findings tie together, as a matrix: a document per row
 * and per column, a filled cell where the radar found the two to be the same document (coloured by
 * severity, hollow once decided). Five copies of one invoice read as one dense block instead of ten
 * list rows; click a cell to open that finding below.
 */
const DuplicateMatrix: React.FC<{
  readonly findings: readonly AnomalyFindingSummary[];
  readonly open: boolean;
  readonly onToggle: () => void;
  readonly selected: string | null;
  readonly onSelect: (id: string) => void;
}> = ({ findings, open, onToggle, selected, onSelect }) => {
  const pairs = findings.filter(
    (finding) => finding.kind === "DUPLICATE_DOCUMENT" && finding.counterpart_work_item_id,
  );
  const documents = new Map<string, string>();
  for (const finding of pairs) {
    documents.set(finding.subject_work_item_id, finding.subject_label);
    documents.set(finding.counterpart_work_item_id as string, finding.counterpart_label);
  }
  if (pairs.length < 2 || documents.size < 3) {
    return null;
  }
  const degree = new Map<string, number>();
  for (const finding of pairs) {
    for (const id of [finding.subject_work_item_id, finding.counterpart_work_item_id as string]) {
      degree.set(id, (degree.get(id) ?? 0) + 1);
    }
  }
  const ids = [...documents.keys()]
    .sort((a, b) => (degree.get(b) ?? 0) - (degree.get(a) ?? 0) || (documents.get(a) ?? "").localeCompare(documents.get(b) ?? ""))
    .slice(0, MATRIX_LIMIT);
  const cell = new Map<string, AnomalyFindingSummary>();
  for (const finding of pairs) {
    const a = finding.subject_work_item_id;
    const b = finding.counterpart_work_item_id as string;
    cell.set(`${a}|${b}`, finding);
    cell.set(`${b}|${a}`, finding);
  }
  return (
    <section className={`${SURFACE} overflow-hidden`} aria-labelledby="duplicate-matrix-title">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-4 py-2.5 text-left hover:bg-muted/40"
      >
        <Grid3x3 className="h-4 w-4 text-muted-foreground" aria-hidden />
        <span id="duplicate-matrix-title" className="text-sm font-semibold">Duplicate matrix</span>
        <span className="text-xs text-muted-foreground">
          {pairs.length} pairs across {documents.size} documents
          {documents.size > MATRIX_LIMIT ? ` (the ${MATRIX_LIMIT} most connected shown)` : ""}
        </span>
        <ChevronRight aria-hidden className={`ml-auto h-4 w-4 text-muted-foreground transition-transform ${open ? "rotate-90" : ""}`} />
      </button>
      {open ? (
        <div className="overflow-x-auto overscroll-x-contain border-t border-border/60 p-4">
          <table className="border-separate border-spacing-1 text-xs" data-testid="duplicate-matrix">
            <thead>
              <tr>
                <th scope="col" className="sr-only">Document</th>
                {ids.map((id, index) => (
                  <th key={id} scope="col" className="h-6 w-6 text-center font-mono text-[10.5px] font-semibold text-muted-foreground" title={documents.get(id)}>
                    {index + 1}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ids.map((row, rowIndex) => (
                <tr key={row}>
                  <th scope="row" className="max-w-[16rem] whitespace-nowrap pr-2 text-left font-normal">
                    <span className="mr-1.5 inline-block w-4 text-right font-mono text-[10.5px] font-semibold text-muted-foreground">{rowIndex + 1}</span>
                    <span className="inline-block max-w-[14rem] truncate align-bottom text-foreground" title={documents.get(row)}>
                      {documents.get(row)}
                    </span>
                  </th>
                  {ids.map((column) => {
                    if (row === column) {
                      return <td key={column} className="h-6 w-6 rounded bg-muted/60" aria-hidden />;
                    }
                    const finding = cell.get(`${row}|${column}`);
                    if (!finding) {
                      return <td key={column} className="h-6 w-6 rounded border border-dashed border-border/70" aria-hidden />;
                    }
                    const decided = finding.status !== "OPEN";
                    return (
                      <td key={column} className="h-6 w-6 p-0">
                        <button
                          type="button"
                          onClick={() => onSelect(finding.id)}
                          aria-label={`${documents.get(row)} and ${documents.get(column)}: ${finding.severity.toLowerCase()} duplicate, ${finding.status.toLowerCase()}`}
                          title={`${documents.get(row)} ↔ ${documents.get(column)}`}
                          className={`block h-6 w-6 rounded ${decided ? "border-2 border-muted-foreground/50 bg-transparent hover:bg-muted" : SEVERITY_CELL[finding.severity]} ${selected === finding.id ? "ring-2 ring-primary ring-offset-1 ring-offset-card" : ""}`}
                        />
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
};

export default ForensicAuditRadar;
