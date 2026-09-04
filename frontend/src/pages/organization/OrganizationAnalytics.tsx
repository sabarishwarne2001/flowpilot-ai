import React, { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BarChart3,
  CheckCircle2,
  Database,
  Loader2,
  PlayCircle,
  Plug,
  Trash2,
  XCircle,
} from "lucide-react";

import {
  createDestination,
  createSchedule,
  deleteDestination,
  deleteSchedule,
  getConsumption,
  listDatasetDescriptors,
  listDestinations,
  listRuns,
  listSchedules,
  testDestination,
  triggerSync,
  updateSchedule,
} from "@/services/api/analytics";
import { analyticsKeys } from "@/services/api/queryKeys";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import {
  DATASET_HINTS,
  DATASET_LABELS,
  DESTINATION_KINDS,
  EXPORT_DATASETS,
  KIND_LABELS,
  MAX_DAY_OF_MONTH,
  MAX_LOOKBACK_DAYS,
  MIN_LOOKBACK_DAYS,
  SCHEDULE_CADENCES,
  STATUS_CLASSES,
  STATUS_LABELS,
  WEEKDAY_LABELS,
  describeCadence,
  describeProbe,
  formatBytes,
  formatCount,
  formatDateTime,
  formatMicros,
  type DestinationKind,
  type ExportDataset,
  type ExportSchedule,
  type ScheduleCadence,
  type WarehouseCredentialInput,
  type WarehouseDestination,
} from "@/types/analytics";

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

const TABS = [
  "destinations",
  "schedules",
  "runs",
  "consumption",
] as const;
type Tab = (typeof TABS)[number];

const TAB_LABELS: Record<Tab, string> = {
  destinations: "Warehouse destinations",
  schedules: "Sync schedules",
  runs: "Run history",
  consumption: "Usage analytics",
};

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
// Destination form
//
// One form per warehouse kind, switched on `kind`. The alternative — one form
// with every field and a note saying which ones apply — is how a tenant ends
// up pasting a BigQuery key into a Snowflake destination and finding out at
// the first scheduled run.
// ---------------------------------------------------------------------------

