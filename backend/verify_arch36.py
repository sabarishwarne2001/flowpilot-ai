#!/usr/bin/env python3
"""ARCH-36 — Navigation, Sidebar & Routing Unification: the gates.

    python verify_arch36.py                   offline (static) gates
    python verify_arch36.py --build           + tsc, eslint on touched files,
                                                vite build, and the tenant path
                                                self-check executed under Node
    python verify_arch36.py --db              + live Postgres probes
    python verify_arch36.py --mutate          + mutation kills
    python verify_arch36.py --build --db --mutate
    python verify_arch36.py --skip-regressions

Regression: verify_arch35.py (which itself runs 34, 33, 32, 31, 31_step0),
with --db when --db is set.

EXIT 0 pass | 1 a gate failed | 2 harness could not run

WHAT THESE GATES CLAIM
======================

ARCH-36 is a routing phase, so its defects are absences: a page with a route
and no link, a helper with no caller, a capability string that no longer
matches the backend. Absences do not fail a type check. Every static gate
below reads source and asserts a relationship between two files that nothing
else in the repository keeps true.

Each gate takes the roots it reads as arguments. `--mutate` copies
frontend/src and the three backend files the gates read into a temporary
directory, breaks one relationship, and runs the same gates there. A mutant
that survives is a gate that asserts nothing.

WHAT THEY DO NOT CLAIM
======================

They do not render the sidebar. Whether the lock icon looks right, or the
palette traps focus correctly in a given browser, is checked by using it. The
checklist is in the ARCH-36 blueprint, §Manual acceptance.
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
BACKEND = HERE
FRONTEND = HERE.parent / "frontend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

EXPECTED_HEAD = "arch35_step1_calibration"
REGRESSIONS = ("verify_arch35.py",)

BACKEND_FILES = (
    "app/core/entitlements.py",
    "app/core/slugs.py",
    "app/services/redaction/vocabulary.py",
)

TOUCHED_FRONTEND = (
    "src/components/layout/CommandPalette.tsx",
    "src/components/layout/commandPaletteEvents.ts",
    "src/components/layout/navigation.ts",
    "src/components/layout/SidebarNavigation.tsx",
    "src/components/redaction/StartRedactionButton.tsx",
    "src/constants/capabilities.ts",
    "src/hooks/useGrantedCapabilities.ts",
    "src/hooks/useIsPartnerMember.ts",
    "src/pages/Assertions/AssertionReviewPage.tsx",
    "src/routes/tenantPaths.ts",
    "src/App.tsx",
    "src/layouts/DashboardLayout.tsx",
    "src/pages/WorkItems/WorkItemDetails.tsx",
    "src/pages/procurement/CaseQueue.tsx",
    "src/pages/Automation/Automation.tsx",
)

SENTINELS = {
    "src/components/layout/navigation.ts": "ARCH36-S1:grouped-navigation",
    "src/components/layout/SidebarNavigation.tsx": "ARCH36-S1:sidebar-groups",
    "src/routes/tenantPaths.ts": "ARCH36-S1:tenant-paths",
    "src/App.tsx": "ARCH36-S1:assertions-route",
    "src/layouts/DashboardLayout.tsx": "ARCH36-S1:command-palette-mount",
    "src/pages/WorkItems/WorkItemDetails.tsx": "ARCH36-S1:redaction-entry-mount",
    "src/pages/procurement/CaseQueue.tsx": "ARCH36-S1:policies-link",
    "src/pages/Automation/Automation.tsx": "ARCH36-S1:timeline-link",
}

# ---------------------------------------------------------------------------
# Written exemptions. Each carries the reason, and each reason is itself
# checked by a gate below — an exemption is a claim, not a waiver.
# ---------------------------------------------------------------------------

#: Workspace routes with no parameter that deliberately have no sidebar row.
SIDEBAR_EXEMPT = {
    "workspaceProcurementPolicies": (
        "reached from the matching queue header and offered by the command "
        "palette (gate: entry points)"
    ),
}

#: Organization routes with no sidebar row.
ORG_ROUTE_EXEMPT = {
    "organizationBillingReturn": "payment gateway return URL built by PlanSelector",
    "organizationNewWorkspace": "opened from the workspace switcher, the picker and the palette",
}

#: Exported `*Path` names with no caller outside tenantPaths.ts.
HELPER_EXEMPT = {
    "organizationPath": "building block for every organization helper",
    "platformPath": "/admin has no index page; SuperAdminGuard owns the shell",
    "procurementCasePath": "CaseQueue links each row relatively (./{id})",
    "isTenantPath": "a predicate, not a path builder",
}

#: Page modules that nothing imports.
PAGE_EXEMPT = {
    "pages/Automation/RuleEditor": (
        "dead before ARCH-36; ARCH-37 replaces the rule builder and deletes it"
    ),
}

#: Which page enforces the capability a gated navigation entry advertises.
GATED_PAGES = {
    "workspaceProcurement": "src/pages/procurement/CaseQueue.tsx",
    "workspaceProcurementPolicies": "src/pages/procurement/TolerancePolicyEditor.tsx",
    "workspaceRadar": "src/pages/radar/ForensicAuditRadar.tsx",
    "workspaceAssertions": "src/pages/Assertions/AssertionReviewPage.tsx",
    # ARCH41-S3:gated-page. The nav entry advertises capability.extraction_memory
    # and this page enforces the same key through useCapabilityAccess.
    "workspaceExtractionMemory": "src/pages/extractionMemory/ExtractionMemory.tsx",
    # ARCH42-S1:gated-page. The nav entry advertises capability.entity_graph and
    # this page enforces the same key through useCapabilityAccess.
    "workspaceEntities": "src/pages/entities/Entities.tsx",
    # ARCH43-S1:gated-pages. Both nav entries advertise capability.case_intelligence
    # and both pages enforce the same key through useCapabilityAccess.
    "workspaceCases": "src/pages/cases/Cases.tsx",
    "workspacePacketSplits": "src/pages/packets/PacketSplits.tsx",
    # ARCH44-S1:gated-page. The nav entry advertises capability.table_intelligence
    # and this page enforces the same key through useCapabilityAccess.
    "workspaceTables": "src/pages/tables/Tables.tsx",
}


class Recorder:
    def __init__(self) -> None:
        self.results: list[tuple[str, bool, str]] = []

    def check(self, name: str, fn: Callable[[], None]) -> bool:
        try:
            fn()
        except AssertionError as exc:
            self.results.append((name, False, str(exc)))
            return False
        except Exception as exc:  # noqa: BLE001
            self.results.append(
                (name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
            return False
        self.results.append((name, True, ""))
        return True

    @property
    def failed(self) -> int:
        return sum(1 for _, ok, _ in self.results if not ok)

    def report(self, title: str) -> None:
        print(f"\n--- {title} ---")
        for name, ok, detail in self.results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
            if not ok and detail:
                for line in detail.splitlines()[:8]:
                    print(f"         {line}")
        print(f"  {len(self.results) - self.failed}/{len(self.results)} passed")


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    if not path.exists():
        raise AssertionError(f"missing file: {path}")
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def _strip_ts_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", r"\1", line) for line in text.splitlines())


def _block(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


@dataclass(frozen=True)
class Roots:
    frontend: Path
    backend: Path

    def fe(self, rel: str) -> str:
        return _read(self.frontend / rel)

    def be(self, rel: str) -> str:
        return _read(self.backend / rel)


def route_patterns(paths_src: str) -> dict[str, str]:
    block = _block(paths_src, "export const ROUTE_PATTERNS = {", "} as const;")
    out: dict[str, str] = {}
    for key, quote, value in re.findall(r'^\s*(\w+):\s*(["`])(.*?)\2,', block, re.M):
        out[key] = value
    assert out, "ROUTE_PATTERNS could not be parsed"
    return out


def is_parameterised(value: str) -> bool:
    return ":" in value or "${" in value


def reserved_segments(paths_src: str) -> set[str]:
    block = _block(paths_src, "RESERVED_ROUTE_SEGMENTS", "]);")
    return set(re.findall(r'^\s*"([a-z0-9-]+)",', _strip_ts_comments(block), re.M))


def nav_items(nav_src: str) -> list[dict[str, str]]:
    items = []
    for match in re.finditer(r'\{\s*id: "(?P<id>[^"]+)",(?P<body>.*?)\n\s*\}', nav_src, re.S):
        body = match.group("body")
        item = {"id": match.group("id")}
        for field in ("name", "route", "minimumRole"):
            found = re.search(rf'\b{field}: "([^"]+)"', body)
            if found:
                item[field] = found.group(1)
        cap = re.search(r"\bcapability: ([^,\n]+)", body)
        if cap:
            item["capability"] = cap.group(1).strip()
        items.append(item)
    assert items, "no navigation entries could be parsed from navigation.ts"
    return items


def frontend_capabilities(src: str) -> dict[str, str]:
    block = _block(src, "export const CAPABILITY = {", "} as const;")
    return dict(re.findall(r'^\s*(\w+): "([^"]+)",', block, re.M))


def _module_string_constants(tree: ast.Module) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in tree.body:
        value = getattr(node, "value", None)
        if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (
            [node.target] if isinstance(node, ast.AnnAssign) else []
        )
        for target in targets:
            if isinstance(target, ast.Name):
                out[target.id] = value.value
    return out


def backend_capabilities(src: str) -> set[str]:
    """CAPABILITY_KEYS, resolved through the module's own constants (AST)."""
    tree = ast.parse(src)
    constants = _module_string_constants(tree)
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None
        )
        if isinstance(target, ast.Name) and target.id == "CAPABILITY_KEYS":
            assert isinstance(node.value, ast.Tuple), "CAPABILITY_KEYS is not a tuple literal"
            out = set()
            for element in node.value.elts:
                if isinstance(element, ast.Name):
                    assert element.id in constants, f"{element.id} has no string literal"
                    out.add(constants[element.id])
                elif isinstance(element, ast.Constant):
                    out.add(element.value)
            assert out, "CAPABILITY_KEYS is empty"
            return out
    raise AssertionError("CAPABILITY_KEYS not found in entitlements.py")


