import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { CornerDownLeft, Lock, Search, type LucideIcon } from "lucide-react";

import {
  buildCreateWorkspaceItem,
  buildOrganizationNavigationItems,
  buildPartnerNavigationItems,
  buildPlatformNavigationItems,
  buildWorkspaceNavigationGroups,
  buildWorkspaceShortcutItems,
} from "./navigation";
import { OPEN_COMMAND_PALETTE_EVENT } from "./commandPaletteEvents";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { useIsPartnerMember } from "@/hooks/useIsPartnerMember";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { useIsSuperAdmin } from "@/routes/SuperAdminGuard";
import { OVERLAY, SURFACE_DIALOG } from "@/components/ui/primitives";

interface PaletteEntry {
  readonly key: string;
  readonly name: string;
  readonly section: string;
  readonly path: string;
  readonly description: string;
  readonly haystack: string;
  readonly locked: boolean;
  readonly icon: LucideIcon;
}

const MAX_RESULTS = 12;

/**
 * ARCH36-S1:command-palette — jump to any page with Ctrl+K / ⌘K.
 *
 * Renders from the same builders as the sidebar, so a page added to the
 * navigation model is searchable without touching this file, and a page the
 * reader's role cannot open is not offered. Capability-locked pages are
 * offered with a lock, for the same reason the sidebar shows them.
 *
 * Mounted once, in DashboardLayout, beneath TenantGuard: it needs the resolved
 * tenant to build paths, and the workspace shell is the only place it has one.
 *
 * Keyboard: Ctrl+K / ⌘K toggles, ↑/↓ move, Enter opens, Escape closes. Focus
 * returns to whatever held it before the palette opened.
 */
