/**
 * ARCH40-S2:ai-settings-page. What serves this workspace, and its defaults.
 *
 * Two halves, deliberately separate:
 *
 *   What runs (read-only)   the provider and model that actually serve
 *                           extraction, where that decision came from, the
 *                           price-book rate in force, BYOK and spend-limit
 *                           state. From GET .../ai-settings/resolved.
 *   Workspace defaults      the values this workspace falls back to when no
 *                           organization routing rule applies.
 *
 * Before ARCH-40 the page showed only the second half and implied it was in
 * force, so an administrator could set a model an organization routing rule
 * silently overrode. It also offered five inputs nothing read: two cost
 * fields (platform-owned since ARCH-14) and three version/tracking fields
 * (dropped by arch40_step3). All five are gone.
 */

import React, { useEffect, useMemo } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useForm, useWatch } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { toast } from "sonner";
import {
  AlertTriangle,
  CheckCircle2,
  Cpu,
  KeyRound,
  Loader2,
  PlugZap,
  Route,
  Wallet,
} from "lucide-react";

import {
  getAISettings,
  getAvailableProviders,
  getResolvedAISettings,
  getSupportedModels,
  testAIConnection,
  updateAISettings,
} from "@/services/api/aiSettings";
import { ApiError } from "@/services/api/client";
import { settingsKeys } from "@/services/api/queryKeys";
import { AI_FIELD_HELP } from "@/constants/aiFieldHelp";
import { aiSettingsSchema, type AISettingsFormData } from "@/schemas/aiSettings";
import type { AIConnectionTestResponse } from "@/types/aiConnectionTest";
import type { ResolvedAISettings } from "@/types/aiSettings";
import { canManageWorkspaceSettings } from "@/permissions/workspacePermissions";
import { ADMINISTRATIVE_ROLES, canViewBilling } from "@/permissions/organizationPermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { organizationBYOKPath, organizationBillingPath } from "@/routes/tenantPaths";

const DEFAULTS: AISettingsFormData = {
  provider: "GROQ",
  model: "openai/gpt-oss-20b",
  temperature: 0.7,
  max_output_tokens: 4096,
  top_p: 0.9,
  frequency_penalty: 0,
  presence_penalty: 0,
  enable_streaming: true,
};

/** Micros to a display amount. Null stays "not priced", never "0". */
const formatRate = (micros: number | null, currency: string | null): string => {
  if (micros === null) {
    return "Not priced";
  }
  const amount = micros / 1_000_000;
  return `${currency ?? ""} ${amount.toLocaleString(undefined, { maximumFractionDigits: 4 })}`.trim();
};

const FieldLabel: React.FC<{ readonly htmlFor: string; readonly field: keyof typeof AI_FIELD_HELP }> = ({
  htmlFor,
  field,
}) => {
  const help = AI_FIELD_HELP[field];
  return (
    <label htmlFor={htmlFor} className="block">
      <span className="text-xs font-bold uppercase tracking-wide text-muted-foreground">{help.title}</span>
      <span className="mt-0.5 block text-[11px] leading-snug text-muted-foreground/80">{help.description}</span>
    </label>
  );
};

