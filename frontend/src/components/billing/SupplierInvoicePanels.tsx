import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, History, Loader2, Upload } from "lucide-react";

import {
  createSupplierInvoice,
  listInvoiceReconciliations,
  toMicros,
} from "@/services/api/cogs";
import { cogsKeys } from "@/services/api/queryKeys";
import StatusPill from "@/components/ui/StatusPill";
import { OVERLAY } from "@/components/ui/primitives";
import {
  formatMicros,
  formatRatio,
  formatSignedMicros,
  UNKNOWN_LABEL,
  type CostBasisMethod,
  type SupplierInvoice,
} from "@/types/cogs";

/**
 * ARCH-29 Slice 2 — supplier invoice ingest and reconciliation history.
 *
 * TWO THINGS THE MARGINS HUB COULD NOT DO
 * =======================================
 *
 * 1. Create a supplier invoice. `listSupplierInvoices` was wired, so the table
 *    could only ever show rows that arrived by statement pull. An operator
 *    holding a PDF from a vendor had no way in, which is precisely the case
 *    ARCH-24 calls authoritative: the provider invoice is the truth about
 *    money.
 *
 * 2. See more than the LATEST reconciliation. `SupplierInvoice.latest_reconciliation`
 *    is a single embedded record and that is all the table rendered. An invoice
 *    reconciled three times — say, re-run after a rate card correction — showed
 *    only the last attempt. For financial close the history IS the argument:
 *    it is how you show why the accepted variance is the one you accepted.
 *
 * COST-TRUTH CONVENTIONS ARE INHERITED, NOT REINVENTED
 * ====================================================
 *
 * The history table reuses the exact display rules `InvoiceRow` already
 * enforces, because a second surface showing the same numbers under different
 * rules is worse than no second surface:
 *
 *   - `variance_ratio === null` renders UNKNOWN_LABEL, never 0.0%. A period
 *     with nothing modelled has an undefined ratio, not a perfect one.
 *   - `is_authoritative_cost === false` is called out in amber. An
 *     ARCH14_SELL_SIDE denominator is customer-price denominated and inflated
 *     by gross margin; reading it as COGS variance is the ARCH-18 G2 error
 *     wearing a different hat.
 *   - `unknown_cost_event_count > 0` is stated in full. Events with no cost
 *     basis mean the modelled total is incomplete, so the variance computed
 *     from it is a floor, not a figure.
 */

/**
 * Mirrors the map in AdminMarginsHub. Kept exhaustive over `CostBasisMethod`
 * rather than partial with a fallback: a Record over the union means adding a
 * fourth method breaks the compile here instead of rendering `undefined` next
 * to a variance figure.
 */
const COST_BASIS_LABEL: Record<CostBasisMethod, string> = {
  ARCH18_SUPPLIER_COST: "supplier cost",
  ARCH18_PRE_CONSOLIDATION: "supplier cost (pre-ARCH-24)",
  ARCH14_SELL_SIDE: "customer price",
};

function detailOf(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })
    ?.response?.data?.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (Array.isArray(detail) && detail[0]?.msg) {
    return String(detail[0].msg);
  }
  return fallback;
}

const formatDateTime = (value: string): string =>
  new Date(value).toLocaleString();

/* ---------------------------------------------------------------------- */
/* Reconciliation history                                                  */
/* ---------------------------------------------------------------------- */

