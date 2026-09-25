/**
 * ARCH46-S2:page-obligations — the workspace's obligations: a list (filter by
 * state, kind and owner, search, export .ics / .csv), a month calendar, the
 * holiday calendars business days are counted on, and subscribable calendar
 * feeds. Obligations read from documents arrive by themselves after a
 * document is processed; "New obligation" adds a person's own. Opening this
 * page with ?entity=<record id> shows one record's obligations.
 */
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { CalendarClock, Download, Loader2, Lock, Plus } from "lucide-react";

import CalendarFeeds from "@/components/obligations/CalendarFeeds";
import HolidayCalendars from "@/components/obligations/HolidayCalendars";
import NewObligation from "@/components/obligations/NewObligation";
import ObligationCalendar from "@/components/obligations/ObligationCalendar";
import ObligationTable from "@/components/obligations/ObligationTable";
import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_GHOST, BUTTON_PRIMARY, HINT, INPUT, PAGE_TITLE, SECTION_TITLE, SELECT, SURFACE } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { obligationPath, verificationPath } from "@/routes/tenantPaths";
import { downloadObligations, listObligations, obligationKeys, type ObligationFilters } from "@/services/api/obligations";
import { errorMessage } from "@/services/api/errors";
import { KINDS, KIND_LABELS, STATE_LABELS, type ObligationDetail, type ObligationState } from "@/types/obligations";

type Tab = "list" | "calendar" | "holidays" | "feeds";

const STATE_FILTERS: readonly { readonly id: string; readonly label: string }[] = [
  { id: "OPEN,DUE_SOON,OVERDUE", label: "Open" },
  { id: "DUE_SOON", label: STATE_LABELS.DUE_SOON },
  { id: "OVERDUE", label: STATE_LABELS.OVERDUE },
  { id: "DONE,WAIVED", label: "Closed" },
  { id: "", label: "All" },
];

