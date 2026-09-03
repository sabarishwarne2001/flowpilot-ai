/**
 * ARCH-18 — platform unit economics.
 *
 * The design constraint that shapes every component below: an unknown cost
 * must never render as a number. Not as £0.00, not as 100%, not as a dash that
 * could be mistaken for zero. It renders as the word "unknown", in muted text,
 * and the headline margin is SUPPRESSED entirely when too little of the
 * revenue has a known cost.
 *
 * That last part is the one that will feel wrong the first time someone opens
 * this page and sees no margin at all. It is correct. Until a price book with
 * cost basis has been published and taken effect, the platform genuinely does
 * not know what anything costs, and a large confident percentage would be
 * fiction. The banner says so in words.
 */

import React, { useCallback, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  CheckCircle2,
  FileText,
  HelpCircle,
  Loader2,
  RefreshCw,
  Scale,
  TrendingDown,
} from "lucide-react";

import {
  acceptVariance,
  getMarginSummary,
  getProviderCosts,
  getRateCard,
  getTenantEconomics,
  listSupplierInvoices,
  reconcileSupplierInvoice,
  trailingWindow,
  windowKey,
  type MarginWindow,
} from "@/services/api/cogs";
import { cogsKeys } from "@/services/api/queryKeys";
import {
  confidenceLabel,
  formatMicros,
  formatRatio,
  formatSignedMicros,
  statusTone,
  UNKNOWN_LABEL,
  type CostBasisMethod,
  type MarginFigures,
  type MarginOrder,
  type SupplierInvoice,
  type TenantEconomicsEntry,
} from "@/types/cogs";

const WINDOWS: readonly { readonly days: number; readonly label: string }[] = [
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
];

const ORDERS: readonly { readonly value: MarginOrder; readonly label: string }[] =
  [
    { value: "MARGIN_ASC", label: "Worst margin first" },
    { value: "MARGIN_DESC", label: "Best margin first" },
    { value: "REVENUE_DESC", label: "Largest revenue" },
    { value: "UNKNOWN_DESC", label: "Most unaccounted cost" },
  ];

/* ---------------------------------------------------------------------- */

const Unknown: React.FC<{ readonly hint?: string }> = ({ hint }) => (
  <span
    className="inline-flex items-center gap-1 text-muted-foreground"
    title={hint ?? "No cost basis recorded for this usage."}
  >
    <HelpCircle className="h-3 w-3" aria-hidden />
    {UNKNOWN_LABEL}
  </span>
);

const Stat: React.FC<{
  readonly label: string;
  readonly value: string;
  readonly isUnknown?: boolean;
  readonly caption?: string;
}> = ({ label, value, isUnknown, caption }) => (
  <div className="rounded-lg border border-border p-4">
    <div className="text-xs uppercase tracking-wide text-muted-foreground">
      {label}
    </div>
    <div
      className={`mt-1 text-2xl font-semibold tabular-nums ${
        isUnknown ? "text-muted-foreground" : "text-foreground"
      }`}
    >
      {value}
    </div>
    {caption ? (
      <div className="mt-1 text-xs text-muted-foreground">{caption}</div>
    ) : null}
  </div>
);

/**
 * The banner is not decoration.
 *
 * `is_trustworthy` comes from the backend and the UI never recomputes it —
 * two independent thresholds would eventually disagree and the page would
 * contradict its own warning.
 */
