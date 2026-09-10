"""ARCH-29 Tranche 1 verification gate.

    python scripts/verify_arch29_tranche1.py
    python scripts/verify_arch29_tranche1.py --static-only

Nine checks across four deliverables: the platform shell (Issue 3), the
voluntary/involuntary sign-out split (Issue 6), the Avatar reader (Issue 5b),
and the embedding pre-bake (Issue 8).

THE FOUR THAT CARRY WEIGHT, AND WHY
===================================

G3  — `Avatar` HAS CALL SITES. This whole deliverable exists because
      `avatar_url` was written and never read: the orphaned-guard defect
      (invariant I4) with the polarity flipped. Shipping a beautiful Avatar
      component that nothing imports would reproduce the exact bug it was
      written to fix, and neither `tsc` nor the test suite would say a word,
      because an unused export is legal TypeScript. G3 counts importers.

G5  — NO GUARD CALLS `loginPathWithRedirect` DIRECTLY. The sign-out fix is a
      centralisation, and centralisations decay one call site at a time. Six
      call sites across five guards were rewired; a seventh guard shipping in
      ARCH-30 would reach for the obvious-looking function and silently restore
      the defect on one surface. This is the check that makes the fix
      structural rather than instance-by-instance.

G4  — `beginSignOut()` PRECEDES THE AWAIT. Ordering is the entire fix. The
      guard redirect races the logout handler inside the await window, so a
      flag raised after `await authApi.logoutRequest()` is raised after the
      race has already been lost. A patch that moves the call two lines down
      is invisible to review and reverts the behaviour completely, so the
      gate asserts source order rather than mere presence.

G7  — BOTH CONFIGURED EMBEDDING SPELLINGS CANONICALISE TO THE SAME KNOWN
      MODEL. `EMBEDDING_MODEL_NAME` is bare, the `document_settings` default is
      namespace-qualified, and `SentenceTransformer` caches them to different
      directories. If they ever diverge, the Dockerfile pre-bakes weights the
      runtime will not find, the image silently re-downloads on the request
      path, and the only symptom is latency that looks like it always did.

Frontend checks read `.tsx` as text. That is deliberate: `verify-api-contracts.mjs`
owns TypeScript semantics via the compiler API, and duplicating a parser here
would give two sources of truth for the same files. What this gate asserts —
that a symbol is imported, that one statement precedes another — is decidable
from source order, and the mutation suite proves each check actually fires.

Database checks SKIP rather than FAIL when the application cannot be imported,
matching verify_arch25.py through verify_arch28.py.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FRONTEND = REPO / "frontend" / "src"
sys.path.insert(0, str(ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
_results: list[tuple[str, str, str]] = []

# --- files under audit -------------------------------------------------------
PLATFORM_LAYOUT = FRONTEND / "layouts" / "PlatformLayout.tsx"
APP_TSX = FRONTEND / "App.tsx"
AVATAR = FRONTEND / "components" / "common" / "Avatar.tsx"
AUTH_STORE = FRONTEND / "store" / "useAuthStore.ts"
TENANT_PATHS = FRONTEND / "routes" / "tenantPaths.ts"
LOGIN_REDIRECT_HOOK = FRONTEND / "routes" / "useLoginRedirect.ts"
DOCKERFILE = ROOT / "Dockerfile"

LOGOUT_LAYOUTS = [
    FRONTEND / "layouts" / "DashboardLayout.tsx",
    FRONTEND / "layouts" / "OrganizationLayout.tsx",
]

GUARDS = [
    FRONTEND / "routes" / "PrivateRoute.tsx",
    FRONTEND / "routes" / "SuperAdminGuard.tsx",
    FRONTEND / "routes" / "TenantGuard.tsx",
    FRONTEND / "routes" / "LegacyRouteRedirect.tsx",
    FRONTEND / "pages" / "Tenant" / "WorkspacePicker.tsx",
]

AVATAR_CONSUMERS = [
    FRONTEND / "components" / "layout" / "DesktopSidebar.tsx",
    FRONTEND / "components" / "layout" / "MobileSidebarContent.tsx",
    FRONTEND / "components" / "layout" / "OrganizationSidebarNavigation.tsx",
]


def record(check: str, status: str, detail: str) -> None:
    _results.append((check, status, detail))


def read(path: pathlib.Path) -> str:
    """UTF-8 with BOM tolerance — several files in this tree carry one."""
    return path.read_text(encoding="utf-8-sig")


# =============================================================================
# G1 — the platform shell exists and is mounted
# =============================================================================
def g1_platform_shell_mounted() -> None:
    check = "29T1-G1 platform shell mounted"
    if not PLATFORM_LAYOUT.exists():
        record(check, FAIL, "layouts/PlatformLayout.tsx does not exist")
        return

    app = read(APP_TSX)
    if "PlatformLayout" not in app:
        record(check, FAIL, "App.tsx never references PlatformLayout")
        return

    # It must WRAP the margins route, not sit beside it. A sibling element
    # renders no chrome and would leave the deadlock in place while passing a
    # naive "is it imported" check.
    pattern = re.compile(
        r"element=\{<PlatformLayout\s*/>\}\s*>.*?platformMargins",
        re.DOTALL,
    )
    if not pattern.search(app):
        record(
            check,
            FAIL,
            "PlatformLayout does not enclose the platformMargins route",
        )
        return

    record(check, PASS, "PlatformLayout encloses ROUTE_PATTERNS.platformMargins")


# =============================================================================
# G2 — the shell offers a way out
# =============================================================================
def g2_platform_shell_has_exit() -> None:
    check = "29T1-G2 platform shell has an exit"
    if not PLATFORM_LAYOUT.exists():
        record(check, FAIL, "layouts/PlatformLayout.tsx does not exist")
        return

    src = read(PLATFORM_LAYOUT)
    has_link = "ROUTES.WORKSPACES" in src and ("<Link" in src or "useNavigate" in src)
    if not has_link:
        record(check, FAIL, "no navigation to ROUTES.WORKSPACES")
        return

    # The scope band is the reason this layout exists rather than reusing the
    # organization shell. Losing it reintroduces the misattribution risk.
    if "every organization" not in src:
        record(check, FAIL, "cross-tenant scope band is missing")
        return

    record(check, PASS, "exit to ROUTES.WORKSPACES and scope band both present")


# =============================================================================
# G3 — Avatar has readers (the orphaned-data check)
# =============================================================================
def g3_avatar_has_call_sites() -> None:
    check = "29T1-G3 Avatar has call sites"
    if not AVATAR.exists():
        record(check, FAIL, "components/common/Avatar.tsx does not exist")
        return

    importers: list[str] = []
    for path in FRONTEND.rglob("*.tsx"):
        if path == AVATAR:
            continue
        src = read(path)
        if "components/common/Avatar" in src and "<Avatar" in src:
            importers.append(path.name)

    if len(importers) < 3:
        record(
            check,
            FAIL,
            f"only {len(importers)} consumer(s): {importers or 'none'} — "
            "Avatar is an orphaned export",
        )
        return

    missing = [p.name for p in AVATAR_CONSUMERS if p.name not in importers]
    if missing:
        record(check, FAIL, f"expected consumers not wired: {missing}")
        return

    record(check, PASS, f"{len(importers)} consumers: {sorted(importers)}")


# =============================================================================
# G4 — beginSignOut precedes the await
# =============================================================================
def g4_signout_flag_precedes_await() -> None:
    check = "29T1-G4 beginSignOut precedes the await"
    failures: list[str] = []

    for path in LOGOUT_LAYOUTS:
        if not path.exists():
            failures.append(f"{path.name}: missing")
            continue
        src = read(path)

        if "beginSignOut()" not in src:
            failures.append(f"{path.name}: never calls beginSignOut()")
            continue

        flag_at = src.index("beginSignOut()")
        try:
            await_at = src.index("await authApi.logoutRequest()")
        except ValueError:
            failures.append(f"{path.name}: no awaited logoutRequest")
            continue

        if flag_at > await_at:
            failures.append(
                f"{path.name}: beginSignOut() is called AFTER the await — "
                "the guard redirect has already fired by then"
            )
            continue

        # clearAuth must be unconditional, or a network failure on the way out
        # strands the flag raised and poisons the next involuntary expiry.
        if "finally" not in src:
            failures.append(f"{path.name}: teardown is not in a finally block")

    if failures:
        record(check, FAIL, "; ".join(failures))
        return

    record(check, PASS, "both handlers raise the flag before awaiting")


# =============================================================================
# G5 — no guard calls loginPathWithRedirect directly
# =============================================================================
def g5_guards_use_the_hook() -> None:
    check = "29T1-G5 guards route through useLoginRedirect"
    if not LOGIN_REDIRECT_HOOK.exists():
        record(check, FAIL, "routes/useLoginRedirect.ts does not exist")
        return

    offenders: list[str] = []
    for path in GUARDS:
        if not path.exists():
            offenders.append(f"{path.name}: missing")
            continue
        src = read(path)
        # Strip block and line comments: the docstrings legitimately name the
        # function they replaced, and a gate that fails on prose would push
        # authors to delete the explanation.
        code = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
        code = re.sub(r"^\s*//.*$", "", code, flags=re.MULTILINE)

        if "loginPathWithRedirect" in code:
            offenders.append(f"{path.name}: still calls loginPathWithRedirect")
        elif "useLoginRedirect" not in code:
            offenders.append(f"{path.name}: does not use the hook")

    if offenders:
        record(check, FAIL, "; ".join(offenders))
        return

    record(check, PASS, f"all {len(GUARDS)} guards route through the hook")


# =============================================================================
# G6 — isSigningOut is transient
# =============================================================================
def g6_signout_flag_not_persisted() -> None:
    check = "29T1-G6 isSigningOut is not persisted"
    src = read(AUTH_STORE)

    if "isSigningOut" not in src:
        record(check, FAIL, "isSigningOut is absent from the store")
        return

    match = re.search(r"partialize:\s*\(state\)\s*=>\s*\(\{(.*?)\}\)", src, re.DOTALL)
    if not match:
        record(check, FAIL, "could not locate partialize")
        return

    if "isSigningOut" in match.group(1):
        record(
            check,
            FAIL,
            "isSigningOut appears in partialize — a tab closed mid-logout "
            "would strand the flag true on disk permanently",
        )
        return

    # And it must be lowered on teardown.
    clear = re.search(r"clearAuth:\s*\(\)\s*=>\s*\{(.*?)\n        \},", src, re.DOTALL)
    if not clear or "isSigningOut: false" not in clear.group(1):
        record(check, FAIL, "clearAuth does not reset isSigningOut")
        return

    record(check, PASS, "transient, and reset by clearAuth")


# =============================================================================
# G7 — embedding model name canonicalisation
# =============================================================================
def g7_embedding_names_agree(static_only: bool) -> None:
    check = "29T1-G7 embedding spellings canonicalise alike"
    if static_only:
        record(check, SKIP, "--static-only")
        return

    try:
        from app.core.config import settings
        from app.core.embeddings import (
            KNOWN_EMBEDDING_MODELS,
            canonical_model_name,
        )
        from app.schemas.document_settings import DocumentSettingsBase
    except Exception as exc:  # noqa: BLE001 — import failure is a SKIP, not a FAIL
        record(check, SKIP, f"application not importable: {type(exc).__name__}")
        return

    from_config = canonical_model_name(settings.EMBEDDING_MODEL_NAME)

    field = DocumentSettingsBase.model_fields.get("embedding_model")
    if field is None:
        record(check, FAIL, "document_settings has no embedding_model field")
        return
    from_schema = canonical_model_name(field.default)

    if from_config != from_schema:
        record(
            check,
            FAIL,
            f"config resolves to {from_config!r} but document_settings "
            f"resolves to {from_schema!r} — the pre-baked cache will miss",
        )
        return

    if from_config not in KNOWN_EMBEDDING_MODELS:
        record(check, FAIL, f"{from_config!r} is not in KNOWN_EMBEDDING_MODELS")
        return

    record(check, PASS, f"both resolve to {from_config!r}")


# =============================================================================
# G8 — the enrich image pre-bakes the embedding weights
# =============================================================================
def g8_dockerfile_prebakes_embedding() -> None:
    check = "29T1-G8 enrich image pre-bakes embeddings"
    src = read(DOCKERFILE)

    if "FROM base AS enrich" not in src:
        record(check, FAIL, "no enrich target in Dockerfile")
        return

    start = src.index("FROM base AS enrich")
    nxt = src.find("\nFROM ", start + 1)
    target = src[start : nxt if nxt != -1 else len(src)]

    # Strip `#` comment lines before inspecting.
    #
    # The first version of this check did not, and the mutation suite caught it:
    # replacing `canonical_model_name(...)` with a hardcoded 'all-MiniLM-L6-v2'
    # in the RUN line left the word `canonical_model_name` sitting in the
    # explanatory comment above it, and the check happily passed. It was reading
    # the prose that described the safeguard rather than the code that
    # implements it — a gate that certifies its own documentation.
    target = "\n".join(
        line for line in target.splitlines() if not line.lstrip().startswith("#")
    )

    if "SentenceTransformer" not in target:
        record(check, FAIL, "enrich target never loads SentenceTransformer")
        return

    # The literal-string failure mode: baking "all-MiniLM-L6-v2" while the
    # runtime loads "sentence-transformers/all-MiniLM-L6-v2" caches to a
    # different directory and changes nothing.
    #
    # Note the trailing `(`. The second version of this check looked for the
    # bare name and the mutation suite caught it again: replacing the CALL with
    # a hardcoded literal left `from app.core.embeddings import
    # canonical_model_name` in place, and the check accepted an import with no
    # call site. That is invariant I4 — the orphaned guard — reproduced inside
    # the gate written to enforce it. Requiring the open paren distinguishes a
    # symbol that is invoked from one that is merely in scope.
    if "canonical_model_name(" not in target:
        record(
            check,
            FAIL,
            "canonical_model_name is imported but never called — the "
            "pre-baked spelling is not the resolved one",
        )
        return

    # Belt and braces: even a correct call is undone by a literal alongside it.
    if re.search(r"""=\s*['"][\w./-]*MiniLM[\w./-]*['"]""", target):
        record(
            check,
            FAIL,
            "a hardcoded model spelling is assigned in the enrich target",
        )
        return

    record(check, PASS, "pre-bake resolves the name through canonical_model_name()")