const Obligations: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const entity = params.get("entity") ?? "";
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.obligations);
  const role = workspace?.role;
  const canEdit = role === "ADMIN" || role === "OWNER" || role === "CONTRIBUTOR";
  const isAdmin = role === "ADMIN" || role === "OWNER";
  const [tab, setTab] = useState<Tab>("list");
  const [state, setState] = useState("OPEN,DUE_SOON,OVERDUE");
  const [kind, setKind] = useState("");
  const [mine, setMine] = useState(false);
  const [q, setQ] = useState("");
  const [composing, setComposing] = useState(false);
  const filters: ObligationFilters = {
    ...(state ? { state } : {}), ...(kind ? { kind } : {}), ...(mine ? { owner: "me" } : {}),
    ...(q.trim() ? { q: q.trim() } : {}), ...(entity ? { entity_id: entity } : {}),
  };
  const query = useQuery({
    queryKey: obligationKeys.list(workspaceId, filters),
    queryFn: () => listObligations(workspaceId, filters),
    enabled: Boolean(workspaceId && capability.granted && tab === "list"),
  });
  const created = (detail: ObligationDetail): void => {
    setComposing(false);
    navigate(obligationPath(orgSlug, workspaceSlug, detail.obligation.id));
  };

  if (!capability.granted) {
    return (
      <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="obligations-lock">
        <div className="flex items-center gap-2">
          <Lock className="h-4 w-4" aria-hidden />
          <h2 id="obligations-lock" className={SECTION_TITLE}>Obligations</h2>
        </div>
        <p className="text-sm text-muted-foreground">
          Every renewal date, notice deadline, payment, delivery, expiry and report your documents commit you to — read
          from the documents, counted in business days on your own holiday calendars, owned by a person, with reminders
          before they are due, Flow Builder triggers when they are due soon or overdue, and a calendar feed for your
          calendar app.
        </p>
        <p className={HINT}>It&apos;s included on the Business and Enterprise plans.</p>
      </section>
    );
  }
  const counts = query.data?.counts_by_state ?? {};
  return (
    <div className="space-y-4 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <CalendarClock className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>Obligations</h1>
        {query.data ? <span className={HINT}>Today is {query.data.today} in {query.data.timezone}</span> : null}
        <div className="ml-auto flex flex-wrap gap-2">
          <button type="button" className={BUTTON_GHOST} onClick={() => void downloadObligations(workspaceId, "ics")}>
            <Download className="h-4 w-4" aria-hidden /> .ics
          </button>
          <button type="button" className={BUTTON_GHOST} onClick={() => void downloadObligations(workspaceId, "csv")}>
            <Download className="h-4 w-4" aria-hidden /> .csv
          </button>
          {canEdit && !composing ? (
            <button type="button" className={BUTTON_PRIMARY} onClick={() => setComposing(true)}>
              <Plus className="h-4 w-4" aria-hidden /> New obligation
            </button>
          ) : null}
        </div>
      </header>
      {query.data && query.data.pending_review > 0 ? (
        <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-2 text-sm">
          {query.data.pending_review} obligation(s) were read with a doubt and wait for a person to confirm them.{" "}
          <Link className="font-semibold underline" to={verificationPath(orgSlug, workspaceSlug)}>Open the review hub</Link>
        </p>
      ) : null}
      {composing ? <NewObligation workspaceId={workspaceId} onCreated={created} onCancel={() => setComposing(false)} /> : null}
      <div className="flex flex-wrap gap-2 border-b border-border/60" role="tablist" aria-label="Obligations views">
        {([["list", "List"], ["calendar", "Calendar"], ["holidays", "Holiday calendars"], ["feeds", "Calendar feeds"]] as const)
          .map(([id, label]) => (
            <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
              className={`border-b-2 px-3 py-2 text-sm font-semibold ${tab === id ? "border-primary text-primary" : "border-transparent text-muted-foreground hover:text-foreground"}`}>
              {label}
            </button>
          ))}
      </div>
      {tab === "list" ? (
        <div className="space-y-3">
          {entity ? (
            <p className="text-sm">
              Showing one record&apos;s obligations.{" "}
              <button type="button" className="font-semibold text-primary hover:underline"
                onClick={() => { params.delete("entity"); setParams(params, { replace: true }); }}>Show all</button>
            </p>
          ) : null}
          <div className="flex flex-wrap items-center gap-2">
            {STATE_FILTERS.map((f) => (
              <button key={f.label} type="button" aria-pressed={state === f.id} onClick={() => setState(f.id)}
                className={`rounded-full border px-3 py-1 text-xs font-semibold ${state === f.id ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground hover:bg-muted"}`}>
                {f.label}
                {f.id && !f.id.includes(",") ? ` (${counts[f.id as ObligationState] ?? 0})` : ""}
              </button>
            ))}
            <select className={`${SELECT} w-44`} value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Kind">
              <option value="">Every kind</option>
              {KINDS.map((k) => <option key={k} value={k}>{KIND_LABELS[k]}</option>)}
            </select>
            <label className="inline-flex items-center gap-1 text-sm">
              <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Mine
            </label>
            <input className={`${INPUT} w-56`} value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search title or party"
              aria-label="Search" />
          </div>
          {query.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
          {query.isError ? <p className="text-sm text-destructive">{errorMessage(query.error, "Obligations could not be loaded.")}</p> : null}
          {query.data ? (
            <div className={SURFACE}>
              <ObligationTable rows={query.data.items} orgSlug={orgSlug} workspaceSlug={workspaceSlug}
                empty="No obligations match. Obligations are read from documents as they are processed." />
              {query.data.total > query.data.items.length ? (
                <p className={`${HINT} p-2`}>Showing {query.data.items.length} of {query.data.total}; narrow the filters to see the rest.</p>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {tab === "calendar" ? (
        <div className="space-y-2">
          <label className="inline-flex items-center gap-1 text-sm">
            <input type="checkbox" checked={mine} onChange={(e) => setMine(e.target.checked)} /> Only mine
          </label>
          <ObligationCalendar workspaceId={workspaceId} orgSlug={orgSlug} workspaceSlug={workspaceSlug} mine={mine} />
        </div>
      ) : null}
      {tab === "holidays" ? <HolidayCalendars workspaceId={workspaceId} isAdmin={isAdmin} /> : null}
      {tab === "feeds" ? <CalendarFeeds workspaceId={workspaceId} /> : null}
    </div>
  );
};

export default Obligations;
