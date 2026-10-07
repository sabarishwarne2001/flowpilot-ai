/**
 * Tenant picker for FlowPilot AI ("Choose a workspace").
 *
 * Active organizations first, each with the workspaces the person can open; then, apart and
 * badged, archived organizations. F-131/F-132: archiving is reversible, so owners and admins see
 * the organization's archived workspaces here with Restore, and an owner can restore an archived
 * organization (typing its slug, as for archiving). Before, archived workspaces vanished from
 * every list and archived organizations could not be restored at all.
 */

import React, { useId, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Archive,
  ArchiveRestore,
  Building2,
  ChevronRight,
  Loader2,
  LogOut,
  Plus,
  RefreshCw,
} from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useTenant } from "@/hooks/useTenant";
import { workspacePath, createWorkspacePath } from "@/routes/tenantPaths";
import { useLoginRedirect } from "@/routes/useLoginRedirect";
import { canCreateWorkspace, canDeleteOrganization } from "@/permissions/organizationPermissions";
import {
  leaveOrganization,
  listOrganizationWorkspaces,
  restoreOrganization,
} from "@/services/api/organization";
import { restoreWorkspace } from "@/services/api/workspaces";
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

const failureMessage = (error: unknown, fallback: string): string =>
  error instanceof ApiError ? error.message : fallback;

