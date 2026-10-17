import React, { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CalendarCheck2, Download, FileCheck2, Lock } from "lucide-react";

import { LoadFailed } from "@/components/common/LoadFailed";
import { useHelm, useHelmBook } from "@/components/helm/HelmContext";
import { Money, MoneyByCurrency } from "@/components/helm/HelmBits";
import { HelmPageHeader } from "@/components/helm/ui/HelmPageHeader";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { helmCertificatePath, helmPath } from "@/routes/tenantPaths";
import { errorMessage } from "@/services/api/errors";
import { getHelmPaymentRun, helmKeys, markHelmInvoicePaid } from "@/services/api/helm";
import { formatCalendarDate, formatCalendarMonthShort, formatNumber } from "@/utils/displayTime";
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

/** Every run day's totals added up, per currency (never across currencies). */
const totalsOf = (run: PaymentRun): Record<string, number> => {
  const out: Record<string, number> = {};
  for (const day of run.days) {
    for (const [currency, micros] of Object.entries(day.totals)) {
      out[currency] = (out[currency] ?? 0) + micros;
    }
  }
  return out;
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
    <div className="mx-auto max-w-6xl space-y-5">
      <HelmPageHeader
        eyebrow={book.name}
        title="Plan"
        description={<>The payment run: what to pay on each run day, after a person approved it. {run.data?.note}</>}
        actions={
          run.data && run.data.days.length > 0 ? (
            <button type="button" className="helm-btn" onClick={() => downloadCsv(run.data)}>
              <Download className="h-3.5 w-3.5" aria-hidden="true" />
              Download the list
            </button>
          ) : null
        }
      />
      {run.data && run.data.days.length > 0 ? (
        <ul className="grid gap-3 sm:grid-cols-3" aria-label="The payment run in figures">
          <li className="helm-panel helm-enter p-4" style={{ ["--i" as string]: 1 }}>
            <p className="helm-label">To pay</p>
            <div className="helm-numeral mt-2 text-2xl">
              <MoneyByCurrency totals={totalsOf(run.data)} />
            </div>
          </li>
          <li className="helm-panel helm-enter p-4" style={{ ["--i" as string]: 2 }}>
            <p className="helm-label">Run days</p>
            <p className="helm-numeral mt-2 text-2xl">{formatNumber(run.data.days.filter((d) => d.run_date !== "unscheduled").length)}</p>
          </li>
          <li className="helm-panel helm-enter p-4" style={{ ["--i" as string]: 3 }}>
            <p className="helm-label">Payments</p>
            <p className="helm-numeral mt-2 text-2xl">{formatNumber(run.data.days.reduce((n, d) => n + d.lines.length, 0))}</p>
          </li>
        </ul>
      ) : null}
      {run.isError ? (
        <LoadFailed what="the payment run" onRetry={() => void run.refetch()} />
      ) : run.isLoading ? (
        <HelmSkeleton label="Loading…" />
      ) : (run.data?.days ?? []).length === 0 ? (
        <div className="helm-panel helm-enter flex flex-col items-start gap-3 p-6 text-sm sm:flex-row sm:items-center">
          <span className="helm-accent flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl border border-[hsl(var(--helm-accent)/0.35)] bg-[hsl(var(--helm-accent)/0.1)]">
            <CalendarCheck2 className="h-5 w-5" aria-hidden="true" />
          </span>
          <p className="flex-1">Nothing scheduled. When you approve a payment in the cockpit it appears here, on its run day.</p>
          <Link to={withBook(helmPath(orgSlug))} className="helm-btn">
            Open the cockpit
          </Link>
        </div>
      ) : (
        (run.data?.days ?? []).map((day, dayIndex) => (
          <section
            key={day.run_date}
            aria-labelledby={`run-${day.run_date}`}
            className="helm-panel helm-enter p-5"
            style={{ ["--i" as string]: dayIndex + 4 }}
          >
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex items-center gap-3">
                {day.run_date === "unscheduled" ? null : (
                  <span className="helm-panel-raised flex h-14 w-14 shrink-0 flex-col items-center justify-center" aria-hidden="true">
                    <span className="helm-label text-[10px] leading-none">
                      {formatCalendarMonthShort(Number(day.run_date.slice(0, 4)), Number(day.run_date.slice(5, 7)))}
                    </span>
                    <span className="helm-numeral text-2xl">{Number(day.run_date.slice(8, 10))}</span>
                  </span>
                )}
                <div>
                  <h2 id={`run-${day.run_date}`} className="text-[15px] font-semibold">
                    {day.run_date === "unscheduled" ? "No run day yet" : formatCalendarDate(day.run_date)}
                  </h2>
                  <p className="helm-muted text-xs">{formatNumber(day.lines.length)} {day.lines.length === 1 ? "payment" : "payments"}</p>
                </div>
              </div>
              <div className="helm-numeral text-xl">
                <MoneyByCurrency totals={day.totals} />
              </div>
            </div>
            <div className="relative mt-3 overflow-x-auto">
              <table className="helm-table w-full min-w-[620px] text-sm">
                <thead>
                  <tr>
                    <th scope="col">Payee</th>
                    <th scope="col">Account</th>
                    <th scope="col">Invoice</th>
                    <th scope="col" className="text-right">Amount</th>
                    <th scope="col">State</th>
                    <th scope="col"><span className="sr-only">Actions</span></th>
                  </tr>
                </thead>
                <tbody>
                  {day.lines.map((line, index) => (
                    <React.Fragment key={`${line.work_item_id ?? "hidden"}-${index}`}>
                    <tr data-testid="helm-run-line">
                      <td className="font-medium">
                        {line.readable ? (
                          line.payee
                        ) : (
                          <span className="helm-muted inline-flex items-center gap-1 text-xs">
                            <Lock className="h-3 w-3" aria-hidden="true" />
                            In a workspace you cannot read
                          </span>
                        )}
                      </td>
                      <td className="helm-id text-xs">{line.readable ? line.bank_account ?? "—" : "—"}</td>
                      <td className="helm-id text-xs">{line.readable ? line.invoice ?? "—" : "—"}</td>
                      <td className="text-right font-semibold">
                        {line.amount_micros !== null ? <Money micros={line.amount_micros} currency={line.currency} /> : "—"}
                      </td>
                      <td>
                        <span className={`helm-chip px-2 text-xs ${line.state === "APPROVED" ? "helm-state-holding" : "helm-state-sky"}`}>
                          {STATE_WORDS[line.state] ?? line.state}
                        </span>
                      </td>
                      <td className="text-right">
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
