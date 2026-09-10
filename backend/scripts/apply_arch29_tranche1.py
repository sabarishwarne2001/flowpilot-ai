"""ARCH-29 Tranche 1 — anchored patch script for MODIFIED files.

    python scripts/apply_arch29_tranche1.py
    python scripts/apply_arch29_tranche1.py --check

Applies eleven edits across nine existing files. New files (Avatar.tsx,
PlatformLayout.tsx, useLoginRedirect.ts) are delivered whole and are not
touched here.

ARCH-19 precedent, and the three properties that come with it:

  IDEMPOTENT   — re-running is a no-op. Each edit tests for its own post-state
                 first and reports SKIP rather than double-applying.
  FAILS LOUDLY — a missing anchor aborts with a non-zero exit and names the
                 file and the fragment. It does not "best effort" past a miss,
                 because a partially patched auth store is worse than an
                 unpatched one: the flag would exist, no guard would read it,
                 and sign-out would look fixed while behaving exactly as before.
  ATOMIC       — every file is validated before ANY file is written. A run that
                 aborts leaves the tree untouched.

`--check` validates anchors and reports what would happen without writing.

Run `scripts/verify_arch29_tranche1.py` afterwards. This script applying
cleanly is not evidence that the result is correct.
"""

from __future__ import annotations

import argparse
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
FE = REPO / "frontend" / "src"

HOOK_IMPORT = 'import { useLoginRedirect } from "@/routes/useLoginRedirect";'
AVATAR_IMPORT = 'import { Avatar } from "@/components/common/Avatar";'

# Each entry: (path, sentinel, [(old, new), ...])
# `sentinel` present in the file means the edit set is already applied.
EDITS: list[tuple[pathlib.Path, str, list[tuple[str, str]]]] = []


# =============================================================================
# Issue 3 — the platform shell
# =============================================================================
EDITS.append((
    FE / "App.tsx",
    "PlatformLayout",
    [
        (
            'const AdminMarginsHub = lazy(() => import("@/pages/admin/AdminMarginsHub"));',
            'const AdminMarginsHub = lazy(() => import("@/pages/admin/AdminMarginsHub"));\n'
            'const PlatformLayout = lazy(() => import("@/layouts/PlatformLayout"));',
        ),
        (
            """                <Route
                  path={ROUTE_PATTERNS.platformShell}
                  element={<SuperAdminGuard />}
                >
                  <Route
                    path={ROUTE_PATTERNS.platformMargins}
                    element={<AdminMarginsHub />}
                  />
                </Route>""",
            """                <Route
                  path={ROUTE_PATTERNS.platformShell}
                  element={<SuperAdminGuard />}
                >
                  {/*
                    ARCH-29 Tranche 1. A pathless layout route, so the guard
                    stays a guard and the chrome stays chrome. Every future
                    platform page mounts inside PlatformLayout and inherits the
                    cross-tenant scope band and the exit; a page added as a
                    sibling of this element would ship without both.
                  */}
                  <Route element={<PlatformLayout />}>
                    <Route
                      path={ROUTE_PATTERNS.platformMargins}
                      element={<AdminMarginsHub />}
                    />
                  </Route>
                </Route>""",
        ),
    ],
))