export const ReconciliationHistory: React.FC<{
  readonly invoice: SupplierInvoice;
}> = ({ invoice }) => {
  const history = useQuery({
    queryKey: cogsKeys.reconciliations(invoice.id),
    queryFn: () => listInvoiceReconciliations(invoice.id),
  });

  if (history.isLoading) {
    return (
      <p className="flex items-center gap-2 px-4 py-3 text-xs text-muted-foreground">
        <Loader2 className="h-3 w-3 animate-spin" aria-hidden />
        Loading reconciliation history…
      </p>
    );
  }

  if (history.isError) {
    return (
      <p className="px-4 py-3 text-xs text-destructive" role="alert">
        {detailOf(history.error, "The reconciliation history failed to load.")}
      </p>
    );
  }

  const runs = history.data ?? [];

  if (runs.length === 0) {
    return (
      <p className="px-4 py-3 text-xs text-muted-foreground">
        This invoice has not been reconciled yet.
      </p>
    );
  }

  return (
    <div className="px-4 py-3">
      <p className="flex items-center gap-1.5 text-xs font-semibold text-foreground">
        <History className="h-3 w-3" aria-hidden />
        {runs.length === 1
          ? "1 reconciliation run"
          : `${runs.length} reconciliation runs`}
      </p>
      <table className="mt-2 w-full text-left text-xs">
        <thead className="text-muted-foreground">
          <tr>
            <th className="py-1 pr-3 font-medium">Run at</th>
            <th className="py-1 pr-3 text-right font-medium">Modelled</th>
            <th className="py-1 pr-3 text-right font-medium">Variance</th>
            <th className="py-1 pr-3 font-medium">Status</th>
            <th className="py-1 font-medium">Basis</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((run, index) => (
            <tr
              key={run.id}
              className="border-t border-border/60 align-top"
            >
              <td className="py-1.5 pr-3 whitespace-nowrap">
                {formatDateTime(run.reconciled_at)}
                {index === 0 ? (
                  <span className="ml-1.5 rounded border border-border px-1 py-0.5 text-[10px] text-muted-foreground">
                    latest
                  </span>
                ) : null}
              </td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {formatMicros(run.modelled_total_micros, invoice.currency)}
                <div className="text-[11px] text-muted-foreground">
                  {run.modelled_event_count.toLocaleString()} events
                </div>
              </td>
              <td className="py-1.5 pr-3 text-right tabular-nums">
                {formatSignedMicros(run.variance_micros, invoice.currency)}
                <div className="text-[11px] text-muted-foreground">
                  {run.variance_ratio === null ? (
                    <span title="Nothing was modelled for this period, so a ratio is undefined rather than zero.">
                      ratio {UNKNOWN_LABEL}
                    </span>
                  ) : (
                    formatRatio(run.variance_ratio)
                  )}
                </div>
              </td>
              <td className="py-1.5 pr-3">
                <StatusPill status={run.status} />
                {run.note ? (
                  <div className="mt-0.5 max-w-[22ch] text-[11px] text-muted-foreground">
                    {run.note}
                  </div>
                ) : null}
              </td>
              <td className="py-1.5">
                {run.is_authoritative_cost ? (
                  <span
                    className="text-[11px] text-muted-foreground"
                    title="Variance computed against genuine supplier cost."
                  >
                    {COST_BASIS_LABEL[run.cost_basis_method]}
                  </span>
                ) : (
                  <span
                    role="note"
                    className="text-[11px] font-medium text-amber-700"
                    title="Denominated in customer price, not supplier cost. Do not read as COGS."
                  >
                    not cost-authoritative ·{" "}
                    {COST_BASIS_LABEL[run.cost_basis_method]}
                  </span>
                )}
                {run.unknown_cost_event_count > 0 ? (
                  <div className="mt-0.5 text-[11px] text-destructive">
                    {run.unknown_cost_event_count.toLocaleString()} events had
                    no cost basis
                  </div>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/* ---------------------------------------------------------------------- */
/* Upload supplier invoice                                                 */
/* ---------------------------------------------------------------------- */

/** Suggestions only. `provider` is a free string on both sides of the wire. */
const PROVIDER_SUGGESTIONS: readonly string[] = [
  "openai",
  "anthropic",
  "groq",
  "gemini",
  "azure_openai",
  "mistral",
  "bedrock",
];

const UUID_PATTERN =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export const SupplierInvoiceModal: React.FC<{
  readonly onClose: () => void;
  readonly onCreated: () => void;
}> = ({ onClose, onCreated }) => {
  const queryClient = useQueryClient();

  const [provider, setProvider] = useState("");
  const [periodStart, setPeriodStart] = useState("");
  const [periodEnd, setPeriodEnd] = useState("");
  const [amount, setAmount] = useState("");
  const [currency, setCurrency] = useState("USD");
  const [reference, setReference] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [notes, setNotes] = useState("");
  const [error, setError] = useState<string | null>(null);

  const micros = amount.trim() ? toMicros(amount.trim()) : null;

  const documentIdInvalid =
    documentId.trim().length > 0 && !UUID_PATTERN.test(documentId.trim());

  const periodInverted =
    periodStart.length > 0 &&
    periodEnd.length > 0 &&
    periodEnd < periodStart;

  const create = useMutation({
    mutationFn: () =>
      createSupplierInvoice({
        provider: provider.trim(),
        period_start: periodStart,
        period_end: periodEnd,
        invoiced_total_micros: micros ?? 0,
        currency: currency.trim().toUpperCase(),
        invoice_reference: reference.trim() || null,
        notes: notes.trim() || null,
        raw_document_file_id: documentId.trim() || null,
      }),
    onSuccess: () => {
      setError(null);
      void queryClient.invalidateQueries({ queryKey: cogsKeys.all() });
      onCreated();
      onClose();
    },
    onError: (err) =>
      setError(
        detailOf(
          err,
          "The invoice was refused. Check the provider name and the period dates.",
        ),
      ),
  });

  const ready =
    provider.trim().length > 0 &&
    periodStart.length > 0 &&
    periodEnd.length > 0 &&
    !periodInverted &&
    micros !== null &&
    Number.isFinite(micros) &&
    micros >= 0 &&
    currency.trim().length === 3 &&
    !documentIdInvalid;

  const field =
    "mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm";
  const label = "text-sm font-medium text-foreground";
  const hint = "mt-1 text-xs text-muted-foreground";

  return (
    <div
      className={OVERLAY}
      role="dialog"
      aria-modal="true"
      aria-labelledby="supplier-invoice-title"
    >
      <div className="my-8 w-full max-w-xl rounded-lg border border-border bg-card p-5 shadow-lg">
        <div className="flex items-start gap-3">
          <Upload
            className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h3
              id="supplier-invoice-title"
              className="text-base font-semibold text-foreground"
            >
              Record a supplier invoice
            </h3>
            <p className="mt-0.5 text-xs text-muted-foreground">
              What the provider actually billed. This is the authoritative
              figure — reconciliation measures our modelled cost against it,
              not the other way round.
            </p>
          </div>
        </div>

        <div className="mt-4 grid gap-3 sm:grid-cols-2">
          <div className="sm:col-span-2">
            <label className={label} htmlFor="invoice-provider">
              Provider
            </label>
            <input
              id="invoice-provider"
              className={field}
              list="invoice-provider-options"
              value={provider}
              maxLength={32}
              onChange={(event) => setProvider(event.target.value)}
              placeholder="openai"
            />
            <datalist id="invoice-provider-options">
              {PROVIDER_SUGGESTIONS.map((name) => (
                <option key={name} value={name} />
              ))}
            </datalist>
            <p className={hint}>
              Must match the provider key used in usage records, or the
              reconciliation will find nothing to compare against.
            </p>
          </div>

          <div>
            <label className={label} htmlFor="invoice-period-start">
              Period start
            </label>
            <input
              id="invoice-period-start"
              type="date"
              className={field}
              value={periodStart}
              onChange={(event) => setPeriodStart(event.target.value)}
            />
          </div>

          <div>
            <label className={label} htmlFor="invoice-period-end">
              Period end
            </label>
            <input
              id="invoice-period-end"
              type="date"
              className={field}
              value={periodEnd}
              onChange={(event) => setPeriodEnd(event.target.value)}
            />
            {/*
              The margin endpoints take exclusive upper bounds; this one is
              inclusive, because that is how a supplier writes an invoice. Two
              conventions in one feature is a real cost, and the alternative —
              an operator mentally transcribing "31 Jul" to "1 Aug" every
              month — is a transcription error against a financial figure.
            */}
            <p className={hint}>
              Inclusive. For a July invoice, enter 31 July.
            </p>
            {periodInverted ? (
              <p className="mt-1 text-xs text-destructive">
                The period ends before it starts.
              </p>
            ) : null}
          </div>

          <div>
            <label className={label} htmlFor="invoice-amount">
              Invoiced total
            </label>
            <input
              id="invoice-amount"
              className={field}
              inputMode="decimal"
              value={amount}
              onChange={(event) => setAmount(event.target.value)}
              placeholder="4213.67"
            />
            <p className={hint}>
              {micros !== null && Number.isFinite(micros)
                ? `${micros.toLocaleString()} micros`
                : "Currency units, as printed on the invoice."}
            </p>
          </div>

          <div>
            <label className={label} htmlFor="invoice-currency">
              Currency
            </label>
            <input
              id="invoice-currency"
              className={`${field} font-mono uppercase`}
              value={currency}
              maxLength={3}
              onChange={(event) => setCurrency(event.target.value)}
            />
            <p className={hint}>Three-letter code.</p>
          </div>

          <div className="sm:col-span-2">
            <label className={label} htmlFor="invoice-reference">
              Invoice reference <span className="font-normal">(optional)</span>
            </label>
            <input
              id="invoice-reference"
              className={field}
              value={reference}
              maxLength={200}
              onChange={(event) => setReference(event.target.value)}
              placeholder="INV-2026-0731"
            />
          </div>

          <div className="sm:col-span-2">
            <label className={label} htmlFor="invoice-document">
              Stored document ID <span className="font-normal">(optional)</span>
            </label>
            <input
              id="invoice-document"
              className={`${field} font-mono`}
              value={documentId}
              onChange={(event) => setDocumentId(event.target.value)}
              placeholder="00000000-0000-0000-0000-000000000000"
            />
            {/*
              Platform-scoped uploads only. The FK is RESTRICT, so pointing at
              a tenant's file would pin that document against the tenant's own
              deletion — an erasure request would then fail on a row they
              cannot see and do not own.
            */}
            <p className={hint}>
              A platform-scoped upload. A tenant&rsquo;s file is refused: the
              reference would block that tenant from ever deleting their own
              document.
            </p>
            {documentIdInvalid ? (
              <p className="mt-1 text-xs text-destructive">
                Not a valid UUID.
              </p>
            ) : null}
          </div>

          <div className="sm:col-span-2">
            <label className={label} htmlFor="invoice-notes">
              Notes <span className="font-normal">(optional)</span>
            </label>
            <textarea
              id="invoice-notes"
              className={field}
              rows={2}
              maxLength={1000}
              value={notes}
              onChange={(event) => setNotes(event.target.value)}
              placeholder="Credit applied for the March outage."
            />
          </div>
        </div>

        {error ? (
          <p className="mt-3 text-xs text-destructive" role="alert">
            {error}
          </p>
        ) : null}

        <div className="mt-5 flex items-center justify-between gap-2">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <FileText className="h-3 w-3" aria-hidden />
            Recorded as an operator upload, not a statement pull.
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              className="rounded-md border border-border px-3 py-1.5 text-sm"
              onClick={onClose}
              disabled={create.isPending}
            >
              Cancel
            </button>
            <button
              type="button"
              className="inline-flex items-center gap-2 rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-foreground disabled:opacity-50"
              disabled={!ready || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              ) : null}
              Record invoice
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SupplierInvoiceModal;
