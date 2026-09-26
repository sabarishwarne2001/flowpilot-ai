/**
 * ARCH47-S2:erp-common — small pieces every ERP posting view shares: the state
 * badge, the plan lock, where an approved outcome lives, and a JSON block.
 */
import React from "react";
import { Lock } from "lucide-react";

import { HINT, SECTION_TITLE, SURFACE, SURFACE_INSET } from "@/components/ui/primitives";
import { casePath, procurementCasePath, tablePath } from "@/routes/tenantPaths";
import { STATE_LABELS, STATE_TONES, type PostingState, type SourceKind } from "@/types/erp";

export const PostingStateBadge: React.FC<{ readonly state: PostingState | null | undefined }> = ({ state }) =>
  state ? (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-semibold ${STATE_TONES[state]}`}>
      {STATE_LABELS[state]}
    </span>
  ) : (
    <span className="inline-flex items-center rounded-full border border-dashed border-border px-2 py-0.5 text-[11px] text-muted-foreground">
      Not posted
    </span>
  );

export const ErpLocked: React.FC<{ readonly compact?: boolean }> = ({ compact = false }) =>
  compact ? (
    <section className={`${SURFACE_INSET} flex items-center gap-2 p-3 text-sm`} aria-label="ERP posting">
      <Lock className="h-4 w-4" aria-hidden />
      <span>Posting approved documents to your ERP is included on the Business and Enterprise plans.</span>
    </section>
  ) : (
    <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="erp-lock">
      <div className="flex items-center gap-2">
        <Lock className="h-4 w-4" aria-hidden />
        <h2 id="erp-lock" className={SECTION_TITLE}>ERP posting</h2>
      </div>
      <p className="text-sm text-muted-foreground">
        Post what you approved — reconciled invoices, confirmed tables, completed cases — to QuickBooks Online, Zoho
        Books, Dynamics 365 Business Central, SAP S/4HANA, NetSuite, Tally Prime, EDI X12 and UBL partners, SFTP drop
        folders or a CSV / Excel import, exactly once, and marked posted only when the ERP says so.
      </p>
      <p className={HINT}>It&apos;s included on the Business and Enterprise plans.</p>
    </section>
  );

export const sourcePath = (orgSlug: string, workspaceSlug: string, kind: SourceKind, id: string): string => {
  if (kind === "PROCUREMENT_CASE") {
    return procurementCasePath(orgSlug, workspaceSlug, id);
  }
  if (kind === "TABLE") {
    return tablePath(orgSlug, workspaceSlug, id);
  }
  return casePath(orgSlug, workspaceSlug, id);
};

export const JsonBlock: React.FC<{ readonly value: unknown; readonly label: string }> = ({ value, label }) => (
  <pre aria-label={label} className="max-h-96 overflow-auto rounded-lg bg-muted/50 p-3 font-mono text-[11px] leading-relaxed">
    {JSON.stringify(value, null, 2)}
  </pre>
);

export const money = (amount?: string | null, currency?: string | null): string =>
  amount ? `${amount}${currency ? ` ${currency}` : ""}` : "—";

export const canContribute = (role?: string | null): boolean => role === "ADMIN" || role === "OWNER" || role === "CONTRIBUTOR";
export const canAdminister = (role?: string | null): boolean => role === "ADMIN" || role === "OWNER";