# =============================================================================
# Issue 6 — the sign-out split
# =============================================================================
EDITS.append((
    FE / "store" / "useAuthStore.ts",
    "isSigningOut",
    [
        (
            """interface AuthState {
  readonly user: User | null;
  readonly token: string | null;
  readonly isAuthenticated: boolean;""",
            """interface AuthState {
  readonly user: User | null;
  readonly token: string | null;
  readonly isAuthenticated: boolean;

  /**
   * ARCH-29 Tranche 1. True between the moment the user clicks "Sign Out" and
   * the moment the session is torn down.
   *
   * WHY A FLAG AND NOT A NAVIGATION ARGUMENT
   * ========================================
   *
   * `handleLogout` already navigated to a bare `/login`. The deep URL that
   * came back anyway was not written by the logout handler at all — it was
   * written by a GUARD, during the await.
   *
   * The sequence: `logoutRequest()` invalidates the session server-side and is
   * awaited. Any authenticated request still in flight during that window —
   * a TanStack refetch, the `/auth/me` poll — now 401s. The client interceptor
   * clears local state, `useMeContext` reports unauthorized, and `PrivateRoute`
   * re-renders while STILL MOUNTED at `/caretakers-global-inc/general/settings`.
   * It returns `<Navigate to={loginPathWithRedirect(destination)} replace />`,
   * which commits before `clearAuth()` and `navigate()` on the next lines ever
   * run. The guard is behaving exactly as designed; it simply cannot tell a
   * session that was revoked from a session the user chose to end.
   *
   * This flag is that distinction, and it is set BEFORE the await so it is
   * already true when the race opens.
   *
   * WHY IT IS NOT PERSISTED
   * =======================
   *
   * `partialize` below lists `user` and `isAuthenticated` and nothing else, so
   * this stays in memory. That is load-bearing: persisted, a tab closed
   * mid-logout would leave the flag true on disk forever, and every subsequent
   * session expiry on that machine would silently discard its destination —
   * a permanent regression written by a transient failure. `verify_arch29_
   * tranche1.py` G6 asserts it is absent from `partialize`.
   */
  readonly isSigningOut: boolean;

  /**
   * Marks the sign-out as voluntary. Call before any await in the handler.
   */
  readonly beginSignOut: () => void;""",
        ),
        (
            """        user: null,
        token: null,
        isAuthenticated: false,

        /**
         * Stores only the access token.""",
            """        user: null,
        token: null,
        isAuthenticated: false,
        isSigningOut: false,

        beginSignOut: () =>
          set((state) => ({
            ...state,
            isSigningOut: true,
          })),

        /**
         * Stores only the access token.""",
        ),
        (
            """          set({
            user: null,
            token: null,
            isAuthenticated: false,
          });
        },""",
            """          set({
            user: null,
            token: null,
            isAuthenticated: false,
            // ARCH-29. Reset here, not at the call sites, for the same reason
            // the tenant reset lives here: clearAuth is reached from explicit
            // sign-out, the 401 interceptor, and a failed login. A path that
            // forgot to lower this flag would leave the NEXT session expiry
            // silently discarding its destination — the involuntary case
            // wearing the voluntary case's behaviour, which is the exact
            // inversion of the bug this flag was added to fix.
            isSigningOut: false,
          });
        },""",
        ),
    ],
))

EDITS.append((
    FE / "routes" / "tenantPaths.ts",
    "loginPathForExit",
    [
        (
            """export const loginPathWithRedirect = (destination: string): string => {
  if (!isSafeRedirectPath(destination)) {
    return "/login";
  }
  return `/login?redirect=${encodeURIComponent(destination)}`;
};""",
            """export const loginPathWithRedirect = (destination: string): string => {
  if (!isSafeRedirectPath(destination)) {
    return "/login";
  }
  return `/login?redirect=${encodeURIComponent(destination)}`;
};

/**
 * ARCH-29 Tranche 1 — the login path for a guard that is turning a user away.
 *
 * Two situations reach every guard's redirect branch and they want opposite
 * treatment:
 *
 *   INVOLUNTARY — the token expired, the server revoked the session, the
 *   refresh failed. The user did not choose to leave. Preserving where they
 *   were standing is correct and is the whole reason `loginPathWithRedirect`
 *   exists.
 *
 *   VOLUNTARY — the user clicked "Sign Out". Remembering the deep settings tab
 *   they were on and putting them back there after the next login is not
 *   helpful; it is the application overriding an explicit decision to leave.
 *
 * Before this function, both produced `?redirect=…` because a guard cannot
 * see intent from `location` alone. `useAuthStore.isSigningOut` carries the
 * intent; this function is where it is applied.
 *
 * WHY THE VOLUNTARY BRANCH IS NOT "JUST RETURN THE SAME THING"
 * ============================================================
 *
 * It returns bare `"/login"`, discarding the destination entirely rather than
 * validating and dropping it. The open-redirect defence in
 * `isSafeRedirectPath` is untouched and still guards the involuntary path —
 * this branch simply never reaches it, because there is nothing to guard.
 *
 * Deep links are unaffected. An invitation URL carries `?redirect=` in the
 * link the user clicked, which arrives on `/login` directly and is parsed by
 * `Login.tsx`; it does not pass through a guard's turn-away branch at all.
 */
export const loginPathForExit = (
  destination: string,
  voluntary: boolean,
): string => (voluntary ? "/login" : loginPathWithRedirect(destination));""",
        ),
        (
            """  expect(
    "an unsafe destination is dropped rather than propagated",
    loginPathWithRedirect("//evil.example.com") === "/login",
  );""",
            """  expect(
    "an unsafe destination is dropped rather than propagated",
    loginPathWithRedirect("//evil.example.com") === "/login",
  );

  // ARCH-29 Tranche 1.
  expect(
    "an involuntary exit preserves the destination",
    loginPathForExit("/acme/engineering/settings", false) ===
      `/login?redirect=${encodeURIComponent("/acme/engineering/settings")}`,
  );
  expect(
    "a voluntary exit discards the destination",
    loginPathForExit("/acme/engineering/settings", true) === "/login",
  );
  expect(
    "a voluntary exit discards an unsafe destination too",
    loginPathForExit("//evil.example.com", true) === "/login",
  );
  expect(
    "an involuntary exit still refuses an unsafe destination",
    loginPathForExit("//evil.example.com", false) === "/login",
  );""",
        ),
    ],
))

