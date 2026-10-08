/**
 * ARCH43-S2:page-cases — the case board. Cases are assembled automatically
 * from published templates (by ARCH-42 entity or ARCH-38 batch) or opened by
 * hand; each column is a status. Templates are drafted as JSON, published
 * (immutable from then on) and retired here.
 */
import React, { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { toast } from "sonner";
import { FolderKanban, Inbox, Loader2, Lock } from "lucide-react";

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
import { CapabilityLoading } from "@/components/common/CapabilityLoading";
import { ViewPlansAction } from "@/components/billing/ViewPlansAction";

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
  // F-167: templates are a workspace admin's; opening a case needs a contributor. The server
  // refuses anyone else, so they are not offered the controls.
  const role = workspace?.role;
  const canManageTemplates = role === "OWNER" || role === "ADMIN";
  const canOpenCase = canManageTemplates || role === "CONTRIBUTOR";
  const refresh = (message: string) => (): void => {
    toast.success(message);
    void client.invalidateQueries({ queryKey: caseKeys.all(workspaceId) });
  };
  const create = useMutation({
    mutationFn: () => {
      let body: TemplateWrite;
      try {
        body = JSON.parse(draft) as TemplateWrite;
      } catch (error) {
        throw new Error(`The template is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
      }
      return createCaseTemplate(workspaceId, body);
    },
    onSuccess: refresh("Template draft saved. Publish it to start assembling cases."),
  });
  const publish = useMutation({ mutationFn: (id: string) => publishCaseTemplate(workspaceId, id), onSuccess: refresh("Template published.") });
  const retire = useMutation({ mutationFn: (id: string) => retireCaseTemplate(workspaceId, id), onSuccess: refresh("Template retired.") });
  const open = useMutation({
    mutationFn: () => createCase(workspaceId, manual.templateId, manual.title),
    onSuccess: () => {
      refresh("Case opened.")();
      setManual({ templateId: manual.templateId, title: "" });
    },
  });
  const failure = create.error ?? publish.error ?? retire.error ?? open.error;

  // F-164: no lock verdict while the plan is still being read.
  if (capability.isLoading) {return <CapabilityLoading />;}
  if (!capability.granted) {
    return (
      <section className={`${SURFACE} mx-auto mt-6 max-w-2xl space-y-3 p-6`} aria-labelledby="cases-lock">
        <div className="flex items-center gap-2"><Lock className="h-4 w-4" aria-hidden /><h2 id="cases-lock" className={SECTION_TITLE}>Case intelligence</h2></div>
        <p className="text-sm text-muted-foreground">
          Split scanned bundles into documents and assemble them into cases — vendor files, loan packets, claims — with a
          checklist of what is missing and rules that catch documents that disagree.
        </p>
        <p className={HINT}>It&apos;s included on the Business and Enterprise plans.</p>
        <ViewPlansAction />
      </section>
    );
  }
  const published = (templates.data ?? []).filter((t) => t.status === "PUBLISHED");
  const allCases = cases.data?.items ?? [];
  return (
    <div className="space-y-5 p-4">
      <header className="flex flex-wrap items-start gap-3">
        <FolderKanban className="mt-1 h-5 w-5 text-primary" aria-hidden />
        <div className="min-w-0 flex-1">
          <h1 className={PAGE_TITLE}>Cases</h1>
          <p className={`mt-0.5 max-w-3xl ${HINT}`}>
            A case gathers the documents of one file (a vendor, a loan, a claim) with a checklist of what is missing and
            rules that catch documents that disagree. Published templates assemble cases as documents arrive.
          </p>
        </div>
        <Link to={packetSplitsPath(orgSlug, workspaceSlug)} className="text-sm text-primary hover:underline">Scanned packets</Link>
      </header>
      {failure ? <p className="text-sm text-destructive" role="alert">{errorMessage(failure, "The request failed.")}</p> : null}
      {cases.isLoading ? <Loader2 className="h-5 w-5 animate-spin" aria-label="Loading" /> : null}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4" role="list" aria-label="Case board">
        {CASE_STATUSES.map((status) => {
          const column = allCases.filter((c) => c.status === status);
          return (
            <section key={status} className={`${SURFACE_INSET} min-h-40 space-y-2 p-2`} role="listitem" aria-label={CASE_STATUS_LABELS[status]}>
              <h2 className="flex items-center justify-between px-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                <span>{CASE_STATUS_LABELS[status]}</span>
                <span className="fp-num rounded-full bg-muted px-1.5 py-px text-[10.5px]">{cases.data?.counts_by_status[status] ?? 0}</span>
              </h2>
              {column.map((c) => (
                <Link key={c.id} to={casePath(orgSlug, workspaceSlug, c.id)} className={`${SURFACE} block space-y-1.5 p-2.5 text-sm transition-colors hover:border-primary/40`}>
                  <span className="block truncate font-semibold" title={c.title}>{c.title}</span>
                  <span className="block h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                    <span className="block h-full rounded-full bg-primary" style={{ width: `${Math.round(c.completeness * 100)}%` }} />
                  </span>
                  <span className={`block ${HINT}`}>
                    {c.documents} documents · {Math.round(c.completeness * 100)}% complete{c.failed_rules ? ` · ${c.failed_rules} rule(s) failed` : ""}
                  </span>
                </Link>
              ))}
              {column.length === 0 && !cases.isLoading ? (
                <p className="flex flex-col items-center gap-1 px-2 py-6 text-center text-xs text-muted-foreground">
                  <Inbox className="h-4 w-4" aria-hidden /> No cases
                </p>
              ) : null}
            </section>
          );
        })}
      </div>
      {canOpenCase ? (
        <section className={`${SURFACE} space-y-2 p-4`}>
          <h2 className={SECTION_TITLE}>Open a case by hand</h2>
          {published.length === 0 ? (
            <p className={HINT}>
              {canManageTemplates ? "Publish a template below first." : "A workspace admin publishes the templates cases are opened from."}
            </p>
          ) : null}
          <div className="flex flex-wrap gap-2">
            <select className={`${INPUT} sm:max-w-xs`} aria-label="Template" value={manual.templateId} onChange={(e) => setManual({ ...manual, templateId: e.target.value })}>
              <option value="">Template…</option>
              {published.map((t) => <option key={t.id} value={t.id}>{t.name} v{t.version}</option>)}
            </select>
            <input className={`${INPUT} min-w-0 flex-1`} aria-label="Case title" placeholder="Title" maxLength={200} value={manual.title} onChange={(e) => setManual({ ...manual, title: e.target.value })} />
            <button type="button" className={BUTTON_PRIMARY} disabled={!manual.templateId || !manual.title.trim() || open.isPending} onClick={() => open.mutate()}>Open case</button>
          </div>
        </section>
      ) : null}
      <section className={`${SURFACE} space-y-3 p-4`}>
        <h2 className={SECTION_TITLE}>Templates</h2>
        {(templates.data ?? []).length === 0 && !templates.isLoading ? (
          <p className={HINT}>No templates yet.</p>
        ) : (
          <ul className="divide-y divide-border/60 text-sm">
            {(templates.data ?? []).map((t) => (
              <li key={t.id} className="flex flex-wrap items-center gap-2 py-2">
                <span className="font-mono text-xs">{t.key} v{t.version}</span>
                <span className="font-medium">{t.name}</span>
                <span className={`rounded-full px-2 py-px text-[10.5px] font-semibold uppercase tracking-wide ${
                  t.status === "PUBLISHED" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300" : t.status === "DRAFT" ? "bg-amber-500/15 text-amber-700 dark:text-amber-300" : "bg-muted text-muted-foreground"
                }`}>{t.status.toLowerCase()}</span>
                <span className={HINT}>{t.required_documents.length} documents · {t.rules.length} rules · by {t.assembly_key.toLowerCase()}</span>
                {canManageTemplates && t.status === "DRAFT" ? (
                  <button type="button" className={`${BUTTON_SECONDARY} ml-auto`} disabled={publish.isPending} onClick={() => publish.mutate(t.id)}>Publish</button>
                ) : null}
                {canManageTemplates && t.status === "PUBLISHED" ? (
                  <button type="button" className={`${BUTTON_SECONDARY} ml-auto`} disabled={retire.isPending} onClick={() => retire.mutate(t.id)}>Retire</button>
                ) : null}
              </li>
            ))}
          </ul>
        )}
        {canManageTemplates ? (
          <>
            <label className="block space-y-1 text-sm">
              <span>New template (JSON). Published templates cannot change; a new draft of the same key becomes the next version.</span>
              <textarea className={`${INPUT} h-56 w-full font-mono text-xs`} spellCheck={false} value={draft} onChange={(e) => setDraft(e.target.value)} />
            </label>
            <button type="button" className={BUTTON_PRIMARY} disabled={create.isPending} onClick={() => create.mutate()}>Save draft</button>
          </>
        ) : (
          <p className={HINT}>Workspace admins create and publish templates.</p>
        )}
      </section>
    </div>
  );
};

export default Cases;
