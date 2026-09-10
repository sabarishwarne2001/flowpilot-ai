"""ARCH-29 Tranche 1 — mutation suite for verify_arch29_tranche1.py.

    python scripts/mutate_arch29_tranche1.py

A gate that has never been observed failing is an assertion, not a control.
This harness introduces one deliberate regression at a time, runs the gate,
asserts that the SPECIFIC check that owns that regression reports FAIL, then
restores the file byte-for-byte and moves on.

Two properties are checked per mutation, not one:

  1. the gate exits non-zero
  2. the intended check is the one that failed

Without (2) a mutation could pass the suite by breaking something unrelated —
a syntax error that makes every check fail would "prove" all nine checks work.

Every mutation restores from an in-memory snapshot in a `finally`, so an
interrupted run leaves the tree clean.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FRONTEND = REPO / "frontend" / "src"
GATE = ROOT / "scripts" / "verify_arch29_tranche1.py"

Mutation = tuple[str, pathlib.Path, str, str, str]

MUTATIONS: list[Mutation] = [
    (
        "G1 — unwrap the platform route from its layout",
        FRONTEND / "App.tsx",
        "<Route element={<PlatformLayout />}>",
        "<Route>",
        "29T1-G1",
    ),
    (
        "G2 — delete the cross-tenant scope band",
        FRONTEND / "layouts" / "PlatformLayout.tsx",
        "every organization on this deployment",
        "this view",
        "29T1-G2",
    ),
    (
        "G3 — orphan the Avatar in one sidebar",
        FRONTEND / "components" / "layout" / "DesktopSidebar.tsx",
        'import { Avatar } from "@/components/common/Avatar";',
        "",
        "29T1-G3",
    ),
    (
        # The one that matters most. Two lines down and the fix is inert.
        "G4 — raise the sign-out flag AFTER the await",
        FRONTEND / "layouts" / "DashboardLayout.tsx",
        "    beginSignOut();\n    try {\n      await authApi.logoutRequest();",
        "    try {\n      await authApi.logoutRequest();\n      beginSignOut();",
        "29T1-G4",
    ),
    (
        "G5 — regress one guard to the raw redirect builder",
        FRONTEND / "routes" / "PrivateRoute.tsx",
        "  const loginPath = useLoginRedirect(destination);",
        "  const loginPath = loginPathWithRedirect(destination);",
        "29T1-G5",
    ),
    (
        "G6 — persist the sign-out flag to localStorage",
        FRONTEND / "store" / "useAuthStore.ts",
        "        partialize: (state) => ({\n          user: state.user,",
        "        partialize: (state) => ({\n          user: state.user,\n          isSigningOut: state.isSigningOut,",
        "29T1-G6",
    ),
    (
        "G6b — stop resetting the flag on teardown",
        FRONTEND / "store" / "useAuthStore.ts",
        "            isSigningOut: false,\n          });\n        },",
        "          });\n        },",
        "29T1-G6",
    ),
    (
        "G8 — hardcode the model spelling in the pre-bake",
        ROOT / "Dockerfile",
        "name = canonical_model_name(settings.EMBEDDING_MODEL_NAME); \\",
        "name = 'all-MiniLM-L6-v2'; \\",
        "29T1-G8",
    ),
    (
        "G9 — drop the voluntary-exit assertion",
        FRONTEND / "routes" / "tenantPaths.ts",
        '    loginPathForExit("/acme/engineering/settings", true) === "/login",',
        "    true,",
        "29T1-G9",
    ),
]


def run_gate() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GATE), "--static-only"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def check_failed(output: str, check_id: str) -> bool:
    """True when the named check reported FAIL in this run."""
    for line in output.splitlines():
        if check_id in line and line.strip().startswith("[FAIL]"):
            return True
    return False


def main() -> int:
    print("=" * 78)
    print("ARCH-29 TRANCHE 1 — MUTATION SUITE")
    print("=" * 78)

    baseline_rc, baseline_out = run_gate()
    if baseline_rc != 0:
        print("BASELINE IS NOT GREEN — mutation results would be meaningless.\n")
        print(baseline_out)
        return 1
    print(f"[ OK ] baseline gate is green (exit {baseline_rc})\n")

    survivors: list[str] = []

    for label, path, old, new, check_id in MUTATIONS:
        original = path.read_text(encoding="utf-8-sig")

        if old not in original:
            print(f"[MISS] {label}\n       anchor not found in {path.name}")
            survivors.append(label)
            continue

        try:
            path.write_text(original.replace(old, new, 1), encoding="utf-8")
            rc, out = run_gate()

            if rc == 0:
                print(f"[SURV] {label}\n       gate still exited 0 — check is asleep")
                survivors.append(label)
            elif not check_failed(out, check_id):
                print(
                    f"[WRNG] {label}\n"
                    f"       gate failed, but {check_id} was not the check that fired"
                )
                survivors.append(label)
            else:
                print(f"[KILL] {label}\n       {check_id} reported FAIL, gate exit {rc}")
        finally:
            path.write_text(original, encoding="utf-8")

    print("-" * 78)

    restored_rc, _ = run_gate()
    if restored_rc != 0:
        print("RESTORE FAILED — the tree is dirty. Investigate before proceeding.")
        return 1
    print(f"[ OK ] tree restored, gate green again (exit {restored_rc})")

    killed = len(MUTATIONS) - len(survivors)
    print(f"{killed}/{len(MUTATIONS)} mutations killed")

    if survivors:
        print("\nSURVIVORS — these regressions would ship undetected:")
        for label in survivors:
            print(f"  - {label}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())