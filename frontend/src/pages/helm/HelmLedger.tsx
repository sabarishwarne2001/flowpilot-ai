import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, FileText, Lock, X } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { formatHelmMoney } from "@/components/helm/money";
import { CalendarHeatmap, type HeatDay } from "@/components/helm/ui/CalendarHeatmap";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { LoadFailed } from "@/components/common/LoadFailed";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { getHelmCalendar, getHelmLedger, helmKeys } from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { HelmPromise } from "@/types/helm";

const VIEWS = [
  { id: "owed", label: "Owed to us", filter: { direction: "OWED_TO_US" } },
  { id: "owe", label: "We owe", filter: { direction: "WE_OWE" } },
  { id: "risk", label: "At risk", filter: { at_risk: true } },
  { id: "all", label: "Everything", filter: {} },
] as const;

type ViewId = (typeof VIEWS)[number]["id"];

export const KIND_WORDS: Record<string, string> = {
  PAYMENT_DUE: "Payment due",
  PAYMENT_EXPECTED: "Payment expected",
  PRICE_AGREED: "Agreed price",
  DELIVERY_DUE: "Delivery due",
  DISCOUNT_OFFERED: "Early-payment discount",
  NOTICE_DEADLINE: "Notice deadline",
  DOCUMENT_VALID_UNTIL: "Document expires",
  OBLIGATION: "Obligation",
};

const STATUS_TONES: Record<string, string> = {
  OPEN: "sky",
  PARTIAL: "drifting",
  KEPT: "holding",
  BROKEN: "breached",
  WAIVED: "unknown",
  EXPIRED: "unknown",
};

const STATUS_WORDS: Record<string, string> = {
  OPEN: "Open",
  PARTIAL: "Partly kept",
  KEPT: "Kept",
  BROKEN: "Broken",
  WAIVED: "Waived",
  EXPIRED: "Expired",
};

/** Never a guess presented as fact (brief §2): each promise says how sure Helm is of it. */
const CONFIDENCE_WORDS: Record<string, string> = {
  VERIFIED: "checked by a person",
  CALIBRATED: "verified automatically",
  EXTRACTED: "read from the document, unchecked",
};

const PromiseRow: React.FC<{ readonly promise: HelmPromise; readonly slugs: Map<string, string> }> = ({ promise, slugs }) => {
  const { orgSlug } = useHelm();
  const evidence = promise.evidence.filter((e) => typeof e.work_item_id === "string");
  const workspaceSlug = slugs.get(promise.workspace_id);
  return (
    <tr className="align-top" data-testid="helm-promise">
      <td>
        <span className="block font-medium">{promise.title}</span>
        <span className="helm-muted block text-xs">
          {KIND_WORDS[promise.kind] ?? promise.kind}
          {promise.counterparty_name ? ` · ${promise.counterparty_name}` : ""}
          {promise.readable ? ` · ${CONFIDENCE_WORDS[promise.confidence] ?? promise.confidence.toLowerCase()}` : ""}
        </span>
        {promise.ask_access ? (
          <span className="helm-muted mt-1 inline-flex items-center gap-1 text-xs">
            <Lock className="h-3 w-3" aria-hidden="true" />
            In {promise.workspace_name ?? "a workspace"} you cannot read. Ask a workspace admin for access.
          </span>
        ) : null}
      </td>
      <td>
        <span className={`helm-chip helm-state-${STATUS_TONES[promise.status] ?? "unknown"} px-2 text-xs`}>
          {STATUS_WORDS[promise.status] ?? promise.status}
        </span>
      </td>
      <td className="whitespace-nowrap text-xs">{promise.due_date ? formatCalendarDate(promise.due_date) : "—"}</td>
      <td className="text-right font-semibold">
        {promise.amount_micros !== null ? <Money micros={promise.amount_micros} currency={promise.currency} /> : "—"}
      </td>
      <td className="text-xs">
        {workspaceSlug && evidence.length > 0 ? (
          <ul className="space-y-0.5">
            {evidence.slice(0, 3).map((item, index) => (
              <li key={`${String(item.work_item_id)}-${index}`}>
                <Link
                  to={workItemDetailsPath(orgSlug, workspaceSlug, String(item.work_item_id))}
                  className="helm-accent inline-flex items-center gap-1 hover:underline"
                >
                  <FileText className="h-3 w-3" aria-hidden="true" />
                  <span className="max-w-[12rem] truncate">{String(item.document ?? "Document")}</span>
                </Link>
              </li>
            ))}
          </ul>
        ) : (
          <span className="helm-muted">—</span>
        )}
      </td>
    </tr>
  );
};

