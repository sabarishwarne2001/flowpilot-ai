/**
 * ARCH41-S3:page — Extraction Memory.
 *
 * What the workspace has learned from its reviewers, per document layout, and
 * the evidence that it helps: correction rates with and without memory, trials
 * written as sentences, and anchor rules with the replay record that earned
 * them. OFF / SHADOW / AUTO is the only switch; SHADOW measures without ever
 * changing an extraction, and is where a new workspace starts.
 */
import React from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Brain, FlaskConical, Loader2, Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { ErrorState } from "@/components/common/ErrorState";
import {
  BUTTON_GHOST,
  HINT,
  PAGE_TITLE,
  SCROLL_X,
  SECTION_TITLE,
  SURFACE,
  SURFACE_INSET,
  TABLE_HEAD,
  TABLE_ROW,
} from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { ApiError, errorMessage } from "@/services/api/errors";
import {
  changeMemoryRule,
  extractionMemoryKeys,
  getMemoryPotential,
  getMemorySettings,
  getMemorySummary,
  listMemoryRules,
  listMemoryTemplates,
  listMemoryTrials,
  putMemorySettings,
  resetMemoryTemplate,
} from "@/services/api/extractionMemory";
import type { MemoryMode } from "@/types/extractionMemory";

const MODES: ReadonlyArray<{ readonly mode: MemoryMode; readonly label: string; readonly help: string }> = [
  { mode: "OFF", label: "Off", help: "Nothing is learned and nothing is applied." },
  { mode: "SHADOW", label: "Shadow", help: "Learns and measures on every document. Never changes an extraction." },
  {
    mode: "AUTO",
    label: "Automatic",
    help: "Starts a trial per layout once there is enough evidence, and applies memory only where the trial proves it cuts corrections.",
  },
];

const STATE_TONE: Record<string, string> = {
  ACTIVE: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  TRIAL: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  RUNNING: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  PROMOTED: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  LEARNING: "bg-muted text-muted-foreground",
  SHADOW: "bg-muted text-muted-foreground",
  REJECTED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  RETIRED: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  ABANDONED: "bg-muted text-muted-foreground",
};

