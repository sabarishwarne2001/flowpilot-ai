"""ARCH-29 Slice 2 verification gate — COGS ingest, warehouse editor, workspace
grants, password change.

    python scripts/verify_arch29_slice2.py
    python scripts/verify_arch29_slice2.py --mutate    # self-test the gate

Fourteen checks. The ones that carry weight:

G1  — `setToken` in the password-change success path, scoped to the mutation
      body rather than the file. POST /auth/change-password revokes every
      session including the caller's and returns a replacement token. Without
      the re-seed, a user who secures their account is signed out of it by
      that act, and the failure looks like a session bug rather than a missing
      line. This is the single most user-visible invariant in the slice.

G3  — no `?? 0` or `|| 0` on a variance, ratio or modelled total in the
      reconciliation history. This is ARCH-18 G2 in a new surface: a null
      ratio rendered as 0.0% says the model matched the invoice exactly, at
      precisely the moment nothing was modelled at all. The history table must
      inherit the display rules InvoiceRow already enforces, or the same
      figures carry two meanings on one page.

G5  — `is_authoritative_cost` is consulted in the history. An ARCH14_SELL_SIDE
      denominator is customer-price denominated and inflated by gross margin.
      Rendering that variance unlabelled invites someone to read it as COGS.

G7  — `credentialIsComplete` gates the rotation submit. The credential is
      all-or-nothing on the wire; a half-filled rotation is accepted by the
      form, written by the backend, and fails hours later in a worker log.

G9  — exactly ONE definition of the per-kind credential fields. The editor and
      the create form both render from CREDENTIAL_FIELDS; a second literal
      field list anywhere is the drift this module was created to prevent.

G11 — the grant modal excludes existing and derived members. Offering someone
      who is already reachable produces a duplicate row or a refusal, and an
      "add member" list that silently omits the org owner reads as a bug.

Static only: reads frontend sources. No database required.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "frontend" / "src"

PASSWORD_PANEL = SRC / "pages" / "Settings" / "PasswordChangePanel.tsx"
PROFILE_PAGE = SRC / "pages" / "Settings" / "ProfileSettings.tsx"
WORKSPACE_PAGE = SRC / "pages" / "Settings" / "Workspace.tsx"
GRANT_MODAL = SRC / "components" / "workspace" / "GrantWorkspaceAccessModal.tsx"
INVOICE_PANELS = SRC / "components" / "billing" / "SupplierInvoicePanels.tsx"
MARGINS_HUB = SRC / "pages" / "admin" / "AdminMarginsHub.tsx"
CREDENTIAL_MODULE = SRC / "components" / "organization" / "warehouseCredential.tsx"
DESTINATION_EDITOR = (
    SRC / "components" / "organization" / "WarehouseDestinationEditor.tsx"
)
ANALYTICS_PAGE = SRC / "pages" / "organization" / "OrganizationAnalytics.tsx"


# ---------------------------------------------------------------------------
# Source handling
# ---------------------------------------------------------------------------

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"(?<![:/])//[^\n]*")


def strip_comments(source: str) -> str:
    """Removes TS block and line comments.

    Every component in this slice documents the trap it avoids, and those
    comments name the very symbols the checks look for. A gate that greps raw
    source passes on the strength of its own documentation.
    """
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", source))


def read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(f"expected file is missing: {path}")
    return path.read_text(encoding="utf-8")


def extract_balanced(source: str, start_pattern: str) -> str:
    """Returns a brace-balanced region beginning at the first match.

    Used to scope a check to one mutation body or one function, because a
    file-wide search cannot distinguish the expression being audited from an
    unrelated one that shares a token.
    """
    match = re.search(start_pattern, source)
    if match is None:
        raise LookupError(f"region not found: {start_pattern}")
    start = match.start()
    depth = 0
    seen = False
    for index in range(start, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
            seen = True
        elif char == "}":
            depth -= 1
            if seen and depth == 0:
                return source[start : index + 1]
    return source[start:]


@dataclass
class Result:
    check: str
    passed: bool
    detail: str


def ok(name: str, condition: bool, good: str, bad: str) -> Result:
    return Result(name, condition, good if condition else bad)


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


def check_g1_token_reseed() -> Result:
    """The check this gate exists for."""
    code = strip_comments(read(PASSWORD_PANEL))
    try:
        mutation = extract_balanced(code, r"const change = useMutation\(")
    except LookupError as exc:
        return Result("G1  password change re-seeds the session token", False, str(exc))

    success = re.search(r"onSuccess:\s*\([^)]*\)\s*=>\s*\{(.*?)\n    \}", mutation, re.S)
    body = success.group(1) if success else mutation
    reseeds = re.search(r"setToken\(\s*\w+\.access_token\s*\)", body) is not None
    return ok(
        "G1  password change re-seeds the session token",
        reseeds,
        "setToken(response.access_token) present in onSuccess",
        "MISSING — the server has revoked this session; without the re-seed "
        "the user is signed out by their own password change",
    )


def check_g2_password_cleared_on_error() -> Result:
    code = strip_comments(read(PASSWORD_PANEL))
    try:
        mutation = extract_balanced(code, r"const change = useMutation\(")
    except LookupError as exc:
        return Result("G2  rejected password is cleared", False, str(exc))
    # Scoped to onError. `reset()` also appears in the onSuccess branch, so a
    # file-wide — or even mutation-wide — search passes with the failure path
    # stripped. Mutation G2 caught exactly that.
    on_error = re.search(r"onError:\s*\([^)]*\)\s*=>\s*\{(.*)", mutation, re.S)
    cleared = on_error is not None and "reset()" in on_error.group(1)
    return ok(
        "G2  rejected password is cleared",
        cleared,
        "form is reset on failure",
        "a rejected password is left sitting in the DOM",
    )


def check_g3_panel_is_mounted() -> Result:
    code = strip_comments(read(PROFILE_PAGE))
    mounted = "<PasswordChangePanel" in code
    return ok(
        "G3  password panel is mounted",
        mounted,
        "mounted in ProfileSettings",
        "component exists with no call site — the orphaned-guard anti-pattern",
    )


# ---------------------------------------------------------------------------
# COGS
# ---------------------------------------------------------------------------


def check_g4_no_zero_default_on_cost_figures() -> Result:
    """ARCH-18 G2, in a new surface."""
    code = strip_comments(read(INVOICE_PANELS))
    offenders: list[str] = []
    for field in (
        "variance_ratio",
        "variance_micros",
        "modelled_total_micros",
        "unknown_cost_event_count",
        "modelled_event_count",
    ):
        offenders += re.findall(rf"{field}\s*(?:\?\?|\|\|)\s*0", code)
    return ok(
        "G4  no zero-defaulting of cost figures",
        not offenders,
        "none found",
        f"found: {offenders}",
    )


def check_g5_null_ratio_renders_unknown() -> Result:
    code = strip_comments(read(INVOICE_PANELS))
    guarded = (
        re.search(r"variance_ratio\s*===\s*null", code) is not None
        and "UNKNOWN_LABEL" in code
    )
    return ok(
        "G5  null variance ratio renders as unknown",
        guarded,
        "null ratio branches to UNKNOWN_LABEL",
        "a null ratio would format as a number — 0.0% claims the model "
        "matched the invoice exactly",
    )


def check_g6_authoritative_cost_is_surfaced() -> Result:
    code = strip_comments(read(INVOICE_PANELS))
    return ok(
        "G6  non-authoritative cost basis is called out",
        "is_authoritative_cost" in code,
        "history distinguishes supplier cost from customer price",
        "a sell-side denominator would render as if it were COGS variance",
    )


def check_g7_unknown_cost_events_surfaced() -> Result:
    code = strip_comments(read(INVOICE_PANELS))
    # The guard, not the token. The field name still appears inside the block
    # it renders, so `"unknown_cost_event_count" in code` survives having the
    # condition replaced with `false`.
    guarded = (
        re.search(r"run\.unknown_cost_event_count\s*>\s*0", code) is not None
    )
    return ok(
        "G7  unknown-cost events are stated",
        guarded,
        "incomplete modelling is visible in the history",
        "events with no cost basis are hidden, so the variance reads as "
        "complete when it is a floor",
    )


def check_g8_invoice_modal_mounted() -> Result:
    code = strip_comments(read(MARGINS_HUB))
    both = "<SupplierInvoiceModal" in code and "<ReconciliationHistory" in code
    return ok(
        "G8  invoice modal and history are mounted",
        both,
        "both mounted in AdminMarginsHub",
        "one or both have no call site",
    )


# ---------------------------------------------------------------------------
# Warehouse destinations
# ---------------------------------------------------------------------------


def check_g9_single_credential_definition() -> Result:
    """One definition of the credential shape, or the two surfaces drift."""
    module = strip_comments(read(CREDENTIAL_MODULE))
    if "CREDENTIAL_FIELDS" not in module:
        return Result(
            "G9  one definition of credential fields",
            False,
            "CREDENTIAL_FIELDS is missing from the shared module",
        )

    # The page must render from the shared fieldset, not its own literals.
    page = strip_comments(read(ANALYTICS_PAGE))
    editor = strip_comments(read(DESTINATION_EDITOR))
    # `<CredentialFieldset` — the JSX usage. Searching for the bare identifier
    # matches the import statement, which survives the element being renamed.
    both_share = "<CredentialFieldset" in page and "<CredentialFieldset" in editor
    # Newline-agnostic: a re-introduced switch written on one line is the same
    # defect as one written across four.
    page_has_own_switch = (
        re.search(r'case\s+"SNOWFLAKE":\s*return\s*\{', page) is not None
    )
    condition = both_share and not page_has_own_switch
    return ok(
        "G9  one definition of credential fields",
        condition,
        "create form and editor both render from CREDENTIAL_FIELDS",
        "a second per-kind field list exists — add a field to one and "
        "rotation silently writes an incomplete credential",
    )


def check_g10_rotation_is_all_or_nothing() -> Result:
    code = strip_comments(read(DESTINATION_EDITOR))
    # Scoped to the assignment. `credentialIsComplete` appears in the import
    # block, so the bare token survives the guard being deleted from the
    # expression that actually gates the submit button.
    assignment = re.search(r"credentialReady\s*=([^;]*);", code)
    gated = (
        assignment is not None
        and "credentialIsComplete(" in assignment.group(1)
    )
    return ok(
        "G10 credential rotation is all-or-nothing",
        gated,
        "submit is gated on a complete credential",
        "a partly-filled rotation could be submitted and would fail at the "
        "next scheduled run, not in the form",
    )


def check_g11_update_sends_only_changed_fields() -> Result:
    code = strip_comments(read(DESTINATION_EDITOR))
    try:
        mutation = extract_balanced(code, r"const save = useMutation\(")
    except LookupError as exc:
        return Result("G11 update sends only changed fields", False, str(exc))
    conditional = (
        "labelChanged" in mutation
        and "statusChanged" in mutation
        and "credentialReady" in mutation
    )
    return ok(
        "G11 update sends only changed fields",
        conditional,
        "payload is built from what actually changed",
        "an unchanged field would be sent as an edit, putting a meaningless "
        "write in the audit trail",
    )


def check_g12_editor_mounted() -> Result:
    code = strip_comments(read(ANALYTICS_PAGE))
    return ok(
        "G12 destination editor is mounted",
        "<WarehouseDestinationEditor" in code,
        "mounted in the destination row",
        "editor exists with no call site",
    )


# ---------------------------------------------------------------------------
# Workspace grants
# ---------------------------------------------------------------------------


def check_g13_grant_excludes_ineligible() -> Result:
    code = strip_comments(read(GRANT_MODAL))
    excludes_existing = "existing.has" in code
    excludes_derived = "is_derived" in code and "derived.has" in code
    requires_active = re.search(r'status\s*===\s*"ACTIVE"', code) is not None
    condition = excludes_existing and excludes_derived and requires_active
    return ok(
        "G13 grant offers only eligible members",
        condition,
        "existing, derived and non-active members are excluded",
        "the picker offers people the backend will refuse, or duplicates an "
        "existing grant",
    )


def check_g14_grant_modal_mounted() -> Result:
    code = strip_comments(read(WORKSPACE_PAGE))
    return ok(
        "G14 grant modal is mounted",
        "<GrantWorkspaceAccessModal" in code,
        "mounted in Workspace settings",
        "modal exists with no call site",
    )


CHECKS = [
    check_g1_token_reseed,
    check_g2_password_cleared_on_error,
    check_g3_panel_is_mounted,
    check_g4_no_zero_default_on_cost_figures,
    check_g5_null_ratio_renders_unknown,
    check_g6_authoritative_cost_is_surfaced,
    check_g7_unknown_cost_events_surfaced,
    check_g8_invoice_modal_mounted,
    check_g9_single_credential_definition,
    check_g10_rotation_is_all_or_nothing,
    check_g11_update_sends_only_changed_fields,
    check_g12_editor_mounted,
    check_g13_grant_excludes_ineligible,
    check_g14_grant_modal_mounted,
]


def run() -> int:
    results = []
    for check in CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # noqa: BLE001 — a broken check is a failed check
            results.append(Result(check.__name__, False, f"check raised: {exc!r}"))

    width = max(len(r.check) for r in results)
    for r in results:
        print(f"[{'PASS' if r.passed else 'FAIL'}] {r.check.ljust(width)}  {r.detail}")

    passed = sum(1 for r in results if r.passed)
    print(f"\n{passed}/{len(results)} checks passed")
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------------------
# Mutation self-test
# ---------------------------------------------------------------------------

MUTATIONS = [
    ("G1", PASSWORD_PANEL, "      setToken(response.access_token);", ""),
    ("G2", PASSWORD_PANEL, "      reset();\n      setError(", "      setError("),
    ("G3", PROFILE_PAGE, "      <PasswordChangePanel />", ""),
    (
        "G4",
        INVOICE_PANELS,
        "{run.modelled_event_count.toLocaleString()} events",
        "{(run.modelled_event_count ?? 0).toLocaleString()} events",
    ),
    (
        "G5",
        INVOICE_PANELS,
        "{run.variance_ratio === null ? (",
        "{false ? (",
    ),
    (
        "G6",
        INVOICE_PANELS,
        "{run.is_authoritative_cost ? (",
        "{true ? (",
    ),
    (
        "G7",
        INVOICE_PANELS,
        "{run.unknown_cost_event_count > 0 ? (",
        "{false ? (",
    ),
    ("G8", MARGINS_HUB, "<ReconciliationHistory invoice={invoice} />", ""),
    (
        "G9",
        ANALYTICS_PAGE,
        "        <CredentialFieldset",
        '        {(() => { switch (kind) { case "SNOWFLAKE": return {}; } })()}\n        <Fieldset',
    ),
    (
        "G10",
        DESTINATION_EDITOR,
        "    rotating && credentialIsComplete(current.kind, value);",
        "    rotating;",
    ),
    (
        "G11",
        DESTINATION_EDITOR,
        "      if (labelChanged) {\n        payload.label = label.trim();\n      }",
        "      payload.label = label.trim();",
    ),
    ("G12", ANALYTICS_PAGE, "        <WarehouseDestinationEditor", "        <NoEditor"),
    (
        "G13",
        GRANT_MODAL,
        "        !existing.has(member.user.id) &&",
        "",
    ),
    ("G14", WORKSPACE_PAGE, "        <GrantWorkspaceAccessModal", "        <NoModal"),
]


def mutate() -> int:
    """Every check observed failing against a deliberate regression.

    A gate that passes its own mutations is not a gate. Each mutation lands on
    a scratch copy; the working tree is never touched.
    """
    failures = 0
    for label, path, old, new in MUTATIONS:
        with tempfile.TemporaryDirectory() as tmp:
            scratch = Path(tmp) / "repo"
            shutil.copytree(
                REPO_ROOT / "frontend",
                scratch / "frontend",
                ignore=shutil.ignore_patterns("node_modules", "dist"),
            )
            shutil.copytree(
                REPO_ROOT / "backend" / "scripts", scratch / "backend" / "scripts"
            )

            target = scratch / path.relative_to(REPO_ROOT)
            source = target.read_text(encoding="utf-8")
            if old not in source:
                print(f"[SKIP] {label}: mutation anchor not found in {path.name}")
                failures += 1
                continue
            target.write_text(source.replace(old, new, 1), encoding="utf-8")

            proc = subprocess.run(
                [
                    sys.executable,
                    str(scratch / "backend" / "scripts" / Path(__file__).name),
                ],
                capture_output=True,
                text=True,
            )
            if proc.returncode == 0:
                print(
                    f"[LIVE] {label}: gate PASSED against a broken tree — "
                    f"this check certifies the break"
                )
                failures += 1
            else:
                print(f"[DEAD] {label}: gate correctly failed")

    print(f"\n{len(MUTATIONS) - failures}/{len(MUTATIONS)} mutations killed")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mutate",
        action="store_true",
        help="self-test: assert each check fails against a regression",
    )
    args = parser.parse_args()
    return mutate() if args.mutate else run()


if __name__ == "__main__":
    raise SystemExit(main())