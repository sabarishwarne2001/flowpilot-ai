#!/usr/bin/env python3
"""ARCH-33 Step 1 — anchored, idempotent patches for MODIFIED files.

    python apply_arch33.py --check     # report, change nothing
    python apply_arch33.py             # apply
    python apply_arch33.py             # again: reports "already applied", no-op

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
describes. An anchor that matches more than its declared count is equally
fatal: the patch author believed the text was unique and it is not, so which
occurrence gets edited is luck.

EVERY SENTINEL IS A SUBSTRING OF THE TEXT ITS OWN PATCH WRITES
==============================================================

ARCH-31 established this and ARCH-32 kept it. A sentinel that is not present
in the replacement text makes the idempotence check a lie: the second run does
not find it, re-applies the edit, and the anchor is gone — so the run fails
with "anchor not found" on a tree that is actually correct.
`verify_arch33.py` asserts the property mechanically for every patch in this
file rather than trusting the author to have held it in their head.

BOM AND LINE ENDINGS
====================

`app/services/automation/executor.py` begins with a UTF-8 BOM, and
`git config core.autocrlf` on Windows leaves CRLF in the working tree. Reading
with the BOM stripped and writing back without restoring it would produce a
whole-file diff on a one-line change. Every write here restores exactly what
it found.

WHY `automation_graph.py` KEEPS ITS LITERAL SETS
================================================

The obvious edit is to make `NODE_TYPES` import
`app.services.assertions.vocabulary`. `app/models/redaction.py` already
imports a service vocabulary that way, so the pattern exists.

It is not used here. `automation_graph.py` is imported by `graph_service`,
which is imported by `executor`, which is the hot path of the automation
engine; putting a `app.services.*` import into it means every automation
import now runs `app/services/__init__.py`, which eagerly imports the LLM
service, the retriever and the embedding service. The sets are therefore
extended literally, and `verify_arch33.py` asserts they equal the vocabulary's
tuples — which prevents the drift the import was meant to prevent, at no
import-graph cost.

WHAT THIS STEP DOES NOT TOUCH
=============================

`app/workers/handlers/__init__.py`, `app/workers/profiles.py`,
`app/api/v1/router.py` and `app/services/automation/executor.py` are Tranche
2's business, because the node executor, the router module and the handler
they would register do not exist yet. Registering a handler in one of the
three places and not the others is the exact defect ARCH-16 shipped:
`assert_imports_match_profile()` raises `ProfileError` at EVERY worker's
startup on a handler no profile claims, so a half-registration here would stop
the fleet booting rather than degrade.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

BACKEND = Path(__file__).resolve().parent

SENTINEL_MODELS_IMPORT = "# ARCH-33 — semantic assertion automation."
SENTINEL_ENTITLEMENTS = "ARCH33-S1:capability-assertions-key"
SENTINEL_USAGE_EVENT = "ARCH-33 §4.7 — one assertion EVALUATION."
SENTINEL_DISPLAY_NAME = "ARCH33-S1:capability-assertions-display"
SENTINEL_NODE_VOCABULARY = "ARCH33-S1:assertion-node-type"
SENTINEL_GRAPH_VALIDATION = "ARCH33-S1:assertion-edge-validation"


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
    sentinel: str
    edits: list[Edit] = field(default_factory=list)
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
# 1. app/models/__init__.py — map the three new tables
# ---------------------------------------------------------------------------

MODELS_IMPORT_ANCHOR = """from app.models.redaction import (  # noqa: F401
    RedactionJob,
    RedactionRegion,
)"""

MODELS_IMPORT_REPLACEMENT = """from app.models.redaction import (  # noqa: F401
    RedactionJob,
    RedactionRegion,
)
# ARCH-33 — semantic assertion automation.
#
# Imported here for the same reason every other mapped class is: the
# declarative registry has to know about a table before anything can query it.
# AssertionEvaluation in particular holds relationships to
# DocumentVerification and AutomationNodeRun, and a model that is only
# imported by the module that uses it produces a `NoSuchTableError` at the
# first cross-module relationship rather than at import.
from app.models.assertion import (  # noqa: F401
    AssertionDefinition,
    AssertionEvaluation,
    AssertionRetrievalPhrase,
)"""

MODELS_ALL_ANCHOR = """    # ARCH-32 — zero-leakage geometric PII redaction.
    "RedactionJob",
    "RedactionRegion",