EDITS.append((
    FE / "routes" / "PrivateRoute.tsx",
    "useLoginRedirect",
    [
        ('import { loginPathWithRedirect } from "@/routes/tenantPaths";', HOOK_IMPORT),
        (
            "  const destination = `${location.pathname}${location.search}`;",
            "  const destination = `${location.pathname}${location.search}`;\n\n"
            "  // ARCH-29 Tranche 1. Hooks must run before any early return, so this is\n"
            "  // resolved unconditionally even on the paths that never use it.\n"
            "  const loginPath = useLoginRedirect(destination);",
        ),
        (
            """  if (!isAuthenticated) {
    return <Navigate to={loginPathWithRedirect(destination)} replace />;
  }""",
            """  if (!isAuthenticated) {
    return <Navigate to={loginPath} replace />;
  }""",
        ),
        (
            """  if (isUnauthorized) {
    return <Navigate to={loginPathWithRedirect(destination)} replace />;
  }""",
            """  if (isUnauthorized) {
    return <Navigate to={loginPath} replace />;
  }""",
        ),
    ],
))

EDITS.append((
    FE / "routes" / "SuperAdminGuard.tsx",
    "useLoginRedirect",
    [
        ('import { loginPathWithRedirect } from "@/routes/tenantPaths";', HOOK_IMPORT),
        (
            "  const { isLoading, isUnauthorized } = useMeContext();",
            "  const { isLoading, isUnauthorized } = useMeContext();\n"
            "  const loginPath = useLoginRedirect(location.pathname);",
        ),
        (
            """      <Navigate
        to={loginPathWithRedirect(location.pathname)}
        replace
      />""",
            """      <Navigate
        to={loginPath}
        replace
      />""",
        ),
    ],
))

EDITS.append((
    FE / "routes" / "TenantGuard.tsx",
    "useLoginRedirect",
    [
        (
            'import { ROUTE_PARAMS, loginPathWithRedirect } from "@/routes/tenantPaths";',
            'import { ROUTE_PARAMS } from "@/routes/tenantPaths";\n' + HOOK_IMPORT,
        ),
        (
            "  const location = useLocation();",
            "  const location = useLocation();\n\n"
            "  // ARCH-29 Tranche 1. Hoisted above the switch: hooks cannot be called from\n"
            "  // inside a case arm.\n"
            "  const loginPath = useLoginRedirect(`${location.pathname}${location.search}`);",
        ),
        (
            "          to={loginPathWithRedirect(`${location.pathname}${location.search}`)}\n",
            "          to={loginPath}\n",
        ),
    ],
))