def backend_reserved_slugs(src: str) -> set[str]:
    tree = ast.parse(src)
    for node in tree.body:
        target = node.target if isinstance(node, ast.AnnAssign) else (
            node.targets[0] if isinstance(node, ast.Assign) else None
        )
        if isinstance(target, ast.Name) and target.id == "RESERVED_SLUGS":
            return {
                n.value
                for n in ast.walk(node)
                if isinstance(n, ast.Constant) and isinstance(n.value, str)
            }
    raise AssertionError("RESERVED_SLUGS not found in slugs.py")


def backend_profiles(src: str) -> set[str]:
    tree = ast.parse(src)
    constants: dict[str, str] = {}
    keys: list[ast.expr] = []
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    constants[target.id] = node.value.value
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "PROFILES" and isinstance(node.value, ast.Dict):
                keys = [k for k in node.value.keys if k is not None]
    assert keys, "PROFILES dict not found in redaction/vocabulary.py"
    out = set()
    for key in keys:
        if isinstance(key, ast.Name):
            out.add(constants[key.id])
        elif isinstance(key, ast.Constant):
            out.add(key.value)
    return out


def frontend_profiles(src: str) -> set[str]:
    block = _block(src, "export const REDACTION_PROFILE_LABELS", "\n};")
    return set(re.findall(r"^  (\w+): \{", block, re.M))