const Badge: React.FC<{ readonly value: string }> = ({ value }) => (
  <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${STATE_TONE[value] ?? "bg-muted"}`}>
    {value.toLowerCase()}
  </span>
);

const pct = (value?: number | null): string =>
  value === null || value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`;

const Stat: React.FC<{ readonly label: string; readonly value: React.ReactNode }> = ({ label, value }) => (
  <div className={`${SURFACE_INSET} p-3`}>
    <p className={HINT}>{label}</p>
    <p className="text-lg font-semibold">{value}</p>
  </div>
);

const LockedView: React.FC<{ readonly workspaceId: string; readonly canChangePlan: boolean }> = ({
  workspaceId,
  canChangePlan,
}) => {
  const potential = useQuery({
    queryKey: extractionMemoryKeys.potential(workspaceId),
    queryFn: () => getMemoryPotential(workspaceId),
    enabled: Boolean(workspaceId),
  });
  return (
    <section className={`${SURFACE} mx-auto max-w-2xl space-y-3 p-6`} aria-labelledby="memory-lock">
      <div className="flex items-center gap-2">
        <Lock className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 id="memory-lock" className={SECTION_TITLE}>
          Extraction memory
        </h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Every correction your reviewers make teaches FlowPilot how documents of that layout are written. Memory is
        applied only after a trial on your own documents shows it reduces corrections.
      </p>
      {potential.data && potential.data.corrected_fields > 0 ? (
        <p className="text-sm">
          Your reviewers have already corrected {potential.data.corrected_fields.toLocaleString()} field
          {potential.data.corrected_fields === 1 ? "" : "s"} across{" "}
          {potential.data.reviewed_documents.toLocaleString()} reviewed document
          {potential.data.reviewed_documents === 1 ? "" : "s"}.
        </p>
      ) : null}
      <p className={HINT}>
        {canChangePlan
          ? "It's included on the Business and Enterprise plans. Change your plan to turn it on."
          : "It's included on the Business and Enterprise plans. Ask an organization owner to change your plan."}
      </p>
    </section>
  );
};

const ExtractionMemory: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const isAdmin = workspace?.role === "ADMIN" || workspace?.role === "OWNER";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.extractionMemory);
  const queryClient = useQueryClient();
  const enabled = Boolean(workspaceId && capability.granted);

  const summary = useQuery({
    queryKey: extractionMemoryKeys.summary(workspaceId),
    queryFn: () => getMemorySummary(workspaceId),
    enabled,
  });
  const settings = useQuery({
    queryKey: extractionMemoryKeys.settings(workspaceId),
    queryFn: () => getMemorySettings(workspaceId),
    enabled,
  });
  const templates = useQuery({
    queryKey: extractionMemoryKeys.templates(workspaceId),
    queryFn: () => listMemoryTemplates(workspaceId),
    enabled,
  });
  const trials = useQuery({
    queryKey: extractionMemoryKeys.trials(workspaceId),
    queryFn: () => listMemoryTrials(workspaceId),
    enabled,
  });
  const rules = useQuery({
    queryKey: extractionMemoryKeys.rules(workspaceId),
    queryFn: () => listMemoryRules(workspaceId),
    enabled,
  });

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: extractionMemoryKeys.all(workspaceId) });
  };
  const setMode = useMutation({ mutationFn: (mode: MemoryMode) => putMemorySettings(workspaceId, mode), onSuccess: refresh });
  const ruleAction = useMutation({
    mutationFn: (input: { readonly id: string; readonly action: "retire" | "shadow" }) =>
      changeMemoryRule(workspaceId, input.id, input.action),
    onSuccess: refresh,
  });
  const resetTemplate = useMutation({
    mutationFn: (templateId: string) => resetMemoryTemplate(workspaceId, templateId),
    onSuccess: refresh,
  });
  const mutationError = setMode.error ?? ruleAction.error ?? resetTemplate.error;

  if (capability.isLoading) {
    return (
      <div className="flex h-64 items-center justify-center">
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden />
      </div>
    );
  }
  if (!capability.granted) {
    return (
      <div className="p-6">
        <LockedView workspaceId={workspaceId} canChangePlan={isAdmin} />
      </div>
    );
  }
  if (summary.isError) {
    return (
      <ErrorState
        title="Extraction memory could not be loaded"
        description={errorMessage(summary.error, "The server did not return the memory summary.")}
        onRetry={() => void summary.refetch()}
      />
    );
  }

  const mode = settings.data?.mode ?? summary.data?.mode ?? "SHADOW";
  const s = summary.data;

  return (
    <div className="space-y-5 p-4">
      <header className="space-y-1">
        <h1 className={`${PAGE_TITLE} flex items-center gap-2`}>
          <Brain className="h-5 w-5 text-primary" aria-hidden /> Extraction memory
        </h1>
        <p className={HINT}>
          Learned from this workspace&apos;s reviewed corrections, one document layout at a time.
        </p>
      </header>

      {mutationError ? (
        <p role="alert" className="rounded-md bg-destructive/10 p-3 text-sm text-destructive">
          {mutationError instanceof ApiError ? mutationError.message : "The change was not saved."}
        </p>
      ) : null}

      <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="memory-mode">
        <h2 id="memory-mode" className={SECTION_TITLE}>
          Mode
        </h2>
        <div role="radiogroup" aria-label="Extraction memory mode" className="grid gap-2 sm:grid-cols-3">
          {MODES.map((option) => (
            <button
              key={option.mode}
              type="button"
              role="radio"
              aria-checked={mode === option.mode}
              disabled={!isAdmin || setMode.isPending}
              onClick={() => setMode.mutate(option.mode)}
              className={`rounded-lg border p-3 text-left text-sm transition ${
                mode === option.mode ? "border-primary bg-primary/5" : "border-border hover:border-primary/60"
              } disabled:cursor-not-allowed disabled:opacity-70`}
            >
              <span className="font-medium">{option.label}</span>
              <span className={`mt-1 block ${HINT}`}>{option.help}</span>
            </button>
          ))}
        </div>
        {!isAdmin ? <p className={HINT}>Only a workspace administrator can change the mode.</p> : null}
      </section>

      {s ? (
        <section className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6" aria-label="Summary">
          <Stat label="Layouts" value={`${s.templates} (${s.active_templates} active)`} />
          <Stat label="Reviewed documents learned from" value={s.exemplar_documents} />
          <Stat label="Corrections learned" value={s.corrected_exemplars} />
          <Stat label="Anchor rules" value={`${s.active_rules} active · ${s.shadow_rules} shadow`} />
          <Stat label="Trials running" value={s.running_trials} />
          <Stat
            label="Fields corrected per document"
            value={`${pct(s.baseline_correction_rate)} → ${pct(s.memory_correction_rate)}`}
          />
        </section>
      ) : null}

      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="memory-trials">
        <h2 id="memory-trials" className={`${SECTION_TITLE} flex items-center gap-2`}>
          <FlaskConical className="h-4 w-4" aria-hidden /> Trials
        </h2>
        {(trials.data ?? []).length === 0 ? (
          <p className={HINT}>
            No trials yet. In automatic mode a layout starts a trial once five of its documents have been reviewed.
          </p>
        ) : (
          <ul className="grid gap-2 md:grid-cols-2">
            {(trials.data ?? []).map((trial) => (
              <li key={trial.id} className={`${SURFACE_INSET} space-y-1 p-3`}>
                <p className="flex items-center justify-between gap-2 text-sm font-medium">
                  {trial.document_type} <Badge value={trial.state} />
                </p>
                <p className="text-sm">{trial.sentence}</p>
                {trial.decision_reason ? <p className={HINT}>{trial.decision_reason}</p> : null}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="memory-layouts">
        <h2 id="memory-layouts" className={SECTION_TITLE}>
          Layouts
        </h2>
        <div className={SCROLL_X}>
          <table className="w-full text-sm">
            <thead>
              <tr className={TABLE_HEAD}>
                <th className="p-2 text-left">Document type</th>
                <th className="p-2 text-left">State</th>
                <th className="p-2 text-right">Documents</th>
                <th className="p-2 text-right">Reviewed</th>
                <th className="p-2 text-right">Corrected / doc, without memory</th>
                <th className="p-2 text-right">With memory</th>
                <th className="p-2 text-left">Fields learned</th>
                {isAdmin ? <th className="p-2" /> : null}
              </tr>
            </thead>
            <tbody>
              {(templates.data ?? []).map((t) => (
                <tr key={t.id} className={TABLE_ROW}>
                  <td className="p-2" title={t.anchor_tokens.join(" · ")}>
                    {t.document_type}
                  </td>
                  <td className="p-2">
                    <Badge value={t.state} />
                  </td>
                  <td className="p-2 text-right">{t.member_count}</td>
                  <td className="p-2 text-right">{t.exemplar_documents}</td>
                  <td className="p-2 text-right">{pct(t.baseline_correction_rate)}</td>
                  <td className="p-2 text-right">{pct(t.memory_correction_rate)}</td>
                  <td className="p-2">
                    <ul className="flex flex-wrap gap-1">
                      {t.fields.map((f) => (
                        <li
                          key={f.field_path}
                          className="rounded bg-muted px-1.5 py-0.5 font-mono text-xs"
                          title={`${f.corrections} corrected, ${f.confirmations} confirmed · ${pct(f.baseline_rate)} → ${pct(f.memory_rate)}`}
                        >
                          {f.field_path}
                        </li>
                      ))}
                    </ul>
                  </td>
                  {isAdmin ? (
                    <td className="p-2 text-right">
                      {t.state !== "LEARNING" ? (
                        <button
                          type="button"
                          className={BUTTON_GHOST}
                          onClick={() => resetTemplate.mutate(t.id)}
                          disabled={resetTemplate.isPending}
                        >
                          Reset
                        </button>
                      ) : null}
                    </td>
                  ) : null}
                </tr>
              ))}
            </tbody>
          </table>
          {(templates.data ?? []).length === 0 ? (
            <p className={`${HINT} p-2`}>
              No layouts yet. They appear as documents are extracted and reviewed with memory on.
            </p>
          ) : null}
        </div>
      </section>

      <section className={`${SURFACE} space-y-2 p-4`} aria-labelledby="memory-rules">
        <h2 id="memory-rules" className={SECTION_TITLE}>
          Anchor rules
        </h2>
        <p className={HINT}>
          A rule becomes active only when its replay record proves it right at least 95% of the time (one-sided Wilson
          lower bound) — about 52 correct replays in a row.
        </p>
        <ul className="space-y-1">
          {(rules.data ?? []).slice(0, 100).map((rule) => (
            <li key={rule.id} className={`${SURFACE_INSET} flex flex-wrap items-center justify-between gap-2 p-2 text-sm`}>
              <span>
                <Badge value={rule.state} /> {rule.sentence}
              </span>
              {isAdmin && rule.state !== "SHADOW" ? (
                <button
                  type="button"
                  className={BUTTON_GHOST}
                  onClick={() => ruleAction.mutate({ id: rule.id, action: "shadow" })}
                >
                  Return to shadow
                </button>
              ) : null}
              {isAdmin && rule.state !== "RETIRED" ? (
                <button
                  type="button"
                  className={BUTTON_GHOST}
                  onClick={() => ruleAction.mutate({ id: rule.id, action: "retire" })}
                >
                  Retire
                </button>
              ) : null}
            </li>
          ))}
        </ul>
        {(rules.data ?? []).length === 0 ? <p className={HINT}>No rules learned yet.</p> : null}
      </section>
    </div>
  );
};

export default ExtractionMemory;