EDITS.append((
    FE / "routes" / "LegacyRouteRedirect.tsx",
    "useLoginRedirect",
    [
        (
            'import { loginPathWithRedirect, toTenantPath } from "@/routes/tenantPaths";',
            'import { toTenantPath } from "@/routes/tenantPaths";\n' + HOOK_IMPORT,
        ),
        (
            "  const location = useLocation();",
            "  const location = useLocation();\n\n"
            "  // ARCH-29 Tranche 1. Hoisted above the switch.\n"
            "  const loginPath = useLoginRedirect(`${location.pathname}${location.search}`);",
        ),
        (
            "          to={loginPathWithRedirect(`${location.pathname}${location.search}`)}\n",
            "          to={loginPath}\n",
        ),
    ],
))

EDITS.append((
    FE / "pages" / "Tenant" / "WorkspacePicker.tsx",
    "useLoginRedirect",
    [
        (
            'import { loginPathWithRedirect, workspacePath, createWorkspacePath } from "@/routes/tenantPaths";',
            'import { workspacePath, createWorkspacePath } from "@/routes/tenantPaths";\n' + HOOK_IMPORT,
        ),
        (
            "  const location = useLocation();",
            "  const location = useLocation();\n\n"
            "  // ARCH-29 Tranche 1. Hoisted above the early returns.\n"
            "  const loginPath = useLoginRedirect(location.pathname);",
        ),
        (
            "      <Navigate to={loginPathWithRedirect(location.pathname)} replace />\n",
            "      <Navigate to={loginPath} replace />\n",
        ),
    ],
))


_LOGOUT_OLD = """  const handleLogout = useCallback(async (): Promise<void> => {
    await authApi.logoutRequest();
    clearAuth();
    navigate(ROUTES.LOGIN, { replace: true });
  }, [clearAuth, navigate]);"""

_LOGOUT_NEW = """  const handleLogout = useCallback(async (): Promise<void> => {
    // ARCH-29 Tranche 1. Set BEFORE the await, not after.
    //
    // `logoutRequest()` revokes the session server-side. Any authenticated
    // request still in flight during that await 401s, the interceptor clears
    // local state, and the guard above this layout re-renders while still
    // mounted at the current deep path — emitting its own redirect to
    // `/login?redirect=<deep path>` before either line below executes. That
    // guard redirect, not this handler, is what put the user back into their
    // settings tab after signing out. Raising the flag first means the guard
    // already knows the exit was deliberate when the race opens.
    beginSignOut();
    try {
      await authApi.logoutRequest();
    } finally {
      // `finally`, because a network failure on the way out must still end the
      // session locally. It also lowers `isSigningOut`: left raised by a failed
      // request, the flag would make the NEXT involuntary expiry discard its
      // destination, which is the bug inverted rather than fixed.
      clearAuth();
      navigate(ROUTES.LOGIN, { replace: true });
    }
  }, [beginSignOut, clearAuth, navigate]);"""

_SEL_OLD = "  const clearAuth = useAuthStore((state) => state.clearAuth);"
_SEL_NEW = (
    "  const clearAuth = useAuthStore((state) => state.clearAuth);\n"
    "  const beginSignOut = useAuthStore((state) => state.beginSignOut);"
)

for _layout in ("DashboardLayout.tsx", "OrganizationLayout.tsx"):
    EDITS.append((
        FE / "layouts" / _layout,
        "beginSignOut",
        [(_SEL_OLD, _SEL_NEW), (_LOGOUT_OLD, _LOGOUT_NEW)],
    ))


