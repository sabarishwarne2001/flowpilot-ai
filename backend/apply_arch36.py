"""ARCH-36 — Navigation, Sidebar & Routing Unification: one atomic, idempotent apply.

Run from backend/, like every `apply_<phase>.py` in this repo:

    python apply_arch36.py --check     # report, write nothing
    python apply_arch36.py             # apply
    python apply_arch36.py             # again: every line reads "already applied"

ARCH-36 has no migration. The Alembic head stays `arch35_step1_calibration`.

HOW THIS SCRIPT DIFFERS FROM apply_arch35.py
============================================

ARCH-35 shipped new files separately and patched existing ones here. ARCH-36
embeds its new files too, so the phase is one command. Three operation kinds:

  * NEW FILE. Written if absent. Present and byte-identical (BOM and CRLF
    folded): "already present". Present and different: FAIL, nothing written.
  * WHOLE-FILE REPLACE. For the two layout files ARCH-36 rewrites. Guarded by
    the sha256 of the file as it stood at the `ARCH-35 DONE` commit. A file
    with local edits fails loudly rather than losing them. A file carrying
    the sentinel is "already applied".
  * ANCHORED PATCH. Exactly as in apply_arch35.py: every edit names an exact
    substring and how many times it must occur; each patch carries a
    SENTINEL that is a substring of its own replacement text.

ATOMIC. Every operation is validated before any is written. If a write then
fails, every file already written in this run is restored from memory and
every file created in this run is removed.

BOM AND CRLF PRESERVING for existing files. New files are written UTF-8, no
BOM, LF — the same as the rest of src/.

WHAT IS CHANGED, AND WHY (audit references are to the ARCH-36 blueprint)
========================================================================

Unreachable pages (A1)
  navigation.ts, SidebarNavigation.tsx   grouped, role-aware, capability-aware
                                         sidebar; Radar, Three-way matching,
                                         Run history, Notifications, Clause
                                         assertions and the Partner portal
                                         gain entries
  CaseQueue.tsx                          link to the tolerance policies
  Automation.tsx                         link to the full run history
  WorkItemDetails.tsx                    "Redact" — the only way to create a
                                         redaction job, so the only way into
                                         the Redaction Studio
  App.tsx, tenantPaths.ts                route + helpers for the ARCH-33
                                         assertion review page, which was
                                         never routed
  DashboardLayout.tsx                    Ctrl+K / ⌘K command palette

Drift (A2)
  constants/capabilities.ts              one copy of the backend capability keys
  tenantPaths.ts, app/core/slugs.py      "partners" reserved on both sides

ROLLBACK
========

    git checkout -- frontend/src backend/app/core/slugs.py
    git clean -n frontend/src      # review, then -f, to drop the new files
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"


# ---------------------------------------------------------------------------
# Sentinels. Each must be a substring of the text its own operation writes.
# ---------------------------------------------------------------------------

SENTINEL_NAV = "ARCH36-S1:grouped-navigation"
SENTINEL_SIDEBAR = "ARCH36-S1:sidebar-groups"
SENTINEL_PATHS = "ARCH36-S1:tenant-paths"
SENTINEL_APP = "ARCH36-S1:assertions-route"
SENTINEL_LAYOUT = "ARCH36-S1:command-palette-mount"
SENTINEL_DETAILS = "ARCH36-S1:redaction-entry-mount"
SENTINEL_CASES = "ARCH36-S1:policies-link"
SENTINEL_AUTOMATION = "ARCH36-S1:timeline-link"
SENTINEL_SLUGS = "ARCH36-S1:reserved-partners"

#: sha256 (BOM stripped, CRLF folded) of the two files at `ARCH-35 DONE`.
NAVIGATION_BASE_SHA256 = "a352a0f82e523d91e02f67d84c50f0d8877ec358a2a8621f4ae54eb42ff78f16"
SIDEBAR_BASE_SHA256 = "fea60c2b00f3bbe3a6958fa86d7264395017387a49b4df5e969226694f28d62b"


# ---------------------------------------------------------------------------
# Operation types
# ---------------------------------------------------------------------------


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    root: Path
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)


@dataclass
class FileReplace:
    root: Path
    relpath: str
    sentinel: str
    base_sha256: str
    content: str


@dataclass
class NewFile:
    root: Path
    relpath: str
    content: str


Operation = Union[FilePatch, FileReplace, NewFile]


class PatchError(RuntimeError):
    pass


def _read(path: Path) -> tuple[str, str, bool]:
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _encode(text: str, newline: str, had_bom: bool) -> bytes:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    return b"\xef\xbb\xbf" + data if had_bom else data


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class Planned:
    path: Path
    relpath: str
    data: Optional[bytes]  # None: nothing to write
    created: bool
    message: str


def plan(op: Operation) -> Planned:
    path = op.root / op.relpath

    if isinstance(op, NewFile):
        if path.exists():
            current, _, _ = _read(path)
            if current == op.content:
                return Planned(path, op.relpath, None, False, "already present")
            # HM-S1:superseded. HARDENING-MASTER extends files ARCH-36 created
            # (the capability list gained six keys). Such a file carries the
            # HM-S1 sentinel and counts as applied, as ARCH-38/40 files do for
            # apply_arch37/38/39 through SUPERSEDING_SENTINELS.
            if "HM-S1:" in current:
                return Planned(path, op.relpath, None, False, "already present (superseded by HARDENING-MASTER)")
            raise PatchError(
                f"{op.relpath}: exists with different content. ARCH-36 creates "
                "this file; a different file at this path is not something "
                "this script will overwrite. Nothing has been written."
            )
        return Planned(path, op.relpath, op.content.encode("utf-8"), True, "new file")

    if not path.exists():
        raise PatchError(f"{op.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if op.sentinel in text:
        return Planned(path, op.relpath, None, False, "already applied")

    if isinstance(op, FileReplace):
        if op.sentinel not in op.content:
            raise PatchError(f"{op.relpath}: sentinel is absent from the replacement")
        actual = _sha256(text)
        if actual != op.base_sha256:
            raise PatchError(
                f"{op.relpath}: sha256 {actual[:12]}… is not the ARCH-35 file "
                f"({op.base_sha256[:12]}…). It has local changes this script "
                "would discard. Nothing has been written."
            )
        return Planned(
            path, op.relpath, _encode(op.content, newline, had_bom), False, "replaced"
        )

    updated = text
    for edit in op.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{op.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if op.sentinel not in updated:
        raise PatchError(
            f"{op.relpath}: sentinel {op.sentinel!r} is absent from the patched "
            "text. A sentinel must be a substring of what its own patch writes, "
            "or the next run re-applies the edit."
        )
    return Planned(
        path, op.relpath, _encode(updated, newline, had_bom), False, f"{len(op.edits)} edit(s)"
    )


# ===========================================================================
# New files
# ===========================================================================

NEW_FILES: dict[str, str] = {}
NEW_FILES['src/components/layout/CommandPalette.tsx'] = r'''import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
'''

NEW_FILES['src/components/layout/commandPaletteEvents.ts'] = r'''/**
 * ARCH36-S1:command-palette-event — how anything opens the command palette.
 *
 * A window event rather than a store field, so the sidebar's search button
 * does not import the palette (and its list rendering) just to open it, and
 * the palette stays the only owner of its own open state.
 */
export const OPEN_COMMAND_PALETTE_EVENT = "flowpilot:open-command-palette";

export const openCommandPalette = (): void => {
  window.dispatchEvent(new Event(OPEN_COMMAND_PALETTE_EVENT));
};

/** "⌘K" on Apple platforms, "Ctrl K" elsewhere. Display only. */
export const commandPaletteShortcutLabel = (): string => {
  const platform =
    typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad/i.test(platform) ? "⌘K" : "Ctrl K";
};
'''

NEW_FILES['src/components/redaction/StartRedactionButton.tsx'] = r'''import React, { useEffect, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { EyeOff, Loader2, Lock } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { redactionPath } from "@/routes/tenantPaths";
import { ApiError } from "@/services/api/client";
import { startRedaction } from "@/services/api/redaction";

/**
 * The redaction profiles the engine defines, with the words a reviewer reads.
 *
 * Keys mirror `PROFILES` in app/services/redaction/vocabulary.py.
 * verify_arch36.py fails when the two sets differ: an extra key here is a
 * button that always answers 422, and a missing one is a profile nobody can
 * start.
 */
export const REDACTION_PROFILE_LABELS: Readonly<Record<string, { label: string; hint: string }>> = {
  all_identifiers: {
    label: "All identifiers",
    hint: "Every detector the engine has",
  },
  financial: {
    label: "Financial",
    hint: "Card numbers, IBAN, SSN, PAN, GSTIN",
  },
  hipaa_safe_harbor: {
    label: "HIPAA Safe Harbor",
    hint: "Names, dates of birth, SSN, card numbers, contact details",
  },
  india_kyc: {
    label: "India KYC",
    hint: "Aadhaar, PAN, GSTIN, date of birth, contact details",
  },
};

interface StartRedactionButtonProps {
  readonly workspaceId: string;
  readonly workItemId: string;
  /** `work_items.file_type` — the stored MIME type. */
  readonly mimeType: string;
  readonly status: string;
}

/**
 * ARCH36-S1:redaction-entry — the only way into the Redaction Studio.
 *
 * ARCH-32 shipped the studio at `redactions/:jobId` and `startRedaction` in
 * the API client, and nothing called `startRedaction`. With no job there is no
 * id, and with no id the studio cannot be opened, so the feature was
 * unreachable however the navigation was arranged.
 *
 * Shown for completed PDFs only, because the engine refuses anything else
 * (it rebuilds a PDF from rendered pages and needs extraction geometry).
 * Hidden below CONTRIBUTOR, which is what the endpoint requires. When the
 * tier lacks the capability the button renders locked rather than hidden;
 * the endpoint's capability gate is what actually refuses.
 */
const StartRedactionButton: React.FC<StartRedactionButtonProps> = ({
  workspaceId,
  workItemId,
  mimeType,
  status,
}) => {
  const navigate = useNavigate();
  const { organization, workspace, workspaceRole } = useResolvedTenant();
  const capability = useCapabilityAccess(organization.organization_id, CAPABILITY.redaction);
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const onPointer = (event: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const start = useMutation({
    mutationFn: (profileKey: string) => startRedaction(workspaceId, workItemId, profileKey),
    onSuccess: (job) => {
      setOpen(false);
      navigate(redactionPath(organization.organization_slug, workspace.slug, job.id));
    },
    onError: (error: unknown) => {
      toast.error(
        error instanceof ApiError ? error.message : "Redaction could not be started.",
      );
    },
  });

  const isPdf = mimeType.toLowerCase() === "application/pdf";
  if (!isPdf || status !== "COMPLETED" || !isAtLeast(workspaceRole, "CONTRIBUTOR")) {
    return null;
  }

  const buttonClass =
    "inline-flex items-center rounded-lg border border-border bg-card px-3 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-50";

  if (!capability.isLoading && !capability.granted) {
    return (
      <button
        type="button"
        disabled
        title="Redaction is not included in your plan"
        className={buttonClass}
      >
        <Lock className="mr-2 h-4 w-4" aria-hidden />
        Redact
      </button>
    );
  }

  return (
    <div ref={containerRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        disabled={capability.isLoading || start.isPending}
        aria-haspopup="menu"
        aria-expanded={open}
        className={buttonClass}
      >
        {start.isPending ? (
          <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
        ) : (
          <EyeOff className="mr-2 h-4 w-4" aria-hidden />
        )}
        Redact
      </button>

      {open && (
        <div
          role="menu"
          aria-label="Redaction profile"
          className="absolute right-0 z-30 mt-2 w-72 rounded-xl border border-border bg-card p-1 shadow-lg"
        >
          <p className="px-3 py-2 text-xs text-muted-foreground">
            Choose what to detect. You review every region before anything is
            applied.
          </p>
          {Object.entries(REDACTION_PROFILE_LABELS).map(([key, profile]) => (
            <button
              key={key}
              type="button"
              role="menuitem"
              onClick={() => start.mutate(key)}
              className="flex w-full flex-col items-start rounded-lg px-3 py-2 text-left hover:bg-muted"
            >
              <span className="text-sm font-semibold">{profile.label}</span>
              <span className="text-xs text-muted-foreground">{profile.hint}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default StartRedactionButton;
'''

NEW_FILES['src/constants/capabilities.ts'] = r'''/**
 * ARCH36-S1:capability-constants — the frontend's one copy of the backend's
 * `CAPABILITY_KEYS` (app/core/entitlements.py).
 *
 * Before ARCH-36 each capability-gated page declared its own string literal.
 * Five copies of a string the backend owns is five places a rename can miss,
 * and a missed rename is not a crash: `useCapabilityAccess` reports the
 * capability absent and the page shows its lock card to a tenant who paid.
 *
 * `verify_arch36.py` fails when this set and the backend set differ, and when
 * any `"capability.*"` literal anywhere under `src/` is not one of these.
 */
export const CAPABILITY = {
  reconciliation: "capability.reconciliation",
  redaction: "capability.redaction",
  semanticAssertions: "capability.semantic_assertions",
  anomalyRadar: "capability.anomaly_radar",
  calibratedAutonomy: "capability.calibrated_autonomy",
} as const;

export type CapabilityKey = (typeof CAPABILITY)[keyof typeof CAPABILITY];

export const CAPABILITY_KEYS: readonly CapabilityKey[] = Object.values(CAPABILITY);
'''

NEW_FILES['src/hooks/useGrantedCapabilities.ts'] = r'''import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { getOrganizationEntitlements } from "@/services/api/entitlements";
import { entitlementKeys } from "@/services/api/queryKeys";

export interface GrantedCapabilities {
  readonly granted: ReadonlySet<string>;
  readonly isLoading: boolean;
  readonly isError: boolean;
}

/**
 * ARCH36-S1:granted-capabilities — every capability the organization's tier
 * grants, as one set.
 *
 * `useCapabilityAccess` answers for one key, which suits a page. The sidebar
 * and the command palette need an answer for every gated entry at once, and a
 * hook per entry would put hook calls inside a loop over data.
 *
 * Same query key and the same fetcher as `useCapabilityAccess`, so a page and
 * the sidebar rendered together issue one request and cannot disagree.
 *
 * `GET /organizations/{id}/entitlements` is `RequireAnyOrgRole`, so a viewer
 * gets a real answer rather than a 403 that would lock every entry.
 */
export const useGrantedCapabilities = (
  organizationId: string,
): GrantedCapabilities => {
  const query = useQuery({
    queryKey: entitlementKeys.all(organizationId),
    queryFn: () => getOrganizationEntitlements(organizationId),
    enabled: Boolean(organizationId),
    staleTime: 60_000,
  });

  const list = (query.data as { capabilities?: readonly string[] } | undefined)
    ?.capabilities;

  const granted = useMemo(() => new Set<string>(list ?? []), [list]);

  return {
    granted,
    isLoading: query.isLoading,
    isError: query.isError,
  };
};
'''

NEW_FILES['src/hooks/useIsPartnerMember.ts'] = r'''import { useQuery } from "@tanstack/react-query";

import { partnerApi } from "@/services/api/partner";
import { partnerKeys } from "@/services/api/queryKeys";

/**
 * ARCH36-S1:partner-membership — whether the signed-in user belongs to any
 * reseller partner (ARCH-27).
 *
 * `GET /partners` answers for the caller only and returns an empty list for a
 * user with no partner membership, so the sidebar can ask it of everyone.
 * It shares `partnerKeys.mine()` with PartnerPortal, so opening the portal
 * after the sidebar has asked costs no second request.
 *
 * Hiding the link is not what protects the portal: every `/partners/{id}/...`
 * endpoint authenticates a partner principal on the server.
 */
export const useIsPartnerMember = (): boolean => {
  const query = useQuery({
    queryKey: partnerKeys.mine(),
    queryFn: partnerApi.listMine,
    staleTime: 5 * 60_000,
    retry: false,
  });

  return (query.data?.length ?? 0) > 0;
};
'''

NEW_FILES['src/pages/Assertions/AssertionReviewPage.tsx'] = r'''import React from "react";
import { Loader2 } from "lucide-react";

import { CAPABILITY } from "@/constants/capabilities";
import { useActiveWorkspace } from "@/hooks/useActiveWorkspace";
import { useCapabilityAccess } from "@/hooks/useCapabilityAccess";
import { HINT, PAGE_TITLE } from "@/components/ui/primitives";
import AssertionReviewQueue from "./AssertionReviewQueue";

/**
 * ARCH36-S1:assertion-review-page — the route for ARCH-33's review queue.
 *
 * `AssertionReviewQueue` shipped in ARCH-33 as a prop-driven component and was
 * never routed or imported, so a document held for a reviewer's verdict could
 * not be resolved from the UI and its workflow stayed paused.
 *
 * This wrapper resolves the workspace and the capability from the same hooks
 * every other capability-gated page uses, so the gate cannot be forgotten at
 * the route. The queue component itself is unchanged; ARCH-40 folds it into
 * the unified review hub as a tab.
 */
const AssertionReviewPage: React.FC = () => {
  const workspace = useActiveWorkspace();
  const workspaceId = workspace?.workspaceId ?? "";
  const organizationId = workspace?.organizationId ?? "";
  const capability = useCapabilityAccess(organizationId, CAPABILITY.semanticAssertions);

  if (!workspaceId || capability.isLoading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
        Loading&hellip;
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <header className="space-y-1">
        <h1 className={PAGE_TITLE}>Clause assertions</h1>
        <p className={HINT}>
          Requirement checks the engine was not confident enough to decide.
          Your verdict resumes the workflow and teaches the calibrator.
        </p>
      </header>
      <AssertionReviewQueue
        workspaceId={workspaceId}
        hasCapability={capability.granted}
        canChangePlan={workspace?.role === "ADMIN"}
      />
    </div>
  );
};

export default AssertionReviewPage;
'''


# ===========================================================================
# Whole-file replacements
# ===========================================================================

NAVIGATION_TS = r'''import type { LucideIcon } from "lucide-react";
import {
  BarChart3,
  Bell,
  ClipboardCheck,
  CreditCard,
  FileText,
  GitCompareArrows,
  Handshake,
  History,
  KeyRound,
  KeySquare,
  LayoutDashboard,
  ListChecks,
  Mail,
  MessageSquare,
  PlusSquare,
  Radar,
  ScrollText,
  Gauge,
  Palette,
  TerminalSquare,
  Shield,
  Settings,
  ShieldCheck,
  SlidersHorizontal,
  Store,
  Sliders,
  Target,
  Scale,
  Users,
  Webhook,
} from "lucide-react";

import { CAPABILITY, type CapabilityKey } from "@/constants/capabilities";
import {
  ROUTE_PATTERNS,
  assertionsPath,
  assistantPath,
  automationPath,
  automationTimelinePath,
  createWorkspacePath,
  notificationsPath,
  organizationApiKeysPath,
  organizationAuditPath,
  organizationBillingPath,
  organizationAnalyticsPath,
  organizationBrandingPath,
  organizationMarketplacePath,
  organizationAutonomyPath,
  organizationBYOKPath,
  organizationCompliancePath,
  organizationDeveloperPath,
  organizationEmailPath,
  organizationIdentityPath,
  organizationMembersPath,
  organizationNotificationsPath,
  organizationSettingsPath,
  organizationSLOsPath,
  organizationWebhooksPath,
  partnerPortalPath,
  platformMarginsPath,
  procurementPath,
  procurementPoliciesPath,
  radarPath,
  verificationPath,
  workItemsPath,
  workspaceDashboardPath,
  workspaceSettingsPath,
} from "@/routes/tenantPaths";
import type { WorkspaceRole } from "@/types/tenancy";

export interface NavigationItem {
  readonly name: string;
  readonly path: string;
  readonly icon: LucideIcon;
}

/**
 * ARCH36-S1:grouped-navigation — the workspace navigation model.
 *
 * WHY EVERY ENTRY NAMES ITS ROUTE PATTERN
 * =======================================
 *
 * Five shipped pages (the audit radar, the matching queue, its tolerance
 * policies, the execution history and the partner portal) had a route and no
 * link. Each one was a page somebody built, registered in App.tsx and then
 * never connected, and nothing noticed because nothing could: a link is a
 * string, and a missing string is not a type error.
 *
 * `route` is typed as a key of ROUTE_PATTERNS, so a renamed pattern is a
 * compile error here. verify_arch36.py reads these keys back and fails when a
 * workspace pattern without a parameter has neither an entry below nor a
 * written reason for having none.
 *
 * WHY LOCKED ENTRIES ARE SHOWN, NOT HIDDEN
 * ========================================
 *
 * An entry with a `capability` renders with a lock when the organization's
 * tier does not grant it, and still links to its page, which renders the
 * existing lock card. Hiding it would make the upgrade invisible to the one
 * reader it is aimed at. The lock is not a security control: every endpoint
 * behind these pages refuses on the server through `capability_gate`.
 *
 * WHY THE GROUPS ARE DATA
 * =======================
 *
 * The sidebar and the command palette both render from this function. A
 * second list in the palette would drift from the first within a release.
 */
export type RoutePatternKey = keyof typeof ROUTE_PATTERNS;

export type WorkspaceNavigationGroupKey =
  | "workspace"
  | "intelligence"
  | "processing"
  | "automation"
  | "review"
  | "configuration";

export interface WorkspaceNavigationItem extends NavigationItem {
  /** Stable identifier. Tests and the command palette key on it. */
  readonly id: string;
  readonly route: RoutePatternKey;
  /** One line, shown in the command palette. */
  readonly description: string;
  readonly capability?: CapabilityKey;
  /** Omitted means every workspace role. */
  readonly minimumRole?: WorkspaceRole;
  /**
   * Match the path exactly. Needed where another entry's path extends this
   * one: the dashboard is the workspace root, and the execution history
   * lives under the workflows path.
   */
  readonly end?: boolean;
  readonly keywords?: readonly string[];
}

export interface WorkspaceNavigationGroup {
  readonly key: WorkspaceNavigationGroupKey;
  readonly label: string;
  readonly items: readonly WorkspaceNavigationItem[];
}

export const buildWorkspaceNavigationGroups = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationGroup[] => [
  {
    key: "workspace",
    label: "Workspace",
    items: [
      {
        id: "overview",
        name: "Overview",
        route: "workspaceDashboard",
        path: workspaceDashboardPath(orgSlug, workspaceSlug),
        icon: LayoutDashboard,
        description: "Workspace dashboard and processing health",
        end: true,
        keywords: ["home", "dashboard"],
      },
      {
        id: "notifications",
        name: "Notifications",
        route: "workspaceNotifications",
        path: notificationsPath(orgSlug, workspaceSlug),
        icon: Bell,
        description: "Every alert raised in this workspace",
        keywords: ["alerts", "inbox"],
      },
    ],
  },
  {
    key: "intelligence",
    label: "Document intelligence",
    items: [
      {
        id: "documents",
        name: "Documents",
        route: "workspaceWorkItems",
        path: workItemsPath(orgSlug, workspaceSlug),
        icon: FileText,
        description: "Upload, search and inspect processed documents",
        keywords: ["work items", "upload", "files", "ocr"],
      },
      {
        id: "assistant",
        name: "AI Assistant",
        route: "workspaceAssistant",
        path: assistantPath(orgSlug, workspaceSlug),
        icon: MessageSquare,
        description: "Ask questions across the workspace's documents",
        keywords: ["chat", "rag", "ask"],
      },
    ],
  },
  {
    key: "processing",
    label: "Enterprise processing",
    items: [
      {
        id: "procurement",
        name: "Three-way matching",
        route: "workspaceProcurement",
        path: procurementPath(orgSlug, workspaceSlug),
        icon: GitCompareArrows,
        description: "Invoice, purchase order and receipt reconciliation cases",
        capability: CAPABILITY.reconciliation,
        keywords: ["procurement", "invoice matching", "po", "grn", "accounts payable"],
      },
      {
        id: "radar",
        name: "Forensic audit radar",
        route: "workspaceRadar",
        path: radarPath(orgSlug, workspaceSlug),
        icon: Radar,
        description: "Duplicate ingestion, price surges and contract drift",
        capability: CAPABILITY.anomalyRadar,
        keywords: ["anomaly", "duplicate", "fraud", "audit"],
      },
    ],
  },
  {
    key: "automation",
    label: "Automation",
    items: [
      {
        id: "workflows",
        name: "Workflows",
        route: "workspaceAutomation",
        path: automationPath(orgSlug, workspaceSlug),
        icon: Sliders,
        description: "Rules that run when documents change",
        end: true,
        keywords: ["automation", "rules", "triggers"],
      },
      {
        id: "run-history",
        name: "Run history",
        route: "workspaceAutomationTimeline",
        path: automationTimelinePath(orgSlug, workspaceSlug),
        icon: History,
        description: "Every workflow execution, grouped by correlation",
        keywords: ["timeline", "executions", "logs", "suppressed"],
      },
    ],
  },
  {
    key: "review",
    label: "Human review",
    items: [
      {
        id: "review-queue",
        name: "Review queue",
        route: "workspaceVerification",
        path: verificationPath(orgSlug, workspaceSlug),
        icon: ClipboardCheck,
        description: "Extraction fields the models disagreed on",
        keywords: ["verification", "hitl", "triage"],
      },
      {
        id: "assertion-reviews",
        name: "Clause assertions",
        route: "workspaceAssertions",
        path: assertionsPath(orgSlug, workspaceSlug),
        icon: ListChecks,
        description: "Requirement checks that need a reviewer's verdict",
        capability: CAPABILITY.semanticAssertions,
        keywords: ["assertions", "clauses", "contracts", "triage"],
      },
    ],
  },
  {
    key: "configuration",
    label: "Configuration",
    items: [
      {
        id: "settings",
        name: "Settings",
        route: "workspaceSettings",
        path: workspaceSettingsPath(orgSlug, workspaceSlug),
        icon: Settings,
        description: "Workspace, AI and document settings",
        // Viewers cannot inspect settings tabs.
        minimumRole: "CONTRIBUTOR",
        keywords: ["preferences", "configuration", "email"],
      },
    ],
  },
];

/**
 * Destinations the command palette offers that do not earn a sidebar row:
 * pages reached from inside another page in normal use.
 */
export const buildWorkspaceShortcutItems = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationItem[] => [
  {
    id: "procurement-policies",
    name: "Tolerance policies",
    route: "workspaceProcurementPolicies",
    path: procurementPoliciesPath(orgSlug, workspaceSlug),
    icon: SlidersHorizontal,
    description: "Price and quantity tolerances for three-way matching",
    capability: CAPABILITY.reconciliation,
    keywords: ["procurement", "tolerance", "policy"],
  },
];

/** Flat list, kept for callers written before ARCH-36. */
export const buildNavigationItems = (
  orgSlug: string,
  workspaceSlug: string,
): readonly WorkspaceNavigationItem[] =>
  buildWorkspaceNavigationGroups(orgSlug, workspaceSlug).flatMap(
    (group) => group.items,
  );

/** Organization-scoped destination the palette offers to every member. */
export const buildCreateWorkspaceItem = (orgSlug: string): NavigationItem => ({
  name: "Create workspace",
  path: createWorkspacePath(orgSlug),
  icon: PlusSquare,
});

/** ARCH-27 partner portal. Shown only to a partner member. */
export const buildPartnerNavigationItems = (
  isPartnerMember: boolean,
): readonly NavigationItem[] =>
  isPartnerMember
    ? [
        {
          name: "Partner portal",
          path: partnerPortalPath(),
          icon: Handshake,
        },
      ]
    : [];

export const buildOrganizationNavigationItems = (
  orgSlug: string,
  organizationRole: string,
): readonly NavigationItem[] => {
  const role = String(organizationRole).toUpperCase();
  const items: NavigationItem[] = [];

  // General is first for everyone. It is the organization's own settings page
  // and, for an OWNER, the only route to the archive flow. Placing it below
  // role-gated entries would put the lifecycle control underneath the things
  // whose lifecycle it governs.
  items.push({
    name: "General",
    path: organizationSettingsPath(orgSlug),
    icon: Settings,
  });

  items.push({
    name: "Notifications",
    path: organizationNotificationsPath(orgSlug),
    icon: Bell,
  });

  if (role === "OWNER" || role === "ADMIN") {
    items.push({
      name: "Members",
      path: organizationMembersPath(orgSlug),
      icon: Users,
    });
    items.push({
      name: "Transactional email",
      path: organizationEmailPath(orgSlug),
      icon: Mail,
    });
  }

  if (role === "OWNER" || role === "ADMIN") {
    items.push({
      name: "Service levels",
      path: organizationSLOsPath(orgSlug),
      icon: Gauge,
    });
    // ARCH-20. ADMIN sees the console because residency, retention and the
    // erasure register are all things an administrator has to be able to
    // read during an audit. The irreversible writes inside it are OWNER-only,
    // enforced by RequireOrgOwner on the route, not by hiding the link.
    items.push({
      name: "Data governance & compliance",
      path: organizationCompliancePath(orgSlug),
      icon: Shield,
    });
    // ARCH-21. ADMIN, not OWNER-only, unlike "API keys" below. The two are
    // different surfaces: that one mints credentials for the internal
    // console, this one manages a commercial gateway's tiers and reads its
    // consumption charts — work an administrator does. Every write behind it
    // is still RequireOrgAdmin plus an explicit human-session check, and the
    // plan ceiling is enforced in the service, so hiding the link is not
    // what protects anything.
    items.push({
      name: "Developer platform",
      path: organizationDeveloperPath(orgSlug),
      icon: TerminalSquare,
    });
    // ARCH-22. ADMIN sees the console; every write behind it is OWNER-gated
    // by RequireOrgOwner on the route. An administrator has to be able to
    // read which provider account the tenant's traffic is running on during
    // an audit, and hiding the link is not what protects the credentials.
    items.push({
      name: "Enterprise BYOK & models",
      path: organizationBYOKPath(orgSlug),
      icon: KeySquare,
    });
    // ARCH-25. ADMIN sees the console because visual branding is an
    // administrator's job. Every DOMAIN operation behind it is OWNER-gated by
    // RequireOrgOwner on the route: a vanity hostname resolves to a tenant,
    // which makes claiming one authentication-adjacent rather than cosmetic.
    // Hiding the link is not what protects the domain endpoints.
    items.push({
      name: "Branding & custom domains",
      path: organizationBrandingPath(orgSlug),
      icon: Palette,
    });
    // ARCH-26. ADMIN sees the console because reading which warehouses the
    // tenant syncs to, and why last night's run failed, is support work.
    // Every write behind it is OWNER-gated by RequireOrgOwner on the
    // endpoint: registering a destination hands a credential for third-party
    // infrastructure to this platform and starts a recurring egress of tenant
    // data to it. Hiding the link is not what protects those endpoints.
    items.push({
      name: "Analytics & BI egress",
      path: organizationAnalyticsPath(orgSlug),
      icon: BarChart3,
    });
    // ARCH-27. ADMIN sees the catalog because reading which third-party
    // workflows are installed, and what they do, is support work. Installing
    // is OWNER-gated by RequireOrgOwner on the endpoint: admitting executable
    // code authored by a third party into the tenant's own automation engine
    // is an ownership decision. Hiding the link is not what protects it —
    // marketplace_installations.verified_signature_id being NOT NULL is.
    items.push({
      name: "Partner marketplace",
      path: organizationMarketplacePath(orgSlug),
      icon: Store,
    });
    // ARCH35-S3:autonomy-nav. ADMIN reads why a decision type is paused and
    // how accurate the platform has been; changing the error limit and
    // resuming are OWNER-gated by RequireOrgOwner on the endpoints.
    items.push({
      name: "Calibrated autonomy",
      path: organizationAutonomyPath(orgSlug),
      icon: Target,
    });
  }

  if (role === "OWNER" || role === "BILLING") {
    items.push({
      name: "Billing",
      path: organizationBillingPath(orgSlug),
      icon: CreditCard,
    });
  }

  if (role === "OWNER") {
    items.push({
      name: "API keys",
      path: organizationApiKeysPath(orgSlug),
      icon: KeyRound,
    });
    items.push({
      name: "Webhooks",
      path: organizationWebhooksPath(orgSlug),
      icon: Webhook,
    });
    items.push({
      name: "Enterprise identity",
      path: organizationIdentityPath(orgSlug),
      icon: ShieldCheck,
    });
    items.push({
      name: "Audit log",
      path: organizationAuditPath(orgSlug),
      icon: ScrollText,
    });
  }

  return items;
};

/**
 * ARCH-18 — platform administration.
 *
 * Separate from buildOrganizationNavigationItems on purpose. The organization
 * builder takes an organization role and produces links scoped to one tenant;
 * this one takes nothing, because a platform page has no tenant. Folding the
 * superuser check into the organization builder would put a cross-tenant link
 * inside an organization's own navigation, which invites reading platform
 * totals as that organization's numbers.
 *
 * Returning an empty array for a non-superuser hides the link. It does not
 * protect the page — SuperAdminGuard redirects, and require_superadmin on the
 * backend refuses. Three layers, only the last of which is a security control.
 */
export const buildPlatformNavigationItems = (
  isSuperAdmin: boolean,
): readonly NavigationItem[] => {
  if (!isSuperAdmin) {
    return [];
  }

  return [
    {
      name: "Unit economics",
      path: platformMarginsPath(),
      icon: Scale,
    },
  ];
};
'''

SIDEBAR_NAVIGATION_TSX = r'''import React, { useMemo } from "react";
import { NavLink } from "react-router-dom";
import { Lock, Search } from "lucide-react";

import {
  buildOrganizationNavigationItems,
  buildPartnerNavigationItems,
  buildPlatformNavigationItems,
  buildWorkspaceNavigationGroups,
  type NavigationItem,
} from "./navigation";
import {
  commandPaletteShortcutLabel,
  openCommandPalette,
} from "./commandPaletteEvents";
import { useGrantedCapabilities } from "@/hooks/useGrantedCapabilities";
import { useIsPartnerMember } from "@/hooks/useIsPartnerMember";
import { isAtLeast } from "@/permissions/workspacePermissions";
import { useResolvedTenant } from "@/routes/TenantContext";
import { useIsSuperAdmin } from "@/routes/SuperAdminGuard";

interface SidebarNavigationProps {
  readonly collapsed: boolean;
  readonly onNavigate?: () => void;
}

interface RenderableItem extends NavigationItem {
  readonly end?: boolean;
  readonly locked?: boolean;
}

/**
 * Primary navigation for the workspace shell.
 *
 * ARCH36-S1:sidebar-groups — what ARCH-36 changed
 * ================================================
 *
 * The workspace list is rendered from `buildWorkspaceNavigationGroups`, in
 * labelled groups. Entries gated by a capability render with a lock when the
 * organization's tier does not grant it and still link to their page, which
 * shows the lock card; see navigation.ts for why they are not hidden. Role
 * filtering reads `minimumRole` from the model instead of comparing paths
 * here, so a second role-gated entry needs no change to this component.
 *
 * While entitlements are loading no lock is drawn. Drawing locks first and
 * removing them a moment later reads as the product changing its mind.
 *
 * Earlier history, still true
 * ===========================
 *
 * ARCH-01 removed the membership query (the role comes from TenantContext,
 * which TenantGuard has already resolved) and the flat route constants (paths
 * are built for the active tenant). Role filtering reads the EFFECTIVE role,
 * so an organization admin holding no stored workspace grant sees the full
 * menu.
 *
 * The organization-scoped group links to /organizations/{slug}/... from inside
 * an ordinary workspace. It is a separate group because it is a different
 * tenancy scope and a different URL tree.
 *
 * ARCH-21 (finding N-2) gave buildPlatformNavigationItems its call site. The
 * platform group renders below the organization group, because a cross-tenant
 * link inside an organization's own navigation invites reading platform-wide
 * totals as that organization's numbers. The builder returns an empty array
 * for a non-superuser, which hides the link; SuperAdminGuard redirects and
 * require_superadmin refuses. Only the last of those is a security control.
 */
const SidebarNavigation: React.FC<SidebarNavigationProps> = ({
  collapsed,
  onNavigate,
}) => {
  const { organization, workspace, workspaceRole, organizationRole } =
    useResolvedTenant();

  const orgSlug = organization.organization_slug;
  const workspaceSlug = workspace.slug;

  const capabilities = useGrantedCapabilities(organization.organization_id);
  const isPartnerMember = useIsPartnerMember();
  const isSuperAdmin = useIsSuperAdmin();

  const groups = useMemo(() => {
    const built = buildWorkspaceNavigationGroups(orgSlug, workspaceSlug);
    return built
      .map((group) => ({
        key: group.key,
        label: group.label,
        items: group.items
          .filter(
            (item) =>
              item.minimumRole === undefined ||
              isAtLeast(workspaceRole, item.minimumRole),
          )
          .map(
            (item): RenderableItem => ({
              name: item.name,
              path: item.path,
              icon: item.icon,
              end: item.end === true,
              locked:
                item.capability !== undefined &&
                !capabilities.isLoading &&
                !capabilities.granted.has(item.capability),
            }),
          ),
      }))
      .filter((group) => group.items.length > 0);
  }, [
    orgSlug,
    workspaceSlug,
    workspaceRole,
    capabilities.granted,
    capabilities.isLoading,
  ]);

  const organizationItems = useMemo(
    () => buildOrganizationNavigationItems(orgSlug, String(organizationRole ?? "")),
    [orgSlug, organizationRole],
  );

  const partnerItems = useMemo(
    () => buildPartnerNavigationItems(isPartnerMember),
    [isPartnerMember],
  );

  const platformItems = useMemo(
    () => buildPlatformNavigationItems(isSuperAdmin),
    [isSuperAdmin],
  );

  const shortcut = commandPaletteShortcutLabel();

  const renderItem = (item: RenderableItem) => {
    const label = item.locked ? `${item.name} (not included in your plan)` : item.name;

    return (
      <NavLink
        key={item.path}
        to={item.path}
        onClick={onNavigate}
        end={item.end === true}
        title={collapsed ? label : item.locked ? "Not included in your plan" : undefined}
        aria-label={label}
        className={({ isActive }) =>
          `
            group relative
            flex items-center
            justify-center
            rounded-lg
            ${collapsed ? "h-11 w-11 p-0" : "h-10 px-3"}
            text-sm font-medium
            transition-all
            ${
              isActive
                ? "bg-primary text-primary-foreground shadow-sm"
                : "text-muted-foreground hover:bg-muted/50 hover:text-foreground"
            }
          `
        }
      >
        <item.icon className="h-5 w-5 flex-shrink-0" aria-hidden />

        {!collapsed ? (
          <>
            <span className="ml-3 min-w-0 flex-1 truncate whitespace-nowrap font-semibold">
              {item.name}
            </span>
            {item.locked && (
              <Lock
                className="ml-2 h-3.5 w-3.5 flex-shrink-0 opacity-70"
                aria-hidden
                data-testid="nav-lock"
              />
            )}
          </>
        ) : (
          <>
            {item.locked && (
              <Lock
                className="absolute bottom-1 right-1 h-3 w-3 opacity-70"
                aria-hidden
                data-testid="nav-lock"
              />
            )}
            <span
              className="
                pointer-events-none
                absolute left-16 z-50
                whitespace-nowrap
                rounded-md
                border border-border
                bg-card
                px-2.5 py-1.5
                text-xs font-semibold
                opacity-0
                shadow-lg
                transition-opacity
                group-hover:opacity-100
              "
            >
              {label}
            </span>
          </>
        )}
      </NavLink>
    );
  };

  const renderGroupLabel = (label: string, first: boolean) =>
    collapsed ? (
      first ? null : <div className="my-2 h-px w-8 bg-border" aria-hidden />
    ) : (
      <p className="px-3 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        {label}
      </p>
    );

  return (
    <nav
      className={`
        flex
        h-full
        min-h-0
        flex-col
        px-3
        py-4
        overflow-y-auto
        overflow-x-hidden
        ${collapsed ? "items-center" : ""}
      `}
      aria-label="Primary Navigation"
    >
      <button
        type="button"
        onClick={openCommandPalette}
        title={collapsed ? `Search (${shortcut})` : undefined}
        aria-label={`Search pages (${shortcut})`}
        aria-keyshortcuts="Control+K Meta+K"
        className={`
          mb-2 flex items-center rounded-lg border border-border bg-background
          text-sm text-muted-foreground transition-colors
          hover:bg-muted/50 hover:text-foreground
          ${collapsed ? "h-10 w-10 justify-center" : "h-9 w-full px-3"}
        `}
      >
        <Search className="h-4 w-4 flex-shrink-0" aria-hidden />
        {!collapsed && (
          <>
            <span className="ml-2 flex-1 text-left">Search…</span>
            <kbd className="rounded border border-border px-1.5 py-0.5 text-[10px] font-semibold">
              {shortcut}
            </kbd>
          </>
        )}
      </button>

      {groups.map((group, index) => (
        <div key={group.key} className="space-y-1" role="group" aria-label={group.label}>
          {renderGroupLabel(group.label, index === 0)}
          {group.items.map(renderItem)}
        </div>
      ))}

      {organizationItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              {organization.organization_name}
            </p>
          )}
          {organizationItems.map((item) => renderItem(item))}
        </div>
      )}

      {partnerItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Partner
            </p>
          )}
          {partnerItems.map((item) => renderItem(item))}
        </div>
      )}

      {platformItems.length > 0 && (
        <div className="mt-5 space-y-1 border-t border-border pt-4">
          {!collapsed && (
            <p className="px-3 pb-1 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
              Platform
            </p>
          )}
          {platformItems.map((item) => renderItem(item))}
        </div>
      )}
    </nav>
  );
};

export default React.memo(SidebarNavigation);
'''

# ===========================================================================
# Anchored patches
# ===========================================================================

PATHS_RESERVED_ANCHOR = """  "organizations",
  "profile","""

PATHS_RESERVED_REPLACEMENT = """  "organizations",
  // ARCH-36. /partners is the ARCH-27 portal. Reserved here and in
  // app/core/slugs.py together: a segment reserved only in the browser would
  // strand an organization that the backend still lets take the slug.
  "partners",
  "profile","""

PATHS_PATTERN_ANCHOR = """  workspaceRadar: "radar","""

PATHS_PATTERN_REPLACEMENT = """  workspaceRadar: "radar",
  // ARCH-36. ARCH-33's assertion review queue. A child of the workspace
  // shell, so no RESERVED_ROUTE_SEGMENTS entry is needed.
  workspaceAssertions: "assertions","""

PATHS_HELPERS_ANCHOR = """export const verificationPath = ("""

PATHS_HELPERS_REPLACEMENT = """// ARCH36-S1:tenant-paths — helpers for the workspace routes that had a pattern
// and no helper. A link built by string concatenation in a page is a link
// verify_arch36.py cannot see.
export const radarPath = (orgSlug: string, workspaceSlug: string): string =>
  `${workspacePath(orgSlug, workspaceSlug)}/radar`;

export const assertionsPath = (
  orgSlug: string,
  workspaceSlug: string,
): string => `${workspacePath(orgSlug, workspaceSlug)}/assertions`;

export const redactionPath = (
  orgSlug: string,
  workspaceSlug: string,
  jobId: string,
): string =>
  `${workspacePath(orgSlug, workspaceSlug)}/redactions/${encodeURIComponent(jobId)}`;

export const verificationPath = ("""

PATHS_SELFCHECK_ANCHOR = """  expect(
    "single-segment and root paths carry no tenant","""

PATHS_SELFCHECK_REPLACEMENT = """  // ARCH-36.
  expect(
    "the partner portal is NOT parsed as a tenant path",
    partnerPortalPath() === "/partners" &&
      parseTenantPath("/partners/book") === null,
  );
  expect(
    "the redaction studio nests under the workspace and encodes the job id",
    redactionPath("acme", "engineering", "job 1") ===
      "/acme/engineering/redactions/job%201",
  );
  expect(
    "the assertion review page nests under the workspace",
    assertionsPath("acme", "engineering") === "/acme/engineering/assertions",
  );
  expect(
    "single-segment and root paths carry no tenant","""

APP_IMPORT_ANCHOR = """const RedactionStudio = lazy(
  () => import("@/pages/redaction/RedactionStudio"),
);"""

APP_IMPORT_REPLACEMENT = """// ARCH36-S1:assertions-route
const AssertionReviewPage = lazy(
  () => import("@/pages/Assertions/AssertionReviewPage"),
);
const RedactionStudio = lazy(
  () => import("@/pages/redaction/RedactionStudio"),
);"""

APP_ROUTE_ANCHOR = """                    <Route
                      path={ROUTE_PATTERNS.workspaceVerification}
                      element={<VerificationReviewQueue />}
                    />
"""

APP_ROUTE_REPLACEMENT = """                    <Route
                      path={ROUTE_PATTERNS.workspaceVerification}
                      element={<VerificationReviewQueue />}
                    />
                    {/* ARCH-36. ARCH-33's queue, routed for the first time. */}
                    <Route
                      path={ROUTE_PATTERNS.workspaceAssertions}
                      element={<AssertionReviewPage />}
                    />
"""

LAYOUT_IMPORT_ANCHOR = """import { useTimezoneCapture } from "@/hooks/useTimezoneCapture";
"""

LAYOUT_IMPORT_REPLACEMENT = """import { useTimezoneCapture } from "@/hooks/useTimezoneCapture";
// ARCH36-S1:command-palette-mount — Ctrl+K / ⌘K. Mounted here because this
// layout only renders beneath TenantGuard, and the palette builds tenant paths.
import CommandPalette from "@/components/layout/CommandPalette";
"""

LAYOUT_MOUNT_ANCHOR = """      <Sidebar onLogout={handleLogout} />
"""

LAYOUT_MOUNT_REPLACEMENT = """      <Sidebar onLogout={handleLogout} />
      <CommandPalette />
"""

DETAILS_IMPORT_ANCHOR = """import ChatPanel from "@/components/assistant/ChatPanel";
"""

DETAILS_IMPORT_REPLACEMENT = """import ChatPanel from "@/components/assistant/ChatPanel";
import StartRedactionButton from "@/components/redaction/StartRedactionButton";
"""

DETAILS_OPEN_ANCHOR = """        {workItem.status === "FAILED" && (
          <button
            type="button"
            onClick={handleRetry}"""

DETAILS_OPEN_REPLACEMENT = """        {/* ARCH36-S1:redaction-entry-mount */}
        <div className="flex items-center gap-2">
        {workspaceId && (
          <StartRedactionButton
            workspaceId={workspaceId}
            workItemId={workItem.id}
            mimeType={workItem.file_type}
            status={workItem.status}
          />
        )}
        {workItem.status === "FAILED" && (
          <button
            type="button"
            onClick={handleRetry}"""

DETAILS_CLOSE_ANCHOR = """            Retry Processing
          </button>
        )}
      </header>"""

DETAILS_CLOSE_REPLACEMENT = """            Retry Processing
          </button>
        )}
        </div>
      </header>"""

CASES_ANCHOR = """      <h1 className={PAGE_TITLE}>Invoice matching</h1>
"""

CASES_REPLACEMENT = """      {/* ARCH36-S1:policies-link — the tolerance editor had a route and no way in. */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className={PAGE_TITLE}>Invoice matching</h1>
        <Link
          to="./policies"
          className="rounded-lg border border-border px-3 py-1.5 text-xs font-semibold text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
        >
          Tolerance policies
        </Link>
      </div>
"""

AUTOMATION_IMPORT_ANCHOR = """import { toast } from "sonner";
"""

AUTOMATION_IMPORT_REPLACEMENT = """import { toast } from "sonner";
import { Link } from "react-router-dom";
"""

AUTOMATION_LINK_ANCHOR = """                Timeline trace logs mapping active condition comparisons and
                execution states.
              </p>
            </header>"""

AUTOMATION_LINK_REPLACEMENT = """                Timeline trace logs mapping active condition comparisons and
                execution states.
              </p>
              {/* ARCH36-S1:timeline-link — the execution history had a route and no way in. */}
              <Link
                to="./timeline"
                className="mt-2 inline-flex text-xs font-bold text-primary hover:underline"
              >
                Open full run history
              </Link>
            </header>"""

SLUGS_ANCHOR = """        "organizations",
        "plan","""

SLUGS_REPLACEMENT = """        "organizations",
        # ARCH36-S1:reserved-partners — /partners is the ARCH-27 partner
        # portal. Reserved together with the frontend's
        # RESERVED_ROUTE_SEGMENTS; existing rows are checked by
        # `verify_arch36.py --db`.
        "partners",
        "plan","""


def operations() -> list[Operation]:
    ops: list[Operation] = [
        NewFile(FRONTEND, relpath, content) for relpath, content in NEW_FILES.items()
    ]
    ops += [
        FileReplace(
            FRONTEND,
            "src/components/layout/navigation.ts",
            SENTINEL_NAV,
            NAVIGATION_BASE_SHA256,
            NAVIGATION_TS,
        ),
        FileReplace(
            FRONTEND,
            "src/components/layout/SidebarNavigation.tsx",
            SENTINEL_SIDEBAR,
            SIDEBAR_BASE_SHA256,
            SIDEBAR_NAVIGATION_TSX,
        ),
        FilePatch(
            FRONTEND,
            "src/routes/tenantPaths.ts",
            SENTINEL_PATHS,
            [
                Edit(PATHS_RESERVED_ANCHOR, PATHS_RESERVED_REPLACEMENT, 1, "reserved segment"),
                Edit(PATHS_PATTERN_ANCHOR, PATHS_PATTERN_REPLACEMENT, 1, "route pattern"),
                Edit(PATHS_HELPERS_ANCHOR, PATHS_HELPERS_REPLACEMENT, 1, "path helpers"),
                Edit(PATHS_SELFCHECK_ANCHOR, PATHS_SELFCHECK_REPLACEMENT, 1, "self-check"),
            ],
        ),
        FilePatch(
            FRONTEND,
            "src/App.tsx",
            SENTINEL_APP,
            [
                Edit(APP_IMPORT_ANCHOR, APP_IMPORT_REPLACEMENT, 1, "lazy import"),
                Edit(APP_ROUTE_ANCHOR, APP_ROUTE_REPLACEMENT, 1, "route"),
            ],
        ),
        FilePatch(
            FRONTEND,
            "src/layouts/DashboardLayout.tsx",
            SENTINEL_LAYOUT,
            [
                Edit(LAYOUT_IMPORT_ANCHOR, LAYOUT_IMPORT_REPLACEMENT, 1, "palette import"),
                Edit(LAYOUT_MOUNT_ANCHOR, LAYOUT_MOUNT_REPLACEMENT, 1, "palette mount"),
            ],
        ),
        FilePatch(
            FRONTEND,
            "src/pages/WorkItems/WorkItemDetails.tsx",
            SENTINEL_DETAILS,
            [
                Edit(DETAILS_IMPORT_ANCHOR, DETAILS_IMPORT_REPLACEMENT, 1, "button import"),
                Edit(DETAILS_OPEN_ANCHOR, DETAILS_OPEN_REPLACEMENT, 1, "header actions open"),
                Edit(DETAILS_CLOSE_ANCHOR, DETAILS_CLOSE_REPLACEMENT, 1, "header actions close"),
            ],
        ),
        FilePatch(
            FRONTEND,
            "src/pages/procurement/CaseQueue.tsx",
            SENTINEL_CASES,
            [Edit(CASES_ANCHOR, CASES_REPLACEMENT, 1, "policies link")],
        ),
        FilePatch(
            FRONTEND,
            "src/pages/Automation/Automation.tsx",
            SENTINEL_AUTOMATION,
            [
                Edit(AUTOMATION_IMPORT_ANCHOR, AUTOMATION_IMPORT_REPLACEMENT, 1, "Link import"),
                Edit(AUTOMATION_LINK_ANCHOR, AUTOMATION_LINK_REPLACEMENT, 1, "timeline link"),
            ],
        ),
        FilePatch(
            BACKEND,
            "app/core/slugs.py",
            SENTINEL_SLUGS,
            [Edit(SLUGS_ANCHOR, SLUGS_REPLACEMENT, 1, "reserved slug")],
        ),
    ]
    return ops


def run(*, check_only: bool) -> int:
    if not FRONTEND.is_dir():
        print(f"  FAIL  frontend not found at {FRONTEND}. Run from backend/.")
        return 1

    try:
        planned = [plan(op) for op in operations()]
    except PatchError as exc:
        print(f"  FAIL  {exc}")
        return 1

    pending = [p for p in planned if p.data is not None]

    if check_only:
        for p in planned:
            verb = "WOULD" if p.data is not None else "OK   "
            print(f"  {verb} {p.relpath}: {p.message}")
        print(f"\n{len(pending)} file(s) would change. Nothing was written.")
        return 0

    written: list[tuple[Planned, Optional[bytes]]] = []
    try:
        for p in pending:
            original = None if p.created else p.path.read_bytes()
            p.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = p.path.with_name(p.path.name + ".arch36.tmp")
            tmp.write_bytes(p.data or b"")
            tmp.replace(p.path)
            written.append((p, original))
    except OSError as exc:
        for done, original in reversed(written):
            try:
                if original is None:
                    done.path.unlink(missing_ok=True)
                else:
                    done.path.write_bytes(original)
            except OSError:
                print(f"  !!    could not restore {done.relpath}; restore it from git")
        print(f"  FAIL  write failed ({exc}); {len(written)} file(s) restored")
        return 1

    for p in planned:
        verb = "WROTE" if p.data is not None else "OK   "
        print(f"  {verb} {p.relpath}: {p.message}")
    print(f"\n{len(pending)} file(s) changed.")
    if pending:
        print("Next: python verify_arch36.py --build   (then --db --mutate)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-36 apply")
    parser.add_argument("--check", action="store_true", help="report without writing")
    args = parser.parse_args()

    print("ARCH-36 — Navigation, Sidebar & Routing Unification")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()
    return run(check_only=args.check)


if __name__ == "__main__":
    sys.exit(main())