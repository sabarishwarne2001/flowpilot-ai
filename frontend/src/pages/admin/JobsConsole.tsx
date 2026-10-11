/**
 * Campaign session 2: the operator's Background jobs console (platform superadmins).
 *
 *   Overview   what is waiting, running, retrying and dead, per job type, and the oldest wait
 *   Dead       jobs that failed every attempt (the dead-letter queue): the error, the organization,
 *              and a retry that sends the job round again once the cause is fixed
 *
 * Before this page a dead job was visible only as a failed document or a database row.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Activity, Loader2, RefreshCw, RotateCcw, Skull } from "lucide-react";
import { toast } from "sonner";

import {
  BUTTON_SECONDARY, HINT, SCROLL_X, SECTION_TITLE, SELECT, SURFACE, TABLE_HEAD, TABLE_ROW,
} from "@/components/ui/primitives";
import { PageHeader } from "@/components/ui/PageHeader";
import { TabList, useUrlTab, type TabDefinition } from "@/components/ui/Tabs";
import { errorMessage } from "@/services/api/errors";
import { getDeadJobs, getJobsSummary, platformJobKeys, retryDeadJob } from "@/services/api/platformJobs";
import { formatTimestamp } from "@/utils/displayTime";

type Tab = "overview" | "dead";
const TABS: readonly TabDefinition<Tab>[] = [
  { id: "overview", label: "Overview", icon: Activity },
  { id: "dead", label: "Dead jobs", icon: Skull },
];
const TAB_IDS = TABS.map((entry) => entry.id);
const COUNT = new Intl.NumberFormat();

const waited = (value: number | null): string => {
  if (value === null) {
    return "—";
  }
  if (value < 120) {
    return `${Math.round(value)} s`;
  }
  if (value < 7_200) {
    return `${Math.round(value / 60)} min`;
  }
  return `${(value / 3_600).toFixed(1)} h`;
};

const Stat: React.FC<{ readonly label: string; readonly value: number; readonly hint: string; readonly alert?: boolean }> = ({
  label, value, hint, alert = false,
}) => (
  <div className={`${SURFACE} p-4`}>
    <p className={HINT}>{label}</p>
    <p className={`mt-1 text-lg font-semibold ${alert && value > 0 ? "text-destructive" : ""}`}>{COUNT.format(value)}</p>
    <p className={`mt-1 ${HINT}`}>{hint}</p>
  </div>
);

const JobsConsole: React.FC = () => {
  const queryClient = useQueryClient();
  const [tab, setTab] = useUrlTab<Tab>(TAB_IDS);
  const [jobType, setJobType] = useState("");

  const summary = useQuery({ queryKey: platformJobKeys.summary(), queryFn: getJobsSummary });
  const dead = useQuery({
    queryKey: platformJobKeys.dead(jobType),
    queryFn: () => getDeadJobs(jobType),
    enabled: tab === "dead",
  });
  const retry = useMutation({
    mutationFn: (jobId: string) => retryDeadJob(jobId),
    onSuccess: async () => {
      toast.success("The job was sent round again.");
      await queryClient.invalidateQueries({ queryKey: platformJobKeys.all() });
    },
    onError: (error) => toast.error(errorMessage(error, "The job could not be retried.")),
  });

  const s = summary.data;
  const deadTypes = (s?.by_type ?? []).filter((row) => row.dead > 0).map((row) => row.job_type);

  return (
    <div className="mx-auto max-w-7xl space-y-6" data-testid="jobs-console">
      <PageHeader
        icon={Activity}
        eyebrow="Platform"
        title="Background jobs"
        description="Document reading, AI enrichment, the engines and every scheduled sweep: what is waiting, what is running, and what has failed for good."
        actions={
          <button type="button" className={BUTTON_SECONDARY} onClick={() => void queryClient.invalidateQueries({ queryKey: platformJobKeys.all() })}>
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />Refresh
          </button>
        }
      />
      <TabList label="Background jobs" idBase="jobs" tabs={TABS} value={tab} onChange={setTab} />

      {summary.isLoading ? <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</div> : null}
      {summary.isError ? <p role="alert" className="text-sm text-destructive">{errorMessage(summary.error, "The job summary could not be loaded.")}</p> : null}

      {s && tab === "overview" ? (
        <div className="space-y-4">
          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
            <Stat label="Waiting" value={s.totals.pending} hint="Queued, not yet picked up" />
            <Stat label="Running" value={s.totals.running} hint="Held by a worker now" />
            <Stat label="Retrying" value={s.totals.retrying} hint="Failed once, will run again" />
            <Stat label="Dead" value={s.totals.dead} hint="Failed every attempt" alert />
            <Stat label="Expired leases" value={s.totals.expired_leases} hint="A worker stopped answering; reclaimed shortly" alert />
          </section>
          <section className={`${SURFACE} ${SCROLL_X} p-4`}>
            <h2 className={SECTION_TITLE}>By job type</h2>
            <p className={HINT}>Generated {formatTimestamp(s.generated_at)}.</p>
            {s.by_type.length === 0 ? (
              <p className="mt-2 text-sm text-muted-foreground">No jobs yet.</p>
            ) : (
              <table className="mt-2 w-full text-sm">
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th className="py-2 pr-3">Job type</th><th className="pr-3">Waiting</th><th className="pr-3">Running</th>
                    <th className="pr-3">Retrying</th><th className="pr-3">Dead</th><th className="pr-3">Done (24 h)</th>
                    <th className="pr-3">Oldest wait</th>
                  </tr>
                </thead>
                <tbody>
                  {s.by_type.map((row) => (
                    <tr key={row.job_type} className={TABLE_ROW}>
                      <td className="py-2 pr-3 font-mono text-xs">{row.job_type}</td>
                      <td className="pr-3">{COUNT.format(row.pending)}</td>
                      <td className="pr-3">{COUNT.format(row.running)}</td>
                      <td className="pr-3">{COUNT.format(row.retrying)}</td>
                      <td className={`pr-3 ${row.dead > 0 ? "font-semibold text-destructive" : ""}`}>{COUNT.format(row.dead)}</td>
                      <td className="pr-3">{COUNT.format(row.succeeded_24h)}</td>
                      <td className="pr-3">{waited(row.oldest_waiting_seconds)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </section>
        </div>
      ) : null}

      {tab === "dead" ? (
        <section className={`${SURFACE} ${SCROLL_X} p-4`}>
          <div className="flex flex-wrap items-end justify-between gap-3">
            <div>
              <h2 className={SECTION_TITLE}>Dead jobs</h2>
              <p className={HINT}>Each failed every attempt. Fix the cause first: a retry runs the same job again.</p>
            </div>
            <label className="text-sm">
              <span className="sr-only">Job type</span>
              <select className={SELECT} value={jobType} onChange={(event) => setJobType(event.target.value)} aria-label="Job type">
                <option value="">All job types</option>
                {deadTypes.map((name) => <option key={name} value={name}>{name}</option>)}
              </select>
            </label>
          </div>
          {dead.isLoading ? <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin" />Loading…</div> : null}
          {dead.isError ? <p role="alert" className="mt-3 text-sm text-destructive">{errorMessage(dead.error, "The dead jobs could not be loaded.")}</p> : null}
          {dead.data && dead.data.items.length === 0 ? <p className="mt-3 text-sm text-muted-foreground">No dead jobs.</p> : null}
          {dead.data && dead.data.items.length > 0 ? (
            <>
              <p className={`mt-2 ${HINT}`}>
                Showing {COUNT.format(dead.data.items.length)} of {COUNT.format(dead.data.total)}, newest first.
              </p>
              <table className="mt-2 w-full text-sm">
                <thead>
                  <tr className={TABLE_HEAD}>
                    <th className="py-2 pr-3">Job type</th><th className="pr-3">Organization</th><th className="pr-3">Attempts</th>
                    <th className="pr-3">Last error</th><th className="pr-3">Failed</th><th className="pr-3"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {dead.data.items.map((job) => (
                    <tr key={job.id} className={TABLE_ROW} data-testid="dead-job-row">
                      <td className="py-2 pr-3 font-mono text-xs">{job.job_type}</td>
                      <td className="pr-3">{job.organization_name ?? (job.organization_id ? job.organization_id : "Platform")}</td>
                      <td className="pr-3">{job.attempts} of {job.max_attempts}</td>
                      <td className="max-w-md pr-3"><span className="line-clamp-2 break-words font-mono text-xs" title={job.last_error ?? ""}>{job.last_error ?? "—"}</span></td>
                      <td className="whitespace-nowrap pr-3">{formatTimestamp(job.updated_at)}</td>
                      <td className="pr-3 text-right">
                        <button
                          type="button"
                          className={BUTTON_SECONDARY}
                          disabled={retry.isPending}
                          onClick={() => retry.mutate(job.id)}
                          aria-label={`Retry ${job.job_type} job ${job.id}`}
                        >
                          {retry.isPending && retry.variables === job.id
                            ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                            : <RotateCcw className="h-3.5 w-3.5" aria-hidden />}
                          Retry
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
};

export default JobsConsole;
