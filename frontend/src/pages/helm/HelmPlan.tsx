import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Download, FileCheck2, Lock } from "lucide-react";

import { LoadFailed } from "@/components/common/LoadFailed";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { helmCertificatePath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { getHelmPaymentRun, helmKeys, markHelmInvoicePaid } from "@/services/api/helm";
import { formatCalendarDate } from "@/utils/displayTime";
import type { PaymentRun, PaymentRunLine } from "@/types/helm";

const today = (): string => new Date().toISOString().slice(0, 10);

const STATE_WORDS: Record<string, string> = { SCHEDULED: "Scheduled", APPROVED: "Approved" };

const csvCell = (value: string | number | null | undefined): string => {
  const text = value === null || value === undefined ? "" : String(value);
  // A leading =, +, - or @ is a formula to a spreadsheet; quote it as text.
  const safe = /^[=+\-@]/.test(text) ? `'${text}` : text;
  return /[",\n]/.test(safe) ? `"${safe.replace(/"/g, '""')}"` : safe;
};

const downloadCsv = (run: PaymentRun): void => {
  const rows = [["Run date", "Payee", "Bank account (masked)", "Invoice", "Currency", "Amount", "State"]];
  for (const day of run.days) {
    for (const line of day.lines.filter((l) => l.readable)) {
      rows.push([
        day.run_date,
        line.payee,
        line.bank_account ?? "",
        line.invoice ?? "",
        line.currency ?? "",
        line.amount_micros === null ? "" : (line.amount_micros / 1_000_000).toFixed(2),
        line.state,
      ]);
    }
  }
  const blob = new Blob([rows.map((r) => r.map(csvCell).join(",")).join("\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `payment-run-${today()}.csv`;
  link.click();
  URL.revokeObjectURL(url);
};

export const HelmPlan: React.FC = () => {
  const { organizationId, orgSlug, role, withBook } = useHelm();
  const book = useHelmBook();
  const queryClient = useQueryClient();
  const run = useQuery({
    queryKey: helmKeys.paymentRun(organizationId, book.id),
    queryFn: () => getHelmPaymentRun(organizationId, book.id),
  });
  const [paying, setPaying] = useState<PaymentRunLine | null>(null);
  const [paidOn, setPaidOn] = useState(today());
  const [reference, setReference] = useState("");
  const canRecord = !book.read_only && ["OWNER", "ADMIN", "BILLING"].includes(role);

  const markPaid = useMutation({
    mutationFn: (line: PaymentRunLine) =>
      markHelmInvoicePaid(organizationId, book.id, line.work_item_id ?? "", { paid_on: paidOn, reference: reference.trim() || null }),
    onSuccess: () => {
      toast.success("Recorded as paid.");
      setPaying(null);
      setReference("");
      void queryClient.invalidateQueries({ queryKey: helmKeys.book(organizationId, book.id) });
    },
    onError: (error) => toast.error(errorMessage(error, "Helm could not record the payment.")),
  });

  return (
    <div className="mx-auto max-w-5xl space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Plan</h1>
          <p className="helm-muted mt-1 text-sm">
            The payment run: what to pay on each run day, after a person approved it. {run.data?.note}
          </p>
        </div>
        {run.data && run.data.days.length > 0 ? (
          <button type="button" className="helm-btn" onClick={() => downloadCsv(run.data)}>
            <Download className="h-3.5 w-3.5" aria-hidden="true" />
            Download the list
          </button>
        ) : null}
      </div>
      {run.isError ? (
        <LoadFailed what="the payment run" onRetry={() => void run.refetch()} />
      ) : run.isLoading ? (
        <p className="helm-muted text-sm" role="status">
          Loading…
        </p>
      ) : (run.data?.days ?? []).length === 0 ? (
        <p className="helm-panel p-4 text-sm">
          Nothing scheduled. When you approve a payment in the cockpit it appears here, on its run day.
        </p>
      ) : (
        (run.data?.days ?? []).map((day) => (
          <section key={day.run_date} aria-labelledby={`run-${day.run_date}`} className="helm-panel p-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h2 id={`run-${day.run_date}`} className="text-sm font-semibold">
                {day.run_date === "unscheduled" ? "No run day yet" : formatCalendarDate(day.run_date)}
              </h2>
              <div className="text-sm font-semibold">
                <MoneyByCurrency totals={day.totals} />
              </div>
            </div>
            <div className="relative mt-2 overflow-x-auto">
              <table className="w-full min-w-[620px] text-sm">
                <thead>
                  <tr className="helm-label text-left">
                    <th scope="col" className="py-1 pr-3">Payee</th>
                    <th scope="col" className="py-1 pr-3">Account</th>
                    <th scope="col" className="py-1 pr-3">Invoice</th>
                    <th scope="col" className="py-1 pr-3 text-right">Amount</th>
                    <th scope="col" className="py-1 pr-3">State</th>
                    <th scope="col" className="py-1"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {day.lines.map((line, index) => (
                    <React.Fragment key={`${line.work_item_id ?? "hidden"}-${index}`}>
                    <tr className="helm-hairline border-t" data-testid="helm-run-line">
                      <td className="py-2 pr-3">
                        {line.readable ? (
                          line.payee
                        ) : (
                          <span className="helm-muted inline-flex items-center gap-1 text-xs">
                            <Lock className="h-3 w-3" aria-hidden="true" />
                            In a workspace you cannot read
                          </span>
                        )}
                      </td>
                      <td className="helm-figure py-2 pr-3 text-xs">{line.readable ? line.bank_account ?? "—" : "—"}</td>
                      <td className="py-2 pr-3 text-xs">{line.readable ? line.invoice ?? "—" : "—"}</td>
                      <td className="py-2 pr-3 text-right">
                        {line.amount_micros !== null ? <Money micros={line.amount_micros} currency={line.currency} /> : "—"}
                      </td>
                      <td className="py-2 pr-3 text-xs">{STATE_WORDS[line.state] ?? line.state}</td>
                      <td className="py-2 text-right">
                        <div className="flex justify-end gap-2">
                          {line.certificate_id ? (
                            <Link
                              to={withBook(helmCertificatePath(orgSlug, line.certificate_id))}
                              className="helm-accent inline-flex items-center gap-1 text-xs font-semibold hover:underline"
                              aria-label={`Certificate for ${line.readable ? line.payee : "this payment"}`}
                            >
                              <FileCheck2 className="h-3.5 w-3.5" aria-hidden="true" />
                              Certificate
                            </Link>
                          ) : null}
                          {canRecord && line.readable && line.work_item_id ? (
                            <button
                              type="button"
                              className="helm-btn min-h-0 py-1 text-xs"
                              aria-expanded={paying?.work_item_id === line.work_item_id}
                              onClick={() => setPaying(paying?.work_item_id === line.work_item_id ? null : line)}
                            >
                              Mark paid
                            </button>
                          ) : null}
                        </div>
                      </td>
                    </tr>
                    {paying && paying.work_item_id === line.work_item_id ? (
                      <tr>
                        <td colSpan={6} className="pb-3">
                          <form
                            className="helm-panel-raised flex flex-wrap items-end gap-3 p-3"
                            onSubmit={(event) => {
                              event.preventDefault();
                              markPaid.mutate(line);
                            }}
                          >
                            <label className="text-xs">
                              <span className="helm-label">Paid on</span>
                              <input
                                className="helm-input mt-1"
                                type="date"
                                required
                                value={paidOn}
                                max={today()}
                                onChange={(event) => setPaidOn(event.target.value)}
                              />
                            </label>
                            <label className="min-w-[12rem] flex-1 text-xs">
                              <span className="helm-label">Reference (optional)</span>
                              <input
                                className="helm-input mt-1"
                                value={reference}
                                maxLength={200}
                                placeholder="UTR or cheque number"
                                onChange={(event) => setReference(event.target.value)}
                              />
                            </label>
                            <button type="submit" className="helm-btn helm-btn-primary" disabled={markPaid.isPending || !paidOn}>
                              Record payment
                            </button>
                            <button type="button" className="helm-btn" onClick={() => setPaying(null)}>
                              Cancel
                            </button>
                            <p className="helm-muted w-full text-xs">Helm only records it; you pay from your bank.</p>
                          </form>
                        </td>
                      </tr>
                    ) : null}
                    </React.Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        ))
      )}
    </div>
  );
};

export default HelmPlan;
