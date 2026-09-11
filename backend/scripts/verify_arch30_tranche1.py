"""ARCH-30 Tranche 1 verification gate.

    python scripts/verify_arch30_tranche1.py
    python scripts/verify_arch30_tranche1.py --static-only

Eleven checks over four blocking findings from the ARCH-30 ground-truth audit:

    T4-F1  no quota tier could be published                    G1 G2 G3 G4
    T4-F3  SCIM unreachable through the ingress                G5 G6 G11
    T4-F4  first federated login loops back to /login          G7 G8
    T4-F5  vanity-domain branding cannot resolve               G9 G10

T4-F2 (Dodo webhook dispatch) is Tranche 2 and is not covered here.

WHY G1 AND G2 EXECUTE INSTEAD OF READING
=======================================

T4-F1 survived verify_arch29_tranche2.py because every check in that gate reads
source. Its G7 asserted the seed's limit keys had display labels; nothing
asserted the seed's tiers could be PUBLISHED. The defect lived in the seam
between a correct seed and a correct validator, and no amount of reading either
file alone finds it.

G1 imports the real `quota_service._validate` and runs the real seed rows
through it. Only `pricing_service.resolve` is stubbed, because it needs a price
book in a database and is not what this gate is about. G2 runs deliberately
malformed entitlement rows through the same function and requires refusal.

`--static-only` is accepted so run_all_gates can pass it uniformly. G1 and G2
need the backend's Python dependencies but no database, so they run in both
modes.

WHY G9 COUNTS READERS OF VITE_API_URL
=====================================

The first pass at T4-F5 fixed the default in `client.ts` and the production
bundle still contained `http://localhost:8000`: the assistant stream client
held its own copy of the old expression. Fixing instances leaves the class
open. G9 requires exactly one module to read `import.meta.env.VITE_API_URL`.
"""

from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import pathlib
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FE = REPO / "frontend"
SRC = FE / "src"

SAML = ROOT / "app" / "api" / "v1" / "saml.py"
SCIM = ROOT / "app" / "api" / "v1" / "scim.py"
CADDYFILE = ROOT / "deploy" / "Caddyfile"
MODEL_ROUTING = ROOT / "app" / "services" / "byok" / "model_routing_service.py"
SEED = ROOT / "scripts" / "seed_quota_tiers.py"

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []


