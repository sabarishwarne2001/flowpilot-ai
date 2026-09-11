"""ARCH-30 Tranche 1 — mutation suite for verify_arch30_tranche1.py.

    python scripts/mutate_arch30_tranche1.py

Same contract as the ARCH-29 suites: one deliberate regression at a time, the
gate must exit non-zero, AND the check that owns that regression must be the one
reporting FAIL. A gate that fails for the wrong reason is asleep on the right
one.

The mutations are the edits a plausible future patch would make, not syntax
vandalism: registering the entitlement as a meter "to make validation pass",
dropping the Host header from a proxy block that looks redundant, restoring the
localhost default "for local convenience", giving the stream client its own API
base back.

ONE DIFFERENCE FROM THE ARCH-29 SUITES
=====================================

Those read with `utf-8-sig` and restored with `write_text(..., "utf-8")`, which
strips a byte-order mark from every file they mutate. The Caddyfile, client.ts
and routes.ts carry one. This suite snapshots RAW BYTES and restores raw bytes,
and the final step asserts every mutated file is byte-identical to its snapshot.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FE = REPO / "frontend"
SRC = FE / "src"
GATE = ROOT / "scripts" / "verify_arch30_tranche1.py"

BOM = b"\xef\xbb\xbf"

MUTATIONS: list[tuple[str, pathlib.Path, str, str, str]] = [
    (
        "G1 — skip the entitlement branch in _validate",
        ROOT / "app" / "services" / "quota_service.py",
        "        if entitlements.is_entitlement_key(spec.limit_key):",
        "        if False and entitlements.is_entitlement_key(spec.limit_key):",
        "30T1-G1",
    ),
    (
        # The loosening that "only" accepts any cost.
        "G2 — accept any non-null cost on an entitlement row",
        ROOT / "app" / "core" / "entitlements.py",
        "    if max_cost_micros != CANONICAL_MAX_COST_MICROS:",
        "    if max_cost_micros is None:",
        "30T1-G2",
    ),
    (
        # The one-line "fix" for T4-F1 that this tranche rejected.
        "G3 — register the entitlement as a usage meter",
        ROOT / "app" / "core" / "usage_events.py",
        '        description="One document completing the extraction pipeline.",\n    ),\n',
        '        description="One document completing the extraction pipeline.",\n    ),\n'
        "    UsageEventType(\n"
        '        name="llm.platform_key",\n'
        "        unit=UsageUnit.REQUEST,\n"
        "        emission=EmissionKind.OCCURRENCE,\n"
        '        description="Mutation: an entitlement registered as a meter.",\n'
        "    ),\n",
        "30T1-G3",
    ),
    (
        "G4 — rename the key the router reads",
        ROOT / "app" / "services" / "byok" / "model_routing_service.py",
        'PLATFORM_KEY_LIMIT_KEY = "llm.platform_key"',
        'PLATFORM_KEY_LIMIT_KEY = "llm.platform_access"',
        "30T1-G4",
    ),
    (
        "G5 — drop SCIM from the tenant custom-domain block",
        ROOT / "deploy" / "Caddyfile",
        "    handle /scim/v2/* {\n        reverse_proxy web:8000 {\n            header_up Host {host}\n",
        "    handle /scim-disabled/* {\n        reverse_proxy web:8000 {\n            header_up Host {host}\n",
        "30T1-G5",
    ),
    (
        "G6 — disable the SCIM host/token binding",
        ROOT / "app" / "api" / "v1" / "scim.py",
        "    if host_org is not None and host_org != key.organization_id:",
        "    if host_org is not None and False:",
        "30T1-G6",
    ),
    (
        # Step 0's shape, restored on the ACS path.
        "G7 — ACS redirects straight to the target again",
        ROOT / "app" / "api" / "v1" / "saml.py",
        "    return _sso_landing_redirect(target=target,\n"
        "                                 plaintext_token=issued.plaintext_token)",
        "    return RedirectResponse(target, status_code=302)",
        "30T1-G7",
    ),
    (
        "G7b — frontend route drifts from the backend redirect",
        SRC / "constants" / "routes.ts",
        '  SSO_COMPLETE: "/auth/sso/complete",',
        '  SSO_COMPLETE: "/sso/complete",',
        "30T1-G7",
    ),
    (
        "G8 — resolver fetches one row and cannot see ambiguity",
        ROOT / "app" / "api" / "v1" / "saml.py",
        "        .limit(2)\n",
        "        .limit(1)\n",
        "30T1-G8",
    ),
    (
        "G9 — restore the localhost default in every build",
        SRC / "services" / "api" / "client.ts",
        '(import.meta.env.DEV ? "http://localhost:8000/api/v1" : "/api/v1")',
        '"http://localhost:8000/api/v1"',
        "30T1-G9",
    ),
    (
        # The instance the first pass of this tranche missed.
        "G9b — stream client computes its own API base again",
        SRC / "services" / "streaming" / "resumableStream.ts",
        "const API_URL = API_BASE_URL;",
        'const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";',
        "30T1-G9",
    ),
    (
        "G9c — remove the build-time same-origin assertion",
        FE / "vite.config.ts",
        "  assertSameOriginApi(mode, command);\n",
        "",
        "30T1-G9",
    ),
    (
        # The template line every new checkout copies into .env.
        "G9d — re-activate VITE_API_URL in .env.example",
        FE / ".env.example",
        "# VITE_API_URL=http://localhost:8000/api/v1\n",
        "VITE_API_URL=http://localhost:8000/api/v1\n",
        "30T1-G9",
    ),
    (
        "G10 — orphan the manifest in Brand",
        SRC / "components" / "branding" / "Brand.tsx",
        "  const manifest = usePublicBrandingManifest({ enabled: tenant === null });",
        "  const manifest = null as BrandingManifest | null;",
        "30T1-G10",
    ),
    (
        "G11 — stop rendering the SCIM base URL",
        SRC / "pages" / "identity" / "ScimTokenManager.tsx",
        "          <ScimBaseUrl />\n",
        "",
        "30T1-G11",
    ),
]


def run_gate() -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GATE), "--static-only"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    return proc.returncode, proc.stdout + proc.stderr


def check_failed(output: str, check_id: str) -> bool:
    return any(
        check_id in line and line.strip().startswith("[FAIL]")
        for line in output.splitlines()
    )


def main() -> int:
    print("=" * 78)
    print("ARCH-30 TRANCHE 1 — MUTATION SUITE")
    print("=" * 78)

    rc, out = run_gate()
    if rc != 0:
        print("BASELINE IS NOT GREEN — mutation results would be meaningless.\n")
        print(out)
        return 1
    print(f"[ OK ] baseline gate is green (exit {rc})\n")

    snapshots: dict[pathlib.Path, bytes] = {}
    survivors: list[str] = []

    for label, path, old, new, check_id in MUTATIONS:
        raw = snapshots.setdefault(path, path.read_bytes())
        has_bom = raw.startswith(BOM)
        text = raw.decode("utf-8-sig")
        if old not in text:
            print(f"[MISS] {label}\n       anchor not found in {path.name}")
            survivors.append(label)
            continue
        mutated = text.replace(old, new, 1).encode("utf-8")
        try:
            path.write_bytes((BOM + mutated) if has_bom else mutated)
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
            path.write_bytes(raw)

    print("-" * 78)
    drifted = [p for p, raw in snapshots.items() if p.read_bytes() != raw]
    if drifted:
        print("RESTORE FAILED — these files differ from their snapshots:")
        for p in drifted:
            print(f"  - {p.relative_to(REPO)}")
        return 1
    rc, _ = run_gate()
    if rc != 0:
        print("RESTORE FAILED — the gate is not green after restoration.")
        return 1
    print(f"[ OK ] {len(snapshots)} files restored byte-for-byte, gate green again (exit {rc})")
    print(f"{len(MUTATIONS) - len(survivors)}/{len(MUTATIONS)} mutations killed")

    if survivors:
        print("\nSURVIVORS — these regressions would ship undetected:")
        for s in survivors:
            print(f"  - {s}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())