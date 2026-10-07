"""ARCH-29 Tranche 2 — mutation suite for verify_arch29_tranche2.py.

    python scripts/mutate_arch29_tranche2.py

Same contract as the Tranche 1 suite: one deliberate regression at a time, the
gate must exit non-zero, AND the check that owns that regression must be the
one that fired. Restores from an in-memory snapshot in a `finally`.

The mutations are chosen to be the ones a plausible future patch would
actually make — reverting the trigger to its readable enumerated form,
restoring the global seat price "for compatibility", ungating one downgrade
branch — rather than syntax vandalism that any check would catch.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FRONTEND = REPO / "frontend" / "src"
GATE = ROOT / "scripts" / "verify_arch29_tranche2.py"

MIGRATION = ROOT / "alembic" / "versions" / "arch29_step1_quota_tier_commercials.py"

MUTATIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        # The readable-looking regression. Someone decides the jsonb diff is
        # opaque and "clarifies" it back into a column list.
        "G1 — revert the trigger to an enumerated denylist",
        MIGRATION,
        "        (to_jsonb(NEW) {_STRIP})\n        IS DISTINCT FROM\n        (to_jsonb(OLD) {_STRIP})",
        "        NEW.key IS DISTINCT FROM OLD.key\n        OR NEW.display_name IS DISTINCT FROM OLD.display_name",
        "29T2-G1",
    ),
    (
        "G2 — drop the zero exemption from the price constraint",
        MIGRATION,
        '"    AND (gateway_price_id IS NOT NULL OR unit_amount_micros = 0))",',
        '"    AND gateway_price_id IS NOT NULL)",',
        "29T2-G2",
    ),
    (
        "G2b — drop the unique index on gateway_price_id",
        MIGRATION,
        "        unique=True,\n",
        "        unique=False,\n",
        "29T2-G2",
    ),
    (
        # F-2 restored "for backwards compatibility".
        "G3 — restore the global seat price fallback",
        ROOT / "app" / "services" / "billing" / "portal_service.py",
        "    if price_id:\n        resolved_price = price_id",
        "    if price_id or settings.BILLING_SEAT_PRICE_ID:\n"
        "        resolved_price = price_id or settings.BILLING_SEAT_PRICE_ID",
        "29T2-G3",
    ),
    (
        "G4 — hardcode unit_amount back to None",
        ROOT / "app" / "api" / "v1" / "billing.py",
        "                unit_amount=unit_amount,",
        "                unit_amount=None,",
        "29T2-G4",
    ),
    (
        # The partial fix. Five of six gated looks done in review.
        "G5 — ungate the no-credential downgrade path",
        ROOT / "app" / "services" / "byok" / "model_routing_service.py",
        '        _assert_platform_key_entitled("no_tenant_credential_configured")\n',
        "",
        "29T2-G5",
    ),
    (
        "G6 — treat an unresolvable tier as permissive",
        ROOT / "app" / "services" / "byok" / "model_routing_service.py",
        "    if tier is None:\n        return False",
        "    if tier is None:\n        return True",
        "29T2-G6",
    ),
    (
        "G7 — publish a meter with no display label",
        ROOT / "scripts" / "seed_quota_tiers.py",
        '"limit_key": "ocr.page", "max_quantity": "100"',
        '"limit_key": "ocr.page_v2", "max_quantity": "100"',
        "29T2-G7",
    ),
    (
        "G8 — un-hide the catch-all entitlement row",
        FRONTEND / "types" / "planEntitlements.ts",
        '    unit: "none",\n    hidden: true,',
        '    unit: "none",',
        "29T2-G8",
    ),
    (
        "G8b — drop the zero-price branch from formatPrice",
        FRONTEND / "pages" / "billing" / "PlanSelector.tsx",
        "  if (plan.unit_amount === 0) {",
        "  if (false) {",
        "29T2-G8",
    ),
]


def run_gate() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GATE), "--static-only"],
        cwd=str(ROOT), capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout + proc.stderr


def check_failed(output: str, check_id: str) -> bool:
    return any(
        check_id in line and line.strip().startswith("[FAIL]")
        for line in output.splitlines()
    )


def main() -> int:
    print("=" * 78)
    print("ARCH-29 TRANCHE 2 — MUTATION SUITE")
    print("=" * 78)

    rc, out = run_gate()
    if rc != 0:
        print("BASELINE IS NOT GREEN — mutation results would be meaningless.\n")
        print(out)
        return 1
    print(f"[ OK ] baseline gate is green (exit {rc})\n")

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
                print(f"[WRNG] {label}\n       failed, but {check_id} was not the check")
                survivors.append(label)
            else:
                print(f"[KILL] {label}\n       {check_id} reported FAIL")
        finally:
            path.write_text(original, encoding="utf-8")

    print("-" * 78)
    rc, _ = run_gate()
    if rc != 0:
        print("RESTORE FAILED — the tree is dirty.")
        return 1
    print(f"[ OK ] tree restored, gate green again (exit {rc})")
    print(f"{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")

    if survivors:
        print("\nSURVIVORS — these regressions would ship undetected:")
        for s in survivors:
            print(f"  - {s}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())