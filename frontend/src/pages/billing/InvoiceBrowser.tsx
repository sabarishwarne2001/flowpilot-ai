import { formatNumber, formatTimestampDate } from "@/utils/displayTime";
import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  CheckCircle2,
  ChevronRight,
  Download,
  Loader2,
  ShieldAlert,
  ShieldCheck,
} from "lucide-react";

import {
  downloadInvoice,
  getInvoice,
  getInvoiceReproduction,
  getInvoices,
} from "@/services/api/billing";
import { billingKeys } from "@/services/api/queryKeys";
import { formatMicros, invoiceStatusLabel, parseQuantity } from "@/types/billing";
import { ErrorState } from "@/components/common/ErrorState";
import { errorMessage } from "@/services/api/errors";

export interface InvoiceBrowserProps {
  readonly organizationId: string;
}

export const InvoiceBrowser: React.FC<InvoiceBrowserProps> = ({
  organizationId,
}) => {
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const listQuery = useQuery({
    queryKey: billingKeys.invoices(organizationId),
    queryFn: () => getInvoices(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 5 * 60 * 1000,
  });

  // HARDENING-T1:D26. A failed request rendered as a blank or permanent spinner.
  if (listQuery.isError) {
    return (
      <ErrorState
        title="Invoices could not be loaded"
        description={errorMessage(listQuery.error, "The server did not return invoices. Check your connection and try again.")}
        onRetry={() => void listQuery.refetch()}
      />
    );
  }

  if (listQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 p-4 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading invoices…
      </div>
    );
  }

  const invoices = listQuery.data?.invoices ?? [];

  if (invoices.length === 0) {
    return (
      <section className="rounded-lg border border-border bg-card p-4">
        <h2 className="text-sm font-medium">Invoices</h2>
        <p className="mt-1 text-sm text-muted-foreground">
          No invoices yet. Each payment&apos;s invoice appears here once it is
          paid.
        </p>
      </section>
    );
  }

  return (
    <section>
      <h2 className="text-sm font-medium">Invoices</h2>

      <ul className="mt-2 divide-y divide-border rounded-lg border border-border bg-card">
        {invoices.map((invoice) => (
          <li key={invoice.id}>
            <button
              type="button"
              onClick={() =>
                setSelectedId((current) =>
                  current === invoice.id ? null : invoice.id,
                )
              }
              aria-expanded={selectedId === invoice.id}
              className="flex w-full items-center gap-3 px-4 py-3 text-left hover:bg-muted/50"
            >
              <ChevronRight
                className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${
                  selectedId === invoice.id ? "rotate-90" : ""
                }`}
                aria-hidden="true"
              />

              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">
                  {invoice.number}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {formatTimestampDate(invoice.period_start)} –{" "}
                  {formatTimestampDate(invoice.period_end)} ·{" "}
                  {invoice.seats_billed}{" "}
                  {invoice.seats_billed === 1 ? "seat" : "seats"}
                </span>
              </span>

              <span className="shrink-0 text-right">
                <span className="block text-sm font-medium tabular-nums">
                  {formatMicros(invoice.total_micros, invoice.currency)}
                </span>
                <span className="block text-xs text-muted-foreground">
                  {invoiceStatusLabel(invoice)}
                  {(invoice.amount_refunded_micros ?? 0) > 0 &&
                  (invoice.amount_refunded_micros ?? 0) < invoice.amount_paid_micros
                    ? ` · ${formatMicros(invoice.amount_refunded_micros ?? 0, invoice.currency)} refunded`
                    : ""}
                </span>
              </span>
            </button>

            {/* Website and payments session (C4): the gateway's tax invoice, and who issued it. */}
            {invoice.downloadable && <InvoiceDocument organizationId={organizationId} invoice={invoice} />}

            {selectedId === invoice.id && (
              <InvoiceDetail
                organizationId={organizationId}
                invoiceId={invoice.id}
              />
            )}
          </li>
        ))}
      </ul>
    </section>
  );
};

const InvoiceDocument: React.FC<{
  readonly organizationId: string;
  readonly invoice: { readonly id: string; readonly number: string; readonly issued_by?: string | null };
}> = ({ organizationId, invoice }) => {
  const [state, setState] = useState<"idle" | "busy" | "failed">("idle");
  const download = async () => {
    setState("busy");
    try {
      await downloadInvoice(organizationId, invoice.id, invoice.number);
      setState("idle");
    } catch {
      setState("failed");
    }
  };
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 pb-3 pl-11 text-xs text-muted-foreground">
      <button
        type="button"
        onClick={() => void download()}
        disabled={state === "busy"}
        className="fp-btn fp-btn-secondary h-7 px-2.5 text-xs"
      >
        {state === "busy" ? <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" /> : <Download className="h-3.5 w-3.5" aria-hidden="true" />}
        Download invoice
      </button>
      {invoice.issued_by && <span>Tax invoice issued by {invoice.issued_by}.</span>}
      {state === "failed" && (
        <span role="alert" className="text-destructive">
          The invoice could not be downloaded. Try again in a minute.
        </span>
      )}
    </div>
  );
};

interface InvoiceDetailProps {
  readonly organizationId: string;
  readonly invoiceId: string;
}

const InvoiceDetail: React.FC<InvoiceDetailProps> = ({
  organizationId,
  invoiceId,
}) => {
  const [reproducing, setReproducing] = useState(false);

  const detailQuery = useQuery({
    queryKey: billingKeys.invoice(organizationId, invoiceId),
    queryFn: () => getInvoice(organizationId, invoiceId),
    staleTime: 5 * 60 * 1000,
  });

  const reproductionQuery = useQuery({
    queryKey: billingKeys.invoiceReproduction(organizationId, invoiceId),
    queryFn: () => getInvoiceReproduction(organizationId, invoiceId),
    enabled: reproducing,
    staleTime: 5 * 60 * 1000,
  });

  if (detailQuery.isLoading) {
    return (
      <div className="border-t border-border px-4 py-3 text-sm text-muted-foreground">
        Loading invoice…
      </div>
    );
  }

  const detail = detailQuery.data;
  if (!detail) {
    return (
      <div className="border-t border-border px-4 py-3 text-sm text-destructive">
        This invoice couldn&apos;t be loaded.
      </div>
    );
  }

  const { invoice, line_items: lines } = detail;

  return (
    <div className="border-t border-border bg-muted/20 px-4 py-3">
      {detail.digest_matches ? (
        <div className="flex items-start gap-2">
          <ShieldCheck
            className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-xs font-medium">
              Integrity verified by the server
            </p>
            <p className="mt-0.5 break-all font-mono text-[11px] text-muted-foreground">
              {detail.content_digest}
            </p>
          </div>
        </div>
      ) : (
        <div
          role="alert"
          className="rounded-md border border-destructive/50 bg-destructive/10 p-3"
        >
          <p className="flex items-center gap-2 text-sm font-semibold text-destructive">
            <ShieldAlert className="h-4 w-4" aria-hidden="true" />
            This invoice does not match its stored digest
          </p>
          <p className="mt-1 text-xs text-foreground/80">
            A finalised invoice should never change. Please contact support and
            quote this invoice number and the digest below before paying.
          </p>
          <p className="mt-1.5 break-all font-mono text-[11px]">
            {detail.content_digest}
          </p>
        </div>
      )}

      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-xs text-muted-foreground">
              <th scope="col" className="py-1.5 pr-3 font-medium">
                Description
              </th>
              <th scope="col" className="py-1.5 pr-3 font-medium">
                Qty
              </th>
              <th scope="col" className="py-1.5 text-right font-medium">
                Amount
              </th>
            </tr>
          </thead>
          <tbody>
            {lines.map((line) => (
              <tr key={line.line_number} className="border-b border-border/40">
                <td className="py-1.5 pr-3">
                  {line.description}
                  {line.estimated_quantity !== null &&
                    parseQuantity(line.estimated_quantity) > 0 && (
                      <span
                        className="ml-1.5 text-xs text-muted-foreground"
                        title="Part of this quantity was estimated because the provider returned no counts."
                      >
                        (partly estimated)
                      </span>
                    )}
                </td>
                <td className="py-1.5 pr-3 tabular-nums text-muted-foreground">
                  {formatNumber(parseQuantity(line.quantity))} {line.unit}
                </td>
                <td className="py-1.5 text-right tabular-nums">
                  {formatMicros(line.amount_micros, invoice.currency)}
                </td>
              </tr>
            ))}
          </tbody>
          <tfoot>
            <tr>
              <td colSpan={2} className="py-1.5 pr-3 text-right text-xs text-muted-foreground">
                Subtotal
              </td>
              <td className="py-1.5 text-right tabular-nums">
                {formatMicros(invoice.subtotal_micros, invoice.currency)}
              </td>
            </tr>
            <tr>
              <td colSpan={2} className="py-1.5 pr-3 text-right text-xs text-muted-foreground">
                Tax
              </td>
              <td className="py-1.5 text-right tabular-nums">
                {formatMicros(invoice.tax_micros, invoice.currency)}
              </td>
            </tr>
            <tr className="border-t border-border">
              <td colSpan={2} className="py-1.5 pr-3 text-right text-sm font-medium">
                Total
              </td>
              <td className="py-1.5 text-right text-sm font-semibold tabular-nums">
                {formatMicros(invoice.total_micros, invoice.currency)}
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="mt-3">
        {!reproducing ? (
          <button
            type="button"
            onClick={() => setReproducing(true)}
            className="rounded-md border border-border bg-background px-3 py-1.5 text-xs hover:bg-muted"
          >
            Re-derive this invoice
          </button>
        ) : reproductionQuery.isLoading ? (
          <p className="text-xs text-muted-foreground">Re-deriving…</p>
        ) : reproductionQuery.data ? (
          <div className="rounded-md border border-border bg-background p-3">
            <p className="text-xs font-medium">Reproduction</p>

            <dl className="mt-2 grid gap-1.5 text-[11px]">
              <IntegrityRow
                label="Digest matches"
                ok={reproductionQuery.data.integrity.digest_matches}
              />
              <IntegrityRow
                label="Arithmetic checks out"
                ok={reproductionQuery.data.integrity.arithmetic_ok}
              />
              <IntegrityRow
                label="Fully reproducible"
                ok={reproductionQuery.data.integrity.reproducible}
              />
            </dl>

            <p className="mt-2 text-[11px] text-muted-foreground">
              Priced from price book v
              {reproductionQuery.data.provenance.price_book_version} · tier{" "}
              {reproductionQuery.data.provenance.quota_tier_key} v
              {reproductionQuery.data.provenance.quota_tier_version}
            </p>

            {reproductionQuery.data.integrity.arithmetic_failures.length > 0 && (
              <p role="alert" className="mt-2 text-[11px] text-destructive">
                {reproductionQuery.data.integrity.arithmetic_failures.length}{" "}
                line(s) failed re-computation. Contact support.
              </p>
            )}
          </div>
        ) : (
          <p className="text-xs text-destructive">
            Reproduction couldn&apos;t be completed.
          </p>
        )}
      </div>
    </div>
  );
};

const IntegrityRow: React.FC<{ label: string; ok: boolean }> = ({
  label,
  ok,
}) => (
  <div className="flex items-center gap-1.5">
    {ok ? (
      <CheckCircle2 className="h-3 w-3 text-emerald-600" aria-hidden="true" />
    ) : (
      <ShieldAlert className="h-3 w-3 text-destructive" aria-hidden="true" />
    )}
    <dt className="text-muted-foreground">{label}</dt>
    <dd className={ok ? "" : "font-medium text-destructive"}>
      {ok ? "yes" : "no"}
    </dd>
  </div>
);

export default InvoiceBrowser;
