#!/usr/bin/env python3
"""ARCH-31 Steps 3-4 — anchored, idempotent wiring for MODIFIED files.

    python apply_arch31_final.py --check     # report, change nothing
    python apply_arch31_final.py             # apply
    python apply_arch31_final.py             # again: reports "already applied", no-op

New files are delivered whole and are not this script's business. This script
touches only files that already existed and must keep everything else in them.

WHY ANCHORS AND SENTINELS RATHER THAN LINE NUMBERS
==================================================

Line numbers rot the moment anything above them changes, and a patch that
applies at the wrong offset does not fail — it corrupts. Each edit here names
the exact text it replaces, asserts how many times that text occurs, and
writes a sentinel comment. Re-running finds the sentinel and skips.

An anchor that matches zero times is a HARD FAILURE, not a warning. It means
the file is not in the state this patch was written against, and continuing
would apply a subset of the changes and leave the tree in a state no gate
describes.

An anchor that matches more than its declared count is equally fatal: the
patch author believed the text was unique and it is not, so which occurrence
gets edited is luck.

BOM AND LINE ENDINGS
====================

`app/api/v1/router.py` begins with a UTF-8 BOM. `git config core.autocrlf` on
Windows leaves CRLF in the working tree. Reading with `utf-8-sig` and writing
back without preserving both would produce a whole-file diff on a one-line
change, and on `router.py` it would strip a BOM the file has carried since
ARCH-01. Every write here restores exactly what it found.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent
FRONTEND = BACKEND.parent / "frontend"

S_WEBHOOK = "ARCH-31 procurement matching. PUBLIC"
S_USAGE = "ARCH-31 \u00a73.2 \u2014 one scored procurement case"
S_GATE = "ARCH-31 Step 3 FIX"
S_HANDLERS = "ARCH31_JOB_TYPES"
S_PROFILES = "ARCH-31 procurement scoring"
S_SCHEDULER = "The sweep is what makes a late-arriving"
#: NOTE: a sentinel must be a substring of the text the patch WRITES.
#: The first cut of S_SCHEDULER read "ARCH-31. The sweep..." while the
#: replacement writes "(ARCH-31). The sweep...". It therefore never matched,
#: the second run re-attempted an anchor its own first run had consumed, and
#: the occurrence check refused with FATAL rather than corrupting the file.
#: That is the guard working; it is also why sentinels are asserted below.
S_ROUTER = "procurement.router"
S_AUDIT = "# ARCH-31 \u2014 procurement three-way matching."
S_PATHS = "workspaceProcurement"
S_APP = "ProcurementCaseQueue"
S_KEYS = "procurementKeys"


@dataclass
class Edit:
    """One anchored replacement within one file."""

    anchor: str
    replacement: str
    #: How many times `anchor` must occur. Anything else is a hard failure.
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    relpath: str
    #: "backend" or "frontend". A single script wires both halves because a
    #: route registered on one side and not the other is a 404 nobody tests.
    sentinel: str
    root: str = "backend"
    edits: list[Edit] = field(default_factory=list)
    #: Optional guard: a reason to skip this file entirely (e.g. the target
    #: symbol already exists for a different reason).
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _read(path: Path) -> tuple[str, str, bool]:
    """Return (text, newline, had_bom) with the file's own conventions intact."""
    raw = path.read_bytes()
    had_bom = raw.startswith(b"\xef\xbb\xbf")
    if had_bom:
        raw = raw[3:]
    text = raw.decode("utf-8")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.replace("\r\n", "\n"), newline, had_bom


def _write(path: Path, text: str, newline: str, had_bom: bool) -> None:
    body = text.replace("\n", newline) if newline != "\n" else text
    data = body.encode("utf-8")
    if had_bom:
        data = b"\xef\xbb\xbf" + data
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# The patches
#
# Ordered backend-first, then frontend. Nothing here depends on that order —
# each edit is independent and anchored — but a reader following a route from
# the handler to the screen reads them in the order the request travels.
# ---------------------------------------------------------------------------

