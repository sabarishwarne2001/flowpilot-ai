/**
 * Tenant picker for FlowPilot AI.
 *
 * Reached in three situations, each of which needs a different explanation:
 *
 *   1. The actor belongs to an organization but can reach no workspace in it —
 *      an organization MEMBER with no grant, or a BILLING controller who is
 *      not meant to have one. They HAVE a tenant, so telling them to create
 *      one would be wrong. That distinction is why no_workspace and
 *      onboarding_required are separate states.
 *
 *   2. The URL named a tenant they cannot reach — removed, archived, never a
 *      member. TenantGuard routes here with an `unreachable` reason rather
 *      than silently substituting a different workspace, so the actor learns
 *      their destination is gone instead of quietly appearing somewhere else.
 *
 *   3. They belong to several tenants and want to choose.
 *
 * This page could not exist before ARCH-01: a second membership crashed the
 * account with MultipleResultsFound, so there was never more than one tenant
 * to pick from.
 */

import React from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { Building2, Loader2, Plus } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useTenant } from "@/hooks/useTenant";
import { loginPathWithRedirect, workspacePath } from "@/routes/tenantPaths";
import type { OrganizationMembershipSummary } from "@/types/tenancy";

interface UnreachableState {
  unreachable?: "organization" | "workspace";
}

const UNREACHABLE_MESSAGE: Record<"organization" | "workspace", string> = {
  organization:
    "That organization is no longer available to you. Your access may have been removed, or the organization may have been archived.",
  workspace:
    "That workspace is no longer available to you. Your access may have been revoked, or the workspace may have been archived.",
};

const OrganizationCard: React.FC<{
  organization: OrganizationMembershipSummary;
}> = ({ organization }) => (
  <section className="space-y-3 rounded-xl border border-border/60 bg-card p-5">
    <header className="flex items-start justify-between gap-4">
      <div className="min-w-0 space-y-0.5">
        <h2 className="truncate text-sm font-bold text-foreground">
          {organization.organization_name}
        </h2>
        <p className="text-xs text-muted-foreground">
          /{organization.organization_slug} · {organization.role.toLowerCase()}
        </p>
      </div>
      {organization.organization_status !== "ACTIVE" && (
        <span className="shrink-0 rounded-full bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {organization.organization_status.toLowerCase()}
        </span>
      )}
    </header>

    {organization.workspaces.length === 0 ? (
      <p className="rounded-lg bg-muted/30 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
        You do not have access to any workspace in this organization yet. An
        organization admin can grant you access.
      </p>
    ) : (
      <ul className="space-y-1.5">
        {organization.workspaces.map((workspace) => (
          <li key={workspace.id}>
            <Link
              to={workspacePath(organization.organization_slug, workspace.slug)}
              className="flex items-center justify-between gap-3 rounded-lg border border-transparent bg-muted/20 px-3 py-2.5 text-sm transition hover:border-border hover:bg-muted/40"
            >
              <span className="min-w-0 truncate font-semibold text-foreground">
                {workspace.workspace_name}
              </span>
              <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                {workspace.effective_role.toLowerCase()}
              </span>
            </Link>
          </li>
        ))}
      </ul>
    )}
  </section>
);

export const WorkspacePicker: React.FC = () => {
  const location = useLocation();
  const { state } = useTenant();

  const unreachable = (location.state as UnreachableState | null)?.unreachable;

  if (state.status === "loading") {
    return (
      <div className="flex h-screen w-full items-center justify-center bg-background">
        <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (state.status === "unauthenticated") {
    return (
      <Navigate to={loginPathWithRedirect(location.pathname)} replace />
    );
  }

  if (state.status === "onboarding_required") {
    return <Navigate to={ROUTES.ONBOARDING} replace />;
  }

  // "error" resolves to an empty list below rather than a redirect: this page
  // is already a safe landing place, and bouncing a failed bootstrap elsewhere
  // would loop.
  const organizations =
    state.status === "ready" || state.status === "no_workspace"
      ? state.organizations
      : [];

  return (
    <div className="flex min-h-screen w-full justify-center bg-background px-6 py-12">
      <div className="w-full max-w-lg space-y-6">
        <header className="space-y-2">
          <h1 className="text-2xl font-extrabold tracking-tight text-foreground">
            Choose a workspace
          </h1>
          <p className="text-sm font-medium text-muted-foreground">
            Select where you want to work.
          </p>
        </header>

        {unreachable && (
          <div className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3">
            <p className="text-sm leading-relaxed text-foreground">
              {UNREACHABLE_MESSAGE[unreachable]}
            </p>
          </div>
        )}

        {state.status === "error" && (
          <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3">
            <p className="text-sm leading-relaxed text-foreground">
              We could not load your organizations. This is a connection
              problem, not a change to your account.
            </p>
          </div>
        )}

        <div className="space-y-4">
          {organizations.map((organization) => (
            <OrganizationCard
              key={organization.organization_id}
              organization={organization}
            />
          ))}
        </div>

        <Link
          to={ROUTES.ONBOARDING}
          className="flex items-center gap-3 rounded-xl border border-dashed border-border px-4 py-3.5 transition hover:border-primary/50 hover:bg-muted/20"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-muted">
            <Plus className="h-4 w-4 text-muted-foreground" />
          </span>
          <span className="space-y-0.5">
            <span className="block text-sm font-semibold text-foreground">
              Create a new organization
            </span>
            <span className="block text-xs text-muted-foreground">
              Start a separate company with its own team and billing.
            </span>
          </span>
        </Link>

        {organizations.length === 0 && state.status !== "error" && (
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <Building2 className="h-5 w-5 text-muted-foreground" />
            <p className="text-sm text-muted-foreground">
              You do not belong to any organization yet.
            </p>
          </div>
        )}
      </div>
    </div>
  );
};

export default WorkspacePicker;
