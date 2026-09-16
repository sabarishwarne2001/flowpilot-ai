import React, { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CirclePause, Info, Loader2, RotateCcw, ShieldCheck } from "lucide-react";

import AutonomyLockCard from "@/components/autonomy/AutonomyLockCard";
import { CoverageChart, ReliabilityDiagram } from "@/components/autonomy/AutonomyCharts";
import {
  BUTTON_PRIMARY,
  BUTTON_SECONDARY,
  FIELD_LABEL,
  HINT,
  PAGE_TITLE,
  SECTION_TITLE,
  SURFACE,
  SURFACE_INSET,
} from "@/components/ui/primitives";
import { StatusPill } from "@/components/ui/StatusPill";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  alphaFromTenths,
  auditRateFromPercent,
  getAutonomyOverview,
  getAutonomyReliability,
  resumeAutonomy,
  updateAutonomySettings,
} from "@/services/api/autonomy";
import { ApiError } from "@/services/api/errors";
import { autonomyKeys } from "@/services/api/queryKeys";
import type { AutonomyEntry } from "@/types/autonomy";
import {
  CALIBRATED_AUTONOMY_CAPABILITY,
  METHOD_LABELS,
  formatRate,
  outcomeForAlpha,
  toNumber,
} from "@/types/autonomy";
import { formatTimestamp } from "@/utils/displayTime";

/**
 * ARCH-35 §6.7 — Autonomy settings.
 *
 * WHAT THE SLIDER MEANS
 * =====================
 *
 * α is "wrong automatic approvals per 100 documents", which is what the
 * conformal bound actually controls. The number a buyer asks for — how often
 * an automatic approval is wrong — is the Clopper-Pearson figure shown beside
 * it. Both are read off the error-versus-coverage curve the server computed
 * when the model was fitted, so moving the slider changes nothing until Save,
 * and what it previews is exactly what Save will enforce.
 *
 * EVERY SENTENCE ABOUT A PROMISE COMES FROM THE SERVER
 * ====================================================
 *
 * `entry.summary` and `suspended_reason` are rendered verbatim. They are
 * assembled from stored numbers in `app/services/calibration/overview.py`;
 * rephrasing them here would put the wording of a guarantee one refactor away
 * from the arithmetic behind it.
 */

const errorOf = (error: unknown): string =>
  error instanceof ApiError ? error.message : "Something went wrong.";

const statusLabel = (entry: AutonomyEntry): { status: string; label: string } => {
  if (entry.status === "SUSPENDED") {
    return { status: "SUSPENDED", label: "Paused" };
  }
  if (entry.model_id === null || entry.method === "PRIOR") {
    return { status: "PENDING", label: "Collecting reviews" };
  }
  if (entry.stale) {
    return { status: "EXPIRED", label: "Check overdue" };
  }
  if (!entry.automated) {
    return { status: "INFO", label: "Measured" };
  }
  return entry.achievable
    ? { status: "ACTIVE", label: "Automatic within limit" }
    : { status: "PAUSED", label: "Everything reviewed" };
};

const Stat: React.FC<{ readonly label: string; readonly value: string; readonly hint?: string | undefined }> = ({
  label,
  value,
  hint,
}) => (
  <div className={`${SURFACE_INSET} p-3`}>
    <p className={HINT}>{label}</p>
    <p className="text-sm font-semibold text-foreground">{value}</p>
    {hint ? <p className={HINT}>{hint}</p> : null}
  </div>
);