"""

MODELS_ALL_REPLACEMENT = """    # ARCH-32 — zero-leakage geometric PII redaction.
    "RedactionJob",
    "RedactionRegion",
    # ARCH-33 — semantic assertion automation with confidence triage.
    "AssertionDefinition",
    "AssertionEvaluation",
    "AssertionRetrievalPhrase",
"""

# ---------------------------------------------------------------------------
# 2. app/core/entitlements.py — register capability.semantic_assertions
# ---------------------------------------------------------------------------

ENTITLEMENTS_EXPORT_ANCHOR = """    # ARCH32-S1:capability-redaction-key
    "REDACTION_CAPABILITY","""

ENTITLEMENTS_EXPORT_REPLACEMENT = """    # ARCH32-S1:capability-redaction-key
    "REDACTION_CAPABILITY",
    # ARCH33-S1:capability-assertions-key
    "SEMANTIC_ASSERTIONS_CAPABILITY","""

ENTITLEMENTS_KEY_ANCHOR = '''#: Every capability key. Disjoint from ADDON_KEYS by construction;
#: verify_arch31_step0 asserts the two sets never intersect.
CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
)'''

ENTITLEMENTS_KEY_REPLACEMENT = '''#: ARCH33-S1:capability-assertions-key. Semantic assertion automation
#: with confidence triage. A CAPABILITY, not an ADDON, and the
#: distinction is the same one ARCH-31 and ARCH-32 recorded above:
#: add-ons are separately purchasable line items with a price, a halt
#: effect and a grace ladder, and `ADDON_KEYS` is asserted equal to
#: `entitlement_service`'s catalog at import. Putting this in
#: ADDON_KEYS would fail that catalog assertion at boot.
#:
#: §4.7 is also specific that the LLM family runs "through the tenant's
#: existing model route under existing metering and spend limits", so
#: there is no second key here for the AI half. One capability gates
#: the feature; the existing `llm.platform_key` entitlement and the
#: existing token meters gate the inference.
SEMANTIC_ASSERTIONS_CAPABILITY: str = "capability.semantic_assertions"

#: Every capability key. Disjoint from ADDON_KEYS by construction;
#: verify_arch31_step0 asserts the two sets never intersect.
CAPABILITY_KEYS: tuple[str, ...] = (
    RECONCILIATION_CAPABILITY,
    REDACTION_CAPABILITY,
    SEMANTIC_ASSERTIONS_CAPABILITY,
)'''

ENTITLEMENTS_ENTRY_ANCHOR = '''    # ARCH32-S1:capability-redaction-key
    Entitlement(
        name=REDACTION_CAPABILITY,
        description=(
            "Zero-leakage redaction: rebuild a document from redacted "
            "pixels so removed content does not exist in the output, with "
            "a leak-checked sanitized PDF and a manifest that carries no "
            "redacted text. Bundled into a tier."
        ),
    ),
)'''

ENTITLEMENTS_ENTRY_REPLACEMENT = '''    # ARCH32-S1:capability-redaction-key
    Entitlement(
        name=REDACTION_CAPABILITY,
        description=(
            "Zero-leakage redaction: rebuild a document from redacted "
            "pixels so removed content does not exist in the output, with "
            "a leak-checked sanitized PDF and a manifest that carries no "
            "redacted text. Bundled into a tier."
        ),
    ),
    # ARCH33-S1:capability-assertions-key
    Entitlement(
        name=SEMANTIC_ASSERTIONS_CAPABILITY,
        description=(
            "Semantic assertions: write a clause requirement as a workflow "
            "step, have it compiled to a typed check, and route anything "
            "uncertain or failing to review with the paragraph attached. "
            "Bundled into a tier, not purchasable on its own."
        ),
    ),
)'''

# ---------------------------------------------------------------------------
# 3. app/core/usage_events.py — register assertion.evaluation
# ---------------------------------------------------------------------------

USAGE_EVENT_ANCHOR = '''    UsageEventType(
        name="redaction.page",
        unit=UsageUnit.PAGE,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One page of sanitized PDF produced by the redaction engine.",
    ),
)'''

USAGE_EVENT_REPLACEMENT = '''    UsageEventType(
        name="redaction.page",
        unit=UsageUnit.PAGE,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One page of sanitized PDF produced by the redaction engine.",
    ),
    # ARCH-33 §4.7 — one assertion EVALUATION.
    #
    # REQUEST, like procurement.case and unlike redaction.page, because the
    # cost here is the assignment rather than the paper: a two-page order
    # form and a sixty-page master agreement are one retrieval, one parse
    # and one routing decision each, and the engine's work between them
    # differs by milliseconds.
    #
    # Metered on the EVALUATION, not on the definition and not on the
    # execution. A rule that runs against a thousand documents is a
    # thousand evaluations; a workflow with three assertion nodes is three
    # evaluations per document. Both are the thing the customer is
    # consuming, and neither is visible if the meter counts rules.
    #
    # LLM-mode evaluations emit this too. The evaluation happened, and it
    # cost an allowance unit, whether a parser or a model answered it. The
    # model's tokens are metered SEPARATELY through the existing
    # llm.input_token / llm.output_token events on the tenant's own model
    # route, which is how §4.7 adds no new cost category.
    UsageEventType(
        name="assertion.evaluation",
        unit=UsageUnit.REQUEST,
        emission=EmissionKind.OCCURRENCE,
        billable=True,
        default_provider="internal",
        description="One assertion evaluated against one work item.",
    ),
)'''

# ---------------------------------------------------------------------------
# 4. app/api/capability_gate.py — a readable name in the 402 body
# ---------------------------------------------------------------------------

DISPLAY_NAME_ANCHOR = """    entitlements.REDACTION_CAPABILITY: "Document redaction",
}"""

DISPLAY_NAME_REPLACEMENT = """    entitlements.REDACTION_CAPABILITY: "Document redaction",
    # ARCH33-S1:capability-assertions-display. Without an entry here the 402
    # body reads "capability.semantic_assertions is included on higher
    # plans", which is a key name in front of a customer.
    entitlements.SEMANTIC_ASSERTIONS_CAPABILITY: "Clause assertions",
}"""

# ---------------------------------------------------------------------------
# 5. app/models/automation_graph.py — widen the node and branch vocabularies
# ---------------------------------------------------------------------------

NODE_VOCABULARY_ANCHOR = '''NODE_TYPES: frozenset[str] = frozenset(
    {"trigger", "condition", "action", "branch", "join"}
)

BRANCH_LABELS: frozenset[str] = frozenset({"default", "true", "false"})'''

NODE_VOCABULARY_REPLACEMENT = '''# ARCH33-S1:assertion-node-type. §4.3 adds ONE node type, and it has two
# outgoing edges — `pass` and `triage` — rather than a condition's true and
# false.
#
# The difference is not cosmetic. A condition's `false` edge means "the
# answer was no". An assertion's `triage` edge means "the answer is not
# trustworthy enough to act on", which is a different statement and needs a
# different word in front of the person wiring the graph.
#
# These sets are declared literally rather than imported from
# `app/services/assertions/vocabulary.py`, even though
# `app/models/redaction.py` imports a service vocabulary that way. This
# module sits under `graph_service` and `executor`, the hot path of the
# automation engine, and an `app.services.*` import here would make every
# automation import execute `app/services/__init__.py` — which eagerly
# imports the LLM service, the retriever and the embedding service.
# `verify_arch33.py` asserts these two sets equal the vocabulary's tuples,
# so the drift the import would have prevented is prevented anyway.
NODE_TYPES: frozenset[str] = frozenset(
    {"trigger", "condition", "action", "branch", "join", "assertion"}
)

BRANCH_LABELS: frozenset[str] = frozenset(
    {"default", "true", "false", "pass", "triage"}
)

#: The two an assertion node must have, and the only two it may have.
ASSERTION_BRANCH_LABELS: frozenset[str] = frozenset({"pass", "triage"})'''

NODE_EXPORT_ANCHOR = '''__all__ = [
    "BRANCH_LABELS",
    "GRAPH_VERSION_DAG",
    "GRAPH_VERSION_FLAT",
    "NODE_TYPES",'''

NODE_EXPORT_REPLACEMENT = '''__all__ = [
    # ARCH33-S1:assertion-node-type
    "ASSERTION_BRANCH_LABELS",
    "BRANCH_LABELS",
    "GRAPH_VERSION_DAG",
    "GRAPH_VERSION_FLAT",
    "NODE_TYPES",'''

NODE_CHECK_ANCHOR = '''        CheckConstraint(
            "node_type IN ('trigger', 'condition', 'action', 'branch', 'join')",
            name="ck_automation_nodes_type_known",
        ),'''

NODE_CHECK_REPLACEMENT = '''        CheckConstraint(
            "node_type IN ('trigger', 'condition', 'action', 'branch', "
            "'join', 'assertion')",
            name="ck_automation_nodes_type_known",
        ),'''

EDGE_CHECK_ANCHOR = '''        CheckConstraint(
            "branch IN ('default', 'true', 'false')",
            name="ck_automation_edges_branch_known",
        ),'''

EDGE_CHECK_REPLACEMENT = '''        CheckConstraint(
            "branch IN ('default', 'true', 'false', 'pass', 'triage')",
            name="ck_automation_edges_branch_known",
        ),'''

# ---------------------------------------------------------------------------
# 6. app/services/automation/graph_service.py — structural rules for the
#    assertion node
# ---------------------------------------------------------------------------
#
# Save-time validation, not run-time. An assertion node with only a `pass`
# edge is a workflow that silently ends whenever a document is uncertain,
# which is the outcome §4.1 sells against, and the administrator who wired it
# believes review is happening.

GRAPH_BRANCH_ANCHOR = '''    for edge in edges:
        source = by_key[edge.from_node_key]
        if edge.branch in ("true", "false") and source.node_type != "branch":
            raise GraphValidationError(
                f"Edge from '{edge.from_node_key}' (a {source.node_type} node) "
                f"is labelled '{edge.branch}', but only a branch node has "
                "true/false outcomes.",
                nodes=[edge.from_node_key],
            )
        if edge.branch == "default" and source.node_type == "branch":
            raise GraphValidationError(
                f"Branch node '{edge.from_node_key}' has a 'default' edge. "
                "A branch's out-edges must be labelled true or false.",
                nodes=[edge.from_node_key],
            )'''

GRAPH_BRANCH_REPLACEMENT = '''    # ARCH33-S1:assertion-edge-validation. An assertion node needs BOTH
    # outcomes wired, for the same reason a branch node does and with a
    # sharper consequence: an unwired `triage` edge means a document the
    # engine was not confident about stops dead, no review is created, and
    # the workflow reports success.
    for node in nodes:
        if node.node_type != "assertion":
            continue
        labels = {e.branch for e in edges if e.from_node_key == node.node_key}
        missing = ASSERTION_BRANCH_LABELS - labels
        if missing:
            raise GraphValidationError(
                f"Assertion node '{node.node_key}' has no "
                f"{'/'.join(sorted(missing))} edge. Both outcomes must be "
                "wired: documents that pass continue, and documents that are "
                "uncertain or fail go to review. An unwired outcome ends the "
                "execution silently.",
                nodes=[node.node_key],
            )

    for edge in edges:
        source = by_key[edge.from_node_key]
        if edge.branch in ("true", "false") and source.node_type != "branch":
            raise GraphValidationError(
                f"Edge from '{edge.from_node_key}' (a {source.node_type} node) "
                f"is labelled '{edge.branch}', but only a branch node has "
                "true/false outcomes.",
                nodes=[edge.from_node_key],
            )
        if edge.branch in ASSERTION_BRANCH_LABELS and source.node_type != "assertion":
            raise GraphValidationError(
                f"Edge from '{edge.from_node_key}' (a {source.node_type} node) "
                f"is labelled '{edge.branch}', but only an assertion node has "
                "pass/triage outcomes.",
                nodes=[edge.from_node_key],
            )
        if edge.branch == "default" and source.node_type == "branch":
            raise GraphValidationError(
                f"Branch node '{edge.from_node_key}' has a 'default' edge. "
                "A branch's out-edges must be labelled true or false.",
                nodes=[edge.from_node_key],
            )
        if edge.branch == "default" and source.node_type == "assertion":
            raise GraphValidationError(
                f"Assertion node '{edge.from_node_key}' has a 'default' edge. "
                "An assertion's out-edges must be labelled pass or triage.",
                nodes=[edge.from_node_key],
            )'''

GRAPH_IMPORT_ANCHOR = '''from app.models.automation_graph import (
    BRANCH_LABELS,
    GRAPH_VERSION_DAG,
    GRAPH_VERSION_FLAT,
    NODE_TYPES,
    AutomationEdge,
    AutomationNode,
)'''

GRAPH_IMPORT_REPLACEMENT = '''from app.models.automation_graph import (
    ASSERTION_BRANCH_LABELS,
    BRANCH_LABELS,
    GRAPH_VERSION_DAG,
    GRAPH_VERSION_FLAT,
    NODE_TYPES,
    AutomationEdge,
    AutomationNode,
)'''


PATCHES: list[FilePatch] = [
    FilePatch(
        relpath="app/models/__init__.py",
        sentinel=SENTINEL_MODELS_IMPORT,
        edits=[
            Edit(
                anchor=MODELS_IMPORT_ANCHOR,
                replacement=MODELS_IMPORT_REPLACEMENT,
                description="import the three assertion models",
            ),
            Edit(
                anchor=MODELS_ALL_ANCHOR,
                replacement=MODELS_ALL_REPLACEMENT,
                description="export the three assertion models",
            ),
        ],
    ),
    FilePatch(
        relpath="app/core/entitlements.py",
        sentinel=SENTINEL_ENTITLEMENTS,
        edits=[
            Edit(
                anchor=ENTITLEMENTS_EXPORT_ANCHOR,
                replacement=ENTITLEMENTS_EXPORT_REPLACEMENT,
                description="export SEMANTIC_ASSERTIONS_CAPABILITY",
            ),
            Edit(
                anchor=ENTITLEMENTS_KEY_ANCHOR,
                replacement=ENTITLEMENTS_KEY_REPLACEMENT,
                description="declare the key and add it to CAPABILITY_KEYS",
            ),
            Edit(
                anchor=ENTITLEMENTS_ENTRY_ANCHOR,
                replacement=ENTITLEMENTS_ENTRY_REPLACEMENT,
                description="register the Entitlement row",
            ),
        ],
    ),
    FilePatch(
        relpath="app/core/usage_events.py",
        sentinel=SENTINEL_USAGE_EVENT,
        edits=[
            Edit(
                anchor=USAGE_EVENT_ANCHOR,
                replacement=USAGE_EVENT_REPLACEMENT,
                description="register the assertion.evaluation meter",
            ),
        ],
    ),
    FilePatch(
        relpath="app/api/capability_gate.py",
        sentinel=SENTINEL_DISPLAY_NAME,
        edits=[
            Edit(
                anchor=DISPLAY_NAME_ANCHOR,
                replacement=DISPLAY_NAME_REPLACEMENT,
                description="display name for the 402 body",
            ),
        ],
    ),
    FilePatch(
        relpath="app/models/automation_graph.py",
        sentinel=SENTINEL_NODE_VOCABULARY,
        edits=[
            Edit(
                anchor=NODE_VOCABULARY_ANCHOR,
                replacement=NODE_VOCABULARY_REPLACEMENT,
                description="widen NODE_TYPES and BRANCH_LABELS",
            ),
            Edit(
                anchor=NODE_EXPORT_ANCHOR,
                replacement=NODE_EXPORT_REPLACEMENT,
                description="export ASSERTION_BRANCH_LABELS",
            ),
            Edit(
                anchor=NODE_CHECK_ANCHOR,
                replacement=NODE_CHECK_REPLACEMENT,
                description="ORM-side node type CHECK",
            ),
            Edit(
                anchor=EDGE_CHECK_ANCHOR,
                replacement=EDGE_CHECK_REPLACEMENT,
                description="ORM-side branch CHECK",
            ),
        ],
    ),
    FilePatch(
        relpath="app/services/automation/graph_service.py",
        sentinel=SENTINEL_GRAPH_VALIDATION,
        edits=[
            Edit(
                anchor=GRAPH_IMPORT_ANCHOR,
                replacement=GRAPH_IMPORT_REPLACEMENT,
                description="import ASSERTION_BRANCH_LABELS",
            ),
            Edit(
                anchor=GRAPH_BRANCH_ANCHOR,
                replacement=GRAPH_BRANCH_REPLACEMENT,
                description="assertion node must wire pass and triage",
            ),
        ],
    ),
]


class PatchError(RuntimeError):
    pass


def apply_patch(patch: FilePatch, *, check_only: bool) -> str:
    path = BACKEND / patch.relpath
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
    parser.add_argument(
        "--check", action="store_true", help="report without writing anything"
    )
    args = parser.parse_args()

    print("ARCH-33 Step 1 — modified-file patches")
    print(f"backend: {BACKEND}")
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