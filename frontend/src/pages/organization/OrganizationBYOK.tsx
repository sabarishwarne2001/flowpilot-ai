import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  KeySquare,
  Loader2,
  Pencil,
  Trash2,
  Zap,
} from "lucide-react";

import {
  deleteCredential,
  deleteModelRoute,
  getBYOKOverview,
  updateFallbackPolicy,
  upsertCredential,
  upsertModelRoute,
  validateCredential,
} from "@/services/api/byok";
import { byokKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  credentialFor,
  explainDowngrade,
  formatLatency,
  formatMicros,
  routeFor,
  STATUS_CLASSES,
  STATUS_LABELS,
  TASK_ORDER,
  type BYOKOverviewResponse,
  type BYOKProvider,
  type BYOKTaskType,
  type ModelRouteResponse,
  type ProviderCatalogEntry,
  type ProviderCredentialResponse,
  type ProviderCredentialUpsert,
  type TaskCatalogEntry,
} from "@/types/byok";

const CARD =
  "rounded-lg border border-border bg-card p-5 text-card-foreground shadow-sm";
const LABEL = "text-sm font-medium text-foreground";
const HINT = "text-xs text-muted-foreground";
const INPUT =
  "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";
const BUTTON =
  "inline-flex items-center gap-2 rounded-md px-3 py-2 text-sm font-medium transition disabled:opacity-50 disabled:cursor-not-allowed";
const PRIMARY = `${BUTTON} bg-primary text-primary-foreground hover:opacity-90`;
const SECONDARY = `${BUTTON} border border-border bg-background hover:bg-muted`;
const DANGER = `${BUTTON} border border-red-200 text-red-700 hover:bg-red-50`;

const WINDOW_DAYS = 30;

const formatDate = (value: string | null): string =>
  value ? new Date(value).toLocaleString() : "—";

const errorMessage = (error: unknown): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail.length > 0) {
    const first = detail[0] as { msg?: string };
    return first?.msg ?? "The request was rejected.";
  }
  return "Something went wrong. Please try again.";
};

// ---------------------------------------------------------------------------
// Savings Card
// ---------------------------------------------------------------------------

