import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { Building2, Check, ChevronsUpDown, Plus } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useResolvedTenant } from "@/routes/TenantContext";
import { createWorkspacePath, rebaseTenantPath } from "@/routes/tenantPaths";
import { canCreateWorkspace } from "@/permissions/organizationPermissions";
import { WorkspaceLogo } from "@/components/workspace/WorkspaceLogo";

interface OrgWorkspaceSwitcherProps {
  readonly collapsed?: boolean;
}

export const OrgWorkspaceSwitcher: React.FC<OrgWorkspaceSwitcherProps> = ({
  collapsed = false,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { organization, workspace, organizations, organizationRole } =
    useResolvedTenant();

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

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

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  const totalWorkspaces = useMemo(
    () => organizations.reduce((sum, org) => sum + org.workspaces.length, 0),
    [organizations],
  );

  const canCreate = canCreateWorkspace(organizationRole);

  if (totalWorkspaces <= 1 && organizations.length <= 1 && !canCreate) {
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
        <WorkspaceLogo
          workspace={workspace}
          className="h-6 w-6 rounded object-cover"
          fallbackClassName="flex h-6 w-6 items-center justify-center rounded bg-primary/10 text-[10px] font-bold text-primary"
        />
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
        <div className="flex min-w-0 flex-1 items-center gap-2.5">
          <WorkspaceLogo
            workspace={workspace}
            className="h-7 w-7 shrink-0 rounded object-cover border border-border"
            fallbackClassName="flex h-7 w-7 shrink-0 items-center justify-center rounded bg-primary/10 text-xs font-bold text-primary"
          />
          <div className="min-w-0 flex-1">
            <span className="block truncate text-sm font-bold text-foreground">
              {workspace.workspace_name}
            </span>
            <span className="block truncate text-[11px] text-muted-foreground">
              {organization.organization_name}
            </span>
          </div>
        </div>

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

          <div className="mt-1 space-y-0.5 border-t border-border/60 pt-1">
            {canCreate && (
              <button
                type="button"
                onClick={() => {
                  setOpen(false);
                  navigate(createWorkspacePath(organization.organization_slug));
                }}
                className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
              >
                <Plus className="h-3.5 w-3.5 shrink-0" />
                <span className="font-medium">
                  New workspace in {organization.organization_name}
                </span>
              </button>
            )}

            <button
              type="button"
              onClick={() => {
                setOpen(false);
                navigate(ROUTES.ONBOARDING);
              }}
              className="flex w-full items-center gap-2 rounded-md px-2 py-2 text-left text-sm text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            >
              <Building2 className="h-3.5 w-3.5 shrink-0" />
              <span className="font-medium">Create organization</span>
            </button>
          </div>
        </div>
      )}
    </div>
  );
};

export default OrgWorkspaceSwitcher;