def _source_files(src_root: Path) -> dict[Path, str]:
    out = {}
    for path in src_root.rglob("*"):
        if path.suffix in (".ts", ".tsx") and not path.name.endswith(".d.ts"):
            out[path] = _read(path)
    return out


# ===========================================================================
# Static gates
# ===========================================================================


def gates_static(rec: Recorder, roots: Roots) -> None:
    fe = roots.frontend
    src = fe / "src"
    paths = roots.fe("src/routes/tenantPaths.ts")
    nav = roots.fe("src/components/layout/navigation.ts")
    nav_code = _strip_ts_comments(nav)
    sidebar = _strip_ts_comments(roots.fe("src/components/layout/SidebarNavigation.tsx"))
    palette = _strip_ts_comments(roots.fe("src/components/layout/CommandPalette.tsx"))
    app = _strip_ts_comments(roots.fe("src/App.tsx"))
    patterns = route_patterns(paths)
    items = nav_items(nav_code)
    sidebar_block = _block(nav_code, "export const buildWorkspaceNavigationGroups", "export const buildWorkspaceShortcutItems")
    sidebar_routes = set(re.findall(r'route: "(\w+)"', sidebar_block))

    # -- A1 reachability ----------------------------------------------------

    def workspace_routes_reachable() -> None:
        missing = []
        for key, value in patterns.items():
            if not key.startswith("workspace") or key.endswith("Shell"):
                continue
            if is_parameterised(value):
                continue
            if key not in sidebar_routes and key not in SIDEBAR_EXEMPT:
                missing.append(key)
        assert not missing, (
            f"workspace route(s) with no sidebar entry and no written exemption: {missing}"
        )
        stale = [k for k in SIDEBAR_EXEMPT if k in sidebar_routes or k not in patterns]
        assert not stale, f"exemption(s) no longer needed or no longer real: {stale}"

    rec.check("A1 every unparameterised workspace route has a sidebar entry or an exemption", workspace_routes_reachable)

    def nav_routes_are_real() -> None:
        unknown = [i["route"] for i in items if i.get("route") not in patterns]
        assert not unknown, f"navigation names route pattern(s) that do not exist: {unknown}"
        ids = [i["id"] for i in items]
        assert len(ids) == len(set(ids)), f"duplicate navigation ids: {ids}"

    rec.check("A1 every navigation entry names a real route pattern, ids unique", nav_routes_are_real)

    def organization_routes_reachable() -> None:
        org_nav = _block(nav_code, "export const buildOrganizationNavigationItems", "export const buildPlatformNavigationItems")
        missing = []
        for key, value in patterns.items():
            if not key.startswith("organization") or key.endswith("Shell"):
                continue
            if key in ORG_ROUTE_EXEMPT:
                continue
            if f"{key}Path(orgSlug)" not in org_nav:
                missing.append(key)
        assert not missing, f"organization route(s) with no navigation entry: {missing}"
        assert "platformMarginsPath()" in nav_code, "platform margins lost its entry"
        assert "partnerPortalPath()" in nav_code, "the partner portal has no entry"

    rec.check("A1 every organization, platform and partner route has an entry or an exemption", organization_routes_reachable)

    def parameterised_routes_have_entry_points() -> None:
        work_items = _read(src / "pages/WorkItems/WorkItems.tsx")
        tray = _read(src / "components/notification/NotificationTray.tsx")
        cases = _strip_ts_comments(_read(src / "pages/procurement/CaseQueue.tsx"))
        button = _strip_ts_comments(_read(src / "components/redaction/StartRedactionButton.tsx"))
        details = _strip_ts_comments(_read(src / "pages/WorkItems/WorkItemDetails.tsx"))
        automation = _strip_ts_comments(_read(src / "pages/Automation/Automation.tsx"))
        switcher = _read(src / "components/layout/OrgWorkspaceSwitcher.tsx")
        plans = _read(src / "pages/billing/PlanSelector.tsx")

        assert "getDetailsPath(item.id)" in work_items and "workItemDetailsPath(" in tray, (
            "document details lost an entry point"
        )
        assert "to={`./${row.id}`}" in cases, "case rows no longer link to the comparison grid"
        assert 'to="./policies"' in cases, "the matching queue no longer links to tolerance policies"
        assert 'to="./timeline"' in automation, "workflows no longer link to the run history"
        assert "startRedaction(" in button and "redactionPath(" in button, (
            "the redaction button must create a job and open the studio with its id"
        )
        assert "<StartRedactionButton" in details, "the document page no longer renders the redaction entry"
        assert "createWorkspacePath(" in switcher, "the switcher lost its create-workspace action"
        assert "organizationBillingReturnPath(" in plans, "checkout no longer returns to the return page"
        assert "buildWorkspaceShortcutItems" in palette and '"workspaceProcurementPolicies"' in nav_code, (
            "the palette no longer offers tolerance policies, which SIDEBAR_EXEMPT relies on"
        )

    rec.check("A1 parameterised and exempt routes have in-app entry points", parameterised_routes_have_entry_points)

    def assertions_routed() -> None:
        assert patterns.get("workspaceAssertions") == "assertions"
        assert "ROUTE_PATTERNS.workspaceAssertions" in app and "<AssertionReviewPage />" in app
        assert 'import("@/pages/Assertions/AssertionReviewPage")' in app
        page = _strip_ts_comments(_read(src / "pages/Assertions/AssertionReviewPage.tsx"))
        assert "<AssertionReviewQueue" in page and "hasCapability={capability.granted}" in page
        assert app.index("workspaceProcurementPolicies") < app.index("workspaceProcurementCase"), (
            "the policies route must precede the :caseId route (ARCH-31)"
        )

    rec.check("A1 ARCH-33's review queue is routed; policies still precede :caseId", assertions_routed)

    # -- A2 drift -------------------------------------------------------------

    def no_orphan_helpers() -> None:
        helpers = re.findall(r"^export const (\w+Path) =", paths, re.M)
        files = {p: t for p, t in _source_files(src).items() if p.name != "tenantPaths.ts"}
        orphans = [
            h for h in helpers
            if h not in HELPER_EXEMPT
            and not any(re.search(rf"\b{h}\b", t) for t in files.values())
        ]
        assert not orphans, f"exported path helper(s) with no caller: {orphans}"
        now_used = [
            h for h in HELPER_EXEMPT
            if h in helpers and any(re.search(rf"\b{h}\b", t) for t in files.values())
        ]
        assert not now_used, f"exemption(s) no longer needed: {now_used}"
        assert "organizationPath(orgSlug)" in paths

    rec.check("A2 no exported path helper is left without a caller", no_orphan_helpers)

    def no_orphan_pages() -> None:
        files = _source_files(src)
        orphans = []
        for page in sorted(src.glob("pages/**/*.tsx")):
            rel = page.relative_to(src).with_suffix("").as_posix()
            if rel in PAGE_EXEMPT:
                continue
            spec = f"@/{rel}"
            found = False
            for other, text in files.items():
                if other == page:
                    continue
                if f'{spec}"' in text or f"{spec}'" in text:
                    found = True
                    break
                if other.parent == page.parent and re.search(rf'from "\./{re.escape(page.stem)}"', text):
                    found = True
                    break
            if not found:
                orphans.append(rel)
        assert not orphans, f"page module(s) nothing imports: {orphans}"

    rec.check("A2 every page module is routed or imported", no_orphan_pages)

    def capability_parity() -> None:
        backend = backend_capabilities(roots.be("app/core/entitlements.py"))
        frontend = frontend_capabilities(roots.fe("src/constants/capabilities.ts"))
        assert set(frontend.values()) == backend, (
            f"frontend {sorted(frontend.values())} != backend {sorted(backend)}"
        )
        strays = {}
        for path, text in _source_files(src).items():
            for literal in re.findall(r'"(capability\.[a-z_]+)"', text):
                if literal not in backend:
                    strays.setdefault(literal, []).append(path.relative_to(src).as_posix())
        assert not strays, f"capability literal(s) the backend does not define: {strays}"

    rec.check("A2 capability keys match app/core/entitlements.py", capability_parity)

    def gated_entries_match_their_pages() -> None:
        frontend = frontend_capabilities(roots.fe("src/constants/capabilities.ts"))
        gated = [i for i in items if "capability" in i]
        assert gated, "no capability-gated navigation entries"
        for item in gated:
            expr = item["capability"]
            assert expr.startswith("CAPABILITY."), (
                f"{item['id']} uses {expr}; gated entries must use the CAPABILITY constants"
            )
            value = frontend[expr.split(".", 1)[1]]
            page_rel = GATED_PAGES.get(item["route"])
            assert page_rel, f"{item['id']} routes to {item['route']}, which GATED_PAGES does not know"
            page = _read(fe / page_rel)
            assert value in page or expr in page, (
                f"{item['id']} advertises {value} but {page_rel} gates on something else"
            )
        ungated = [
            route for route in GATED_PAGES
            if not any(i.get("route") == route and "capability" in i for i in items)
        ]
        assert not ungated, f"gated page(s) whose entry shows no lock: {ungated}"

    rec.check("A2 every locked entry advertises the capability its page enforces", gated_entries_match_their_pages)

    def profile_parity() -> None:
        backend = backend_profiles(roots.be("app/services/redaction/vocabulary.py"))
        frontend = frontend_profiles(_read(src / "components/redaction/StartRedactionButton.tsx"))
        assert frontend == backend, f"frontend profiles {sorted(frontend)} != backend {sorted(backend)}"

    rec.check("A2 redaction profiles match app/services/redaction/vocabulary.py", profile_parity)

    def reserved_segment_parity() -> None:
        frontend = reserved_segments(paths)
        backend = backend_reserved_slugs(roots.be("app/core/slugs.py"))
        assert "partners" in frontend, "the frontend no longer reserves 'partners'"
        assert "partners" in backend, "app/core/slugs.py no longer reserves 'partners'"
        loose = sorted(frontend - backend)
        assert not loose, (
            f"segment(s) reserved in the browser but not on the backend: {loose}. "
            "An organization could take that slug and become unreachable."
        )

    rec.check("A2 every reserved route segment is also a reserved slug", reserved_segment_parity)

    # -- A3 gating and rendering ---------------------------------------------

    def role_gating() -> None:
        settings = [i for i in items if i.get("route") == "workspaceSettings"]
        assert settings and settings[0].get("minimumRole") == "CONTRIBUTOR", (
            "Settings must require CONTRIBUTOR; viewers cannot open its tabs"
        )
        assert "isAtLeast(workspaceRole, item.minimumRole)" in sidebar
        assert "isAtLeast(workspaceRole, item.minimumRole)" in palette, (
            "the palette must not offer a page the sidebar hides"
        )

    rec.check("A3 role gating is read from the model by sidebar and palette alike", role_gating)

    def lock_rendering() -> None:
        assert "useGrantedCapabilities(" in sidebar
        assert "!capabilities.isLoading" in sidebar and "!capabilities.granted.has(item.capability)" in sidebar
        assert sidebar.count("item.locked && (") == 2 and sidebar.count("<Lock") == 2, (
            "the lock must be drawn in both the expanded and the collapsed sidebar"
        )
        assert "(not included in your plan)" in sidebar, "a locked entry must say so to a screen reader"
        assert "buildWorkspaceNavigationGroups(" in sidebar
        hook = _strip_ts_comments(roots.fe("src/hooks/useGrantedCapabilities.ts"))
        access = _strip_ts_comments(roots.fe("src/hooks/useCapabilityAccess.ts"))
        assert "entitlementKeys.all(organizationId)" in hook and "entitlementKeys.all(organizationId)" in access, (
            "the sidebar and the pages must share one entitlements query"
        )

    rec.check("A3 locked entries render, announce themselves, and share the page's query", lock_rendering)

    def palette_wired() -> None:
        layout = _strip_ts_comments(roots.fe("src/layouts/DashboardLayout.tsx"))
        assert "<CommandPalette />" in layout, "the palette is not mounted"
        assert "buildWorkspaceNavigationGroups(" in palette, "the palette must render from the navigation model"
        for needle in (
            "(event.metaKey || event.ctrlKey)",
            '=== "k"',
            'case "ArrowDown":',
            'case "ArrowUp":',
            'case "Enter":',
            'case "Escape":',
            'role="listbox"',
            'aria-modal="true"',
            "restoreFocusRef",
            "OPEN_COMMAND_PALETTE_EVENT",
        ):
            assert needle in palette, f"palette is missing {needle}"
        assert "openCommandPalette" in sidebar, "the sidebar lost its search button"

    rec.check("A3 command palette is mounted, keyboard-complete and model-driven", palette_wired)

    def redaction_button_guards() -> None:
        button = _strip_ts_comments(_read(src / "components/redaction/StartRedactionButton.tsx"))
        assert '"application/pdf"' in button, "the button must be offered for PDFs only"
        assert 'status !== "COMPLETED"' in button
        assert 'isAtLeast(workspaceRole, "CONTRIBUTOR")' in button, "the endpoint requires CONTRIBUTOR"
        assert "CAPABILITY.redaction" in button
        assert "ApiError" in button and "error.response" not in button

    rec.check("A3 the redaction entry mirrors the endpoint's own preconditions", redaction_button_guards)

    # -- A4 earlier phases ----------------------------------------------------

    def earlier_invariants() -> None:
        layout = roots.fe("src/layouts/DashboardLayout.tsx")
        cases = roots.fe("src/pages/procurement/CaseQueue.tsx")
        org_sidebar = roots.fe("src/components/layout/OrganizationSidebarNavigation.tsx")
        assert "organizationAutonomyPath(orgSlug)" in nav, "verify_arch35 asserts this"
        assert "useTimezoneCapture()" in layout, "verify_arch30_tranche4 asserts this"
        assert "formatTimestamp" in cases and "toLocaleString()" not in cases, "verify_arch31 asserts this"
        assert '"Calibrated autonomy": "Governance & security"' in org_sidebar
        for name in ("Calibrated autonomy", "Partner marketplace", "Audit log", "Billing"):
            assert f'name: "{name}"' in nav, f"organization entry {name!r} disappeared"

    rec.check("A4 invariants earlier gates assert still hold", earlier_invariants)

    def no_migration() -> None:
        versions = roots.backend / "alembic" / "versions"
        if versions.exists():
            stray = [p.name for p in versions.glob("*.py") if "arch36" in p.name.lower()]
            assert not stray, f"ARCH-36 has no DDL, but found {stray}"

    rec.check("A4 ARCH-36 adds no migration", no_migration)


