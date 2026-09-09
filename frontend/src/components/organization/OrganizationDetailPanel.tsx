import React from "react";
import { useQuery } from "@tanstack/react-query";
import { Building2, Layers, Loader2 } from "lucide-react";

import {
  getOrganization,
  listOrganizationWorkspaces,
} from "@/services/api/organization";

/**
 * ARCH-29 Slice 3 — organization identity and the full workspace roster.
 *
 * BOTH CALLS FETCH FACTS THE BOOTSTRAP CONTEXT DOES NOT CARRY
 * ===========================================================
 *
 * `OrganizationGeneral` reads everything from `useResolvedOrganization()`,
 * which is backed by `OrganizationMembershipSummary` from `/me/context`. That
 * shape has six fields: organization_id, slug, name, status, the actor's role,
 * and the actor's workspaces. It is a membership summary, not the
 * organization.
 *
 *   getOrganization           -> legal_name, created_at, updated_at.
 *                                None are in the context. `legal_name` is the
 *                                entity that appears on invoices and in a DPA,
 *                                and is a different string from the display
 *                                name often enough to matter.
 *
 *   listOrganizationWorkspaces -> every workspace in the tenant.
 *                                The context carries the workspaces THIS ACTOR
 *                                reaches. For an admin auditing the tenant
 *                                those are different sets, and the gap between
 *                                them is the interesting part: a workspace
 *                                nobody has been granted still exists, still
 *                                stores documents, and still bills.
 *
 * This is the exception in a slice where most "detail" endpoints turned out to
 * return exactly what their list already had.
 */

const formatDate = (value: string | null | undefined): string =>
  value ? new Date(value).toLocaleDateString() : "—";

const Field: React.FC<{
  readonly label: string;
  readonly children: React.ReactNode;
}> = ({ label, children }) => (
  <div>
    <dt className="text-xs text-muted-foreground">{label}</dt>
    <dd className="mt-0.5 text-sm text-foreground">{children}</dd>
  </div>
);

export const OrganizationDetailPanel: React.FC<{
  readonly organizationId: string;
}> = ({ organizationId }) => {
  const detail = useQuery({
    queryKey: ["organizations", organizationId, "detail"],
    queryFn: () => getOrganization(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const workspaces = useQuery({
    queryKey: ["organizations", organizationId, "workspaces", "all"],
    queryFn: () => listOrganizationWorkspaces(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const rows = workspaces.data ?? [];

  return (
    <div className="space-y-4">
      <section className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <Building2
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Registered details
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              The entity of record. This is what appears on invoices and in a
              data processing agreement.
            </p>
          </div>
        </div>

        {detail.isLoading ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading…
          </p>
        ) : detail.isError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            The organization record could not be loaded.
          </p>
        ) : detail.data ? (
          <dl className="mt-4 grid gap-4 sm:grid-cols-3">
            <Field label="Legal name">
              {detail.data.legal_name ? (
                detail.data.legal_name
              ) : (
                /*
                  Not defaulted to the display name. They are different fields
                  and a tenant that has not set one should see that it is
                  unset, rather than be shown a value that looks entered.
                */
                <span className="text-muted-foreground">
                  Not set — the display name is used instead
                </span>
              )}
            </Field>
            <Field label="Created">{formatDate(detail.data.created_at)}</Field>
            <Field label="Last updated">
              {formatDate(detail.data.updated_at)}
            </Field>
          </dl>
        ) : null}
      </section>

      <section className="rounded-xl border border-border bg-card p-4">
        <div className="flex items-start gap-3">
          <Layers
            className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden
          />
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Workspaces in this organization
            </h2>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Every workspace in the tenant, including any you have not been
              added to.
            </p>
          </div>
        </div>

        {workspaces.isLoading ? (
          <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
            Loading…
          </p>
        ) : workspaces.isError ? (
          <p className="mt-3 text-sm text-destructive" role="alert">
            The workspace list could not be loaded.
          </p>
        ) : rows.length === 0 ? (
          <p className="mt-3 text-sm text-muted-foreground">
            This organization has no workspaces yet.
          </p>
        ) : (
          <>
            <ul className="mt-3 divide-y divide-border rounded-lg border border-border">
              {rows.map((workspace) => (
                <li
                  key={workspace.id}
                  className="flex flex-wrap items-center justify-between gap-2 px-3 py-2"
                >
                  <div>
                    <p className="text-sm font-medium text-foreground">
                      {workspace.workspace_name}
                    </p>
                    <p className="font-mono text-xs text-muted-foreground">
                      {workspace.slug}
                    </p>
                  </div>
                  {workspace.status !== "ACTIVE" ? (
                    <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
                      {workspace.status}
                    </span>
                  ) : (
                    <span className="rounded border border-border px-2 py-0.5 text-xs text-muted-foreground">
                      Active
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <p className="mt-2 text-xs text-muted-foreground">
              {rows.length} {rows.length === 1 ? "workspace" : "workspaces"}.
            </p>
          </>
        )}
      </section>
    </div>
  );
};

export default OrganizationDetailPanel;
