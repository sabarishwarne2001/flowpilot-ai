#!/usr/bin/env python3
"""ARCH-33 Tranche 2 — anchored, idempotent patches for MODIFIED files.

    python apply_arch33_final.py --check     # report, change nothing
    python apply_arch33_final.py             # apply
    python apply_arch33_final.py             # again: "already applied", no-op

Same contract as `apply_arch33.py` and `apply_arch32.py`: anchors declare how
many times they must occur, sentinels make re-runs safe, and every sentinel is
a substring of the text its own patch writes — asserted mechanically by
`verify_arch33.py` rather than trusted to the author.

WHAT THIS STEP TOUCHES THAT TRANCHE 1 DID NOT
=============================================

Tranche 1 deliberately left four files alone because the modules they would
have referenced did not exist. They exist now:

    app/services/automation/executor.py      dispatch the assertion node
    app/api/v1/router.py                     mount the assertions router
    app/services/document_verification_service.py
                                             let ARCH-13's resolver ignore
                                             assertion fields
    frontend/src/services/api/queryKeys.ts   cache keys
    frontend/src/services/api/endpoints.ts   endpoint paths

WHY NO WORKER REGISTRATION
==========================

ARCH-33 introduces NO new job type, and that is worth saying out loud because
the three-place rule would otherwise be the first thing a reader looks for.

An assertion runs inside `automation.execute`, which is already in the handler
map, already on a profile, and already scheduled where it needs to be. A
reviewer's resolution re-enqueues the same job type. Adding
`assertion.evaluate` would have created a second path into the same executor
and a second place for the graph walk to diverge —
`assert_imports_match_profile()` would have been satisfied and the product
would have had two answers to "what ran this node".

`verify_arch33.py` asserts the absence: no `assertion.*` key in `_HANDLERS`,
and none in `ALL_PHASE_JOB_TYPES`.

WHY ARCH-13'S RESOLVER HAS TO CHANGE
====================================

`document_verification_service.resolve` requires a chosen VALUE for every
field in disagreement and then releases the automation. That is right for an
extracted field and wrong for an assertion: resolving an assertion is choosing
which EDGE a paused execution takes, not filling in a number.

Left alone, an assertion field on a verification would make ARCH-13's resolver
demand a value the reviewer was never asked for, and the extraction review for
that document could not be completed at all. The patch below scopes ARCH-13's
resolver to the fields it owns; `triage.resolve_assertion` owns the rest, and
both close the verification only when nothing is outstanding.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent
FRONTEND = BACKEND.parent / "frontend"

SENTINEL_EXECUTOR = "ARCH33-S2:assertion-node-dispatch"
SENTINEL_ROUTER = "ARCH33-S2:assertions-router"
SENTINEL_VERIFICATION = "ARCH33-S2:assertion-fields-are-not-ours"
SENTINEL_QUERY_KEYS = "ARCH33-S2:assertion-query-keys"
SENTINEL_ENDPOINTS = "ARCH33-S2:assertion-endpoints"


@dataclass
class Edit:
    anchor: str
    replacement: str
    occurrences: int = 1
    description: str = ""


@dataclass
class FilePatch:
    relpath: str
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
    #: Backend by default; frontend files set this.
    root: str = "backend"
    precondition: Optional[Callable[[str], Optional[str]]] = None


def _root_for(patch: FilePatch) -> Path:
    return BACKEND if patch.root == "backend" else FRONTEND


def _read(path: Path) -> tuple[str, str, bool]:
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
# 1. app/services/automation/executor.py — dispatch the assertion node
# ---------------------------------------------------------------------------

EXECUTOR_ANCHOR = '''    if node.node_type == "join":
        _record_node(state, node_key=node.node_key, node_type=node.node_type, status=AutomationNodeRunStatus.COMPLETED)
        return True'''

EXECUTOR_REPLACEMENT = '''    if node.node_type == "join":
        _record_node(state, node_key=node.node_key, node_type=node.node_type, status=AutomationNodeRunStatus.COMPLETED)
        return True

    # ARCH33-S2:assertion-node-dispatch. Four lines here, and everything
    # specific to an assertion in app/services/assertions/node_executor.py.
    #
    # This module is the hot path of the automation engine and is gated by
    # ARCH-13's own suite; putting retrieval, evaluation and triage inside it
    # would make every future change to a clause check a change to the module
    # that runs every workflow in the product.
    #
    # The import is local on purpose: node_executor imports _record_node and
    # _propagate_skip back out of this module, and a module-scope import here
    # would close the cycle at startup.
    if node.node_type == "assertion":
        from app.services.assertions import node_executor

        return node_executor.execute(state, node=node)'''

# ---------------------------------------------------------------------------
# 2. app/api/v1/router.py — mount the assertions router
# ---------------------------------------------------------------------------

ROUTER_IMPORT_ANCHOR = """from app.api.v1 import (
    ai_settings,
    api_keys,
    assistant,"""

ROUTER_IMPORT_REPLACEMENT = """from app.api.v1 import (
    ai_settings,
    api_keys,
    assertions,
    assistant,"""

ROUTER_MOUNT_ANCHOR = """api_router.include_router(redactions.router)"""

ROUTER_MOUNT_REPLACEMENT = """api_router.include_router(redactions.router)
# ARCH33-S2:assertions-router. ARCH-33 clause assertions. Every route in it is
# capability-gated, including the reads: the review queue is a list of every
# clause finding the engine produced on a tenant's contracts, which IS the
# product. Gating only the writes would let a tenant read all of it and simply
# not click resolve.
api_router.include_router(assertions.router)"""

# ---------------------------------------------------------------------------
# 3. app/services/document_verification_service.py — assertion fields are not
#    ARCH-13's to resolve
# ---------------------------------------------------------------------------

VERIFICATION_ANCHOR = """    disagreed = {f.field_path: f for f in verification.fields if not f.agreed}"""

VERIFICATION_REPLACEMENT = '''    # ARCH33-S2:assertion-fields-are-not-ours. A verification can now carry
    # `assertion:{definition_id}` fields written by ARCH-33's triage, and this
    # resolver must not demand values for them.
    #
    # Resolving an extracted field means choosing its VALUE. Resolving an
    # assertion means choosing which EDGE a paused execution takes, and the
    # reviewer is asked a different question ("It passes" / "It fails") on a
    # different screen. Left in, every assertion on a document would make this
    # function refuse with "fields are still unresolved" for a field nobody
    # was ever shown here — so the extraction review could not be completed at
    # all.
    #
    # `app/services/assertions/triage.py` owns those rows, and both resolvers
    # close the verification only once NOTHING on it is outstanding, so
    # neither can release an execution the other was still waiting on.
    from app.services.assertions.vocabulary import FIELD_PATH_PREFIX

    disagreed = {
        f.field_path: f
        for f in verification.fields
        if not f.agreed and not f.field_path.startswith(FIELD_PATH_PREFIX)
    }'''

# ---------------------------------------------------------------------------
# 4. frontend/src/services/api/queryKeys.ts
# ---------------------------------------------------------------------------

QUERY_KEYS_ANCHOR = """/** ARCH-31 — procurement matching. Workspace-scoped, like work items. */
export const procurementKeys = {"""

QUERY_KEYS_REPLACEMENT = """/**
 * ARCH33-S2:assertion-query-keys. Clause assertions.
 *
 * `preview` carries the sentence AND the threshold, because the consequence
 * line under the slider changes with both: the same sentence at 90% and at
 * 99% predicts a different share of documents going to review, and a key that
 * ignored the threshold would serve the 90% sentence under a 99% slider.
 */
export const assertionKeys = {
  all: (workspaceId: string) =>
    [...workspaceScope(workspaceId), "assertions"] as const,
  preview: (workspaceId: string, sentence: string, thresholdPercent: number) =>
    [...assertionKeys.all(workspaceId), "preview", sentence, thresholdPercent] as const,
  rule: (workspaceId: string, ruleId: string) =>
    [...assertionKeys.all(workspaceId), "rule", ruleId] as const,
  reviews: (workspaceId: string, workItemId?: string) =>
    [...assertionKeys.all(workspaceId), "reviews", workItemId ?? "all"] as const,
  phrases: (workspaceId: string, family?: string) =>
    [...assertionKeys.all(workspaceId), "phrases", family ?? "all"] as const,
};

/** ARCH-31 — procurement matching. Workspace-scoped, like work items. */
export const procurementKeys = {"""

# ---------------------------------------------------------------------------
# 5. frontend/src/services/api/endpoints.ts
# ---------------------------------------------------------------------------

ENDPOINTS_ANCHOR = """/** ARCH-30 Tranche 2 (D-8) — add-on entitlements and add-on checkout. */
export const ENTITLEMENT_ENDPOINTS = {"""

ENDPOINTS_REPLACEMENT = """/**
 * ARCH33-S2:assertion-endpoints. Clause assertions, workspace-scoped.
 *
 * `phrases` is workspace-scoped on the wire and organization-scoped in the
 * database, and that is deliberate rather than sloppy: §4.3 scopes the
 * learned synonym table to the TENANT, and the route carries a workspace only
 * so the same `RequireWorkspaceRole` dependency and the same capability gate
 * apply to it as to everything else here.
 */
export const ASSERTION_ENDPOINTS = {
  preview: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/preview`,
  rule: (workspaceId: string, ruleId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/rules/${seg(ruleId)}`,
  node: (workspaceId: string, ruleId: string, nodeKey: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/rules/${seg(ruleId)}/nodes/${seg(nodeKey)}`,
  simulate: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/simulate`,
  reviews: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/reviews`,
  resolve: (workspaceId: string, evaluationId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/reviews/${seg(evaluationId)}/resolve`,
  phrases: (workspaceId: string): string =>
    `/workspaces/${seg(workspaceId)}/assertions/phrases`,
} as const;

/** ARCH-30 Tranche 2 (D-8) — add-on entitlements and add-on checkout. */
export const ENTITLEMENT_ENDPOINTS = {"""


PATCHES: list[FilePatch] = [
    FilePatch(
        relpath="app/services/automation/executor.py",
        sentinel=SENTINEL_EXECUTOR,
        edits=[
            Edit(
                anchor=EXECUTOR_ANCHOR,
                replacement=EXECUTOR_REPLACEMENT,
                description="dispatch the assertion node type",
            ),
        ],
    ),
    FilePatch(
        relpath="app/api/v1/router.py",
        sentinel=SENTINEL_ROUTER,
        edits=[
            Edit(
                anchor=ROUTER_IMPORT_ANCHOR,
                replacement=ROUTER_IMPORT_REPLACEMENT,
                description="import the assertions module",
            ),
            Edit(
                anchor=ROUTER_MOUNT_ANCHOR,
                replacement=ROUTER_MOUNT_REPLACEMENT,
                description="mount the assertions router",
            ),
        ],
    ),
    FilePatch(
        relpath="app/services/document_verification_service.py",
        sentinel=SENTINEL_VERIFICATION,
        edits=[
            Edit(
                anchor=VERIFICATION_ANCHOR,
                replacement=VERIFICATION_REPLACEMENT,
                description="exclude assertion fields from ARCH-13's resolver",
            ),
        ],
    ),
    FilePatch(
        relpath="src/services/api/queryKeys.ts",
        sentinel=SENTINEL_QUERY_KEYS,
        root="frontend",
        edits=[
            Edit(
                anchor=QUERY_KEYS_ANCHOR,
                replacement=QUERY_KEYS_REPLACEMENT,
                description="assertionKeys",
            ),
        ],
    ),
    FilePatch(
        relpath="src/services/api/endpoints.ts",
        sentinel=SENTINEL_ENDPOINTS,
        root="frontend",
        edits=[
            Edit(
                anchor=ENDPOINTS_ANCHOR,
                replacement=ENDPOINTS_REPLACEMENT,
                description="ASSERTION_ENDPOINTS",
            ),
        ],
    ),
]


class PatchError(RuntimeError):
    pass


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = _root_for(patch) / patch.relpath
    if not path.exists():
        raise PatchError(f"{patch.relpath}: file does not exist")

    text, newline, had_bom = _read(path)

    if patch.precondition is not None:
        reason = patch.precondition(text)
        if reason:
            return f"SKIP  {patch.relpath}: {reason}"

    if patch.sentinel in text:
        return f"OK    {patch.relpath}: already applied"

    updated = text
    for edit in patch.edits:
        found = updated.count(edit.anchor)
        if found != edit.occurrences:
            raise PatchError(
                f"{patch.relpath}: anchor for {edit.description!r} occurs "
                f"{found} time(s), expected {edit.occurrences}. The file is "
                "not in the state this patch was written against; nothing "
                "has been written."
            )
        updated = updated.replace(edit.anchor, edit.replacement, edit.occurrences)

    if patch.sentinel not in updated:
        raise PatchError(
            f"{patch.relpath}: sentinel {patch.sentinel!r} is absent from the "
            "patched text. A sentinel must be a substring of what its own "
            "patch writes, or the next run re-applies the edit."
        )

    if check_only:
        return f"WOULD {patch.relpath}: {len(patch.edits)} edit(s)"

    _write(path, updated, newline, had_bom)
    return f"WROTE {patch.relpath}: {len(patch.edits)} edit(s)"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    print("ARCH-33 Tranche 2 — modified-file patches")
    print(f"backend:  {BACKEND}")
    print(f"frontend: {FRONTEND}")
    print()

    results: list[str] = []
    try:
        for patch in PATCHES:
            results.append(apply_patch(patch, check_only=args.check))
    except PatchError as exc:
        for line in results:
            print(f"  {line}")
        print(f"\n  FAIL  {exc}")
        return 1

    for line in results:
        print(f"  {line}")

    pending = sum(1 for line in results if line.startswith(("WOULD", "WROTE")))
    print()
    if args.check:
        print(f"{pending} file(s) would change. Nothing was written.")
    else:
        print(f"{pending} file(s) changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())