# =============================================================================
# Issue 5b — Avatar consumption points
# =============================================================================
EDITS.append((
    FE / "components" / "layout" / "DesktopSidebar.tsx",
    AVATAR_IMPORT,
    [
        (
            'import { Brand } from "@/components/branding/Brand";',
            'import { Brand } from "@/components/branding/Brand";\n' + AVATAR_IMPORT,
        ),
        (
            """          <div
            className={`
              min-w-0
              overflow-hidden
              transition-all
              duration-300
              ease-in-out
              ${
                isDesktopCollapsed
                  ? "max-w-0 opacity-0"
                  : "max-w-[220px] opacity-100"
              }
            `}
          >
            <span className="block truncate text-xs font-semibold text-muted-foreground select-none">
              Signed in as
            </span>

            <span className="mt-1 block truncate text-sm font-extrabold leading-none">
              {user?.email ?? "User Profile"}
            </span>
          </div>""",
            """          {/*
            ARCH-29 Tranche 1. The avatar stays rendered when the sidebar is
            collapsed — it is the identity affordance that survives the text
            being clipped to zero width, which is the collapsed state's whole
            point.
          */}
          <div className="flex min-w-0 items-center gap-2.5">
            <Avatar
              userId={user?.id}
              email={user?.email}
              size={isDesktopCollapsed ? "sm" : "md"}
            />

            <div
              className={`
                min-w-0
                overflow-hidden
                transition-all
                duration-300
                ease-in-out
                ${
                  isDesktopCollapsed
                    ? "max-w-0 opacity-0"
                    : "max-w-[180px] opacity-100"
                }
              `}
            >
              <span className="block truncate text-xs font-semibold text-muted-foreground select-none">
                Signed in as
              </span>

              <span className="mt-1 block truncate text-sm font-extrabold leading-none">
                {user?.email ?? "User Profile"}
              </span>
            </div>
          </div>""",
        ),
    ],
))

EDITS.append((
    FE / "components" / "layout" / "MobileSidebarContent.tsx",
    AVATAR_IMPORT,
    [
        (
            'import { Brand } from "@/components/branding/Brand";',
            'import { Brand } from "@/components/branding/Brand";\n' + AVATAR_IMPORT,
        ),
        (
            """        <div className="mb-3 truncate text-xs">
          <span className="block font-semibold text-muted-foreground">Signed in as</span>
          <span className="font-bold text-foreground">{user?.email ?? "User Profile"}</span>
        </div>""",
            """        <div className="mb-3 flex min-w-0 items-center gap-2.5 text-xs">
          <Avatar userId={user?.id} email={user?.email} size="md" />
          <div className="min-w-0 truncate">
            <span className="block font-semibold text-muted-foreground">Signed in as</span>
            <span className="block truncate font-bold text-foreground">
              {user?.email ?? "User Profile"}
            </span>
          </div>
        </div>""",
        ),
    ],
))

EDITS.append((
    FE / "components" / "layout" / "OrganizationSidebarNavigation.tsx",
    AVATAR_IMPORT,
    [
        # This file has no Brand import to anchor against, unlike the other two
        # sidebars, so the import lands after the auth store import. Omitting
        # this edit is how the first draft of this script shipped a file that
        # used <Avatar> without importing it — caught by re-applying to a
        # pristine checkout and running tsc, not by reading the diff.
        (
            'import { useAuthStore } from "@/store/useAuthStore";',
            'import { useAuthStore } from "@/store/useAuthStore";\n' + AVATAR_IMPORT,
        ),
        (
            """          <div className="flex items-center gap-2">
            <div className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-muted-foreground">
                Signed in as
              </span>
              <span className="mt-0.5 block truncate text-sm font-bold leading-none text-foreground">
                {user?.email ?? "User Profile"}
              </span>
            </div>""",
            """          <div className="flex items-center gap-2.5">
            <Avatar userId={user?.id} email={user?.email} size="md" />

            <div className="min-w-0 flex-1">
              <span className="block text-xs font-semibold text-muted-foreground">
                Signed in as
              </span>
              <span className="mt-0.5 block truncate text-sm font-bold leading-none text-foreground">
                {user?.email ?? "User Profile"}
              </span>
            </div>""",
        ),
    ],
))


