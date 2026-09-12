/**
 * Tenant picker for FlowPilot AI.
 */

import React from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Building2, Loader2, LogOut, Plus } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useTenant } from "@/hooks/useTenant";
import { workspacePath, createWorkspacePath } from "@/routes/tenantPaths";
import { useLoginRedirect } from "@/routes/useLoginRedirect";
import { canCreateWorkspace } from "@/permissions/organizationPermissions";
import { leaveOrganization } from "@/services/api/organization";
import { ApiError } from "@/services/api/errors";
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
  onLeft: () => void;
}> = ({ organization, onLeft }) => {
  const canCreate = canCreateWorkspace(organization.role);
  const isArchived = organization.organization_status !== "ACTIVE";

  const stranded =
    !isArchived && organization.workspaces.length === 0 && !canCreate;

  const { mutate: leave, isPending: isLeaving } = useMutation({
    mutationFn: () => leaveOrganization(organization.organization_id),
    onSuccess: () => {
      toast.success(`You have left ${organization.organization_name}.`);
      onLeft();
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError
          ? error.message
          : "Could not leave this organization. Please try again.",
      );
    },
  });

  return (
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

      {isArchived ? (
        <div className="space-y-2">
          <p className="rounded-lg border border-border/60 bg-muted/30 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
            This organization is archived. All workspaces and API integrations
            are inactive.
          </p>
          {organization.workspaces.length > 0 && (
            <ul className="space-y-1.5">
              {organization.workspaces.map((workspace) => (
                <li
                  key={workspace.id}
                  className="flex items-center justify-between gap-3 rounded-lg border border-transparent bg-muted/10 px-3 py-2.5 text-sm opacity-60"
                >
                  <span className="min-w-0 truncate font-semibold text-muted-foreground">
                    {workspace.workspace_name}
                  </span>
                  <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                    inactive
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : organization.workspaces.length === 0 ? (
        <p className="rounded-lg bg-muted/30 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          {canCreate
            ? "No workspaces yet. Create one to get started."
            : "You do not have access to any workspace in this organization yet. An organization admin can grant you access."}
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

      {canCreate && !isArchived && (
        <Link
          to={createWorkspacePath(organization.organization_slug)}
          className="flex items-center gap-2 rounded-lg border border-dashed border-border/70 px-3 py-2 text-xs font-semibold text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" />
          New workspace
        </Link>
      )}

      {stranded && (
        <button
          type="button"
          onClick={() => leave()}
          disabled={isLeaving}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-border/70 px-3 py-2 text-xs font-semibold text-muted-foreground transition hover:border-destructive/50 hover:text-destructive disabled:opacity-60"
        >
          {isLeaving ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <LogOut className="h-3.5 w-3.5" />
          )}
          {isLeaving ? "Leaving..." : "Leave organization"}
        </button>
      )}
    </section>
  );
};

export const WorkspacePicker: React.FC = () => {
  const location = useLocation();

  // ARCH-29 Tranche 1. Hoisted above the early returns.
  const loginPath = useLoginRedirect(location.pathname);
  const { state, refresh } = useTenant();

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
      <Navigate to={loginPath} replace />
    );
  }

  if (state.status === "onboarding_required") {
    return <Navigate to={ROUTES.ONBOARDING} replace />;
  }

  // ARCH-30 Tranche 3 (B.1). Sign-in without a destination lands on the
  // primary workspace dashboard: the one tenant resolution already selected
  // (last used, else first). A person who opens /workspaces deliberately to
  // switch still gets the picker, because only sign-in adds `landing=1`.
  if (new URLSearchParams(location.search).get("landing") === "1" && state.status === "ready") {
    return (
      <Navigate
        to={workspacePath(state.organization.organization_slug, state.workspace.slug)}
        replace
      />
    );
  }

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
              onLeft={refresh}
            />
          ))}
        </div>

        <Link
          to={ROUTES.NEW_ORGANIZATION}
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
