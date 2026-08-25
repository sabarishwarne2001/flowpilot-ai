import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { Building2, Check, ChevronsUpDown } from "lucide-react";

import { useResolvedTenant } from "@/routes/TenantContext";
import { rebaseTenantPath } from "@/routes/tenantPaths";
import { useSessionGuardStore } from "@/store/useSessionGuardStore";

export interface TenantSwitcherProps {
  readonly collapsed?: boolean;
}

export const TenantSwitcher: React.FC<TenantSwitcherProps> = ({
  collapsed = false,
}) => {
  const navigate = useNavigate();
  const location = useLocation();
  const queryClient = useQueryClient();
  const resetSessionGuards = useSessionGuardStore(
    (state) => state.resetSessionGuards,
  );

  const { organization, workspace, organizations } = useResolvedTenant();

  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onPointerDown = (event: MouseEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const entries = useMemo(
    () =>
      organizations.flatMap((org) =>
        org.workspaces.map((ws) => ({
          organizationSlug: org.organization_slug,
          organizationName: org.organization_name,
          workspaceSlug: ws.slug,
          workspaceName: ws.workspace_name,
          workspaceId: ws.id,
        })),
      ),
    [organizations],
  );

  const handleSelect = useCallback(
    (organizationSlug: string, workspaceSlug: string, workspaceId: string) => {
      setOpen(false);

      if (workspaceId === workspace.id) {
        return;
      }

      queryClient.clear();
      resetSessionGuards();

      const destination = rebaseTenantPath(
        location.pathname,
        organizationSlug,
        workspaceSlug,
      );

      navigate(destination);
    },
    [
      location.pathname,
      navigate,
      queryClient,
      resetSessionGuards,
      workspace.id,
    ],
  );

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((previous) => !previous)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex w-full items-center gap-2 rounded-md border border-border px-2 py-2 text-left hover:bg-muted"
      >
        <Building2
          className="h-4 w-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />

        {!collapsed && (
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">
              {workspace.workspace_name}
            </span>
            <span className="block truncate text-xs text-muted-foreground">
              {organization.organization_name}
            </span>
          </span>
        )}

        {!collapsed && (
          <ChevronsUpDown
            className="h-4 w-4 shrink-0 text-muted-foreground"
            aria-hidden="true"
          />
        )}
      </button>

      {open && (
        <ul
          role="listbox"
          aria-label="Switch workspace"
          className="absolute z-40 mt-1 max-h-80 w-full min-w-[15rem] overflow-y-auto rounded-md border border-border bg-popover p-1 shadow-lg"
        >
          {entries.map((entry) => {
            const selected = entry.workspaceId === workspace.id;
            return (
              <li key={entry.workspaceId}>
                <button
                  type="button"
                  role="option"
                  aria-selected={selected}
                  onClick={() =>
                    handleSelect(
                      entry.organizationSlug,
                      entry.workspaceSlug,
                      entry.workspaceId,
                    )
                  }
                  className="flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-sm hover:bg-muted"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate">
                      {entry.workspaceName}
                    </span>
                    <span className="block truncate text-xs text-muted-foreground">
                      {entry.organizationName}
                    </span>
                  </span>
                  {selected && (
                    <Check
                      className="h-4 w-4 shrink-0 text-primary"
                      aria-hidden="true"
                    />
                  )}
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
};

export default TenantSwitcher;
