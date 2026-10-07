import React, { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  FloatingFocusManager,
  FloatingPortal,
  autoUpdate,
  flip,
  offset,
  shift,
  size,
  useClick,
  useDismiss,
  useFloating,
  useInteractions,
  useRole,
} from "@floating-ui/react";
import { Building2, Check, ChevronsUpDown, Plus, Search } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { useResolvedTenant } from "@/routes/TenantContext";
import { createWorkspacePath, rebaseTenantPath } from "@/routes/tenantPaths";
import { canCreateWorkspace } from "@/permissions/organizationPermissions";
import { WorkspaceLogo } from "@/components/workspace/WorkspaceLogo";
import { Brand } from "@/components/branding/Brand";

interface OrgWorkspaceSwitcherProps {
  readonly collapsed?: boolean;
}

const ROLE_TONE: Readonly<Record<string, string>> = {
  owner: "border-primary/30 bg-primary/10 text-primary",
  admin: "border-violet-500/30 bg-violet-500/10 text-violet-600 dark:text-violet-300",
};

/**
 * The workspace identity at the top of the sidebar, and the control for going
 * to another workspace or organization.
 *
 * The list renders in a portal positioned by floating-ui, so the sidebar's
 * scroll container can no longer clip it or trap it behind the page, and it
 * carries a filter for people who belong to many organizations.
 */
