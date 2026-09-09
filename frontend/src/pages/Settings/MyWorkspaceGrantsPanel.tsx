import React from "react";
import { useQuery } from "@tanstack/react-query";
import { FolderOpen, Loader2 } from "lucide-react";

import { getMyWorkspaces } from "@/services/api/me";
import { useTenant } from "@/hooks/useTenant";

/**
 * ARCH-29 Slice 3 — the actor's explicit workspace grants.
 *
 * THE DISTINCTION THIS PANEL IS ABOUT
 * ===================================
 *
 * `getMyWorkspaces` is NOT the workspace list in the switcher, and the
 * difference is the entire reason it earns a network call when most of this
 * slice did not. Its contract is explicit: every workspace the actor holds a
 * direct grant on, deliberately excluding workspaces reachable only through
 * organization-level derived elevation. `/me/context` — which the switcher and
 * every guard read from — returns the complete reachable set.
 *
 * So the two disagree, correctly, and the disagreement is informative:
 *
 *   An organization OWNER of a tenant with thirty workspaces reaches all
 *   thirty and may hold a direct grant on none of them.
 *
 * That is exactly the case where a naive panel does damage. "Your workspaces:
 * none" next to a switcher listing thirty reads as a bug, and the person most
 * likely to see it is the account owner. So an empty list here is never shown
 * bare — it is shown with the reason, and the reachable count is shown beside
 * it so the two numbers are visibly about different things rather than
 * apparently contradicting each other.
 */

const ROLE_LABELS: Readonly<Record<string, string>> = {
  VIEWER: "Viewer",
  CONTRIBUTOR: "Contributor",
  ADMIN: "Admin",
};

export const MyWorkspaceGrantsPanel: React.FC = () => {
  const { state } = useTenant();

  const grants = useQuery({
    queryKey: ["me", "workspaces", "explicit"],
    queryFn: getMyWorkspaces,
    staleTime: 60_000,
  });

  // The reachable set, from the same bootstrap context the switcher uses.
  const reachable =
    state.status === "ready" || state.status === "no_workspace"
      ? state.organizations.reduce(
          (total, organization) => total + organization.workspaces.length,
          0,
        )
      : null;

  const items = grants.data ?? [];

  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="flex items-start gap-3">
        <FolderOpen
          className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden
        />
        <div>
          <h2 className="text-lg font-bold tracking-tight text-foreground">
            Your workspace access
          </h2>
          <p className="mt-0.5 text-sm text-muted-foreground">
            Workspaces you were added to directly. This is not the same as
            everything you can open — an organization owner or admin reaches
            every workspace in their organization without being added to any.
          </p>
        </div>
      </div>

      {grants.isLoading ? (
        <p className="mt-3 flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
          Loading…
        </p>
      ) : grants.isError ? (
        <p className="mt-3 text-sm text-destructive" role="alert">
          Your workspace grants could not be loaded. Reload the page to try
          again.
        </p>
      ) : items.length === 0 ? (
        <p className="mt-3 rounded-lg border border-border bg-muted/40 p-3 text-sm text-muted-foreground">
          You hold no direct workspace grants.
          {reachable !== null && reachable > 0 ? (
            <>
              {" "}
              You can still open {reachable}{" "}
              {reachable === 1 ? "workspace" : "workspaces"} through your
              organization role — that access comes from the organization, not
              from being added to each workspace individually.
            </>
          ) : null}
        </p>
      ) : (
        <>
          <ul className="mt-3 divide-y divide-border rounded-lg border border-border">
            {items.map((workspace) => (
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
                <div className="flex items-center gap-2">
                  {workspace.status !== "ACTIVE" ? (
                    <span className="rounded border border-amber-200 bg-amber-50 px-2 py-0.5 text-xs text-amber-800">
                      {workspace.status}
                    </span>
                  ) : null}
                  <span className="rounded border border-border px-2 py-0.5 text-xs text-muted-foreground">
                    {ROLE_LABELS[workspace.effective_role] ??
                      workspace.effective_role}
                  </span>
                </div>
              </li>
            ))}
          </ul>
          {reachable !== null && reachable > items.length ? (
            <p className="mt-2 text-xs text-muted-foreground">
              You can open {reachable} in total; the rest come from your
              organization role rather than a direct grant.
            </p>
          ) : null}
        </>
      )}
    </div>
  );
};

export default MyWorkspaceGrantsPanel;