const SavingsCard: React.FC<{ overview: BYOKOverviewResponse }> = ({ overview }) => {
  const { savings } = overview;
  return (
    <section className={CARD}>
      <div className="flex items-center gap-2">
        <Zap className="h-4 w-4 text-muted-foreground" aria-hidden />
        <h2 className="text-base font-semibold">
          Where your AI spend is billed
        </h2>
      </div>
      <p className={`${HINT} mt-1`}>
        Last {savings.window_days} days, counted from metered usage events.
      </p>

      <dl className="mt-4 grid grid-cols-2 gap-4 sm:grid-cols-4">
        <div>
          <dt className={HINT}>On your own keys</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {savings.byok_share_percent}%
          </dd>
        </div>
        <div>
          <dt className={HINT}>Your-key events</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {savings.byok_events.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className={HINT}>FlowPilot-billed events</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {savings.platform_events.toLocaleString()}
          </dd>
        </div>
        <div>
          <dt className={HINT}>FlowPilot supplier cost</dt>
          <dd className="text-2xl font-semibold tabular-nums">
            {formatMicros(savings.platform_cost_micros)}
          </dd>
        </div>
      </dl>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Key Editor
// ---------------------------------------------------------------------------

const KeyEditor: React.FC<{
  entry: ProviderCatalogEntry;
  existing: ProviderCredentialResponse | undefined;
  busy: boolean;
  onSave: (payload: ProviderCredentialUpsert) => void;
  onCancel: () => void;
}> = ({ entry, existing, busy, onSave, onCancel }) => {
  const [apiKey, setApiKey] = useState("");
  const [endpoint, setEndpoint] = useState(existing?.resource_endpoint ?? "");
  const [deployment, setDeployment] = useState(existing?.deployment_name ?? "");

  const trimmedKey = apiKey.trim();
  const valid =
    trimmedKey.length > 0 &&
    (!entry.requires_endpoint || (endpoint.trim().length > 0 && deployment.trim().length > 0));

  const handleSave = () => {
    const payload: ProviderCredentialUpsert = {
      provider: entry.provider,
      api_key: trimmedKey,
      ...(entry.requires_endpoint && endpoint.trim() ? { resource_endpoint: endpoint.trim() } : {}),
      ...(entry.requires_endpoint && deployment.trim() ? { deployment_name: deployment.trim() } : {}),
    };
    onSave(payload);
  };

  return (
    <div className="mt-4 space-y-3 rounded-md border border-border bg-muted/40 p-3">
      <div>
        <label className={LABEL} htmlFor={`key-${entry.provider}`}>
          {existing ? `Replace ${entry.label} key` : `${entry.label} API key`}
        </label>
        <input
          id={`key-${entry.provider}`}
          type="password"
          autoComplete="off"
          spellCheck={false}
          className={INPUT}
          placeholder={entry.key_prefix ? `${entry.key_prefix}…` : "API key"}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
      </div>

      {entry.requires_endpoint && (
        <>
          <div>
            <label className={LABEL} htmlFor={`endpoint-${entry.provider}`}>
              Resource Endpoint Host
            </label>
            <input
              id={`endpoint-${entry.provider}`}
              type="text"
              autoComplete="off"
              spellCheck={false}
              className={INPUT}
              placeholder={`my-resource${entry.endpoint_suffix ?? ".openai.azure.com"}`}
              value={endpoint}
              onChange={(e) => setEndpoint(e.target.value)}
            />
          </div>

          <div>
            <label className={LABEL} htmlFor={`deployment-${entry.provider}`}>
              Deployment Name
            </label>
            <input
              id={`deployment-${entry.provider}`}
              type="text"
              autoComplete="off"
              spellCheck={false}
              className={INPUT}
              placeholder="e.g. gpt-4o"
              value={deployment}
              onChange={(e) => setDeployment(e.target.value)}
            />
          </div>
        </>
      )}

      <div className="mt-3 flex gap-2">
        <button
          type="button"
          className={PRIMARY}
          disabled={busy || !valid}
          onClick={handleSave}
        >
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : null}
          {existing ? "Rotate key" : "Save key"}
        </button>
        <button type="button" className={SECONDARY} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Provider Card
// ---------------------------------------------------------------------------

const ProviderCard: React.FC<{
  entry: ProviderCatalogEntry;
  credential: ProviderCredentialResponse | undefined;
  canWrite: boolean;
  organizationId: string;
}> = ({ entry, credential, canWrite, organizationId }) => {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: byokKeys.all(organizationId),
    });
  };

  const save = useMutation({
    mutationFn: (payload: ProviderCredentialUpsert) =>
      upsertCredential(organizationId, payload),
    onSuccess: () => {
      setEditing(false);
      setFailure(null);
      setNotice("Key stored. Run a test to confirm it works.");
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const test = useMutation({
    mutationFn: () => validateCredential(organizationId, entry.provider),
    onSuccess: (result) => {
      setFailure(null);
      setNotice(
        result.ok
          ? `Connected in ${result.latency_ms} ms.`
          : `The provider rejected this key: ${result.error ?? "unknown error"}`,
      );
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const remove = useMutation({
    mutationFn: () => deleteCredential(organizationId, entry.provider),
    onSuccess: () => {
      setNotice("Key retired.");
      setFailure(null);
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const fallback = useMutation({
    mutationFn: (allow: boolean) =>
      updateFallbackPolicy(organizationId, entry.provider, {
        allow_platform_fallback: allow,
      }),
    onSuccess: () => {
      setFailure(null);
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const status = credential?.status ?? "UNCONFIGURED";
  const busy = save.isPending || test.isPending || remove.isPending || fallback.isPending;

  return (
    <article className={CARD}>
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold">{entry.label}</h3>
          {credential ? (
            <p className={`${HINT} mt-0.5`}>
              ••••{credential.key_last_four} · fingerprint <code>{credential.key_fingerprint}</code>
            </p>
          ) : (
            <p className={`${HINT} mt-0.5`}>No key stored.</p>
          )}
        </div>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-xs font-medium ${STATUS_CLASSES[status]}`}
        >
          {STATUS_LABELS[status]}
        </span>
      </header>

      {credential?.resource_endpoint && (
        <div className="mt-2 text-xs text-muted-foreground">
          <p><strong>Host:</strong> {credential.resource_endpoint}</p>
          <p><strong>Deployment:</strong> {credential.deployment_name}</p>
        </div>
      )}

      {credential && (
        <dl className="mt-3 space-y-1 text-xs text-muted-foreground">
          <div className="flex justify-between gap-4">
            <dt>Last tested</dt>
            <dd>{formatDate(credential.last_validated_at)}</dd>
          </div>
          <div className="flex justify-between gap-4">
            <dt>Latency</dt>
            <dd>{formatLatency(credential.last_validation_latency_ms)}</dd>
          </div>
        </dl>
      )}

      {credential?.validation_error && (
        <p className="mt-3 rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-800">
          {credential.validation_error}
        </p>
      )}

      {credential && (
        <div className="mt-4 rounded-md border border-border p-3">
          <label className="flex items-start gap-2">
            <input
              type="checkbox"
              className="mt-0.5"
              disabled={!canWrite || busy || !credential.fallback_is_possible}
              checked={credential.allow_platform_fallback && credential.fallback_is_possible}
              onChange={(e) => fallback.mutate(e.target.checked)}
            />
            <span>
              <span className={LABEL}>Allow fallback to FlowPilot account</span>
              <span className={`${HINT} mt-1 block`}>
                {credential.fallback_is_possible
                  ? "If your key fails or is rate-limited, execute request on FlowPilot's provider account."
                  : "FlowPilot holds no platform key for this provider, so fallback is unavailable."}
              </span>
            </span>
          </label>
        </div>
      )}

      {notice && (
        <p className="mt-3 flex items-start gap-2 text-xs text-emerald-800">
          <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {notice}
        </p>
      )}
      {failure && (
        <p className="mt-3 flex items-start gap-2 text-xs text-red-700">
          <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {failure}
        </p>
      )}

      {editing ? (
        <KeyEditor
          entry={entry}
          existing={credential}
          busy={save.isPending}
          onSave={(payload) => save.mutate(payload)}
          onCancel={() => setEditing(false)}
        />
      ) : (
        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            className={SECONDARY}
            disabled={!canWrite || busy}
            onClick={() => {
              setNotice(null);
              setFailure(null);
              setEditing(true);
            }}
          >
            <Pencil className="h-3.5 w-3.5" aria-hidden />
            {credential ? "Rotate" : "Add key"}
          </button>
          {credential && (
            <>
              <button
                type="button"
                className={SECONDARY}
                disabled={!canWrite || busy}
                onClick={() => {
                  setNotice(null);
                  setFailure(null);
                  test.mutate();
                }}
              >
                {test.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
                Test connection
              </button>
              <button
                type="button"
                className={DANGER}
                disabled={!canWrite || busy}
                onClick={() => remove.mutate()}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                Retire
              </button>
            </>
          )}
        </div>
      )}
    </article>
  );
};

// ---------------------------------------------------------------------------
// Routing Row
// ---------------------------------------------------------------------------

const RouteRow: React.FC<{
  taskType: BYOKTaskType;
  taskLabel: string;
  route: ModelRouteResponse | undefined;
  providers: readonly ProviderCatalogEntry[];
  canWrite: boolean;
  organizationId: string;
}> = ({ taskType, taskLabel, route, providers, canWrite, organizationId }) => {
  const queryClient = useQueryClient();

  const eligibleProviders = useMemo(
    () => providers.filter((p: ProviderCatalogEntry) => p.supported_tasks.includes(taskType)),
    [providers, taskType],
  );

  const [provider, setProvider] = useState<BYOKProvider>(
    route?.provider ?? eligibleProviders[0]?.provider ?? "GROQ",
  );
  const [model, setModel] = useState(route?.model_name ?? "");
  const [useTenantKey, setUseTenantKey] = useState(route?.use_tenant_key ?? true);
  const [failure, setFailure] = useState<string | null>(null);

  const selected = providers.find((entry: ProviderCatalogEntry) => entry.provider === provider);

  const invalidate = () => {
    void queryClient.invalidateQueries({
      queryKey: byokKeys.all(organizationId),
    });
  };

  const save = useMutation({
    mutationFn: () =>
      upsertModelRoute(organizationId, {
        task_type: taskType,
        provider,
        model_name: model.trim(),
        use_tenant_key: useTenantKey,
        is_enabled: true,
      }),
    onSuccess: () => {
      setFailure(null);
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const remove = useMutation({
    mutationFn: () => deleteModelRoute(organizationId, taskType),
    onSuccess: () => {
      setFailure(null);
      setModel("");
      setUseTenantKey(true);
      invalidate();
    },
    onError: (error) => setFailure(errorMessage(error)),
  });

  const downgrade = explainDowngrade(route?.downgrade_reason ?? null);
  const claimsButDoesNot =
    route?.use_tenant_key === true && route?.effective_tenant_key === false;

  return (
    <tr className="border-t border-border align-top">
      <td className="py-3 pr-3">
        <span className={LABEL}>{taskLabel}</span>
        {route && (
          <span
            className={`mt-1 block text-xs ${
              route.effective_tenant_key ? "text-emerald-700" : "text-muted-foreground"
            }`}
          >
            {route.effective_tenant_key ? "Running on your key" : "Running on FlowPilot account"}
          </span>
        )}
      </td>
      <td className="py-3 pr-3">
        <select
          className={INPUT}
          disabled={!canWrite}
          value={provider}
          onChange={(e) => {
            const next = e.target.value as BYOKProvider;
            setProvider(next);
          }}
        >
          {eligibleProviders.map((entry: ProviderCatalogEntry) => (
            <option key={entry.provider} value={entry.provider}>
              {entry.label}
            </option>
          ))}
        </select>
      </td>
      <td className="py-3 pr-3">
        <input
          className={INPUT}
          disabled={!canWrite}
          list={`models-${taskType}`}
          placeholder="Model / deployment name"
          value={model}
          onChange={(e) => setModel(e.target.value)}
        />
        <datalist id={`models-${taskType}`}>
          {(selected?.suggested_models ?? []).map((name: string) => (
            <option key={name} value={name} />
          ))}
        </datalist>
      </td>
      <td className="py-3 pr-3">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            disabled={!canWrite}
            checked={useTenantKey}
            onChange={(e) => setUseTenantKey(e.target.checked)}
          />
          <span>My key</span>
        </label>
      </td>
      <td className="py-3">
        <div className="flex gap-2">
          <button
            type="button"
            className={SECONDARY}
            disabled={!canWrite || save.isPending || model.trim().length === 0}
            onClick={() => save.mutate()}
          >
            {save.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden /> : null}
            Save
          </button>
          {route && (
            <button
              type="button"
              className={DANGER}
              disabled={!canWrite || remove.isPending}
              onClick={() => remove.mutate()}
            >
              Clear
            </button>
          )}
        </div>
        {claimsButDoesNot && downgrade && (
          <p className="mt-2 text-xs text-amber-800">{downgrade}</p>
        )}
        {failure && <p className="mt-2 text-xs text-red-700">{failure}</p>}
      </td>
    </tr>
  );
};

// ---------------------------------------------------------------------------
// Routing Table
// ---------------------------------------------------------------------------

const RoutingTable: React.FC<{
  overview: BYOKOverviewResponse;
  canWrite: boolean;
  organizationId: string;
}> = ({ overview, canWrite, organizationId }) => {
  const labels = useMemo(() => {
    const map = new Map<BYOKTaskType, string>();
    overview.tasks.forEach((task: TaskCatalogEntry) => map.set(task.task_type, task.label));
    return map;
  }, [overview.tasks]);

  return (
    <section className={CARD}>
      <h2 className="text-base font-semibold">Model routing</h2>
      <p className={`${HINT} mt-1`}>
        Point each pipeline stage at a provider and model.
      </p>

      <div className="mt-4 overflow-x-auto">
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-xs uppercase tracking-wide text-muted-foreground">
              <th className="pb-2 pr-3 font-medium">Task</th>
              <th className="pb-2 pr-3 font-medium">Provider</th>
              <th className="pb-2 pr-3 font-medium">Model / Deployment</th>
              <th className="pb-2 pr-3 font-medium">Key</th>
              <th className="pb-2 font-medium">Actions</th>
            </tr>
          </thead>
          <tbody>
            {TASK_ORDER.map((taskType: BYOKTaskType) => (
              <RouteRow
                key={taskType}
                taskType={taskType}
                taskLabel={labels.get(taskType) ?? taskType}
                route={routeFor(overview.routes, taskType)}
                providers={overview.providers}
                canWrite={canWrite}
                organizationId={organizationId}
              />
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

// ---------------------------------------------------------------------------
// Main OrganizationBYOK Page
// ---------------------------------------------------------------------------

const OrganizationBYOK: React.FC = () => {
  const { organizationId, organizationRole } = useResolvedOrganization();
  const canWrite = String(organizationRole).toUpperCase() === "OWNER";

  const overview = useQuery({
    queryKey: byokKeys.overview(organizationId, WINDOW_DAYS),
    queryFn: () => getBYOKOverview(organizationId, WINDOW_DAYS),
  });

  if (overview.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading BYOK configuration…
      </div>
    );
  }

  if (overview.isError || !overview.data) {
    return (
      <div className="p-6">
        <p className="text-sm text-red-700">{errorMessage(overview.error)}</p>
      </div>
    );
  }

  const data = overview.data;

  return (
    <div className="space-y-6 p-6">
      <header>
        <div className="flex items-center gap-2">
          <KeySquare className="h-5 w-5 text-muted-foreground" aria-hidden />
          <h1 className="text-xl font-semibold">Enterprise BYOK &amp; models</h1>
        </div>
        <p className={`${HINT} mt-1 max-w-3xl`}>
          Bring your own provider API keys across Groq, Gemini, OpenAI, Anthropic, Azure OpenAI, and Mistral.
        </p>
      </header>

      <SavingsCard overview={data} />

      <section>
        <h2 className="text-base font-semibold">Provider keys</h2>
        <div className="mt-3 grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {data.providers.map((entry: ProviderCatalogEntry) => (
            <ProviderCard
              key={entry.provider}
              entry={entry}
              credential={credentialFor(data.credentials, entry.provider)}
              canWrite={canWrite}
              organizationId={organizationId}
            />
          ))}
        </div>
      </section>

      <RoutingTable
        overview={data}
        canWrite={canWrite}
        organizationId={organizationId}
      />
    </div>
  );
};

export default OrganizationBYOK;
