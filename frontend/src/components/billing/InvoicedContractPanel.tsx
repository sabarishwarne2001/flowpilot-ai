/**
 * ARCH50-S2:invoiced-contract — the organization's invoiced enterprise contract, read-only: the term, what each
 * period costs and every invoice (with what is due and what is overdue). Contracts are drafted, activated and
 * invoiced by the operator; payment is by bank transfer against the invoice number and PO.
 */
import React from "react";
import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";

import { HINT, SCROLL_X, SECTION_TITLE, SURFACE, TABLE_HEAD, TABLE_ROW } from "@/components/ui/primitives";
import { getTenantContract, revopsKeys } from "@/services/api/revops";
import { money } from "@/types/revops";
import { formatCalendarDate, formatTimestamp } from "@/utils/displayTime";

export const InvoicedContractPanel: React.FC<{ readonly organizationId: string }> = ({ organizationId }) => {
  const query = useQuery({
    queryKey: revopsKeys.tenantContract(organizationId),
    queryFn: () => getTenantContract(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });
  const contract = query.data?.contract;
  if (!contract) {
    return null;
  }
  const outstanding = contract.invoices.filter((inv) => inv.status === "ISSUED");
  return (
    <section className={`${SURFACE} space-y-3 p-4`} aria-labelledby="invoiced-contract" data-testid="invoiced-contract">
      <div className="flex items-center gap-2">
        <FileText className="h-4 w-4" aria-hidden />
        <h2 id="invoiced-contract" className={SECTION_TITLE}>Invoiced contract {contract.contract_number}</h2>
      </div>
      <p className="text-sm">
        {contract.tier_key} plan · {contract.seats} seats · {money(contract.amount_per_period_micros, contract.currency)} per{" "}
        {contract.billing_interval} · {formatCalendarDate(contract.term_start)} – {formatCalendarDate(contract.term_end)} · {contract.status}
      </p>
      <p className={HINT}>
        Payment terms: net {contract.payment_terms_days} days{contract.po_number ? ` · PO ${contract.po_number}` : ""}.
        {outstanding.length ? ` ${outstanding.length} invoice${outstanding.length === 1 ? "" : "s"} open.` : " Nothing outstanding."}
      </p>
      <div className={SCROLL_X}>
        <table className="w-full text-sm">
          <thead>
            <tr className={TABLE_HEAD}><th className="py-2 pr-3">Invoice</th><th className="pr-3">Period</th><th className="pr-3 text-right">Total</th><th className="pr-3">Due</th><th className="pr-3">Status</th></tr>
          </thead>
          <tbody>
            {contract.invoices.map((inv) => (
              <tr key={inv.id} className={TABLE_ROW}>
                <td className="py-2 pr-3 font-mono">{inv.invoice_number}</td>
                <td className="pr-3">{formatCalendarDate(inv.period_start)} – {formatCalendarDate(inv.period_end)}</td>
                <td className="pr-3 text-right tabular-nums">{money(inv.total_micros, inv.currency)}</td>
                <td className="pr-3">{formatTimestamp(inv.due_at)}</td>
                <td className={`pr-3 ${inv.overdue ? "text-destructive" : ""}`}>{inv.overdue ? "Overdue" : inv.status === "PAID" ? "Paid" : inv.status === "VOID" ? "Void" : "Open"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
};

export default InvoicedContractPanel;
