import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Building2, Check, ChevronsUpDown, Plus } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useResolvedTenant } from "@/routes/TenantContext";
import { rebaseTenantPath } from "@/routes/tenantPaths";

/**
 * Organization and workspace switcher for FlowPilot AI.
 *
 * This component could not exist before ARCH-01. A second active membership
 * raised MultipleResultsFound on the backend and returned HTTP 500 on every
 * subsequent request, so there was never more than one tenant to switch
 * between.
 *
 * SWITCHING IS NAVIGATION, NOT STATE
 *
 * Selecting a workspace navigates to its URL. TenantGuard then reconciles the
 * new path against the bootstrap context and writes the selection to the
 * store. Mutating the store directly and letting the URL follow would give two
 * sources of truth for "which tenant am I in", and the address bar would lag
 * behind the content — the exact class of drift ARCH-01 removed by putting the
 * tenant in the path.
 *
 * rebaseTenantPath carries the current sub-page across, so switching from
 * /acme/eng/work-items lands on /beta/main/work-items rather than dumping the
 * user back on a dashboard. Note this is rebaseTenantPath, NOT toTenantPath:
 * the current path already carries a tenant prefix to strip, which is the
 * distinction those two functions exist to keep apart.
 *
 * Every workspace shown comes from /me/context, which the server has already
 * filtered by access — organization OWNER and ADMIN see every workspace
 * through their derived grant, everyone else sees only explicit ones. Nothing
 * is filtered client-side.
 */

interface OrgWorkspaceSwitcherProps {
  readonly collapsed?: boolean;
}

export const OrgWorkspaceSwitcher: React.FC<OrgWorkspaceSwitcherProps> = ({
  collapsed = false,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { organization, workspace, organizations } = useResolvedTenant();

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  // Close on outside click and on Escape. A dropdown that traps focus or
  // survives a click elsewhere is a persistent annoyance in a component this
  // frequently used.
  useEffect(() => {
    if (!open) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    document.addEventListener("keydown", handleKeyDown);

    return () => {
      document.removeEventListener("mousedown", handlePointerDown);
      document.removeEventListener("keydown", handleKeyDown);
    };
  }, [open]);

  // Close when the route changes, so the panel does not linger over the page
  // the user just navigated to.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  const totalWorkspaces = useMemo(
    () => organizations.reduce((sum, org) => sum + org.workspaces.length, 0),
    [organizations],
  );

  // A single workspace has nothing to switch to. Rendering a dropdown that
  // offers one option is noise in the most valuable space in the sidebar.
  if (totalWorkspaces <= 1 && organizations.length <= 1) {
    return null;
  }

  const handleSelect = (orgSlug: string, workspaceSlug: string): void => {
    setOpen(false);

    if (
      orgSlug === organization.organization_slug &&
      workspaceSlug === workspace.slug
    ) {
      return;
    }

    navigate(rebaseTenantPath(location.pathname, orgSlug, workspaceSlug));
  };

  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => navigate(ROUTES.WORKSPACES)}
        title={`${organization.organization_name} · ${workspace.workspace_name}`}
        aria-label="Switch workspace"
        className="mx-auto flex h-9 w-9 items-center justify-center rounded-lg border border-border bg-background text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
      >
        <Building2 className="h-4 w-4" />
      </button>
    );
  }

  return (
    <div ref={containerRef} className="relative px-3 py-2">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 rounded-lg border border-border bg-background px-3 py-2 text-left transition-colors hover:bg-muted/50"
      >
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-bold text-foreground">
            {workspace.workspace_name}
          </span>
          <span className="block truncate text-[11px] text-muted-foreground">
            {organization.organization_name}
          </span>
        </span>

        <ChevronsUpDown className="h-4 w-4 shrink-0 text-muted-foreground" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-3 right-3 z-50 mt-1 max-h-[60vh] overflow-y-auto rounded-lg border border-border bg-card p-1.5 shadow-lg"
        >
          {organizations.map((org) => (
            <div key={org.organization_id} className="mb-1 last:mb-0">
              <p className="px-2 py-1.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                {org.organization_name}
              </p>

              {org.workspaces.length === 0 ? (
                <p className="px-2 pb-1.5 text-xs text-muted-foreground/70">
                  No workspaces you can access
                </p>
              ) : (
                org.workspaces.map((ws) => {
                  const isCurrent =
                    ws.id === workspace.id &&
                    org.organization_id === organization.organization_id;

                  return (
                    <button
                      key={ws.id}
                      type="button"
                      role="option"
                      aria-selected={isCurrent}
                      onClick={() =>
                        handleSelect(org.organization_slug, ws.slug)
                      }
                      className={`flex w-full items-center justify-between gap-2 rounded-md px-2 py-2 text-left text-sm transition-colors ${
                        isCurrent
                          ? "bg-primary/10 text-foreground"
                          : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
                      }`}
                    >
                      <span className="min-w-0 flex-1 truncate font-medium">
                        {ws.workspace_name}
                      </span>

                      <span className="shrink-0 text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/70">
                        {ws.effective_role.toLowerCase()}
                      </span>

                      {isCurrent && (
                        <Check className="h-3.5 w-3.5 shrink-0 text-primary" />
                      )}
                    </button>
                  );
                })
              )}
            </div>
          ))}

          <div className="mt-1 border-t border-border/60 pt-1">
            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate(ROUTES.ONBOARDING);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            >
              <Plus className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium">Create organization</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default OrgWorkspaceSwitcher;