export const OrgWorkspaceSwitcher: React.FC<OrgWorkspaceSwitcherProps> = ({
  collapsed = false,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const { organization, workspace, organizations, organizationRole } =
    useResolvedTenant();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const searchRef = useRef<HTMLInputElement>(null);

  const { refs, floatingStyles, context } = useFloating({
    open,
    onOpenChange: (next) => {
      setOpen(next);
      if (!next) {
        setQuery("");
      }
    },
    placement: collapsed ? "right-start" : "bottom-start",
    middleware: [
      offset(collapsed ? 10 : 6),
      flip({ padding: 8 }),
      shift({ padding: 8 }),
      size({
        padding: 8,
        apply({ availableHeight, elements }) {
          elements.floating.style.maxHeight = `${Math.max(240, Math.min(availableHeight, 520))}px`;
        },
      }),
    ],
    whileElementsMounted: autoUpdate,
  });
  const { getReferenceProps, getFloatingProps } = useInteractions([
    useClick(context),
    useDismiss(context),
    useRole(context, { role: "dialog" }),
  ]);

  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  // Only ACTIVE organizations are switchable.
  //
  // This dropdown is the one place inside the app that offers a jump into
  // another tenant, and an archived one cannot be entered: TenantGuard turns
  // it away and deps.OrgContext refuses every request underneath. Listing it
  // offers a destination that does not exist -- the actor clicks, the app
  // bounces them to the picker, and nothing explains why.
  //
  // The workspace picker at /workspaces still shows archived organizations,
  // greyed and inert. That is the right place for them: it is an inventory of
  // what you belong to, whereas this is a control for going somewhere.
  const switchableOrganizations = useMemo(
    () => organizations.filter((org) => org.organization_status === "ACTIVE"),
    [organizations],
  );

  const totalWorkspaces = useMemo(
    () => switchableOrganizations.reduce((sum, org) => sum + org.workspaces.length, 0),
    [switchableOrganizations],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) {
      return switchableOrganizations;
    }
    return switchableOrganizations
      .map((org) => {
        const orgMatches = org.organization_name.toLowerCase().includes(needle);
        const workspaces = orgMatches
          ? org.workspaces
          : org.workspaces.filter((ws) => ws.workspace_name.toLowerCase().includes(needle));
        return { ...org, workspaces };
      })
      .filter((org) => org.workspaces.length > 0);
  }, [switchableOrganizations, query]);

  const canCreate = canCreateWorkspace(organizationRole);
  const showSearch = totalWorkspaces > 5 || switchableOrganizations.length > 2;

  // Nothing to switch to and nothing to create: the identity alone.
  if (totalWorkspaces <= 1 && switchableOrganizations.length <= 1 && !canCreate) {
    return (
      <div className={collapsed ? "flex justify-center" : "px-1"}>
        <Brand variant={collapsed ? "sidebar-compact" : "sidebar"} />
      </div>
    );
  }

  const handleSelect = (orgSlug: string, workspaceSlug: string): void => {
    setOpen(false);
    if (orgSlug === organization.organization_slug && workspaceSlug === workspace.slug) {
      return;
    }
    navigate(rebaseTenantPath(location.pathname, orgSlug, workspaceSlug));
  };

  const logo = (sizeClass: string, text: string) => (
    <WorkspaceLogo
      workspace={workspace}
      className={`${sizeClass} shrink-0 rounded-md object-cover ring-1 ring-border-strong/60`}
      fallbackClassName={`flex ${sizeClass} shrink-0 items-center justify-center rounded-md bg-gradient-to-br from-primary to-violet-500 ${text} font-semibold text-white shadow-inner-highlight`}
    />
  );

  return (
    <>
      {collapsed ? (
        <button
          type="button"
          ref={refs.setReference}
          {...getReferenceProps()}
          aria-label="Switch workspace"
          title={`${organization.organization_name} · ${workspace.workspace_name}`}
          className={`mx-auto flex h-9 w-9 items-center justify-center rounded-lg hover:bg-accent ${open ? "bg-accent" : ""}`}
        >
          {logo("h-7 w-7", "text-[11px]")}
        </button>
      ) : (
        <button
          type="button"
          ref={refs.setReference}
          {...getReferenceProps()}
          className={`group flex w-full min-w-0 items-center gap-2.5 rounded-lg px-2 py-1.5 text-left hover:bg-accent/70 ${
            open ? "bg-accent/70" : ""
          }`}
        >
          {logo("h-8 w-8", "text-xs")}
          <span className="min-w-0 flex-1">
            <span className="block truncate text-[13px] font-semibold leading-5 text-foreground">
              {workspace.workspace_name}
            </span>
            <span className="block truncate text-[11px] leading-4 text-muted-foreground">
              {organization.organization_name}
            </span>
          </span>
          <ChevronsUpDown className="h-3.5 w-3.5 shrink-0 text-muted-foreground/70 group-hover:text-muted-foreground" aria-hidden="true" />
        </button>
      )}

      {open && (
        <FloatingPortal>
          <FloatingFocusManager context={context} modal={false} initialFocus={showSearch ? searchRef : -1}>
            <div
              ref={refs.setFloating}
              style={floatingStyles}
              {...getFloatingProps()}
              aria-label="Switch workspace"
              className="fp-popover z-[60] flex w-72 flex-col overflow-hidden"
            >
              {showSearch && (
                <div className="flex items-center gap-2 border-b border-border px-3 py-2">
                  <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" aria-hidden="true" />
                  <input
                    ref={searchRef}
                    type="search"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    placeholder="Find a workspace…"
                    aria-label="Find a workspace"
                    className="w-full border-0 bg-transparent p-0 text-sm text-foreground shadow-none placeholder:text-muted-foreground/70 focus-visible:shadow-none focus-visible:ring-0"
                  />
                </div>
              )}

              <div role="listbox" aria-label="Workspaces" className="min-h-0 flex-1 overflow-y-auto p-1.5">
                {filtered.length === 0 && (
                  <p className="px-2 py-6 text-center text-xs text-muted-foreground">No workspace matches “{query}”.</p>
                )}
                {filtered.map((org) => (
                  <div key={org.organization_id} role="group" aria-label={org.organization_name} className="mb-1 last:mb-0">
                    <p className="fp-eyebrow truncate px-2 pb-1 pt-1.5 text-[10px]">{org.organization_name}</p>

                    {org.workspaces.length === 0 ? (
                      <p className="px-2 pb-1.5 text-xs text-muted-foreground/70">No workspaces you can access</p>
                    ) : (
                      org.workspaces.map((ws) => {
                        const isCurrent =
                          ws.id === workspace.id && org.organization_id === organization.organization_id;
                        const roleKey = ws.effective_role.toLowerCase();
                        return (
                          <button
                            key={ws.id}
                            type="button"
                            role="option"
                            aria-selected={isCurrent}
                            onClick={() => handleSelect(org.organization_slug, ws.slug)}
                            className={`flex w-full items-center gap-2.5 rounded-md px-2 py-1.5 text-left text-sm ${
                              isCurrent ? "bg-accent text-foreground" : "text-foreground/85 hover:bg-accent/70 hover:text-foreground"
                            }`}
                          >
                            <span
                              aria-hidden="true"
                              className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-border-strong/60 bg-muted text-[10px] font-semibold text-muted-foreground"
                            >
                              {ws.workspace_name.charAt(0).toUpperCase()}
                            </span>
                            <span className="min-w-0 flex-1 truncate font-medium">{ws.workspace_name}</span>
                            <span
                              className={`shrink-0 rounded border px-1.5 text-[10px] font-medium uppercase leading-4 tracking-wide ${
                                ROLE_TONE[roleKey] ?? "border-border-strong/60 bg-muted/60 text-muted-foreground"
                              }`}
                            >
                              {roleKey}
                            </span>
                            <Check
                              className={`h-3.5 w-3.5 shrink-0 text-primary ${isCurrent ? "opacity-100" : "opacity-0"}`}
                              aria-hidden="true"
                            />
                          </button>
                        );
                      })
                    )}
                  </div>
                ))}
              </div>

              <div className="space-y-0.5 border-t border-border bg-muted/30 p-1.5">
                {canCreate && (
                  <button
                    type="button"
                    onClick={() => {
                      setOpen(false);
                      navigate(createWorkspacePath(organization.organization_slug));
                    }}
                    className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground/85 hover:bg-accent hover:text-foreground"
                  >
                    <span className="flex h-6 w-6 items-center justify-center rounded-md border border-dashed border-border-strong text-muted-foreground">
                      <Plus className="h-3.5 w-3.5" aria-hidden="true" />
                    </span>
                    <span className="min-w-0 truncate font-medium">New workspace in {organization.organization_name}</span>
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false);
                    navigate(ROUTES.NEW_ORGANIZATION);
                  }}
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm text-foreground/85 hover:bg-accent hover:text-foreground"
                >
                  <span className="flex h-6 w-6 items-center justify-center rounded-md border border-dashed border-border-strong text-muted-foreground">
                    <Building2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </span>
                  <span className="font-medium">Create organization</span>
                </button>
              </div>
            </div>
          </FloatingFocusManager>
        </FloatingPortal>
      )}
    </>
  );
};

export default OrgWorkspaceSwitcher;
