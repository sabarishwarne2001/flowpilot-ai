import React, { useMemo, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, FileWarning, Loader2, Scale, TrendingUp } from "lucide-react";

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
    return (
      <SideBySide
        label={label}
        leftTitle="This document"
        rightTitle="Matched document"
        left={asText(left["text"] ?? left["value"] ?? left["work_item_id"])}
        right={asText(right["text"] ?? right["value"] ?? right["work_item_id"])}
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
      <header className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className={PAGE_TITLE}>Audit radar</h1>
        {counts ? (
          <p className={HINT}>
            {counts.high} high &middot; {counts.medium} medium &middot;{" "}
            {counts.dismissed} dismissed
          </p>
        ) : null}
      </header>

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
            className={SELECT}
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
            className={SELECT}
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
        <ul className="space-y-2">
          {items.map((finding: AnomalyFindingSummary) => (
            <li key={finding.id} className={`${SURFACE} p-4`}>
              <button
                type="button"
                className="w-full space-y-1 text-left"
                onClick={() =>
                  setOpenId((current) => (current === finding.id ? null : finding.id))
                }
                aria-expanded={openId === finding.id}
              >
                <div className="flex items-center gap-2">
                  <SeverityBadge severity={finding.severity} />
                  <KindIcon kind={finding.kind} />
                  <span className="text-sm font-medium text-foreground">
                    {finding.headline}
                  </span>
                </div>
                <p className={HINT}>
                  {LAYER_LABELS[finding.layer]} &middot;{" "}
                  {percent(finding.score)}% &middot;{" "}
                  {formatTimestamp(finding.created_at)}
                  {finding.status === "OPEN" ? "" : ` · ${finding.status.toLowerCase()}`}
                </p>
              </button>

              {openId === finding.id ? (
                <div className="mt-4 space-y-3 border-t border-border/60 pt-4">
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
      )}
    </div>
  );
};

export default ForensicAuditRadar;
