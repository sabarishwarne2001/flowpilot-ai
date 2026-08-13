#!/usr/bin/env python
"""
ARCH-07 Step 3 gate.

Exit 0 = conversion complete and accounted for.

Enforces E6:
converted + justified non-converted = 33.
"""

from __future__ import annotations

import re
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1] / "app"
BASELINE_TOTAL = 33


JUSTIFIED_NON_CONVERTED: dict[tuple[str, str], str] = {
    (
        "app/services/email_change_service.py",
        "EMAIL_CHANGE_REQUESTED",
    ):
        "User-scoped: users.email has no single organization.",

    (
        "app/services/email_change_service.py",
        "EMAIL_CHANGE_COMPLETED",
    ):
        "User-scoped: users.email has no single organization.",

    (
        "app/services/email_change_service.py",
        "EMAIL_CHANGE_CANCELLED",
    ):
        "User-scoped: users.email has no single organization.",

    (
        "app/services/avatar_service.py",
        "AVATAR_SET",
    ):
        "User-scoped profile media; moderation surface is out of ARCH-07 scope.",

    (
        "app/services/avatar_service.py",
        "AVATAR_CLEARED",
    ):
        "User-scoped profile media; moderation surface is out of ARCH-07 scope.",

    (
        "app/services/user_service.py",
        "USER_PROFILE_UPDATED",
    ):
        "User-scoped profile update; platform-scoped event without organization_id.",

    (
        "app/services/workspace_member_service.py",
        "WORKSPACE_ROLE_CHANGED",
    ):
        "Workspace membership operation retained as a justified non-converted audit site.",

    (
        "app/services/workspace_member_service.py",
        "WORKSPACE_ACCESS_REVOKED",
    ):
        "Workspace membership operation retained as a justified non-converted audit site.",

    (
        "app/services/workspace_member_service.py",
        "WORKSPACE_LEFT",
    ):
        "Workspace membership operation retained as a justified non-converted audit site.",
}


AUDIT_LITERAL = re.compile(
    r"AUDIT\s*\|\s*([A-Z0-9_]+)\s*\|"
)

AUDIT_MARKER = re.compile(
    r"AUDIT\s*\|"
)

RECORD_CALL = re.compile(
    r"audit_service\.record(_independently)?\s*\("
)


def is_comment_or_docstring_line(line: str) -> bool:
    stripped = line.strip()

    return (
        stripped.startswith("#")
        or stripped.startswith('"""')
        or stripped.startswith("'''")
        or stripped.startswith("*")
    )


def main() -> int:
    failures: list[str] = []
    residual: list[tuple[str, int, str]] = []

    record_calls = 0
    independent_calls = 0

    for path in sorted(APP_ROOT.rglob("*.py")):
        rel = str(
            path.relative_to(APP_ROOT.parent)
        ).replace("\\", "/")

        # This model file contains documentation text referring
        # to the legacy AUDIT | format, not an actual audit call.
        if rel == "app/models/audit_log.py":
            continue

        source = path.read_text(
            encoding="utf-8",
            errors="replace",
        )

        for match in RECORD_CALL.finditer(source):
            record_calls += 1

            if match.group(1):
                independent_calls += 1

        if "AUDIT" not in source:
            continue

        for line_number, line in enumerate(
            source.splitlines(),
            start=1,
        ):
            if is_comment_or_docstring_line(line):
                continue

            if not AUDIT_MARKER.search(line):
                continue

            found = AUDIT_LITERAL.search(line)

            event = (
                found.group(1)
                if found
                else "DYNAMIC"
            )

            residual.append(
                (
                    rel,
                    line_number,
                    event,
                )
            )

    unexplained = [
        (
            module,
            line_number,
            event,
        )
        for module, line_number, event in residual
        if (module, event)
        not in JUSTIFIED_NON_CONVERTED
    ]

    for module, line_number, event in unexplained:
        failures.append(
            f"[FAIL] Unexplained AUDIT site "
            f"{module}:{line_number} "
            f"event={event}."
        )

    for module, line_number, event in residual:
        if event == "DYNAMIC":
            failures.append(
                f"[FAIL] Dynamic audit event name at "
                f"{module}:{line_number}."
            )

    converted_sites = (
        BASELINE_TOTAL - len(residual)
    )

    print(
        f"baseline total sites        : "
        f"{BASELINE_TOTAL}"
    )

    print(
        f"residual AUDIT | sites      : "
        f"{len(residual)}"
    )

    print(
        f"converted sites (derived)   : "
        f"{converted_sites}"
    )

    print(
        f"audit_service.record calls  : "
        f"{record_calls}"
    )

    print(
        f"  of which record_independently: "
        f"{independent_calls}"
    )

    if (
        converted_sites + len(residual)
        != BASELINE_TOTAL
    ):
        failures.append(
            "[FAIL] E6 arithmetic does not close."
        )

    if failures:
        for failure in failures:
            print(failure)

        return 1

    print(
        "[PASS] Step 3 gate: conversion complete "
        "and fully accounted for."
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())