const DestinationForm: React.FC<{
  organizationId: string;
  onDone: () => void;
}> = ({ organizationId, onDone }) => {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<DestinationKind>("S3");
  const [label, setLabel] = useState("");
  const [fields, setFields] = useState<Record<string, string>>({});

  const set = (key: string) => (
    event: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => setFields((current) => ({ ...current, [key]: event.target.value }));

  const value = (key: string): string => fields[key] ?? "";

  const buildCredential = (): WarehouseCredentialInput => {
    switch (kind) {
      case "SNOWFLAKE":
        return {
          kind: "SNOWFLAKE",
          account: value("account"),
          user: value("user"),
          warehouse: value("warehouse"),
          database: value("database"),
          db_schema: value("db_schema") || "PUBLIC",
          stage_name: value("stage_name"),
          stage_bucket: value("stage_bucket"),
          stage_region: value("stage_region"),
          private_key: value("private_key"),
          stage_access_key_id: value("stage_access_key_id"),
          stage_secret_access_key: value("stage_secret_access_key"),
        };
      case "BIGQUERY":
        return {
          kind: "BIGQUERY",
          project_id: value("project_id"),
          dataset: value("dataset"),
          location: value("location") || "US",
          service_account_json: value("service_account_json"),
        };
      case "DATABRICKS":
        return {
          kind: "DATABRICKS",
          host: value("host"),
          warehouse_id: value("warehouse_id"),
          catalog: value("catalog") || "main",
          db_schema: value("db_schema") || "default",
          volume: value("volume"),
          access_token: value("access_token"),
        };
      default:
        return {
          kind: "S3",
          bucket: value("bucket"),
          region: value("region"),
          prefix: value("prefix") || "flowpilot/",
          access_key_id: value("access_key_id"),
          secret_access_key: value("secret_access_key"),
        };
    }
  };

  const save = useMutation({
    mutationFn: () =>
      createDestination(organizationId, { label, credential: buildCredential() }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: analyticsKeys.destinations(organizationId),
      });
      setLabel("");
      setFields({});
      onDone();
    },
  });

  const text = (key: string, title: string, hint?: string) => (
    <div key={key}>
      <label className={LABEL} htmlFor={`field-${key}`}>
        {title}
      </label>
      <input
        id={`field-${key}`}
        className={INPUT}
        value={value(key)}
        onChange={set(key)}
        autoComplete="off"
      />
      {hint ? <p className={HINT}>{hint}</p> : null}
    </div>
  );

  const secret = (key: string, title: string, hint?: string) => (
    <div key={key}>
      <label className={LABEL} htmlFor={`field-${key}`}>
        {title}
      </label>
      <input
        id={`field-${key}`}
        className={INPUT}
        type="password"
        value={value(key)}
        onChange={set(key)}
        autoComplete="new-password"
      />
      {hint ? <p className={HINT}>{hint}</p> : null}
    </div>
  );

  const area = (key: string, title: string, hint?: string) => (
    <div key={key}>
      <label className={LABEL} htmlFor={`field-${key}`}>
        {title}
      </label>
      <textarea
        id={`field-${key}`}
        className={`${INPUT} h-28 font-mono text-xs`}
        value={value(key)}
        onChange={set(key)}
      />
      {hint ? <p className={HINT}>{hint}</p> : null}
    </div>
  );

  return (
    <div className={CARD}>
      <h3 className="text-base font-semibold">Add a warehouse destination</h3>
      <p className={`${HINT} mt-1`}>
        Credentials are encrypted before they are stored and are never returned
        by this console. You will see a fingerprint instead.
      </p>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="destination-kind">
            Warehouse
          </label>
          <select
            id="destination-kind"
            className={INPUT}
            value={kind}
            onChange={(event) =>
              setKind(event.target.value as DestinationKind)
            }
          >
            {DESTINATION_KINDS.map((option) => (
              <option key={option} value={option}>
                {KIND_LABELS[option]}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={LABEL} htmlFor="destination-label">
            Label
          </label>
          <input
            id="destination-label"
            className={INPUT}
            value={label}
            onChange={(event) => setLabel(event.target.value)}
          />
          <p className={HINT}>Shown in the run history. Must be unique.</p>
        </div>
      </div>

      <div className="mt-4 grid gap-4 md:grid-cols-2">
        {kind === "S3" ? (
          <>
            {text("bucket", "Bucket")}
            {text("region", "Region", "e.g. eu-west-1")}
            {text("prefix", "Key prefix", "Defaults to flowpilot/")}
            {text("access_key_id", "Access key ID")}
            {secret("secret_access_key", "Secret access key")}
          </>
        ) : null}

        {kind === "BIGQUERY" ? (
          <>
            {text("project_id", "Project ID")}
            {text("dataset", "Dataset")}
            {text(
              "location",
              "Location",
              "Must match the dataset's own location, or the load is rejected after upload.",
            )}
            <div className="md:col-span-2">
              {area(
                "service_account_json",
                "Service account JSON",
                "Paste the whole key file. A user OAuth client will not work for unattended loads.",
              )}
            </div>
          </>
        ) : null}

        {kind === "DATABRICKS" ? (
          <>
            {text("host", "Workspace host", "Hostname only, no https://")}
            {text("warehouse_id", "SQL warehouse ID")}
            {text("catalog", "Catalog", "Defaults to main")}
            {text("db_schema", "Schema", "Defaults to default")}
            {text(
              "volume",
              "Unity Catalog volume",
              "Receives the Parquet before COPY INTO reads it.",
            )}
            {secret("access_token", "Personal access token")}
          </>
        ) : null}

        {kind === "SNOWFLAKE" ? (
          <>
            {text("account", "Account identifier", "e.g. xy12345.eu-west-1")}
            {text("user", "User")}
            {text("warehouse", "Warehouse")}
            {text("database", "Database")}
            {text("db_schema", "Schema", "Defaults to PUBLIC")}
            {text(
              "stage_name",
              "External stage",
              "A stage you have already created, pointing at the bucket below.",
            )}
            {text("stage_bucket", "Stage bucket")}
            {text("stage_region", "Stage region")}
            {text("stage_access_key_id", "Stage access key ID")}
            {secret("stage_secret_access_key", "Stage secret access key")}
            <div className="md:col-span-2">
              {area(
                "private_key",
                "Private key (PKCS#8 PEM)",
                "Snowflake's SQL API authenticates with a key pair; it has no password path. Paste the private half — the public half stays in Snowflake.",
              )}
            </div>
          </>
        ) : null}
      </div>

      {kind === "SNOWFLAKE" ? (
        <p className={`${HINT} mt-4 rounded-md bg-muted p-3`}>
          Snowflake needs two credentials because its SQL API cannot accept file
          bytes. We write Parquet into your stage bucket, then run COPY INTO
          from the stage.
        </p>
      ) : null}

      {save.isError ? (
        <p className="mt-4 text-sm text-red-700">{errorMessage(save.error)}</p>
      ) : null}

      <div className="mt-5 flex gap-2">
        <button
          type="button"
          className={PRIMARY}
          disabled={!label || save.isPending}
          onClick={() => save.mutate()}
        >
          {save.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Plug className="h-4 w-4" />
          )}
          Add destination
        </button>
        <button type="button" className={SECONDARY} onClick={onDone}>
          Cancel
        </button>
      </div>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Destination row
// ---------------------------------------------------------------------------

const DestinationRow: React.FC<{
  organizationId: string;
  destination: WarehouseDestination;
}> = ({ organizationId, destination }) => {
  const queryClient = useQueryClient();

  const probe = useMutation({
    mutationFn: () => testDestination(organizationId, destination.id),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: analyticsKeys.destinations(organizationId),
      }),
  });

  const remove = useMutation({
    mutationFn: () => deleteDestination(organizationId, destination.id),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: analyticsKeys.destinations(organizationId),
      }),
  });

  // null, true and false are three states, not two. A destination nobody has
  // probed must not show the same icon as one that answered.
  const probeIcon =
    destination.last_test_ok === null ? (
      <AlertTriangle className="h-4 w-4 text-muted-foreground" />
    ) : destination.last_test_ok ? (
      <CheckCircle2 className="h-4 w-4 text-emerald-600" />
    ) : (
      <XCircle className="h-4 w-4 text-red-600" />
    );

  return (
    <div className={`${CARD} flex flex-col gap-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <Database className="h-4 w-4 text-muted-foreground" />
            <span className="text-base font-semibold">{destination.label}</span>
            <span className="rounded border border-border px-2 py-0.5 text-xs">
              {KIND_LABELS[destination.kind]}
            </span>
            {destination.status === "DISABLED" ? (
              <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
                Disabled
              </span>
            ) : null}
          </div>
          <p className={`${HINT} mt-1 flex items-center gap-2`}>
            {probeIcon}
            {describeProbe(destination)}
          </p>
          <p className={`${HINT} mt-1 font-mono`}>
            credential {destination.credential_fingerprint}
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            className={SECONDARY}
            disabled={probe.isPending}
            onClick={() => probe.mutate()}
          >
            {probe.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plug className="h-4 w-4" />
            )}
            Test connection
          </button>
          <button
            type="button"
            className={DANGER}
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            <Trash2 className="h-4 w-4" />
            Remove
          </button>
        </div>
      </div>

      {probe.data && !probe.data.ok ? (
        <p className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          {probe.data.detail ?? "Connection refused."}
        </p>
      ) : null}
      {probe.data?.ok ? (
        <p className="rounded-md bg-emerald-50 p-3 text-sm text-emerald-800">
          Reachable
          {probe.data.latency_ms !== null
            ? ` in ${probe.data.latency_ms} ms`
            : ""}
          .
        </p>
      ) : null}
      {remove.isError ? (
        <p className="text-sm text-red-700">{errorMessage(remove.error)}</p>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Schedules
// ---------------------------------------------------------------------------

const ScheduleRow: React.FC<{
  organizationId: string;
  schedule: ExportSchedule;
}> = ({ organizationId, schedule }) => {
  const queryClient = useQueryClient();
  const invalidate = () =>
    queryClient.invalidateQueries({
      queryKey: analyticsKeys.schedules(organizationId),
    });

  const toggle = useMutation({
    mutationFn: () =>
      updateSchedule(organizationId, schedule.id, {
        enabled: !schedule.enabled,
      }),
    onSuccess: invalidate,
  });

  const reset = useMutation({
    mutationFn: () =>
      updateSchedule(organizationId, schedule.id, { reset_circuit: true }),
    onSuccess: invalidate,
  });

  const remove = useMutation({
    mutationFn: () => deleteSchedule(organizationId, schedule.id),
    onSuccess: invalidate,
  });

  return (
    <div className={`${CARD} flex flex-col gap-3`}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-base font-semibold">
            {schedule.destination_label ?? "Destination"}
          </p>
          <p className={HINT}>{describeCadence(schedule)}</p>
          <p className={`${HINT} mt-1`}>
            {schedule.datasets
              .map((dataset) => DATASET_LABELS[dataset as ExportDataset] ?? dataset)
              .join(", ")}{" "}
            · {schedule.lookback_days}-day window
          </p>
          <p className={`${HINT} mt-1`}>
            Last run {formatDateTime(schedule.last_run_at)} · Next{" "}
            {formatDateTime(schedule.next_run_at)}
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            className={SECONDARY}
            disabled={toggle.isPending}
            onClick={() => toggle.mutate()}
          >
            {schedule.enabled ? "Pause" : "Resume"}
          </button>
          <button
            type="button"
            className={DANGER}
            disabled={remove.isPending}
            onClick={() => remove.mutate()}
          >
            <Trash2 className="h-4 w-4" />
            Delete
          </button>
        </div>
      </div>

      {schedule.circuit_opened_at ? (
        <div className="rounded-md bg-red-50 p-3 text-sm text-red-700">
          <p className="font-medium">
            Paused after {schedule.consecutive_failure_count} consecutive
            failures.
          </p>
          <p className="mt-1">
            This schedule stopped rather than retrying indefinitely. Fix the
            destination, test the connection, then clear the failure count.
          </p>
          <button
            type="button"
            className={`${SECONDARY} mt-2`}
            disabled={reset.isPending}
            onClick={() => reset.mutate()}
          >
            Clear failures and resume
          </button>
        </div>
      ) : null}

      {!schedule.circuit_opened_at && schedule.consecutive_failure_count > 0 ? (
        <p className="rounded-md bg-amber-50 p-3 text-sm text-amber-800">
          {schedule.consecutive_failure_count} consecutive failure
          {schedule.consecutive_failure_count === 1 ? "" : "s"}. The schedule
          pauses itself if this keeps up.
        </p>
      ) : null}
    </div>
  );
};

const ScheduleForm: React.FC<{
  organizationId: string;
  destinations: WarehouseDestination[];
}> = ({ organizationId, destinations }) => {
  const queryClient = useQueryClient();
  const [destinationId, setDestinationId] = useState("");
  const [datasets, setDatasets] = useState<ExportDataset[]>(["USAGE_ROLLUPS"]);
  const [cadence, setCadence] = useState<ScheduleCadence>("DAILY");
  const [hourUtc, setHourUtc] = useState(2);
  const [dayOfWeek, setDayOfWeek] = useState(0);
  const [dayOfMonth, setDayOfMonth] = useState(1);
  const [lookbackDays, setLookbackDays] = useState(1);

  const create = useMutation({
    mutationFn: () =>
      createSchedule(organizationId, {
        destination_id: destinationId,
        datasets,
        cadence,
        hour_utc: hourUtc,
        day_of_week: cadence === "WEEKLY" ? dayOfWeek : null,
        day_of_month: cadence === "MONTHLY" ? dayOfMonth : null,
        lookback_days: lookbackDays,
        enabled: true,
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({
        queryKey: analyticsKeys.schedules(organizationId),
      });
      setDatasets(["USAGE_ROLLUPS"]);
    },
  });

  const toggleDataset = (dataset: ExportDataset) =>
    setDatasets((current) =>
      current.includes(dataset)
        ? current.filter((item) => item !== dataset)
        : [...current, dataset],
    );

  return (
    <div className={CARD}>
      <h3 className="text-base font-semibold">Schedule an export</h3>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div>
          <label className={LABEL} htmlFor="schedule-destination">
            Destination
          </label>
          <select
            id="schedule-destination"
            className={INPUT}
            value={destinationId}
            onChange={(event) => setDestinationId(event.target.value)}
          >
            <option value="">Select a destination…</option>
            {destinations.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label} ({KIND_LABELS[item.kind]})
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className={LABEL} htmlFor="schedule-cadence">
            Cadence
          </label>
          <select
            id="schedule-cadence"
            className={INPUT}
            value={cadence}
            onChange={(event) =>
              setCadence(event.target.value as ScheduleCadence)
            }
          >
            {SCHEDULE_CADENCES.map((option) => (
              <option key={option} value={option}>
                {option.charAt(0) + option.slice(1).toLowerCase()}
              </option>
            ))}
          </select>
        </div>

        <div>
          <label className={LABEL} htmlFor="schedule-hour">
            Hour (UTC)
          </label>
          <input
            id="schedule-hour"
            className={INPUT}
            type="number"
            min={0}
            max={23}
            value={hourUtc}
            onChange={(event) => setHourUtc(Number(event.target.value))}
          />
          <p className={HINT}>
            UTC, not local. A local hour moves twice a year and the run in the
            repeated hour would fire twice.
          </p>
        </div>

        {cadence === "WEEKLY" ? (
          <div>
            <label className={LABEL} htmlFor="schedule-weekday">
              Day of week
            </label>
            <select
              id="schedule-weekday"
              className={INPUT}
              value={dayOfWeek}
              onChange={(event) => setDayOfWeek(Number(event.target.value))}
            >
              {WEEKDAY_LABELS.map((day, index) => (
                <option key={day} value={index}>
                  {day}
                </option>
              ))}
            </select>
          </div>
        ) : null}

        {cadence === "MONTHLY" ? (
          <div>
            <label className={LABEL} htmlFor="schedule-day">
              Day of month
            </label>
            <input
              id="schedule-day"
              className={INPUT}
              type="number"
              min={1}
              max={MAX_DAY_OF_MONTH}
              value={dayOfMonth}
              onChange={(event) => setDayOfMonth(Number(event.target.value))}
            />
            <p className={HINT}>
              Capped at {MAX_DAY_OF_MONTH}. A schedule on the 31st would skip
              February entirely.
            </p>
          </div>
        ) : null}

        <div>
          <label className={LABEL} htmlFor="schedule-lookback">
            Lookback (days)
          </label>
          <input
            id="schedule-lookback"
            className={INPUT}
            type="number"
            min={MIN_LOOKBACK_DAYS}
            max={MAX_LOOKBACK_DAYS}
            value={lookbackDays}
            onChange={(event) => setLookbackDays(Number(event.target.value))}
          />
          <p className={HINT}>
            Overlapping windows are intentional — a gap costs far more to notice
            than a duplicate.
          </p>
        </div>
      </div>

      <fieldset className="mt-4">
        <legend className={LABEL}>Datasets</legend>
        <div className="mt-2 grid gap-2 md:grid-cols-2">
          {EXPORT_DATASETS.map((dataset) => (
            <label
              key={dataset}
              className="flex items-start gap-2 rounded-md border border-border p-3"
            >
              <input
                type="checkbox"
                className="mt-1"
                checked={datasets.includes(dataset)}
                onChange={() => toggleDataset(dataset)}
              />
              <span>
                <span className="text-sm font-medium">
                  {DATASET_LABELS[dataset]}
                </span>
                <span className={`${HINT} block`}>{DATASET_HINTS[dataset]}</span>
              </span>
            </label>
          ))}
        </div>
      </fieldset>

      {create.isError ? (
        <p className="mt-4 text-sm text-red-700">{errorMessage(create.error)}</p>
      ) : null}

      <button
        type="button"
        className={`${PRIMARY} mt-5`}
        disabled={!destinationId || datasets.length === 0 || create.isPending}
        onClick={() => create.mutate()}
      >
        {create.isPending ? (
          <Loader2 className="h-4 w-4 animate-spin" />
        ) : null}
        Create schedule
      </button>
    </div>
  );
};

// ---------------------------------------------------------------------------
// Manual trigger
// ---------------------------------------------------------------------------

const ManualTrigger: React.FC<{
  organizationId: string;
  destinations: WarehouseDestination[];
}> = ({ organizationId, destinations }) => {
  const queryClient = useQueryClient();
  const [destinationId, setDestinationId] = useState("");
  const [lookbackDays, setLookbackDays] = useState(1);
  const [datasets, setDatasets] = useState<ExportDataset[]>(["USAGE_ROLLUPS"]);

  const run = useMutation({
    mutationFn: () =>
      triggerSync(organizationId, {
        destination_id: destinationId,
        datasets,
        lookback_days: lookbackDays,
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({
        queryKey: analyticsKeys.runs(organizationId),
      }),
  });

  const active = destinations.filter((item) => item.status === "ACTIVE");

  return (
    <div className={CARD}>
      <h3 className="text-base font-semibold">Sync now</h3>
      <p className={`${HINT} mt-1`}>
        Queued as a background job. The run appears in the history as soon as it
        starts.
      </p>

      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <div>
          <label className={LABEL} htmlFor="manual-destination">
            Destination
          </label>
          <select
            id="manual-destination"
            className={INPUT}
            value={destinationId}
            onChange={(event) => setDestinationId(event.target.value)}
          >
            <option value="">Select…</option>
            {active.map((item) => (
              <option key={item.id} value={item.id}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className={LABEL} htmlFor="manual-lookback">
            Lookback (days)
          </label>
          <input
            id="manual-lookback"
            className={INPUT}
            type="number"
            min={MIN_LOOKBACK_DAYS}
            max={MAX_LOOKBACK_DAYS}
            value={lookbackDays}
            onChange={(event) => setLookbackDays(Number(event.target.value))}
          />
        </div>
        <div className="flex items-end">
          <button
            type="button"
            className={PRIMARY}
            disabled={!destinationId || datasets.length === 0 || run.isPending}
            onClick={() => run.mutate()}
          >
            {run.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <PlayCircle className="h-4 w-4" />
            )}
            Sync now
          </button>
        </div>
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {EXPORT_DATASETS.map((dataset) => (
          <label
            key={dataset}
            className="flex items-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm"
          >
            <input
              type="checkbox"
              checked={datasets.includes(dataset)}
              onChange={() =>
                setDatasets((current) =>
                  current.includes(dataset)
                    ? current.filter((item) => item !== dataset)
                    : [...current, dataset],
                )
              }
            />
            {DATASET_LABELS[dataset]}
          </label>
        ))}
      </div>

      {run.isError ? (
        <p className="mt-4 text-sm text-red-700">{errorMessage(run.error)}</p>
      ) : null}
      {run.data ? (
        <p className="mt-4 rounded-md bg-emerald-50 p-3 text-sm text-emerald-800">
          Queued. Job {run.data.job_id}.
        </p>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

const OrganizationAnalytics: React.FC = () => {
  const { organizationId } = useResolvedOrganization();
  const [tab, setTab] = useState<Tab>("destinations");
  const [adding, setAdding] = useState(false);

  const destinations = useQuery({
    queryKey: analyticsKeys.destinations(organizationId),
    queryFn: () => listDestinations(organizationId),
    enabled: Boolean(organizationId),
  });

  const schedules = useQuery({
    queryKey: analyticsKeys.schedules(organizationId),
    queryFn: () => listSchedules(organizationId),
    enabled: Boolean(organizationId) && tab === "schedules",
  });

  const runs = useQuery({
    queryKey: analyticsKeys.runs(organizationId),
    queryFn: () => listRuns(organizationId, 50),
    enabled: Boolean(organizationId) && tab === "runs",
  });

  const consumption = useQuery({
    queryKey: analyticsKeys.consumption(organizationId, 30),
    queryFn: () => getConsumption(organizationId, 30, "DAY"),
    enabled: Boolean(organizationId) && tab === "consumption",
  });

  const datasets = useQuery({
    queryKey: analyticsKeys.datasets(organizationId),
    queryFn: () => listDatasetDescriptors(organizationId),
    enabled: Boolean(organizationId) && tab === "consumption",
  });

  const totals = useMemo(() => {
    const buckets = consumption.data?.buckets ?? [];
    const byEvent = new Map<string, number>();
    buckets.forEach((bucket) => {
      byEvent.set(
        bucket.event_type,
        (byEvent.get(bucket.event_type) ?? 0) + bucket.billed_micros,
      );
    });
    return Array.from(byEvent.entries()).sort((a, b) => b[1] - a[1]);
  }, [consumption.data]);

  if (!organizationId) {
    return null;
  }

  return (
    <div className="space-y-6">
      <header>
        <h1 className="text-xl font-semibold">Analytics &amp; BI egress</h1>
        <p className={`${HINT} mt-1`}>
          Schedule tenant-scoped exports of your usage, documents, assistant
          activity and automation runs into your own data warehouse. Exports
          carry consumption volumes and the prices you were invoiced.
        </p>
      </header>

      <nav className="flex flex-wrap gap-2 border-b border-border pb-2">
        {TABS.map((option) => (
          <button
            key={option}
            type="button"
            className={`rounded-md px-3 py-2 text-sm font-medium ${
              tab === option
                ? "bg-primary text-primary-foreground"
                : "hover:bg-muted"
            }`}
            onClick={() => setTab(option)}
          >
            {TAB_LABELS[option]}
          </button>
        ))}
      </nav>

      {tab === "destinations" ? (
        <div className="space-y-4">
          {adding ? (
            <DestinationForm
              organizationId={organizationId}
              onDone={() => setAdding(false)}
            />
          ) : (
            <button
              type="button"
              className={PRIMARY}
              onClick={() => setAdding(true)}
            >
              <Plug className="h-4 w-4" />
              Add destination
            </button>
          )}

          {destinations.isLoading ? (
            <p className={HINT}>Loading destinations…</p>
          ) : null}
          {destinations.data?.length === 0 && !adding ? (
            <p className={HINT}>No destinations yet.</p>
          ) : null}
          {destinations.data?.map((destination) => (
            <DestinationRow
              key={destination.id}
              organizationId={organizationId}
              destination={destination}
            />
          ))}
        </div>
      ) : null}

      {tab === "schedules" ? (
        <div className="space-y-4">
          <ScheduleForm
            organizationId={organizationId}
            destinations={destinations.data ?? []}
          />
          <ManualTrigger
            organizationId={organizationId}
            destinations={destinations.data ?? []}
          />
          {schedules.isLoading ? <p className={HINT}>Loading…</p> : null}
          {schedules.data?.length === 0 ? (
            <p className={HINT}>No schedules yet.</p>
          ) : null}
          {schedules.data?.map((schedule) => (
            <ScheduleRow
              key={schedule.id}
              organizationId={organizationId}
              schedule={schedule}
            />
          ))}
        </div>
      ) : null}

      {tab === "runs" ? (
        <div className={CARD}>
          <h3 className="text-base font-semibold">Recent runs</h3>
          {runs.isLoading ? <p className={`${HINT} mt-2`}>Loading…</p> : null}
          {runs.data?.length === 0 ? (
            <p className={`${HINT} mt-2`}>Nothing has run yet.</p>
          ) : null}
          <div className="mt-4 space-y-3">
            {runs.data?.map((run) => (
              <div
                key={run.id}
                className="rounded-md border border-border p-4 text-sm"
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2">
                    <span
                      className={`rounded border px-2 py-0.5 text-xs ${
                        STATUS_CLASSES[run.status]
                      }`}
                    >
                      {STATUS_LABELS[run.status]}
                    </span>
                    <span className="font-medium">{run.destination_label}</span>
                    <span className={HINT}>
                      {run.trigger === "MANUAL" ? "Manual" : "Scheduled"}
                    </span>
                  </div>
                  <span className={HINT}>{formatDateTime(run.started_at)}</span>
                </div>

                <div className={`${HINT} mt-2 grid gap-1 md:grid-cols-3`}>
                  {/* "Not counted" is deliberate: a crashed run has an unknown
                      row count, and rendering 0 would make it look like an
                      empty window. */}
                  <span>Rows: {formatCount(run.row_count)}</span>
                  <span>Size: {formatBytes(run.byte_count)}</span>
                  <span>Parts: {formatCount(run.part_count)}</span>
                </div>

                {run.bundle_digest ? (
                  <p className={`${HINT} mt-2 font-mono break-all`}>
                    digest {run.bundle_digest}
                  </p>
                ) : null}

                {run.error_detail ? (
                  <p className="mt-2 rounded-md bg-red-50 p-2 text-red-700">
                    {run.error_code}: {run.error_detail}
                  </p>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}

      {tab === "consumption" ? (
        <div className="space-y-4">
          <div className={CARD}>
            <div className="flex items-center gap-2">
              <BarChart3 className="h-4 w-4 text-muted-foreground" />
              <h3 className="text-base font-semibold">Last 30 days</h3>
            </div>
            {consumption.isLoading ? (
              <p className={`${HINT} mt-2`}>Loading…</p>
            ) : null}
            {consumption.data ? (
              <>
                <div className="mt-4 grid gap-4 md:grid-cols-2">
                  <div>
                    <p className={HINT}>Total invoiced</p>
                    <p className="text-2xl font-semibold">
                      {formatMicros(consumption.data.total_billed_micros)}
                    </p>
                  </div>
                  <div>
                    <p className={HINT}>Metered events</p>
                    <p className="text-2xl font-semibold">
                      {consumption.data.total_event_count.toLocaleString()}
                    </p>
                  </div>
                </div>

                <div className="mt-5 space-y-2">
                  {totals.map(([eventType, micros]) => {
                    const share =
                      consumption.data.total_billed_micros > 0
                        ? (micros / consumption.data.total_billed_micros) * 100
                        : 0;
                    return (
                      <div key={eventType}>
                        <div className="flex justify-between text-sm">
                          <span>{eventType}</span>
                          <span>{formatMicros(micros)}</span>
                        </div>
                        <div className="mt-1 h-2 rounded bg-muted">
                          <div
                            className="h-2 rounded bg-primary"
                            style={{ width: `${Math.min(share, 100)}%` }}
                          />
                        </div>
                      </div>
                    );
                  })}
                  {totals.length === 0 ? (
                    <p className={HINT}>No metered usage in this window.</p>
                  ) : null}
                </div>

                {consumption.data.p95_latency_ms !== null ? (
                  <p className={`${HINT} mt-4`}>
                    p95 latency {consumption.data.p95_latency_ms} ms
                    {consumption.data.latency_method ===
                    "HISTOGRAM_INTERPOLATED"
                      ? " (interpolated — accurate to one bucket width)"
                      : ""}
                  </p>
                ) : null}
              </>
            ) : null}
          </div>

          <div className={CARD}>
            <h3 className="text-base font-semibold">Exportable datasets</h3>
            <p className={`${HINT} mt-1`}>
              These column lists come from the same specifications the Parquet
              writer uses, so what you model downstream matches what you
              receive.
            </p>
            <div className="mt-4 space-y-4">
              {datasets.data?.map((descriptor) => (
                <details
                  key={descriptor.dataset}
                  className="rounded-md border border-border p-3"
                >
                  <summary className="cursor-pointer text-sm font-medium">
                    {DATASET_LABELS[descriptor.dataset]} (v{descriptor.version})
                  </summary>
                  <p className={`${HINT} mt-2`}>{descriptor.description}</p>
                  <ul className="mt-2 space-y-1">
                    {descriptor.columns.map((column) => (
                      <li key={column.name} className="text-xs">
                        <span className="font-mono">{column.name}</span>{" "}
                        <span className={HINT}>
                          {column.type} — {column.description}
                        </span>
                      </li>
                    ))}
                  </ul>
                </details>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
};

export default OrganizationAnalytics;