PATCHES: list[FilePatch] = [
    # -- ARCH-09 event vocabulary -------------------------------------------
    FilePatch(
        relpath="app/core/webhook_events.py",
        sentinel=S_WEBHOOK,
        edits=[
            Edit(
                anchor='        "document.failed",\n    }\n)',
                replacement=(
                    '        "document.failed",\n'
                    "        # ARCH-31 procurement matching. PUBLIC rather than INTERNAL:\n"
                    "        # a tenant's AP automation is the intended consumer — these\n"
                    "        # say an invoice finished matching, was approved, or was\n"
                    "        # disputed, and every one of those is a thing an external\n"
                    "        # workflow should be able to react to. None of them names an\n"
                    "        # internal cost, a seat count or a provider.\n"
                    '        "procurement.completed",\n'
                    '        "procurement.approved",\n'
                    '        "procurement.disputed",\n'
                    "    }\n)"
                ),
                description="register the three procurement webhook events",
            ),
        ],
    ),
    # -- ARCH-14 usage vocabulary -------------------------------------------
    FilePatch(
        relpath="app/core/usage_events.py",
        sentinel=S_USAGE,
        edits=[
            Edit(
                anchor=(
                    '        description="One authenticated request served by the '
                    'public API gateway.",\n    ),\n)'
                ),
                replacement=(
                    '        description="One authenticated request served by the '
                    'public API gateway.",\n'
                    "    ),\n"
                    "    # ARCH-31 §3.2 — one scored procurement case, keyed on\n"
                    "    # input_digest so a re-score under a NEW policy bills (it is new\n"
                    "    # work) and a sweep that finds nothing changed does not.\n"
                    "    #\n"
                    "    # Billable: this is the phase's unit of value. REQUEST rather\n"
                    "    # than PAGE because the cost is the assignment, not the paper —\n"
                    "    # a two-line invoice and a two-hundred-line invoice are one case\n"
                    "    # each and the matcher's work between them differs by\n"
                    "    # microseconds.\n"
                    "    UsageEventType(\n"
                    '        name="procurement.case",\n'
                    "        unit=UsageUnit.REQUEST,\n"
                    "        emission=EmissionKind.OCCURRENCE,\n"
                    "        billable=True,\n"
                    '        default_provider="internal",\n'
                    '        description="One procurement case scored against a tolerance policy.",\n'
                    "    ),\n)"
                ),
                description="register the procurement.case meter",
            ),
        ],
    ),
    # -- the capability gate defect -----------------------------------------
    FilePatch(
        relpath="app/api/capability_gate.py",
        sentinel=S_GATE,
        edits=[
            Edit(
                anchor=(
                    "    tier = quota_service.resolve_tier(db, organization_id=organization_id)\n"
                    "    if tier is None:\n"
                    "        return False\n"
                    '    limits = getattr(tier, "limits", None) or {}\n'
                    "    if isinstance(limits, dict) and capability_key in limits:\n"
                    "        return True\n"
                    '    for row in getattr(tier, "entitlement_rows", None) or []:\n'
                    '        if getattr(row, "entitlement_key", None) == capability_key:\n'
                    "            return True\n"
                    "    return False"
                ),
                replacement=(
                    "    tier = quota_service.resolve_tier(db, organization_id=organization_id)\n"
                    "    if tier is None:\n"
                    "        return False\n"
                    "\n"
                    "    # ARCH-31 Step 3 FIX. The first cut read `tier.limits` and\n"
                    "    # `tier.entitlement_rows`. `resolve_tier` returns a\n"
                    "    # `quota_service._TierSnapshot`, which has NEITHER attribute — it\n"
                    "    # exposes `entries`, a tuple of `TierLimit`. Both getattr calls\n"
                    "    # therefore returned their defaults and this function returned\n"
                    "    # False for every organization on every tier, including tiers\n"
                    "    # that bundle the capability.\n"
                    "    #\n"
                    "    # Nothing failed loudly: a 402 with remedy PLAN_UPGRADE is the\n"
                    "    # NORMAL response for most customers, so a paying Enterprise\n"
                    "    # tenant being told to upgrade looks exactly like the intended\n"
                    "    # behaviour until they call support.\n"
                    "    #\n"
                    "    # `entitlement_service.tier_grants` had the correct shape all\n"
                    "    # along — `any(entry.limit_key == key for entry in tier.entries)`\n"
                    "    # — which is the reading used here. One source of truth for\n"
                    "    # \"does this tier grant X\", not two that disagree.\n"
                    "    return any(\n"
                    "        getattr(entry, \"limit_key\", None) == capability_key\n"
                    "        for entry in getattr(tier, \"entries\", ()) or ()\n"
                    "    )"
                ),
                description="read tier.entries, the attribute _TierSnapshot actually has",
            ),
        ],
    ),
    # -- job registration, place 1 of 3: the handler map ---------------------
    FilePatch(
        relpath="app/workers/handlers/__init__.py",
        sentinel=S_HANDLERS,
        edits=[
            Edit(
                anchor=(
                    "ARCH27_JOB_TYPES: frozenset[str] = frozenset(\n"
                    '    {"partner.rev_share_compute", "partner.rev_share_seal"}\n)'
                ),
                replacement=(
                    "ARCH27_JOB_TYPES: frozenset[str] = frozenset(\n"
                    '    {"partner.rev_share_compute", "partner.rev_share_seal"}\n)\n'
                    'ARCH31_JOB_TYPES: frozenset[str] = frozenset({"procurement.score"})'
                ),
                description="declare the ARCH-31 job vocabulary",
            ),
            Edit(
                anchor="    | ARCH27_JOB_TYPES\n)",
                replacement="    | ARCH27_JOB_TYPES\n    | ARCH31_JOB_TYPES\n)",
                description="add ARCH-31 to the phase union the registry is asserted against",
            ),
            Edit(
                anchor=(
                    "def _partner_rev_share_compute(payload: dict[str, Any]) -> dict[str, Any]:"
                ),
                replacement=(
                    "def _procurement_score(payload: dict[str, Any]) -> dict[str, Any]:\n"
                    "    from app.workers.handlers.procurement import handle_procurement_score\n"
                    "    return handle_procurement_score(payload)\n"
                    "\n"
                    "\n"
                    "def _partner_rev_share_compute(payload: dict[str, Any]) -> dict[str, Any]:"
                ),
                description="the handler thunk, importing lazily like every sibling",
            ),
            Edit(
                anchor='    "partner.rev_share_seal": _partner_rev_share_seal,\n}',
                replacement=(
                    '    "partner.rev_share_seal": _partner_rev_share_seal,\n'
                    "    # ARCH-31. Also listed on the LIGHT profile in\n"
                    "    # app/workers/profiles.py and in DEFAULT_SCHEDULE in\n"
                    "    # app/workers/scheduler.py. A handler here with no profile there\n"
                    "    # is a job that enqueues cleanly and never runs — and\n"
                    "    # assert_imports_match_profile() raises ProfileError at every\n"
                    "    # worker's startup on a handler no profile claims, so registering\n"
                    "    # this without the profile entry stops the entire fleet booting.\n"
                    '    "procurement.score": _procurement_score,\n}'
                ),
                description="register procurement.score in the handler map",
            ),
            Edit(
                anchor='    "ARCH27_JOB_TYPES",\n    "ALL_PHASE_JOB_TYPES",',
                replacement='    "ARCH27_JOB_TYPES",\n    "ARCH31_JOB_TYPES",\n    "ALL_PHASE_JOB_TYPES",',
                description="export the new phase constant",
            ),
        ],
    ),
    # -- job registration, place 2 of 3: the worker profile ------------------
    FilePatch(
        relpath="app/workers/profiles.py",
        sentinel=S_PROFILES,
        edits=[
            Edit(
                anchor=(
                    '            "partner.rev_share_compute",\n'
                    '            "partner.rev_share_seal",\n'
                    "        }\n    ),"
                ),
                replacement=(
                    '            "partner.rev_share_compute",\n'
                    '            "partner.rev_share_seal",\n'
                    "            # ARCH-31 procurement scoring. Integer arithmetic, a\n"
                    "            # Hungarian assignment over a matrix that is single-digit\n"
                    "            # by single-digit on real documents, and a lexical cosine\n"
                    "            # that imports nothing. The thin image is the right home,\n"
                    "            # and `similarity.LexicalBackend` is the default precisely\n"
                    "            # so that stays true — see similarity.py for why\n"
                    "            # SentenceTransformers cannot run here.\n"
                    "            #\n"
                    "            # As with every entry above, this is not optional\n"
                    "            # bookkeeping: assert_imports_match_profile() raises\n"
                    "            # ProfileError at every worker's startup on a handler no\n"
                    "            # profile claims.\n"
                    '            "procurement.score",\n'
                    "        }\n    ),"
                ),
                description="claim procurement.score on the LIGHT profile",
            ),
        ],
    ),
    # -- job registration, place 3 of 3: the scheduler -----------------------
    FilePatch(
        relpath="app/workers/scheduler.py",
        sentinel=S_SCHEDULER,
        edits=[
            Edit(
                anchor=(
                    '            "Start, advance and end add-on grace windows; halt custom domains "\n'
                    '            "and export schedules after grace (ARCH-30 D-6)."\n'
                    "        ),\n    ),\n)"
                ),
                replacement=(
                    '            "Start, advance and end add-on grace windows; halt custom domains "\n'
                    '            "and export schedules after grace (ARCH-30 D-6)."\n'
                    "        ),\n"
                    "    ),\n"
                    "    ScheduledJob(\n"
                    '        job_type="procurement.score",\n'
                    "        interval_seconds=300,\n"
                    "        description=(\n"
                    '            "Re-score procurement cases whose counterparts have since "\n'
                    '            "arrived (ARCH-31). The sweep is what makes a late-arriving "\n'
                    '            "goods receipt matter: nothing in a receipt\'s own arrival "\n'
                    '            "path knows which invoices it might now complete, so "\n'
                    '            "without this a Monday NOT_RECEIVED sits flagged forever "\n'
                    '            "after Thursday\'s delivery resolves it. Cheap to repeat: "\n'
                    '            "score_case writes nothing when the digest is unchanged."\n'
                    "        ),\n"
                    "    ),\n)"
                ),
                description="schedule the five-minute re-score sweep",
            ),
        ],
    ),
    # -- audit vocabulary on the Python side ---------------------------------
    FilePatch(
        relpath="app/models/audit_log.py",
        sentinel=S_AUDIT,
        edits=[
            Edit(
                anchor='    MANIFEST_INSTALLED = "MANIFEST_INSTALLED"',
                replacement=(
                    '    MANIFEST_INSTALLED = "MANIFEST_INSTALLED"\n'
                    "    # ARCH-31 — procurement three-way matching. The enum VALUES were\n"
                    "    # added to PostgreSQL by arch31_step1_procurement_vocabulary;\n"
                    "    # these are the Python members that may emit them. A member here\n"
                    "    # without the ALTER TYPE fails on insert, and the ALTER TYPE\n"
                    "    # without a member here is simply unreachable.\n"
                    '    MATCH_APPROVED = "MATCH_APPROVED"\n'
                    '    MATCH_DISPUTED = "MATCH_DISPUTED"\n'
                    '    MATCH_RESCORED = "MATCH_RESCORED"\n'
                    '    TOLERANCE_PUBLISHED = "TOLERANCE_PUBLISHED"'
                ),
                description="add the four ARCH-31 audit actions",
            ),
            Edit(
                anchor='    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"',
                replacement=(
                    '    MARKETPLACE_ITEM = "MARKETPLACE_ITEM"\n'
                    '    PROCUREMENT_CASE = "PROCUREMENT_CASE"\n'
                    '    PROCUREMENT_TOLERANCE_POLICY = "PROCUREMENT_TOLERANCE_POLICY"'
                ),
                description="add the two ARCH-31 audit resource types",
            ),
        ],
    ),
    # -- route registration (this file carries a UTF-8 BOM) ------------------
    FilePatch(
        relpath="app/api/v1/router.py",
        sentinel=S_ROUTER,
        edits=[
            Edit(
                anchor="    partner,\n    notifications,",
                replacement="    partner,\n    procurement,\n    notifications,",
                description="import the procurement module",
            ),
            Edit(
                anchor="api_router.include_router(marketplace.router)",
                replacement=(
                    "api_router.include_router(marketplace.router)\n"
                    "api_router.include_router(procurement.router)  # ARCH-31 three-way matching"
                ),
                description="mount the procurement router",
            ),
        ],
    ),
    # -- frontend ------------------------------------------------------------
    FilePatch(
        relpath="src/routes/tenantPaths.ts",
        root="frontend",
        sentinel=S_PATHS,
        edits=[
            Edit(
                anchor='  workspaceVerification: "verification",',
                replacement=(
                    '  workspaceVerification: "verification",\n'
                    "  // ARCH-31. No RESERVED_ROUTE_SEGMENTS entry is needed: this is a\n"
                    "  // child of the workspace shell, not a sibling of it, so\n"
                    "  // parseTenantPath never sees \"procurement\" in the org or\n"
                    "  // workspace position.\n"
                    '  workspaceProcurement: "procurement",\n'
                    '  workspaceProcurementCase: "procurement/:caseId",\n'
                    '  workspaceProcurementPolicies: "procurement/policies",'
                ),
                description="declare the three workspace procurement routes",
            ),
            Edit(
                anchor=(
                    "export const automationPath = (\n"
                    "  orgSlug: string,\n"
                    "  workspaceSlug: string,\n"
                    "): string => `${workspacePath(orgSlug, workspaceSlug)}/automation`;"
                ),
                replacement=(
                    "export const automationPath = (\n"
                    "  orgSlug: string,\n"
                    "  workspaceSlug: string,\n"
                    "): string => `${workspacePath(orgSlug, workspaceSlug)}/automation`;\n"
                    "\n"
                    "export const procurementPath = (\n"
                    "  orgSlug: string,\n"
                    "  workspaceSlug: string,\n"
                    "): string => `${workspacePath(orgSlug, workspaceSlug)}/procurement`;\n"
                    "\n"
                    "export const procurementCasePath = (\n"
                    "  orgSlug: string,\n"
                    "  workspaceSlug: string,\n"
                    "  caseId: string,\n"
                    "): string =>\n"
                    "  `${procurementPath(orgSlug, workspaceSlug)}/${encodeURIComponent(caseId)}`;\n"
                    "\n"
                    "export const procurementPoliciesPath = (\n"
                    "  orgSlug: string,\n"
                    "  workspaceSlug: string,\n"
                    "): string => `${procurementPath(orgSlug, workspaceSlug)}/policies`;"
                ),
                description="path builders",
            ),
        ],
    ),
    FilePatch(
        relpath="src/services/api/queryKeys.ts",
        root="frontend",
        sentinel=S_KEYS,
        edits=[
            Edit(
                anchor="export const invalidatePartner = async (",
                replacement=(
                    "/** ARCH-31 — procurement matching. Workspace-scoped, like work items. */\n"
                    "export const procurementKeys = {\n"
                    "  all: (workspaceId: string) =>\n"
                    '    [...workspaceScope(workspaceId), "procurement"] as const,\n'
                    "  cases: (workspaceId: string, filter: string, onlyExceptions: boolean) =>\n"
                    '    [...procurementKeys.all(workspaceId), "cases", filter, onlyExceptions] as const,\n'
                    "  case: (workspaceId: string, caseId: string) =>\n"
                    '    [...procurementKeys.all(workspaceId), "case", caseId] as const,\n'
                    "  policies: (workspaceId: string) =>\n"
                    '    [...procurementKeys.all(workspaceId), "policies"] as const,\n'
                    "};\n"
                    "\n"
                    "export const invalidatePartner = async ("
                ),
                description="procurement query keys",
            ),
        ],
    ),
    FilePatch(
        relpath="src/App.tsx",
        root="frontend",
        sentinel=S_APP,
        edits=[
            Edit(
                anchor=(
                    "const VerificationReviewQueue = lazy(\n"
                    '  () => import("@/pages/Verification/VerificationReviewQueue"),\n'
                    ");"
                ),
                replacement=(
                    "const VerificationReviewQueue = lazy(\n"
                    '  () => import("@/pages/Verification/VerificationReviewQueue"),\n'
                    ");\n"
                    "const ProcurementCaseQueue = lazy(\n"
                    '  () => import("@/pages/procurement/CaseQueue"),\n'
                    ");\n"
                    "const ThreeWayComparison = lazy(\n"
                    '  () => import("@/pages/procurement/ThreeWayComparison"),\n'
                    ");\n"
                    "const TolerancePolicyEditor = lazy(\n"
                    '  () => import("@/pages/procurement/TolerancePolicyEditor"),\n'
                    ");"
                ),
                description="lazy-load the three procurement screens",
            ),
            Edit(
                anchor=(
                    "                    <Route\n"
                    "                      path={ROUTE_PATTERNS.workspaceNotifications}\n"
                    "                      element={<Notifications />}\n"
                    "                    />"
                ),
                replacement=(
                    "                    {/* ARCH-31. The policies route is declared\n"
                    "                        BEFORE the :caseId route: react-router would\n"
                    "                        otherwise match \"policies\" as a case id and\n"
                    "                        render the comparison grid against a case\n"
                    "                        that does not exist. */}\n"
                    "                    <Route\n"
                    "                      path={ROUTE_PATTERNS.workspaceProcurement}\n"
                    "                      element={<ProcurementCaseQueue />}\n"
                    "                    />\n"
                    "                    <Route\n"
                    "                      path={ROUTE_PATTERNS.workspaceProcurementPolicies}\n"
                    "                      element={<TolerancePolicyEditor />}\n"
                    "                    />\n"
                    "                    <Route\n"
                    "                      path={ROUTE_PATTERNS.workspaceProcurementCase}\n"
                    "                      element={<ThreeWayComparison />}\n"
                    "                    />\n"
                    "                    <Route\n"
                    "                      path={ROUTE_PATTERNS.workspaceNotifications}\n"
                    "                      element={<Notifications />}\n"
                    "                    />"
                ),
                description="mount the three routes inside the workspace shell",
            ),
        ],
    ),
]


