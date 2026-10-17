import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { BookOpen, Building2, History, Lock, Route as RouteIcon, ScrollText, ShieldCheck, Sparkles, Trash2 } from "lucide-react";

import { useHelm } from "@/components/helm/HelmContext";
import { InstrumentRing, StateBadge } from "@/components/helm/HelmBits";
import { HelmMark } from "@/components/helm/ui/HelmMark";
import { helmPath, organizationBillingPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import {
  buildHelmSample,
  createHelmBook,
  deleteHelmSample,
  getHelmCockpit,
  getHelmOverview,
  helmKeys,
  requestHelmBacktest,
} from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";

/**
 * "Explore Helm with a sample company" (brief §10.3), on every plan: an invented Pune distributor
 * with nine months of documents, in a labelled sample workspace anyone can delete.
 */
const SampleCompany: React.FC = () => {
  const { organizationId, orgSlug, overview, canDecide } = useHelm();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const sample = overview.sample;
  // While the worker builds it, read the overview again every few seconds.
  useQuery({
    queryKey: helmKeys.overview(organizationId),
    queryFn: () => getHelmOverview(organizationId),
    enabled: sample.building,
    refetchInterval: sample.building ? 3_000 : false,
  });
  const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
  const build = useMutation({
    mutationFn: () => buildHelmSample(organizationId),
    onSuccess: () => void refresh(),
    onError: (error) => toast.error(errorMessage(error, "Helm could not build the sample company.")),
  });
  const remove = useMutation({
    mutationFn: () => deleteHelmSample(organizationId),
    onSuccess: () => {
      toast.success("The sample company is deleted.");
      void refresh();
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not delete the sample company.")),
  });
  const [confirming, setConfirming] = useState(false);

  return (
    <section aria-labelledby="helm-start-sample" className="helm-panel helm-panel-primary helm-enter p-6" style={{ ["--i" as string]: 2 }} data-testid="helm-sample">
      <h2 id="helm-start-sample" className="flex items-center gap-2 text-[15px] font-semibold">
        <Building2 className="helm-accent h-4 w-4" aria-hidden="true" />
        Explore Helm with a sample company
      </h2>
      <p className="helm-muted mt-1 text-sm">
        {sample.name}, an invented distributor in Pune, with nine months of purchase orders, invoices, contracts and bank
        statements. Helm reads them with the same engines as yours and shows what it would have held. It lives in its own
        labelled workspace and does not count towards your plan.
      </p>
      {sample.book_id ? (
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            className="helm-btn helm-btn-primary"
            onClick={() => navigate(`${helmPath(orgSlug)}?book=${encodeURIComponent(sample.book_id as string)}`)}
          >
            Open {sample.name}
          </button>
          <button
            type="button"
            className="helm-btn"
            onClick={() => navigate(`${helmPath(orgSlug)}?book=${encodeURIComponent(sample.book_id as string)}&tour=0`)}
          >
            Take the guided tour
          </button>
          {canDecide ? (
            confirming ? (
              <>
                <button type="button" className="helm-btn" disabled={remove.isPending} onClick={() => remove.mutate()}>
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                  Yes, delete it
                </button>
                <button type="button" className="helm-btn" onClick={() => setConfirming(false)}>
                  Keep it
                </button>
              </>
            ) : (
              <button type="button" className="helm-btn" onClick={() => setConfirming(true)}>
                <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                Delete the sample company
              </button>
            )
          ) : null}
        </div>
      ) : sample.building || build.isPending ? (
        <p className="mt-4 flex items-center gap-3 text-sm" role="status" aria-live="polite">
          <HelmMark size={22} spinning className="helm-accent shrink-0" />
          Building {sample.name}: Helm is reading its documents. This takes about a minute.
        </p>
      ) : canDecide ? (
        <button type="button" className="helm-btn helm-btn-primary mt-3" onClick={() => build.mutate()}>
          Explore Helm with a sample company
        </button>
      ) : (
        <p className="helm-muted mt-3 text-sm">An organization owner or admin can add it.</p>
      )}
    </section>
  );
};

/** Helm preview: one replay a week, of up to three months, of a workspace's own documents. */
const PreviewReplay: React.FC = () => {
  const { organizationId, orgSlug, overview, canDecide } = useHelm();
  const navigate = useNavigate();
  const candidates = overview.workspaces.filter((w) => !w.is_sample);
  const [workspaceId, setWorkspaceId] = useState(candidates[0]?.id ?? "");
  const replay = useMutation({
    mutationFn: () => requestHelmBacktest(organizationId, { workspace_id: workspaceId, months: 3 }),
    onSuccess: (queued) =>
      navigate(
        `${helmPath(orgSlug, "time-machine")}?book=${encodeURIComponent(queued.book_id)}&backtest=${encodeURIComponent(queued.id)}`,
      ),
    onError: (error) => toast.error(errorMessage(error, "Helm could not start the replay.")),
  });
  if (!canDecide || candidates.length === 0) {
    return null;
  }
  const { backtest_allowed: allowed, next_backtest_at: next } = overview.preview;
  return (
    <section aria-labelledby="helm-start-replay" className="helm-panel helm-enter p-6" style={{ ["--i" as string]: 3 }}>
      <h2 id="helm-start-replay" className="flex items-center gap-2 text-[15px] font-semibold">
        <History className="h-4 w-4" aria-hidden="true" />
        Replay your own documents
      </h2>
      <p className="helm-muted mt-1 text-sm">
        See what Helm would have held over the last three months of a workspace's documents. Nothing is executed. Helm
        preview runs one replay a week.
      </p>
      {allowed ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <label className="text-xs">
            <span className="helm-label block">Workspace</span>
            <select className="helm-input mt-1" value={workspaceId} onChange={(event) => setWorkspaceId(event.target.value)}>
              {candidates.map((w) => (
                <option key={w.id} value={w.id}>
                  {w.name}
                </option>
              ))}
            </select>
          </label>
          <button type="button" className="helm-btn helm-btn-primary" disabled={!workspaceId || replay.isPending} onClick={() => replay.mutate()}>
            Replay three months
          </button>
        </div>
      ) : (
        <p className="mt-3 text-sm">This week's replay is used. The next one is available {next ? `on ${formatCalendarDate(next)}` : "next week"}.</p>
      )}
    </section>
  );
};

const FIVE_PARTS: { name: string; text: string; icon: typeof BookOpen }[] = [
  { name: "Promise ledger", text: "Every promise your documents make: what you owe, what is owed to you, agreed prices, deadlines.", icon: BookOpen },
  { name: "Mandates", text: "Rules that must always be true, such as never paying above the agreed price, in plain words.", icon: ScrollText },
  { name: "Planner", text: "A payment plan that keeps every mandate, the law on MSME payments and your cash floor.", icon: RouteIcon },
  { name: "Guard", text: "Checks every action twice, when planned and just before it runs, and certifies it.", icon: ShieldCheck },
  { name: "Time machine", text: "Replays your own history to show what Helm would have held, before you trust it.", icon: History },
];

/**
 * Behind the preview's sales card: the sample company's live instruments, blurred and inert, so
 * the card sits on the real thing. Nothing is drawn until the sample exists (no invented numbers).
 */
const SampleBackdrop: React.FC<{ readonly bookId: string }> = ({ bookId }) => {
  const { organizationId } = useHelm();
  const cockpit = useQuery({
    queryKey: helmKeys.cockpit(organizationId, bookId),
    queryFn: () => getHelmCockpit(organizationId, bookId),
    staleTime: 60_000,
  });
  const instruments = cockpit.data?.summary.instruments ?? [];
  if (instruments.length === 0) {
    return null;
  }
  return (
    <div className="helm-preview-veil absolute inset-0 grid grid-cols-2 gap-3 p-4 sm:grid-cols-3" aria-hidden="true" inert>
      {instruments.slice(0, 6).map((instrument) => (
        <div key={instrument.mandate_id} className="helm-panel-raised flex gap-3 p-3">
          <InstrumentRing state={instrument.state} size={40} />
          <div className="min-w-0">
            <p className="truncate text-xs font-semibold">{instrument.title}</p>
            <StateBadge state={instrument.state} />
            <p className="helm-figure mt-1 truncate text-sm font-semibold">{instrument.figure}</p>
          </div>
        </div>
      ))}
    </div>
  );
};

/** Where Helm begins: start it on a workspace (Business and up), or read what preview offers. */
export const HelmStart: React.FC = () => {
  const { organizationId, orgSlug, overview, access, canDecide, role, books } = useHelm();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const start = useMutation({
    mutationFn: (workspaceId: string) => createHelmBook(organizationId, { workspace_id: workspaceId }),
    onSuccess: async (book) => {
      await queryClient.invalidateQueries({ queryKey: helmKeys.overview(organizationId) });
      toast.success(`Helm is reading ${book.name}. It starts in Propose mode: it suggests, a person decides.`);
      navigate(`${helmPath(orgSlug, "setup")}?book=${encodeURIComponent(book.id)}`);
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not start on this workspace.")),
  });
  const candidates = overview.workspaces.filter((w) => !w.is_sample && !w.has_book);

  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="helm-panel helm-panel-strong helm-enter relative overflow-hidden p-6 sm:p-9">
        <div className="pointer-events-none absolute -right-16 -top-16 hidden text-[hsl(var(--helm-accent)/0.12)] md:block" aria-hidden="true">
          <HelmMark size={340} />
        </div>
        <span className="helm-accent flex h-14 w-14 items-center justify-center rounded-2xl border border-[hsl(var(--helm-accent)/0.4)] bg-[hsl(var(--helm-accent)/0.12)] shadow-[0_0_40px_-8px_hsl(var(--helm-accent)/0.8)]">
          <HelmMark size={32} />
        </span>
        <p className="helm-label mt-5">The flagship engine</p>
        <h1 className="helm-title mt-2 max-w-2xl text-[2.25rem] lg:text-[2.75rem]">
          Helm keeps your business <span className="helm-signal-text">promises</span>
        </h1>
        <p className="helm-muted mt-3 max-w-2xl text-[15px] leading-relaxed">
          Helm reads the documents you already have and keeps what they promise. It decides with rules you can read; AI
          only helps you write a rule. It never moves money: payments go through your own bank.
        </p>
        <ul className="relative mt-7 grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
          {FIVE_PARTS.map((part, index) => {
            const Icon = part.icon;
            return (
              <li key={part.name} className="helm-panel-raised helm-enter p-4" style={{ ["--i" as string]: index + 1 }}>
                <Icon className="helm-accent h-4 w-4" aria-hidden="true" />
                <p className="mt-2 text-sm font-semibold">{part.name}</p>
                <p className="helm-muted mt-1 text-xs leading-relaxed">{part.text}</p>
              </li>
            );
          })}
        </ul>
      </div>

      <SampleCompany />

      {access.preview ? <PreviewReplay /> : null}

      {access.preview ? (
        <div className="helm-panel helm-enter relative min-h-[17rem] overflow-hidden" style={{ ["--i" as string]: 4 }} data-testid="helm-preview-card">
          {overview.sample.book_id ? <SampleBackdrop bookId={overview.sample.book_id} /> : null}
          <div className="relative flex min-h-[17rem] items-center justify-center p-5 sm:p-8">
            <div className="helm-panel helm-panel-strong helm-panel-primary max-w-lg p-6 text-center">
              <span className="helm-accent mx-auto flex h-11 w-11 items-center justify-center rounded-2xl border border-[hsl(var(--helm-accent)/0.4)] bg-[hsl(var(--helm-accent)/0.12)]">
                <Sparkles className="h-5 w-5" aria-hidden="true" />
              </span>
              <p className="mt-3 flex items-center justify-center gap-2 text-lg font-semibold tracking-tight">
                <Lock className="h-4 w-4" aria-hidden="true" />
                Helm preview
              </p>
              <p className="helm-muted mt-2 text-sm leading-relaxed">
                Helm is included on the Business and Enterprise plans. On your plan you can explore Helm with a sample company
                and run one backtest a week, of up to three months, on your own documents.
              </p>
              {role === "OWNER" || role === "BILLING" ? (
                <Link to={organizationBillingPath(orgSlug)} className="helm-btn helm-btn-primary helm-btn-hero mt-4">
                  See plans
                </Link>
              ) : null}
            </div>
          </div>
        </div>
      ) : canDecide ? (
        <section aria-labelledby="helm-start-workspaces" className="helm-panel helm-enter p-6" style={{ ["--i" as string]: 4 }}>
          <h2 id="helm-start-workspaces" className="text-[15px] font-semibold">
            Start Helm on a workspace
          </h2>
          <p className="helm-muted mt-1 text-xs">
            Helm keeps one book per workspace. Four short steps follow: who you are, your cash, your mandates, and a
            replay of what Helm would have held.
          </p>
          {candidates.length === 0 ? (
            <p className="mt-3 text-sm">Every workspace you can read already has Helm.</p>
          ) : (
            <ul className="mt-3 space-y-2">
              {candidates.map((workspace) => (
                <li key={workspace.id} className="helm-hairline flex flex-wrap items-center justify-between gap-2 border-t pt-2 first:border-t-0 first:pt-0">
                  <span className="text-sm font-medium">{workspace.name}</span>
                  <button
                    type="button"
                    className="helm-btn helm-btn-primary"
                    disabled={start.isPending}
                    onClick={() => start.mutate(workspace.id)}
                  >
                    Start Helm on {workspace.name}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </section>
      ) : books.length === 0 ? (
        <p className="helm-panel p-5 text-sm">An organization owner or admin starts Helm. You will see its plan here once they do.</p>
      ) : null}
    </div>
  );
};

export default HelmStart;