export const HelmLedger: React.FC = () => {
  const { organizationId, overview } = useHelm();
  const book = useHelmBook();
  const [params, setParams] = useSearchParams();
  // The cockpit's Promise calendar opens one day here: ?view=owe|owed&due=YYYY-MM-DD.
  const asked = params.get("view");
  const [view, setView] = useState<ViewId>(VIEWS.some((v) => v.id === asked) ? (asked as ViewId) : "owe");
  const [counterparty, setCounterparty] = useState("");
  const dueParam = params.get("due");
  const due = dueParam && /^\d{4}-\d{2}-\d{2}$/.test(dueParam) ? dueParam : undefined;
  const filter = VIEWS.find((v) => v.id === view)?.filter ?? {};
  const filters = { ...filter, counterparty: counterparty || undefined, due_on: due };
  const clearDay = (): void =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.delete("due");
      return next;
    });
  const ledger = useQuery({
    queryKey: helmKeys.ledger(organizationId, book.id, filters as Record<string, string | boolean | undefined>),
    queryFn: () => getHelmLedger(organizationId, book.id, filters),
  });
  // The Promise calendar on top: the next 12 weeks of what we owe or are owed; a day filters the table.
  const calendar = useQuery({
    queryKey: helmKeys.calendar(organizationId, book.id),
    queryFn: () => getHelmCalendar(organizationId, book.id),
    staleTime: 60_000,
  });
  const heatDirection = view === "owed" ? "OWED_TO_US" : "WE_OWE";
  const heatCurrency = useMemo(() => {
    const totals = new Map<string, number>();
    for (const e of calendar.data?.entries ?? []) {
      totals.set(e.currency, (totals.get(e.currency) ?? 0) + e.amount_micros);
    }
    return [...totals.entries()].sort((a, b) => b[1] - a[1])[0]?.[0] ?? "INR";
  }, [calendar.data]);
  const heat = useMemo(() => {
    const out = new Map<string, HeatDay>();
    for (const e of calendar.data?.entries ?? []) {
      if (e.direction === heatDirection && e.currency === heatCurrency) {
        out.set(e.on, { value: e.amount_micros, words: `${formatHelmMoney(e.amount_micros, e.currency)} in ${e.count} ${e.count === 1 ? "promise" : "promises"}` });
      }
    }
    return out;
  }, [calendar.data, heatDirection, heatCurrency]);
  const openDay = (iso: string): void =>
    setParams((current) => {
      const next = new URLSearchParams(current);
      next.set("due", iso);
      if (view === "owe" || view === "owed") {
        next.set("view", view);
      }
      return next;
    });
  const slugs = useMemo(() => new Map(overview.workspaces.map((w) => [w.id, w.slug])), [overview.workspaces]);
  const counterparties = useMemo(() => {
    const seen = new Map<string, string>();
    for (const promise of ledger.data?.promises ?? []) {
      if (promise.counterparty_key && promise.counterparty_name) {
        seen.set(promise.counterparty_key, promise.counterparty_name);
      }
    }
    return [...seen.entries()].sort((a, b) => a[1].localeCompare(b[1]));
  }, [ledger.data]);

  return (
    <div className="mx-auto max-w-[1400px] space-y-5">
      <HelmPageHeader
        eyebrow={book.name}
        title="Promise ledger"
        description="Every promise your documents make: what we owe, what is owed to us, prices agreed, deliveries, notice periods and expiring documents. Each comes from a document you can open."
      />
      <div className="grid gap-4 lg:grid-cols-12">
        <div className="grid gap-4 sm:grid-cols-2 lg:col-span-5 lg:grid-cols-1">
          <div className="helm-panel helm-enter p-5" style={{ ["--i" as string]: 1 }}>
            <p className="helm-label">We owe (open)</p>
            <div className="helm-numeral mt-2 text-[1.75rem]">
              {ledger.data ? <MoneyByCurrency totals={ledger.data.open_totals.WE_OWE} empty="Nothing open" /> : "…"}
            </div>
          </div>
          <div className="helm-panel helm-enter p-5" style={{ ["--i" as string]: 2 }}>
            <p className="helm-label">Owed to us (open)</p>
            <div className="helm-numeral mt-2 text-[1.75rem]">
              {ledger.data ? <MoneyByCurrency totals={ledger.data.open_totals.OWED_TO_US} empty="Nothing open" /> : "…"}
            </div>
          </div>
        </div>
        <section aria-labelledby="helm-ledger-calendar" className="helm-panel helm-enter min-w-0 p-5 lg:col-span-7" style={{ ["--i" as string]: 3 }}>
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
            <h2 id="helm-ledger-calendar" className="helm-label">
              Promise calendar · {view === "owed" ? "owed to us" : "we owe"}
            </h2>
            <span className="helm-faint text-xs">Choose a day to see only its promises</span>
          </div>
          <CalendarHeatmap
            key={heatDirection}
            today={calendar.data?.start ?? new Date().toISOString().slice(0, 10)}
            weeks={12}
            days={heat}
            direction={view === "owed" ? "owed" : "owe"}
            label={`Promise calendar: ${view === "owed" ? "what is owed to us" : "what we owe"} in ${heatCurrency}, the next 12 weeks`}
            summary={`${heat.size} days in the next 12 weeks have ${view === "owed" ? "money owed to us" : "money we owe"} falling due.`}
            onOpen={openDay}
            testId="helm-ledger-heat"
          />
        </section>
      </div>
      <div className="flex flex-wrap items-end gap-3">
        <div role="radiogroup" aria-label="Show" className="flex flex-wrap gap-1.5">
          {VIEWS.map((option) => (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={view === option.id}
              className={`helm-chip ${view === option.id ? "helm-chip-accent" : ""}`}
              onClick={() => setView(option.id)}
            >
              {option.label}
            </button>
          ))}
        </div>
        {due ? (
          <span className="helm-chip helm-chip-accent gap-1.5 px-3 py-1 text-[13px]" data-testid="helm-ledger-day">
            <CalendarDays className="h-3.5 w-3.5" aria-hidden="true" />
            Due on {formatCalendarDate(due)}
            <button type="button" className="-mr-1 rounded-full p-0.5" onClick={clearDay} aria-label="Show every day">
              <X className="h-3.5 w-3.5" aria-hidden="true" />
            </button>
          </span>
        ) : null}
        <label className="text-xs">
          <span className="helm-label">Counterparty</span>
          <select className="helm-input mt-1 h-8 min-h-0 w-56 py-0 text-xs" value={counterparty} onChange={(e) => setCounterparty(e.target.value)}>
            <option value="">All</option>
            {counterparties.map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
      </div>
      {ledger.isError ? (
        <LoadFailed what="the ledger" onRetry={() => void ledger.refetch()} />
      ) : ledger.isLoading ? (
        <HelmSkeleton label="Loading…" />
      ) : (
        <div className="helm-panel max-h-[70vh] overflow-auto p-2">
          <table className="helm-table w-full min-w-[720px] text-sm">
            <caption className="helm-muted px-3 py-2 text-left text-xs">
              {ledger.data?.total ?? 0} promises{ledger.data && ledger.data.total > (ledger.data.promises.length ?? 0) ? `, the first ${ledger.data.promises.length} shown` : ""}
            </caption>
            <thead>
              <tr>
                <th scope="col">Promise</th>
                <th scope="col">Status</th>
                <th scope="col">Due</th>
                <th scope="col" className="text-right">Amount</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {(ledger.data?.promises ?? []).map((promise) => (
                <PromiseRow key={promise.id} promise={promise} slugs={slugs} />
              ))}
            </tbody>
          </table>
          {(ledger.data?.promises ?? []).length === 0 ? <p className="helm-muted px-3 py-4 text-sm">Nothing here.</p> : null}
        </div>
      )}
    </div>
  );
};

export default HelmLedger;