const CoverageBanner: React.FC<{ readonly figures: MarginFigures }> = ({
  figures,
}) => {
  if (figures.event_count === 0) {
    return null;
  }

  if (figures.is_trustworthy) {
    return (
      <div className="flex items-start gap-2 rounded-lg border border-border bg-muted/40 p-3 text-sm">
        <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" aria-hidden />
        <span>{confidenceLabel(figures)}</span>
      </div>
    );
  }

  return (
    <div
      role="status"
      className="flex items-start gap-2 rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" aria-hidden />
      <div>
        <div className="font-medium">
          Gross margin is not being shown for this period.
        </div>
        <p className="mt-1 text-muted-foreground">
          {formatRatio(figures.unknown_cost_share)} of revenue (
          {formatMicros(figures.unknown_cost_revenue_micros)} across{" "}
          {figures.unknown_cost_event_count.toLocaleString()} events) came from
          usage with no recorded supplier cost. A margin computed over the
          remainder would describe a minority of the business. Publish a price
          book version carrying <code>cost_basis_micros</code> to close the gap
          — cost basis cannot be backfilled into an already-published book.
        </p>
      </div>
    </div>
  );
};

const MarginCell: React.FC<{ readonly figures: MarginFigures }> = ({
  figures,
}) => {
  if (figures.gross_margin_ratio === null) {
    return <Unknown hint="No usage in this period carries a cost basis." />;
  }
  const negative = figures.gross_margin_ratio < 0;
  return (
    <span
      className={`inline-flex items-center gap-1 tabular-nums ${
        negative ? "text-destructive" : "text-foreground"
      }`}
    >
      {negative ? <TrendingDown className="h-3 w-3" aria-hidden /> : null}
      {formatRatio(figures.gross_margin_ratio)}
      {!figures.is_trustworthy ? (
        <span
          className="text-muted-foreground"
          title={`Excludes ${formatRatio(
            figures.unknown_cost_share,
          )} of this tenant's revenue`}
        >
          *
        </span>
      ) : null}
    </span>
  );
};

const TenantRow: React.FC<{ readonly entry: TenantEconomicsEntry }> = ({
  entry,
}) => {
  const { figures } = entry;
  return (
    <tr className="border-b border-border last:border-0">
      <td className="py-2 pr-4">
        <div className="font-medium">
          {entry.organization_name ?? entry.organization_slug ?? "—"}
        </div>
        <div className="text-xs text-muted-foreground">
          {entry.organization_slug ?? entry.organization_id}
        </div>
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {formatMicros(figures.revenue_micros)}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {figures.known_cost_event_count === 0 ? (
          <Unknown />
        ) : (
          formatMicros(figures.cost_basis_micros)
        )}
      </td>
      <td className="py-2 pr-4 text-right">
        {figures.gross_margin_micros === null ? (
          <Unknown />
        ) : (
          <span className="tabular-nums">
            {formatMicros(figures.gross_margin_micros)}
          </span>
        )}
      </td>
      <td className="py-2 pr-4 text-right">
        <MarginCell figures={figures} />
      </td>
      <td className="py-2 text-right tabular-nums text-muted-foreground">
        {formatRatio(figures.unknown_cost_share)}
      </td>
    </tr>
  );
};

/**
 * ARCH-24 discriminator labels. Kept as a lookup rather than prettified
 * inline so the three legal values stay visible in one place and an unknown
 * fourth value shows up as undefined instead of being silently formatted.
 */
const COST_BASIS_LABEL: Record<CostBasisMethod, string> = {
  ARCH18_SUPPLIER_COST: "supplier cost",
  ARCH18_PRE_CONSOLIDATION: "supplier cost (pre-ARCH-24)",
  ARCH14_SELL_SIDE: "customer price",
};