def gates_applied(rec: Recorder) -> None:
    def sentinels() -> None:
        missing = [rel for rel, s in SENTINELS.items() if s not in _read(FRONTEND / rel)]
        assert not missing, f"not applied: {missing}. Run python apply_arch36.py"
        assert "ARCH36-S1:reserved-partners" in _read(BACKEND / "app/core/slugs.py")

    rec.check("apply: every sentinel is present", sentinels)

    def idempotent() -> None:
        script = BACKEND / "apply_arch36.py"
        assert script.exists(), "apply_arch36.py is missing"
        done = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=str(BACKEND), capture_output=True, text=True, timeout=120,
        )
        assert done.returncode == 0, done.stdout[-800:]
        assert "0 file(s) would change" in done.stdout, done.stdout[-800:]

    rec.check("apply: a second run would change nothing", idempotent)

    def slugs_import() -> None:
        spec_src = _read(BACKEND / "app/core/slugs.py")
        compile(spec_src, "slugs.py", "exec")
        assert "partners" in backend_reserved_slugs(spec_src)

    rec.check("apply: app/core/slugs.py still compiles and reserves 'partners'", slugs_import)


# ===========================================================================
# Build
# ===========================================================================


def _npx() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def _run(command: list[str], *, cwd: Path, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(
        command, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        shell=False,
    )


def gates_build(rec: Recorder) -> None:
    def node_modules() -> None:
        assert (FRONTEND / "node_modules").is_dir(), (
            "frontend/node_modules is missing. Note: `npm ci` fails on this "
            "repository because package-lock.json is out of sync with "
            "package.json; run `npm install` (see the blueprint, finding R-1)."
        )

    if not rec.check("build: node_modules present", node_modules):
        return

    def typecheck() -> None:
        done = _run([_npx(), "tsc", "--noEmit", "-p", "tsconfig.json"], cwd=FRONTEND)
        assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]

    rec.check("build: tsc --noEmit is clean", typecheck)

    def lint() -> None:
        done = _run([_npx(), "eslint", *TOUCHED_FRONTEND], cwd=FRONTEND)
        assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]

    rec.check("build: eslint is clean on every file ARCH-36 touches", lint)

    def vite() -> None:
        done = _run([_npx(), "vite", "build"], cwd=FRONTEND)
        assert done.returncode == 0, (done.stdout + done.stderr)[-2000:]

    rec.check("build: vite build succeeds", vite)

    def self_check() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            module = Path(tmp) / "tenantPaths.mts"
            shutil.copyfile(FRONTEND / "src/routes/tenantPaths.ts", module)
            script = (
                f"import({module.as_uri()!r}).then((m) => {{"
                "const f = m.runTenantPathSelfCheck();"
                "if (f.length) { console.error(JSON.stringify(f)); process.exit(1); }"
                "console.log('ok'); })"
            )
            done = _run(
                ["node", "--experimental-strip-types", "--no-warnings", "-e", script],
                cwd=FRONTEND, timeout=60,
            )
            assert done.returncode == 0 and "ok" in done.stdout, (
                "Node 22.6+ is needed for --experimental-strip-types.\n"
                + (done.stdout + done.stderr)[-1500:]
            )

    rec.check("build: the tenant path self-check passes when executed (Node --experimental-strip-types)", self_check)