export const AutonomySettings: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const role = String(organizationRole).toUpperCase();
  const isOwner = role === "OWNER";
  const queryClient = useQueryClient();
  const capability = useCapabilityAccess(organizationId, CALIBRATED_AUTONOMY_CAPABILITY);

  const [selected, setSelected] = useState<string>("verification.document");
  const [alphaTenths, setAlphaTenths] = useState<number>(50);
  const [auditPercent, setAuditPercent] = useState<number>(2);
  const [notice, setNotice] = useState<string | null>(null);

  const overview = useQuery({
    queryKey: autonomyKeys.overview(organizationId),
    queryFn: () => getAutonomyOverview(organizationId),
    enabled: Boolean(organizationId) && capability.granted,
    staleTime: 30_000,
  });

  const reliability = useQuery({
    queryKey: autonomyKeys.reliability(organizationId, selected),
    queryFn: () => getAutonomyReliability(organizationId, selected),
    enabled: Boolean(organizationId) && capability.granted,
    staleTime: 30_000,
  });

  const entry = useMemo<AutonomyEntry | null>(
    () => overview.data?.entries.find((item) => item.decision_type === selected) ?? null,
    [overview.data, selected],
  );

  // Reset the sliders to the stored settings whenever the selection or the
  // stored settings change. Integer positions only; see services/api/autonomy.
  useEffect(() => {
    if (!entry) {
      return;
    }
    setAlphaTenths(Math.round((toNumber(entry.target_error_rate) ?? 0.05) * 1000));
    setAuditPercent(Math.round((toNumber(entry.audit_sample_rate) ?? 0.02) * 100));
    setNotice(null);
  }, [entry]);

  const alpha = alphaTenths / 1000;
  const auditRate = auditPercent / 100;
  const preview = useMemo(
    () => outcomeForAlpha(reliability.data?.coverage_curve ?? [], alpha, auditRate),
    [reliability.data, alpha, auditRate],
  );

  const invalidate = async (): Promise<void> => {
    await queryClient.invalidateQueries({ queryKey: autonomyKeys.all(organizationId) });
  };

  const save = useMutation({
    mutationFn: () =>
      updateAutonomySettings(organizationId, selected, {
        target_error_rate: alphaFromTenths(alphaTenths),
        audit_sample_rate: auditRateFromPercent(auditPercent),
      }),
    onSuccess: async (result) => {
      setNotice(result.message);
      await invalidate();
    },
  });

  const resume = useMutation({
    mutationFn: () => resumeAutonomy(organizationId, selected),
    onSuccess: async (result) => {
      setNotice(result.message);
      await invalidate();
    },
    onError: async () => {
      await invalidate();
    },
  });

  if (capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> Loading&hellip;
      </div>
    );
  }

  if (!capability.granted) {
    return (
      <div className="p-6">
        <AutonomyLockCard canChangePlan={isOwner} />
      </div>
    );
  }

  const minLabels = overview.data?.min_labels ?? 50;
  const confidencePct = Math.round((overview.data?.confidence ?? 0.95) * 100);
  const dirty =
    entry !== null &&
    (alphaFromTenths(alphaTenths) !== Number(entry.target_error_rate).toFixed(5) ||
      auditRateFromPercent(auditPercent) !== Number(entry.audit_sample_rate).toFixed(4));
  const coldStart = entry !== null && entry.label_count < minLabels;

  return (
    <div className="space-y-4">
      <header className="space-y-1">
        <h1 className={PAGE_TITLE}>Calibrated autonomy</h1>
        <p className="text-sm text-muted-foreground">
          Choose how often an automatic approval may be wrong. The platform lets
          documents through without review only as far as your own reviewed
          documents show that limit holds, and pauses itself when they stop
          showing it.
        </p>
      </header>

      {overview.isPending ? (
        <p className={HINT}>Loading&hellip;</p>
      ) : overview.isError ? (
        <p className="text-sm text-destructive">{errorOf(overview.error)}</p>
      ) : (
        <div className="grid gap-4 lg:grid-cols-[18rem_1fr]">
          <nav className={`${SURFACE} p-2`} aria-label="Decision types">
            <ul className="space-y-1">
              {(overview.data?.entries ?? []).map((item) => {
                const pill = statusLabel(item);
                return (
                  <li key={item.decision_type}>
                    <button
                      type="button"
                      onClick={() => setSelected(item.decision_type)}
                      aria-current={item.decision_type === selected}
                      className={`w-full rounded-lg px-3 py-2 text-left transition-colors ${
                        item.decision_type === selected ? "bg-muted" : "hover:bg-muted/50"
                      }`}
                    >
                      <span className="block text-sm font-medium text-foreground">
                        {item.display_name}
                      </span>
                      <span className="mt-1 flex items-center gap-2">
                        <StatusPill status={pill.status} label={pill.label} />
                        <span className={HINT}>{item.label_count} reviewed</span>
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </nav>

          {entry ? (
            <div className="space-y-4">
              <section className={`${SURFACE} space-y-3 p-5`}>
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <h2 className={SECTION_TITLE}>{entry.display_name}</h2>
                    <p className={HINT}>
                      {entry.method ? METHOD_LABELS[entry.method] : "No model yet"}
                      {entry.fitted_at ? ` · fitted ${formatTimestamp(entry.fitted_at)}` : ""}
                      {entry.last_checked_at
                        ? ` · checked ${formatTimestamp(entry.last_checked_at)}`
                        : ""}
                    </p>
                  </div>
                  <StatusPill {...statusLabel(entry)} />
                </div>

                {entry.status === "SUSPENDED" ? (
                  <div className="flex flex-wrap items-start gap-3 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3">
                    <CirclePause className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
                    <p className="flex-1 text-sm text-foreground">{entry.suspended_reason}</p>
                    {isOwner ? (
                      <button
                        type="button"
                        className={BUTTON_SECONDARY}
                        disabled={resume.isPending}
                        onClick={() => resume.mutate()}
                      >
                        {resume.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : (
                          <RotateCcw className="h-3.5 w-3.5" aria-hidden />
                        )}
                        Check again now
                      </button>
                    ) : null}
                  </div>
                ) : null}

                <p className="text-sm text-foreground">{entry.summary}</p>

                {coldStart ? (
                  <p className="flex items-start gap-2 text-sm text-muted-foreground">
                    <Info className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
                    With fewer than {minLabels} reviewed documents there is no way to
                    measure accuracy honestly, so nothing is approved automatically.
                    That is the correct outcome for a new or low-volume account, not a
                    fault. {entry.label_count} of {minLabels} so far.
                  </p>
                ) : null}

                {entry.last_rejection ? (
                  <p className="flex items-start gap-2 text-xs text-amber-600">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                    The latest refit was refused: {entry.last_rejection} The model in
                    force is unchanged.
                  </p>
                ) : null}

                <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
                  <Stat label="Reviewed documents" value={String(entry.label_count)} />
                  <Stat
                    label="Approved automatically"
                    value={formatRate(entry.auto_share)}
                    hint="Share of documents like these, at the saved limit"
                  />
                  <Stat
                    label="Wrong automatic approvals"
                    value={`≤ ${formatRate(entry.conformal_bound)} of documents`}
                    hint={`Limit chosen: ${formatRate(entry.target_error_rate)}`}
                  />
                  <Stat
                    label={`Error among automatic approvals (${confidencePct}% confidence)`}
                    value={`≤ ${formatRate(entry.clopper_pearson_upper)}`}
                  />
                  <Stat
                    label="Confidence needed"
                    value={formatRate(entry.threshold)}
                    hint="Calibrated, not the raw score"
                  />
                  <Stat
                    label="Calibration error"
                    value={`${formatRate(entry.ece_after)} (raw ${formatRate(entry.ece_before)})`}
                    hint="Expected calibration error on held-out reviews"
                  />
                  <Stat label="Brier score" value={entry.brier_after ?? "—"} />
                  <Stat
                    label="Audit share"
                    value={formatRate(entry.audit_sample_rate)}
                    hint={
                      reliability.data
                        ? `${reliability.data.audit_labels} audit reviews in the current fit`
                        : undefined
                    }
                  />
                </div>
              </section>

              {entry.automated ? (
                <section className={`${SURFACE} space-y-4 p-5`} aria-labelledby="autonomy-limit">
                  <h3 id="autonomy-limit" className={SECTION_TITLE}>
                    Error limit
                  </h3>
                  <div className="space-y-2">
                    <label className={FIELD_LABEL} htmlFor="autonomy-alpha">
                      Wrong automatic approvals allowed: {formatRate(alpha)} of documents
                    </label>
                    <input
                      id="autonomy-alpha"
                      type="range"
                      min={5}
                      max={200}
                      step={5}
                      value={alphaTenths}
                      disabled={!isOwner}
                      onChange={(event) => setAlphaTenths(Number(event.target.value))}
                      className="w-full"
                    />
                    {reliability.isPending ? (
                      <p className={HINT}>Loading the measured curve&hellip;</p>
                    ) : preview.achievable && preview.point ? (
                      <p className="text-sm text-foreground">
                        At {formatRate(alpha)}, about {formatRate(preview.autoShare)} of
                        documents like these would be approved automatically and{" "}
                        {formatRate(preview.reviewShare)} would go to review (audits
                        included). Among automatic approvals the error rate would be at
                        most {formatRate(preview.point.clopper_pearson_upper)} with{" "}
                        {confidencePct}% confidence; {formatRate(preview.point.observed_error)}{" "}
                        was observed on the {preview.point.passed} reviewed documents that
                        would have passed.
                      </p>
                    ) : (
                      <p className="text-sm text-foreground">
                        A {formatRate(alpha)} limit is not achievable on your current
                        evidence, so everything would go to review. Raise the limit, or
                        review more documents.
                      </p>
                    )}
                  </div>

                  <div className="space-y-2">
                    <label className={FIELD_LABEL} htmlFor="autonomy-audit">
                      Audit share: {auditPercent}% of automatic approvals still reviewed
                    </label>
                    <input
                      id="autonomy-audit"
                      type="range"
                      min={1}
                      max={25}
                      step={1}
                      value={auditPercent}
                      disabled={!isOwner}
                      onChange={(event) => setAuditPercent(Number(event.target.value))}
                      className="w-full"
                    />
                    <p className="flex items-start gap-2 text-xs text-muted-foreground">
                      <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                      A random share of automatic approvals is still sent to a person
                      and marked as an audit in the review queue. Without audits the
                      only reviewed documents would be the uncertain ones, and nobody
                      could check that the limit still holds. Audits cannot be switched
                      off.
                    </p>
                  </div>

                  {isOwner ? (
                    <div className="flex flex-wrap items-center gap-2">
                      <button
                        type="button"
                        className={BUTTON_PRIMARY}
                        disabled={!dirty || save.isPending}
                        onClick={() => save.mutate()}
                      >
                        {save.isPending ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                        ) : null}
                        Save and recalculate
                      </button>
                      {save.isError ? (
                        <p className="text-sm text-destructive">{errorOf(save.error)}</p>
                      ) : null}
                    </div>
                  ) : (
                    <p className={HINT}>Only an organization owner can change the limit.</p>
                  )}
                  {resume.isError ? (
                    <p className="text-sm text-destructive">{errorOf(resume.error)}</p>
                  ) : null}
                  {notice ? <p className="text-sm text-foreground">{notice}</p> : null}
                </section>
              ) : (
                <section className={`${SURFACE} p-5`}>
                  <p className="text-sm text-muted-foreground">
                    Nothing is approved automatically on this decision, so there is no
                    limit to set. These figures show how often the engine is right on
                    your documents.
                  </p>
                </section>
              )}

              <section className={`${SURFACE} grid gap-6 p-5 xl:grid-cols-2`}>
                {reliability.isPending ? (
                  <p className={HINT}>Loading charts&hellip;</p>
                ) : reliability.isError ? (
                  <p className="text-sm text-destructive">{errorOf(reliability.error)}</p>
                ) : reliability.data ? (
                  <>
                    <div className="space-y-2">
                      <h3 className={SECTION_TITLE}>Reliability</h3>
                      <ReliabilityDiagram
                        bins={reliability.data.reliability}
                        rawBins={reliability.data.reliability_raw}
                        curve={reliability.data.fitted_curve}
                      />
                    </div>
                    <div className="space-y-2">
                      <h3 className={SECTION_TITLE}>Error against coverage</h3>
                      <CoverageChart
                        curve={reliability.data.coverage_curve}
                        alpha={alpha}
                        selected={preview.point}
                      />
                      {reliability.data.last_check ? (
                        <p className={HINT}>
                          Last drift check {formatTimestamp(reliability.data.last_check.at)}:{" "}
                          {reliability.data.last_check.psi_checked &&
                          reliability.data.last_check.psi !== null
                            ? `score stability index ${reliability.data.last_check.psi.toFixed(2)} (pauses above ${reliability.data.psi_threshold.toFixed(2)})`
                            : "not enough recent documents to compare score distributions"}
                          {`; ${reliability.data.last_check.realized_wrong} wrong of ${reliability.data.last_check.realized_total} audited automatic approvals since the fit.`}
                        </p>
                      ) : null}
                    </div>
                  </>
                ) : null}
              </section>

              <p className={HINT}>
                These guarantees hold for documents like the ones you have reviewed. A
                new kind of document changes that, which is what the nightly drift
                check and the audits are there to catch.
              </p>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
};

export default AutonomySettings;