# =============================================================================
# Issue 8 — the embedding pre-bake
# =============================================================================
EDITS.append((
    ROOT / "Dockerfile",
    "ARCH-29 Tranche 1 — pre-bake the embedding weights",
    [
        (
            """ENV WORKER_PROFILE=enrich \\
    TRANSFORMERS_OFFLINE=0 \\
    HF_HOME=/home/flowpilot/.cache/huggingface

CMD ["python", "-m", "app.worker", "--loop", "jobs", "--profile", "enrich"]""",
            """ENV WORKER_PROFILE=enrich \\
    TRANSFORMERS_OFFLINE=0 \\
    HF_HOME=/home/flowpilot/.cache/huggingface

# ARCH-29 Tranche 1 — pre-bake the embedding weights.
#
# The `ocr` target below has pre-baked PaddleOCR since ARCH-10 and the reranker
# image has pre-baked its CrossEncoder since ARCH-11. This target did not, so
# `embedding_service._load_model()` pulled all-MiniLM-L6-v2 from the Hugging
# Face Hub on the first vector generation of a cold container — over the
# network, unauthenticated, on the request path.
#
# WHY THE NAME COMES FROM `canonical_model_name` AND IS NOT WRITTEN OUT HERE
# =========================================================================
#
# `SentenceTransformer("all-MiniLM-L6-v2")` and
# `SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")` load the same
# weights into DIFFERENT cache directories. `settings.EMBEDDING_MODEL_NAME` is
# the bare form and the `document_settings` default is the qualified form —
# two strings, one model, as `app/core/embeddings.py` has documented since
# ARCH-11. Baking under one spelling and loading under the other produces a
# cache MISS and a silent runtime download: the optimisation appears to be
# applied, the image grows by 90MB, and nothing gets faster.
#
# Resolving through `canonical_model_name` at build time means the string baked
# is by construction the string loaded. `verify_arch29_tranche1.py` G7 asserts
# both configured spellings still canonicalise to the same known model, so a
# future config edit that breaks the equality fails the gate rather than
# quietly reintroducing the download.
RUN python -c "\\
from app.core.embeddings import canonical_model_name; \\
from app.core.config import settings; \\
from sentence_transformers import SentenceTransformer; \\
name = canonical_model_name(settings.EMBEDDING_MODEL_NAME); \\
print('ARCH-29 pre-baking embedding model:', name); \\
SentenceTransformer(name)" || \\
    (echo 'Embedding warmup notice: weights will download on first run' && true)

CMD ["python", "-m", "app.worker", "--loop", "jobs", "--profile", "enrich"]""",
        ),
    ],
))


# =============================================================================
# Runner
# =============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="ARCH-29 Tranche 1 patch")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate anchors without writing",
    )
    args = parser.parse_args()

    planned: list[tuple[pathlib.Path, str]] = []
    skipped: list[str] = []
    misses: list[str] = []

    # Pass 1 — validate everything before writing anything.
    for path, sentinel, pairs in EDITS:
        if not path.exists():
            misses.append(f"{path}: file does not exist")
            continue

        src = path.read_text(encoding="utf-8-sig")

        if sentinel in src:
            skipped.append(path.name)
            continue

        working = src
        for old, new in pairs:
            if old not in working:
                misses.append(
                    f"{path.name}: anchor not found —\n"
                    f"        {old.strip().splitlines()[0][:96]}"
                )
                break
            working = working.replace(old, new, 1)
        else:
            planned.append((path, working))

    if misses:
        print("ANCHOR MISSES — nothing was written:\n")
        for m in misses:
            print(f"  {m}")
        print(
            "\nThe repository is not at the expected revision, or these files "
            "have diverged.\nResolve before re-running; a partial application "
            "is worse than none."
        )
        return 1

    if args.check:
        print(f"[check] {len(planned)} file(s) would change, "
              f"{len(skipped)} already applied")
        for path, _ in planned:
            print(f"  WOULD PATCH  {path.relative_to(REPO)}")
        for name in skipped:
            print(f"  SKIP         {name}")
        return 0

    # Pass 2 — write.
    for path, content in planned:
        path.write_text(content, encoding="utf-8")
        print(f"  PATCHED  {path.relative_to(REPO)}")
    for name in skipped:
        print(f"  SKIP     {name} (already applied)")

    print(f"\n{len(planned)} patched, {len(skipped)} skipped.")
    print("Now run: python scripts/verify_arch29_tranche1.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())