def _merge_by_path(patches: list[FilePatch]) -> dict[str, FilePatch]:
    """Collapse patches that touch the same file into one write.

    Two FilePatch entries for one path would each read the file, apply their
    own edits and write — and the second read would see the first's result
    only by luck of ordering. One entry per path, asserted here rather than
    assumed.
    """
    merged: dict[str, FilePatch] = {}
    for patch in patches:
        existing = merged.get(patch.relpath)
        if existing is None:
            merged[patch.relpath] = patch
            continue
        if existing.sentinel != patch.sentinel:
            raise SystemExit(
                f"two patches for {patch.relpath} declare different "
                f"sentinels; idempotency would be undecidable"
            )
        existing.edits.extend(patch.edits)
    return merged


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = (FRONTEND if patch.root == "frontend" else BACKEND) / patch.relpath
    if not path.exists():
        raise SystemExit(f"FATAL: {patch.relpath} does not exist")

    text, newline, had_bom = _read(path)

    if patch.sentinel in text:
        return "already applied"

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"skipped: {reason}"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise SystemExit(
                f"FATAL: {patch.relpath}: anchor for {edit.description!r} "
                f"occurs {found} time(s), expected {edit.occurrences}.\n"
                f"  The file is not in the state this patch was written "
                f"against. Applying a subset would leave the tree in a state "
                f"no gate describes.\n"
                f"  anchor: {edit.anchor[:90]!r}..."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if updated == text:
        return "no change"

    if check_only:
        return f"would apply {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"applied {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="report what would change, write nothing"
    )
    args = parser.parse_args()

    print("ARCH-31 Steps 1-2 — patching modified files")
    print(f"  backend:  {BACKEND}")
    print(f"  frontend: {FRONTEND}")
    print(f"  mode:    {'CHECK (no writes)' if args.check else 'APPLY'}\n")

    merged = _merge_by_path(PATCHES)
    for relpath, patch in merged.items():
        status = apply_patch(patch, check_only=args.check)
        print(f"  [{status:>22}] {relpath}")

    print(
        "\nRe-running this script is safe: each file carries a sentinel and "
        "is skipped once present."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())