# =============================================================================
# G9 — the pure exit-path function is self-checked
# =============================================================================
def g9_exit_path_self_checked() -> None:
    check = "29T1-G9 loginPathForExit is self-checked"
    src = read(TENANT_PATHS)

    if "loginPathForExit" not in src:
        record(check, FAIL, "loginPathForExit is absent")
        return

    match = re.search(
        r"export const runTenantPathSelfCheck.*", src, re.DOTALL
    )
    if not match:
        record(check, FAIL, "runTenantPathSelfCheck not found")
        return

    body = match.group(0)
    voluntary = 'loginPathForExit("/acme/engineering/settings", true) === "/login"'
    involuntary = 'loginPathForExit("/acme/engineering/settings", false)'

    if voluntary.replace(" ", "") not in body.replace(" ", "").replace("\n", ""):
        record(check, FAIL, "no assertion that a voluntary exit drops the destination")
        return
    if involuntary.replace(" ", "") not in body.replace(" ", "").replace("\n", ""):
        record(check, FAIL, "no assertion that an involuntary exit keeps it")
        return

    record(check, PASS, "both directions asserted in the self-check")


# =============================================================================
# Runner
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-29 Tranche 1 gate")
    parser.add_argument("--static-only", action="store_true")
    args = parser.parse_args()

    g1_platform_shell_mounted()
    g2_platform_shell_has_exit()
    g3_avatar_has_call_sites()
    g4_signout_flag_precedes_await()
    g5_guards_use_the_hook()
    g6_signout_flag_not_persisted()
    g7_embedding_names_agree(args.static_only)
    g8_dockerfile_prebakes_embedding()
    g9_exit_path_self_checked()

    width = max(len(name) for name, _, _ in _results)
    failed = 0
    skipped = 0

    print("=" * 78)
    print("ARCH-29 TRANCHE 1 — VERIFICATION GATE")
    print("=" * 78)

    for name, status, detail in _results:
        if status == FAIL:
            failed += 1
        elif status == SKIP:
            skipped += 1
        print(f"[{status:4}] {name:<{width}}  {detail}")

    print("-" * 78)
    passed = len(_results) - failed - skipped
    print(f"{passed} passed, {failed} failed, {skipped} skipped")

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())