import React from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Loader2, ShieldAlert } from "lucide-react";

import { ROUTES } from "@/constants/routes";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { authApi } from "@/services/api/auth";
import { restoreSession } from "@/services/api/client";
import { useAuthStore } from "@/store/useAuthStore";

/**
 * ARCH-30 Tranche 1 (T4-F4) — completes a SAML or OIDC login.
 *
 * WHY THIS PAGE EXISTS
 * ====================
 *
 * The identity provider posts to the backend's ACS (or redirects to the OIDC
 * callback). ARCH-30 Step 0 made both handlers set the HttpOnly refresh cookie
 * on their 302. The browser then arrived at the SPA holding a valid session
 * and no way for the SPA to know it:
 *
 *   - `SessionBootstrap` restores from the cookie only when the persisted
 *     `isAuthenticated` flag is already true. On a first SSO login from a
 *     browser that never used a password, it is false, so no restore runs.
 *   - `PrivateRoute` redirects on that same flag before making a request.
 *
 * The user was bounced to /login with a live session cookie in the jar. Every
 * component was correct; the seam between the backend's handoff and the
 * frontend's bootstrap had no owner. This page owns it.
 *
 * WHAT IT DOES, IN ORDER
 * ======================
 *
 *   1. Exchange the cookie for an access token (`restoreSession`), unless
 *      `SessionBootstrap` already did so on this page load.
 *   2. Load the profile with that token.
 *   3. Commit both with `setAuth`, which raises the persisted flag.
 *   4. Navigate to the validated `next`, replacing this entry in history so
 *      Back does not replay the exchange.
 *
 * On failure it STOPS and says so, with a link to the login page. It does not
 * redirect automatically: an automatic redirect to /login from here is the
 * exact loop being removed, and a user watching a page flash between two URLs
 * cannot report which one failed.
 *
 * WHY NO "STARTED" REF
 * ====================
 *
 * The obvious guard against a double effect under StrictMode is a ref set on
 * first run. It breaks this page: StrictMode mounts, cleans up (cancelling the
 * first run), and mounts again — and the ref makes the second run return
 * early, so nothing ever navigates. Instead both runs proceed and share work:
 * `restoreSession` goes through `refreshOnce`, which collapses concurrent calls
 * into one rotation, and the cancelled run stops before touching the profile.
 */

type Phase = "exchanging" | "failed";

export const SsoComplete: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const setAuth = useAuthStore((state) => state.setAuth);
  const [phase, setPhase] = React.useState<Phase>("exchanging");

  /**
   * Validated with the same function Login and PublicRoute use. The backend
   * already refused an unsafe value, so a failure here means the URL was edited
   * after the redirect — and the answer is the picker, not the edited path.
   *
   * "/" maps to the workspace picker for parity with the password path: the
   * backend sends "/" when the login began without a destination, and the
   * picker is where a password login without a destination lands.
   */
  const destination = React.useMemo(() => {
    const requested = new URLSearchParams(location.search).get("next");
    return requested && requested !== "/" && isSafeRedirectPath(requested)
      ? requested
      : `${ROUTES.WORKSPACES}?landing=1`;
  }, [location.search]);

  React.useEffect(() => {
    let cancelled = false;

    const complete = async (): Promise<void> => {
      // SessionBootstrap restores for a browser whose persisted flag was true,
      // and it does so with THIS page load's cookie — the one the ACS just set.
      // Reusing that token avoids a second rotation. The profile is still
      // reloaded below, because the persisted user may be whoever signed in on
      // this browser before.
      const existing = useAuthStore.getState().token;
      const token = existing ?? (await restoreSession());
      if (cancelled) {
        return;
      }
      if (token === null) {
        setPhase("failed");
        return;
      }

      try {
        const user = await authApi.getMeRequest();
        if (cancelled) {
          return;
        }
        setAuth(user, token);
        navigate(destination, { replace: true });
      } catch {
        if (!cancelled) {
          setPhase("failed");
        }
      }
    };

    void complete();

    return () => {
      cancelled = true;
    };
  }, [destination, navigate, setAuth]);

  if (phase === "failed") {
    return (
      <div className="mx-auto flex w-full flex-col justify-center space-y-4 text-center sm:w-[350px]">
        <ShieldAlert className="mx-auto h-8 w-8 text-destructive" aria-hidden="true" />
        <h1 className="text-xl font-semibold tracking-tight">
          We couldn&apos;t finish signing you in
        </h1>
        <p className="text-sm text-muted-foreground">
          Your identity provider accepted the sign-in, but this browser did not
          receive a usable session. This usually means cookies are blocked for
          this site, or the sign-in took long enough to expire. Try again from
          the sign-in page.
        </p>
        <Link
          to={ROUTES.LOGIN}
          replace
          className="inline-flex h-10 items-center justify-center rounded-md bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90"
        >
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div
      className="mx-auto flex w-full flex-col items-center justify-center space-y-3 sm:w-[350px]"
      role="status"
      aria-live="polite"
    >
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" aria-hidden="true" />
      <p className="text-sm text-muted-foreground">Completing sign-in…</p>
    </div>
  );
};

export default SsoComplete;