const CommandPalette: React.FC = () => {
  const navigate = useNavigate();
  const { organization, workspace, workspaceRole, organizationRole } =
    useResolvedTenant();
  const capabilities = useGrantedCapabilities(organization.organization_id);
  const isPartnerMember = useIsPartnerMember();
  const isSuperAdmin = useIsSuperAdmin();

  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const restoreFocusRef = useRef<HTMLElement | null>(null);

  const orgSlug = organization.organization_slug;
  const workspaceSlug = workspace.slug;

  const entries = useMemo((): readonly PaletteEntry[] => {
    const out: PaletteEntry[] = [];
    const isLocked = (capability: string | undefined): boolean =>
      capability !== undefined &&
      !capabilities.isLoading &&
      !capabilities.granted.has(capability);

    const groups = buildWorkspaceNavigationGroups(orgSlug, workspaceSlug);
    const shortcuts = buildWorkspaceShortcutItems(orgSlug, workspaceSlug);
    const workspaceItems = [
      ...groups.flatMap((group) =>
        group.items.map((item) => ({ item, section: group.label })),
      ),
      ...shortcuts.map((item) => ({ item, section: "Shortcuts" })),
    ];

    for (const { item, section } of workspaceItems) {
      if (item.minimumRole !== undefined && !isAtLeast(workspaceRole, item.minimumRole)) {
        continue;
      }
      out.push({
        key: `ws:${item.id}`,
        name: item.name,
        section,
        path: item.path,
        description: item.description,
        haystack: [item.name, item.description, section, ...(item.keywords ?? [])]
          .join(" ")
          .toLowerCase(),
        locked: isLocked(item.capability),
        icon: item.icon,
      });
    }

    const organizationSection = organization.organization_name;
    const organizationItems = [
      ...buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
      buildCreateWorkspaceItem(orgSlug),
    ];
    for (const item of organizationItems) {
      out.push({
        key: `org:${item.path}`,
        name: item.name,
        section: organizationSection,
        path: item.path,
        description: `Organization · ${organizationSection}`,
        haystack: `${item.name} organization ${organizationSection}`.toLowerCase(),
        locked: false,
        icon: item.icon,
      });
    }

    for (const item of [
      ...buildPartnerNavigationItems(isPartnerMember),
      ...buildPlatformNavigationItems(isSuperAdmin),
    ]) {
      out.push({
        key: `x:${item.path}`,
        name: item.name,
        section: "Other",
        path: item.path,
        description: item.path,
        haystack: item.name.toLowerCase(),
        locked: false,
        icon: item.icon,
      });
    }

    return out;
  }, [
    orgSlug,
    workspaceSlug,
    workspaceRole,
    organizationRole,
    organization.organization_name,
    capabilities.granted,
    capabilities.isLoading,
    isPartnerMember,
    isSuperAdmin,
  ]);

  const results = useMemo(() => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) {
      return entries.slice(0, MAX_RESULTS);
    }
    return entries
      .map((entry) => {
        if (!terms.every((term) => entry.haystack.includes(term))) {
          return null;
        }
        const name = entry.name.toLowerCase();
        const score = terms.reduce(
          (total, term) =>
            total + (name.startsWith(term) ? 3 : name.includes(term) ? 2 : 1),
          0,
        );
        return { entry, score };
      })
      .filter((row): row is { entry: PaletteEntry; score: number } => row !== null)
      .sort((a, b) => b.score - a.score)
      .slice(0, MAX_RESULTS)
      .map((row) => row.entry);
  }, [entries, query]);

  const close = useCallback(() => {
    setOpen(false);
    setQuery("");
    setActive(0);
    const target = restoreFocusRef.current;
    restoreFocusRef.current = null;
    if (target && typeof target.focus === "function") {
      target.focus();
    }
  }, []);

  const openPalette = useCallback(() => {
    if (document.activeElement instanceof HTMLElement) {
      restoreFocusRef.current = document.activeElement;
    }
    setOpen(true);
  }, []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && !event.altKey && event.key.toLowerCase() === "k") {
        event.preventDefault();
        if (open) {
          close();
        } else {
          openPalette();
        }
      }
    };
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener(OPEN_COMMAND_PALETTE_EVENT, openPalette);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener(OPEN_COMMAND_PALETTE_EVENT, openPalette);
    };
  }, [open, close, openPalette]);

  useEffect(() => {
    if (open) {
      inputRef.current?.focus();
    }
  }, [open]);

  useEffect(() => {
    setActive(0);
  }, [query]);

  const go = (entry: PaletteEntry | undefined) => {
    if (!entry) {
      return;
    }
    close();
    navigate(entry.path);
  };

  const onInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        setActive((index) => (results.length === 0 ? 0 : (index + 1) % results.length));
        break;
      case "ArrowUp":
        event.preventDefault();
        setActive((index) =>
          results.length === 0 ? 0 : (index - 1 + results.length) % results.length,
        );
        break;
      case "Enter":
        event.preventDefault();
        go(results[active]);
        break;
      case "Escape":
        event.preventDefault();
        close();
        break;
      default:
        break;
    }
  };

  if (!open) {
    return null;
  }

  const activeId = results[active] ? `palette-${results[active].key}` : undefined;

  return (
    <div
      className={`${OVERLAY} z-[70] items-start pt-[12vh]`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          close();
        }
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Search pages"
        className={`${SURFACE_DIALOG} w-full max-w-xl overflow-hidden`}
      >
        <div className="flex items-center gap-2 border-b border-border px-4">
          <Search className="h-4 w-4 text-muted-foreground" aria-hidden />
          <input
            ref={inputRef}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={onInputKeyDown}
            placeholder="Jump to a page…"
            className="h-12 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            role="combobox"
            aria-expanded="true"
            aria-controls="command-palette-results"
            aria-activedescendant={activeId}
            aria-autocomplete="list"
          />
          <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold text-muted-foreground">
            Esc
          </kbd>
        </div>

        <ul
          id="command-palette-results"
          role="listbox"
          aria-label="Pages"
          className="max-h-[50vh] overflow-y-auto p-2"
        >
          {results.length === 0 ? (
            <li className="px-3 py-6 text-center text-sm text-muted-foreground">
              No page matches “{query.trim()}”.
            </li>
          ) : (
            results.map((entry, index) => {
              const Icon = entry.icon;
              const selected = index === active;
              return (
                <li
                  key={entry.key}
                  id={`palette-${entry.key}`}
                  role="option"
                  aria-selected={selected}
                  onMouseEnter={() => setActive(index)}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    go(entry);
                  }}
                  className={`flex cursor-pointer items-center gap-3 rounded-lg px-3 py-2 ${
                    selected ? "bg-primary/10 text-foreground" : "text-muted-foreground"
                  }`}
                >
                  <Icon className="h-4 w-4 flex-shrink-0" />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 text-sm font-semibold text-foreground">
                      <span className="truncate">{entry.name}</span>
                      {entry.locked && (
                        <span className="inline-flex items-center gap-1 rounded-full border border-border px-1.5 text-[10px] font-medium text-muted-foreground">
                          <Lock className="h-3 w-3" aria-hidden />
                          Upgrade
                        </span>
                      )}
                    </span>
                    <span className="block truncate text-xs">{entry.description}</span>
                  </span>
                  <span className="hidden text-[10px] uppercase tracking-wide sm:block">
                    {entry.section}
                  </span>
                  {selected && <CornerDownLeft className="h-3.5 w-3.5" aria-hidden />}
                </li>
              );
            })
          )}
        </ul>
      </div>
    </div>
  );
};

export default CommandPalette;