def record(check: str, status: str, detail: str) -> None:
    _results.append((check, status, detail))


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def _prepare_backend_imports() -> None:
    """Make `app.*` importable without a database.

    Placeholders only fill variables that are ABSENT from the process
    environment. No check here opens a connection; the engine that
    `app.db.session` builds at import time is never used.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for name, value in (
        ("DATABASE_URL", "postgresql://gate:gate@127.0.0.1:1/gate"),
        ("SECRET_KEY", "arch30-tranche1-gate-" + "s" * 48),
        ("JWT_SECRET_KEY", "arch30-tranche1-gate-" + "j" * 48),
    ):
        os.environ.setdefault(name, value)


def _function_source(src: str, name: str) -> Optional[str]:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src, node)
    return None


def _module_string_constant(src: str, name: str) -> Optional[str]:
    tree = ast.parse(src)
    for node in tree.body:
        targets: list[ast.expr] = []
        value: Optional[ast.expr] = None
        if isinstance(node, ast.Assign):
            targets, value = list(node.targets), node.value
        elif isinstance(node, ast.AnnAssign):
            targets, value = [node.target], node.value
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                return value.value
    return None


# =============================================================================
# G1 — the seeded tiers can be published (EXECUTED)
# =============================================================================
def g1_seed_tiers_publishable() -> None:
    check = "30T1-G1 seeded tiers pass publish_tier validation"
    try:
        _prepare_backend_imports()
        from app.services import pricing_service, quota_service

        spec = importlib.util.spec_from_file_location("_arch30_seed", SEED)
        if spec is None or spec.loader is None:
            record(check, FAIL, "could not load seed_quota_tiers.py")
            return
        seed = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(seed)
    except Exception as exc:  # noqa: BLE001
        record(check, FAIL, f"import failed: {type(exc).__name__}: {exc}")
        return

    original_resolve = pricing_service.resolve
    pricing_service.resolve = lambda *args, **kwargs: None  # needs a price book
    rejected: list[str] = []
    try:
        for tier_key, payload in seed.PLACEHOLDER_TIERS.items():
            try:
                quota_service._validate(
                    None,
                    entries=seed._specs(payload["entries"]),
                    effective_from=datetime.now(timezone.utc),
                )
            except quota_service.QuotaTierValidationError as exc:
                rejected.append(f"{tier_key}: {exc}")
    finally:
        pricing_service.resolve = original_resolve

    if rejected:
        record(check, FAIL, " | ".join(rejected))
    else:
        record(
            check, PASS,
            f"{len(seed.PLACEHOLDER_TIERS)} tiers validate through the real _validate",
        )


# =============================================================================
# G2 — malformed entitlement rows are refused (EXECUTED)
# =============================================================================
def g2_entitlement_shape_enforced() -> None:
    check = "30T1-G2 malformed entitlement rows are refused"
    try:
        _prepare_backend_imports()
        from app.core import entitlements
        from app.models.spend_limit import SpendLimitPeriod
        from app.services import quota_service
        from app.services.quota_service import TierEntrySpec
    except Exception as exc:  # noqa: BLE001
        record(check, FAIL, f"import failed: {type(exc).__name__}: {exc}")
        return

    other_period = next(
        (p for p in SpendLimitPeriod if p.value != entitlements.CANONICAL_PERIOD), None
    )
    key = entitlements.PLATFORM_KEY

    cases: list[tuple[str, TierEntrySpec, bool]] = [
        ("canonical row", TierEntrySpec(limit_key=key, max_cost_micros=0), True),
        ("non-zero cost", TierEntrySpec(limit_key=key, max_cost_micros=1), False),
        ("quantity", TierEntrySpec(limit_key=key, max_cost_micros=0,
                                   max_quantity=Decimal("1")), False),
        ("ALLOW_AND_WARN", TierEntrySpec(limit_key=key, max_cost_micros=0,
                                         overage_policy="ALLOW_AND_WARN"), False),
        ("grace", TierEntrySpec(limit_key=key, max_cost_micros=0,
                                grace_quantity=Decimal("1")), False),
        ("unregistered addon key", TierEntrySpec(limit_key="addon.unregistered",
                                                 max_cost_micros=0), False),
    ]
    if other_period is not None:
        cases.append(("non-MONTH period", TierEntrySpec(
            limit_key=key, max_cost_micros=0, period=other_period), False))

    wrong: list[str] = []
    for label, spec, should_accept in cases:
        try:
            quota_service._validate(
                None, entries=[spec], effective_from=datetime.now(timezone.utc)
            )
            accepted = True
        except quota_service.QuotaTierValidationError:
            accepted = False
        if accepted != should_accept:
            wrong.append(f"{label}: {'accepted' if accepted else 'refused'}")

    if wrong:
        record(check, FAIL, "; ".join(wrong))
    else:
        record(check, PASS, f"{len(cases)} cases behave as specified")


# =============================================================================
# G3 — the two vocabularies cannot share a name
# =============================================================================
def g3_vocabularies_disjoint() -> None:
    check = "30T1-G3 entitlement vocabulary is disjoint from meters"
    try:
        _prepare_backend_imports()
        from app.core import usage_events
        from app.core import entitlements
    except Exception as exc:  # noqa: BLE001
        record(check, FAIL, f"entitlements import raised {type(exc).__name__}: {exc}")
        return

    meters = set(usage_events.USAGE_EVENT_TYPES) | {usage_events.TOTAL_COST_KEY}
    overlap = sorted(set(entitlements.ENTITLEMENT_KEYS) & meters)
    if overlap:
        record(check, FAIL, f"shared names: {overlap}")
    elif not entitlements.ENTITLEMENT_KEYS:
        record(check, FAIL, "entitlement registry is empty")
    else:
        record(check, PASS, f"{len(entitlements.ENTITLEMENT_KEYS)} entitlement(s), no overlap")


# =============================================================================
# G4 — every reader and label uses a registered key
# =============================================================================
def g4_entitlement_readers_registered() -> None:
    check = "30T1-G4 entitlement readers and labels use registered keys"
    try:
        _prepare_backend_imports()
        from app.core import entitlements
    except Exception as exc:  # noqa: BLE001
        record(check, FAIL, f"import failed: {type(exc).__name__}: {exc}")
        return

    registered = set(entitlements.ENTITLEMENT_KEYS)
    problems: list[str] = []

    reader = _module_string_constant(read(MODEL_ROUTING), "PLATFORM_KEY_LIMIT_KEY")
    if reader is None:
        problems.append("model_routing_service.PLATFORM_KEY_LIMIT_KEY literal not found")
    elif reader not in registered:
        problems.append(f"model_routing reads {reader!r}, which is not registered")

    seed_src = read(SEED)
    seed_key: Optional[str] = None
    for node in ast.parse(seed_src).body:
        if (isinstance(node, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "PLATFORM_KEY" for t in node.targets)
                and isinstance(node.value, ast.Dict)):
            for k, v in zip(node.value.keys, node.value.values):
                if (isinstance(k, ast.Constant) and k.value == "limit_key"
                        and isinstance(v, ast.Constant)):
                    seed_key = v.value
    if seed_key is None:
        problems.append("seed PLATFORM_KEY['limit_key'] not found")
    elif seed_key not in registered:
        problems.append(f"seed publishes {seed_key!r}, which is not registered")

    labels = read(SRC / "types" / "planEntitlements.ts")
    for key in sorted(registered):
        entry = re.search(
            r'"' + re.escape(key) + r'"\s*:\s*\{(.*?)\n\s*\},', labels, re.DOTALL
        )
        if entry is None:
            problems.append(f"{key} has no KNOWN_METERS entry")
        elif not re.search(r"capability\s*:\s*true", entry.group(1)):
            problems.append(f"{key} is not marked capability: true on the plan card")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "reader, seed and plan-card labels agree with the registry")


# =============================================================================
# G5 — the ingress proxies SCIM on both site blocks
# =============================================================================
def g5_scim_reachable_through_ingress() -> None:
    check = "30T1-G5 ingress proxies /scim/v2 on platform and tenant hosts"
    src = read(CADDYFILE)
    platform_start = src.find("{$APP_DOMAIN} {")
    custom_start = src.find("\n:443 {")
    redirect_start = src.find("http://{$APP_DOMAIN}")
    if min(platform_start, custom_start, redirect_start) < 0:
        record(check, FAIL, "could not locate the platform, :443 and redirect site blocks")
        return

    platform = src[platform_start:custom_start]
    custom = src[custom_start:redirect_start]
    # The body ends at a brace on its OWN line. Caddy placeholders such as
    # `{host}` sit inline, and a first-`}` match stops inside `{host}` and
    # reports the Host header missing when it is present.
    handle = re.compile(
        r"handle\s+/scim/v2/\*\s*\{\s*reverse_proxy\s+web:8000\s*\{(?P<body>.*?)\n\s*\}",
        re.DOTALL,
    )

    problems: list[str] = []
    if handle.search(platform) is None:
        problems.append("platform block has no /scim/v2/* reverse_proxy")
    tenant = handle.search(custom)
    if tenant is None:
        problems.append("tenant custom-domain block has no /scim/v2/* reverse_proxy")
    elif "header_up Host {host}" not in tenant.group("body"):
        problems.append(
            "tenant SCIM proxy drops the Host header; scim_key's host binding "
            "could never see the tenant"
        )

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "both site blocks proxy SCIM; tenant block preserves Host")


# =============================================================================
# G6 — on a tenant host, a SCIM token must belong to that tenant
# =============================================================================
def g6_scim_token_bound_to_host() -> None:
    check = "30T1-G6 SCIM token is bound to the resolved host tenant"
    seg = _function_source(read(SCIM), "scim_key")
    if seg is None:
        record(check, FAIL, "scim_key not found")
        return

    problems: list[str] = []
    if "host_organization_id(request)" not in seg:
        problems.append("does not read the host-resolved tenant")
    compare = re.search(r"host_org\s*!=\s*key\.organization_id", seg)
    if compare is None:
        problems.append("does not compare the host tenant with the token's organization")
    elif "raise ScimNotFound" not in seg[compare.end():]:
        problems.append("a mismatch does not raise ScimNotFound")
    if re.search(r"return\s+scim_service\.authenticate\(", seg):
        problems.append("returns authenticate() directly, bypassing the binding")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "mismatched host/token refused as invalid credentials")


# =============================================================================
# G7 — federated logins land on the completion route, which is mounted
#       outside both session guards
# =============================================================================
def g7_federated_login_completes() -> None:
    check = "30T1-G7 federated login lands on the SSO completion route"
    saml = read(SAML)
    problems: list[str] = []

    backend_path = _module_string_constant(saml, "SSO_COMPLETE_PATH")
    routes = read(SRC / "constants" / "routes.ts")
    frontend_path = re.search(r'SSO_COMPLETE:\s*"([^"]+)"', routes)
    if backend_path is None:
        problems.append("saml.SSO_COMPLETE_PATH not found")
    if frontend_path is None:
        problems.append("ROUTES.SSO_COMPLETE not found")
    if backend_path and frontend_path and backend_path != frontend_path.group(1):
        problems.append(
            f"backend redirects to {backend_path!r} but the SPA mounts "
            f"{frontend_path.group(1)!r}"
        )

    for name in ("assertion_consumer_service", "oidc_callback"):
        seg = _function_source(saml, name)
        if seg is None:
            problems.append(f"{name} not found")
            continue
        if "_sso_landing_redirect(" not in seg:
            problems.append(f"{name} does not return through _sso_landing_redirect")
        if re.search(r"RedirectResponse\(\s*f[\"']\{frontend\}\{target\}", seg):
            problems.append(f"{name} still issues the bare redirect to the target")

    helper = _function_source(saml, "_sso_landing_redirect")
    if helper is None:
        problems.append("_sso_landing_redirect not found")
    else:
        for token in ("set_refresh_cookie(", "SSO_COMPLETE_PATH", "is_safe_redirect_path("):
            if token not in helper:
                problems.append(f"_sso_landing_redirect lacks {token}")

    page_path = SRC / "pages" / "Auth" / "SsoComplete.tsx"
    if not page_path.exists():
        problems.append("SsoComplete.tsx is missing")
    else:
        page = read(page_path)
        for token in ("restoreSession", "setAuth(", "isSafeRedirectPath("):
            if token not in page:
                problems.append(f"SsoComplete lacks {token}")

    app = read(SRC / "App.tsx")
    route_at = app.find("path={ROUTES.SSO_COMPLETE}")
    register_at = app.find("path={ROUTES.REGISTER}")
    private_at = app.find("<Route element={<PrivateRoute />}>")
    if route_at < 0:
        problems.append("App.tsx does not mount ROUTES.SSO_COMPLETE")
    elif not (0 <= register_at < route_at < private_at):
        problems.append("completion route is not between the public auth group and PrivateRoute")
    elif "</Route>" not in app[register_at:route_at]:
        problems.append("completion route is nested inside the PublicRoute group")
    elif not re.search(
        r"<Route element=\{<AuthLayout />\}>\s*<Route\s+path=\{ROUTES\.SSO_COMPLETE\}", app
    ):
        problems.append("completion route is not a direct child of an unguarded AuthLayout")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "ACS and OIDC hand off to an unguarded completion route")


# =============================================================================
# G8 — discover and start resolve SSO through one function that refuses
#       ambiguity
# =============================================================================
def g8_sso_resolution_single_and_strict() -> None:
    check = "30T1-G8 SSO discover/start share one strict resolver"
    saml = read(SAML)
    problems: list[str] = []

    for name in ("discover", "start_sso"):
        seg = _function_source(saml, name)
        if seg is None:
            problems.append(f"{name} not found")
            continue
        if "_resolve_sso_binding(" not in seg:
            problems.append(f"{name} does not use _resolve_sso_binding")
        if ".first()" in seg or ".one_or_none()" in seg:
            problems.append(f"{name} runs its own query again")

    resolver = _function_source(saml, "_resolve_sso_binding")
    if resolver is None:
        problems.append("_resolve_sso_binding not found")
    else:
        required = {
            ".limit(2)": "cannot distinguish one binding from many",
            "len(rows) > 1": "does not test for ambiguity",
            "raise AmbiguousSsoBinding": "does not refuse ambiguity",
            "VerifiedDomain.status.in_(": "does not require a verified domain",
            "VerifiedDomain.is_sso_binding.is_(True)": "does not require the SSO binding flag",
        }
        for token, meaning in required.items():
            if token not in resolver:
                problems.append(f"resolver {meaning} ({token} absent)")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "one resolver; ambiguity and unverified domains refused")


# =============================================================================
# G9 — production calls the API same-origin, and only one module owns the base
# =============================================================================
def g9_production_api_same_origin() -> None:
    check = "30T1-G9 production API base is same-origin with a single owner"
    problems: list[str] = []

    client = read(SRC / "services" / "api" / "client.ts")
    declaration = re.search(r"export const API_BASE_URL(?::\s*string)?\s*=\s*(.*?);", client, re.DOTALL)
    if declaration is None:
        problems.append("client.ts does not export API_BASE_URL")
    elif not re.search(
        r'import\.meta\.env\.DEV\s*\?\s*"http://localhost:8000/api/v1"\s*:\s*"/api/v1"',
        declaration.group(1),
    ):
        problems.append("API_BASE_URL does not default to /api/v1 outside development")

    readers = sorted(
        str(path.relative_to(SRC))
        for path in SRC.rglob("*")
        if path.suffix in {".ts", ".tsx"}
        and "import.meta.env.VITE_API_URL" in read(path)
    )
    if readers != ["services/api/client.ts"]:
        problems.append(f"VITE_API_URL must be read only by services/api/client.ts; readers: {readers}")

    vite = read(FE / "vite.config.ts")
    body = _ts_function_body(vite, "assertSameOriginApi")
    if body is None:
        problems.append("vite.config.ts has no assertSameOriginApi")
    elif "throw new Error(" not in body:
        problems.append("assertSameOriginApi never throws")
    if not re.search(
        r"defineConfig\(\s*\(\{\s*mode\s*,\s*command\s*\}\)\s*=>\s*\{\s*assertSameOriginApi\(\s*mode\s*,\s*command\s*\)",
        vite,
    ):
        problems.append("defineConfig does not call assertSameOriginApi(mode, command) first")

    # The build guard is only as good as what feeds `vite build`. Vite loads
    # `.env` in every mode, so a template or dev script that writes an absolute
    # URL there turns every local production build into a refusal — or, before
    # the guard, into a bundle pointed at localhost.
    template = read(FE / ".env.example")
    if re.search(r"^\s*VITE_API_URL\s*=", template, re.MULTILINE):
        problems.append(".env.example sets VITE_API_URL, which `vite build` also reads")
    for script in ("start_dev.ps1", "start_dev.sh"):
        text = read(REPO / script)
        if ".env.development.local" not in text:
            problems.append(f"{script} does not write the dev-only env file")
        if re.search(r"(Set-EnvValue\s+\$frontendEnv|env_set\s+\"\$\{FRONTEND_ENV\}\")\s+'?VITE_API_URL", text):
            problems.append(f"{script} still writes VITE_API_URL into frontend/.env")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "relative default, one reader, build guard, dev env kept out of builds")


def _ts_function_body(src: str, name: str) -> Optional[str]:
    start = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)[^{]*\{", src)
    if start is None:
        return None
    depth, index = 1, start.end()
    while index < len(src) and depth:
        if src[index] == "{":
            depth += 1
        elif src[index] == "}":
            depth -= 1
        index += 1
    return src[start.end():index - 1]


# =============================================================================
# G10 — the host-resolved manifest has consumers
# =============================================================================
def g10_branding_manifest_consumed() -> None:
    check = "30T1-G10 public branding manifest is consumed pre-auth"
    problems: list[str] = []

    hook_path = SRC / "hooks" / "usePublicBrandingManifest.ts"
    if not hook_path.exists():
        problems.append("usePublicBrandingManifest.ts is missing")
    else:
        hook = read(hook_path)
        for token in ("brandingKeys.manifest", "getPublicBrandingManifest"):
            if token not in hook:
                problems.append(f"hook lacks {token}")

    service = read(SRC / "services" / "api" / "branding.ts")
    if "BRANDING_ENDPOINTS.manifest" not in service:
        problems.append("no service function requests BRANDING_ENDPOINTS.manifest")

    for rel in ("components/branding/Brand.tsx", "layouts/AuthLayout.tsx"):
        text = read(SRC / rel)
        if not re.search(r"=\s*usePublicBrandingManifest\(", text):
            problems.append(f"{rel} does not call usePublicBrandingManifest")

    brand = read(SRC / "components" / "branding" / "Brand.tsx")
    if "resolveApiAssetUrl(" not in brand:
        problems.append("Brand renders the manifest logo without resolveApiAssetUrl")

    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "hook, service and both pre-auth consumers wired")


# =============================================================================
# G11 — the SCIM console shows the base URL an IdP needs
# =============================================================================
def g11_scim_base_url_surfaced() -> None:
    check = "30T1-G11 SCIM console shows the base URL"
    text = read(SRC / "pages" / "identity" / "ScimTokenManager.tsx")
    problems = [
        f"lacks {token}"
        for token in ("<ScimBaseUrl />", "const ScimBaseUrl", "apiOrigin()", "/scim/v2")
        if token not in text
    ]
    if problems:
        record(check, FAIL, "; ".join(problems))
    else:
        record(check, PASS, "base URL rendered with a copy control")


def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-30 Tranche 1 gate")
    parser.add_argument(
        "--static-only", action="store_true",
        help="accepted for run_all_gates; no check here needs a database",
    )
    parser.parse_args()

    g1_seed_tiers_publishable()
    g2_entitlement_shape_enforced()
    g3_vocabularies_disjoint()
    g4_entitlement_readers_registered()
    g5_scim_reachable_through_ingress()
    g6_scim_token_bound_to_host()
    g7_federated_login_completes()
    g8_sso_resolution_single_and_strict()
    g9_production_api_same_origin()
    g10_branding_manifest_consumed()
    g11_scim_base_url_surfaced()

    width = max(len(name) for name, _, _ in _results)
    failed = sum(1 for _, status, _ in _results if status == FAIL)
    skipped = sum(1 for _, status, _ in _results if status == SKIP)

    print("=" * 78)
    print("ARCH-30 TRANCHE 1 — VERIFICATION GATE")
    print("=" * 78)
    for name, status, detail in _results:
        print(f"[{status:4}] {name:<{width}}  {detail}")
    print("-" * 78)
    print(f"{len(_results) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())