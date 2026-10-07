import React, { useId, useState } from "react";
import { Building2, ChevronDown, FolderKanban } from "lucide-react";

/**
 * What each role can do, in plain words, on the two members pages.
 *
 * FlowPilot has two separate kinds of role. The organization role decides company-wide things
 * (billing, members, sign-in and compliance); the workspace role decides what someone can do with
 * the documents of one workspace. The descriptions used to live only in hover tooltips, which a
 * keyboard, touch or screen-reader user never sees, and nothing said how the two relate.
 */

interface RoleRow {
  readonly role: string;
  readonly summary: string;
}

const ORGANIZATION_ROLES: readonly RoleRow[] = [
  { role: "Owner", summary: "Everything: the plan and payment, sign-in rules (SSO, security policy), archiving the organization and transferring ownership. Admin of every workspace." },
  { role: "Admin", summary: "Members and invitations, workspaces, API keys, webhooks, the audit log, and viewing billing. Admin of every workspace. Cannot change the plan, sign-in rules or ownership." },
  { role: "Billing", summary: "Sees the plan, invoices and usage. No documents, unless also added to a workspace." },
  { role: "Member", summary: "No company settings. Works only in the workspaces they have been added to, with the role given there." },
];

const WORKSPACE_ROLES: readonly RoleRow[] = [
  { role: "Admin", summary: "Everything a contributor can do, plus workspace settings, members, automations and exports." },
  { role: "Contributor", summary: "Uploads and processes documents, corrects extracted fields, decides review items and uses the assistant." },
  { role: "Viewer", summary: "Reads documents and their extracted data. Changes nothing; no assistant, automations or review queue." },
];

export interface RoleGuideProps {
  /** Which page it sits on; that scope is listed first and open. */
  readonly scope: "organization" | "workspace";
}

const Table: React.FC<{ rows: readonly RoleRow[]; caption: string }> = ({ rows, caption }) => (
  <dl className="divide-y divide-border/70" aria-label={caption}>
    {rows.map((row) => (
      <div key={row.role} className="grid gap-1 py-2 sm:grid-cols-[7rem_1fr] sm:gap-4">
        <dt className="text-sm font-semibold text-foreground">{row.role}</dt>
        <dd className="text-sm leading-relaxed text-muted-foreground">{row.summary}</dd>
      </div>
    ))}
  </dl>
);

export const RoleGuide: React.FC<RoleGuideProps> = ({ scope }) => {
  const [open, setOpen] = useState(false);
  const panelId = useId();
  const organizationFirst = scope === "organization";

  const organization = (
    <section className="space-y-1">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
        Organization roles: the company
      </h3>
      <Table rows={ORGANIZATION_ROLES} caption="Organization roles" />
    </section>
  );
  const workspace = (
    <section className="space-y-1">
      <h3 className="flex items-center gap-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <FolderKanban className="h-3.5 w-3.5" aria-hidden="true" />
        Workspace roles: one workspace&apos;s documents
      </h3>
      <Table rows={WORKSPACE_ROLES} caption="Workspace roles" />
    </section>
  );

  return (
    <div className="fp-card overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex w-full items-center justify-between gap-3 px-4 py-3 text-left text-sm font-semibold text-foreground transition hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <span>What can each role do?</span>
        <ChevronDown
          className={`h-4 w-4 shrink-0 text-muted-foreground transition-transform ${open ? "rotate-180" : ""}`}
          aria-hidden="true"
        />
      </button>
      {open && (
        <div id={panelId} className="space-y-4 border-t border-border px-4 py-4">
          <p className="text-sm leading-relaxed text-muted-foreground">
            Everyone has an <strong className="text-foreground">organization role</strong> for company-wide
            settings and, for each workspace they can open, a <strong className="text-foreground">workspace role</strong>{" "}
            for its documents. Organization owners and admins are automatically admins of every workspace.
          </p>
          {organizationFirst ? organization : workspace}
          {organizationFirst ? workspace : organization}
        </div>
      )}
    </div>
  );
};

export default RoleGuide;
