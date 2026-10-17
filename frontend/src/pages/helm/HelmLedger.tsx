import React, { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarDays, FileText, Lock, X } from "lucide-react";

import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { LoadFailed } from "@/components/common/LoadFailed";
import { workItemDetailsPath } from "@/routes/tenantPaths";
import { getHelmLedger, helmKeys } from "@/services/api/helm";
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
    <tr className="helm-hairline border-t align-top" data-testid="helm-promise">
      <td className="py-2 pr-3">
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
      <td className="py-2 pr-3 text-xs">{STATUS_WORDS[promise.status] ?? promise.status}</td>
      <td className="py-2 pr-3 text-xs">{promise.due_date ? formatCalendarDate(promise.due_date) : "—"}</td>
      <td className="py-2 pr-3 text-right">
        {promise.amount_micros !== null ? <Money micros={promise.amount_micros} currency={promise.currency} /> : "—"}
      </td>
      <td className="py-2 text-xs">
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
    <div className="mx-auto max-w-6xl space-y-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Promise ledger</h1>
        <p className="helm-muted mt-1 text-sm">
          Every promise your documents make: what we owe, what is owed to us, prices agreed, deliveries, notice periods
          and expiring documents. Each comes from a document you can open.
        </p>
      </div>
      {ledger.data ? (
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="helm-panel p-4">
            <p className="helm-label">We owe (open)</p>
            <div className="mt-1 text-lg font-semibold">
              <MoneyByCurrency totals={ledger.data.open_totals.WE_OWE} empty="Nothing open" />
            </div>
          </div>
          <div className="helm-panel p-4">
            <p className="helm-label">Owed to us (open)</p>
            <div className="mt-1 text-lg font-semibold">
              <MoneyByCurrency totals={ledger.data.open_totals.OWED_TO_US} empty="Nothing open" />
            </div>
          </div>
        </div>
      ) : null}
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
        <p className="helm-muted text-sm" role="status">
          Loading…
        </p>
      ) : (
        <div className="helm-panel overflow-x-auto p-4">
          <table className="w-full min-w-[720px] text-sm">
            <caption className="helm-muted pb-2 text-left text-xs">
              {ledger.data?.total ?? 0} promises{ledger.data && ledger.data.total > (ledger.data.promises.length ?? 0) ? `, the first ${ledger.data.promises.length} shown` : ""}
            </caption>
            <thead>
              <tr className="helm-label text-left">
                <th scope="col" className="py-1 pr-3">Promise</th>
                <th scope="col" className="py-1 pr-3">Status</th>
                <th scope="col" className="py-1 pr-3">Due</th>
                <th scope="col" className="py-1 pr-3 text-right">Amount</th>
                <th scope="col" className="py-1">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {(ledger.data?.promises ?? []).map((promise) => (
                <PromiseRow key={promise.id} promise={promise} slugs={slugs} />
              ))}
            </tbody>
          </table>
          {(ledger.data?.promises ?? []).length === 0 ? <p className="helm-muted py-4 text-sm">Nothing here.</p> : null}
        </div>
      )}
    </div>
  );
};

export default HelmLedger;