const StatusBadge: React.FC<{ readonly status: string }> = ({ status }) => {
  const tone = statusTone(status as never);
  const className =
    tone === "warn"
      ? "border-destructive/50 text-destructive"
      : tone === "ok"
        ? "border-border text-foreground"
        : "border-border text-muted-foreground";
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[11px] ${className}`}>
      {status}
    </span>
  );
};

const InvoiceRow: React.FC<{
  readonly invoice: SupplierInvoice;
  readonly onReconcile: (id: string, force: boolean) => void;
  readonly onAccept: (reconciliationId: string) => void;
  readonly busy: boolean;
}> = ({ invoice, onReconcile, onAccept, busy }) => {
  const latest = invoice.latest_reconciliation;

  return (
    <tr className="border-b border-border last:border-0 align-top">
      <td className="py-2 pr-4">
        <div className="font-medium">{invoice.provider}</div>
        <div className="text-xs text-muted-foreground">
          {invoice.period_start} → {invoice.period_end}
          {invoice.invoice_reference ? ` · ${invoice.invoice_reference}` : ""}
        </div>
        {/*
          ARCH-24 N-3. A statement pull is an API estimate; an operator upload
          is a human holding the actual invoice. A reviewer signing off a
          variance needs to know which one they are looking at.
        */}
        <div className="mt-0.5 text-[11px] text-muted-foreground">
          {invoice.origin === "STATEMENT_PULL"
            ? "from provider statement pull"
            : "operator-supplied invoice"}
          {invoice.superseded_invoice_id
            ? " · supersedes an earlier statement row"
            : ""}
        </div>
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {formatMicros(invoice.invoiced_total_micros, invoice.currency)}
      </td>
      <td className="py-2 pr-4 text-right tabular-nums">
        {latest ? (
          formatMicros(latest.modelled_total_micros, invoice.currency)
        ) : (
          <span className="text-muted-foreground">not reconciled</span>
        )}
      </td>
      <td className="py-2 pr-4 text-right">
        {latest ? (
          <div className="tabular-nums">
            {formatSignedMicros(latest.variance_micros, invoice.currency)}
            <div className="text-xs text-muted-foreground">
              {latest.variance_ratio === null ? (
                <span title="Nothing was modelled for this period, so a ratio is undefined rather than zero.">
                  ratio {UNKNOWN_LABEL}
                </span>
              ) : (
                formatRatio(latest.variance_ratio)
              )}
            </div>
          </div>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="py-2 pr-4">
        {latest ? (
          <div className="space-y-1">
            <StatusBadge status={latest.status} />
            {/*
              ARCH-24. The denominator that produced this variance. An
              ARCH14_SELL_SIDE row is customer-price denominated and inflated
              by gross margin — it is NOT COGS variance, and the backend says
              so via is_authoritative_cost rather than the client inferring it
              from the string.
            */}
            {latest.is_authoritative_cost ? (
              <div
                className="text-[11px] text-muted-foreground"
                title="Variance computed against genuine supplier cost."
              >
                basis: {COST_BASIS_LABEL[latest.cost_basis_method]}
              </div>
            ) : (
              <div
                role="note"
                className="text-[11px] font-medium text-amber-700"
                title="Denominated in customer price, not supplier cost. Do not read as COGS."
              >
                not cost-authoritative ·{" "}
                {COST_BASIS_LABEL[latest.cost_basis_method]}
              </div>
            )}
            {latest.unknown_cost_event_count > 0 ? (
              <div className="text-xs text-destructive">
                {latest.unknown_cost_event_count.toLocaleString()} events in this
                period had no cost basis
              </div>
            ) : null}
            {latest.note ? (
              <div className="text-xs text-muted-foreground">{latest.note}</div>
            ) : null}
          </div>
        ) : null}
      </td>
      <td className="py-2 text-right">
        <div className="flex justify-end gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => onReconcile(invoice.id, false)}
            className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
          >
            Reconcile
          </button>
          {latest && latest.status === "INVESTIGATE" ? (
            <button
              type="button"
              disabled={busy}
              onClick={() => onAccept(latest.id)}
              className="rounded border border-border px-2 py-1 text-xs hover:bg-muted disabled:opacity-50"
            >
              Accept
            </button>
          ) : null}
        </div>
      </td>
    </tr>
  );
};

/* ---------------------------------------------------------------------- */

export const AdminMarginsHub: React.FC = () => {
  const queryClient = useQueryClient();
  const [days, setDays] = useState(30);
  const [order, setOrder] = useState<MarginOrder>("MARGIN_ASC");
  const [error, setError] = useState<string | null>(null);

  const window = useMemo<MarginWindow>(() => trailingWindow(days), [days]);
  const key = useMemo(() => windowKey(window), [window]);

  const summary = useQuery({
    queryKey: cogsKeys.marginSummary(key),
    queryFn: () => getMarginSummary(window),
    staleTime: 60_000,
  });

  const tenants = useQuery({
    queryKey: cogsKeys.tenantEconomics(key, order),
    queryFn: () => getTenantEconomics(window, order, 50),
    staleTime: 60_000,
  });

  const providers = useQuery({
    queryKey: cogsKeys.providerCosts(key),
    queryFn: () => getProviderCosts(window),
    staleTime: 60_000,
  });

  const rateCard = useQuery({
    queryKey: cogsKeys.rateCard(),
    queryFn: getRateCard,
    staleTime: 300_000,
  });

  const invoices = useQuery({
    queryKey: cogsKeys.supplierInvoices(),
    queryFn: () => listSupplierInvoices(),
    staleTime: 60_000,
  });

  const invalidate = useCallback(async () => {
    await queryClient.invalidateQueries({ queryKey: cogsKeys.all() });
  }, [queryClient]);

  const reconcile = useMutation({
    mutationFn: ({ id, force }: { id: string; force: boolean }) =>
      reconcileSupplierInvoice(id, { force }),
    onSuccess: invalidate,
    onError: (err: unknown) => {
      // A 409 here is the period-not-closed refusal, and it is worth
      // surfacing verbatim rather than as "something went wrong": the
      // operator's next action depends on whether they should wait or force.
      const message =
        (err as { response?: { data?: { detail?: string } } })?.response?.data
          ?.detail ?? "Reconciliation failed.";
      setError(message);
    },
  });

  const accept = useMutation({
    mutationFn: ({ id, note }: { id: string; note: string }) =>
      acceptVariance(id, { note }),
    onSuccess: invalidate,
    onError: () => setError("Could not accept the variance."),
  });

  const handleReconcile = useCallback(
    (id: string, force: boolean) => {
      setError(null);
      reconcile.mutate({ id, force });
    },
    [reconcile],
  );

  const handleAccept = useCallback(
    (reconciliationId: string) => {
      setError(null);

      const note = globalThis.prompt(
        "Why is this variance acceptable? A note is required — an accepted variance with no stated reason is indistinguishable from a mistake.",
      );
      if (!note || !note.trim()) {
        return;
      }
      accept.mutate({ id: reconciliationId, note: note.trim() });
    },
    [accept],
  );

  const figures = summary.data?.figures;
  const busy = reconcile.isPending || accept.isPending;

  return (
    <div className="space-y-6 p-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-semibold">
            <Scale className="h-5 w-5" aria-hidden />
            Unit economics
          </h1>
          <p className="mt-1 text-sm text-muted-foreground">
            Revenue against supplier cost across every tenant. Figures exclude
            usage with no recorded cost basis rather than treating it as free.
          </p>
        </div>

        <div className="flex items-center gap-2">
          <label className="sr-only" htmlFor="cogs-window">
            Reporting window
          </label>
          <select
            id="cogs-window"
            value={days}
            onChange={(event) => setDays(Number(event.target.value))}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {WINDOWS.map((option) => (
              <option key={option.days} value={option.days}>
                {option.label}
              </option>
            ))}
          </select>
          <button
            type="button"
            onClick={() => void invalidate()}
            className="inline-flex items-center gap-1 rounded border border-border px-2 py-1 text-sm hover:bg-muted"
          >
            <RefreshCw className="h-3.5 w-3.5" aria-hidden />
            Refresh
          </button>
        </div>
      </header>

      {error ? (
        <div
          role="alert"
          className="rounded-lg border border-destructive/40 bg-destructive/5 p-3 text-sm"
        >
          {error}
        </div>
      ) : null}

      {summary.isLoading ? (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
          Loading platform figures…
        </div>
      ) : null}

      {summary.isError ? (
        <div className="rounded-lg border border-destructive/40 p-3 text-sm">
          Could not load margin figures.
        </div>
      ) : null}

      {figures ? (
        <>
          <CoverageBanner figures={figures} />

          <section className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Stat
              label="Revenue"
              value={formatMicros(figures.revenue_micros)}
              caption={`${figures.event_count.toLocaleString()} metered events`}
            />
            <Stat
              label="Modelled COGS"
              value={
                figures.known_cost_event_count === 0
                  ? UNKNOWN_LABEL
                  : formatMicros(figures.cost_basis_micros)
              }
              isUnknown={figures.known_cost_event_count === 0}
              caption={`${figures.known_cost_event_count.toLocaleString()} events with a cost basis`}
            />
            <Stat
              label="Gross margin"
              value={
                figures.is_trustworthy
                  ? formatMicros(figures.gross_margin_micros)
                  : UNKNOWN_LABEL
              }
              isUnknown={!figures.is_trustworthy}
              caption={
                figures.is_trustworthy
                  ? formatRatio(figures.gross_margin_ratio)
                  : "Suppressed — too little cost is known"
              }
            />
            <Stat
              label="Unaccounted revenue"
              value={formatMicros(figures.unknown_cost_revenue_micros)}
              caption={`${formatRatio(
                figures.unknown_cost_share,
              )} of revenue · ${figures.unknown_cost_event_count.toLocaleString()} events`}
            />
          </section>
        </>
      ) : null}

      {rateCard.data ? (
        <section className="rounded-lg border border-border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <h2 className="text-sm font-semibold">Supplier rate card</h2>
            <div className="text-xs text-muted-foreground">
              {rateCard.data.price_book_version === null
                ? "No price book in force"
                : `Price book v${rateCard.data.price_book_version} · ${
                    rateCard.data.with_cost_basis
                  }/${rateCard.data.entry_count} entries carry a cost basis (${formatRatio(
                    rateCard.data.coverage_ratio,
                  )})`}
            </div>
          </div>

          {rateCard.data.entries.length === 0 ? (
            <p className="mt-3 text-sm text-muted-foreground">
              No published price book covers this moment.
            </p>
          ) : (
            <div className="mt-3 overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                  <tr className="border-b border-border">
                    <th className="pb-2 pr-4 font-medium">Unit</th>
                    <th className="pb-2 pr-4 text-right font-medium">Price µ</th>
                    <th className="pb-2 pr-4 text-right font-medium">Cost µ</th>
                    <th className="pb-2 pr-4 text-right font-medium">Margin µ</th>
                    <th className="pb-2 font-medium">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {rateCard.data.entries.map((entry) => (
                    <tr
                      key={`${entry.provider}:${entry.event_type}:${entry.model ?? "*"}:${entry.tier_key ?? "*"}`}
                      className="border-b border-border last:border-0"
                    >
                      <td className="py-2 pr-4">
                        <div>{entry.event_type}</div>
                        <div className="text-xs text-muted-foreground">
                          {entry.provider}/{entry.model ?? "*"}
                        </div>
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {entry.unit_price_micros}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {entry.cost_basis_micros ?? <Unknown />}
                      </td>
                      <td className="py-2 pr-4 text-right tabular-nums">
                        {entry.unit_margin_micros ?? <Unknown />}
                      </td>
                      <td className="py-2 text-xs text-muted-foreground">
                        {entry.cost_basis_source ?? "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ) : null}

      <section className="rounded-lg border border-border p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">Tenant profitability</h2>
          <select
            aria-label="Ranking order"
            value={order}
            onChange={(event) => setOrder(event.target.value as MarginOrder)}
            className="rounded border border-border bg-background px-2 py-1 text-sm"
          >
            {ORDERS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>

        {tenants.isLoading ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading tenants…
          </div>
        ) : tenants.data && tenants.data.entries.length > 0 ? (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 font-medium">Organization</th>
                  <th className="pb-2 pr-4 text-right font-medium">Revenue</th>
                  <th className="pb-2 pr-4 text-right font-medium">COGS</th>
                  <th className="pb-2 pr-4 text-right font-medium">Margin</th>
                  <th className="pb-2 pr-4 text-right font-medium">Margin %</th>
                  <th className="pb-2 text-right font-medium">Unaccounted</th>
                </tr>
              </thead>
              <tbody>
                {tenants.data.entries.map((entry) => (
                  <TenantRow key={entry.organization_id} entry={entry} />
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-xs text-muted-foreground">
              An asterisk marks a margin computed over a minority of that
              tenant&apos;s revenue. Tenants with no known cost show{" "}
              {UNKNOWN_LABEL} and are never ranked as profitable.
            </p>
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground">
            No metered usage in this period.
          </p>
        )}
      </section>

      {providers.data && providers.data.entries.length > 0 ? (
        <section className="rounded-lg border border-border p-4">
          <h2 className="text-sm font-semibold">Modelled cost by supplier</h2>
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 font-medium">Provider</th>
                  <th className="pb-2 pr-4 text-right font-medium">Revenue</th>
                  <th className="pb-2 pr-4 text-right font-medium">
                    Modelled cost
                  </th>
                  <th className="pb-2 text-right font-medium">Events w/o cost</th>
                </tr>
              </thead>
              <tbody>
                {providers.data.entries.map((entry) => (
                  <tr
                    key={entry.provider ?? "unattributed"}
                    className="border-b border-border last:border-0"
                  >
                    <td className="py-2 pr-4">{entry.provider ?? "—"}</td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {formatMicros(entry.revenue_micros)}
                    </td>
                    <td className="py-2 pr-4 text-right tabular-nums">
                      {formatMicros(entry.cost_basis_micros)}
                    </td>
                    <td
                      className={`py-2 text-right tabular-nums ${
                        entry.unknown_cost_event_count > 0
                          ? "text-destructive"
                          : "text-muted-foreground"
                      }`}
                    >
                      {entry.unknown_cost_event_count.toLocaleString()}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ) : null}

      <section className="rounded-lg border border-border p-4">
        <h2 className="flex items-center gap-2 text-sm font-semibold">
          <FileText className="h-4 w-4" aria-hidden />
          Supplier invoices
        </h2>

        {invoices.isLoading ? (
          <div className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
            Loading invoices…
          </div>
        ) : invoices.data && invoices.data.entries.length > 0 ? (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-left text-xs uppercase tracking-wide text-muted-foreground">
                <tr className="border-b border-border">
                  <th className="pb-2 pr-4 font-medium">Invoice</th>
                  <th className="pb-2 pr-4 text-right font-medium">Invoiced</th>
                  <th className="pb-2 pr-4 text-right font-medium">Modelled</th>
                  <th className="pb-2 pr-4 text-right font-medium">Variance</th>
                  <th className="pb-2 pr-4 font-medium">Status</th>
                  <th className="pb-2 text-right font-medium">Actions</th>
                </tr>
              </thead>
              <tbody>
                {invoices.data.entries.map((invoice) => (
                  <InvoiceRow
                    key={invoice.id}
                    invoice={invoice}
                    busy={busy}
                    onReconcile={handleReconcile}
                    onAccept={handleAccept}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="mt-3 text-sm text-muted-foreground">
            No supplier invoices ingested yet. Without them,{" "}
            <code>cost_basis_micros</code> is an unchecked assertion that decays
            as supplier rates change.
          </p>
        )}
      </section>
    </div>
  );
};

export default AdminMarginsHub;
