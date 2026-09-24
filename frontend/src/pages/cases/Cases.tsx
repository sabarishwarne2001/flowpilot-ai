/**
 * ARCH43-S2:page-cases — the case board. Cases are assembled automatically
 * from published templates (by ARCH-42 entity or ARCH-38 batch) or opened by
 * hand; each column is a status. Templates are drafted as JSON, published
 * (immutable from then on) and retired here.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { FolderKanban, Loader2, Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { BUTTON_PRIMARY, BUTTON_SECONDARY, HINT, INPUT, PAGE_TITLE, SECTION_TITLE, SURFACE, SURFACE_INSET } from "@/components/ui/primitives";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { casePath, packetSplitsPath } from "@/routes/tenantPaths";
import {
  caseKeys, createCase, createCaseTemplate, listCases, listCaseTemplates, publishCaseTemplate, retireCaseTemplate,
} from "@/services/api/cases";
import { errorMessage } from "@/services/api/errors";
import { CASE_STATUSES, CASE_STATUS_LABELS, type TemplateWrite } from "@/types/cases";

const EXAMPLE: TemplateWrite = {
  key: "purchase-file",
  name: "Purchase file",
  assembly_key: "ENTITY",
  entity_kind: "ORGANIZATION",
  entity_role: "vendor",
  required_documents: [{ doc_type: "purchase_order" }, { doc_type: "invoice" }, { doc_type: "goods_receipt" }],
  rules: [
    { id: "vendor-matches", op: "FUZZY_EQUAL", label: "Invoice vendor matches the PO",
      left: { doc_type: "invoice", field: "vendor_name" }, right: { doc_type: "purchase_order", field: "vendor_name" } },
    { id: "invoiced-within-po", op: "SUM_EQUALS", label: "Invoices add up to the PO total",
      terms: [{ doc_type: "invoice", field: "total_amount" }], right: { doc_type: "purchase_order", field: "total_amount" } },
    { id: "invoice-after-po", op: "DATE_ORDER", label: "Invoice dated on or after the PO",
      left: { doc_type: "purchase_order", field: "po_date" }, right: { doc_type: "invoice", field: "invoice_date" } },
  ],
};

const Cases: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const { orgSlug = "", workspaceSlug = "" } = useParams<{ orgSlug: string; workspaceSlug: string }>();
  const capability = useCapabilityAccess(workspace?.organizationId ?? "", CAPABILITY.caseIntelligence);
  const enabled = Boolean(workspaceId && capability.granted);
  const client = useQueryClient();
  const cases = useQuery({ queryKey: caseKeys.all(workspaceId), queryFn: () => listCases(workspaceId), enabled });
  const templates = useQuery({ queryKey: caseKeys.templates(workspaceId), queryFn: () => listCaseTemplates(workspaceId), enabled });
  const [draft, setDraft] = useState(JSON.stringify(EXAMPLE, null, 2));
  const [manual, setManual] = useState({ templateId: "", title: "" });
  const refresh = (): void => { void client.invalidateQueries({ queryKey: caseKeys.all(workspaceId) }); };
  const create = useMutation({ mutationFn: () => createCaseTemplate(workspaceId, JSON.parse(draft) as TemplateWrite), onSuccess: refresh });
  const publish = useMutation({ mutationFn: (id: string) => publishCaseTemplate(workspaceId, id), onSuccess: refresh });
  const retire = useMutation({ mutationFn: (id: string) => retireCaseTemplate(workspaceId, id), onSuccess: refresh });
  const open = useMutation({ mutationFn: () => createCase(workspaceId, manual.templateId, manual.title), onSuccess: refresh });
  const failure = create.error ?? publish.error ?? retire.error ?? open.error;

  if (!capability.granted) {
    return (
      <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="cases-lock">
        <div className="flex items-center gap-2"><Lock className="h-4 w-4" aria-hidden /><h2 id="cases-lock" className={SECTION_TITLE}>Case intelligence</h2></div>
        <p className="text-sm text-muted-foreground">
          Split scanned bundles into documents and assemble them into cases — vendor files, loan packets, claims — with a
          checklist of what is missing and rules that catch documents that disagree.
        </p>
        <p className={HINT}>It&apos;s included on the Business and Enterprise plans.</p>
      </section>
    );
  }
  const published = (templates.data ?? []).filter((t) => t.status === "PUBLISHED");
  return (
    <div className="space-y-5 p-4">
      <header className="flex flex-wrap items-center gap-2">
        <FolderKanban className="h-5 w-5" aria-hidden />
        <h1 className={PAGE_TITLE}>Cases</h1>
        <Link to={packetSplitsPath(orgSlug, workspaceSlug)} className="ml-auto text-sm underline">Scanned packets</Link>
      </header>
      {failure ? <p className="text-sm text-destructive">{errorMessage(failure, "The request failed.")}</p> : null}
      {cases.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      <div className="grid gap-3 md:grid-cols-4" role="list" aria-label="Case board">
        {CASE_STATUSES.map((status) => (
          <section key={status} className={`${SURFACE_INSET} min-h-40 space-y-2 p-2`} role="listitem" aria-label={CASE_STATUS_LABELS[status]}>
            <h2 className="text-xs font-bold uppercase tracking-wide">
              {CASE_STATUS_LABELS[status]} · {cases.data?.counts_by_status[status] ?? 0}
            </h2>
            {(cases.data?.items ?? []).filter((c) => c.status === status).map((c) => (
              <Link key={c.id} to={casePath(orgSlug, workspaceSlug, c.id)} className={`${SURFACE} block space-y-1 p-2 text-sm hover:bg-muted`}>
                <span className="font-semibold">{c.title}</span>
                <span className={`block ${HINT}`}>
                  {c.documents} documents · {Math.round(c.completeness * 100)}% complete{c.failed_rules ? ` · ${c.failed_rules} rule(s) failed` : ""}
                </span>
              </Link>
            ))}
          </section>
        ))}
      </div>
      <section className={`${SURFACE} space-y-3 p-4`}>
        <h2 className={SECTION_TITLE}>Templates</h2>
        <ul className="space-y-1 text-sm">
          {(templates.data ?? []).map((t) => (
            <li key={t.id} className="flex flex-wrap items-center gap-2">
              <span className="font-mono">{t.key} v{t.version}</span><span>{t.name}</span>
              <span className="rounded bg-muted px-1.5 text-xs">{t.status}</span>
              <span className={HINT}>{t.required_documents.length} documents · {t.rules.length} rules · by {t.assembly_key.toLowerCase()}</span>
              {t.status === "DRAFT" && <button type="button" className={BUTTON_SECONDARY} onClick={() => publish.mutate(t.id)}>Publish</button>}
              {t.status === "PUBLISHED" && <button type="button" className={BUTTON_SECONDARY} onClick={() => retire.mutate(t.id)}>Retire</button>}
            </li>
          ))}
        </ul>
        <label className="block space-y-1 text-sm">
          <span>New template (JSON). Published templates cannot change; a new draft of the same key becomes the next version.</span>
          <textarea className={`${INPUT} h-56 w-full font-mono text-xs`} value={draft} onChange={(e) => setDraft(e.target.value)} />
        </label>
        <button type="button" className={BUTTON_PRIMARY} disabled={create.isPending} onClick={() => create.mutate()}>Save draft</button>
      </section>
      <section className={`${SURFACE} space-y-2 p-4`}>
        <h2 className={SECTION_TITLE}>Open a case by hand</h2>
        <div className="flex flex-wrap gap-2">
          <select className={INPUT} aria-label="Template" value={manual.templateId} onChange={(e) => setManual({ ...manual, templateId: e.target.value })}>
            <option value="">Template…</option>
            {published.map((t) => <option key={t.id} value={t.id}>{t.name} v{t.version}</option>)}
          </select>
          <input className={INPUT} aria-label="Case title" placeholder="Title" value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} />
          <button type="button" className={BUTTON_PRIMARY} disabled={!manual.templateId || !manual.title || open.isPending} onClick={() => open.mutate()}>Open case</button>
        </div>
      </section>
    </div>
  );
};

export default Cases;