const ResolvedPanel: React.FC<{
  readonly resolved: ResolvedAISettings | undefined;
  readonly loading: boolean;
  readonly orgSlug: string;
  readonly showOrgLinks: boolean;
  readonly showBilling: boolean;
}> = ({ resolved, loading, orgSlug, showOrgLinks, showBilling }) => {
  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-border bg-card p-5 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" /> Resolving what serves this workspace…
      </div>
    );
  }
  if (!resolved) {
    return null;
  }
  const fromRule = resolved.resolution_origin === "route_rule";
  return (
    <section aria-labelledby="ai-resolved-title" className="rounded-xl border border-border bg-card">
      <header className="border-b border-border/60 px-5 py-4">
        <h2 id="ai-resolved-title" className="text-sm font-extrabold uppercase tracking-wider">What runs</h2>
        <p className="mt-1 text-xs text-muted-foreground">
          The model that actually serves this workspace&apos;s extraction. Read-only: it reflects organization routing, keys and limits.
        </p>
      </header>
      <dl className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2">
        <div className="rounded-lg border border-border/60 p-3">
          <dt className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <Cpu className="h-3.5 w-3.5" aria-hidden="true" /> Model
          </dt>
          <dd className="mt-1 font-mono text-sm">{resolved.resolved_provider} / {resolved.resolved_model}</dd>
          <dd className="mt-1 inline-flex items-center gap-1 text-[11px] text-muted-foreground">
            <Route className="h-3 w-3" aria-hidden="true" />
            {fromRule ? "Chosen by an organization routing rule" : "This workspace's default"}
          </dd>
        </div>
        <div className="rounded-lg border border-border/60 p-3">
          <dt className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <Wallet className="h-3.5 w-3.5" aria-hidden="true" /> Price per 1M input tokens
          </dt>
          <dd className="mt-1 font-mono text-sm">
            {formatRate(resolved.price_per_1m_input_micros, resolved.currency)}
          </dd>
          <dd className="mt-1 text-[11px] text-muted-foreground">
            {resolved.price_book_version !== null ? `Price book v${resolved.price_book_version}` : "No price book in force"}
            {resolved.price_is_fallback ? " · provider fallback rate" : ""}
          </dd>
        </div>
        <div className="rounded-lg border border-border/60 p-3">
          <dt className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <KeyRound className="h-3.5 w-3.5" aria-hidden="true" /> Provider key
          </dt>
          <dd className="mt-1 text-sm">
            {resolved.uses_tenant_key
              ? "Your organization's own key (BYOK)"
              : resolved.byok_configured
                ? "Platform key — a BYOK key exists but does not serve this task"
                : "Platform key"}
          </dd>
          {showOrgLinks && (
            <dd className="mt-1">
              <Link to={organizationBYOKPath(orgSlug)} className="text-[11px] font-semibold text-primary hover:underline">
                Manage keys and routing
              </Link>
            </dd>
          )}
        </div>
        <div className="rounded-lg border border-border/60 p-3">
          <dt className="flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
            <Wallet className="h-3.5 w-3.5" aria-hidden="true" /> Spend limit
          </dt>
          <dd className="mt-1 text-sm">
            {resolved.spend_limit_configured
              ? `${formatRate(resolved.spend_limit_max_cost_micros, resolved.currency)} per ${(resolved.spend_limit_period ?? "period").toLowerCase()}${resolved.spend_limit_hard_stop ? " · hard stop" : " · alert only"}`
              : "No limit configured"}
          </dd>
          {showBilling && (
            <dd className="mt-1">
              <Link to={organizationBillingPath(orgSlug)} className="text-[11px] font-semibold text-primary hover:underline">
                Billing and limits
              </Link>
            </dd>
          )}
        </div>
      </dl>
      {resolved.warnings.length > 0 && (
        <ul className="mx-5 mb-5 space-y-1.5 rounded-lg border border-amber-500/40 bg-amber-500/5 px-3 py-2" role="status">
          {resolved.warnings.map((warning) => (
            <li key={warning} className="flex items-start gap-2 text-xs text-amber-800 dark:text-amber-300">
              <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" /> {warning}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};

export const AISettings: React.FC = () => {
  const queryClient = useQueryClient();
  const { workspace, workspaceRole, organization, organizationRole } = useResolvedTenant();
  const workspaceId = workspace.id;
  const canManage = canManageWorkspaceSettings(workspaceRole);

  const {
    register,
    handleSubmit,
    reset,
    control,
    setValue,
    getValues,
    formState: { errors, isDirty },
  } = useForm<AISettingsFormData>({
    resolver: zodResolver(aiSettingsSchema),
    defaultValues: DEFAULTS,
  });

  const settingsQuery = useQuery({
    queryKey: settingsKeys.ai(workspaceId),
    queryFn: () => getAISettings(workspaceId),
  });
  const resolvedQuery = useQuery({
    queryKey: settingsKeys.aiResolved(workspaceId),
    queryFn: () => getResolvedAISettings(workspaceId),
    staleTime: 30_000,
  });
  const modelsQuery = useQuery({
    queryKey: settingsKeys.aiModels(workspaceId),
    queryFn: () => getSupportedModels(workspaceId),
    staleTime: 300_000,
  });
  const providersQuery = useQuery({
    queryKey: settingsKeys.aiProviders(workspaceId),
    queryFn: () => getAvailableProviders(workspaceId),
    staleTime: 300_000,
  });

  useEffect(() => {
    const data = settingsQuery.data;
    if (!data) {
      return;
    }
    reset({
      provider: data.provider,
      model: data.model,
      temperature: data.temperature,
      max_output_tokens: data.max_output_tokens,
      top_p: data.top_p,
      frequency_penalty: data.frequency_penalty,
      presence_penalty: data.presence_penalty,
      enable_streaming: data.enable_streaming,
    });
  }, [settingsQuery.data, reset]);

  const provider = useWatch({ control, name: "provider" });
  const models = useMemo<readonly string[]>(
    () => modelsQuery.data?.[provider] ?? [],
    [modelsQuery.data, provider],
  );
  const providers = providersQuery.data?.providers ?? ["GROQ", "GEMINI"];

  useEffect(() => {
    const current = getValues("model");
    const first = models[0];
    if (models.length > 0 && first !== undefined && !models.includes(current)) {
      setValue("model", first, { shouldDirty: true });
    }
  }, [models, getValues, setValue]);

  const save = useMutation({
    mutationFn: (values: AISettingsFormData) => updateAISettings(workspaceId, values),
    onSuccess: async (saved) => {
      toast.success("Workspace AI defaults saved.");
      reset({
        provider: saved.provider,
        model: saved.model,
        temperature: saved.temperature,
        max_output_tokens: saved.max_output_tokens,
        top_p: saved.top_p,
        frequency_penalty: saved.frequency_penalty,
        presence_penalty: saved.presence_penalty,
        enable_streaming: saved.enable_streaming,
      });
      await queryClient.invalidateQueries({ queryKey: settingsKeys.all(workspaceId) });
    },
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : "The AI defaults could not be saved.");
    },
  });

  const test = useMutation<AIConnectionTestResponse, unknown, AISettingsFormData>({
    mutationFn: (values) => testAIConnection(workspaceId, values),
    onError: (error: unknown) => {
      toast.error(error instanceof ApiError ? error.message : "The connection test failed.");
    },
  });

  const orgSlug = organization.organization_slug;
  const isOrgAdmin = ADMINISTRATIVE_ROLES.has(organizationRole);
  const disabled = !canManage || save.isPending;

  const numberField = (
    name: "temperature" | "top_p" | "max_output_tokens" | "frequency_penalty" | "presence_penalty",
    step: string,
  ): React.ReactNode => (
    <div className="space-y-1.5">
      <FieldLabel htmlFor={`ai-${name}`} field={name} />
      <input
        id={`ai-${name}`}
        type="number"
        step={step}
        disabled={disabled}
        {...register(name, { valueAsNumber: true })}
        className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20 disabled:opacity-60"
        aria-invalid={Boolean(errors[name])}
      />
      {errors[name] && <p className="text-xs text-destructive">{errors[name]?.message}</p>}
    </div>
  );

  if (settingsQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground" role="status">
        <Loader2 className="h-4 w-4 animate-spin" /> Loading AI settings…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <ResolvedPanel
        resolved={resolvedQuery.data}
        loading={resolvedQuery.isLoading}
        orgSlug={orgSlug}
        showOrgLinks={isOrgAdmin}
        showBilling={canViewBilling(organizationRole)}
      />

      <form
        onSubmit={handleSubmit((values) => save.mutate(values))}
        className="rounded-xl border border-border bg-card"
        aria-labelledby="ai-defaults-title"
      >
        <header className="border-b border-border/60 px-5 py-4">
          <h2 id="ai-defaults-title" className="text-sm font-extrabold uppercase tracking-wider">Workspace defaults</h2>
          <p className="mt-1 text-xs text-muted-foreground">
            Used when no organization routing rule applies.
            {!canManage && " You can view these; a workspace admin can change them."}
          </p>
        </header>

        <div className="grid grid-cols-1 gap-5 p-5 md:grid-cols-2">
          <div className="space-y-1.5">
            <FieldLabel htmlFor="ai-provider" field="provider" />
            <select
              id="ai-provider"
              disabled={disabled}
              {...register("provider")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 text-sm disabled:opacity-60"
            >
              {providers.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
          </div>
          <div className="space-y-1.5">
            <FieldLabel htmlFor="ai-model" field="model" />
            <select
              id="ai-model"
              disabled={disabled || models.length === 0}
              {...register("model")}
              className="w-full rounded-lg border border-border bg-background px-3 py-2 font-mono text-sm disabled:opacity-60"
            >
              {models.length === 0 && <option value={getValues("model")}>{getValues("model")}</option>}
              {models.map((value) => (
                <option key={value} value={value}>{value}</option>
              ))}
            </select>
            {errors.model && <p className="text-xs text-destructive">{errors.model.message}</p>}
          </div>
          {numberField("temperature", "0.05")}
          {numberField("top_p", "0.05")}
          {numberField("max_output_tokens", "1")}
          {numberField("frequency_penalty", "0.1")}
          {numberField("presence_penalty", "0.1")}
          <div className="flex items-start gap-3 rounded-lg border border-border/60 p-3">
            <input
              id="ai-enable_streaming"
              type="checkbox"
              disabled={disabled}
              {...register("enable_streaming")}
              className="mt-1 h-4 w-4 accent-primary"
            />
            <FieldLabel htmlFor="ai-enable_streaming" field="enable_streaming" />
          </div>
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-3 border-t border-border/60 px-5 py-4">
          <div className="min-h-[1.25rem] text-xs" aria-live="polite">
            {test.isPending && (
              <span className="inline-flex items-center gap-1.5 text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Testing…
              </span>
            )}
            {test.data && (
              <span
                className={`inline-flex max-w-xl items-start gap-1.5 ${
                  test.data.success ? "text-emerald-600 dark:text-emerald-400" : "text-destructive"
                }`}
              >
                {test.data.success ? (
                  <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                ) : (
                  <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                )}
                <span>
                  <span className="font-semibold">
                    {test.data.success ? "Connected" : "Failed"} · {test.data.provider}/{test.data.model}
                    {test.data.latency_ms !== null ? ` · ${Math.round(test.data.latency_ms)} ms` : ""}
                    {test.data.resolution_origin === "route_rule" ? " · via routing rule" : ""}
                    {test.data.credential_source === "TENANT" ? " · your key" : ""}
                  </span>
                  <span className="block font-normal text-muted-foreground">{test.data.message}</span>
                </span>
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={!canManage || test.isPending}
              onClick={handleSubmit((values) => test.mutate(values))}
              className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-2 text-sm font-semibold hover:bg-muted disabled:opacity-50"
            >
              <PlugZap className="h-4 w-4" /> Test connection
            </button>
            <button
              type="submit"
              disabled={disabled || !isDirty}
              className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-4 py-2 text-sm font-bold text-primary-foreground hover:bg-primary/90 disabled:opacity-50"
            >
              {save.isPending && <Loader2 className="h-4 w-4 animate-spin" />} Save defaults
            </button>
          </div>
        </footer>
      </form>
    </div>
  );
};

export default AISettings;