# ===========================================================================
# Database
# ===========================================================================


def _database_url(explicit: Optional[str]) -> str:
    if explicit:
        return explicit
    if os.environ.get("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    from app.db.session import engine

    return engine.url.render_as_string(hide_password=False)


def gates_db(rec: Recorder) -> None:
    from sqlalchemy import text as sql

    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        def head() -> None:
            value = db.execute(sql("SELECT version_num FROM alembic_version")).scalar_one()
            # ARCH39-S1:head-widened-36. ARCH-36 added no migration; ARCH-39 does.
            assert value in (EXPECTED_HEAD, "arch39_step1_conversations", "arch37_step1_flow_builder", "arch38_step1_batches", "arch40_step2_settings_backfill", "arch40_step2a_review_view_paths", "arch40_step3_contract_ai_settings", "hm1_tier_price_per_key", "arch41_step1_extraction_memory", "arch42_step1_entity_graph", "arch43_step1_case_intelligence", "arch44_step1_table_intelligence"), (  # ARCH37-S1:head-widened-36  ARCH38-S1:head-widened-36  ARCH40-S1:head-widened-36  # HM-S1:head-widened  ARCH41-S2:head-widened-36  ARCH42-S1:head-widened-36  ARCH43-S1:head-widened-36  ARCH44-S1:head-widened-36
                f"alembic head is {value}; expected {EXPECTED_HEAD} or later"
            )

        rec.check(f"DB: head is still {EXPECTED_HEAD}", head)

        def no_org_shadows_a_route() -> None:
            segments = sorted(reserved_segments(_read(FRONTEND / "src/routes/tenantPaths.ts")))
            rows = db.execute(
                sql("SELECT slug FROM organizations WHERE lower(slug) = ANY(:segments)"),
                {"segments": segments},
            ).scalars().all()
            assert not rows, (
                f"organization slug(s) {rows} shadow a reserved route. Rename them "
                "before deploying ARCH-36, or those tenants' workspaces are unreachable."
            )

        rec.check("DB: no organization slug shadows a reserved route segment", no_org_shadows_a_route)

        def capability_rows_known() -> None:
            backend = backend_capabilities(_read(BACKEND / "app/core/entitlements.py"))
            exists = db.execute(
                sql("SELECT to_regclass('public.quota_tier_entries') IS NOT NULL")
            ).scalar_one()
            if not exists:
                return
            rows = db.execute(
                sql(
                    "SELECT DISTINCT limit_key FROM quota_tier_entries "
                    "WHERE limit_key LIKE 'capability.%'"
                )
            ).scalars().all()
            unknown = sorted(set(rows) - backend)
            assert not unknown, f"tier rows grant capability keys no code knows: {unknown}"

        rec.check("DB: every capability granted by a tier is one the sidebar knows", capability_rows_known)
    finally:
        db.rollback()
        db.close()


# ===========================================================================
# Mutation
# ===========================================================================

MUTANTS: tuple[dict[str, str], ...] = (
    {
        "id": "M01", "name": "radar entry removed from the sidebar", "must": "die",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": 'route: "workspaceRadar",', "replace": 'route: "workspaceDashboard",',
    },
    {
        "id": "M02", "name": "notifications entry removed", "must": "die",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": 'route: "workspaceNotifications",', "replace": 'route: "workspaceWorkItems",',
    },
    {
        "id": "M03", "name": "Settings opened to viewers", "must": "die",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": 'minimumRole: "CONTRIBUTOR",', "replace": 'minimumRole: "VIEWER",',
    },
    {
        "id": "M04", "name": "a capability key renamed in the frontend only", "must": "die",
        "file": "frontend/src/constants/capabilities.ts",
        "find": '"capability.anomaly_radar"', "replace": '"capability.anomaly_radar_v2"',
    },
    {
        "id": "M05", "name": "a capability added to the backend only", "must": "die",
        "file": "backend/app/core/entitlements.py",
        "find": "    CALIBRATED_AUTONOMY_CAPABILITY,\n)",
        "replace": "    CALIBRATED_AUTONOMY_CAPABILITY,\n    REDACTION_CAPABILITY_V2,\n)\nREDACTION_CAPABILITY_V2: str = \"capability.redaction_v2\"",
    },
    {
        "id": "M06", "name": "radar entry advertises the wrong capability", "must": "die",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": "capability: CAPABILITY.anomalyRadar,", "replace": "capability: CAPABILITY.redaction,",
    },
    {
        "id": "M07", "name": "a redaction profile the engine does not define", "must": "die",
        "file": "frontend/src/components/redaction/StartRedactionButton.tsx",
        "find": "  india_kyc: {", "replace": "  gdpr_basic: {",
    },
    {
        "id": "M08", "name": "'partners' dropped from the backend reservation", "must": "die",
        "file": "backend/app/core/slugs.py",
        "find": '        "partners",\n', "replace": "",
    },
    {
        "id": "M09", "name": "palette unmounted", "must": "die",
        "file": "frontend/src/layouts/DashboardLayout.tsx",
        "find": "      <CommandPalette />\n", "replace": "",
    },
    {
        "id": "M10", "name": "policies route moved after :caseId", "must": "die",
        "file": "frontend/src/App.tsx",
        "find": (
            "                    <Route\n"
            "                      path={ROUTE_PATTERNS.workspaceProcurementPolicies}\n"
            "                      element={<TolerancePolicyEditor />}\n"
            "                    />\n"
            "                    <Route\n"
            "                      path={ROUTE_PATTERNS.workspaceProcurementCase}\n"
            "                      element={<ThreeWayComparison />}\n"
            "                    />\n"
        ),
        "replace": (
            "                    <Route\n"
            "                      path={ROUTE_PATTERNS.workspaceProcurementCase}\n"
            "                      element={<ThreeWayComparison />}\n"
            "                    />\n"
            "                    <Route\n"
            "                      path={ROUTE_PATTERNS.workspaceProcurementPolicies}\n"
            "                      element={<TolerancePolicyEditor />}\n"
            "                    />\n"
        ),
    },
    {
        "id": "M11", "name": "redaction entry removed from the document page", "must": "die",
        "file": "frontend/src/pages/WorkItems/WorkItemDetails.tsx",
        "find": "          <StartRedactionButton\n", "replace": "          <span data-removed\n",
    },
    {
        "id": "M12", "name": "lock no longer drawn", "must": "die",
        "file": "frontend/src/components/layout/SidebarNavigation.tsx",
        "find": "            {item.locked && (\n              <Lock\n                className=\"ml-2",
        "replace": "            {false && (\n              <Lock\n                className=\"ml-2",
    },
    {
        "id": "M13", "name": "tolerance policies link removed", "must": "die",
        "file": "frontend/src/pages/procurement/CaseQueue.tsx",
        "find": 'to="./policies"', "replace": 'to="."',
    },
    {
        "id": "M14", "name": "a new workspace route shipped with no link", "must": "die",
        "file": "frontend/src/routes/tenantPaths.ts",
        "find": '  workspaceNotifications: "notifications",',
        "replace": '  workspaceNotifications: "notifications",\n  workspaceReports: "reports",',
    },
    {
        "id": "M15", "name": "palette hides nothing by role", "must": "die",
        "file": "frontend/src/components/layout/CommandPalette.tsx",
        "find": "!isAtLeast(workspaceRole, item.minimumRole)", "replace": "false",
    },
    {
        "id": "M16", "name": "comment-only edit to navigation.ts", "must": "survive",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": "WHY THE GROUPS ARE DATA", "replace": "WHY THE GROUPS ARE DATA (edited)",
    },
    {
        "id": "M17", "name": "an entry's display name changes", "must": "survive",
        "file": "frontend/src/components/layout/navigation.ts",
        "find": 'name: "Run history",', "replace": 'name: "Execution history",',
    },
)


def _mutant_roots(tmp: Path) -> Roots:
    fe = tmp / "frontend"
    be = tmp / "backend"
    shutil.copytree(FRONTEND / "src", fe / "src")
    for rel in BACKEND_FILES:
        (be / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(BACKEND / rel, be / rel)
    return Roots(frontend=fe, backend=be)


def run_mutations(rec: Recorder) -> None:
    for mutant in MUTANTS:

        def make_gate(mutant: dict[str, str] = mutant) -> Callable[[], None]:
            def gate() -> None:
                with tempfile.TemporaryDirectory() as tmp:
                    roots = _mutant_roots(Path(tmp))
                    target = Path(tmp) / mutant["file"]
                    text = _read(target)
                    assert text.count(mutant["find"]) >= 1, (
                        f"{mutant['id']}: anchor not found in {mutant['file']}"
                    )
                    target.write_text(text.replace(mutant["find"], mutant["replace"], 1), encoding="utf-8")
                    sub = Recorder()
                    gates_static(sub, roots)
                    died = sub.failed > 0
                    if mutant["must"] == "die":
                        assert died, f"{mutant['id']} SURVIVED: {mutant['name']}"
                    else:
                        assert not died, (
                            f"{mutant['id']} DIED: {[n for n, ok, _ in sub.results if not ok]}"
                        )

            return gate

        verb = "DIES" if mutant["must"] == "die" else "SURVIVES"
        rec.check(f"{mutant['id']} {mutant['name']} -> {verb}", make_gate())


# ===========================================================================
# Regressions
# ===========================================================================


def run_regressions(rec: Recorder, *, db: bool, database_url: Optional[str]) -> None:
    for script in REGRESSIONS:
        path = BACKEND / script

        def make_gate(script: str = script, path: Path = path) -> Callable[[], None]:
            def gate() -> None:
                assert path.exists(), f"{script} is missing"
                command = [sys.executable, str(path)]
                if db:
                    command += ["--db", "--database-url", _database_url(database_url)]
                done = subprocess.run(
                    command, cwd=str(BACKEND), capture_output=True, text=True, timeout=3600
                )
                lines = (done.stdout or done.stderr).splitlines()
                print(f"         ({script}) {lines[-1] if lines else ''}")
                assert done.returncode == 0, (
                    f"{script} exited {done.returncode}\n" + "\n".join(done.stdout.splitlines()[-25:])
                )

            return gate

        rec.check(f"regression: {script}{' --db' if db else ''}", make_gate())


# ===========================================================================
# main
# ===========================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-36 verification")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--db", action="store_true")
    parser.add_argument("--mutate", action="store_true")
    parser.add_argument("--skip-regressions", action="store_true")
    parser.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    args = parser.parse_args()

    print("ARCH-36 — Navigation, Sidebar & Routing Unification")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")

    if not (FRONTEND / "src").is_dir():
        print("frontend/src not found; run from backend/")
        return 2

    offline = Recorder()
    gates_applied(offline)
    gates_static(offline, Roots(frontend=FRONTEND, backend=BACKEND))
    offline.report("offline")
    failed = offline.failed

    if args.build:
        build = Recorder()
        gates_build(build)
        build.report("build")
        failed += build.failed

    if args.db:
        live = Recorder()
        try:
            gates_db(live)
        except Exception:  # noqa: BLE001
            live.results.append(("database harness", False, traceback.format_exc()))
        live.report("database")
        failed += live.failed

    if args.mutate:
        mutate = Recorder()
        run_mutations(mutate)
        mutate.report("mutation")
        failed += mutate.failed

    if not args.skip_regressions:
        regression = Recorder()
        run_regressions(regression, db=args.db, database_url=args.database_url)
        regression.report("regression")
        failed += regression.failed

    print(f"\n{'FAILED' if failed else 'PASSED'} — {failed} gate(s) failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())