const Initial: React.FC<{ readonly name: string; readonly muted?: boolean }> = ({ name, muted = false }) => (
  <span
    aria-hidden="true"
    className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg text-sm font-semibold ${
      muted ? "bg-muted text-muted-foreground" : "bg-primary/10 text-primary"
    }`}
  >
    {name.trim().charAt(0).toUpperCase() || "?"}
  </span>
);

const StatusBadge: React.FC<{ readonly label: string }> = ({ label }) => (
  <span className="inline-flex shrink-0 items-center gap-1 rounded-full border border-border bg-muted px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
    <Archive className="h-3 w-3" aria-hidden="true" />
    {label}
  </span>
);

/** Owners and admins: the organization's archived workspaces, each with Restore. */
const ArchivedWorkspaces: React.FC<{
  readonly organization: OrganizationMembershipSummary;
  readonly onRestored: () => void;
}> = ({ organization, onRestored }) => {
  const queryClient = useQueryClient();
  const queryKey = ["organizations", organization.organization_id, "workspaces", "with-archived"];
  const archived = useQuery({
    queryKey,
    queryFn: () => listOrganizationWorkspaces(organization.organization_id, { includeArchived: true }),
    select: (rows) => rows.filter((row) => row.status === "ARCHIVED"),
    staleTime: 30_000,
  });
  const restore = useMutation({
    mutationFn: (workspace: { id: string; workspace_name: string }) => restoreWorkspace(workspace.id),
    onSuccess: async (_updated, workspace) => {
      toast.success(`${workspace.workspace_name} restored.`);
      await queryClient.invalidateQueries({ queryKey });
      onRestored();
    },
    onError: (error: unknown) => toast.error(failureMessage(error, "The workspace could not be restored.")),
  });

  if (archived.isLoading || archived.isError || !archived.data?.length) {
    return null;
  }
  return (
    <section
      aria-label={`Archived workspaces in ${organization.organization_name}`}
      className="space-y-1.5 border-t border-border/70 pt-3"
    >
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        Archived workspaces
      </h3>
      <ul className="space-y-1.5">
        {archived.data.map((workspace) => (
          <li
            key={workspace.id}
            className="flex items-center justify-between gap-3 rounded-lg border border-dashed border-border px-3 py-2 text-sm"
          >
            <span className="flex min-w-0 items-center gap-2">
              <span className="truncate text-muted-foreground">{workspace.workspace_name}</span>
              <StatusBadge label="archived" />
            </span>
            <button
              type="button"
              onClick={() => restore.mutate(workspace)}
              disabled={restore.isPending}
              aria-label={`Restore ${workspace.workspace_name}`}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-semibold text-foreground transition hover:bg-muted disabled:opacity-60"
            >
              {restore.isPending && restore.variables?.id === workspace.id ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <ArchiveRestore className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              Restore
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
};

const ActiveOrganizationCard: React.FC<{
  readonly organization: OrganizationMembershipSummary;
  readonly onChanged: () => void;
}> = ({ organization, onChanged }) => {
  const canCreate = canCreateWorkspace(organization.role);
  const stranded = organization.workspaces.length === 0 && !canCreate;

  const { mutate: leave, isPending: isLeaving } = useMutation({
    mutationFn: () => leaveOrganization(organization.organization_id),
    onSuccess: () => {
      toast.success(`You have left ${organization.organization_name}.`);
      onChanged();
    },
    onError: (error: unknown) =>
      toast.error(failureMessage(error, "Could not leave this organization. Please try again.")),
  });

  return (
    <article
      aria-label={organization.organization_name}
      className="fp-card space-y-3 p-5"
    >
      <header className="flex items-center gap-3">
        <Initial name={organization.organization_name} />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-foreground">{organization.organization_name}</h2>
          <p className="truncate text-xs text-muted-foreground">
            {/* Monospace: Inter's contextual alternates draw the x in "6x9" as a multiplication sign. */}
            <span className="font-mono">/{organization.organization_slug}</span> · {organization.role.toLowerCase()}
          </p>
        </div>
      </header>

      {organization.workspaces.length === 0 ? (
        <p className="rounded-lg bg-muted/40 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
          {canCreate
            ? "No workspaces yet. Create one to get started."
            : "You do not have access to any workspace in this organization yet. An organization admin can add you to one."}
        </p>
      ) : (
        <ul className="space-y-1.5">
          {organization.workspaces.map((workspace) => (
            <li key={workspace.id}>
              <Link
                to={workspacePath(organization.organization_slug, workspace.slug)}
                className="group flex items-center justify-between gap-3 rounded-lg border border-border/60 bg-background/60 px-3 py-2.5 text-sm transition hover:border-border-strong hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                <span className="min-w-0 truncate font-semibold text-foreground">{workspace.workspace_name}</span>
                <span className="flex shrink-0 items-center gap-2 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
                  {workspace.effective_role.toLowerCase()}
                  <ChevronRight className="h-3.5 w-3.5 transition group-hover:translate-x-0.5" aria-hidden="true" />
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}

      {canCreate && (
        <Link
          to={createWorkspacePath(organization.organization_slug)}
          className="flex items-center gap-2 rounded-lg border border-dashed border-border px-3 py-2 text-xs font-semibold text-muted-foreground transition hover:border-primary/50 hover:text-foreground"
        >
          <Plus className="h-3.5 w-3.5" aria-hidden="true" />
          New workspace
        </Link>
      )}

      {canCreate && <ArchivedWorkspaces organization={organization} onRestored={onChanged} />}

      {stranded && (
        <button
          type="button"
          onClick={() => leave()}
          disabled={isLeaving}
          className="flex w-full items-center justify-center gap-2 rounded-lg border border-border px-3 py-2 text-xs font-semibold text-muted-foreground transition hover:border-destructive/50 hover:text-destructive disabled:opacity-60"
        >
          {isLeaving ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <LogOut className="h-3.5 w-3.5" />}
          {isLeaving ? "Leaving..." : "Leave organization"}
        </button>
      )}
    </article>
  );
};

const ArchivedOrganizationCard: React.FC<{
  readonly organization: OrganizationMembershipSummary;
  readonly onChanged: () => void;
}> = ({ organization, onChanged }) => {
  const canRestore = canDeleteOrganization(organization.role);
  const [confirming, setConfirming] = useState(false);
  const [typed, setTyped] = useState("");
  const inputId = useId();
  const restore = useMutation({
    mutationFn: () => restoreOrganization(organization.organization_id, typed.trim()),
    onSuccess: () => {
      toast.success(`${organization.organization_name} restored.`);
      setConfirming(false);
      onChanged();
    },
    onError: (error: unknown) => toast.error(failureMessage(error, "The organization could not be restored.")),
  });

  return (
    <article aria-label={organization.organization_name} className="space-y-3 rounded-xl border border-dashed border-border bg-card/50 p-5">
      <header className="flex items-center gap-3">
        <Initial name={organization.organization_name} muted />
        <div className="min-w-0 flex-1">
          <h2 className="truncate text-sm font-semibold text-muted-foreground">{organization.organization_name}</h2>
          <p className="truncate text-xs text-muted-foreground">
            {/* Monospace: Inter's contextual alternates draw the x in "6x9" as a multiplication sign. */}
            <span className="font-mono">/{organization.organization_slug}</span> · {organization.role.toLowerCase()}
          </p>
        </div>
        <StatusBadge label={organization.organization_status.toLowerCase()} />
      </header>
      <p className="text-xs leading-relaxed text-muted-foreground">
        {organization.organization_status === "ARCHIVED"
          ? "Archived: its documents are kept, but no one can open its workspaces, and its API keys, automations and integrations are off."
          : "Suspended by FlowPilot. Its documents are kept; contact support to lift the suspension."}{" "}
        {organization.organization_status === "ARCHIVED" &&
          (canRestore ? "You can restore it." : "An owner can restore it.")}
      </p>
      {organization.workspaces.length > 0 && (
        <ul className="flex flex-wrap gap-1.5" aria-label="Its workspaces">
          {organization.workspaces.map((workspace) => (
            <li key={workspace.id} className="rounded-md bg-muted px-2 py-0.5 text-xs text-muted-foreground">
              {workspace.workspace_name}
            </li>
          ))}
        </ul>
      )}

      {canRestore && organization.organization_status === "ARCHIVED" && !confirming && (
        <button
          type="button"
          onClick={() => {
            setTyped("");
            setConfirming(true);
          }}
          className="inline-flex items-center gap-1.5 rounded-lg border border-border bg-card px-3 py-1.5 text-xs font-semibold text-foreground transition hover:bg-muted"
        >
          <ArchiveRestore className="h-3.5 w-3.5" aria-hidden="true" />
          Restore organization
        </button>
      )}
      {confirming && (
        <form
          className="space-y-2 rounded-lg border border-border bg-background/60 p-3"
          onSubmit={(event) => {
            event.preventDefault();
            restore.mutate();
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") {
              setConfirming(false);
            }
          }}
        >
          <label htmlFor={inputId} className="block text-xs text-muted-foreground">
            Type <strong className="font-mono text-foreground">{organization.organization_slug}</strong> to confirm.
            Everyone&apos;s access returns; its API keys stay off until you issue new ones.
          </label>
          <input
            id={inputId}
            aria-label="Type the organization's slug to confirm"
            value={typed}
            autoFocus
            onChange={(event) => setTyped(event.target.value)}
            className="w-full rounded-md border border-input bg-background px-2.5 py-1.5 font-mono text-sm"
          />
          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setConfirming(false)}
              className="rounded-md px-3 py-1.5 text-xs font-semibold text-muted-foreground hover:bg-muted"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={typed.trim() !== organization.organization_slug || restore.isPending}
              className="fp-btn fp-btn-primary px-3 py-1.5 text-xs"
            >
              {restore.isPending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
              Restore
            </button>
          </div>
        </form>
      )}
    </article>
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
      <div className="flex min-h-screen w-full justify-center bg-background px-6 py-12" role="status" aria-label="Loading your workspaces">
        <div className="w-full max-w-lg space-y-4">
          <div className="h-7 w-48 animate-pulse rounded bg-muted" />
          <div className="h-4 w-64 animate-pulse rounded bg-muted" />
          {[0, 1].map((index) => (
            <div key={index} className="fp-card h-32 animate-pulse" />
          ))}
        </div>
      </div>
    );
  }

  if (state.status === "unauthenticated") {
    return <Navigate to={loginPath} replace />;
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
    state.status === "ready" || state.status === "no_workspace" ? state.organizations : [];
  const active = organizations.filter((organization) => organization.organization_status === "ACTIVE");
  const inactive = organizations.filter((organization) => organization.organization_status !== "ACTIVE");

  return (
    <div className="flex min-h-screen w-full justify-center bg-background px-4 py-10 sm:px-6 sm:py-14">
      <div className="w-full max-w-lg space-y-6">
        <header className="space-y-1.5">
          <h1 className="text-2xl font-semibold tracking-tight text-foreground">Choose a workspace</h1>
          <p className="text-sm text-muted-foreground">Select where you want to work.</p>
        </header>

        {unreachable && (
          <div role="status" className="rounded-xl border border-amber-500/30 bg-amber-500/5 px-4 py-3">
            <p className="text-sm leading-relaxed text-foreground">{UNREACHABLE_MESSAGE[unreachable]}</p>
          </div>
        )}

        {state.status === "error" && (
          <div role="alert" className="flex items-start justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 px-4 py-3">
            <p className="text-sm leading-relaxed text-foreground">
              We could not load your organizations. This is a connection problem, not a change to your account.
            </p>
            <button
              type="button"
              onClick={() => refresh()}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1 text-xs font-semibold hover:bg-muted"
            >
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Try again
            </button>
          </div>
        )}

        {active.length > 0 && (
          <div className="space-y-4">
            {active.map((organization) => (
              <ActiveOrganizationCard key={organization.organization_id} organization={organization} onChanged={refresh} />
            ))}
          </div>
        )}

        <Link
          to={ROUTES.NEW_ORGANIZATION}
          className="flex items-center gap-3 rounded-xl border border-dashed border-border px-4 py-3.5 transition hover:border-primary/50 hover:bg-card"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-muted">
            <Plus className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </span>
          <span className="space-y-0.5">
            <span className="block text-sm font-semibold text-foreground">Create a new organization</span>
            <span className="block text-xs text-muted-foreground">
              Start a separate company with its own team and billing.
            </span>
          </span>
        </Link>

        {inactive.length > 0 && (
          <section aria-label="Archived organizations" className="space-y-3">
            <h2 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
              Archived and suspended
            </h2>
            {inactive.map((organization) => (
              <ArchivedOrganizationCard key={organization.organization_id} organization={organization} onChanged={refresh} />
            ))}
          </section>
        )}

        {organizations.length === 0 && state.status !== "error" && (
          <div className="flex flex-col items-center gap-2 py-6 text-center">
            <Building2 className="h-5 w-5 text-muted-foreground" aria-hidden="true" />
            <p className="text-sm text-muted-foreground">You do not belong to any organization yet.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default WorkspacePicker;
